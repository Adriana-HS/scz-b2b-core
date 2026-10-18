"""Corre los casos con el orquestador real (#81) contra la BD con los seeds de #27.

Cada caso es un cliente nuevo, con su historial cargado por los mismos caminos que en producción
(webhook para lo que escribió, historial del bot para lo que respondió). El turno corre con las
herramientas reales; un envoltorio registra qué herramientas pidió el modelo y con qué argumentos.
El aviso de IA se da por dado (no es lo que se mide) y los topes se suben (una corrida no debe
cortarse por la cuota diaria del negocio de ejemplo).
"""

import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from agent_evals.scoring import CaseResult, Observation, ToolUse, score
from agent_evals.suite import Case, Suite
from app.application.agent import ProcessConversation
from app.application.agent_tools import AgentToolbox, TurnContext
from app.application.inbound import RecordInboundMessages
from app.domain.agent import AgentLimits
from app.domain.ai import ToolCall, ToolResult, ToolSpec
from app.domain.jobs import PROCESS_CONVERSATION, Job
from app.domain.messaging import InboundBatch, InboundMessage
from app.domain.ports import ChannelResolver, ChatModel, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext
from app.verticals import VERTICAL_POLICIES

ChatModelFactory = Callable[[UUID, UUID], ChatModel]
_NO_LIMITS = AgentLimits(daily_budget_usd=Decimal(1_000_000), messages_per_minute=1000, turns_per_hour=1000)


class RecordingToolbox:
    """Las herramientas reales, anotando cada pedido del modelo."""

    def __init__(self, inner: AgentToolbox, log: list[tuple[ToolCall, ToolResult]]) -> None:
        self._inner, self._log = inner, log

    def specs(self) -> tuple[ToolSpec, ...]:
        return self._inner.specs()

    async def execute(self, call: ToolCall) -> ToolResult:
        result = await self._inner.execute(call)
        self._log.append((call, result))
        return result


class EvalRunner:
    def __init__(
        self, *, unit_of_work: UnitOfWorkFactory, resolver: ChannelResolver, chat_model: ChatModelFactory
    ) -> None:
        self._unit_of_work = unit_of_work
        self._resolver = resolver
        self._chat_model = chat_model

    async def run(self, suite: Suite, cases: Sequence[Case] | None = None) -> list[CaseResult]:
        return [score(case, await self.observe(suite, case)) for case in (cases or suite.cases)]

    async def observe(self, suite: Suite, case: Case) -> Observation:
        context = TenantContext(suite.organization_id, Actor.BOT)
        conversation = await self._prepare(suite, case, context)
        log: list[tuple[ToolCall, ToolResult]] = []

        def toolbox(turn: TurnContext, enabled: Sequence[str]) -> RecordingToolbox:
            return RecordingToolbox(AgentToolbox(self._unit_of_work, turn, enabled=enabled), log)

        handler = ProcessConversation(
            unit_of_work=self._unit_of_work, chat_model=self._chat_model, toolbox=toolbox,
            policies=VERTICAL_POLICIES, limits=_NO_LIMITS,
        )  # fmt: skip
        job = Job(
            id=0,
            kind=PROCESS_CONVERSATION,
            organization_id=suite.organization_id,
            conversation_key=str(conversation),
            payload={"conversation_id": str(conversation)},
        )
        outcome = await handler.run(job)
        tools = tuple([await self._tool_use(context, call) for call, _ in log])
        return Observation(tools=tools, outcome=outcome, reply=await self._reply(context, conversation))

    async def _prepare(self, suite: Suite, case: Case, context: TenantContext) -> UUID:
        """Un cliente nuevo con el historial del caso; el último mensaje queda pendiente."""
        sender = "5917" + "".join(secrets.choice("0123456789") for _ in range(7))
        record = RecordInboundMessages(resolver=self._resolver, unit_of_work=self._unit_of_work)
        conversation: UUID | None = None
        for turn in (*case.history, None):
            if turn is None or turn.role == "cliente":
                text = case.message if turn is None else turn.text
                message = InboundMessage(f"wamid.eval.{secrets.token_hex(8)}", sender, None, "text", text,
                                         datetime.now(UTC))  # fmt: skip
                await record(InboundBatch(suite.phone_number_id, (message,)))
                conversation = conversation or await self._conversation_of(suite, context, sender)
                if turn is not None:  # lo ya respondido no vuelve a quedar pendiente
                    await self._mark_answered(context, conversation)
            else:
                assert conversation is not None, "el historial empieza por el cliente"  # noqa: S101
                async with self._unit_of_work(context) as uow:
                    await uow.conversations.add_bot_reply(conversation, turn.text)
        assert conversation is not None  # noqa: S101
        async with self._unit_of_work(context) as uow:
            state = await uow.conversations.load_turn(conversation, 1)
            assert state is not None  # noqa: S101
            await uow.conversations.record_privacy_notice(state.customer_id)
        return conversation

    async def _conversation_of(self, suite: Suite, context: TenantContext, wa_id: str) -> UUID:
        """La conversación abierta del cliente (la que acaba de crear el webhook)."""
        channel = await self._resolver.resolve(suite.phone_number_id)
        assert channel is not None, f"canal {suite.phone_number_id} sin cargar: ¿faltan los seeds?"  # noqa: S101
        now = datetime.now(UTC)
        async with self._unit_of_work(context) as uow:
            customer = await uow.inbound.upsert_customer(wa_id, None, now)
            return await uow.inbound.open_conversation(customer, now, channel.channel_id)

    async def _mark_answered(self, context: TenantContext, conversation: UUID) -> None:
        async with self._unit_of_work(context) as uow:
            state = await uow.conversations.load_turn(conversation, 50)
            assert state is not None  # noqa: S101
            await uow.conversations.mark_processed(conversation, [m.id for m in state.pending])

    async def _reply(self, context: TenantContext, conversation: UUID) -> str | None:
        async with self._unit_of_work(context) as uow:
            state = await uow.conversations.load_turn(conversation, 50)
        assert state is not None  # noqa: S101
        return next((m.text for m in reversed(state.history) if m.direction == "OUT"), None)

    async def _tool_use(self, context: TenantContext, call: ToolCall) -> ToolUse:
        """Para create_quote, cada línea como (código, cantidad), aunque el modelo haya usado item_id."""
        skus: list[tuple[str, int]] = []
        if call.name == "create_quote":
            items: list[dict[str, Any]] = [i for i in call.arguments.get("items", []) if isinstance(i, dict)]
            for item in items:
                quantity = int(item.get("quantity", 0))
                if isinstance(item.get("sku"), str):
                    skus.append((item["sku"], quantity))
                elif isinstance(item.get("item_id"), str):
                    async with self._unit_of_work(context) as uow:
                        details = await uow.catalog.get_details(UUID(item["item_id"]))
                    skus.append((details.sku if details else item["item_id"], quantity))
        return ToolUse(call.name, dict(call.arguments), tuple(skus))
