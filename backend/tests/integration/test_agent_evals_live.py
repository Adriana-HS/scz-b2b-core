"""Corrida de las evals golden con un modelo de verdad (#28). Solo con SCZ_RUN_EVALS=1.

Por decisión de AHS (18-oct-2026), a mano y con Gemini gratis: solo datos inventados (ADR-0019).
La configuración de la IA sale del entorno o del .env (LLM_PROVIDER, LLM_API_KEY, LLM_MODEL y los
precios). Deja el reporte en evals/conversations/results/<rubro>-latest.{json,md}, comparado con
la corrida anterior. No falla por la exactitud: es una medición (Q-ACC-5 y Q-ACC-6 quedan en el
reporte); falla si la corrida no se pudo hacer. Ver docs/TESTING.md.
"""

import asyncio
import os
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
import pytest

from agent_evals.report import summarize, to_json, write
from agent_evals.runner import EvalRunner
from agent_evals.scoring import CaseResult
from agent_evals.suite import load_suite
from app.application.ai import CircuitBreaker, RecordLlmCalls, resilient_chat_model
from app.core.config import Settings
from app.domain.ai import AiPurpose
from app.domain.ports import ChatModel
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory
from app.worker import build_chat_adapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SCZ_RUN_EVALS") != "1", reason="evals con IA: solo a mano (SCZ_RUN_EVALS=1)"
    ),
]

RESULTS = Path(__file__).resolve().parents[3] / "evals" / "conversations" / "results"
PAUSE_S = float(os.environ.get("SCZ_EVALS_PAUSE_S", "6"))  # el plan gratuito limita pedidos por minuto


@pytest.mark.parametrize("name", ["importadora", "clinica"])
async def test_evals_con_el_modelo_configurado(pool: asyncpg.Pool, name: str) -> None:
    settings = Settings()
    pricing = settings.llm_pricing()
    if settings.llm_api_key is None or settings.llm_model is None or pricing is None:
        pytest.fail("faltan LLM_API_KEY, LLM_MODEL o los precios LLM_PRICE_* en el entorno o el .env")
    assert settings.environment in ("local", "test"), "las evals usan datos inventados: solo local o test"
    model_id = settings.llm_model
    suite = load_suite(name)
    uow = unit_of_work_factory(pool)

    async with httpx.AsyncClient() as http:
        provider, adapter = build_chat_adapter(settings, http)
        breaker = CircuitBreaker()

        def chat_model(organization_id: UUID, conversation_id: UUID) -> ChatModel:
            return resilient_chat_model(
                adapter, breaker=breaker, recorder=RecordLlmCalls(uow), pricing=pricing, provider=provider,
                model=model_id, purpose=AiPurpose.CHAT, organization_id=organization_id,
                conversation_id=conversation_id,
            )  # fmt: skip

        runner = EvalRunner(unit_of_work=uow, resolver=PostgresChannelResolver(pool), chat_model=chat_model)
        results: list[CaseResult] = []
        for case in suite.cases:
            results += await runner.run(suite, [case])
            await asyncio.sleep(PAUSE_S)

    _, markdown = write(RESULTS, name, to_json(summarize(name, f"{provider}/{model_id}", results), results))
    print("\n" + markdown)  # noqa: T201 - el reporte de la corrida (pytest -s)
