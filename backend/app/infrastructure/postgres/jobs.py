"""Cola durable y mantenimiento sobre las funciones de la BD (migraciones 06 y 15).

Son funciones SECURITY DEFINER sin tenant (la cola es de toda la plataforma), solo para
`app_backend`. El tenant de cada trabajo viaja en `organization_id`; el handler abre su
propia UnitOfWork con ese tenant.
"""

import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import asyncpg

from app.domain.jobs import Job
from app.infrastructure.postgres.errors import translate_errors

_CLAIM = """
    SELECT id, kind, organization_id, conversation_key, payload, attempts, max_attempts
    FROM private.claim_jobs($1, $2, $3::text[])
"""
_FINISH = "SELECT private.finish_job($1, $2, $3, $4, $5)"


def _payload(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


class PostgresJobQueue:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def claim(self, worker_id: str, limit: int, kinds: Sequence[str]) -> list[Job]:
        with translate_errors():
            rows = await self._pool.fetch(_CLAIM, worker_id, limit, list(kinds))
        return [
            Job(
                id=row["id"],
                kind=row["kind"],
                organization_id=row["organization_id"],
                conversation_key=row["conversation_key"],
                payload=_payload(row["payload"]),
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
            )
            for row in rows
        ]

    async def complete(self, job_id: int, worker_id: str) -> str | None:
        with translate_errors():
            status: str | None = await self._pool.fetchval(_FINISH, job_id, worker_id, True, None, True)
        return status

    async def fail(self, job_id: int, worker_id: str, error: str, *, retry: bool) -> str | None:
        with translate_errors():
            status: str | None = await self._pool.fetchval(_FINISH, job_id, worker_id, False, error, retry)
        return status


class PostgresMaintenance:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def requeue_stale(self, lease: timedelta) -> tuple[int, int]:
        with translate_errors():
            row = await self._pool.fetchrow(
                "SELECT requeued, dead FROM private.requeue_stale_jobs($1)", lease
            )
        return (row["requeued"], row["dead"]) if row is not None else (0, 0)

    async def expire_stale_orders(self) -> int:
        with translate_errors():
            expired: int = await self._pool.fetchval("SELECT private.expire_stale_orders()")
        return expired

    async def purge_retention(self) -> None:
        with translate_errors():
            await self._pool.execute("SELECT private.purge_retention()")
