"""Caso de uso: registrar los mensajes que llegan por el webhook (RF-01, ADR-0005).

Todo lo de un lote ocurre en UNA transacción: evento, cliente, conversación, mensaje y
trabajo en la cola. Si algo falla, no queda nada y el webhook responde 500 para que Meta
reintente; como el evento tampoco quedó, el reintento se procesa completo. Si el evento
ya existía (Meta reenvió), ese mensaje se omite: es idempotente.
"""

from typing import Protocol

from app.domain.messaging import InboundBatch, InboundResult
from app.domain.ports import ChannelResolver, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext


class InboundHandler(Protocol):
    """Lo que necesita el webhook: registrar un lote y saber cómo fue."""

    async def __call__(self, batch: InboundBatch) -> InboundResult: ...


class RecordInboundMessages:
    def __init__(self, *, resolver: ChannelResolver, unit_of_work: UnitOfWorkFactory) -> None:
        self._resolver = resolver
        self._unit_of_work = unit_of_work

    async def __call__(self, batch: InboundBatch) -> InboundResult:
        channel = await self._resolver.resolve(batch.phone_number_id)
        if channel is None:
            # Número que no es de ningún negocio activo: se responde 200 igual (§8.2)
            return InboundResult(unknown_channel=True)

        accepted = duplicates = 0
        # El webhook no es el agente: actúa como SYSTEM. El turno del agente (BOT) lo corre el worker.
        async with self._unit_of_work(TenantContext(channel.organization_id, Actor.SYSTEM)) as uow:
            for message in batch.messages:
                if not await uow.inbound.record_event(message, batch.phone_number_id):
                    duplicates += 1
                    continue
                customer_id = await uow.inbound.upsert_customer(
                    message.from_wa_id, message.profile_name, message.sent_at
                )
                conversation_id = await uow.inbound.open_conversation(
                    customer_id, message.sent_at, channel.channel_id
                )
                await uow.inbound.add_message(conversation_id, message)
                await uow.inbound.enqueue_conversation(conversation_id)
                accepted += 1
        return InboundResult(accepted=accepted, duplicates=duplicates)
