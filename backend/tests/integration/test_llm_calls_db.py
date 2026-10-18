"""Costo de IA en private.llm_calls contra Postgres real como `app_backend` (#22, Q-COST-1)."""

import secrets
from decimal import Decimal

import asyncpg
import pytest

from app.application.ai import RecordLlmCalls
from app.domain.ai import AiPurpose, LlmCall, TokenUsage
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration


def _call(org: object, trace_id: str, **overrides: object) -> LlmCall:
    values: dict[str, object] = {
        "organization_id": org,
        "purpose": AiPurpose.CHAT,
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "usage": TokenUsage(input_tokens=1200, output_tokens=350, cache_read_tokens=8000),
        "cost_usd": Decimal("0.011625"),
        "latency_ms": 1840,
        "tool_calls": 2,
        "outcome": "OK",
        "trace_id": trace_id,
    }
    values.update(overrides)
    return LlmCall(**values)  # type: ignore[arg-type]


async def test_el_costo_queda_exacto_en_el_tenant_de_la_llamada(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    trace_id = secrets.token_hex(16)

    await RecordLlmCalls(unit_of_work_factory(pool)).record(
        _call(seed.org_a, trace_id, conversation_id=seed.conversation_a)
    )

    row = await admin.fetchrow("SELECT * FROM private.llm_calls WHERE trace_id = $1", trace_id)
    assert row is not None
    assert row["organization_id"] == seed.org_a and row["conversation_id"] == seed.conversation_a
    assert row["cost_usd"] == Decimal("0.011625")  # numeric(12,6), sin pasar por float
    assert (row["input_tokens"], row["output_tokens"], row["cached_tokens"]) == (1200, 350, 8000)
    assert (row["purpose"], row["provider"], row["model"], row["outcome"]) == (
        "CHAT", "anthropic", "claude-sonnet-5-5", "OK",
    )  # fmt: skip
    assert (row["latency_ms"], row["tool_calls"]) == (1840, 2)


async def test_una_conversacion_de_otro_negocio_se_rechaza(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    """La fila va al tenant de la UnitOfWork; una conversación ajena no pasa la FK compuesta
    (migración 19)."""
    trace_id = secrets.token_hex(16)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await RecordLlmCalls(unit_of_work_factory(pool)).record(
            _call(seed.org_b, trace_id, conversation_id=seed.conversation_a)
        )

    assert await admin.fetchval("SELECT count(*) FROM private.llm_calls WHERE trace_id = $1", trace_id) == 0


async def test_cada_negocio_ve_solo_sus_llamadas(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    trace_a, trace_b = secrets.token_hex(16), secrets.token_hex(16)
    recorder = RecordLlmCalls(unit_of_work_factory(pool))

    await recorder.record(_call(seed.org_a, trace_a))
    await recorder.record(_call(seed.org_b, trace_b, conversation_id=None))

    rows = await admin.fetch(
        "SELECT trace_id, organization_id FROM private.llm_calls WHERE trace_id = ANY($1::text[])",
        [trace_a, trace_b],
    )
    assert {(r["trace_id"], r["organization_id"]) for r in rows} == {
        (trace_a, seed.org_a),
        (trace_b, seed.org_b),
    }
