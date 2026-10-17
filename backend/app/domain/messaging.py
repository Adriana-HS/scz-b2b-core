"""Mensajería entrante, independiente del formato de Meta (el webhook lo traduce, ADR-0006)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class WhatsAppChannel:
    """Número conectado de un negocio. `access_token_ref` es el NOMBRE del secreto, nunca el valor."""

    organization_id: UUID
    channel_id: UUID
    access_token_ref: str


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Un mensaje de un cliente, ya validado por el webhook.

    `wa_message_id` es la clave de idempotencia: Meta puede reenviar el mismo evento.
    `raw` es el evento original de ese mensaje (sin el resto del lote), para el worker
    (por ejemplo, el id de una imagen que hay que descargar). Se purga a los 30 días.
    """

    wa_message_id: str
    from_wa_id: str
    profile_name: str | None
    msg_type: str
    text: str | None
    sent_at: datetime
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.sent_at.tzinfo is None:
            raise ValueError("sent_at debe tener zona horaria (todo se guarda en timestamptz)")


@dataclass(frozen=True, slots=True)
class InboundBatch:
    """Mensajes que llegaron a un mismo número (phone_number_id) en un POST del webhook."""

    phone_number_id: str
    messages: tuple[InboundMessage, ...]


@dataclass(frozen=True, slots=True)
class InboundResult:
    """Resultado de registrar un lote. Lo usa la métrica, no el cliente."""

    accepted: int = 0
    duplicates: int = 0
    unknown_channel: bool = False
