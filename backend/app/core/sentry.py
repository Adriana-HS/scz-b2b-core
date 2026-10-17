"""Sentry para errores y alertas, sin datos personales (SYSTEM_DESIGN.md §8.1, §10.2).

* Solo se activa si hay SENTRY_DSN.
* `data_collection` cerrado (usuario, cookies, encabezados, cuerpos, query y variables
  locales en off): Sentry no recibe teléfonos, IPs ni cookies.
* `scrub_event` redacta los textos del evento (mensaje, excepciones, breadcrumbs) y quita
  encabezados, cookies, cuerpo y query del request antes de enviarlo.
* Los logs de nivel ERROR (por ejemplo "ALERTA job_dead") se envían como eventos: la alerta
  de Sentry sobre ellos cubre "cualquier trabajo DEAD" sin esperar a las métricas.
"""

from typing import TYPE_CHECKING, Any

from app import __version__
from app.core.redaction import redact

if TYPE_CHECKING:
    from sentry_sdk._types import DataCollectionUserOptions

_DROP_REQUEST_KEYS = ("data", "cookies", "headers", "query_string", "env")

# Lo que el SDK recolecta solo (reemplaza a send_default_pii, que sentry-sdk 2.72 marca deprecado):
# nada del usuario, cookies, encabezados, query, cuerpos, variables locales ni datos de consultas.
# Con data_collection explícito el SDK no aplica su EventScrubber: lo hace scrub_event.
_DATA_COLLECTION: "DataCollectionUserOptions" = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "off"}},
    "http_bodies": [],
    "url_query_params": {"mode": "off"},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},  # prompts y respuestas del agente (#26)
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,  # las variables locales pueden traer datos del cliente
}


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        for key in _DROP_REQUEST_KEYS:
            request.pop(key, None)
    event.pop("user", None)
    for key in ("message", "logentry", "exception", "breadcrumbs", "extra", "contexts"):
        if key in event:
            event[key] = _redact_value(event[key])
    return event


def init_sentry(dsn: str | None, environment: str, *, transport: Any = None) -> bool:
    """Inicializa Sentry si hay DSN. Devuelve si quedó activo. `transport`: solo para pruebas."""
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        transport=transport,
        dsn=dsn,
        environment=environment,
        release=f"scz-b2b-core-backend@{__version__}",
        data_collection=_DATA_COLLECTION,
        before_send=scrub_event,  # type: ignore[arg-type]
        before_breadcrumb=lambda crumb, hint: _redact_value(crumb),
        traces_sample_rate=0.0,  # las trazas van por OpenTelemetry
    )
    return True
