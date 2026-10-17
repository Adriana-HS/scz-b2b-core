"""Tipos del dominio de IA (#22, principios-y-patrones §2.1). Sin SDKs ni HTTP.

Los puertos (ChatModel, VisionExtractor, Embedder, SpeechToText) viven en ports.py y hablan
estos tipos: el agente no sabe qué proveedor hay detrás. El modelo se fija por su
identificador exacto (nunca un alias como `-latest`, que cambia de modelo sin aviso: cambian
el costo y las respuestas) y cada llamada deja su costo en private.llm_calls (Q-COST-1).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,63}$")
_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.000001")  # private.llm_calls.cost_usd es numeric(12,6)


def validate_model_id(model: str) -> str:
    """El identificador exacto del modelo. Rechaza alias móviles y valores que no son un ID."""
    if not _MODEL_ID.fullmatch(model) or "latest" in model:
        raise ValueError("el modelo debe fijarse por su identificador exacto (sin alias como -latest)")
    return model


class AiPurpose(StrEnum):
    """Para qué se usó la IA (private.llm_calls.purpose)."""

    CHAT = "CHAT"
    OCR = "OCR"
    EMBEDDING = "EMBEDDING"
    SUMMARY = "SUMMARY"
    STT = "STT"


class Role(StrEnum):
    USER = "user"  # el cliente, o los resultados de las herramientas que se le devuelven al modelo
    ASSISTANT = "assistant"


class StopReason(StrEnum):
    END_TURN = "END_TURN"
    TOOL_USE = "TOOL_USE"  # el modelo pidió herramientas: el agente las ejecuta y vuelve a llamar
    MAX_TOKENS = "MAX_TOKENS"
    REFUSAL = "REFUSAL"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Una herramienta que el modelo puede pedir. El esquema describe sus argumentos (JSON Schema)."""

    name: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError("nombre de herramienta inválido: letras, números, _ o -, hasta 64")
        if self.input_schema.get("type") != "object":
            raise ValueError("el esquema de una herramienta debe ser un objeto JSON Schema")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Lo que el modelo pidió. Son datos, no órdenes: el agente los valida antes de ejecutar (#24)."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content: str  # JSON con cifras que vienen de la BD (grounding numérico, #25)
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()

    def __post_init__(self) -> None:
        if not self.text and not self.tool_calls and not self.tool_results:
            raise ValueError("un mensaje no puede estar vacío")
        if self.tool_calls and self.role is not Role.ASSISTANT:
            raise ValueError("solo el asistente pide herramientas")
        if self.tool_results and self.role is not Role.USER:
            raise ValueError("los resultados de herramientas van del lado del cliente (role=user)")

    @classmethod
    def user(cls, text: str) -> "ChatMessage":
        return cls(Role.USER, text=text)

    @classmethod
    def assistant(cls, text: str | None, tool_calls: tuple[ToolCall, ...] = ()) -> "ChatMessage":
        return cls(Role.ASSISTANT, text=text, tool_calls=tool_calls)

    @classmethod
    def from_tool_results(cls, *results: ToolResult) -> "ChatMessage":
        return cls(Role.USER, tool_results=tuple(results))


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0  # prompt cacheado: se cobra mucho menos
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens) < 0:
            raise ValueError("los tokens no pueden ser negativos")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: StopReason
    usage: TokenUsage
    model: str  # el que respondió (debe coincidir con el fijado)


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Precios del modelo en USD por millón de tokens. Vienen de la configuración, nunca del código:
    cambian con el proveedor y el plan."""

    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    cache_read_usd_per_mtok: Decimal
    cache_write_usd_per_mtok: Decimal

    def __post_init__(self) -> None:
        prices = (
            self.input_usd_per_mtok, self.output_usd_per_mtok,
            self.cache_read_usd_per_mtok, self.cache_write_usd_per_mtok,
        )  # fmt: skip
        if any(price < 0 for price in prices):
            raise ValueError("un precio no puede ser negativo")

    def cost(self, usage: TokenUsage) -> Decimal:
        """Costo operativo de la llamada (no es dinero del cliente: no pasa por la BD de precios)."""
        total = (
            usage.input_tokens * self.input_usd_per_mtok
            + usage.output_tokens * self.output_usd_per_mtok
            + usage.cache_read_tokens * self.cache_read_usd_per_mtok
            + usage.cache_write_tokens * self.cache_write_usd_per_mtok
        ) / _MILLION
        return total.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LlmCall:
    """Una fila de private.llm_calls: base del costo por tenant y de las cuotas (Q-COST-1, #25)."""

    organization_id: UUID
    purpose: AiPurpose
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: Decimal
    latency_ms: int
    tool_calls: int
    outcome: str  # OK o el código del error (HTTP_529, CIRCUIT_OPEN...)
    conversation_id: UUID | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReceiptExtraction:
    """Lo que la visión lee de un comprobante QR. Es evidencia, nunca una confirmación (ADR-0008):
    las reglas antifraude (#31) y una persona con MFA deciden."""

    amount_bs: Decimal | None = None
    bank_code: str | None = None
    txn_number: str | None = None
    txn_at: datetime | None = None
    beneficiary_name: str | None = None
    beneficiary_account_last4: str | None = None
    glosa: str | None = None
    confidence: Decimal | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
