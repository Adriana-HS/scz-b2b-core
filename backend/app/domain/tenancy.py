"""Contexto de tenant y actor con el que trabaja el backend (ADR-0002, ADR-0004)."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Actor(StrEnum):
    """Quién actúa desde el backend. HUMAN no existe aquí a propósito: el backend nunca
    se declara persona (SEC-02). Las personas actúan con su JWT desde el dashboard."""

    BOT = "BOT"  # todo turno del agente de IA
    SYSTEM = "SYSTEM"  # jobs programados y, en la fase 7, callbacks bancarios verificados


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Organización y actor de una unidad de trabajo. El servidor lo construye; nunca el LLM."""

    organization_id: UUID
    actor: Actor

    def __post_init__(self) -> None:
        if not isinstance(self.organization_id, UUID):
            raise TypeError("organization_id debe ser UUID")
        if not isinstance(self.actor, Actor):
            raise TypeError("actor debe ser Actor.BOT o Actor.SYSTEM")
