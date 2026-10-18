"""Orquestador del agente de punta a punta contra Postgres real (#81).

webhook (RecordInboundMessages) -> trabajo PROCESS_CONVERSATION -> turno con el modelo guionado
y las herramientas reales -> una respuesta en el outbox, el historial y el costo en llm_calls.
"""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from app.application.agent import TEMPLATE_QUOTA, ProcessConversation
from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.ai import CircuitBreaker, RecordLlmCalls, resilient_chat_model
from app.application.inbound import RecordInboundMessages
from app.domain.ai import AiPurpose, ModelPricing, ModelResponse, StopReason, TokenUsage, ToolCall
from app.domain.jobs import PROCESS_CONVERSATION, Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory
from app.verticals.common import ai_notice

from ..doubles.ai import ScriptedChatModel
from .conftest import Seed

pytestmark = pytest.mark.integration

PRICING = ModelPricing(Decimal("0.10"), Decimal("0.50"), Decimal("0.01"), Decimal("0.125"))


class Policy:
    enabled_tools = ("search_catalog", "get_item_details", "create_quote", "handoff_to_human")

    def system_prompt(self, business_name: str) -> str:
        return f"Eres el asistente de {business_name}."

    def ai_notice(self, business_name: str, approved: str | None) -> str:
        return ai_notice(business_name, approved)


async def _customer_writes(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed, text: str) -> UUID:
    sender = "5917" + "".join(secrets.choice("0123456789") for _ in range(7))
    message = InboundMessage(
        f"wamid.agent.{secrets.token_hex(6)}", sender, None, "text", text, datetime.now(UTC)
    )
    use_case = RecordInboundMessages(
        resolver=PostgresChannelResolver(pool), unit_of_work=unit_of_work_factory(pool)
    )
    await use_case(InboundBatch(seed.phone_a, (message,)))
    conversation: UUID = await admin.fetchval(
        "SELECT v.id FROM public.conversations v JOIN public.customers c ON c.id = v.customer_id"
        " WHERE c.organization_id = $1 AND c.wa_id = $2",
        seed.org_a,
        sender,
    )
    return conversation


def _handler(pool: asyncpg.Pool, model: ScriptedChatModel) -> ProcessConversation:
    uow = unit_of_work_factory(pool)
    breaker = CircuitBreaker()

    def chat_model(organization_id: UUID, conversation_id: UUID) -> object:
        return resilient_chat_model(
            model, breaker=breaker, recorder=RecordLlmCalls(uow), pricing=PRICING, provider="anthropic",
            model="claude-haiku-5-5", purpose=AiPurpose.CHAT, organization_id=organization_id,
            conversation_id=conversation_id,
        )  # fmt: skip

    def toolbox(context: TurnContext, enabled: Sequence[str]) -> AgentToolbox:
        return AgentToolbox(uow, context, enabled=enabled)

    return ProcessConversation(
        unit_of_work=uow, chat_model=chat_model, toolbox=toolbox,  # type: ignore[arg-type]
        policies={"IMPORTADORA_REPUESTOS": Policy()},
    )  # fmt: skip


def _job(seed: Seed, conversation_id: UUID) -> Job:
    return Job(
        id=1,
        kind=PROCESS_CONVERSATION,
        organization_id=seed.org_a,
        conversation_key=str(conversation_id),
        payload={"conversation_id": str(conversation_id)},
    )


async def test_un_turno_completo_deja_una_respuesta_y_su_costo(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, f"¿tienen {seed.sku_bob_a}?")
    search = ToolCall(id="t1", name="search_catalog", arguments={"query": seed.sku_bob_a})
    model = ScriptedChatModel(
        ModelResponse("", (search,), StopReason.TOOL_USE, TokenUsage(800, 40), "claude-haiku-5-5"),
        ModelResponse("Sí, a 480,00 Bs.", (), StopReason.END_TURN, TokenUsage(900, 30), "claude-haiku-5-5"),
    )

    await _handler(pool, model)(_job(seed, conversation))

    outbox = await admin.fetch(
        "SELECT kind, payload ->> 'body' AS body FROM private.outbound_messages WHERE conversation_id = $1",
        conversation,
    )
    business = await admin.fetchval("SELECT name FROM public.organizations WHERE id = $1", seed.org_a)
    reply = f"{ai_notice(business, None)}\n\nSí, a 480,00 Bs."  # primer contacto: con el aviso de IA (#29)
    assert [(r["kind"], r["body"]) for r in outbox] == [("TEXT", reply)]
    noticed = await admin.fetchval(
        "SELECT c.privacy_notice_at FROM public.customers c"
        " JOIN public.conversations v ON v.customer_id = c.id WHERE v.id = $1",
        conversation,
    )
    assert noticed is not None
    history = await admin.fetch(
        "SELECT direction, sender, body, processed_at FROM public.messages WHERE conversation_id = $1"
        " ORDER BY created_at, id",
        conversation,
    )
    assert [(r["direction"], r["sender"]) for r in history] == [("IN", "CUSTOMER"), ("OUT", "BOT")]
    assert history[0]["processed_at"] is not None
    tool_result = model.calls[1][1][-1].tool_results[0].content
    assert '"480.00"' in tool_result  # el precio lo trajo la herramienta desde la BD
    costs = await admin.fetch(
        "SELECT outcome, input_tokens FROM private.llm_calls WHERE conversation_id = $1 ORDER BY id",
        conversation,
    )
    assert [(r["outcome"], r["input_tokens"]) for r in costs] == [("OK", 800), ("OK", 900)]


async def test_repetir_el_trabajo_no_duplica_la_respuesta(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "hola")
    handler = _handler(pool, ScriptedChatModel(
        ModelResponse("¡Hola! ¿Qué repuesto buscas?", (), StopReason.END_TURN, TokenUsage(10, 5), "m")
    ))  # fmt: skip

    await handler(_job(seed, conversation))
    await handler(_job(seed, conversation))  # el worker murió después del COMMIT y el trabajo se repite

    count = await admin.fetchval(
        "SELECT count(*) FROM private.outbound_messages WHERE conversation_id = $1", conversation
    )
    assert count == 1


async def test_con_una_persona_a_cargo_no_responde(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "quiero hablar con alguien")
    await admin.execute("UPDATE public.conversations SET mode = 'HUMAN' WHERE id = $1", conversation)
    model = ScriptedChatModel(ModelResponse("no", (), StopReason.END_TURN, TokenUsage(1, 1), "m"))

    await _handler(pool, model)(_job(seed, conversation))

    assert model.calls == []
    assert (
        await admin.fetchval(
            "SELECT count(*) FROM private.outbound_messages WHERE conversation_id = $1", conversation
        )
        == 0
    )


# --- Cuotas (#25): el costo del día y la actividad del cliente salen de la BD --------------------
@pytest.fixture
async def llm_call(admin: asyncpg.Connection) -> AsyncIterator[Callable[..., Awaitable[None]]]:
    """Inserta costos de IA y los borra al final: si quedaran, agotarían la cuota de las otras pruebas."""
    ids: list[int] = []

    async def insert(org: UUID, cost: str, ago: timedelta = timedelta(0)) -> None:
        ids.append(await admin.fetchval(
            "INSERT INTO private.llm_calls (organization_id, purpose, provider, model, cost_usd, created_at)"
            " VALUES ($1, 'CHAT', 'anthropic', 'claude-haiku-5-5', $2, now() - $3::interval)"
            " RETURNING id",
            org, Decimal(cost), ago,
        ))  # fmt: skip

    yield insert
    await admin.execute("DELETE FROM private.llm_calls WHERE id = ANY($1::bigint[])", ids)


async def test_el_costo_del_dia_es_solo_de_hoy_y_del_negocio(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed, llm_call: Callable[..., Awaitable[None]]
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "hola")
    baseline = await admin.fetchval(
        "SELECT coalesce(sum(cost_usd), 0) FROM private.llm_calls WHERE organization_id = $1"
        " AND created_at >= (date_trunc('day', now() AT TIME ZONE 'America/La_Paz')"
        " AT TIME ZONE 'America/La_Paz')",
        seed.org_a,
    )
    await llm_call(seed.org_a, "0.120000")
    await llm_call(seed.org_a, "5.000000", ago=timedelta(days=2))  # otro día
    await llm_call(seed.org_b, "9.000000")  # otro negocio

    async with unit_of_work_factory(pool)(TenantContext(seed.org_a, Actor.BOT)) as uow:
        state = await uow.conversations.load_turn(conversation, 12)

    assert state is not None
    assert state.usage.ai_cost_today_usd == baseline + Decimal("0.12")
    assert state.usage.customer_messages_last_minute == 1
    assert state.usage.bot_replies_last_hour == 0


async def test_con_la_cuota_agotada_responde_la_plantilla_y_pasa_a_una_persona(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed, llm_call: Callable[..., Awaitable[None]]
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "hola")
    await llm_call(seed.org_a, "1000.000000")  # muy por encima de cualquier tope
    model = ScriptedChatModel(ModelResponse("no", (), StopReason.END_TURN, TokenUsage(1, 1), "m"))

    await _handler(pool, model)(_job(seed, conversation))

    assert model.calls == []
    body = await admin.fetchval(
        "SELECT payload ->> 'body' FROM private.outbound_messages WHERE conversation_id = $1", conversation
    )
    assert body.endswith(TEMPLATE_QUOTA)  # cliente nuevo: con el aviso de IA delante (#29)
    mode = await admin.fetchval("SELECT mode::text FROM public.conversations WHERE id = $1", conversation)
    assert mode == "HUMAN"


# --- Aviso de IA (#29) ----------------------------------------------------------------------------
async def test_el_turno_lee_el_aviso_y_el_texto_aprobado_por_el_negocio(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    conversation = await _customer_writes(pool, admin, seed, "hola")
    own = "Le atiende el asistente automático de la importadora. Para hablar con una persona, pídalo."
    before = await admin.fetchval("SELECT config FROM public.organizations WHERE id = $1", seed.org_a)
    await admin.execute(
        "UPDATE public.organizations SET config = config || jsonb_build_object('ai_notice', $2::text)"
        " WHERE id = $1",
        seed.org_a,
        own,
    )
    try:
        async with unit_of_work_factory(pool)(TenantContext(seed.org_a, Actor.BOT)) as uow:
            state = await uow.conversations.load_turn(conversation, 12)
            assert state is not None
            assert (state.privacy_notice_at, state.ai_notice) == (None, own)
            await uow.conversations.record_privacy_notice(state.customer_id)
        async with unit_of_work_factory(pool)(TenantContext(seed.org_a, Actor.BOT)) as uow:
            after = await uow.conversations.load_turn(conversation, 12)
    finally:
        await admin.execute(
            "UPDATE public.organizations SET config = $2::jsonb WHERE id = $1", seed.org_a, before
        )

    assert after is not None and after.privacy_notice_at is not None


async def test_la_fecha_del_primer_aviso_no_se_pisa(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    """La evidencia es la PRIMERA vez que se informó (RF-03): registrar otra vez no la cambia."""
    conversation = await _customer_writes(pool, admin, seed, "hola")
    customer = await admin.fetchval(
        "SELECT customer_id FROM public.conversations WHERE id = $1", conversation
    )
    first = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    await admin.execute("UPDATE public.customers SET privacy_notice_at = $2 WHERE id = $1", customer, first)

    async with unit_of_work_factory(pool)(TenantContext(seed.org_a, Actor.BOT)) as uow:
        await uow.conversations.record_privacy_notice(customer)

    assert (
        await admin.fetchval("SELECT privacy_notice_at FROM public.customers WHERE id = $1", customer)
        == first
    )
