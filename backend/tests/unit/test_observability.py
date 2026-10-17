"""Observabilidad (#19, SYSTEM_DESIGN.md §10.2): logs JSON sin PII, trazas de punta a punta,
métricas de los SLO y Sentry sin datos personales."""

import dataclasses
import hashlib
import hmac
import json
import logging
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import HistogramDataPoint
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app.application.worker import JobRunner, default_maintenance
from app.core import telemetry
from app.core.config import Settings
from app.core.sentry import init_sentry, scrub_event
from app.core.structured_logging import JsonFormatter, configure_logging, log_context
from app.domain.jobs import Job, QueueStats
from app.domain.messaging import InboundBatch, InboundResult
from app.domain.outbound import OutboundKind, OutboundStatus, PendingSend
from app.main import create_app

from ..conftest import MetricValue

PHONE = "59170000001"
TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def _record(msg: str, *args: object, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise RuntimeError(f"Key (wa_id)=({PHONE}) ya existe")
        except RuntimeError:
            exc_info = sys.exc_info()
    return logging.LogRecord("scz.prueba", logging.ERROR, __file__, 1, msg, args, exc_info)


# --- Logs JSON --------------------------------------------------------------------------------
def test_cada_log_es_una_linea_json_con_los_campos_base() -> None:
    line = JsonFormatter().format(_record("trabajo %s listo", "id=3"))

    entry = json.loads(line)
    assert entry["level"] == "ERROR" and entry["logger"] == "scz.prueba"
    assert entry["msg"] == "trabajo id=3 listo"
    assert datetime.fromisoformat(entry["ts"]).tzinfo is not None
    assert "\n" not in line  # una línea por evento: los agregadores no la parten


def test_el_mensaje_y_el_traceback_se_redactan() -> None:
    entry = json.loads(JsonFormatter().format(_record("cliente %s escribió", PHONE, exc=True)))

    assert PHONE not in entry["msg"] and PHONE not in entry["exc"]
    assert "RuntimeError" in entry["exc"]  # el tipo de error sí se conserva


def test_el_log_lleva_el_trace_id_del_span_actual() -> None:
    with telemetry.tracer.start_as_current_span("prueba") as span:
        entry = json.loads(JsonFormatter().format(_record("hola")))
    context = span.get_span_context()

    assert entry["trace_id"] == format(context.trace_id, "032x")
    assert entry["span_id"] == format(context.span_id, "016x")
    assert "trace_id" not in json.loads(JsonFormatter().format(_record("fuera del span")))


def test_log_context_agrega_campos_sin_pisar_los_base_y_se_retira_al_salir() -> None:
    org = uuid4()
    with log_context(job_id=7, organization_id=org, msg="no pisa", nota=f"tel {PHONE}"):
        inside = json.loads(JsonFormatter().format(_record("dentro")))
    outside = json.loads(JsonFormatter().format(_record("fuera")))

    assert inside["job_id"] == "7" and inside["organization_id"] == str(org)
    assert inside["msg"] == "dentro"
    assert PHONE not in inside["nota"]  # también se redacta el contexto
    assert "job_id" not in outside


def test_configure_logging_es_idempotente_y_respeta_otros_handlers() -> None:
    root = logging.getLogger()
    other = logging.NullHandler()
    root.addHandler(logging.StreamHandler())  # el de basicConfig: se reemplaza
    root.addHandler(other)

    configure_logging("WARNING")
    configure_logging("INFO")

    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) == 1
    assert other in root.handlers
    assert not [h for h in root.handlers if type(h) is logging.StreamHandler and h not in json_handlers]
    assert root.level == logging.INFO


# --- Propagación de la traza --------------------------------------------------------------------
def test_sin_span_no_hay_traceparent() -> None:
    assert telemetry.current_traceparent() == ""
    assert telemetry.context_from_traceparent(None) is None
    assert telemetry.context_from_traceparent("") is None


def test_el_traceparent_viaja_y_se_recupera() -> None:
    with telemetry.tracer.start_as_current_span("webhook") as span:
        carried = telemetry.current_traceparent()

    assert TRACEPARENT.match(carried)
    parent = telemetry.context_from_traceparent(carried)
    with telemetry.tracer.start_as_current_span("job", context=parent) as child:
        pass
    assert child.get_span_context().trace_id == span.get_span_context().trace_id


def _job(job_id: int, trace_context: str | None = None, attempts: int = 1, kind: str = "T_OBS") -> Job:
    return Job(
        id=job_id,
        kind=kind,
        organization_id=None,
        conversation_key=None,
        attempts=attempts,
        max_attempts=8,
        trace_context=trace_context,
    )


class OneShotQueue:
    def __init__(self, jobs: list[Job], *, finish: str | None = "DONE", fail: str | None = "QUEUED") -> None:
        self.jobs, self.finish, self.fail_status = jobs, finish, fail

    async def claim(self, worker_id: str, limit: int, kinds: Any) -> list[Job]:
        taken, self.jobs = self.jobs[:limit], self.jobs[limit:]
        return taken

    async def complete(self, job_id: int, worker_id: str) -> str | None:
        return self.finish

    async def fail(self, job_id: int, worker_id: str, error: str, *, retry: bool) -> str | None:
        return self.fail_status


async def _run(queue: OneShotQueue, handler: Any, kind: str = "T_OBS") -> None:
    runner = JobRunner(queue, {kind: handler}, worker_id="w-obs")
    await runner.poll_once()
    await runner.drain()


async def test_el_worker_continua_la_traza_de_quien_encolo(spans: InMemorySpanExporter) -> None:
    with telemetry.tracer.start_as_current_span("POST /v1/webhooks/whatsapp") as webhook:
        carried = telemetry.current_traceparent()
    seen: list[str] = []

    async def handler(job: Job) -> None:
        seen.append(telemetry.current_traceparent())  # lo que guardaría una UoW abierta aquí

    await _run(OneShotQueue([_job(1, carried)]), handler)

    [job_span] = [s for s in spans.get_finished_spans() if s.name == "job T_OBS"]
    assert job_span.context.trace_id == webhook.get_span_context().trace_id
    assert job_span.parent is not None and job_span.parent.span_id == webhook.get_span_context().span_id
    assert job_span.attributes is not None and job_span.attributes["job.id"] == "1"
    assert seen[0].split("-")[1] == format(webhook.get_span_context().trace_id, "032x")


async def test_un_trabajo_sin_traza_abre_una_nueva(spans: InMemorySpanExporter) -> None:
    await _run(OneShotQueue([_job(2, "basura-no-w3c")]), _ok)

    [job_span] = [s for s in spans.get_finished_spans() if s.name == "job T_OBS"]
    assert job_span.parent is None  # un traceparent inválido no rompe el trabajo


async def _ok(job: Job) -> None:
    return None


async def test_los_logs_del_handler_llevan_job_id_y_trace_id(caplog: pytest.LogCaptureFixture) -> None:
    lines: list[dict[str, Any]] = []

    async def handler(job: Job) -> None:
        lines.append(json.loads(JsonFormatter().format(_record("procesando"))))

    await _run(OneShotQueue([_job(3)]), handler)

    assert lines[0]["job_id"] == "3" and lines[0]["job_kind"] == "T_OBS"
    assert re.fullmatch(r"[0-9a-f]{32}", lines[0]["trace_id"])


# --- Métricas -------------------------------------------------------------------------------------
async def test_metricas_de_trabajos_por_resultado(metric: MetricValue) -> None:
    kind = f"T_M_{uuid4().hex[:6]}"
    done_before = metric("jobs_processed", kind=kind, outcome="done")

    async def failing(job: Job) -> None:
        raise RuntimeError(PHONE)

    await _run(OneShotQueue([_job(1, kind=kind)]), _ok, kind)
    await _run(OneShotQueue([_job(2, kind=kind)], fail="DEAD"), failing, kind)
    await _run(OneShotQueue([_job(3, kind=kind)], fail="QUEUED"), failing, kind)
    await _run(OneShotQueue([_job(4, kind=kind)], finish=None), _ok, kind)

    assert metric("jobs_processed", kind=kind, outcome="done") == done_before + 1
    assert metric("jobs_processed", kind=kind, outcome="dead") == 1
    assert metric("jobs_processed", kind=kind, outcome="retried") == 1
    assert metric("jobs_processed", kind=kind, outcome="lost") == 1
    assert metric("jobs_dead", kind=kind) == 1
    assert metric("job_duration_ms", kind=kind) == 4


class SnapshotMaintenance:
    async def requeue_stale(self, lease: timedelta) -> tuple[int, int]:
        return (0, 2)

    async def expire_stale_orders(self) -> int:
        return 0

    async def purge_retention(self) -> None:
        return None

    async def queue_stats(self, kinds: Sequence[str]) -> list[QueueStats]:
        self.asked = list(kinds)
        return [QueueStats("T_SNAP", queued=5, running=1, dead=2, oldest_ready_s=42.5)]

    async def fx_stale_tenants(self) -> int:
        return 3


async def test_el_mantenimiento_publica_el_estado_de_la_plataforma(metric: MetricValue) -> None:
    dead_before = metric("jobs_dead", kind="lease_vencido")

    maintenance = SnapshotMaintenance()

    await default_maintenance(maintenance, lease=timedelta(minutes=5), kinds=["T_SNAP", "T_IDLE"]).run_due()

    assert maintenance.asked == ["T_SNAP", "T_IDLE"]  # solo los tipos con handler
    assert metric("jobs_queued", kind="T_SNAP") == 5 and metric("jobs_running", kind="T_SNAP") == 1
    assert metric("jobs_dead_pending", kind="T_SNAP") == 2
    assert metric("jobs_oldest_ready_s", kind="T_SNAP") == 42.5
    assert metric("jobs_queued", kind="T_IDLE") == 0  # sin filas se informa en cero: la alerta se apaga
    assert metric("fx_stale_tenants") == 3
    assert metric("jobs_dead", kind="lease_vencido") == dead_before + 2

    await default_maintenance(maintenance, lease=timedelta(minutes=5), kinds=[]).run_due()
    assert metric("jobs_queued", kind="T_SNAP") == 0  # el snapshot se reemplaza, no se acumula


# --- Webhook: span de servidor y webhook_ack_ms ---------------------------------------------------
SECRET = "secreto-de-prueba"


class TraceCapturingHandler:
    def __init__(self) -> None:
        self.traceparents: list[str] = []

    async def __call__(self, batch: InboundBatch) -> InboundResult:
        self.traceparents.append(telemetry.current_traceparent())
        return InboundResult(accepted=1)


def _webhook_client(handler: TraceCapturingHandler) -> TestClient:
    settings = Settings(_env_file=None, environment="test", meta_app_secret=SECRET)
    return TestClient(create_app(settings, inbound_handler=handler), raise_server_exceptions=False)


def _signed(body: bytes) -> dict[str, str]:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()}


def _body() -> bytes:
    message = {
        "from": PHONE,
        "id": "wamid.obs",
        "timestamp": "1792000000",
        "type": "text",
        "text": {"body": "x"},
    }
    value = {"metadata": {"phone_number_id": "PNID-1"}, "messages": [message]}
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "W", "changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()


def test_el_webhook_abre_la_traza_y_mide_el_ack(spans: InMemorySpanExporter, metric: MetricValue) -> None:
    handler = TraceCapturingHandler()
    acks, requests = metric("webhook_ack_ms", status="200"), metric("webhook_requests")
    body = _body()

    response = _webhook_client(handler).post("/v1/webhooks/whatsapp", content=body, headers=_signed(body))

    assert response.status_code == 200
    [server] = [s for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert server.name == "POST /v1/webhooks/whatsapp"
    assert server.attributes is not None and server.attributes["http.response.status_code"] == 200
    # La UoW del caso de uso guardaría esta traza en jobs.trace_context: misma traza que el request
    assert handler.traceparents[0].split("-")[1] == format(server.context.trace_id, "032x")
    assert metric("webhook_ack_ms", status="200") == acks + 1
    assert metric("webhook_requests") == requests + 1


def test_la_firma_invalida_se_cuenta_y_su_ack_tambien(metric: MetricValue) -> None:
    failures, acks = metric("webhook_signature_failures"), metric("webhook_ack_ms", status="401")
    handler = TraceCapturingHandler()

    response = _webhook_client(handler).post(
        "/v1/webhooks/whatsapp", content=_body(), headers={"X-Hub-Signature-256": "sha256=00"}
    )

    assert response.status_code == 401 and handler.traceparents == []
    assert metric("webhook_signature_failures") == failures + 1
    assert metric("webhook_ack_ms", status="401") == acks + 1


def test_otras_rutas_no_cuentan_como_ack_del_webhook(
    spans: InMemorySpanExporter, metric: MetricValue
) -> None:
    before = metric("webhook_ack_ms")

    _webhook_client(TraceCapturingHandler()).get("/v1/health")

    assert metric("webhook_ack_ms") == before
    assert [s.name for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER] == ["GET /v1/health"]


def test_las_rutas_desconocidas_no_crean_nombres_de_span_nuevos(spans: InMemorySpanExporter) -> None:
    _webhook_client(TraceCapturingHandler()).get(f"/{PHONE}/scan")

    [server] = [s for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert server.name == "GET"
    assert PHONE not in json.dumps(dict(server.attributes or {}))  # url.path también se redacta


def test_el_verify_token_de_la_query_no_llega_a_la_traza(spans: InMemorySpanExporter) -> None:
    token = "token-de-verificacion-secreto"
    params = {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1158201444"}

    _webhook_client(TraceCapturingHandler()).get("/v1/webhooks/whatsapp", params=params)

    [server] = [s for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    attributes = dict(server.attributes or {})
    assert token not in json.dumps(attributes) and "1158201444" not in json.dumps(attributes)
    assert attributes["url.query"] == "hub.mode=REDACTED&hub.verify_token=REDACTED&hub.challenge=REDACTED"


# --- Outbox: mensajes cobrables, latencia de respuesta ---------------------------------------------
def _pending(kind: OutboundKind, payload: dict[str, Any], last_inbound_at: datetime | None) -> PendingSend:
    return PendingSend(
        outbound_id=41, status=OutboundStatus.PENDING, kind=kind, payload=payload, to_wa_id=PHONE,
        phone_number_id="PNID", access_token_ref="WA_TOKEN_A", window_open=True, consent_ok=True,
        last_inbound_at=last_inbound_at,
    )  # fmt: skip


async def _send(row: PendingSend) -> None:
    from .test_send_outbound import _job as send_job
    from .test_send_outbound import _setup

    handler, *_ = _setup(row)
    await handler(send_job())


async def test_un_texto_cuenta_como_servicio_y_mide_la_latencia_de_respuesta(metric: MetricValue) -> None:
    service, latency = metric("wa_billable_messages", category="service"), metric("reply_latency_ms")

    await _send(_pending(OutboundKind.TEXT, {"body": "hola"}, datetime.now(UTC) - timedelta(seconds=3)))

    assert metric("wa_billable_messages", category="service") == service + 1
    assert metric("reply_latency_ms") == latency + 1


@pytest.mark.parametrize(
    ("category", "label"), [("MARKETING", "marketing"), ("UTILITY", "utility"), ("<script>", "unknown")]
)
async def test_una_plantilla_cuenta_por_su_categoria_sin_latencia(
    category: str, label: str, metric: MetricValue
) -> None:
    billable, latency = metric("wa_billable_messages", category=label), metric("reply_latency_ms")
    payload = {"name": "aviso", "language": "es", "category": category, "components": []}

    await _send(_pending(OutboundKind.TEMPLATE, payload, datetime.now(UTC)))

    assert metric("wa_billable_messages", category=label) == billable + 1
    assert metric("reply_latency_ms") == latency  # una plantilla no es una respuesta al cliente


async def test_un_resultado_incierto_se_cuenta(metric: MetricValue) -> None:
    before = metric("outbound_outcome_unknown")
    row = _pending(OutboundKind.TEXT, {"body": "hola"}, None)

    await _send(dataclasses.replace(row, status=OutboundStatus.SENDING))

    assert metric("outbound_outcome_unknown") == before + 1


@pytest.mark.parametrize(("name", "threshold"), [("reply_latency_ms", 8000), ("webhook_ack_ms", 250)])
def test_los_histogramas_tienen_un_limite_en_el_umbral_del_slo(name: str, threshold: float) -> None:
    """Con un límite justo en el umbral, el percentil de la alerta no se interpola entre buckets."""
    from ..conftest import METRICS

    getattr(telemetry, name).record(1.0, telemetry.attributes(status=200))
    data = METRICS.get_metrics_data()
    assert data is not None
    bounds = [
        point.explicit_bounds
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, HistogramDataPoint)
    ]
    assert bounds and all(threshold in b for b in bounds)


def test_ninguna_metrica_queda_fuera_del_catalogo() -> None:
    """Las alertas solo usan nombres de METRIC_NAMES: un instrumento nuevo debe agregarse ahí."""
    source = Path(telemetry.__file__).read_text(encoding="utf-8")
    created = set(re.findall(r'create_(?:counter|histogram|observable_gauge)\(\s*"(\w+)"', source))
    gauges = set(re.findall(r'\(\s*"(\w+)",\s*"\w+",\s*"[^"]+"\s*\),', source))
    assert created and gauges
    assert created | gauges == telemetry.METRIC_NAMES


# --- Sentry -----------------------------------------------------------------------------------------
def _event() -> dict[str, Any]:
    return {
        "message": f"falló el envío a {PHONE}",
        "logentry": {"message": "cliente %s", "params": [PHONE], "formatted": f"cliente {PHONE}"},
        "exception": {"values": [{"type": "RuntimeError", "value": f"Key (wa_id)=({PHONE})"}]},
        "breadcrumbs": {"values": [{"message": f"GET /x?token=EAAG{'a' * 40}"}]},
        "request": {
            "url": "https://api/v1/webhooks/whatsapp",
            "method": "POST",
            "data": {"from": PHONE},
            "headers": {"Authorization": "Bearer abc.def"},
            "cookies": {"sb": "x"},
            "query_string": "hub.verify_token=secreto",
            "env": {"REMOTE_ADDR": "10.0.0.1"},
        },
        "user": {"ip_address": "10.0.0.1"},
        "extra": {"nota": f"tel {PHONE}"},
    }


def test_scrub_event_quita_datos_del_request_y_del_usuario() -> None:
    event = scrub_event(_event())

    assert event["request"] == {"url": "https://api/v1/webhooks/whatsapp", "method": "POST"}
    assert "user" not in event
    serialized = json.dumps(event)
    assert PHONE not in serialized and "EAAG" not in serialized and "Bearer abc" not in serialized
    assert event["exception"]["values"][0]["type"] == "RuntimeError"


def test_sin_dsn_sentry_no_se_activa() -> None:
    assert init_sentry(None, "test") is False
    assert init_sentry("", "test") is False


def test_sentry_activo_envia_eventos_ya_redactados() -> None:
    import sentry_sdk
    from sentry_sdk.envelope import Envelope
    from sentry_sdk.transport import Transport

    sent: list[dict[str, Any]] = []

    class CapturingTransport(Transport):
        def capture_envelope(self, envelope: Envelope) -> None:
            event = envelope.get_event()
            if event is not None:
                sent.append(dict(event))

    try:
        assert init_sentry("https://public@sentry.invalid/1", "test", transport=CapturingTransport())
        try:
            raise RuntimeError(f"Key (wa_id)=({PHONE}) ya existe")
        except RuntimeError as exc:
            sentry_sdk.capture_exception(exc)
        sentry_sdk.flush()
    finally:
        sentry_sdk.init(dsn=None)  # deja Sentry inactivo para el resto de la corrida

    assert len(sent) == 1
    assert PHONE not in json.dumps(sent[0], default=str)
    assert sent[0]["exception"]["values"][0]["type"] == "RuntimeError"
    assert "user" not in sent[0] or "ip_address" not in sent[0].get("user", {})
