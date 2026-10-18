"""Trazas y métricas con OpenTelemetry (SYSTEM_DESIGN.md §10.2, #19).

* Siempre hay un TracerProvider del SDK: los logs llevan trace_id aunque no se exporte nada.
  Las trazas y métricas se exportan por OTLP/HTTP solo si hay OTEL_EXPORTER_OTLP_ENDPOINT.
* La traza cruza la cola: la UnitOfWork fija `app.traceparent` y la BD lo guarda en
  `jobs.trace_context` (migración 18); el worker continúa la traza al procesar el trabajo.
* Los nombres de métricas están en METRIC_NAMES: las alertas de ops/alerts/ solo pueden usar
  esos nombres (lo verifica tests/unit/test_alert_rules.py).
* Ninguna métrica lleva datos personales: los atributos son tipos, categorías o el
  organization_id (identificador, no PII).
"""

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence

from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.redaction import redact
from app.domain.jobs import QueueStats

SERVICE_NAMESPACE = "scz-b2b-core"

_meter = metrics.get_meter(SERVICE_NAMESPACE)
tracer = trace.get_tracer(SERVICE_NAMESPACE)

# --- Métricas (nombres estables: los usan las alertas) ------------------------------------
# Sin `unit`: la unidad ya está en el nombre y Prometheus agregaría un sufijo (_milliseconds).
# Al exportar, los contadores llevan _total y los histogramas _bucket/_sum/_count.
# Los límites de los histogramas incluyen los umbrales de los SLO (250 ms, 8 s) para que el
# percentil calculado no dependa de la interpolación entre buckets lejanos.
webhook_ack_ms = _meter.create_histogram(
    "webhook_ack_ms",
    description="Recepción del webhook de Meta hasta la respuesta, en ms (Q-PERF-1)",
    explicit_bucket_boundaries_advisory=[5, 10, 25, 50, 100, 150, 250, 500, 1000, 2500, 5000],
)
webhook_signature_failures = _meter.create_counter(
    "webhook_signature_failures", description="POST del webhook rechazados por firma inválida o ausente"
)
webhook_requests = _meter.create_counter("webhook_requests", description="POST del webhook recibidos")
reply_latency_ms = _meter.create_histogram(
    "reply_latency_ms",
    description="Último mensaje del cliente hasta que Meta acepta la respuesta, en ms (Q-PERF-2)",
    explicit_bucket_boundaries_advisory=[500, 1000, 2000, 3000, 5000, 8000, 10000, 15000, 30000, 60000],
)
jobs_processed = _meter.create_counter(
    "jobs_processed",
    description="Trabajos cerrados por el worker, por tipo y resultado (done/retried/dead/lost)",
)
jobs_dead = _meter.create_counter(
    "jobs_dead", description="Trabajos que pasaron a DEAD (DLQ): alerta inmediata"
)
job_duration_ms = _meter.create_histogram(
    "job_duration_ms",
    description="Duración de un trabajo, en ms",
    explicit_bucket_boundaries_advisory=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000],
)
wa_billable_messages = _meter.create_counter(
    "wa_billable_messages", description="Mensajes aceptados por Meta, por categoría de cobro (EXT-04)"
)
outbound_outcome_unknown = _meter.create_counter(
    "outbound_outcome_unknown", description="Envíos con resultado incierto (ADR-0017): revisión humana"
)
agent_turns = _meter.create_counter(
    "agent_turns", description="Turnos del agente por resultado: replied, limit, ai_unavailable, skipped_*"
)
agent_tool_calls = _meter.create_counter(
    "agent_tool_calls",
    description="Herramientas que pidió el agente, por herramienta y resultado (OK o código)",
)
llm_cost_usd = _meter.create_counter(
    "llm_cost_usd", description="Costo de IA en USD por tenant y propósito (Q-COST-1); lo alimenta el agente"
)

# Gauges del estado de la plataforma: los alimenta el worker cada minuto (mantenimiento).
_queues: dict[str, QueueStats] = {}
_fx_stale = 0
_snapshot_lock = threading.Lock()


def set_platform_snapshot(
    *, kinds: Sequence[str], queues: Sequence[QueueStats], fx_stale_tenants: int
) -> None:
    """Un tipo procesado sin filas en la cola se informa en cero (la alerta se apaga sola)."""
    global _fx_stale
    by_kind = {q.kind: q for q in queues}
    with _snapshot_lock:
        _queues.clear()
        _queues.update({kind: by_kind.get(kind, QueueStats(kind)) for kind in kinds})
        _fx_stale = fx_stale_tenants


def _queue_gauge(field: str) -> Callable[[CallbackOptions], Iterable[Observation]]:
    def callback(options: CallbackOptions) -> Iterable[Observation]:
        with _snapshot_lock:
            return [Observation(getattr(q, field), {"kind": q.kind}) for q in _queues.values()]

    return callback


def _fx_gauge(options: CallbackOptions) -> Iterable[Observation]:
    with _snapshot_lock:
        return [Observation(_fx_stale)]


for _name, _field, _desc in (
    ("jobs_queued", "queued", "Trabajos en cola, por tipo"),
    ("jobs_running", "running", "Trabajos en curso, por tipo"),
    ("jobs_dead_pending", "dead", "Trabajos en DEAD sin resolver, por tipo"),
    ("jobs_oldest_ready_s", "oldest_ready_s", "Espera del trabajo listo más viejo, por tipo"),
):
    _meter.create_observable_gauge(_name, callbacks=[_queue_gauge(_field)], description=_desc)
_meter.create_observable_gauge(
    "fx_stale_tenants", callbacks=[_fx_gauge], description="Negocios con ítems en USD y TC vencido"
)

METRIC_NAMES = frozenset(
    {
        "webhook_ack_ms", "webhook_signature_failures", "webhook_requests", "reply_latency_ms",
        "jobs_processed", "jobs_dead", "job_duration_ms", "wa_billable_messages",
        "outbound_outcome_unknown", "llm_cost_usd", "agent_tool_calls", "agent_turns",
        "jobs_queued", "jobs_running", "jobs_dead_pending", "jobs_oldest_ready_s", "fx_stale_tenants",
    }
)  # fmt: skip


# --- Propagación de la traza --------------------------------------------------------------
def current_traceparent() -> str:
    """El traceparent W3C del span actual, o '' si no hay traza (se guarda en la transacción)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent", "")


def context_from_traceparent(traceparent: str | None) -> Context | None:
    """Contexto padre a partir de un traceparent guardado (jobs.trace_context)."""
    if not traceparent:
        return None
    return propagate.extract({"traceparent": traceparent})


# --- Configuración del SDK -------------------------------------------------------------------
_REDACTED_ATTRIBUTES = ("url.query", "url.path", "url.full")


class RedactingSpanProcessor(SpanProcessor):
    """Redacta los atributos de URL al iniciar el span, antes de que lo vea cualquier exportador.

    El span de servidor de FastAPI guarda la query (en la verificación del webhook trae
    hub.verify_token) y el path tal como llegaron. La query queda solo con sus claves.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for key in _REDACTED_ATTRIBUTES:
            value = span.attributes.get(key) if span.attributes else None
            if not isinstance(value, str):
                continue
            if key == "url.query":
                keys = [pair.split("=", 1)[0] for pair in value.split("&") if pair]
                span.set_attribute(key, "&".join(f"{redact(k)}=REDACTED" for k in keys))
            else:
                span.set_attribute(key, redact(value.split("?", 1)[0]))

    def on_end(self, span: ReadableSpan) -> None:
        return None


_configured = False
_configure_lock = threading.Lock()


def configure_telemetry(
    service_name: str,
    environment: str,
    otlp_endpoint: str | None,
    *,
    span_processors: Sequence[SpanProcessor] = (),
    metric_readers: Sequence[MetricReader] = (),
) -> None:
    """Idempotente. Sin endpoint: trazas y métricas en el proceso (no se exportan).
    `span_processors` y `metric_readers` extra los usan las pruebas (exportadores en memoria)."""
    global _configured
    with _configure_lock:
        if _configured:
            return
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": SERVICE_NAMESPACE,
                "deployment.environment": environment,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(RedactingSpanProcessor())  # primero: antes de exportar
        for processor in span_processors:
            tracer_provider.add_span_processor(processor)
        readers = list(metric_readers)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            base = otlp_endpoint.rstrip("/")
            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
            )
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics")))
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))
        _configured = True


def attributes(**values: object) -> Mapping[str, str]:
    """Atributos de métricas y spans: solo valores no nulos, como texto."""
    return {key: str(value) for key, value in values.items() if value is not None}
