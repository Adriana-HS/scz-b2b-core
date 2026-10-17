"""JobRunner y mantenimiento con dobles (sin BD): reclamo continuo, fencing, errores sin PII, alertas."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import timedelta

import pytest

from app.application.worker import JobRunner, MaintenanceSchedule, describe_error
from app.domain.errors import BusinessRuleError, ErrorCode, PermanentJobError
from app.domain.jobs import Job

PII = "59170000001 mi carnet 1234567"


def _job(job_id: int, kind: str = "T_OK", attempts: int = 1) -> Job:
    return Job(
        id=job_id, kind=kind, organization_id=None, conversation_key=None, attempts=attempts, max_attempts=8
    )


class FakeQueue:
    def __init__(
        self, jobs: list[Job], *, finish_status: str | None = "DONE", fail_status: str | None = "QUEUED"
    ):
        self.pending = list(jobs)
        self.claims: list[tuple[int, tuple[str, ...]]] = []
        self.completed: list[int] = []
        self.failed: list[tuple[int, str, bool]] = []
        self.finish_status = finish_status
        self.fail_status = fail_status
        self.claim_error: Exception | None = None

    async def claim(self, worker_id: str, limit: int, kinds: Sequence[str]) -> list[Job]:
        if self.claim_error:
            raise self.claim_error
        self.claims.append((limit, tuple(kinds)))
        taken = [j for j in self.pending if j.kind in kinds][:limit]
        self.pending = [j for j in self.pending if j not in taken]
        return taken

    async def complete(self, job_id: int, worker_id: str) -> str | None:
        self.completed.append(job_id)
        return self.finish_status

    async def fail(self, job_id: int, worker_id: str, error: str, *, retry: bool) -> str | None:
        self.failed.append((job_id, error, retry))
        return self.fail_status


def _runner(queue: FakeQueue, handlers: dict, **kwargs: object) -> JobRunner:  # type: ignore[type-arg]
    return JobRunner(queue, handlers, worker_id="w-test", **kwargs)  # type: ignore[arg-type]


async def ok_handler(job: Job) -> None:
    return None


async def test_solo_reclama_los_tipos_que_sabe_procesar() -> None:
    queue = FakeQueue([_job(1, "T_OK"), _job(2, "T_SIN_HANDLER")])
    runner = _runner(queue, {"T_OK": ok_handler})

    await runner.poll_once()
    await runner.drain()

    assert queue.claims == [(16, ("T_OK",))]
    assert queue.completed == [1]
    assert [j.id for j in queue.pending] == [2]  # nunca se tocó: no va a DEAD por falta de handler


async def test_sin_handlers_no_reclama_nada() -> None:
    queue = FakeQueue([_job(1)])

    assert await _runner(queue, {}).poll_once() == 0
    assert queue.claims == []


async def test_un_error_se_guarda_sin_el_mensaje_y_se_reintenta() -> None:
    async def failing(job: Job) -> None:
        raise RuntimeError(PII)

    queue = FakeQueue([_job(1)])
    runner = _runner(queue, {"T_OK": failing})
    await runner.poll_once()
    await runner.drain()

    assert queue.failed == [(1, "RuntimeError", True)]
    assert "1234567" not in queue.failed[0][1]


async def test_los_errores_de_dominio_guardan_su_codigo() -> None:
    assert describe_error(BusinessRuleError(ErrorCode.OUT_OF_STOCK, PII)) == "BusinessRuleError:OUT_OF_STOCK"
    assert describe_error(ValueError(PII)) == "ValueError"


async def test_un_error_permanente_no_se_reintenta() -> None:
    async def permanent(job: Job) -> None:
        raise PermanentJobError("payload inválido")

    queue = FakeQueue([_job(1)], fail_status="DEAD")
    runner = _runner(queue, {"T_OK": permanent})
    await runner.poll_once()
    await runner.drain()

    assert queue.failed == [(1, "PermanentJobError", False)]


async def test_un_trabajo_que_excede_el_timeout_se_corta_y_se_reintenta() -> None:
    async def slow(job: Job) -> None:
        await asyncio.sleep(10)

    queue = FakeQueue([_job(1)])
    runner = _runner(queue, {"T_OK": slow}, job_timeout_s=0.05)
    await runner.poll_once()
    await runner.drain()

    assert queue.failed == [(1, "TimeoutError", True)]


async def test_dead_genera_alerta_en_el_log(caplog: pytest.LogCaptureFixture) -> None:
    async def failing(job: Job) -> None:
        raise RuntimeError(PII)

    caplog.set_level(logging.WARNING, logger="scz.worker")
    queue = FakeQueue([_job(7, attempts=8)], fail_status="DEAD")
    runner = _runner(queue, {"T_OK": failing})
    await runner.poll_once()
    await runner.drain()

    alerts = [r for r in caplog.records if r.levelno == logging.ERROR and "ALERTA job_dead" in r.getMessage()]
    assert len(alerts) == 1
    assert "id=7" in alerts[0].getMessage() and "kind=T_OK" in alerts[0].getMessage()
    assert "1234567" not in caplog.text
    assert runner.stats.dead == 1


async def test_lease_perdido_se_registra_como_advertencia(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="scz.worker")
    queue = FakeQueue([_job(1)], finish_status=None)  # finish_job devolvió NULL: otro worker tiene el trabajo
    runner = _runner(queue, {"T_OK": ok_handler})
    await runner.poll_once()
    await runner.drain()

    assert "lease perdido" in caplog.text
    assert runner.stats.lost_leases == 1


async def test_la_concurrencia_esta_acotada_y_se_reclama_solo_lo_que_se_libera() -> None:
    release = asyncio.Event()
    started = 0

    async def blocking(job: Job) -> None:
        nonlocal started
        started += 1
        await release.wait()

    queue = FakeQueue([_job(i) for i in range(1, 6)])
    runner = _runner(queue, {"T_OK": blocking}, concurrency=2)

    assert await runner.poll_once() == 2
    await asyncio.sleep(0)
    assert await runner.poll_once() == 0  # sin espacio libre no se reclama
    assert queue.claims == [(2, ("T_OK",))]
    release.set()
    await runner.drain()
    assert await runner.poll_once() == 2
    await runner.drain()
    assert started == 4


async def test_run_se_detiene_y_espera_los_trabajos_en_curso() -> None:
    finished: list[int] = []

    async def slowish(job: Job) -> None:
        await asyncio.sleep(0.05)
        finished.append(job.id)

    queue = FakeQueue([_job(1), _job(2)])
    runner = _runner(queue, {"T_OK": slowish}, poll_interval_s=0.01)
    stop = asyncio.Event()

    task = asyncio.create_task(runner.run(stop))
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert sorted(finished) == [1, 2]  # apagado ordenado: no deja trabajos a medias
    assert sorted(queue.completed) == [1, 2]


async def test_si_la_bd_falla_al_reclamar_el_worker_sigue_vivo(caplog: pytest.LogCaptureFixture) -> None:
    queue = FakeQueue([])
    queue.claim_error = ConnectionError("bd caída")
    runner = _runner(queue, {"T_OK": ok_handler})

    assert await runner.poll_once() == 0
    assert "ConnectionError" in caplog.text


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_el_mantenimiento_corre_cada_tarea_segun_su_intervalo() -> None:
    calls: list[str] = []
    clock = FakeClock()

    async def every_minute() -> None:
        calls.append("minuto")

    async def daily() -> None:
        calls.append("diaria")

    schedule = MaintenanceSchedule(
        [("minuto", timedelta(minutes=1), every_minute), ("diaria", timedelta(days=1), daily)], clock=clock
    )

    await schedule.run_due()  # al arrancar corre todo
    clock.now = 30
    await schedule.run_due()  # nada vencido
    clock.now = 61
    await schedule.run_due()

    assert calls == ["minuto", "diaria", "minuto"]


async def test_una_tarea_de_mantenimiento_que_falla_no_detiene_las_demas(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    async def broken() -> None:
        raise ConnectionError(PII)

    async def fine() -> None:
        calls.append("ok")

    schedule = MaintenanceSchedule(
        [("rota", timedelta(minutes=1), broken), ("sana", timedelta(minutes=1), fine)], clock=FakeClock()
    )
    await schedule.run_due()

    assert calls == ["ok"]
    assert "ConnectionError" in caplog.text and "1234567" not in caplog.text


class FakeMaintenance:
    def __init__(self, requeued: int, dead: int) -> None:
        self.result = (requeued, dead)
        self.calls: list[str] = []

    async def requeue_stale(self, lease: timedelta) -> tuple[int, int]:
        self.calls.append(f"requeue:{int(lease.total_seconds())}")
        return self.result

    async def expire_stale_orders(self) -> int:
        self.calls.append("expire")
        return 3

    async def purge_retention(self) -> None:
        self.calls.append("purge")


async def test_mantenimiento_por_defecto_alerta_los_dead_del_reaper(caplog: pytest.LogCaptureFixture) -> None:
    from app.application.worker import default_maintenance

    caplog.set_level(logging.INFO, logger="scz.worker")
    fake = FakeMaintenance(requeued=2, dead=1)

    await default_maintenance(fake, lease=timedelta(minutes=5)).run_due()

    assert fake.calls == ["requeue:300", "expire", "purge"]
    assert "2 trabajos con el lease vencido" in caplog.text
    assert "ALERTA job_dead count=1" in caplog.text
    assert "3 órdenes vencidas" in caplog.text


async def test_si_la_bd_sigue_caida_el_worker_espera_cada_vez_mas() -> None:
    queue = FakeQueue([])
    queue.claim_error = ConnectionError("bd caída")
    runner = _runner(queue, {"T_OK": ok_handler}, poll_interval_s=0.25)

    waits = []
    for _ in range(9):
        await runner.poll_once()
        waits.append(runner._idle_wait())

    assert waits[:3] == [0.5, 1.0, 2.0]
    assert waits[-1] == 30.0  # tope
    queue.claim_error = None
    await runner.poll_once()
    assert runner._idle_wait() == 0.25  # al recuperarse vuelve al sondeo normal
