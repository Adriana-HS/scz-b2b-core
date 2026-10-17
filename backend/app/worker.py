"""Proceso worker: composition root de los handlers de la cola durable (ADR-0001, ADR-0005).

Ejecutar en local:  uv run python -m app.worker   (requiere DATABASE_URL)

Handlers registrados: ninguno todavía. Llegan con sus issues: EMBED_ITEM (#25),
PROCESS_CONVERSATION (#26) y PRICE_CHANGE_ALERT. Hasta entonces el worker solo corre el
mantenimiento (vencimientos, reaper, retención) y NO reclama esos trabajos: quedan en la
cola, sin gastar intentos, hasta que exista quien los procese.
"""

import asyncio
import contextlib
import logging
import os
import secrets
import signal
import socket
from collections.abc import Mapping
from datetime import timedelta

from app.application.worker import JobHandler, JobRunner, default_maintenance
from app.core.config import Settings, get_settings
from app.infrastructure.postgres.jobs import PostgresJobQueue, PostgresMaintenance
from app.infrastructure.postgres.pool import create_pool

logger = logging.getLogger("scz.worker")


def build_handlers() -> Mapping[str, JobHandler]:
    """Tipo de trabajo -> handler. Agregar aquí cada handler nuevo (y su prueba)."""
    return {}


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
    try:
        runner = JobRunner(
            PostgresJobQueue(pool),
            build_handlers(),
            worker_id=worker_id,
            concurrency=settings.worker_concurrency,
            job_timeout_s=settings.job_timeout_s,
            poll_interval_s=settings.worker_poll_interval_s,
            maintenance=default_maintenance(
                PostgresMaintenance(pool), lease=timedelta(seconds=settings.job_lease_s)
            ),
        )
        if stop is None:
            stop = asyncio.Event()
            _install_stop_signals(stop)
        await runner.run(stop)
    finally:
        await pool.close()


def main(settings: Settings | None = None) -> int:
    """Arranca el worker y devuelve el código de salida del proceso."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    if settings.database_url is None:
        logger.error("worker: falta DATABASE_URL; no hay cola que procesar")
        return 2
    logger.info("worker iniciado (entorno=%s)", settings.environment)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_worker(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
