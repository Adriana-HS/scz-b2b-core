from decimal import Decimal
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


_PRICES: dict[str, Any] = {
    "llm_price_input_usd_per_mtok": "3",
    "llm_price_output_usd_per_mtok": "15",
    "llm_price_cache_read_usd_per_mtok": "0.30",
    "llm_price_cache_write_usd_per_mtok": "3.75",
}


def test_el_modelo_de_ia_con_sus_precios_arma_el_tarifario() -> None:
    settings = Settings(_env_file=None, environment="local", llm_model="claude-sonnet-5-5", **_PRICES)

    pricing = settings.llm_pricing()
    assert pricing is not None and pricing.output_usd_per_mtok == Decimal("15")


def test_sin_modelo_de_ia_no_hay_tarifario() -> None:
    assert Settings(_env_file=None, environment="local").llm_pricing() is None


@pytest.mark.parametrize("model", ["claude-3-5-sonnet-latest", "Claude Sonnet", ""])
def test_el_modelo_de_ia_va_fijado_por_identificador_exacto(model: str) -> None:
    with pytest.raises(ValidationError, match="modelo"):
        Settings(_env_file=None, environment="local", llm_model=model, **_PRICES)


def test_un_modelo_sin_precios_no_arranca() -> None:
    """Sin precios el costo se registraría en cero: falla cerrado (Q-COST-1)."""
    partial = {k: v for k, v in _PRICES.items() if "output" not in k}
    with pytest.raises(ValidationError, match="precios"):
        Settings(_env_file=None, environment="local", llm_model="claude-sonnet-5-5", **partial)


def test_un_precio_negativo_se_rechaza() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, environment="local", llm_model="claude-sonnet-5-5",
            **{**_PRICES, "llm_price_input_usd_per_mtok": "-1"},
        )  # fmt: skip


def test_la_clave_de_ia_no_aparece_al_imprimir_la_configuracion() -> None:
    settings = Settings(_env_file=None, environment="local", llm_api_key="sk-ant-clave-de-prueba")

    assert "sk-ant-clave-de-prueba" not in repr(settings)
