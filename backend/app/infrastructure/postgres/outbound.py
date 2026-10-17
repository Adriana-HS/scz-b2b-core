"""Repositorio del outbox (migraciones 03 y 16). Todo con el tenant de la UnitOfWork.

* enqueue: mensaje + trabajo SEND_OUTBOUND en la misma transacción. La BD rechaza
  (trigger) un mensaje libre fuera de la ventana de 24 h o una plantilla sin consentimiento.
* lock_for_send: FOR UPDATE, para que dos workers no envíen el mismo mensaje a la vez;
  devuelve también la ventana y el consentimiento al momento del envío.
* apply_status: los estados de Meta solo avanzan (rank), y un failed no pisa un mensaje ya
  entregado o leído.
"""

import json
from typing import TYPE_CHECKING, Any

from app.domain.outbound import (
    SEND_OUTBOUND,
    DeliveryStatus,
    OutboundKind,
    OutboundRequest,
    OutboundStatus,
    PendingSend,
    StatusUpdate,
    outbound_job_payload,
)
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session

_ENQUEUE = """
    INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES (private.backend_org_id(), $1, $2, $3, $4::jsonb)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
"""
_ENQUEUE_JOB = """
    INSERT INTO private.jobs (organization_id, kind, payload)
    VALUES (private.backend_org_id(), $1, $2::jsonb)
"""
_LOCK = """
    SELECT o.id, o.status, o.kind, o.payload, c.wa_id, w.phone_number_id, w.access_token_ref,
           (c.last_inbound_at IS NOT NULL
            AND c.last_inbound_at >= now() - interval '24 hours') AS window_open,
           (c.optout_at IS NULL
            AND (c.optin_utility_at IS NOT NULL OR c.optin_marketing_at IS NOT NULL)) AS consent_ok
    FROM private.outbound_messages o
    JOIN public.conversations v ON v.organization_id = o.organization_id AND v.id = o.conversation_id
    JOIN public.customers c ON c.organization_id = v.organization_id AND c.id = v.customer_id
    LEFT JOIN public.whatsapp_channels w
           ON w.organization_id = v.organization_id AND w.id = v.whatsapp_channel_id AND w.is_active
    WHERE o.id = $1
    FOR UPDATE OF o
"""
_MARK_SENDING = (
    "UPDATE private.outbound_messages"
    " SET status = 'SENDING', attempts = attempts + 1, last_error = NULL WHERE id = $1"
)
_MARK_SENT = (
    "UPDATE private.outbound_messages SET status = 'SENT', wa_message_id = $2, sent_at = now() WHERE id = $1"
)
_MARK_RETRY = (
    "UPDATE private.outbound_messages SET status = 'PENDING', last_error = left($2, 500) WHERE id = $1"
)
_MARK_FAILED = (
    "UPDATE private.outbound_messages SET status = 'FAILED', last_error = left($2, 500) WHERE id = $1"
)

# Orden de los estados: solo se avanza. FAILED solo aplica si aún no se entregó.
_RANK = "CASE {col} WHEN 'PENDING' THEN 0 WHEN 'SENDING' THEN 1 WHEN 'SENT' THEN 2 WHEN 'DELIVERED' THEN 3 WHEN 'READ' THEN 4 ELSE 5 END"  # noqa: E501
_APPLY_STATUS = f"""
    UPDATE private.outbound_messages o
       SET status = $2,
           last_error = CASE WHEN $2 = 'FAILED' THEN left($3, 500) ELSE o.last_error END
     WHERE o.wa_message_id = $1
       AND o.status <> 'FAILED'
       AND (
             ($2 = 'FAILED' AND o.status IN ('SENDING', 'SENT'))
          OR ($2 <> 'FAILED' AND {_RANK.format(col="$2")} > {_RANK.format(col="o.status")})
       )
    RETURNING o.id
"""  # noqa: S608 - _RANK es una constante del módulo, sin entrada externa

_DELIVERY_TO_STATUS = {
    DeliveryStatus.SENT: OutboundStatus.SENT,
    DeliveryStatus.DELIVERED: OutboundStatus.DELIVERED,
    DeliveryStatus.READ: OutboundStatus.READ,
    DeliveryStatus.FAILED: OutboundStatus.FAILED,
}


def _payload(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


class PostgresOutboundRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def enqueue(self, request: OutboundRequest) -> int | None:
        connection = self._session.connection()
        with translate_errors():
            outbound_id: int | None = await connection.fetchval(
                _ENQUEUE, request.conversation_id, request.idempotency_key, request.kind.value,
                json.dumps(request.payload),
            )  # fmt: skip
            if outbound_id is None:
                return None  # misma idempotency_key: ya estaba encolado
            await connection.execute(
                _ENQUEUE_JOB,
                SEND_OUTBOUND,
                json.dumps(outbound_job_payload(outbound_id, request.conversation_id)),
            )
        return outbound_id

    async def lock_for_send(self, outbound_id: int) -> PendingSend | None:
        connection = self._session.connection()
        with translate_errors():
            row = await connection.fetchrow(_LOCK, outbound_id)
        if row is None:
            return None
        return PendingSend(
            outbound_id=row["id"],
            status=OutboundStatus(row["status"]),
            kind=OutboundKind(row["kind"]),
            payload=_payload(row["payload"]),
            to_wa_id=row["wa_id"],
            phone_number_id=row["phone_number_id"],
            access_token_ref=row["access_token_ref"],
            window_open=row["window_open"],
            consent_ok=row["consent_ok"],
        )

    async def _execute(self, query: str, *args: object) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(query, *args)

    async def mark_sending(self, outbound_id: int) -> None:
        await self._execute(_MARK_SENDING, outbound_id)

    async def mark_sent(self, outbound_id: int, wa_message_id: str) -> None:
        await self._execute(_MARK_SENT, outbound_id, wa_message_id)

    async def mark_retry(self, outbound_id: int, error: str) -> None:
        await self._execute(_MARK_RETRY, outbound_id, error)

    async def mark_failed(self, outbound_id: int, reason: str) -> None:
        await self._execute(_MARK_FAILED, outbound_id, reason)

    async def apply_status(self, update: StatusUpdate) -> bool:
        connection = self._session.connection()
        status = _DELIVERY_TO_STATUS[update.status]
        error = f"META_{update.error_code}" if update.error_code else None
        with translate_errors():
            changed = await connection.fetchval(_APPLY_STATUS, update.wa_message_id, status.value, error)
        return changed is not None
