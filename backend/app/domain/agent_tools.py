"""Argumentos de las herramientas del agente (#24, SYSTEM_DESIGN.md §7.2).

Es lo único que el LLM puede mandar: QUÉ busca, qué ítem y cuántos. Nunca el negocio, el
cliente, la conversación, un precio o un estado: `extra="forbid"` rechaza cualquier campo de
más (inyección directa, §7.4) y el servidor completa el resto desde el contexto del turno.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.orders import QuoteLine


class HandoffReason(StrEnum):
    """Por qué el bot pasa la conversación a una persona (conversations.handoff_reason)."""

    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"  # el cliente pidió una persona (RF-04)
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # algo que el negocio no atiende por el bot
    COMPLAINT = "COMPLAINT"
    TOOL_ERROR = "TOOL_ERROR"  # una herramienta falló y el bot no puede seguir solo
    PAYMENT_ISSUE = "PAYMENT_ISSUE"
    OTHER = "OTHER"


class SearchCatalogArgs(BaseModel):
    """Buscar repuestos o servicios del negocio por texto o código."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200, description="Lo que busca el cliente, en sus palabras")
    category: str | None = Field(default=None, max_length=100, description="Categoría, si el cliente la dijo")


class GetItemDetailsArgs(BaseModel):
    """Ficha de un ítem encontrado con search_catalog."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: UUID = Field(description="El item_id que devolvió search_catalog")


class CreateQuoteArgs(BaseModel):
    """Cotizar ítems del catálogo. Los precios, el IVA, la seña y la vigencia los calcula la BD."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[QuoteLine, ...] = Field(min_length=1, max_length=50, description="Ítems y cantidades")


class HandoffArgs(BaseModel):
    """Pasar la conversación a una persona del equipo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: HandoffReason
    summary: str = Field(
        min_length=1, max_length=1000, description="Qué necesita el cliente, para la persona que lo atienda"
    )
