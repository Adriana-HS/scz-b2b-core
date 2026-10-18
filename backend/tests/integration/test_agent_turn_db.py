"""Orquestador del agente de punta a punta contra Postgres real (#81).

webhook (RecordInboundMessages) -> trabajo PROCESS_CONVERSATION -> turno con el modelo guionado
y las herramientas reales -> una respuesta en el outbox, el historial y el costo en llm_calls.
"""

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from app.application.agent import ProcessConversation
from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.ai import CircuitBreaker, RecordLlmCalls, resilient_chat_model
from app.application.inbound import RecordInboundMessages
from app.domain.ai import AiPurpose, ModelPricing, ModelResponse, StopReason, TokenUsage, ToolCall
from app.domain.jobs import PROCESS_CONVERSATION, Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from ..doubles.ai import ScriptedChatModel
from .conftest import Seed

pytestmark = pytest.mark.integration

PRICING = ModelPricing(Decimal("0.10"), Decimal("0.50"), Decimal("0.01"), Decimal("0.125"))


class Policy:
    enabled_tools = ("search_catalog", "get_item_details", "create_quote", "handoff_to_human")

    def system_prompt(self, business_name: str) -> str:
        return f"Eres el asistente de {business_name}."


async def _customer_writes(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed, text: str) -> UUID:
    sender = "5917" + "".join(secrets.choice("0123456789") for _ in range(7))
    message = InboundMessage(
        f"wamid.agent.{secrets.token_hex(6)}", sender, None, "text", text, datetime.now(UTC)
    )
    use_case = RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )
    await use_case(InboundBatch(seed.phone_a, (message,)))
    conversation: UUID = await admin.fetchval(
        "SELECT v.id FROM public.conversations v JOIN public.customers c ON c.id = v.customer_id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        seed.org_a,
        sender,
    )
    return conversation


def _handler(pool: asyncpg.Pool, model: ScriptedChatModel) -> ProcessConversation:
    uow = unit_of_work_factory(pool)
    breaker = CircuitBreaker()

    def chat_model(organization_id: UUID, conversation_id: UUID) -> object:
        return resilient_chat_model(
            model, breaker=breaker, recorder=RecordLlmCalls(uow), pricing=PRICING, provider="anthropic",
            model="claude-haiku-5-5", purpose=AiPurpose.CHAT, organization_id=organization_id,
            conversation_id=conversation_id,
        )  # fmt: skip

    def toolbox(context: TurnContext, enabled: Sequence[str]) -> AgentToolbox:
        return AgentToolbox(uow, context, enabled=enabled)

    return ProcessConversation(
        unit_of_work=uow, chat_model=chat_model, toolbox=toolbox,  # type: ignore[arg-type]
        policies={"IMPORTADORA_REPUESTOS": Policy()},
    )  # fmt: skip


def _job(seed: Seed, conversation_id: UUID) -> Job:
    return Job(
        id=1,
        kind=PROCESS_CONVERSATION,
        organization_id=seed.org_a,
        conversation_key=str(conversation_id),
        payload={"conversation_id": str(conversation_id)},
    )


async def test_un_turno_completo_deja_una_respuesta_y_su_costo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, f"¿tienen {seed.sku_bob_a}?")
    search = ToolCall(id="t1", name="search_catalog", arguments={"query": seed.sku_bob_a})
    model = ScriptedChatModel(
        ModelResponse("", (search,), StopReason.TOOL_USE, TokenUsage(800, 40), "claude-haiku-5-5"),
        ModelResponse("Sí, a 480,00 Bs.", (), StopReason.END_TURN, TokenUsage(900, 30), "claude-haiku-5-5"),
    )

    await _handler(pool, model)(_job(seed, conversation))

    outbox = await admin.fetch(
        "SELECT kind, payload ->> 'body' AS body FROM private.outbound_messages WHERE conversation_id = $1",
        conversation,
    )
    assert [(r["kind"], r["body"]) for r in outbox] == [("TEXT", "Sí, a 480,00 Bs.")]
    history = await admin.fetch(
        "SELECT direction, sender, body, processed_at FROM public.messages WHERE conversation_id = $1"
        " ORDER BY created_at, id",
        conversation,
    )
    assert [(r["direction"], r["sender"]) for r in history] == [("IN", "CUSTOMER"), ("OUT", "BOT")]
    assert history[0]["processed_at"] is not None
    tool_result = model.calls[1][1][-1].tool_results[0].content
    assert '"480.00"' in tool_result  # el precio lo trajo la herramienta desde la BD
    costs = await admin.fetch(
        "SELECT outcome, input_tokens FROM private.llm_calls WHERE conversation_id = $1 ORDER BY id",
        conversation,
    )
    assert [(r["outcome"], r["input_tokens"]) for r in costs] == [("OK", 800), ("OK", 900)]


async def test_repetir_el_trabajo_no_duplica_la_respuesta(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "hola")
    handler = _handler(pool, ScriptedChatModel(
        ModelResponse("¡Hola! ¿Qué repuesto buscas?", (), StopReason.END_TURN, TokenUsage(10, 5), "m")
    ))  # fmt: skip

    await handler(_job(seed, conversation))
    await handler(_job(seed, conversation))  # el worker murió después del COMMIT y el trabajo se repite

    count = await admin.fetchval(
        "SELECT count(*) FROM private.outbound_messages WHERE conversation_id = $1", conversation
    )
    assert count == 1


async def test_con_una_persona_a_cargo_no_responde(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "quiero hablar con alguien")
    await admin.execute("UPDATE public.conversations SET mode = 'HUMAN' WHERE id = $1", conversation)
    model = ScriptedChatModel(ModelResponse("no", (), StopReason.END_TURN, TokenUsage(1, 1), "m"))

    await _handler(pool, model)(_job(seed, conversation))

    assert model.calls == []
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM private.outbound_messages WHERE conversation_id = $1", conversation
        )
        == 0
    )
