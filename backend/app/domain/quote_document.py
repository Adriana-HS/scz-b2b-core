"""El documento de una cotización (#26, RF-20, SYSTEM_DESIGN §8.3). Sin I/O.

Todas las cifras vienen de la BD (create_quote, private.tax_breakdown): aquí solo se describen
para que un renderizador las formatee. Nada en este módulo calcula montos.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.catalog import TaxMode

_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")


@dataclass(frozen=True, slots=True)
class QuoteDocumentLine:
    sku: str
    name: str
    quantity: int
    unit_price_bs: Decimal
    line_total_bs: Decimal  # columna generada por la BD


@dataclass(frozen=True, slots=True)
class QuoteDocument:
    business_name: str
    legal_name: str | None
    nit: str | None
    reference_code: str
    issued_at: datetime
    valid_until: datetime
    timezone: str  # la del negocio (organizations.timezone): las fechas se muestran en su hora
    customer_name: str | None
    lines: tuple[QuoteDocumentLine, ...]
    subtotal_bs: Decimal
    iva_bs: Decimal
    total_bs: Decimal
    deposit_required_bs: Decimal
    applied_fx_rate: Decimal | None  # solo si hay ítems en USD
    tax_mode: TaxMode
    iva_percent: Decimal  # tax_regimes.iva_rate, en porcentaje: 13.00 en el régimen general

    @property
    def filename(self) -> str:
        """Nombre del adjunto en WhatsApp. Solo letras, números y guiones: nada de rutas."""
        safe = _UNSAFE.sub("-", self.reference_code).strip("-") or "cotizacion"
        return f"Cotizacion-{safe}.pdf"


@dataclass(frozen=True, slots=True)
class QuoteDelivery:
    """Lo que hace falta para mandar el PDF: el documento y por qué número sale."""

    document: QuoteDocument
    conversation_id: UUID | None  # una cotización hecha desde el panel puede no tener conversación
    phone_number_id: str | None
    access_token_ref: str | None
