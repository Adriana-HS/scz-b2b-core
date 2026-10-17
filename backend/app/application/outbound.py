"""Casos de uso del outbox (#18, RF-06, ADR-0005/0006).

SendOutbound (handler SEND_OUTBOUND del worker), en dos transacciones con la llamada a Meta
en el medio, FUERA de toda transacción (no se retiene una conexión durante la red):
  1. Bloquea el mensaje y vuelve a mirar ventana y consentimiento; marca SENDING; COMMIT.
  2. Llama a Meta.
  3. Marca SENT (o vuelve a PENDING para reintentar, o FAILED si no tiene arreglo); COMMIT.
La Cloud API no tiene clave de idempotencia. Si el worker muere en el paso 2, el reintento
encuentra SENDING y NO reenvía: lo deja FAILED (OUTCOME_UNKNOWN) con una alerta. Preferimos
un mensaje para revisar a una respuesta duplicada al cliente.

RecordDeliveryStatuses aplica los estados que Meta informa por webhook, solo hacia adelante.
"""

import logging
from datetime import UTC, datetime
from typing import Protocol

from app.core import telemetry
from app.domain.errors import PermanentJobError, PermanentSendError, TransientSendError
from app.domain.jobs import Job
from app.domain.outbound import (
    SEND_OUTBOUND,
    FailureReason,
    OutboundKind,
    OutboundStatus,
    StatusBatch,
    StatusResult,
    TemplateCategory,
)
from app.domain.ports import ChannelResolver, MessageSender, SecretStore, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext

logger = logging.getLogger("scz.outbox")

__all__ = ["SEND_OUTBOUND", "RecordDeliveryStatuses", "SendOutbound", "StatusHandler"]

_RESOLVED = frozenset(
    {OutboundStatus.SENT, OutboundStatus.DELIVERED, OutboundStatus.READ, OutboundStatus.FAILED}
)
_TEMPLATE_CATEGORIES = frozenset(c.value for c in TemplateCategory)


class SendOutbound:
    def __init__(
        self, *, unit_of_work: UnitOfWorkFactory, sender: MessageSender, secrets: SecretStore
    ) -> None:
        self._unit_of_work = unit_of_work
        self._sender = sender
        self._secrets = secrets

    async def __call__(self, job: Job) -> None:
        if job.organization_id is None or not isinstance(job.payload.get("outbound_id"), int):
            raise PermanentJobError("SEND_OUTBOUND sin organization_id u outbound_id")
        context = TenantContext(job.organization_id, Actor.SYSTEM)
        outbound_id: int = job.payload["outbound_id"]

        # 1. Preparar: lo que se decida aquí queda confirmado antes de tocar la red
        async with self._unit_of_work(context) as uow:
            row = await uow.outbound.lock_for_send(outbound_id)
            if row is None or row.status in _RESOLVED:
                return  # inexistente, de otro negocio o ya resuelto: idempotente
            if row.status == OutboundStatus.SENDING:
                await uow.outbound.mark_failed(outbound_id, FailureReason.OUTCOME_UNKNOWN)
                telemetry.outbound_outcome_unknown.add(1)
                logger.error(
                    "ALERTA outbound_outcome_unknown id=%d: el envío anterior quedó a medias", outbound_id
                )
                return
            if row.kind == OutboundKind.TEMPLATE and not row.consent_ok:
                await uow.outbound.mark_failed(outbound_id, FailureReason.CONSENT_REQUIRED)
                return
            if row.kind != OutboundKind.TEMPLATE and not row.window_open:
                await uow.outbound.mark_failed(outbound_id, FailureReason.WINDOW_CLOSED)
                return
            token = self._secrets.get(row.access_token_ref) if row.access_token_ref else None
            if token is None or row.phone_number_id is None:
                # Configuración incompleta del canal: se reintenta; si no se arregla, DEAD con alerta
                raise LookupError("canal sin número o sin token configurado")
            await uow.outbound.mark_sending(outbound_id)

        # 2. Enviar, fuera de toda transacción
        try:
            wa_message_id = await self._sender.send(
                row.phone_number_id, token, row.to_wa_id, row.kind, row.payload
            )
        except TransientSendError as exc:
            last_attempt = job.attempts >= job.max_attempts
            async with self._unit_of_work(context) as uow:
                if last_attempt:
                    await uow.outbound.mark_failed(outbound_id, exc.code)
                else:
                    await uow.outbound.mark_retry(outbound_id, exc.code)
            raise
        except PermanentSendError as exc:
            async with self._unit_of_work(context) as uow:
                await uow.outbound.mark_failed(outbound_id, exc.code)
            logger.warning("outbox: Meta rechazó el mensaje id=%d (%s)", outbound_id, exc.code)
            return

        # 3. Confirmar
        async with self._unit_of_work(context) as uow:
            await uow.outbound.mark_sent(outbound_id, wa_message_id)
        self._observe_sent(row.kind, row.payload, row.last_inbound_at)

    @staticmethod
    def _observe_sent(
        kind: OutboundKind, payload: dict[str, object], last_inbound_at: datetime | None
    ) -> None:
        """Métricas de un envío aceptado por Meta. Categoría de cobro (EXT-04): service (dentro de la
        ventana) o la categoría de la plantilla. La latencia de respuesta solo para mensajes libres.
        La etiqueta sale de un conjunto cerrado: un payload raro no crea series nuevas."""
        category = "service"
        if kind == OutboundKind.TEMPLATE:
            raw = payload.get("category")
            category = raw.lower() if isinstance(raw, str) and raw in _TEMPLATE_CATEGORIES else "unknown"
        telemetry.wa_billable_messages.add(1, telemetry.attributes(category=category))
        if kind != OutboundKind.TEMPLATE and last_inbound_at is not None:
            latency_ms = (datetime.now(UTC) - last_inbound_at).total_seconds() * 1000
            telemetry.reply_latency_ms.record(max(latency_ms, 0.0))


class StatusHandler(Protocol):
    async def __call__(self, batch: StatusBatch) -> StatusResult: ...


class RecordDeliveryStatuses:
    """Webhook de estados: phone_number_id -> negocio; dentro de su tenant, cada estado avanza o se ignora."""

    def __init__(self, *, resolver: ChannelResolver, unit_of_work: UnitOfWorkFactory) -> None:
        self._resolver = resolver
        self._unit_of_work = unit_of_work

    async def __call__(self, batch: StatusBatch) -> StatusResult:
        channel = await self._resolver.resolve(batch.phone_number_id)
        if channel is None:
            return StatusResult(ignored=len(batch.updates), unknown_channel=True)
        applied = 0
        async with self._unit_of_work(TenantContext(channel.organization_id, Actor.SYSTEM)) as uow:
            for update in batch.updates:
                applied += int(await uow.outbound.apply_status(update))
        return StatusResult(applied=applied, ignored=len(batch.updates) - applied)
