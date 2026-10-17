"""Consentimiento por WhatsApp contra Postgres real como `app_backend` (#13, RF-08)."""

import secrets
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest

from app.application.inbound import RecordInboundMessages
from app.domain.errors import BusinessRuleError, ErrorCode
from app.domain.messaging import InboundBatch, InboundMessage
from app.domain.outbound import OutboundRequest, TemplateCategory
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork, unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration


def _wa_id() -> str:
    return "591" + "".join(secrets.choice("0123456789") for _ in range(8))


async def _say(pool: asyncpg.Pool, seed: Seed, sender: str, text: str) -> str:
    wamid = f"wamid.{secrets.token_hex(8)}"
    use_case = RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )
    await use_case(
        InboundBatch(seed.phone_a, (InboundMessage(wamid, sender, None, "text", text, datetime.now(UTC)),))
    )
    return wamid


async def _customer(admin: asyncpg.Connection, seed: Seed, sender: str) -> asyncpg.Record:
    row = await admin.fetchrow(
        "SELECT c.id, c.optin_utility_at, c.optin_marketing_at, c.optout_at, v.id AS conversation_id"
        " FROM public.customers c JOIN public.conversations v ON v.customer_id = c.id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        seed.org_a,
        sender,
    )
    assert row is not None
    return row


async def _template(
    pool: asyncpg.Pool, seed: Seed, conversation_id: UUID, category: TemplateCategory
) -> None:
    request = OutboundRequest.template(
        conversation_id,
        f"tpl-{secrets.token_hex(4)}",
        "recordatorio_cita",
        "es",
        category,
    )
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.SYSTEM)) as uow:
        await uow.outbound.enqueue(request)


async def test_baja_por_whatsapp_deja_evidencia_confirma_y_no_encola_al_agente(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    wamid = await _say(pool, seed, sender, "¡No más!")

    customer = await _customer(admin, seed, sender)
    assert customer["optout_at"] is not None
    event = await admin.fetchrow(
        "SELECT kind, source, evidence FROM public.consent_events WHERE customer_id = $1", customer["id"]
    )
    assert event is not None and tuple(event) == ("OPTOUT", "WHATSAPP_KEYWORD", wamid)
    reply = await admin.fetchrow(
        "SELECT kind, payload->>'body' AS body FROM private.outbound_messages WHERE idempotency_key = $1",
        f"consent:{wamid}",
    )
    assert reply is not None and reply["kind"] == "TEXT" and "ALTA" in reply["body"]
    queued_turns = await admin.fetchval(
        "SELECT count(*) FROM private.jobs WHERE kind = 'PROCESS_CONVERSATION' AND conversation_key = $1",
        str(customer["conversation_id"]),
    )
    assert queued_turns == 0  # la baja no despierta al agente


async def test_despues_de_la_baja_ninguna_plantilla_sale_y_alta_habilita_solo_utilidad(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    await _say(pool, seed, sender, "BAJA")
    conversation_id = (await _customer(admin, seed, sender))["conversation_id"]

    with pytest.raises(BusinessRuleError) as info:
        await _template(pool, seed, conversation_id, TemplateCategory.UTILITY)
    assert info.value.code is ErrorCode.CONSENT_REQUIRED

    await _say(pool, seed, sender, "ALTA")
    customer = await _customer(admin, seed, sender)
    assert customer["optout_at"] is None and customer["optin_utility_at"] is not None

    await _template(pool, seed, conversation_id, TemplateCategory.UTILITY)  # ahora sí
    with pytest.raises(BusinessRuleError):
        await _template(pool, seed, conversation_id, TemplateCategory.MARKETING)  # marketing sigue sin opt-in
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM public.consent_events WHERE customer_id = $1", customer["id"]
        )
        == 2
    )  # BAJA y ALTA, ambas con evidencia


async def test_un_mensaje_comun_no_cambia_el_consentimiento(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    await _say(pool, seed, sender, "no más frenos, quiero cotizar pastillas para la Hilux")

    customer = await _customer(admin, seed, sender)
    assert customer["optout_at"] is None
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM public.consent_events WHERE customer_id = $1", customer["id"]
        )
        == 0
    )
