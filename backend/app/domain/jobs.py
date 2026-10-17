"""Trabajos de la cola durable (ADR-0005)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


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
