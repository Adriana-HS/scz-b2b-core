"""Contrato de los puertos de IA (#22, principios-y-patrones §1: sustitución de Liskov).

La misma suite corre contra cada implementación de un puerto: el fake que usan las pruebas y
los adaptadores reales (con un doble de HTTP). Si un fake se aparta del contrato, las pruebas
que lo usan mienten; si un adaptador se aparta, el agente falla en producción.
"""

from collections.abc import Callable

import httpx
import pytest

from app.domain.ai import ChatMessage, ModelResponse, StopReason, TokenUsage, ToolCall, ToolSpec
from app.domain.errors import AiError, PermanentAiError, TransientAiError
from app.domain.ports import ChatModel, Embedder, SpeechToText, VisionExtractor
from app.infrastructure.ai.anthropic import AnthropicChatModel

from ..doubles.ai import (
    EMBEDDING_DIMENSIONS,
    FakeEmbedder,
    FakeSpeechToText,
    FakeVisionExtractor,
    ScriptedChatModel,
)

TOOL = ToolSpec(name="search_catalog", description="Busca", input_schema={"type": "object"})
HISTORY = (ChatMessage.user("¿tienen pastillas de freno?"),)

# Escenario -> (lo que devuelve el fake, lo que devuelve la API de Anthropic)
SCENARIOS: dict[str, tuple[ModelResponse | Exception, httpx.Response]] = {
    "texto": (
        ModelResponse("Sí, tenemos.", (), StopReason.END_TURN, TokenUsage(10, 5), "m-1"),
        httpx.Response(200, json={
            "model": "m-1", "content": [{"type": "text", "text": "Sí, tenemos."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }),
    ),
    "herramienta": (
        ModelResponse("", (ToolCall("t1", "search_catalog", {"query": "freno"}),), StopReason.TOOL_USE,
                      TokenUsage(10, 5), "m-1"),
        httpx.Response(200, json={
            "model": "m-1", "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search_catalog", "input": {"query": "freno"}}
            ],
        }),
    ),
    "sobrecarga": (TransientAiError("OVERLOADED"), httpx.Response(529, json={"type": "error"})),
    "request_invalido": (PermanentAiError("BAD_REQUEST"), httpx.Response(400, json={"type": "error"})),
}  # fmt: skip

type ChatFactory = Callable[[str], ChatModel]


def _fake(scenario: str) -> ChatModel:
    return ScriptedChatModel(SCENARIOS[scenario][0])


def _anthropic(scenario: str) -> ChatModel:
    response = SCENARIOS[scenario][1]
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    return AnthropicChatModel(client, api_key="sk-ant-prueba", model="claude-sonnet-5-5")


@pytest.fixture(params=[_fake, _anthropic], ids=["fake", "anthropic"])
def chat_model(request: pytest.FixtureRequest) -> ChatFactory:
    factory: ChatFactory = request.param
    return factory


async def _turn(model: ChatModel) -> ModelResponse:
    return await model.run_turn("sistema", HISTORY, (TOOL,), timeout_s=5)


async def test_una_respuesta_de_texto_cumple_el_contrato(chat_model: ChatFactory) -> None:
    response = await _turn(chat_model("texto"))

    assert response.text == "Sí, tenemos." and response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.input_tokens >= 0 and response.usage.output_tokens >= 0
    assert response.model


async def test_un_pedido_de_herramienta_cumple_el_contrato(chat_model: ChatFactory) -> None:
    response = await _turn(chat_model("herramienta"))

    assert response.stop_reason is StopReason.TOOL_USE
    [call] = response.tool_calls
    assert call.name == TOOL.name and isinstance(call.arguments, dict) and call.id


@pytest.mark.parametrize(
    ("scenario", "error"), [("sobrecarga", TransientAiError), ("request_invalido", PermanentAiError)]
)
async def test_los_errores_son_del_dominio_y_con_codigo(
    chat_model: ChatFactory, scenario: str, error: type[AiError]
) -> None:
    """El agente decide por tipo (reintentar o degradar), nunca por el texto del error."""
    with pytest.raises(error) as raised:
        await _turn(chat_model(scenario))

    assert raised.value.code.isupper()


# --- Embedder, SpeechToText y VisionExtractor: hoy solo el fake (sus adaptadores, en #23, #30 y #31) --
EMBEDDERS: list[Callable[[], Embedder]] = [FakeEmbedder]


@pytest.mark.parametrize("make", EMBEDDERS)
async def test_embedder_un_vector_por_texto_con_la_dimension_de_la_bd(make: Callable[[], Embedder]) -> None:
    vectors = await make().embed(["pastilla de freno", "amortiguador"])

    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIMENSIONS for v in vectors)


@pytest.mark.parametrize("make", EMBEDDERS)
async def test_embedder_es_determinista(make: Callable[[], Embedder]) -> None:
    embedder = make()

    assert (await embedder.embed(["freno"]))[0] == (await embedder.embed(["freno"]))[0]
    assert (await embedder.embed(["freno"]))[0] != (await embedder.embed(["filtro"]))[0]


SPEECH: list[Callable[[], SpeechToText]] = [FakeSpeechToText]
VISION: list[Callable[[], VisionExtractor]] = [FakeVisionExtractor]


@pytest.mark.parametrize("make", SPEECH)
async def test_speech_to_text_devuelve_texto_y_rechaza_audio_vacio(make: Callable[[], SpeechToText]) -> None:
    stt = make()

    assert isinstance(await stt.transcribe(b"OggS...", "audio/ogg"), str)
    with pytest.raises(ValueError):
        await stt.transcribe(b"", "audio/ogg")


@pytest.mark.parametrize("make", VISION)
async def test_vision_devuelve_una_extraccion_con_confianza(make: Callable[[], VisionExtractor]) -> None:
    extraction = await make().extract_receipt(b"\x89PNG...", timeout_s=5)

    assert extraction.confidence is not None and 0 <= extraction.confidence <= 1
