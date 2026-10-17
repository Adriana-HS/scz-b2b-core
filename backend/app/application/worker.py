"""Bucle del worker: reclamo continuo, timeout, reintentos, DLQ con alerta y mantenimiento (ADR-0005).

* Reclamo continuo: mantiene hasta `concurrency` trabajos en curso y reclama solo los lugares
  libres. Un turno lento del agente no retrasa a las demás conversaciones.
* Solo reclama los tipos con handler: un worker que aún no sabe procesar un tipo no lo toca
  (si lo reclamara y fallara, el trabajo terminaría en DEAD).
* Cada trabajo tiene un timeout; un error se guarda solo por su TIPO (el mensaje puede traer
  datos del cliente) y se reintenta con backoff; PermanentJobError va directo a DEAD.
* Fencing: si complete/fail devuelve None, otro worker tiene el trabajo (este perdió el lease).
* DEAD produce un log de nivel ERROR con "ALERTA job_dead": lo recoge la alerta de §10.2 (#19).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from app.core import telemetry
from app.core.structured_logging import log_context
from app.domain.errors import CodedError, PermanentJobError
from app.domain.jobs import Job, JobStatus
from app.domain.ports import JobQueue, QueueMaintenance

logger = logging.getLogger("scz.worker")


class JobHandler(Protocol):
    async def __call__(self, job: Job) -> None: ...


def describe_error(exc: BaseException) -> str:
    """Lo que se guarda en jobs.last_error: el tipo (y el código estable si lo hay), nunca el mensaje."""
    name = type(exc).__name__
    return f"{name}:{exc.code.value}" if isinstance(exc, CodedError) else name


@dataclass
class RunnerStats:
    done: int = 0
    retried: int = 0
    dead: int = 0
    lost_leases: int = 0


class JobRunner:
    def __init__(
        self,
        queue: JobQueue,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str,
        concurrency: int = 16,
        job_timeout_s: float = 60.0,
        poll_interval_s: float = 0.25,
        maintenance: "MaintenanceSchedule | None" = None,
    ) -> None:
        self._queue = queue
        self._handlers = dict(handlers)
        self._kinds = tuple(sorted(self._handlers))
        self._worker_id = worker_id
        self._concurrency = concurrency
        self._timeout = job_timeout_s
        self._poll_interval = poll_interval_s
        self._maintenance = maintenance
        self._in_flight: set[asyncio.Task[None]] = set()
        self._claim_failures = 0
        self.stats = RunnerStats()

    async def poll_once(self) -> int:
        """Reclama hasta llenar los lugares libres y arranca esos trabajos. Devuelve cuántos tomó."""
        free = self._concurrency - len(self._in_flight)
        if not self._kinds or free <= 0:
            return 0
        try:
            jobs = await self._queue.claim(self._worker_id, free, self._kinds)
        except Exception as exc:
            self._claim_failures += 1
            logger.error("worker: no se pudo reclamar de la cola (%s); se reintenta", describe_error(exc))
            return 0
        self._claim_failures = 0
        for job in jobs:
            task = asyncio.create_task(self._process(job), name=f"job-{job.id}")
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
        return len(jobs)

    async def drain(self) -> None:
        """Espera a que terminen los trabajos en curso (apagado ordenado)."""
        while self._in_flight:
            await asyncio.gather(*tuple(self._in_flight), return_exceptions=True)

    async def run(self, stop: asyncio.Event) -> None:
        logger.info(
            "worker %s: procesa %s (concurrencia %d)",
            self._worker_id,
            self._kinds or "nada",
            self._concurrency,
        )
        while not stop.is_set():
            if self._maintenance is not None:
                await self._maintenance.run_due()
            if await self.poll_once() == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._idle_wait())
        logger.info(
            "worker %s: deteniéndose; esperando %d trabajos en curso", self._worker_id, len(self._in_flight)
        )
        await self.drain()

    def _idle_wait(self) -> float:
        """Sondeo normal; si la BD falla, espera exponencial (tope 30 s) para no inundar los logs."""
        if self._claim_failures == 0:
            return self._poll_interval
        return float(min(self._poll_interval * 2**self._claim_failures, 30.0))

    async def _process(self, job: Job) -> None:
        # La traza continúa la de quien encoló (webhook, outbox): trace_id de punta a punta
        parent = telemetry.context_from_traceparent(job.trace_context)
        span_attrs = telemetry.attributes(
            **{"job.id": job.id, "job.kind": job.kind, "job.attempt": job.attempts}
        )
        with (
            telemetry.tracer.start_as_current_span(
                f"job {job.kind}", context=parent, attributes=span_attrs, record_exception=False
            ),
            log_context(job_id=job.id, job_kind=job.kind, organization_id=job.organization_id),
        ):
            started = time.monotonic()
            status = await self._run_handler(job)
            telemetry.job_duration_ms.record(
                (time.monotonic() - started) * 1000, telemetry.attributes(kind=job.kind)
            )
            self._account(job, status)

    async def _run_handler(self, job: Job) -> str | None:
        handler = self._handlers[job.kind]
        try:
            async with asyncio.timeout(self._timeout):
                await handler(job)
        except PermanentJobError as exc:
            status = await self._safe(
                self._queue.fail(job.id, self._worker_id, describe_error(exc), retry=False)
            )
        except Exception as exc:
            status = await self._safe(
                self._queue.fail(job.id, self._worker_id, describe_error(exc), retry=True)
            )
        else:
            status = await self._safe(self._queue.complete(job.id, self._worker_id))
        return status

    async def _safe(self, call: Awaitable[str | None]) -> str | None:
        try:
            return await call
        except Exception as exc:  # la BD cayó al cerrar: el reaper lo reintentará al vencer el lease
            logger.error("worker: no se pudo cerrar un trabajo (%s)", describe_error(exc))
            return None

    def _account(self, job: Job, status: str | None) -> None:
        outcomes: dict[str | None, str] = {None: "lost", JobStatus.DONE: "done", JobStatus.DEAD: "dead"}
        outcome = outcomes.get(status, "retried")
        telemetry.jobs_processed.add(1, telemetry.attributes(kind=job.kind, outcome=outcome))
        if status is None:
            self.stats.lost_leases += 1
            logger.warning(
                "worker: lease perdido en el trabajo id=%d kind=%s; otro worker lo tiene", job.id, job.kind
            )
        elif status == JobStatus.DONE:
            self.stats.done += 1
        elif status == JobStatus.DEAD:
            self.stats.dead += 1
            telemetry.jobs_dead.add(1, telemetry.attributes(kind=job.kind))
            logger.error("ALERTA job_dead id=%d kind=%s attempts=%d", job.id, job.kind, job.attempts)
        else:
            self.stats.retried += 1


MaintenanceTask = tuple[str, timedelta, Callable[[], Awaitable[None]]]


class MaintenanceSchedule:
    """Corre cada tarea cuando venció su intervalo. Una tarea que falla no frena a las demás."""

    def __init__(
        self, tasks: Sequence[MaintenanceTask], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._tasks = list(tasks)
        self._clock = clock
        self._last_run: dict[str, float] = {}

    async def run_due(self) -> None:
        now = self._clock()
        for name, interval, task in self._tasks:
            last = self._last_run.get(name)
            if last is not None and now - last < interval.total_seconds():
                continue
            self._last_run[name] = now
            try:
                await task()
            except Exception as exc:
                logger.error("worker: falló la tarea de mantenimiento %s (%s)", name, describe_error(exc))


def default_maintenance(
    maintenance: QueueMaintenance, *, lease: timedelta, kinds: Sequence[str] = ()
) -> MaintenanceSchedule:
    """Las tareas de pg_cron (migración 06), corridas por el worker: Supabase no trae pg_cron activo.

    `kinds`: los tipos con handler; el estado de la cola se mide solo para ellos (un tipo sin
    handler todavía espera a propósito y no debe disparar "cola atascada")."""

    async def requeue() -> None:
        requeued, dead = await maintenance.requeue_stale(lease)
        if requeued:
            logger.warning("worker: %d trabajos con el lease vencido vuelven a la cola", requeued)
        if dead:
            telemetry.jobs_dead.add(dead, telemetry.attributes(kind="lease_vencido"))
            logger.error("ALERTA job_dead count=%d motivo=lease_vencido_sin_intentos", dead)

    async def expire() -> None:
        expired = await maintenance.expire_stale_orders()
        if expired:
            logger.info("worker: %d órdenes vencidas", expired)

    async def platform_snapshot() -> None:
        stats = await maintenance.queue_stats(kinds) if kinds else []
        fx_stale = await maintenance.fx_stale_tenants()
        telemetry.set_platform_snapshot(kinds=kinds, queues=stats, fx_stale_tenants=fx_stale)

    return MaintenanceSchedule(
        [
            ("requeue_stale_jobs", timedelta(minutes=1), requeue),
            ("platform_snapshot", timedelta(minutes=1), platform_snapshot),
            ("expire_stale_orders", timedelta(minutes=1), expire),
            ("purge_retention", timedelta(days=1), maintenance.purge_retention),
        ]
    )
