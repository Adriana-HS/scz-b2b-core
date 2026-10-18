"""Repositorios sobre las RPC de la BD. No calculan nada: traducen filas a modelos del dominio.

Las RPC sacan el tenant de `private.backend_org_id()` (fijado por la UnitOfWork), nunca
de un parámetro. Los montos llegan como `numeric` y se mantienen como `Decimal`.
"""

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.agent import ConversationMessage, TurnState, TurnUsage
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

# Estado del turno (#81). La RLS y el organization_id del tenant acotan todo al negocio.
_TURN = """
    SELECT c.id, c.customer_id, c.mode::text AS mode, c.bot_paused_until, c.summary,
           o.name AS business_name, o.vertical::text AS vertical
    FROM public.conversations c
    JOIN public.organizations o ON o.id = c.organization_id
    WHERE c.id = $1 AND c.organization_id = private.backend_org_id()
"""
# Últimos N mensajes + todos los pendientes (aunque sean más viejos), en orden cronológico
_TURN_MESSAGES = """
    SELECT id, direction, body, wa_message_id, msg_type,
           (direction = 'IN' AND processed_at IS NULL) AS pending
    FROM (
        SELECT m.*, row_number() OVER (ORDER BY m.created_at DESC, m.id DESC) AS rn
        FROM public.messages m
        WHERE m.conversation_id = $1 AND m.organization_id = private.backend_org_id()
    ) recent
    WHERE rn <= $2 OR (direction = 'IN' AND processed_at IS NULL)
    ORDER BY created_at, id
"""
# Uso reciente para las cuotas (#25). El día del tope es el de Bolivia (America/La_Paz, UTC-4).
_TURN_USAGE = """
    SELECT
        (SELECT count(*) FROM public.messages m
         WHERE m.conversation_id = $1 AND m.organization_id = private.backend_org_id()
           AND m.direction = 'IN' AND m.created_at > now() - interval '1 minute') AS messages_last_minute,
        (SELECT count(*) FROM public.messages m
         WHERE m.conversation_id = $1 AND m.organization_id = private.backend_org_id()
           AND m.direction = 'OUT' AND m.sender = 'BOT' AND m.created_at > now() - interval '1 hour')
            AS bot_replies_last_hour,
        (SELECT coalesce(sum(l.cost_usd), 0) FROM private.llm_calls l
         WHERE l.organization_id = private.backend_org_id()
           AND l.created_at >= (date_trunc('day', now() AT TIME ZONE 'America/La_Paz')
                                AT TIME ZONE 'America/La_Paz')) AS ai_cost_today_usd
"""
_MARK_PROCESSED = """
    UPDATE public.messages SET processed_at = now()
    WHERE conversation_id = $1 AND id = ANY($2::bigint[]) AND direction = 'IN' AND processed_at IS NULL
"""
_ADD_BOT_REPLY = """
    INSERT INTO public.messages (organization_id, conversation_id, direction, sender, msg_type, body)
    VALUES (private.backend_org_id(), $1, 'OUT', 'BOT', 'text', $2)
"""

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

    async def load_turn(self, conversation_id: UUID, history_limit: int) -> TurnState | None:
        connection = self._session.connection()
        with translate_errors():
            row = await connection.fetchrow(_TURN, conversation_id)
            if row is None:
                return None
            messages = await connection.fetch(_TURN_MESSAGES, conversation_id, history_limit)
            usage = await connection.fetchrow(_TURN_USAGE, conversation_id)
        assert usage is not None  # noqa: S101 - un SELECT sin FROM siempre devuelve una fila
        return TurnState(
            conversation_id=row["id"],
            customer_id=row["customer_id"],
            business_name=row["business_name"],
            vertical=row["vertical"],
            mode=row["mode"],
            bot_paused_until=row["bot_paused_until"],
            summary=row["summary"],
            history=tuple(
                ConversationMessage(
                    id=m["id"],
                    direction=m["direction"],
                    text=m["body"],
                    wa_message_id=m["wa_message_id"],
                    msg_type=m["msg_type"],
                    pending=m["pending"],
                )
                for m in messages
            ),
            usage=TurnUsage(
                customer_messages_last_minute=usage["messages_last_minute"],
                bot_replies_last_hour=usage["bot_replies_last_hour"],
                ai_cost_today_usd=usage["ai_cost_today_usd"],
            ),
        )

    async def mark_processed(self, conversation_id: UUID, message_ids: Sequence[int]) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(_MARK_PROCESSED, conversation_id, list(message_ids))

    async def add_bot_reply(self, conversation_id: UUID, text: str) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(_ADD_BOT_REPLY, conversation_id, text)


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
