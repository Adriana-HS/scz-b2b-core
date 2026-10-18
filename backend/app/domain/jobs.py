"""Trabajos de la cola durable (ADR-0005)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

PROCESS_CONVERSATION = "PROCESS_CONVERSATION"  # turno del agente para una conversación (#81)
SEND_QUOTE_PDF = "SEND_QUOTE_PDF"  # PDF de una cotización: generar, subir a Meta y encolar (#26)


def quote_pdf_job_payload(order_id: UUID, conversation_id: UUID) -> dict[str, object]:
    """Payload de SEND_QUOTE_PDF: solo referencias; las cifras se leen de la BD al generar."""
    return {"order_id": str(order_id), "conversation_id": str(conversation_id)}


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    DEAD = "DEAD"  # cola de trabajos fallidos definitivos (DLQ): genera alerta


@dataclass(frozen=True, slots=True)
class Job:
    """Un trabajo reclamado por un worker.

    Contrato para los handlers: el procesamiento es *at-least-once*. Un trabajo puede
    ejecutarse más de una vez (el worker murió, venció el lease), así que el handler debe
    ser idempotente: hacer su trabajo dentro de una UnitOfWork (si muere, se revierte) y
    enviar mensajes por el outbox con una clave de idempotencia, nunca directo (#18).
    """

    id: int
    kind: str
    organization_id: UUID | None
    conversation_key: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    attempts: int = 1  # incluido este intento
    max_attempts: int = 8
    trace_context: str | None = None  # traceparent W3C de quien lo encoló (traza de punta a punta)


@dataclass(frozen=True, slots=True)
class QueueStats:
    """Estado de la cola para un tipo de trabajo (métricas y alertas, §10.2)."""

    kind: str
    queued: int = 0
    running: int = 0
    dead: int = 0
    oldest_ready_s: float = 0.0  # espera del trabajo listo (run_after vencido) más viejo
