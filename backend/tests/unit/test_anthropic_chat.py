"""Adaptador de Anthropic (Messages API) con un doble de HTTP: formato, respuestas y errores (#22)."""

import json
from typing import Any

import httpx
import pytest

from app.domain.ai import ChatMessage, StopReason, ToolCall, ToolResult, ToolSpec
from app.domain.errors import PermanentAiError, TransientAiError
from app.infrastructure.ai.anthropic import AnthropicChatModel

API_KEY = "sk-ant-clave-de-prueba-0000"
MODEL = "claude-sonnet-5-5"
TOOL = ToolSpec(
    name="search_catalog",
    description="Busca repuestos en el catálogo del negocio",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


def _text_reply(text: str = "Tenemos pastillas de freno.", **usage: int) -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 30, **usage},
    }


def _tool_reply() -> dict[str, Any]:
    return {
        "id": "msg_02",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [
            {"type": "text", "text": "Voy a buscar."},
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "search_catalog",
                "input": {"query": "freno hilux"},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 200, "output_tokens": 40, "cache_read_input_tokens": 1500,
                  "cache_creation_input_tokens": 0},
    }  # fmt: skip


class Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def body(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[-1].content)
        return data


def _model(handler: Recorder, **kwargs: Any) -> AnthropicChatModel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicChatModel(client, api_key=API_KEY, model=MODEL, **kwargs)


async def _turn(model: AnthropicChatModel, history: tuple[ChatMessage, ...] | None = None) -> Any:
    return await model.run_turn(
        "Eres el asistente de una importadora.",
        history or (ChatMessage.user("¿tienen pastillas de freno?"),),
        (TOOL,),
        timeout_s=10,
    )


# --- Request ------------------------------------------------------------------------------------
async def test_el_request_va_a_messages_con_clave_version_y_modelo_fijado() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler))

    request = handler.requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert handler.body["model"] == MODEL  # el identificador exacto, nunca un alias
    assert handler.body["max_tokens"] == 1024


async def test_el_prompt_de_sistema_va_cacheado_y_las_herramientas_con_su_esquema() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler))

    assert handler.body["system"] == [
        {
            "type": "text",
            "text": "Eres el asistente de una importadora.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert handler.body["tools"] == [
        {"name": TOOL.name, "description": TOOL.description, "input_schema": dict(TOOL.input_schema)}
    ]


async def test_el_historial_traduce_herramientas_pedidas_y_sus_resultados() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))
    call = ToolCall(id="toolu_01", name="search_catalog", arguments={"query": "freno"})
    history = (
        ChatMessage.user("¿tienen pastillas?"),
        ChatMessage.assistant("Voy a buscar.", (call,)),
        ChatMessage.from_tool_results(ToolResult(tool_call_id="toolu_01", content='{"items": 2}')),
    )

    await _turn(_model(handler), history)

    assert handler.body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "¿tienen pastillas?"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Voy a buscar."},
                {"type": "tool_use", "id": "toolu_01", "name": "search_catalog", "input": {"query": "freno"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "content": '{"items": 2}',
                    "is_error": False,
                }
            ],
        },
    ]


# --- Respuesta ------------------------------------------------------------------------------------
async def test_una_respuesta_de_texto() -> None:
    response = await _turn(_model(Recorder(httpx.Response(200, json=_text_reply()))))

    assert response.text == "Tenemos pastillas de freno."
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)
    assert response.model == MODEL


async def test_una_respuesta_que_pide_una_herramienta() -> None:
    response = await _turn(_model(Recorder(httpx.Response(200, json=_tool_reply()))))

    assert response.stop_reason is StopReason.TOOL_USE
    assert response.tool_calls == (
        ToolCall(id="toolu_01", name="search_catalog", arguments={"query": "freno hilux"}),
    )
    assert response.usage.cache_read_tokens == 1500


@pytest.mark.parametrize(
    ("stop", "expected"),
    [("max_tokens", StopReason.MAX_TOKENS), ("refusal", StopReason.REFUSAL),
     ("stop_sequence", StopReason.END_TURN), ("algo_nuevo", StopReason.OTHER)],
)  # fmt: skip
async def test_los_motivos_de_corte_se_traducen(stop: str, expected: StopReason) -> None:
    reply = {**_text_reply(), "stop_reason": stop}

    response = await _turn(_model(Recorder(httpx.Response(200, json=reply))))

    assert response.stop_reason is expected


@pytest.mark.parametrize(
    "body", [b"no es json", json.dumps({"content": "texto"}).encode(), json.dumps({"usage": {}}).encode()]
)
async def test_una_respuesta_rota_es_transitoria(body: bytes) -> None:
    with pytest.raises(TransientAiError, match="BAD_RESPONSE"):
        await _turn(_model(Recorder(httpx.Response(200, content=body))))


# --- Errores --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code"),
    [(429, "RATE_LIMITED"), (500, "HTTP_500"), (503, "HTTP_503"), (529, "OVERLOADED")],
)
async def test_errores_que_vale_la_pena_reintentar(status: int, code: str) -> None:
    with pytest.raises(TransientAiError, match=code):
        await _turn(_model(Recorder(httpx.Response(status, json={"type": "error"}))))


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "BAD_REQUEST"), (401, "AUTH"), (403, "AUTH"), (404, "MODEL_NOT_FOUND"), (413, "TOO_LARGE"),
     (422, "HTTP_422")],
)  # fmt: skip
async def test_errores_que_no_se_arreglan_reintentando(status: int, code: str) -> None:
    with pytest.raises(PermanentAiError, match=code):
        await _turn(_model(Recorder(httpx.Response(status, json={"type": "error"}))))


async def test_el_retry_after_de_un_429_se_respeta() -> None:
    handler = Recorder(httpx.Response(429, headers={"retry-after": "7"}, json={"type": "error"}))

    with pytest.raises(TransientAiError) as error:
        await _turn(_model(handler))

    assert error.value.retry_after_s == 7.0


@pytest.mark.parametrize(
    ("exc", "code"), [(httpx.ReadTimeout("t"), "TIMEOUT"), (httpx.ConnectError("c"), "NETWORK")]
)
async def test_la_red_y_el_timeout_son_transitorios(exc: Exception, code: str) -> None:
    with pytest.raises(TransientAiError, match=code):
        await _turn(_model(Recorder(exc)))


async def test_la_clave_y_el_contenido_nunca_aparecen_en_un_error() -> None:
    detail = {"type": "error", "error": {"message": f"clave {API_KEY} inválida para ¿tienen pastillas?"}}

    with pytest.raises(PermanentAiError) as error:
        await _turn(_model(Recorder(httpx.Response(401, json=detail))))

    text = f"{error.value!s} {error.value!r} {error.value.__cause__!r} {error.value.__context__!r}"
    assert API_KEY not in text and "pastillas" not in text


def test_sin_clave_o_con_un_alias_no_se_construye() -> None:
    client = httpx.AsyncClient()
    with pytest.raises(ValueError, match="clave"):
        AnthropicChatModel(client, api_key="", model=MODEL)
    with pytest.raises(ValueError, match="modelo"):
        AnthropicChatModel(client, api_key=API_KEY, model="claude-3-5-sonnet-latest")
