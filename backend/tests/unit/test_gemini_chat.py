"""Adaptador de Gemini (generateContent) con un doble de HTTP: formato, firmas y errores (#83).

Solo para desarrollo (ADR-0019): en el plan gratuito Google puede usar y revisar lo enviado.
"""

import json
from typing import Any

import httpx
import pytest

from app.domain.ai import ChatMessage, StopReason, ToolCall, ToolResult, ToolSpec
from app.domain.errors import PermanentAiError, TransientAiError
from app.infrastructure.ai.gemini import GeminiChatModel

API_KEY = "AIza-clave-de-prueba-0000"
MODEL = "gemini-3.8-flash"
TOOL = ToolSpec(
    name="search_catalog",
    description="Busca repuestos en el catálogo del negocio",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                  "additionalProperties": False},
)  # fmt: skip


def _reply(*parts: dict[str, Any], finish: str = "STOP", **usage: int) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30, **usage},
        "modelVersion": MODEL,
    }


def _text_reply(text: str = "Tenemos pastillas de freno.") -> dict[str, Any]:
    return _reply({"text": text})


def _tool_reply() -> dict[str, Any]:
    return _reply(
        {"text": "Voy a buscar."},
        {"functionCall": {"name": "search_catalog", "args": {"query": "freno hilux"}},
         "thoughtSignature": "firma-A"},
        {"functionCall": {"name": "get_item_details", "args": {"item_id": "x"}}},
    )  # fmt: skip


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


def _model(handler: Recorder, **kwargs: Any) -> GeminiChatModel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiChatModel(client, api_key=API_KEY, model=MODEL, **kwargs)


async def _turn(
    model: GeminiChatModel,
    history: tuple[ChatMessage, ...] | None = None,
    tools: tuple[ToolSpec, ...] = (TOOL,),
) -> Any:
    return await model.run_turn(
        "Eres el asistente de una importadora.",
        history or (ChatMessage.user("¿tienen pastillas de freno?"),),
        tools,
        timeout_s=10,
    )


# --- Request ------------------------------------------------------------------------------------
async def test_el_request_va_a_generate_content_con_la_clave_en_el_encabezado() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler))

    request = handler.requests[0]
    assert str(request.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    )
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)  # nunca en la URL: las URLs quedan en logs y trazas
    assert handler.body["generationConfig"] == {
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingLevel": "low"},
    }


async def test_sin_nivel_de_razonamiento_no_se_manda_thinking_config() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler, thinking_level=None))

    assert handler.body["generationConfig"] == {"maxOutputTokens": 2048}


async def test_el_sistema_y_las_herramientas_con_su_json_schema() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler))

    assert handler.body["systemInstruction"] == {"parts": [{"text": "Eres el asistente de una importadora."}]}
    assert handler.body["tools"] == [
        {"functionDeclarations": [
            {"name": TOOL.name, "description": TOOL.description,
             "parametersJsonSchema": dict(TOOL.input_schema)}
        ]}
    ]  # fmt: skip


async def test_sin_herramientas_no_se_manda_tools() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler), tools=())

    assert "tools" not in handler.body


async def test_los_esquemas_con_referencias_se_expanden() -> None:
    """Pydantic pone los modelos anidados en $defs (la cotización): se mandan en línea."""
    line = {"type": "object", "properties": {"quantity": {"type": "integer"}}}
    tool = ToolSpec(
        name="create_quote",
        description="Cotiza",
        input_schema={
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/QuoteLine"}}},
            "$defs": {"QuoteLine": line},
        },
    )
    handler = Recorder(httpx.Response(200, json=_text_reply()))

    await _turn(_model(handler), tools=(tool,))

    [declaration] = handler.body["tools"][0]["functionDeclarations"]
    assert declaration["parametersJsonSchema"] == {
        "type": "object", "properties": {"items": {"type": "array", "items": line}},
    }  # fmt: skip


async def test_el_historial_devuelve_herramientas_firmas_y_resultados() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))
    first = ToolCall(id="gemini:0:search_catalog", name="search_catalog", arguments={"query": "freno"},
                     signature="firma-A")  # fmt: skip
    second = ToolCall(id="fc-77", name="get_item_details", arguments={"item_id": "x"})
    history = (
        ChatMessage.user("¿tienen pastillas?"),
        ChatMessage.assistant("Voy a buscar.", (first, second)),
        ChatMessage.from_tool_results(
            ToolResult(tool_call_id="gemini:0:search_catalog", content='{"items": 2}'),
            ToolResult(tool_call_id="fc-77", content="no es json", is_error=True),
        ),
    )

    await _turn(_model(handler), history)

    assert handler.body["contents"] == [
        {"role": "user", "parts": [{"text": "¿tienen pastillas?"}]},
        {"role": "model", "parts": [
            {"text": "Voy a buscar."},
            {"functionCall": {"name": "search_catalog", "args": {"query": "freno"}},
             "thoughtSignature": "firma-A"},
            {"functionCall": {"name": "get_item_details", "args": {"item_id": "x"}, "id": "fc-77"}},
        ]},
        {"role": "user", "parts": [
            {"functionResponse": {"name": "search_catalog", "response": {"items": 2}}},
            {"functionResponse": {"name": "get_item_details", "response": {"error": "no es json"},
                                  "id": "fc-77"}},
        ]},
    ]  # fmt: skip


async def test_un_resultado_que_no_es_un_objeto_va_envuelto() -> None:
    handler = Recorder(httpx.Response(200, json=_text_reply()))
    call = ToolCall(id="fc-1", name="search_catalog", arguments={})
    history = (
        ChatMessage.user("hola"),
        ChatMessage.assistant(None, (call,)),
        ChatMessage.from_tool_results(ToolResult(tool_call_id="fc-1", content="[1, 2]")),
    )

    await _turn(_model(handler), history)

    part = handler.body["contents"][2]["parts"][0]
    assert part["functionResponse"]["response"] == {"result": [1, 2]}


async def test_un_resultado_de_una_herramienta_desconocida_no_se_manda() -> None:
    """Un resultado sin su pedido en el historial es un error del agente: falla, no se adivina."""
    history = (
        ChatMessage.user("hola"),
        ChatMessage.from_tool_results(ToolResult(tool_call_id="perdido", content="{}")),
    )

    with pytest.raises(PermanentAiError, match="BAD_REQUEST"):
        await _turn(_model(Recorder(httpx.Response(200, json=_text_reply()))), history)


# --- Respuesta ------------------------------------------------------------------------------------
async def test_una_respuesta_de_texto_sin_los_pensamientos() -> None:
    reply = _reply({"text": "pensando...", "thought": True}, {"text": "Tenemos pastillas."},
                   cachedContentTokenCount=100, thoughtsTokenCount=50)  # fmt: skip

    response = await _turn(_model(Recorder(httpx.Response(200, json=reply))))

    assert response.text == "Tenemos pastillas."
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    # el prompt cacheado se cuenta aparte y el razonamiento se cobra como salida
    usage = response.usage
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (20, 100, 80)
    assert response.model == MODEL


async def test_una_respuesta_que_pide_herramientas_conserva_la_firma() -> None:
    response = await _turn(_model(Recorder(httpx.Response(200, json=_tool_reply()))))

    assert response.stop_reason is StopReason.TOOL_USE  # Gemini dice STOP aunque pida herramientas
    assert response.text == "Voy a buscar."
    assert response.tool_calls == (
        ToolCall(id="gemini:0:search_catalog", name="search_catalog", arguments={"query": "freno hilux"},
                 signature="firma-A"),
        ToolCall(id="gemini:1:get_item_details", name="get_item_details", arguments={"item_id": "x"}),
    )  # fmt: skip


async def test_el_id_que_manda_gemini_se_respeta() -> None:
    reply = _reply({"functionCall": {"id": "fc-9", "name": "search_catalog", "args": {}}})

    response = await _turn(_model(Recorder(httpx.Response(200, json=reply))))

    assert response.tool_calls[0].id == "fc-9"
    assert response.tool_calls[0].arguments == {}


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("MAX_TOKENS", StopReason.MAX_TOKENS), ("SAFETY", StopReason.REFUSAL),
     ("PROHIBITED_CONTENT", StopReason.REFUSAL), ("RECITATION", StopReason.REFUSAL),
     ("MALFORMED_FUNCTION_CALL", StopReason.OTHER), ("ALGO_NUEVO", StopReason.OTHER)],
)  # fmt: skip
async def test_los_motivos_de_corte_se_traducen(finish: str, expected: StopReason) -> None:
    reply = _reply({"text": "x"}, finish=finish)

    response = await _turn(_model(Recorder(httpx.Response(200, json=reply))))

    assert response.stop_reason is expected


async def test_un_prompt_bloqueado_es_un_rechazo_sin_texto() -> None:
    reply = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 12}}

    response = await _turn(_model(Recorder(httpx.Response(200, json=reply))))

    assert (response.text, response.tool_calls, response.stop_reason) == ("", (), StopReason.REFUSAL)
    assert response.usage.input_tokens == 12


@pytest.mark.parametrize(
    "body",
    [
        b"no es json",
        json.dumps({"candidates": "x"}).encode(),
        json.dumps({"candidates": [{"content": 1}]}).encode(),
    ],
)
async def test_una_respuesta_rota_es_transitoria(body: bytes) -> None:
    with pytest.raises(TransientAiError, match="BAD_RESPONSE"):
        await _turn(_model(Recorder(httpx.Response(200, content=body))))


# --- Errores --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code"), [(429, "RATE_LIMITED"), (500, "HTTP_500"), (503, "HTTP_503"), (504, "HTTP_504")]
)
async def test_errores_que_vale_la_pena_reintentar(status: int, code: str) -> None:
    with pytest.raises(TransientAiError, match=code):
        await _turn(_model(Recorder(httpx.Response(status, json={"error": {"code": status}}))))


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "BAD_REQUEST"), (401, "AUTH"), (403, "AUTH"), (404, "MODEL_NOT_FOUND"), (413, "TOO_LARGE"),
     (422, "HTTP_422")],
)  # fmt: skip
async def test_errores_que_no_se_arreglan_reintentando(status: int, code: str) -> None:
    with pytest.raises(PermanentAiError, match=code):
        await _turn(_model(Recorder(httpx.Response(status, json={"error": {"code": status}}))))


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(429, headers={"retry-after": "7"}, json={}), 7.0),
        (httpx.Response(429, json={"error": {"details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"}]}}), 13.0),
        (httpx.Response(429, json={"error": {"details": [{"retryDelay": "pronto"}]}}), None),
        (httpx.Response(429, content=b"no es json"), None),
    ],
)  # fmt: skip
async def test_la_espera_que_pide_un_429_se_respeta(response: httpx.Response, expected: float | None) -> None:
    with pytest.raises(TransientAiError) as error:
        await _turn(_model(Recorder(response)))

    assert error.value.retry_after_s == expected


@pytest.mark.parametrize(
    ("exc", "code"), [(httpx.ReadTimeout("t"), "TIMEOUT"), (httpx.ConnectError("c"), "NETWORK")]
)
async def test_la_red_y_el_timeout_son_transitorios(exc: Exception, code: str) -> None:
    with pytest.raises(TransientAiError, match=code):
        await _turn(_model(Recorder(exc)))


async def test_la_clave_y_el_contenido_nunca_aparecen_en_un_error() -> None:
    detail = {"error": {"code": 400, "message": f"API key {API_KEY} inválida para ¿tienen pastillas?"}}

    with pytest.raises(PermanentAiError) as error:
        await _turn(_model(Recorder(httpx.Response(400, json=detail))))

    text = f"{error.value!s} {error.value!r} {error.value.__cause__!r} {error.value.__context__!r}"
    assert API_KEY not in text and "pastillas" not in text


def test_sin_clave_o_con_un_alias_no_se_construye() -> None:
    client = httpx.AsyncClient()
    assert GeminiChatModel(client, api_key=API_KEY, model=MODEL).model == MODEL
    with pytest.raises(ValueError, match="clave"):
        GeminiChatModel(client, api_key="", model=MODEL)
    with pytest.raises(ValueError, match="modelo"):
        GeminiChatModel(client, api_key=API_KEY, model="gemini-flash-latest")
