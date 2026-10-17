"""Worker contra Postgres real como `app_backend` (#17, ADR-0005, Q-REL-1)."""

import asyncio
import os
import secrets
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest

from app.application.worker import JobRunner
from app.domain.jobs import Job
from app.domain.messaging import InboundMessage
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.jobs import PostgresJobQueue, PostgresMaintenance
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork

from .conftest import Seed

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


async def _enqueue(admin: asyncpg.Connection, kind: str, **cols: object) -> int:
    keys = ", ".join(["kind", *cols])
    params = ", ".join(f"${i}" for i in range(1, len(cols) + 2))
    job_id: int = await admin.fetchval(
        f"INSERT INTO private.jobs ({keys}) VALUES ({params}) RETURNING id",  # noqa: S608 - columnas fijas de la prueba
        kind,
        *cols.values(),
    )
    return job_id


async def _job(admin: asyncpg.Connection, job_id: int) -> asyncpg.Record:
    row = await admin.fetchrow(
        "SELECT status, attempts, locked_by, last_error FROM private.jobs WHERE id = $1", job_id
    )
    assert row is not None
    return row


async def test_un_trabajo_exitoso_queda_done_y_el_payload_llega_como_dict(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    kind = f"T_OK_{secrets.token_hex(3)}"
    job_id = await _enqueue(admin, kind, payload='{"conversation_id": "abc"}')
    seen: list[Job] = []

    async def handler(job: Job) -> None:
        seen.append(job)

    runner = JobRunner(PostgresJobQueue(pool), {kind: handler}, worker_id="w-int")
    assert await runner.poll_once() == 1
    await runner.drain()

    assert seen[0].payload == {"conversation_id": "abc"} and seen[0].attempts == 1
    assert (await _job(admin, job_id))["status"] == "DONE"


async def test_un_fallo_guarda_solo_el_tipo_de_error_y_vuelve_a_la_cola(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    kind = f"T_FAIL_{secrets.token_hex(3)}"
    job_id = await _enqueue(admin, kind)

    async def handler(job: Job) -> None:
        raise RuntimeError("Key (wa_id)=(59170000001) dato del cliente")

    runner = JobRunner(PostgresJobQueue(pool), {kind: handler}, worker_id="w-int")
    await runner.poll_once()
    await runner.drain()

    row = await _job(admin, job_id)
    assert (row["status"], row["last_error"]) == ("QUEUED", "RuntimeError")


async def test_el_mantenimiento_informa_los_trabajos_que_pasan_a_dead(
    admin: asyncpg.Connection, pool: asyncpg.Pool
) -> None:
    kind = f"T_STALE_{secrets.token_hex(3)}"
    alive = await _enqueue(admin, kind)
    exhausted = await _enqueue(admin, kind)
    await admin.execute(
        """UPDATE private.jobs
              SET status = 'RUNNING', locked_by = 'w-muerto', locked_at = now() - interval '1 hour',
                  attempts = CASE WHEN id = $2 THEN max_attempts ELSE 1 END
            WHERE id = ANY($1::bigint[])""",
        [alive, exhausted],
        exhausted,
    )

    requeued, dead = await PostgresMaintenance(pool).requeue_stale(timedelta(minutes=5))

    assert requeued >= 1 and dead >= 1
    assert (await _job(admin, alive))["status"] == "QUEUED"
    assert (await _job(admin, exhausted))["status"] == "DEAD"


async def test_matar_el_worker_a_mitad_de_un_trabajo_no_pierde_ni_duplica(
    backend_dsn: str, pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    """Prueba de caos (Q-REL-1): el worker muere con una transacción abierta. Lo que escribió se
    revierte, el reaper libera el trabajo y otro worker lo termina. El mensaje queda UNA vez."""
    job_id = await _enqueue(admin, "T_CHAOS", organization_id=seed.org_a)
    wamid = f"wamid.chaos.{secrets.token_hex(6)}"
    app_name = f"scz-chaos-{secrets.token_hex(4)}"
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "SCZ_CHAOS_DSN": backend_dsn,
        "SCZ_CHAOS_APP_NAME": app_name,
        "SCZ_CHAOS_ORG": str(seed.org_a),
        "SCZ_CHAOS_CONVERSATION": str(seed.conversation_a),
        "SCZ_CHAOS_WAMID": wamid,
        "SCZ_CHAOS_WORKER": "w-que-muere",
    }
    with subprocess.Popen(  # noqa: ASYNC220 - Popen no bloquea; la lectura bloqueante va en un hilo
        [sys.executable, "-m", "tests.integration.chaos_worker"],
        cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    ) as child:  # fmt: skip
        assert child.stdout is not None and child.stderr is not None
        try:
            line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=60)
        finally:
            child.kill()  # el worker muere a mitad del trabajo, sin COMMIT
            child.wait(timeout=30)
        assert line.strip() == f"CLAIMED {job_id}", child.stderr.read()

    # Postgres revierte la transacción de un cliente caído al detectar la desconexión;
    # aquí se fuerza de inmediato para que la prueba no dependa de los keepalives de TCP.
    await admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = $1", app_name
    )
    row = await _job(admin, job_id)
    assert (row["status"], row["locked_by"]) == ("RUNNING", "w-que-muere")  # quedó colgado
    assert await admin.fetchval("SELECT count(*) FROM public.messages WHERE wa_message_id = $1", wamid) == 0

    requeued, _ = await PostgresMaintenance(pool).requeue_stale(timedelta(seconds=0))
    assert requeued >= 1

    async def finish_the_job(job: Job) -> None:
        async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.SYSTEM)) as uow:
            message = InboundMessage(wamid, "59170000001", None, "text", "respuesta", row_time)
            await uow.inbound.add_message(seed.conversation_a, message)

    row_time = await admin.fetchval("SELECT now()")
    survivor = JobRunner(PostgresJobQueue(pool), {"T_CHAOS": finish_the_job}, worker_id="w-sobreviviente")
    assert await survivor.poll_once() == 1
    await survivor.drain()

    final = await _job(admin, job_id)
    assert (final["status"], final["attempts"]) == ("DONE", 2)
    assert await admin.fetchval("SELECT count(*) FROM public.messages WHERE wa_message_id = $1", wamid) == 1


async def test_mantenimiento_real_vence_ordenes_y_purga(pool: asyncpg.Pool) -> None:
    maintenance = PostgresMaintenance(pool)

    assert isinstance(await maintenance.expire_stale_orders(), int)
    await maintenance.purge_retention()  # no lanza: app_backend tiene EXECUTE y es SECURITY DEFINER


async def test_el_proceso_worker_arranca_mantiene_y_se_apaga_ordenado(
    backend_dsn: str, admin: asyncpg.Connection
) -> None:
    from app.core.config import Settings
    from app.worker import run_worker

    stale = await _enqueue(admin, f"T_ARRANQUE_{secrets.token_hex(3)}")
    await admin.execute(
        "UPDATE private.jobs SET status = 'RUNNING', locked_by = 'w-viejo',"
        " locked_at = now() - interval '1 hour'"
        " WHERE id = $1",
        stale,
    )
    settings = Settings(_env_file=None, environment="test", database_url=backend_dsn)
    stop = asyncio.Event()

    task = asyncio.create_task(run_worker(settings, stop))
    await asyncio.sleep(1.0)  # alcanza para la primera ronda de mantenimiento
    stop.set()
    await asyncio.wait_for(task, timeout=15)

    assert (await _job(admin, stale))["status"] == "QUEUED"  # el reaper del worker lo liberó
