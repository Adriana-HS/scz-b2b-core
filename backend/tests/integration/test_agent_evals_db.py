"""Runner de las evals (#28) contra Postgres real, con un modelo guionado (sin IA): prueba que el
historial se carga como en producción, que las herramientas reales corren y que el puntaje ve lo
que pasó. Necesita los seeds de #27."""

from collections.abc import Callable
from uuid import UUID

import asyncpg
import pytest

from agent_evals.runner import EvalRunner
from agent_evals.suite import Suite, load_suite
from app.domain.ai import ModelResponse, StopReason, TokenUsage, ToolCall
from app.domain.ports import ChatModel
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from ..doubles.ai import ScriptedChatModel

pytestmark = pytest.mark.integration


def _tools(*calls: tuple[str, dict[str, object]]) -> ModelResponse:
    calls_ = tuple(ToolCall(f"t{i}", name, args) for i, (name, args) in enumerate(calls))
    return ModelResponse("", calls_, StopReason.TOOL_USE, TokenUsage(10, 5), "guion")


def _text(text: str) -> ModelResponse:
    return ModelResponse(text, (), StopReason.END_TURN, TokenUsage(10, 5), "guion")


def _runner(pool: asyncpg.Pool, model: ScriptedChatModel) -> EvalRunner:
    def factory(organization_id: UUID, conversation_id: UUID) -> ChatModel:
        return model

    chat_model: Callable[[UUID, UUID], ChatModel] = factory
    return EvalRunner(unit_of_work=unit_of_work_factory(pool), resolver=PostgresChannelResolver(pool),
                      chat_model=chat_model)  # fmt: skip


async def _suite(admin: asyncpg.Connection) -> Suite:
    suite = load_suite("importadora")
    if not await admin.fetchval("SELECT count(*) FROM public.catalog_items WHERE organization_id = $1",
                                suite.organization_id):  # fmt: skip
        pytest.skip("sin los seeds de #27 (supabase db reset los carga)")
    return suite


async def test_una_cotizacion_por_codigo_pasa_con_las_herramientas_reales(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    suite = await _suite(admin)
    case = next(c for c in suite.cases if c.id == "imp-14")
    model = ScriptedChatModel(
        _tools(("create_quote", {"items": [{"sku": "FRE-001", "quantity": 2}]})),
        _text("Listo, le envío su cotización por este chat."),
    )

    [result] = await _runner(pool, model).run(suite, [case])

    assert result.passed, result.reasons
    assert result.outcome == "replied"


async def test_el_historial_se_carga_y_solo_el_ultimo_mensaje_queda_pendiente(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    suite = await _suite(admin)
    case = next(c for c in suite.cases if c.id == "imp-15")  # historial: cliente, bot; ahora "dame 3"
    model = ScriptedChatModel(_text("¿Me confirma la cantidad?"))

    observation = await _runner(pool, model).observe(suite, case)

    [(_, messages)] = [(call[0], call[1]) for call in model.calls]
    texts = [m.text for m in messages]
    assert texts == ["precio del filtro de aire", case.history[1].text, "dame 3, haceme la cotizacion"]
    assert observation.tools == () and observation.reply == "¿Me confirma la cantidad?"


async def test_un_turno_que_no_cumple_falla_con_su_motivo(
    pool: asyncpg.Pool, admin: asyncpg.Connection
) -> None:
    suite = await _suite(admin)
    case = next(c for c in suite.cases if c.id == "imp-03")  # debía buscar pastillas
    model = ScriptedChatModel(_text("¡Hola! ¿En qué le ayudo?"))

    [result] = await _runner(pool, model).run(suite, [case])

    assert not result.passed and "no usó search_catalog" in result.reasons
