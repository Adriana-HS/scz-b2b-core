"""Repositorios sobre las RPC de la BD. No calculan nada: traducen filas a modelos del dominio.

Las RPC sacan el tenant de `private.backend_org_id()` (fijado por la UnitOfWork), nunca
de un parámetro. Los montos llegan como `numeric` y se mantienen como `Decimal`.
"""

import json
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.catalog import CatalogMatch, CatalogSearch, ItemDetails, PriceStatus, TaxMode
from app.domain.orders import Quote, QuoteRequest
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session

_SEARCH = """
    SELECT item_id, sku, name, item_type::text AS item_type, stock_available, duration_minutes,
           unit_price_bs, price_status, tax_mode::text AS tax_mode
    FROM public.search_catalog($1, NULL, $2, $3)
"""

_DETAILS = """
    SELECT item_id, sku, name, description, category, item_type::text AS item_type, stock_available,
           duration_minutes, unit_price_bs, price_status, tax_mode::text AS tax_mode, attributes
    FROM public.get_item_details($1)
"""

_HANDOFF = "SELECT public.request_handoff($1, $2, $3)"

_CREATE_QUOTE = """
    SELECT order_id, reference_code, subtotal_bs, iva_bs, total_bs, deposit_required_bs,
           applied_fx_rate, tax_mode::text AS tax_mode, valid_until
    FROM public.create_quote($1, $2, $3::jsonb)
"""


class PostgresCatalogRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def search(self, criteria: CatalogSearch) -> list[CatalogMatch]:
        connection = self._session.connection()
        with translate_errors():
            rows = await connection.fetch(_SEARCH, criteria.query, criteria.limit, criteria.category)
        return [
            CatalogMatch(
                item_id=row["item_id"],
                sku=row["sku"],
                name=row["name"],
                item_type=row["item_type"],
                stock_available=row["stock_available"],
                duration_minutes=row["duration_minutes"],
                unit_price_bs=row["unit_price_bs"],
                price_status=PriceStatus(row["price_status"]),
                tax_mode=TaxMode(row["tax_mode"]),
            )
            for row in rows
        ]

    async def get_details(self, item_id: UUID) -> ItemDetails | None:
        connection = self._session.connection()
        with translate_errors():
            row = await connection.fetchrow(_DETAILS, item_id)
        if row is None:
            return None
        return ItemDetails(
            item_id=row["item_id"],
            sku=row["sku"],
            name=row["name"],
            description=row["description"],
            category=row["category"],
            item_type=row["item_type"],
            stock_available=row["stock_available"],
            duration_minutes=row["duration_minutes"],
            unit_price_bs=row["unit_price_bs"],
            price_status=PriceStatus(row["price_status"]),
            tax_mode=TaxMode(row["tax_mode"]),
            attributes=json.loads(row["attributes"])
            if isinstance(row["attributes"], str)
            else row["attributes"],
        )


class PostgresConversationRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def request_handoff(self, conversation_id: UUID, reason: str, summary: str) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(_HANDOFF, conversation_id, reason, summary)


class PostgresOrderRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def create_quote(self, request: QuoteRequest) -> Quote:
        """Cotización atómica en la BD: valida ítems del tenant, congela precio y TC, reserva stock."""
        connection = self._session.connection()
        items = json.dumps(
            [{"item_id": str(line.item_id), "quantity": line.quantity} for line in request.lines]
        )
        with translate_errors():
            row = await connection.fetchrow(
                _CREATE_QUOTE, request.customer_id, request.conversation_id, items
            )
        if row is None:  # pragma: no cover - la RPC siempre devuelve la orden o lanza
            raise RuntimeError("create_quote no devolvió la orden")
        return Quote(
            order_id=row["order_id"],
            reference_code=row["reference_code"],
            subtotal_bs=row["subtotal_bs"],
            iva_bs=row["iva_bs"],
            total_bs=row["total_bs"],
            deposit_required_bs=row["deposit_required_bs"],
            applied_fx_rate=row["applied_fx_rate"],
            tax_mode=TaxMode(row["tax_mode"]),
            valid_until=row["valid_until"],
        )

    async def count_for_customer(self, customer_id: UUID) -> int:
        connection = self._session.connection()
        with translate_errors():
            count: int = await connection.fetchval(
                "SELECT count(*) FROM public.orders WHERE customer_id = $1", customer_id
            )
        return count
