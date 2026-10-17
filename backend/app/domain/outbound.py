"""Mensajes salientes por el outbox (ADR-0005, ADR-0006, RF-06).

El dominio describe QUÉ se envía; el adaptador de la Cloud API decide el formato de Meta.
Nadie envía directo a Meta: todo pasa por el outbox (idempotency_key única) y el worker.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

SEND_OUTBOUND = "SEND_OUTBOUND"  # tipo de trabajo de la cola que envía un mensaje del outbox


def outbound_job_payload(outbound_id: int, conversation_id: UUID) -> dict[str, object]:
    """Payload del trabajo SEND_OUTBOUND: solo referencias; el contenido vive en el outbox."""
    return {"outbound_id": outbound_id, "conversation_id": str(conversation_id)}


_TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
_MAX_TEXT = 4096


class OutboundKind(StrEnum):
    TEXT = "TEXT"
    DOCUMENT = "DOCUMENT"
    TEMPLATE = "TEMPLATE"  # la única que sale fuera de la ventana de 24 h (con consentimiento)


class TemplateCategory(StrEnum):
    """Categoría de la plantilla en Meta. Define qué consentimiento exige (migración 17)."""

    UTILITY = "UTILITY"  # recordatorios, avisos de pedidos y citas: exige opt-in de utilidad
    MARKETING = "MARKETING"  # promociones: exige opt-in de marketing


class OutboundStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"  # confirmado ANTES de llamar a Meta: si el worker muere, no se reenvía
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class FailureReason(StrEnum):
    WINDOW_CLOSED = "WINDOW_CLOSED"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"  # el worker murió durante la llamada a Meta: puede o no haber salido


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    """Un mensaje a encolar. `idempotency_key` evita enviar dos veces la misma respuesta."""

    conversation_id: UUID
    idempotency_key: str
    kind: OutboundKind
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= len(self.idempotency_key) <= 200:
            raise ValueError("idempotency_key debe tener entre 1 y 200 caracteres")

    @classmethod
    def text(cls, conversation_id: UUID, idempotency_key: str, body: str) -> "OutboundRequest":
        if not body or len(body) > _MAX_TEXT:
            raise ValueError("el texto debe tener entre 1 y 4096 caracteres")
        return cls(conversation_id, idempotency_key, OutboundKind.TEXT, {"body": body})

    @classmethod
    def document(
        cls,
        conversation_id: UUID,
        idempotency_key: str,
        media_id: str,
        filename: str,
        caption: str | None = None,
    ) -> "OutboundRequest":
        """PDF de cotización o recibo, ya subido a Meta: se envía por media_id (§8.3)."""
        if not media_id or not filename or len(filename) > 240:
            raise ValueError("media_id y filename son obligatorios")
        payload: dict[str, Any] = {"media_id": media_id, "filename": filename}
        if caption:
            payload["caption"] = caption[:1024]
        return cls(conversation_id, idempotency_key, OutboundKind.DOCUMENT, payload)

    @classmethod
    def template(
        cls,
        conversation_id: UUID,
        idempotency_key: str,
        name: str,
        language: str,
        category: TemplateCategory,
        components: list[dict[str, Any]] | None = None,
    ) -> "OutboundRequest":
        if not _TEMPLATE_NAME.fullmatch(name) or not _LANGUAGE.fullmatch(language):
            raise ValueError("nombre de plantilla o idioma inválido")
        if not isinstance(category, TemplateCategory):
            raise ValueError("la plantilla necesita su categoría (UTILITY o MARKETING)")
        return cls(
            conversation_id,
            idempotency_key,
            OutboundKind.TEMPLATE,
            {"name": name, "language": language, "category": category.value, "components": components or []},
        )


@dataclass(frozen=True, slots=True)
class PendingSend:
    """Lo que el worker necesita para enviar un mensaje, leído y bloqueado en la BD."""

    outbound_id: int
    status: OutboundStatus
    kind: OutboundKind
    payload: dict[str, Any]
    to_wa_id: str
    phone_number_id: str | None
    access_token_ref: str | None
    window_open: bool
    consent_ok: bool
    last_inbound_at: datetime | None = None  # para reply_latency_ms


class DeliveryStatus(StrEnum):
    """Estados que informa Meta por webhook. Solo avanzan: sent < delivered < read."""

    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    wa_message_id: str
    status: DeliveryStatus
    at: datetime
    error_code: str | None = None  # código de Meta; nunca el texto del error


@dataclass(frozen=True, slots=True)
class StatusBatch:
    phone_number_id: str
    updates: tuple[StatusUpdate, ...]


@dataclass(frozen=True, slots=True)
class StatusResult:
    applied: int = 0  # estados que hicieron avanzar un mensaje
    ignored: int = 0  # desconocidos, de otro negocio o que llegaron tarde (no retroceden)
    unknown_channel: bool = False
