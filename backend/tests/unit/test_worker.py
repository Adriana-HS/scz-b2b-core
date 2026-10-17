import logging

import pytest

from app.core.config import Settings
from app.worker import main


def test_sin_base_de_datos_el_worker_no_arranca(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scz.worker")

    assert main(Settings(_env_file=None, environment="test")) == 2
    assert "DATABASE_URL" in caplog.text


def test_el_lease_debe_ser_mayor_que_el_timeout_de_un_trabajo() -> None:
    # Si no, el reaper le quitaría trabajos vivos a un worker sano: doble procesamiento
    with pytest.raises(ValueError, match="job_lease_s"):
        Settings(_env_file=None, environment="test", job_timeout_s=120, job_lease_s=120)


def test_valores_por_defecto_del_worker() -> None:
    settings = Settings(_env_file=None, environment="test")

    assert (settings.worker_concurrency, settings.job_timeout_s, settings.job_lease_s) == (16, 60.0, 300)


async def test_run_worker_exige_base_de_datos() -> None:
    from app.worker import run_worker

    with pytest.raises(ValueError, match="DATABASE_URL"):
        await run_worker(Settings(_env_file=None, environment="test"))
