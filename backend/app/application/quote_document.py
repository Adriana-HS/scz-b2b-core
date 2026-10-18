"""PDF de cotización por WhatsApp (#26, RF-20, SYSTEM_DESIGN §8.3 y §9).

SendQuoteDocument es el handler SEND_QUOTE_PDF. Lo encola create_quote en su misma transacción,
con la conversación como conversation_key: corre después del turno del agente.

  1. Lee la cotización, sus líneas y el canal (una transacción corta, como SYSTEM).
  2. Genera el PDF en un thread pool: reportlab es sincrónico y no debe frenar el event loop.
  3. Lo sube a Meta (fuera de toda transacción) y obtiene su media_id.
  4. Lo encola en el outbox como DOCUMENT por media_id, con clave quote-pdf:{orden}: un reintento
     no manda dos PDF. Nunca un enlace firmado: caduca antes de que el cliente lo abra (§8.3).

Las cifras son las de la BD; el PDF solo las formatea.
"""

import asyncio
import logging
from uuid import UUID

from app.domain.errors import BusinessRuleError, ErrorCode, PermanentJobError, PermanentSendError
from app.domain.jobs import Job
from app.domain.outbound import OutboundRequest
from app.domain.ports import MediaUploader, QuoteRenderer, SecretStore, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext

logger = logging.getLogger("scz.quote_document")

PDF_MIME = "application/pdf"


class SendQuoteDocument:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        renderer: QuoteRenderer,
        uploader: MediaUploader,
        secrets: SecretStore,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._renderer = renderer
        self._uploader = uploader
        self._secrets = secrets

    async def __call__(self, job: Job) -> None:
        order_id = self._order_id(job)
        assert job.organization_id is not None  # noqa: S101 - lo valida _order_id
        context = TenantContext(job.organization_id, Actor.SYSTEM)

        async with self._unit_of_work(context) as uow:
            delivery = await uow.orders.quote_delivery(order_id)
        if delivery is None or delivery.conversation_id is None:
            return  # de otro negocio, inexistente o hecha sin conversación: no hay a quién mandarla
        token = self._secrets.get(delivery.access_token_ref) if delivery.access_token_ref else None
        if token is None or delivery.phone_number_id is None:
            # Configuración incompleta del canal: se reintenta; si no se arregla, DEAD con alerta
            raise LookupError("canal sin número o sin token configurado")

        document = delivery.document
        pdf = await asyncio.to_thread(self._renderer.render, document)
        try:
            media_id = await self._uploader.upload_media(
                delivery.phone_number_id, token, pdf, PDF_MIME, document.filename
            )
        except PermanentSendError as exc:
            raise PermanentJobError(f"Meta rechazó el PDF ({exc.code})") from None

        request = OutboundRequest.document(
            delivery.conversation_id,
            f"quote-pdf:{order_id}",
            media_id,
            document.filename,
            caption=f"Cotización {document.reference_code}",
        )
        try:
            async with self._unit_of_work(context) as uow:
                await uow.outbound.enqueue(request)
        except BusinessRuleError as error:
            if error.code is not ErrorCode.WINDOW_CLOSED:
                raise
            # Pasaron las 24 h: el PDF ya no puede salir como mensaje libre (§10.1). No se reintenta.
            logger.warning("pdf de cotización: ventana de 24 h cerrada; no se envía")

    @staticmethod
    def _order_id(job: Job) -> UUID:
        raw = job.payload.get("order_id")
        if job.organization_id is None or not isinstance(raw, str):
            raise PermanentJobError("SEND_QUOTE_PDF sin organization_id u order_id")
        try:
            return UUID(raw)
        except ValueError:
            raise PermanentJobError("order_id inválido") from None
