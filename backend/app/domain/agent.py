"""Estado de un turno del agente (#81, SYSTEM_DESIGN.md §7.1). Sin I/O."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """Un mensaje del historial. `pending`: entró del cliente y el agente todavía no lo atendió."""

    id: int
    direction: str  # IN (cliente) | OUT (bot o equipo)
    text: str | None
    wa_message_id: str | None
    msg_type: str
    pending: bool = False


@dataclass(frozen=True, slots=True)
class TurnState:
    """Lo que el orquestador necesita para un turno, leído en una sola transacción."""

    conversation_id: UUID
    customer_id: UUID
    business_name: str
    vertical: str
    mode: str  # BOT | HUMAN | CLOSED
    bot_paused_until: datetime | None
    summary: str | None
    history: tuple[ConversationMessage, ...]  # en orden cronológico, incluye los pendientes

    @property
    def pending(self) -> tuple[ConversationMessage, ...]:
        return tuple(m for m in self.history if m.pending)
