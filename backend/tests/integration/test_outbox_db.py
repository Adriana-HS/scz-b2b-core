"""Outbox contra Postgres real como `app_backend` (#18, RF-06, ADR-0005/0006)."""

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from app.application.inbound import RecordInboundMessages
from app.application.outbound import RecordDeliveryStatuses, SendOutbound
from app.domain.errors import BusinessRuleError, ErrorCode
from app.domain.jobs import Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.domain.outbound import DeliveryStatus, OutboundKind, OutboundRequest, StatusBatch, StatusUpdate
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork, unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration


class FakeMetaSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, OutboundKind]] = []

    async def send(
        self, phone_number_id: str, token: str, to: str, kind: OutboundKind, payload: dict[str, Any]
    ) -> str:
        self.sent.append((phone_number_id, token, to, kind))
        return f"wamid.out.{secrets.token_hex(6)}"


class DictSecrets:
    def get(self, name: str) -> str | None:
        return {"vault:wa_token_a": "token-de-prueba-a"}.get(name)


async def _new_conversation(pool: asyncpg.Pool, seed: Seed, admin: asyncpg.Connection) -> tuple[UUID, str]:
    """Un cliente que escribe por el número de A: deja conversación con canal y ventana abierta."""
    sender = "591" + "".join(secrets.choice("0123456789") for _ in range(8))
    msg = InboundMessage(f"wamid.{secrets.token_hex(8)}", sender, None, "text", "hola", datetime.now(UTC))
    use_case = RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )
    await use_case(InboundBatch(seed.phone_a, (msg,)))
    conversation_id: UUID = await admin.fetchval(
        "SELECT v.id FROM public.conversations v JOIN public.customers c ON c.id = v.customer_id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        seed.org_a,
        sender,
    )
    return conversation_id, sender


async def _enqueue(pool: asyncpg.Pool, org: UUID, request: OutboundRequest) -> int | None:
    async with PostgresUnitOfWork(pool, TenantContext(org, Actor.BOT)) as uow:
        return await uow.outbound.enqueue(request)


async def test_el_webhook_deja_el_numero_en_la_conversacion(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, _ = await _new_conversation(pool, seed, admin)

    phone = await admin.fetchval(
        "SELECT w.phone_number_id FROM public.conversations v"
        " JOIN public.whatsapp_channels w ON w.id = v.whatsapp_channel_id WHERE v.id = $1",
        conversation_id,
    )
    assert phone == seed.phone_a


async def test_encolar_crea_el_mensaje_y_su_trabajo_y_una_clave_repetida_no_duplica(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, _ = await _new_conversation(pool, seed, admin)
    key = f"turno:{conversation_id}:{secrets.token_hex(4)}"

    first = await _enqueue(
        pool, seed.org_a, OutboundRequest.text(conversation_id, key, "Hola, ¿en qué le ayudo?")
    )
    again = await _enqueue(pool, seed.org_a, OutboundRequest.text(conversation_id, key, "Hola de nuevo"))

    assert first is not None and again is None
    assert (
        await admin.fetchval("SELECT count(*) FROM private.outbound_messages WHERE idempotency_key = $1", key)
        == 1
    )
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM private.jobs"
            " WHERE kind = 'SEND_OUTBOUND' AND (payload->>'outbound_id')::bigint = $1",
            first,
        )
        == 1
    )


async def test_fuera_de_la_ventana_la_bd_rechaza_el_texto(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, sender = await _new_conversation(pool, seed, admin)
    await admin.execute(
        "UPDATE public.customers SET last_inbound_at = now() - interval '2 days'"
        " WHERE organization_id = $1 AND wa_id = $2",
        seed.org_a,
        sender,
    )

    with pytest.raises(BusinessRuleError) as info:
        await _enqueue(
            pool, seed.org_a, OutboundRequest.text(conversation_id, f"k-{secrets.token_hex(4)}", "tarde")
        )

    assert info.value.code is ErrorCode.WINDOW_CLOSED


async def test_envio_completo_por_el_numero_de_la_conversacion(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation_id, sender = await _new_conversation(pool, seed, admin)
    outbound_id = await _enqueue(
        pool, seed.org_a, OutboundRequest.text(conversation_id, f"k-{secrets.token_hex(4)}", "hola")
    )
    assert outbound_id is not None
    meta = FakeMetaSender()
    handler = SendOutbound(unit_of_work=unit_of_work_factory(pool), sender=meta, secrets=DictSecrets())

    job = Job(
        id=1,
        kind="SEND_OUTBOUND",
        organization_id=seed.org_a,
        conversation_key=None,
        payload={"outbound_id": outbound_id},
        attempts=1,
        max_attempts=8,
    )
    await handler(job)
    await handler(job)  # un reintento después del éxito no reenvía

    assert meta.sent == [(seed.phone_a, "token-de-prueba-a", sender, OutboundKind.TEXT)]
    row = await admin.fetchrow(
        "SELECT status, wa_message_id, attempts, sent_at FROM private.outbound_messages WHERE id = $1",
        outbound_id,
    )
    assert row is not None
    assert (row["status"], row["attempts"]) == ("SENT", 1)
    assert row["wa_message_id"].startswith("wamid.out.") and row["sent_at"] is not None


async def _sent_message(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed) -> tuple[int, str]:
    conversation_id, _ = await _new_conversation(pool, seed, admin)
    outbound_id = await _enqueue(
        pool, seed.org_a, OutboundRequest.text(conversation_id, f"k-{secrets.token_hex(4)}", "hola")
    )
    wamid = f"wamid.out.{secrets.token_hex(6)}"
    await admin.execute(
        "UPDATE private.outbound_messages SET status = 'SENT', wa_message_id = $2 WHERE id = $1",
        outbound_id,
        wamid,
    )
    assert outbound_id is not None
    return outbound_id, wamid


async def _status(admin: asyncpg.Connection, outbound_id: int) -> tuple[str, str | None]:
    row = await admin.fetchrow(
        "SELECT status, last_error FROM private.outbound_messages WHERE id = $1", outbound_id
    )
    assert row is not None
    return row["status"], row["last_error"]


def _record(pool: asyncpg.Pool) -> RecordDeliveryStatuses:
    return RecordDeliveryStatuses(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )


async def test_los_estados_solo_avanzan(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed) -> None:
    outbound_id, wamid = await _sent_message(pool, admin, seed)
    now = datetime.now(UTC)

    def upd(status: DeliveryStatus, code: str | None = None) -> StatusBatch:
        return StatusBatch(seed.phone_a, (StatusUpdate(wamid, status, now, code),))

    await _record(pool)(upd(DeliveryStatus.DELIVERED))
    assert (await _status(admin, outbound_id))[0] == "DELIVERED"
    await _record(pool)(upd(DeliveryStatus.READ))
    await _record(pool)(upd(DeliveryStatus.DELIVERED))  # llega tarde: no pisa READ
    await _record(pool)(upd(DeliveryStatus.FAILED, "131026"))  # tampoco un failed después de leído
    assert await _status(admin, outbound_id) == ("READ", None)


async def test_un_failed_antes_de_entregar_queda_con_el_codigo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    outbound_id, wamid = await _sent_message(pool, admin, seed)

    result = await _record(pool)(
        StatusBatch(seed.phone_a, (StatusUpdate(wamid, DeliveryStatus.FAILED, datetime.now(UTC), "131026"),))
    )

    assert result.applied == 1
    assert await _status(admin, outbound_id) == ("FAILED", "META_131026")


async def test_un_estado_por_el_numero_de_otro_negocio_no_toca_nada(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    outbound_id, wamid = await _sent_message(pool, admin, seed)

    result = await _record(pool)(
        StatusBatch(seed.phone_b, (StatusUpdate(wamid, DeliveryStatus.READ, datetime.now(UTC)),))
    )

    assert result.applied == 0
    assert (await _status(admin, outbound_id))[0] == "SENT"  # la RLS de B no ve el mensaje de A


async def test_estados_de_mensajes_desconocidos_se_ignoran(pool: asyncpg.Pool, seed: Seed) -> None:
    result = await _record(pool)(
        StatusBatch(seed.phone_a, (StatusUpdate("wamid.no-existe", DeliveryStatus.READ, datetime.now(UTC)),))
    )

    assert (result.applied, result.ignored) == (0, 1)
