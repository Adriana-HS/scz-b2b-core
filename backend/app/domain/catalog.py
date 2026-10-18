"""Catálogo: resultado de búsqueda tal como lo calcula la BD (ADR-0003, ADR-0007)."""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PriceStatus(StrEnum):
    OK = "OK"
    FX_MISSING = "FX_MISSING"  # ítem en USD y el negocio no cargó TC
    FX_STALE = "FX_STALE"  # TC más viejo que fx_max_age_hours: el bot no ofrece precio


class TaxMode(StrEnum):
    IVA_INCLUIDO = "IVA_INCLUIDO"
    IVA_ADICIONAL = "IVA_ADICIONAL"


class CatalogSearch(BaseModel):
    """Entrada de la búsqueda. Viene del LLM: se valida antes de tocar la BD."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)
    category: str | None = Field(default=None, max_length=100)


class CatalogMatch(BaseModel):
    """Un ítem encontrado. `unit_price_bs` es None si el precio no se puede ofrecer."""

    model_config = ConfigDict(frozen=True)

    item_id: UUID
    sku: str
    name: str
    item_type: str
    stock_available: int | None
    duration_minutes: int | None
    unit_price_bs: Decimal | None
    price_status: PriceStatus
    tax_mode: TaxMode


class ItemDetails(BaseModel):
    """Ficha de un ítem (public.get_item_details). Precio y stock calculados por la BD, igual que
    en la búsqueda; solo atributos públicos (nunca costos ni márgenes)."""

    model_config = ConfigDict(frozen=True)

    item_id: UUID
    sku: str
    name: str
    description: str | None
    category: str | None
    item_type: str
    stock_available: int | None
    duration_minutes: int | None
    unit_price_bs: Decimal | None
    price_status: PriceStatus
    tax_mode: TaxMode
    attributes: Mapping[str, Any]
