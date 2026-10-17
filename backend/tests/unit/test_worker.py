import logging

import pytest

from app.core.config import Settings
from app.worker import main


def test_el_worker_arranca_con_configuracion_valida(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scz.worker")

    assert main(Settings(_env_file=None, environment="test")) == 0
    assert "worker iniciado (entorno=test)" in caplog.text
