"""Webhook -> BD contra Postgres real como `app_backend` (RF-01, ADR-0005).

Prueba el caso de uso con los adaptadores reales: resolución de canal antes del tenant,
una transacción por lote, idempotencia por wa_message_id y encolado con debounce.
"""

import secrets
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest

from app.application.inbound import RecordInboundMessages
from app.domain.messaging import InboundBatch, InboundMessage
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 15, 30, tzinfo=UTC)


def _wa_id() -> str:
    return "591" + "".join(secrets.choice("0123456789") for _ in range(8))


def _msg(sender: str, text: str = "pastilla freno hilux") -> InboundMessage:
    wamid = f"wamid.{secrets.token_hex(8)}"
    return InboundMessage(
        wamid, sender, "Cliente sintético", "text", text, NOW, raw={"id": wamid, "type": "text"}
    )


def _use_case(pool: asyncpg.Pool) -> RecordInboundMessages:
    return RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )


async def _counts(admin: asyncpg.Connection, org: UUID, wa_id: str) -> dict[str, int]:
    row = await admin.fetchrow(
        """SELECT
             (SELECT count(*) FROM public.customers c
               WHERE c.organization_id = $1 AND c.wa_id = $2) AS customers,
             (SELECT count(*) FROM public.conversations v JOIN public.customers c ON c.id = v.customer_id
               WHERE c.organization_id = $1 AND c.wa_id = $2) AS conversations,
             (SELECT count(*) FROM public.messages m JOIN public.conversations v ON v.id = m.conversation_id
               JOIN public.customers c ON c.id = v.customer_id
               WHERE c.organization_id = $1 AND c.wa_id = $2) AS messages,
             (SELECT count(*) FROM private.jobs j
               JOIN public.conversations v ON v.id::text = j.conversation_key
               JOIN public.customers c ON c.id = v.customer_id
               WHERE c.organization_id = $1 AND c.wa_id = $2
                 AND j.kind = 'PROCESS_CONVERSATION' AND j.status = 'QUEUED') AS queued_jobs""",
        org,
        wa_id,
    )
    assert row is not None
    return dict(row)


async def test_el_primer_mensaje_crea_cliente_conversacion_mensaje_y_trabajo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    message = _msg(sender)

    result = await _use_case(pool)(InboundBatch(seed.phone_a, (message,)))

    assert result.accepted == 1
    assert await _counts(admin, seed.org_a, sender) == {
        "customers": 1, "conversations": 1, "messages": 1, "queued_jobs": 1,
    }  # fmt: skip
    stored = await admin.fetchrow(
        "SELECT organization_id, event_type, payload->>'id' AS id"
        " FROM private.inbound_events WHERE wa_message_id = $1",
        message.wa_message_id,
    )
    assert stored is not None
    assert (stored["organization_id"], stored["event_type"], stored["id"]) == (
        seed.org_a,
        "message",
        message.wa_message_id,
    )
    customer = await admin.fetchrow(
        "SELECT display_name, last_inbound_at FROM public.customers"
        " WHERE organization_id = $1 AND wa_id = $2",
        seed.org_a,
        sender,
    )
    assert customer is not None
    assert (customer["display_name"], customer["last_inbound_at"]) == ("Cliente sintético", NOW)


async def test_un_reenvio_de_meta_no_crea_filas_nuevas(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    message = _msg(sender)
    await _use_case(pool)(InboundBatch(seed.phone_a, (message,)))
    before = await _counts(admin, seed.org_a, sender)

    result = await _use_case(pool)(InboundBatch(seed.phone_a, (message,)))

    assert (result.accepted, result.duplicates) == (0, 1)
    assert await _counts(admin, seed.org_a, sender) == before


async def test_una_rafaga_queda_en_una_conversacion_y_un_solo_trabajo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    burst = tuple(_msg(sender, f"mensaje {i}") for i in range(3))

    result = await _use_case(pool)(InboundBatch(seed.phone_a, burst))

    assert result.accepted == 3
    assert await _counts(admin, seed.org_a, sender) == {
        "customers": 1, "conversations": 1, "messages": 3, "queued_jobs": 1,
    }  # fmt: skip


async def test_cada_numero_escribe_solo_en_su_negocio(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()

    await _use_case(pool)(InboundBatch(seed.phone_b, (_msg(sender),)))

    assert (await _counts(admin, seed.org_b, sender))["messages"] == 1
    assert (await _counts(admin, seed.org_a, sender))["customers"] == 0


@pytest.mark.parametrize("phone", ["PNID-NO-EXISTE", "phone_inactive"])
async def test_numero_desconocido_o_inactivo_no_escribe_nada(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed, phone: str
) -> None:
    sender = _wa_id()
    phone_number_id = getattr(seed, phone, phone)

    result = await _use_case(pool)(InboundBatch(phone_number_id, (_msg(sender),)))

    assert result.unknown_channel
    assert (await _counts(admin, seed.org_a, sender))["customers"] == 0


async def test_si_falla_un_mensaje_no_queda_nada_del_lote(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    sender = _wa_id()
    good = _msg(sender)
    # Un wa_id que la BD rechaza (CHECK ^[0-9]{8,15}$): el webhook ya los filtra; aquí se fuerza
    bad = InboundMessage(f"wamid.{secrets.token_hex(8)}", "no-es-telefono", None, "text", "x", NOW)

    with pytest.raises(asyncpg.PostgresError):
        await _use_case(pool)(InboundBatch(seed.phone_a, (good, bad)))

    assert (await _counts(admin, seed.org_a, sender))["customers"] == 0
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM private.inbound_events WHERE wa_message_id = $1", good.wa_message_id
        )
        == 0
    )  # sin el evento, el reintento de Meta lo procesará completo
