"""Repositorio de entrada del webhook (ADR-0005). Todo con el tenant de la UnitOfWork.

`organization_id` sale siempre de `private.backend_org_id()`, nunca de un parámetro:
aunque un bug pasara datos de otro negocio, la RLS rechazaría la fila.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.messaging import InboundMessage
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session

# Idempotencia: la PK es wa_message_id. Un reenvío de Meta no inserta nada y devuelve 0 filas.
_RECORD_EVENT = """
    INSERT INTO private.inbound_events (wa_message_id, organization_id, phone_number_id, event_type, payload)
    VALUES ($1, private.backend_org_id(), $2, 'message', $3::jsonb)
    ON CONFLICT (wa_message_id) DO NOTHING
    RETURNING wa_message_id
"""

# last_inbound_at controla la ventana de 24 h de Meta (RF-06): solo avanza, nunca retrocede.
_UPSERT_CUSTOMER = """
    INSERT INTO public.customers (organization_id, wa_id, display_name, last_inbound_at)
    VALUES (private.backend_org_id(), $1, $2, $3)
    ON CONFLICT (organization_id, wa_id) DO UPDATE SET
        display_name    = coalesce(EXCLUDED.display_name, public.customers.display_name),
        last_inbound_at = greatest(public.customers.last_inbound_at, EXCLUDED.last_inbound_at)
    RETURNING id
"""

# Una sola conversación abierta por cliente (índice único parcial uq_open_conversation).
# Guarda el número por el que escribió el cliente: la respuesta sale por ese número (migración 16).
_OPEN_CONVERSATION = """
    INSERT INTO public.conversations (organization_id, customer_id, last_inbound_at, whatsapp_channel_id)
    VALUES (private.backend_org_id(), $1, $2, $3)
    ON CONFLICT (organization_id, customer_id) WHERE mode <> 'CLOSED' DO UPDATE SET
        last_inbound_at     = greatest(public.conversations.last_inbound_at, EXCLUDED.last_inbound_at),
        whatsapp_channel_id = EXCLUDED.whatsapp_channel_id
    RETURNING id
"""

_ADD_MESSAGE = """
    INSERT INTO public.messages
        (organization_id, conversation_id, direction, sender, wa_message_id, msg_type, body)
    VALUES (private.backend_org_id(), $1, 'IN', 'CUSTOMER', $2, $3, $4)
    ON CONFLICT (wa_message_id) DO NOTHING
"""

# Debounce: un solo trabajo en cola por conversación; cada mensaje lo pospone (máx. 4 s).
_ENQUEUE = "SELECT private.enqueue_conversation(private.backend_org_id(), $1)"


class PostgresInboundRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def record_event(self, message: InboundMessage, phone_number_id: str) -> bool:
        connection = self._session.connection()
        with translate_errors():
            inserted = await connection.fetchval(
                _RECORD_EVENT, message.wa_message_id, phone_number_id, json.dumps(message.raw)
            )
        return inserted is not None

    async def upsert_customer(self, wa_id: str, profile_name: str | None, seen_at: datetime) -> UUID:
        connection = self._session.connection()
        with translate_errors():
            customer_id: UUID = await connection.fetchval(_UPSERT_CUSTOMER, wa_id, profile_name, seen_at)
        return customer_id

    async def open_conversation(self, customer_id: UUID, seen_at: datetime, channel_id: UUID) -> UUID:
        connection = self._session.connection()
        with translate_errors():
            conversation_id: UUID = await connection.fetchval(
                _OPEN_CONVERSATION, customer_id, seen_at, channel_id
            )
        return conversation_id

    async def add_message(self, conversation_id: UUID, message: InboundMessage) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(
                _ADD_MESSAGE, conversation_id, message.wa_message_id, message.msg_type, message.text
            )

    async def enqueue_conversation(self, conversation_id: UUID) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(_ENQUEUE, conversation_id)
