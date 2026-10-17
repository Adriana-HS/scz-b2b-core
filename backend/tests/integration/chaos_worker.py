"""Worker de prueba para la prueba de caos (no es una prueba: lo lanza test_worker_db.py).

Reclama un trabajo T_CHAOS, abre una UnitOfWork, escribe un mensaje y se queda colgado con
la transacción abierta, hasta que la prueba mata el proceso. Imprime CLAIMED <job_id> cuando
el trabajo está a medias. Configuración por variables de entorno (solo datos sintéticos).
"""

import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from app.application.worker import JobRunner
from app.domain.jobs import Job
from app.domain.messaging import InboundMessage
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.jobs import PostgresJobQueue
from app.infrastructure.postgres.pool import create_pool
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork


async def _main() -> None:
    pool = await create_pool(os.environ["SCZ_CHAOS_DSN"], min_size=1, max_size=2,
                             application_name=os.environ["SCZ_CHAOS_APP_NAME"])  # fmt: skip
    org = UUID(os.environ["SCZ_CHAOS_ORG"])
    conversation = UUID(os.environ["SCZ_CHAOS_CONVERSATION"])
    wamid = os.environ["SCZ_CHAOS_WAMID"]

    async def hang_mid_job(job: Job) -> None:
        async with PostgresUnitOfWork(pool, TenantContext(org, Actor.SYSTEM)) as uow:
            message = InboundMessage(wamid, "59170000001", None, "text", "respuesta", datetime.now(UTC))
            await uow.inbound.add_message(conversation, message)  # queda sin COMMIT
            sys.stdout.write(f"CLAIMED {job.id}\n")
            sys.stdout.flush()
            await asyncio.Event().wait()  # colgado hasta que lo maten

    runner = JobRunner(
        PostgresJobQueue(pool), {"T_CHAOS": hang_mid_job}, worker_id=os.environ["SCZ_CHAOS_WORKER"]
    )
    while await runner.poll_once() == 0:  # noqa: ASYNC110 - sondeo de la cola, como el worker real
        await asyncio.sleep(0.05)
    await runner.drain()


if __name__ == "__main__":
    asyncio.run(_main())
