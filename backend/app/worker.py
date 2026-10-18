"""Proceso worker: composition root de los handlers de la cola durable (ADR-0001, ADR-0005).

Ejecutar en local:  uv run python -m app.worker   (requiere DATABASE_URL)

Handlers registrados: SEND_OUTBOUND (outbox, #18) y SEND_QUOTE_PDF (PDF de cotización, #26), si
hay META_GRAPH_API_VERSION, y
PROCESS_CONVERSATION (agente, #81), si hay LLM_API_KEY, LLM_MODEL y sus precios. Los demás
llegan con sus issues: PRICE_CHANGE_ALERT. EMBED_ITEM ya no se encola (sin embeddings, ADR-0020).
Un tipo sin handler NO se reclama: espera en la cola, sin gastar intentos.
"""

import asyncio
import contextlib
import logging
import os
import secrets
import signal
import socket
from collections.abc import Mapping, Sequence
from datetime import timedelta
from uuid import UUID

import asyncpg
import httpx

from app.application.agent import ProcessConversation
from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.ai import CircuitBreaker, RecordLlmCalls, resilient_chat_model
from app.application.outbound import SEND_OUTBOUND, SendOutbound
from app.application.quote_document import SendQuoteDocument
from app.application.worker import JobHandler, JobRunner, default_maintenance
from app.core.config import Settings, get_settings
from app.core.observability import setup_observability
from app.domain.ai import AiPurpose
from app.domain.jobs import PROCESS_CONVERSATION, SEND_QUOTE_PDF
from app.domain.ports import ChatModel
from app.infrastructure.ai.anthropic import PROVIDER as ANTHROPIC
from app.infrastructure.ai.anthropic import AnthropicChatModel
from app.infrastructure.ai.gemini import PROVIDER as GEMINI
from app.infrastructure.ai.gemini import GeminiChatModel
from app.infrastructure.pdf.reportlab_renderer import ReportlabQuoteRenderer
from app.infrastructure.postgres.jobs import PostgresJobQueue, PostgresMaintenance
from app.infrastructure.postgres.pool import create_pool
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory
from app.infrastructure.secret_store.env import EnvSecretStore
from app.infrastructure.whatsapp.cloud_api import CloudApiSender
from app.verticals import VERTICAL_POLICIES

logger = logging.getLogger("scz.worker")


def build_handlers(
    settings: Settings, pool: asyncpg.Pool, http: httpx.AsyncClient
) -> Mapping[str, JobHandler]:
    """Tipo de trabajo -> handler. Agregar aquí cada handler nuevo (y su prueba)."""
    handlers: dict[str, JobHandler] = {}
    if settings.meta_graph_api_version is not None:
        sender = CloudApiSender(http, api_version=settings.meta_graph_api_version)
        handlers[SEND_OUTBOUND] = SendOutbound(
            unit_of_work=unit_of_work_factory(pool), sender=sender, secrets=EnvSecretStore()
        )
        handlers[SEND_QUOTE_PDF] = SendQuoteDocument(
            unit_of_work=unit_of_work_factory(pool), renderer=ReportlabQuoteRenderer(), uploader=sender,
            secrets=EnvSecretStore(),
        )  # fmt: skip
    else:
        logger.warning(
            "worker: sin META_GRAPH_API_VERSION no se envían mensajes ni PDF (SEND_OUTBOUND y SEND_QUOTE_PDF"
            " esperan en cola)"
        )
    agent = build_agent(settings, pool, http)
    if agent is not None:
        handlers[PROCESS_CONVERSATION] = agent
    else:
        logger.warning(
            "worker: sin LLM_API_KEY y LLM_MODEL el agente no responde (PROCESS_CONVERSATION espera)"
        )
    return handlers


def build_chat_adapter(settings: Settings, http: httpx.AsyncClient) -> tuple[str, ChatModel]:
    """El adaptador del proveedor configurado: Anthropic (ADR-0018) o, solo en desarrollo, Gemini
    (ADR-0019; la configuración lo rechaza en staging y producción)."""
    assert settings.llm_api_key is not None and settings.llm_model is not None  # noqa: S101 - lo valida build_agent
    key, model_id = settings.llm_api_key.get_secret_value(), settings.llm_model
    if settings.llm_provider == GEMINI:
        return GEMINI, GeminiChatModel(http, api_key=key, model=model_id)
    return ANTHROPIC, AnthropicChatModel(http, api_key=key, model=model_id)


def build_agent(
    settings: Settings, pool: asyncpg.Pool, http: httpx.AsyncClient
) -> ProcessConversation | None:
    """El orquestador del agente (#81), o None si falta configuración."""
    pricing = settings.llm_pricing()
    if settings.llm_api_key is None or settings.llm_model is None or pricing is None:
        return None
    model_id = settings.llm_model
    provider, adapter = build_chat_adapter(settings, http)
    breaker = CircuitBreaker()  # uno por proceso y proveedor: si el proveedor cae, todos los turnos degradan
    uow = unit_of_work_factory(pool)
    recorder = RecordLlmCalls(uow)

    def chat_model(organization_id: UUID, conversation_id: UUID) -> ChatModel:
        return resilient_chat_model(
            adapter, breaker=breaker, recorder=recorder, pricing=pricing, provider=provider,
            model=model_id, purpose=AiPurpose.CHAT, organization_id=organization_id,
            conversation_id=conversation_id,
        )  # fmt: skip

    def toolbox(context: TurnContext, enabled: Sequence[str]) -> AgentToolbox:
        return AgentToolbox(uow, context, enabled=enabled)

    return ProcessConversation(
        unit_of_work=uow,
        chat_model=chat_model,
        toolbox=toolbox,
        policies=VERTICAL_POLICIES,
        limits=settings.agent_limits(),
    )


def _install_stop_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows: sin add_signal_handler
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run_worker(settings: Settings, stop: asyncio.Event | None = None) -> None:
    """Corre hasta recibir SIGINT/SIGTERM (o hasta `stop`, en pruebas) y termina lo que tenga en curso."""
    if settings.database_url is None:
        raise ValueError("el worker necesita DATABASE_URL")
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
    pool = await create_pool(
        settings.database_url.get_secret_value(),
        max_size=settings.worker_concurrency + 2,  # +2: mantenimiento y cierre de trabajos
        application_name="scz-b2b-core-worker",
    )
    http = httpx.AsyncClient(limits=httpx.Limits(max_connections=settings.worker_concurrency))
    try:
        handlers = build_handlers(settings, pool, http)
        runner = JobRunner(
            PostgresJobQueue(pool),
            handlers,
            worker_id=worker_id,
            concurrency=settings.worker_concurrency,
            job_timeout_s=settings.job_timeout_s,
            poll_interval_s=settings.worker_poll_interval_s,
            maintenance=default_maintenance(
                PostgresMaintenance(pool), lease=timedelta(seconds=settings.job_lease_s), kinds=list(handlers)
            ),
        )
        if stop is None:
            stop = asyncio.Event()
            _install_stop_signals(stop)
        await runner.run(stop)
    finally:
        await http.aclose()
        await pool.close()


def main(settings: Settings | None = None) -> int:
    """Arranca el worker y devuelve el código de salida del proceso."""
    settings = settings or get_settings()
    setup_observability(settings, service_name="scz-b2b-core-worker")
    if settings.database_url is None:
        logger.error("worker: falta DATABASE_URL; no hay cola que procesar")
        return 2
    logger.info("worker iniciado (entorno=%s)", settings.environment)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_worker(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
