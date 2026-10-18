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
from app.domain.jobs import SEND_QUOTE_PDF, quote_pdf_job_payload
from app.domain.orders import Quote, QuoteRequest
from app.domain.quote_document import QuoteDelivery, QuoteDocument, QuoteDocumentLine
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session

_SEARCH = """
    SELECT item_id, sku, name, item_type::text AS item_type, stock_available, duration_minutes,
           unit_price_bs, price_status, tax_mode::text AS tax_mode
    FROM public.search_catalog($1, NULL, $2, $3)
"""

# Códigos de producto -> ítems activos del tenant (#89). Mismo normalizado que la columna generada.
_FIND_BY_SKUS = """
    SELECT sku, id FROM public.catalog_items
    WHERE organization_id = private.backend_org_id() AND is_active
      AND sku_normalized = ANY(
          SELECT upper(regexp_replace(s, '[^A-Za-z0-9]', '', 'g')) FROM unnest($1::text[]) s
      )
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
           o.name AS business_name, o.vertical::text AS vertical,
           cu.privacy_notice_at,
           CASE WHEN jsonb_typeof(o.config -> 'ai_notice') = 'string'
                THEN o.config ->> 'ai_notice' END AS ai_notice
    FROM public.conversations c
    JOIN public.organizations o ON o.id = c.organization_id
    JOIN public.customers cu ON cu.id = c.customer_id AND cu.organization_id = c.organization_id
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
_RECORD_PRIVACY_NOTICE = """
    UPDATE public.customers SET privacy_notice_at = now()
    WHERE id = $1 AND organization_id = private.backend_org_id() AND privacy_notice_at IS NULL
"""
_MARK_PROCESSED = """
    UPDATE public.messages SET processed_at = now()
    WHERE conversation_id = $1 AND id = ANY($2::bigint[]) AND direction = 'IN' AND processed_at IS NULL
"""
_ADD_BOT_REPLY = """
    INSERT INTO public.messages (organization_id, conversation_id, direction, sender, msg_type, body)
    VALUES (private.backend_org_id(), $1, 'OUT', 'BOT', 'text', $2)
"""

# PDF de cotización (#26): el trabajo va serializado con la conversación (sale después del turno)
_REQUEST_QUOTE_DOCUMENT = """
    INSERT INTO private.jobs (organization_id, kind, payload, conversation_key)
    VALUES (private.backend_org_id(), $1, $2::jsonb, $3)
"""
_QUOTE_DELIVERY = """
    SELECT o.reference_code, o.created_at, o.valid_until, o.subtotal_bs, o.iva_bs, o.total_bs,
           o.deposit_required_bs, o.applied_fx_rate, o.conversation_id,
           tr.mode::text AS tax_mode, tr.iva_rate,
           g.name AS business_name, g.legal_name, g.nit, g.timezone,
           cu.display_name AS customer_name,
           w.phone_number_id, w.access_token_ref
    FROM public.orders o
    JOIN public.organizations g ON g.id = o.organization_id
    JOIN public.customers cu ON cu.organization_id = o.organization_id AND cu.id = o.customer_id
    JOIN public.tax_regimes tr ON tr.id = o.tax_regime_id
    LEFT JOIN public.conversations v ON v.organization_id = o.organization_id AND v.id = o.conversation_id
    LEFT JOIN public.whatsapp_channels w
           ON w.organization_id = v.organization_id AND w.id = v.whatsapp_channel_id AND w.is_active
    WHERE o.id = $1 AND o.organization_id = private.backend_org_id()
"""
_QUOTE_LINES = """
    SELECT sku_snapshot, name_snapshot, quantity, unit_price_bs, line_total_bs
    FROM public.order_items
    WHERE order_id = $1 AND organization_id = private.backend_org_id()
    ORDER BY sku_snapshot, id
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

    async def find_by_skus(self, skus: Sequence[str]) -> list[tuple[str, UUID]]:
        connection = self._session.connection()
        with translate_errors():
            rows = await connection.fetch(_FIND_BY_SKUS, list(skus))
        return [(row["sku"], row["id"]) for row in rows]

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
            privacy_notice_at=row["privacy_notice_at"],
            ai_notice=row["ai_notice"],
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

    async def record_privacy_notice(self, customer_id: UUID) -> None:
        with translate_errors():
            await self._session.connection().execute(_RECORD_PRIVACY_NOTICE, customer_id)

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

    async def request_quote_document(self, order_id: UUID, conversation_id: UUID) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(
                _REQUEST_QUOTE_DOCUMENT, SEND_QUOTE_PDF,
                json.dumps(quote_pdf_job_payload(order_id, conversation_id)), str(conversation_id),
            )  # fmt: skip

    async def quote_delivery(self, order_id: UUID) -> QuoteDelivery | None:
        connection = self._session.connection()
        with translate_errors():
            row = await connection.fetchrow(_QUOTE_DELIVERY, order_id)
            if row is None:
                return None
            lines = await connection.fetch(_QUOTE_LINES, order_id)
        document = QuoteDocument(
            business_name=row["business_name"],
            legal_name=row["legal_name"],
            nit=row["nit"],
            reference_code=row["reference_code"],
            issued_at=row["created_at"],
            valid_until=row["valid_until"],
            timezone=row["timezone"],
            customer_name=row["customer_name"],
            lines=tuple(
                QuoteDocumentLine(
                    sku=line["sku_snapshot"],
                    name=line["name_snapshot"],
                    quantity=line["quantity"],
                    unit_price_bs=line["unit_price_bs"],
                    line_total_bs=line["line_total_bs"],
                )
                for line in lines
            ),
            subtotal_bs=row["subtotal_bs"],
            iva_bs=row["iva_bs"],
            total_bs=row["total_bs"],
            deposit_required_bs=row["deposit_required_bs"],
            applied_fx_rate=row["applied_fx_rate"],
            tax_mode=TaxMode(row["tax_mode"]),
            iva_percent=row["iva_rate"],
        )
        return QuoteDelivery(
            document=document,
            conversation_id=row["conversation_id"],
            phone_number_id=row["phone_number_id"],
            access_token_ref=row["access_token_ref"],
        )

    async def count_for_customer(self, customer_id: UUID) -> int:
        connection = self._session.connection()
        with translate_errors():
            count: int = await connection.fetchval(
                "SELECT count(*) FROM public.orders WHERE customer_id = $1", customer_id
            )
        return count
