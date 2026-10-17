from typing import Any

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
        meta_graph_api_version="v23.0",
    )

    assert "clave-de-prueba" not in repr(settings)
    assert "secreto-de-prueba" not in repr(settings)


def test_valores_desconocidos_de_entorno_se_rechazan() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prod")


@pytest.mark.parametrize("version", ["23.0", "latest", "v23", "v23.0/../me"])
def test_la_version_de_la_graph_api_se_valida(version: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="local", meta_graph_api_version=version)


_PRODUCTION: dict[str, Any] = {
    "environment": "production",
    "database_url": "postgresql://app_backend:clave-de-prueba@localhost/postgres",
    "meta_app_secret": "secreto-de-prueba",
    "meta_webhook_verify_token": "token-de-prueba",
    "meta_graph_api_version": "v23.0",
}


def test_en_produccion_el_colector_otlp_debe_ser_https() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(_env_file=None, **_PRODUCTION, otel_exporter_otlp_endpoint="http://colector:4318")

    settings = Settings(_env_file=None, **_PRODUCTION, otel_exporter_otlp_endpoint="https://otlp.example.com")
    assert settings.otel_exporter_otlp_endpoint == "https://otlp.example.com"


def test_en_local_el_colector_puede_ser_http() -> None:
    settings = Settings(
        _env_file=None, environment="local", otel_exporter_otlp_endpoint="http://localhost:4318"
    )

    assert settings.otel_exporter_otlp_endpoint == "http://localhost:4318"


@pytest.mark.parametrize("endpoint", ["localhost:4318", "ftp://colector", "https://colector con espacio"])
def test_el_endpoint_otlp_se_valida(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="local", otel_exporter_otlp_endpoint=endpoint)


def test_el_dsn_de_sentry_no_aparece_al_imprimir_la_configuracion() -> None:
    settings = Settings(
        _env_file=None, environment="local", sentry_dsn="https://clave-publica@sentry.invalid/1"
    )

    assert "clave-publica" not in repr(settings)
