"""Arranque de la observabilidad de un proceso (api o worker): SYSTEM_DESIGN.md §10.2, #19.

Se llama al arrancar el proceso (lifespan de la api, main del worker), no al importar
módulos: así las pruebas pueden instalar exportadores en memoria antes.
"""

from app.core import telemetry
from app.core.config import Settings
from app.core.sentry import init_sentry
from app.core.structured_logging import configure_logging


def setup_observability(settings: Settings, *, service_name: str) -> None:
    """Logs JSON sin PII, OpenTelemetry (exporta solo con endpoint) y Sentry (solo con DSN)."""
    configure_logging(settings.log_level)
    telemetry.configure_telemetry(service_name, settings.environment, settings.otel_exporter_otlp_endpoint)
    init_sentry(settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None, settings.environment)
