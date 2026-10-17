import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_fuera_de_local_sin_secretos_el_proceso_no_arranca(environment: str) -> None:
    with pytest.raises(ValidationError, match="database_url, meta_app_secret"):
        Settings(_env_file=None, environment=environment)


def test_en_local_los_secretos_son_opcionales() -> None:
    settings = Settings(_env_file=None, environment="local")

    assert settings.database_url is None


def test_los_secretos_no_aparecen_al_imprimir_la_configuracion() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql://app_backend:clave-de-prueba@localhost/postgres",
        meta_app_secret="secreto-de-prueba",
        meta_webhook_verify_token="token-de-prueba",
    )

    assert "clave-de-prueba" not in repr(settings)
    assert "secreto-de-prueba" not in repr(settings)


def test_valores_desconocidos_de_entorno_se_rechazan() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prod")
