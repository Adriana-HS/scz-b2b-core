"""Órdenes: entrada y resultado de una cotización (ADR-0004, ADR-0007, ADR-0012).

El LLM elige QUÉ (ítem y cantidad); la BD decide CUÁNTO cuesta y SI es posible.
Por eso las líneas no aceptan precios y el resultado trae los montos ya calculados.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.catalog import TaxMode


class QuoteLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: UUID
    quantity: int = Field(ge=1, le=10_000)


class QuoteRequest(BaseModel):
    """`customer_id` y `conversation_id` los inyecta el servidor, nunca el LLM (§7.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    customer_id: UUID
    conversation_id: UUID | None
    lines: tuple[QuoteLine, ...] = Field(min_length=1, max_length=50)


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: UUID
    reference_code: str
    subtotal_bs: Decimal
    iva_bs: Decimal
    total_bs: Decimal
    deposit_required_bs: Decimal
    applied_fx_rate: Decimal | None
    tax_mode: TaxMode
    valid_until: datetime
