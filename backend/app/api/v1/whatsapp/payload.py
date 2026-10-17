"""Traducción del payload de Meta a mensajes del dominio (capa anticorrupción, ADR-0006).

Solo se toman los campos esperados. Un mensaje que no cumple las reglas se descarta y se
cuenta, sin tumbar el resto del lote: un 4xx o 5xx por un solo mensaje haría que Meta
reenvíe todo el lote indefinidamente.
Referencia del formato: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.messaging import InboundBatch, InboundMessage

_MAX_TEXT = 4096  # límite de Meta para un mensaje de texto


class _MetaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(min_length=1, max_length=256)
    from_: str = Field(alias="from", pattern=r"^[0-9]{8,15}$")  # mismo CHECK que customers.wa_id
    timestamp: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=32)


@dataclass(frozen=True, slots=True)
class ParsedWebhook:
    batches: tuple[InboundBatch, ...] = ()
    statuses: int = 0  # estados de mensajes salientes: los procesa el outbox (#18)
    invalid: int = 0  # mensajes descartados por no cumplir el formato
    ignored: int = 0  # cambios de otros campos (plantillas, cuenta, ...)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_of(message: dict[str, Any]) -> str | None:
    kind = message.get("type")
    if kind == "text":
        text = _as_dict(message.get("text")).get("body")
    elif kind == "interactive":
        interactive = _as_dict(message.get("interactive"))
        reply = _as_dict(interactive.get("button_reply") or interactive.get("list_reply"))
        text = reply.get("title")
    elif kind == "button":
        text = _as_dict(message.get("button")).get("text")
    else:
        return None  # imagen, audio, documento...: el worker usa `raw` (id de la media)
    if not isinstance(text, str):
        return None
    if len(text) > _MAX_TEXT:
        raise ValueError("texto más largo que el máximo de WhatsApp")
    return text


def _profile_names(value: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in _as_list(value.get("contacts")):
        contact = _as_dict(contact)
        name = _as_dict(contact.get("profile")).get("name")
        if isinstance(contact.get("wa_id"), str) and isinstance(name, str):
            names[contact["wa_id"]] = name[:200]
    return names


def parse_webhook(payload: Any) -> ParsedWebhook:
    """Agrupa los mensajes por número del negocio. Nunca lanza por un payload inesperado."""
    payload = _as_dict(payload)
    if payload.get("object") != "whatsapp_business_account":
        return ParsedWebhook()

    by_phone: dict[str, list[InboundMessage]] = defaultdict(list)
    statuses = invalid = ignored = 0
    for entry in _as_list(payload.get("entry")):
        for change in _as_list(_as_dict(entry).get("changes")):
            change = _as_dict(change)
            if change.get("field") != "messages":
                ignored += 1
                continue
            value = _as_dict(change.get("value"))
            phone_number_id = _as_dict(value.get("metadata")).get("phone_number_id")
            statuses += len(_as_list(value.get("statuses")))
            messages = _as_list(value.get("messages"))
            if not isinstance(phone_number_id, str) or not phone_number_id:
                invalid += len(messages)
                continue
            names = _profile_names(value)
            for raw in messages:
                try:
                    meta = _MetaMessage.model_validate(raw)
                    text = _text_of(raw)
                except (ValidationError, ValueError):
                    invalid += 1
                    continue
                by_phone[phone_number_id].append(
                    InboundMessage(
                        wa_message_id=meta.id,
                        from_wa_id=meta.from_,
                        profile_name=names.get(meta.from_),
                        msg_type=meta.type,
                        text=text,
                        sent_at=datetime.fromtimestamp(meta.timestamp, UTC),
                        raw=raw,
                    )
                )
    batches = tuple(InboundBatch(phone, tuple(msgs)) for phone, msgs in by_phone.items())
    return ParsedWebhook(batches=batches, statuses=statuses, invalid=invalid, ignored=ignored)
