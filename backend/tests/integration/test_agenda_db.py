"""Agenda de punta a punta contra Postgres real (#35): horarios de los seeds del consultorio, reserva
sin doble reserva (restricción de exclusión) y lista interactiva en el outbox. Necesita los seeds."""

import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from app.application.agent import ProcessConversation
from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.inbound import RecordInboundMessages
from app.domain.ai import ModelResponse, StopReason, TokenUsage, ToolCall
from app.domain.booking import parse_slot_token
from app.domain.jobs import PROCESS_CONVERSATION, Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.domain.ports import ChatModel
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory
from app.verticals import VERTICAL_POLICIES

from ..doubles.ai import ScriptedChatModel

pytestmark = pytest.mark.integration

CLINIC = UUID("5eed0000-0000-4000-8000-00000000000b")
TOOLS = VERTICAL_POLICIES["CLINICA_CONSULTORIO"].enabled_tools


def _weekday_ahead() -> str:
    """Un día hábil (lunes a viernes) de acá a 2 o más días, en la hora del negocio."""
    day = (datetime.now(ZoneInfo("America/La_Paz")) + timedelta(days=2)).date()
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


async def _patient(pool: asyncpg.Pool, admin: asyncpg.Connection) -> tuple[UUID, UUID]:
    if not await admin.fetchval("SELECT count(*) FROM public.resources WHERE organization_id = $1", CLINIC):
        pytest.skip("sin los seeds de #27 (supabase db reset los carga)")
    sender = "5917" + "".join(secrets.choice("0123456789") for _ in range(7))
    message = InboundMessage(f"wamid.cita.{secrets.token_hex(6)}", sender, None, "text", "quiero una cita",
                             datetime.now(UTC))  # fmt: skip
    await RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )(InboundBatch("SEED-CLI-0001", (message,)))
    row = await admin.fetchrow(
        "SELECT v.id, v.customer_id FROM public.conversations v"
        " JOIN public.customers c ON c.id = v.customer_id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        CLINIC,
        sender,
    )
    assert row is not None
    return row["id"], row["customer_id"]


async def _run(
    pool: asyncpg.Pool, conversation: UUID, customer: UUID, name: str, args: dict[str, Any]
) -> Any:
    toolbox = AgentToolbox(
        unit_of_work_factory(pool), TurnContext(CLINIC, customer, conversation), enabled=TOOLS
    )
    result = await toolbox.execute(ToolCall("t1", name, args))
    return result.is_error, json.loads(result.content)


async def test_reservar_un_horario_y_que_otro_paciente_no_pueda_tomarlo(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    conversation, customer = await _patient(pool, admin)
    day = _weekday_ahead()

    is_error, availability = await _run(pool, conversation, customer, "check_availability",
                                        {"service_sku": "CON-001", "day": day})  # fmt: skip
    assert not is_error and availability["slots"], availability
    slot = availability["slots"][0]["slot"]
    resource, start = parse_slot_token(slot)

    is_error, held = await _run(pool, conversation, customer, "hold_appointment",
                                {"service_sku": "con-001", "slot": slot})  # fmt: skip
    assert not is_error, held
    order = await admin.fetchrow(
        "SELECT status::text AS status, customer_id, resource_id, lower(slot) AS start FROM public.orders"
        " WHERE reference_code = $1 AND organization_id = $2",
        held["reference_code"],
        CLINIC,
    )
    assert order is not None
    assert (order["status"], order["customer_id"], order["resource_id"], order["start"]) == (
        "HELD", customer, resource, start,
    )  # fmt: skip

    other_conversation, other = await _patient(pool, admin)
    is_error, taken = await _run(pool, other_conversation, other, "hold_appointment",
                                 {"service_sku": "CON-001", "slot": slot})  # fmt: skip
    assert is_error and taken["error"] == "SLOT_TAKEN"
    assert taken["slots"] and slot not in {s["slot"] for s in taken["slots"]}  # alternativas sin el tomado


async def test_los_horarios_llegan_al_cliente_como_lista_interactiva(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    conversation, _ = await _patient(pool, admin)
    await admin.execute(
        "UPDATE public.customers SET privacy_notice_at = now() WHERE id = "
        "(SELECT customer_id FROM public.conversations WHERE id = $1)",
        conversation,
    )
    check = ToolCall("t1", "check_availability", {"service_sku": "CON-001", "day": _weekday_ahead()})
    model = ScriptedChatModel(
        ModelResponse("", (check,), StopReason.TOOL_USE, TokenUsage(10, 5), "guion"),
        ModelResponse("Estos son los horarios:", (), StopReason.END_TURN, TokenUsage(10, 5), "guion"),
    )

    def chat_model(organization_id: UUID, conversation_id: UUID) -> ChatModel:
        return model

    def toolbox(context: TurnContext, enabled: Sequence[str]) -> AgentToolbox:
        return AgentToolbox(unit_of_work_factory(pool), context, enabled=enabled)

    handler = ProcessConversation(
        unit_of_work=unit_of_work_factory(pool),
        chat_model=chat_model,
        toolbox=toolbox,
        policies=VERTICAL_POLICIES,
    )
    outcome = await handler.run(Job(id=1, kind=PROCESS_CONVERSATION, organization_id=CLINIC,
                                    conversation_key=str(conversation),
                                    payload={"conversation_id": str(conversation)}))  # fmt: skip

    assert outcome == "replied"
    row = await admin.fetchrow(
        "SELECT kind, payload FROM private.outbound_messages WHERE conversation_id = $1", conversation
    )
    assert row is not None and row["kind"] == "INTERACTIVE"
    payload = json.loads(row["payload"])
    assert payload["body"] == "Estos son los horarios:" and 1 <= len(payload["rows"]) <= 10
    assert all(r["id"].startswith("slot:") for r in payload["rows"])
