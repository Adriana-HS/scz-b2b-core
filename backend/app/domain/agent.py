"""Estado de un turno del agente (#81, SYSTEM_DESIGN.md §7.1). Sin I/O."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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
class TurnUsage:
    """Uso reciente, para las cuotas (#25, §7.4.4). Lo cuenta la BD en la misma lectura del turno."""

    customer_messages_last_minute: int = 0  # mensajes de este cliente (incluye los pendientes)
    bot_replies_last_hour: int = 0  # respuestas del bot en esta conversación
    ai_cost_today_usd: Decimal = Decimal(0)  # costo de IA del negocio hoy (día de Bolivia)


@dataclass(frozen=True, slots=True)
class AgentLimits:
    """Topes del agente (RF-51). Valores por defecto decididos por AHS el 18-oct-2026."""

    daily_budget_usd: Decimal = Decimal("0.75")  # por negocio y día; al 80 % se avisa
    messages_per_minute: int = 10  # por cliente
    turns_per_hour: int = 30  # por cliente

    def __post_init__(self) -> None:
        if self.daily_budget_usd < 0 or self.messages_per_minute < 1 or self.turns_per_hour < 1:
            raise ValueError("los topes del agente deben ser positivos")


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
    usage: TurnUsage = field(default_factory=TurnUsage)

    @property
    def pending(self) -> tuple[ConversationMessage, ...]:
        return tuple(m for m in self.history if m.pending)
