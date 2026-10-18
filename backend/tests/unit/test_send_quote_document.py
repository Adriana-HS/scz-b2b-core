"""Envío del PDF de cotización (#26): handler SEND_QUOTE_PDF con dobles.

Lee la cotización (cifras de la BD), genera el PDF en un thread pool (no bloquea el event loop),
lo sube a Meta y lo encola en el outbox como DOCUMENT por media_id. Nunca un enlace firmado.
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import pytest

from app.application.quote_document import SendQuoteDocument
from app.domain.catalog import TaxMode
from app.domain.errors import (
    BusinessRuleError,
    ErrorCode,
    PermanentJobError,
    PermanentSendError,
    TransientSendError,
)
from app.domain.jobs import SEND_QUOTE_PDF, Job, quote_pdf_job_payload
from app.domain.outbound import OutboundKind, OutboundRequest
from app.domain.quote_document import QuoteDelivery, QuoteDocument, QuoteDocumentLine
from app.domain.tenancy import Actor, TenantContext

ORG, ORDER, CONVERSATION = uuid4(), uuid4(), uuid4()
DOC = QuoteDocument(
    business_name="Importadora de Ejemplo", legal_name=None, nit=None, reference_code="COT-2026-000123",
    issued_at=datetime(2026, 10, 18, 14, 30, tzinfo=UTC),
    valid_until=datetime(2026, 10, 19, 14, 30, tzinfo=UTC),
    timezone="America/La_Paz", customer_name=None,
    lines=(QuoteDocumentLine("PF-001", "Pastillas", 1, Decimal("480.00"), Decimal("480.00")),),
    subtotal_bs=Decimal("480.00"), iva_bs=Decimal("62.40"), total_bs=Decimal("480.00"),
    deposit_required_bs=Decimal("0.00"), applied_fx_rate=None, tax_mode=TaxMode.IVA_INCLUIDO,
    iva_percent=Decimal("13.00"),
)  # fmt: skip
DELIVERY = QuoteDelivery(DOC, CONVERSATION, phone_number_id="106540352242922", access_token_ref="WA_TOKEN_A")
Log = list[tuple[str, Any]]


class FakeOrders:
    def __init__(self, log: Log, delivery: QuoteDelivery | None) -> None:
        self.log, self.delivery = log, delivery

    async def quote_delivery(self, order_id: UUID) -> QuoteDelivery | None:
        self.log.append(("load", order_id))
        return self.delivery


class FakeOutbound:
    def __init__(self, log: Log, error: Exception | None) -> None:
        self.log, self.error = log, error

    async def enqueue(self, request: OutboundRequest) -> int | None:
        self.log.append(("enqueue", request))
        if self.error:
            raise self.error
        return 7


class FakeUnitOfWork:
    def __init__(
        self, log: Log, context: TenantContext, delivery: QuoteDelivery | None, error: Exception | None
    ):
        self.log, self.context = log, context
        self.orders = FakeOrders(log, delivery)
        self.outbound = FakeOutbound(log, error)

    async def __aenter__(self) -> Self:
        self.log.append(("begin", self.context))
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.log.append(("end", "rollback" if exc_type else "commit"))


class FakeRenderer:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.thread: int | None = None

    def render(self, document: QuoteDocument) -> bytes:
        self.thread = threading.get_ident()
        self.log.append(("render", document.reference_code))
        return b"%PDF-falso"


class FakeUploader:
    def __init__(self, log: Log, error: Exception | None = None) -> None:
        self.log, self.error = log, error

    async def upload_media(self, phone_number_id: str, token: str, content: bytes, mime_type: str,
                           filename: str) -> str:  # fmt: skip
        self.log.append(("upload", (phone_number_id, token, content, mime_type, filename)))
        if self.error:
            raise self.error
        return "media-123"


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        return self.values.get(name)


def _handler(
    delivery: QuoteDelivery | None = DELIVERY,
    *,
    upload_error: Exception | None = None,
    enqueue_error: Exception | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[SendQuoteDocument, Log, FakeRenderer]:
    log: Log = []
    renderer = FakeRenderer(log)
    handler = SendQuoteDocument(
        unit_of_work=lambda ctx: FakeUnitOfWork(log, ctx, delivery, enqueue_error),  # type: ignore[arg-type]  # doble mínimo
        renderer=renderer,
        uploader=FakeUploader(log, upload_error),
        secrets=FakeSecrets({"WA_TOKEN_A": "token-de-prueba"} if secrets is None else secrets),
    )
    return handler, log, renderer


def _job(**payload: Any) -> Job:
    return Job(id=3, kind=SEND_QUOTE_PDF, organization_id=ORG, conversation_key=str(CONVERSATION),
               payload=payload or quote_pdf_job_payload(ORDER, CONVERSATION))  # fmt: skip


def _enqueued(log: Log) -> list[OutboundRequest]:
    return [arg for kind, arg in log if kind == "enqueue"]


# --- El camino normal -----------------------------------------------------------------------------
async def test_genera_sube_y_encola_el_pdf_por_media_id() -> None:
    handler, log, _ = _handler()

    await handler(_job())

    assert ("begin", TenantContext(ORG, Actor.SYSTEM)) in log
    assert ("upload", ("106540352242922", "token-de-prueba", b"%PDF-falso", "application/pdf",
                       "Cotizacion-COT-2026-000123.pdf")) in log  # fmt: skip
    [request] = _enqueued(log)
    assert request.kind is OutboundKind.DOCUMENT
    assert request.conversation_id == CONVERSATION
    assert request.idempotency_key == f"quote-pdf:{ORDER}"  # un reintento no manda dos PDF
    assert request.payload == {
        "media_id": "media-123", "filename": "Cotizacion-COT-2026-000123.pdf",
        "caption": "Cotización COT-2026-000123",
    }  # fmt: skip


async def test_el_pdf_se_genera_fuera_del_event_loop() -> None:
    handler, _, renderer = _handler()

    await handler(_job())

    assert renderer.thread is not None and renderer.thread != threading.get_ident()


async def test_la_red_va_fuera_de_las_transacciones() -> None:
    """No se retiene una conexión de la BD mientras se genera ni mientras se sube a Meta."""
    handler, log, _ = _handler()

    await handler(_job())

    kinds = [kind for kind, _ in log]
    assert kinds == ["begin", "load", "end", "render", "upload", "begin", "enqueue", "end"]


# --- Casos que no envían --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "delivery",
    [None, QuoteDelivery(DOC, None, phone_number_id="106540352242922", access_token_ref="WA_TOKEN_A")],
)
async def test_sin_cotizacion_o_sin_conversacion_no_hace_nada(delivery: QuoteDelivery | None) -> None:
    handler, log, _ = _handler(delivery)

    await handler(_job())

    assert not [kind for kind, _ in log if kind in ("render", "upload", "enqueue")]


@pytest.mark.parametrize(
    ("delivery", "secrets"),
    [
        (QuoteDelivery(DOC, CONVERSATION, phone_number_id=None, access_token_ref="WA_TOKEN_A"), None),
        (DELIVERY, {}),  # el secreto no está cargado
    ],
)
async def test_sin_canal_o_sin_token_se_reintenta(
    delivery: QuoteDelivery, secrets: dict[str, str] | None
) -> None:
    handler, log, _ = _handler(delivery, secrets=secrets)

    with pytest.raises(LookupError):
        await handler(_job())

    assert not [kind for kind, _ in log if kind in ("render", "upload")]


async def test_un_error_transitorio_de_meta_se_reintenta() -> None:
    handler, log, _ = _handler(upload_error=TransientSendError("HTTP_503"))

    with pytest.raises(TransientSendError):
        await handler(_job())

    assert _enqueued(log) == []


async def test_si_meta_rechaza_el_archivo_no_se_reintenta(caplog: pytest.LogCaptureFixture) -> None:
    handler, log, _ = _handler(upload_error=PermanentSendError("META_131053"))

    with pytest.raises(PermanentJobError):
        await handler(_job())

    assert _enqueued(log) == []


async def test_con_la_ventana_cerrada_no_reintenta_para_siempre() -> None:
    handler, log, _ = _handler(enqueue_error=BusinessRuleError(ErrorCode.WINDOW_CLOSED, "ventana"))

    await handler(_job())  # no lanza: pasaron las 24 h, el PDF ya no puede salir como mensaje libre

    assert len(_enqueued(log)) == 1


async def test_otro_error_al_encolar_no_se_oculta() -> None:
    handler, _, _ = _handler(enqueue_error=BusinessRuleError(ErrorCode.NOT_FOUND, "otra"))

    with pytest.raises(BusinessRuleError):
        await handler(_job())


@pytest.mark.parametrize(
    "job",
    [
        Job(
            id=1,
            kind=SEND_QUOTE_PDF,
            organization_id=None,
            conversation_key=None,
            payload=quote_pdf_job_payload(ORDER, CONVERSATION),
        ),
        Job(id=1, kind=SEND_QUOTE_PDF, organization_id=ORG, conversation_key=None, payload={}),
        Job(
            id=1,
            kind=SEND_QUOTE_PDF,
            organization_id=ORG,
            conversation_key=None,
            payload={"order_id": "no-es-uuid"},
        ),
    ],
)
async def test_un_trabajo_mal_formado_va_a_dead(job: Job) -> None:
    handler, _, _ = _handler()

    with pytest.raises(PermanentJobError):
        await handler(job)


def test_el_payload_del_trabajo_solo_lleva_referencias() -> None:
    assert quote_pdf_job_payload(ORDER, CONVERSATION) == {
        "order_id": str(ORDER),
        "conversation_id": str(CONVERSATION),
    }
