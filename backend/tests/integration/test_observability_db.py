"""Traza de punta a punta y métricas de la plataforma contra Postgres real (#19, migración 18)."""

import secrets

import asyncpg
import pytest

from app.core import telemetry
from app.domain.outbound import OutboundRequest
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.jobs import PostgresJobQueue, PostgresMaintenance
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork

from .conftest import Seed
from .test_outbox_db import _new_conversation

pytestmark = pytest.mark.integration


async def _trace_of_outbound_job(admin: asyncpg.Connection, outbound_id: int) -> str | None:
    value: str | None = await admin.fetchval(
        "SELECT trace_context FROM private.jobs"
        " WHERE kind = 'SEND_OUTBOUND' AND payload ->> 'outbound_id' = $1",
        str(outbound_id),
    )
    return value


async def test_el_trabajo_encolado_guarda_la_traza_de_quien_lo_encolo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, _ = await _new_conversation(pool, seed, admin)
    request = OutboundRequest.text(conversation_id, f"obs-{secrets.token_hex(6)}", "hola")

    with telemetry.tracer.start_as_current_span("POST /v1/webhooks/whatsapp") as span:
        async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
            outbound_id = await uow.outbound.enqueue(request)
    assert outbound_id is not None

    stored = await _trace_of_outbound_job(admin, outbound_id)
    assert stored is not None
    assert stored.split("-")[1] == format(span.get_span_context().trace_id, "032x")


async def test_sin_traza_activa_el_trabajo_queda_sin_trace_context(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, _ = await _new_conversation(pool, seed, admin)
    request = OutboundRequest.text(conversation_id, f"obs-{secrets.token_hex(6)}", "hola")

    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        outbound_id = await uow.outbound.enqueue(request)
    assert outbound_id is not None

    assert await _trace_of_outbound_job(admin, outbound_id) is None


async def test_el_worker_recibe_el_trace_context_al_reclamar(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    kind = f"T_TRACE_{secrets.token_hex(3)}"
    traceparent = f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
    await admin.execute("INSERT INTO private.jobs (kind, trace_context) VALUES ($1, $2)", kind, traceparent)

    [job] = await PostgresJobQueue(pool).claim("w-obs", 1, [kind])

    assert job.trace_context == traceparent
    await PostgresJobQueue(pool).complete(job.id, "w-obs")


async def test_estado_de_la_cola_y_tc_vencido_para_las_metricas(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    kind = f"T_STATS_{secrets.token_hex(3)}"
    await admin.execute(
        "INSERT INTO private.jobs (kind, run_after) VALUES ($1, now() - interval '90 seconds'),"
        " ($1, now() + interval '1 hour')",
        kind,
    )
    maintenance = PostgresMaintenance(pool)

    [stats] = await maintenance.queue_stats([kind, f"{kind}_SIN_FILAS"])
    stale = await maintenance.fx_stale_tenants()

    assert (stats.kind, stats.queued, stats.running, stats.dead) == (kind, 2, 0, 0)
    assert 90 <= stats.oldest_ready_s < 3600  # el listo espera 90 s; el de backoff no cuenta
    assert isinstance(stale, int) and stale >= 0
    await admin.execute("DELETE FROM private.jobs WHERE kind = $1", kind)
