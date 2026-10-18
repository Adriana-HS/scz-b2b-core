"""PDF de cotización de punta a punta contra Postgres real (#26).

cliente escribe -> create_quote (la BD calcula) -> trabajo SEND_QUOTE_PDF en la misma transacción
-> PDF real con las cifras de la BD -> subida a Meta (doble) -> DOCUMENT por media_id en el outbox.
"""

import io
import secrets
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from pypdf import PdfReader

from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.inbound import RecordInboundMessages
from app.application.quote_document import SendQuoteDocument
from app.domain.ai import ToolCall
from app.domain.jobs import SEND_QUOTE_PDF, Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.infrastructure.pdf.reportlab_renderer import ReportlabQuoteRenderer
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration


class Uploader:
    def __init__(self) -> None:
        self.files: list[tuple[str, str, bytes, str]] = []

    async def upload_media(self, phone_number_id: str, token: str, content: bytes, mime_type: str,
                           filename: str) -> str:  # fmt: skip
        self.files.append((phone_number_id, token, content, filename))
        return "media-de-prueba"


class Secrets:
    def get(self, name: str) -> str | None:
        return {"vault:wa_token_a": "token-a"}.get(name)


async def _conversation(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed) -> tuple[UUID, UUID]:
    sender = "5917" + "".join(secrets.choice("0123456789") for _ in range(7))
    message = InboundMessage(
        f"wamid.pdf.{secrets.token_hex(6)}", sender, None, "text", "cotíceme", datetime.now(UTC)
    )
    await RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )(InboundBatch(seed.phone_a, (message,)))
    row = await admin.fetchrow(
        "SELECT v.id, v.customer_id FROM public.conversations v"
        " JOIN public.customers c ON c.id = v.customer_id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        seed.org_a,
        sender,
    )
    assert row is not None
    return row["id"], row["customer_id"]


async def test_la_cotizacion_llega_como_pdf_por_media_id(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation, customer = await _conversation(pool, admin, seed)
    uow = unit_of_work_factory(pool)
    toolbox = AgentToolbox(uow, TurnContext(seed.org_a, customer, conversation))

    result = await toolbox.execute(
        ToolCall("t1", "create_quote", {"items": [{"item_id": str(seed.item_bob_a), "quantity": 2}]})
    )
    assert not result.is_error, result.content

    job_row = await admin.fetchrow(
        "SELECT id, payload::text AS payload, conversation_key FROM private.jobs"
        " WHERE kind = $1 AND organization_id = $2 AND payload ->> 'conversation_id' = $3",
        SEND_QUOTE_PDF,
        seed.org_a,
        str(conversation),
    )
    assert job_row is not None and job_row["conversation_key"] == str(conversation)  # va después del turno
    order = await admin.fetchrow(
        "SELECT id, reference_code, total_bs FROM public.orders WHERE conversation_id = $1", conversation
    )
    assert order is not None

    import json

    job = Job(
        id=job_row["id"],
        kind=SEND_QUOTE_PDF,
        organization_id=seed.org_a,
        conversation_key=job_row["conversation_key"],
        payload=json.loads(job_row["payload"]),
    )
    uploader = Uploader()
    await SendQuoteDocument(
        unit_of_work=uow, renderer=ReportlabQuoteRenderer(), uploader=uploader, secrets=Secrets()
    )(job)

    [(phone, token, pdf, filename)] = uploader.files
    assert (phone, token, filename) == (seed.phone_a, "token-a", f"Cotizacion-{order['reference_code']}.pdf")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert order["reference_code"] in text
    total = f"{order['total_bs']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    assert total in text  # la cifra de la BD, en formato boliviano
    outbox = await admin.fetchrow(
        "SELECT kind, payload ->> 'media_id' AS media_id, idempotency_key FROM private.outbound_messages"
        " WHERE conversation_id = $1",
        conversation,
    )
    assert outbox is not None
    assert (outbox["kind"], outbox["media_id"], outbox["idempotency_key"]) == (
        "DOCUMENT", "media-de-prueba", f"quote-pdf:{order['id']}",
    )  # fmt: skip


async def test_una_cotizacion_de_otro_negocio_no_se_lee(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation, customer = await _conversation(pool, admin, seed)
    uow = unit_of_work_factory(pool)
    toolbox = AgentToolbox(uow, TurnContext(seed.org_a, customer, conversation))
    await toolbox.execute(
        ToolCall("t1", "create_quote", {"items": [{"item_id": str(seed.item_bob_a), "quantity": 1}]})
    )
    order_id = await admin.fetchval("SELECT id FROM public.orders WHERE conversation_id = $1", conversation)

    from app.domain.tenancy import Actor, TenantContext

    async with uow(TenantContext(seed.org_b, Actor.SYSTEM)) as other:
        assert await other.orders.quote_delivery(order_id) is None  # RLS: el negocio B no la ve
