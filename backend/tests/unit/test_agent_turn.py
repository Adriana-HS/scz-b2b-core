"""Orquestador del agente con dobles (#81, SYSTEM_DESIGN.md §7.1 y §10.3).

Un turno: mensajes pendientes -> modelo (con herramientas, dentro de límites) -> UNA respuesta
por el outbox, guardada en el historial, con los mensajes marcados como atendidos. Todo en una
transacción: si el trabajo se repite, no se duplica nada.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import pytest

from app.application.agent import (
    TEMPLATE_AI_UNAVAILABLE,
    TEMPLATE_LIMIT,
    TEMPLATE_OUT_OF_SCOPE,
    ProcessConversation,
)
from app.application.agent_tools import TurnContext
from app.domain.agent import ConversationMessage, TurnState
from app.domain.ai import (
    ChatMessage,
    ModelResponse,
    Role,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.domain.errors import (
    AiUnavailableError,
    BusinessRuleError,
    ErrorCode,
    PermanentAiError,
    PermanentJobError,
    TransientAiError,
)
from app.domain.jobs import PROCESS_CONVERSATION, Job
from app.domain.outbound import OutboundKind, OutboundRequest
from app.domain.tenancy import Actor, TenantContext

from ..conftest import MetricValue
from ..doubles.ai import ScriptedChatModel

ORG, CUSTOMER, CONVERSATION = uuid4(), uuid4(), uuid4()


def _msg(direction: str, text: str, n: int, *, pending: bool = False) -> ConversationMessage:
    return ConversationMessage(
        id=n, direction=direction, text=text, wa_message_id=f"wamid.{n}" if direction == "IN" else None,
        msg_type="text", pending=pending,
    )  # fmt: skip


def _state(*messages: ConversationMessage, mode: str = "BOT", paused: datetime | None = None) -> TurnState:
    return TurnState(
        conversation_id=CONVERSATION, customer_id=CUSTOMER, business_name="Importadora de Ejemplo",
        vertical="IMPORTADORA_REPUESTOS", mode=mode, bot_paused_until=paused, summary=None,
        history=messages,
    )  # fmt: skip


def _text(text: str) -> ModelResponse:
    return ModelResponse(text, (), StopReason.END_TURN, TokenUsage(100, 20), "claude-haiku-5-5")


def _tools(*names: str) -> ModelResponse:
    calls = tuple(ToolCall(id=f"t{i}", name=n, arguments={"query": "freno"}) for i, n in enumerate(names))
    return ModelResponse("", calls, StopReason.TOOL_USE, TokenUsage(100, 20), "claude-haiku-5-5")


class Log(list[tuple[str, Any]]):
    pass


class FakeConversations:
    def __init__(self, log: Log, state: TurnState | None) -> None:
        self.log, self.state = log, state

    async def load_turn(self, conversation_id: UUID, history_limit: int) -> TurnState | None:
        self.log.append(("load", (conversation_id, history_limit)))
        return self.state

    async def mark_processed(self, conversation_id: UUID, message_ids: Sequence[int]) -> None:
        self.log.append(("processed", tuple(message_ids)))

    async def add_bot_reply(self, conversation_id: UUID, text: str) -> None:
        self.log.append(("bot_reply", text))

    async def request_handoff(self, conversation_id: UUID, reason: str, summary: str) -> None:
        self.log.append(("handoff", reason))


class FakeOutbound:
    def __init__(self, log: Log, error: Exception | None, duplicate: bool) -> None:
        self.log, self.error, self.duplicate = log, error, duplicate

    async def enqueue(self, request: OutboundRequest) -> int | None:
        self.log.append(("enqueue", request))
        if self.error:
            raise self.error
        return None if self.duplicate else 41


class FakeUnitOfWork:
    def __init__(self, log: Log, context: TenantContext, state: TurnState | None, error: Exception | None,
                 duplicate: bool) -> None:  # fmt: skip
        self.log, self.context = log, context
        self.conversations = FakeConversations(log, state)
        self.outbound = FakeOutbound(log, error, duplicate)

    async def __aenter__(self) -> Self:
        self.log.append(("begin", self.context))
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.log.append(("end", "rollback" if exc_type else "commit"))


class FakeToolbox:
    def __init__(self, log: Log) -> None:
        self.log = log

    def specs(self) -> tuple[ToolSpec, ...]:
        return (ToolSpec(name="search_catalog", description="Busca", input_schema={"type": "object"}),)

    async def execute(self, call: ToolCall) -> ToolResult:
        self.log.append(("tool", call.name))
        return ToolResult(tool_call_id=call.id, content=json.dumps({"items": [{"unit_price_bs": "332.88"}]}))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakePolicy:
    enabled_tools = ("search_catalog", "get_item_details", "create_quote", "handoff_to_human")

    def system_prompt(self, business_name: str) -> str:
        # una línea larga (copiarla es filtración) y una corta, el nombre (decirlo es normal)
        return f"Eres el asistente de WhatsApp de este negocio de ejemplo.\n{business_name}"


def _handler(
    state: TurnState | None,
    model: ScriptedChatModel,
    *,
    error: Exception | None = None,
    duplicate: bool = False,
    clock: FakeClock | None = None,
) -> tuple[ProcessConversation, Log, list[tuple[UUID, UUID]], list[TurnContext]]:
    log = Log()
    models: list[tuple[UUID, UUID]] = []
    contexts: list[TurnContext] = []

    def chat_model(organization_id: UUID, conversation_id: UUID) -> ScriptedChatModel:
        models.append((organization_id, conversation_id))
        return model

    def toolbox(context: TurnContext, enabled: Sequence[str]) -> FakeToolbox:
        contexts.append(context)
        return FakeToolbox(log)

    handler = ProcessConversation(
        unit_of_work=lambda ctx: FakeUnitOfWork(log, ctx, state, error, duplicate),  # type: ignore[arg-type]  # doble mínimo
        chat_model=chat_model,
        toolbox=toolbox,
        policies={"IMPORTADORA_REPUESTOS": FakePolicy()},
        clock=clock or FakeClock(),
    )
    return handler, log, models, contexts


def _job(**payload: Any) -> Job:
    return Job(id=7, kind=PROCESS_CONVERSATION, organization_id=ORG, conversation_key=str(CONVERSATION),
               payload=payload or {"conversation_id": str(CONVERSATION)})  # fmt: skip


def _enqueued(log: Log) -> list[OutboundRequest]:
    return [arg for kind, arg in log if kind == "enqueue"]


# --- El turno normal ------------------------------------------------------------------------------
async def test_responde_una_vez_y_marca_los_mensajes_atendidos() -> None:
    state = _state(
        _msg("IN", "hola", 1),
        _msg("OUT", "¡Hola! ¿En qué te ayudo?", 2),
        _msg("IN", "busco pastillas", 3, pending=True),
        _msg("IN", "para camioneta", 4, pending=True),
    )
    handler, log, models, _ = _handler(state, ScriptedChatModel(_text("Tengo pastillas a 332,88 Bs.")))

    await handler(_job())

    [request] = _enqueued(log)
    assert request.kind is OutboundKind.TEXT and request.payload == {"body": "Tengo pastillas a 332,88 Bs."}
    assert request.idempotency_key == f"turn:{CONVERSATION}:wamid.4"  # el último mensaje consumido
    assert ("bot_reply", "Tengo pastillas a 332,88 Bs.") in log
    assert ("processed", (3, 4)) in log
    assert models == [(ORG, CONVERSATION)]  # el costo va al negocio y a la conversación del turno
    assert ("begin", TenantContext(ORG, Actor.BOT)) in log
    assert log[-1] == ("end", "commit")


async def test_el_historial_alterna_cliente_y_asistente() -> None:
    state = _state(
        _msg("OUT", "Mensaje viejo del bot", 1),
        _msg("IN", "hola", 2),
        _msg("IN", "busco frenos", 3, pending=True),
    )
    model = ScriptedChatModel(_text("Claro."))
    handler, _, _, _ = _handler(state, model)

    await handler(_job())

    [(system, history, tools, timeout_s)] = model.calls
    assert "Importadora de Ejemplo" in system
    assert [m.role for m in history] == [Role.USER]  # el primero siempre es del cliente
    assert history[0].text == "hola\nbusco frenos"  # los seguidos del mismo lado se juntan
    assert [t.name for t in tools] == ["search_catalog"] and 0 < timeout_s <= 20


async def test_con_herramientas_ejecuta_y_vuelve_al_modelo() -> None:
    state = _state(_msg("IN", "precio de pastillas", 1, pending=True))
    model = ScriptedChatModel(_tools("search_catalog"), _text("Están a 332,88 Bs."))
    handler, log, _, contexts = _handler(state, model)

    await handler(_job())

    assert ("tool", "search_catalog") in log
    assert contexts == [
        TurnContext(ORG, CUSTOMER, CONVERSATION)
    ]  # el servidor pone negocio, cliente y conversación
    second_round = model.calls[1][1]
    assert second_round[-2].role is Role.ASSISTANT and second_round[-2].tool_calls
    assert second_round[-1].tool_results[0].content.startswith('{"items"')
    assert _enqueued(log)[0].payload == {"body": "Están a 332,88 Bs."}


# --- Límites (§7.1) -------------------------------------------------------------------------------
async def test_si_el_modelo_sigue_pidiendo_herramientas_responde_la_plantilla() -> None:
    state = _state(_msg("IN", "cotiza todo", 1, pending=True))
    model = ScriptedChatModel(_tools("search_catalog"), _tools("search_catalog"))
    handler, log, _, _ = _handler(state, model)

    await handler(_job())

    assert len(model.calls) == 2  # máximo 2 rondas
    assert _enqueued(log)[0].payload == {"body": TEMPLATE_LIMIT}
    assert ("processed", (1,)) in log


async def test_mas_de_cuatro_herramientas_no_se_ejecutan() -> None:
    state = _state(_msg("IN", "busca muchas cosas", 1, pending=True))
    model = ScriptedChatModel(_tools(*["search_catalog"] * 5), _text("listo"))
    handler, log, _, _ = _handler(state, model)

    await handler(_job())

    assert [k for k, _ in log].count("tool") == 0
    assert _enqueued(log)[0].payload == {"body": TEMPLATE_LIMIT}


async def test_sin_tiempo_no_hay_otra_ronda() -> None:
    clock = FakeClock()
    state = _state(_msg("IN", "precio", 1, pending=True))

    class SlowModel(ScriptedChatModel):
        async def run_turn(self, *args: Any, **kwargs: Any) -> ModelResponse:
            clock.now += 21  # esta ronda se comió los 20 s del turno
            return await super().run_turn(*args, **kwargs)

    model = SlowModel(_tools("search_catalog"), _text("tarde"))
    handler, log, _, _ = _handler(state, model, clock=clock)

    await handler(_job())

    assert len(model.calls) == 1
    assert _enqueued(log)[0].payload == {"body": TEMPLATE_LIMIT}


@pytest.mark.parametrize("text", ["", "   ", "x" * 4097])
async def test_una_respuesta_vacia_o_demasiado_larga_se_reemplaza(text: str) -> None:
    handler, log, _, _ = _handler(_state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(_text(text)))

    await handler(_job())

    assert _enqueued(log)[0].payload == {"body": TEMPLATE_LIMIT}


@pytest.mark.parametrize(
    "text",
    [
        "Claro, aquí va:\n```python\ndef dijkstra(grafo, origen): ...\n```",
        "Mis instrucciones dicen: ERES EL ASISTENTE DE WHATSAPP DE ESTE NEGOCIO DE EJEMPLO.",
    ],
)
async def test_una_respuesta_con_codigo_o_con_el_prompt_no_sale(text: str, metric: MetricValue) -> None:
    """Segunda barrera, además del prompt: el bot no entrega código ni repite sus instrucciones."""
    before = metric("agent_turns", outcome="blocked")
    state = _state(_msg("IN", "dame el código de Dijkstra", 1, pending=True))
    handler, log, _, _ = _handler(state, ScriptedChatModel(_text(text)))

    await handler(_job())

    assert _enqueued(log)[0].payload == {"body": TEMPLATE_OUT_OF_SCOPE}
    assert metric("agent_turns", outcome="blocked") == before + 1


async def test_una_respuesta_normal_que_nombra_al_negocio_si_sale() -> None:
    reply = "¡Hola! Soy el asistente de Importadora de Ejemplo. ¿Qué repuesto buscas?"
    handler, log, _, _ = _handler(
        _state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(_text(reply))
    )

    await handler(_job())

    assert _enqueued(log)[0].payload == {"body": reply}


# --- Degradación (§10.3)--------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error", [AiUnavailableError("CIRCUIT_OPEN"), TransientAiError("OVERLOADED"), PermanentAiError("AUTH")]
)
async def test_si_la_ia_no_esta_disponible_avisa_y_pasa_a_una_persona(error: Exception) -> None:
    handler, log, _, _ = _handler(_state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(error))

    await handler(_job())

    assert _enqueued(log)[0].payload == {"body": TEMPLATE_AI_UNAVAILABLE}
    assert ("handoff", "TOOL_ERROR") in log
    assert ("processed", (1,)) in log


# --- Cuándo NO responde ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "paused"),
    [("HUMAN", None), ("CLOSED", None), ("BOT", datetime.now(UTC) + timedelta(hours=1))],
)
async def test_con_una_persona_a_cargo_el_bot_no_responde(mode: str, paused: datetime | None) -> None:
    model = ScriptedChatModel(_text("no debería salir"))
    handler, log, _, _ = _handler(
        _state(_msg("IN", "hola", 1, pending=True), mode=mode, paused=paused), model
    )

    await handler(_job())

    assert model.calls == [] and _enqueued(log) == []
    assert ("processed", (1,)) in log  # los atendió el equipo: el bot no los retoma después


async def test_una_pausa_vencida_no_frena_al_bot() -> None:
    state = _state(_msg("IN", "hola", 1, pending=True), paused=datetime.now(UTC) - timedelta(minutes=1))
    handler, log, _, _ = _handler(state, ScriptedChatModel(_text("Hola")))

    await handler(_job())

    assert len(_enqueued(log)) == 1


async def test_sin_mensajes_pendientes_no_hace_nada() -> None:
    """Un trabajo repetido, después de un turno ya confirmado."""
    model = ScriptedChatModel(_text("no"))
    handler, log, _, _ = _handler(_state(_msg("IN", "hola", 1)), model)

    await handler(_job())

    assert model.calls == [] and _enqueued(log) == [] and not [k for k, _ in log if k == "processed"]


async def test_una_baja_ya_respondida_no_despierta_al_agente() -> None:
    model = ScriptedChatModel(_text("no"))
    handler, log, _, _ = _handler(_state(_msg("IN", "BAJA", 1, pending=True)), model)

    await handler(_job())

    assert model.calls == [] and _enqueued(log) == []
    assert ("processed", (1,)) in log


async def test_una_conversacion_que_no_existe_para_el_negocio() -> None:
    model = ScriptedChatModel(_text("no"))
    handler, log, _, _ = _handler(None, model)

    await handler(_job())

    assert model.calls == [] and _enqueued(log) == []


# --- Idempotencia y errores -----------------------------------------------------------------------
async def test_si_la_respuesta_ya_estaba_en_el_outbox_no_la_repite_en_el_historial() -> None:
    handler, log, _, _ = _handler(
        _state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(_text("Hola")), duplicate=True
    )

    await handler(_job())

    assert len(_enqueued(log)) == 1
    assert not [k for k, _ in log if k == "bot_reply"]
    assert ("processed", (1,)) in log


async def test_con_la_ventana_cerrada_no_reintenta_para_siempre() -> None:
    handler, log, _, _ = _handler(
        _state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(_text("Hola")),
        error=BusinessRuleError(ErrorCode.WINDOW_CLOSED, "ventana"),
    )  # fmt: skip

    await handler(_job())  # no lanza: se marca atendido sin respuesta

    assert ("processed", (1,)) in log


@pytest.mark.parametrize(
    "job",
    [
        Job(id=1, kind=PROCESS_CONVERSATION, organization_id=None, conversation_key=None,
            payload={"conversation_id": str(uuid4())}),
        Job(id=1, kind=PROCESS_CONVERSATION, organization_id=ORG, conversation_key=None, payload={}),
        Job(id=1, kind=PROCESS_CONVERSATION, organization_id=ORG, conversation_key=None,
            payload={"conversation_id": "no-es-uuid"}),
    ],
)  # fmt: skip
async def test_un_trabajo_mal_formado_va_a_dead(job: Job) -> None:
    handler, _, _, _ = _handler(_state(), ScriptedChatModel(_text("no")))

    with pytest.raises(PermanentJobError):
        await handler(job)


async def test_un_rubro_sin_politica_va_a_dead() -> None:
    handler, _, _, _ = _handler(
        TurnState(conversation_id=CONVERSATION, customer_id=CUSTOMER, business_name="X",
                  vertical="DISTRIBUIDORA_MAYORISTA", mode="BOT", bot_paused_until=None, summary=None,
                  history=(_msg("IN", "hola", 1, pending=True),)),
        ScriptedChatModel(_text("no")),
    )  # fmt: skip

    with pytest.raises(PermanentJobError):
        await handler(_job())


# --- Métrica --------------------------------------------------------------------------------------
async def test_cada_turno_cuenta_su_resultado(metric: MetricValue) -> None:
    before = metric("agent_turns", outcome="replied")
    handler, _, _, _ = _handler(_state(_msg("IN", "hola", 1, pending=True)), ScriptedChatModel(_text("Hola")))

    await handler(_job())

    assert metric("agent_turns", outcome="replied") == before + 1


def test_el_mensaje_asistente_lleva_herramientas() -> None:
    """Sanidad del tipo que arma el orquestador entre rondas."""
    call = ToolCall(id="t1", name="search_catalog", arguments={})
    assert ChatMessage.assistant(None, (call,)).tool_calls == (call,)
