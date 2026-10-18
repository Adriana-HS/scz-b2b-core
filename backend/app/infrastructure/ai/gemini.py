"""Adaptador de chat de Gemini (generateContent). Capa anticorrupción del proveedor (#83).

**Solo para desarrollo** (ADR-0019): en el plan gratuito de Google AI Studio, Google usa lo enviado
para mejorar sus productos y una persona puede revisarlo. La configuración rechaza este proveedor
fuera de `local` y `test`: solo datos sintéticos. Producción usa Anthropic (ADR-0018).

POST https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent con la clave
en `x-goog-api-key` (nunca en la URL: las URLs quedan en logs y trazas). httpx directo, sin SDK,
igual que el adaptador de Anthropic; reintentos, circuit breaker, trazas y costo son decoradores.

* Herramientas con `parametersJsonSchema` (JSON Schema estándar), con los `$ref` expandidos.
* Firmas de razonamiento: la llamada a herramienta que trae `thoughtSignature` la devuelve tal cual
  en la ronda siguiente (sin ella Gemini 3 responde 400). Viaja en `ToolCall.signature`.
* Gemini responde STOP aunque pida herramientas: si hay `functionCall`, el motivo es TOOL_USE.
* Tokens: el prompt cacheado va aparte y el razonamiento se cobra como salida.
* Clasificación: red, timeout, 429 y 5xx -> TransientAiError (con la espera que pida el 429);
  400, 401/403, 404, 413 y el resto de 4xx -> PermanentAiError. Solo códigos estables.
Referencias: https://ai.google.dev/api/generate-content y
https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.domain.ai import (
    ChatMessage,
    ModelResponse,
    Role,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolSpec,
    validate_model_id,
)
from app.domain.errors import PermanentAiError, TransientAiError

PROVIDER = "gemini"
_GENERATED_ID = "gemini:"  # Gemini no siempre manda id: estos los inventa el adaptador y no se devuelven
_STOP_REASONS = {
    "STOP": StopReason.END_TURN,
    "MAX_TOKENS": StopReason.MAX_TOKENS,
    "SAFETY": StopReason.REFUSAL,
    "RECITATION": StopReason.REFUSAL,
    "BLOCKLIST": StopReason.REFUSAL,
    "PROHIBITED_CONTENT": StopReason.REFUSAL,
    "SPII": StopReason.REFUSAL,
}
_PERMANENT_CODES = {400: "BAD_REQUEST", 401: "AUTH", 403: "AUTH", 404: "MODEL_NOT_FOUND", 413: "TOO_LARGE"}
_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


def _inline_refs(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Expande los `#/$defs/...` que genera Pydantic para modelos anidados (sin recursión: los
    argumentos de las herramientas no la tienen)."""
    defs: Mapping[str, Any] = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, Mapping):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(defs[ref.removeprefix("#/$defs/")])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def _function_response(content: str, *, is_error: bool) -> dict[str, Any]:
    """`response` debe ser un objeto JSON: lo que no lo es va envuelto."""
    try:
        value: Any = json.loads(content)
    except ValueError:
        value = content
    if is_error:
        return {"error": value}
    return value if isinstance(value, dict) else {"result": value}


def _contents(history: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}
    contents: list[dict[str, Any]] = []
    for message in history:
        parts: list[dict[str, Any]] = []
        if message.text:
            parts.append({"text": message.text})
        for call in message.tool_calls:
            names[call.id] = call.name
            function: dict[str, Any] = {"name": call.name, "args": dict(call.arguments)}
            if not call.id.startswith(_GENERATED_ID):
                function["id"] = call.id
            part: dict[str, Any] = {"functionCall": function}
            if call.signature:
                part["thoughtSignature"] = call.signature
            parts.append(part)
        for result in message.tool_results:
            name = names.get(result.tool_call_id)
            if name is None:
                raise PermanentAiError("BAD_REQUEST")  # un resultado sin su pedido: error del agente
            response: dict[str, Any] = {
                "name": name, "response": _function_response(result.content, is_error=result.is_error),
            }  # fmt: skip
            if not result.tool_call_id.startswith(_GENERATED_ID):
                response["id"] = result.tool_call_id
            parts.append({"functionResponse": response})
        contents.append({"role": "model" if message.role is Role.ASSISTANT else "user", "parts": parts})
    return contents


def _retry_after(response: httpx.Response) -> float | None:
    """El 429 de Google trae la espera en el cuerpo (RetryInfo.retryDelay, p. ej. "13s")."""
    try:
        value = float(response.headers.get("retry-after", ""))
        return value if value >= 0 else None
    except ValueError:
        pass
    try:
        details = response.json()["error"]["details"]
        for detail in details:
            match = _DELAY.fullmatch(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


class GeminiChatModel:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 2048,  # incluye el razonamiento: con menos, corta respuestas
        thinking_level: str | None = "low",  # menos latencia para el presupuesto de 20 s del turno
        base_url: str = "https://generativelanguage.googleapis.com",
    ) -> None:
        if not api_key:
            raise ValueError("falta la clave de la API de IA (LLM_API_KEY)")
        self._client = client
        self._api_key = api_key
        self._model = validate_model_id(model)
        self._max_tokens = max_tokens
        self._thinking_level = thinking_level
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{self._model}:generateContent"

    @property
    def model(self) -> str:
        return self._model

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        config: dict[str, Any] = {"maxOutputTokens": self._max_tokens}
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _contents(history),
            "generationConfig": config,
        }
        if tools:
            declarations = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parametersJsonSchema": _inline_refs(tool.input_schema),
                }
                for tool in tools
            ]
            body["tools"] = [{"functionDeclarations": declarations}]
        headers = {"x-goog-api-key": self._api_key}
        try:
            response = await self._client.post(self._url, json=body, headers=headers, timeout=timeout_s)
        except httpx.TimeoutException:
            raise TransientAiError("TIMEOUT") from None  # `from None`: la excepción trae el request
        except httpx.HTTPError:
            raise TransientAiError("NETWORK") from None

        if response.status_code == 200:
            return self._parse(response)
        status = response.status_code
        if status == 429:
            raise TransientAiError("RATE_LIMITED", retry_after_s=_retry_after(response))
        if status >= 500:
            raise TransientAiError(f"HTTP_{status}", retry_after_s=_retry_after(response))
        raise PermanentAiError(_PERMANENT_CODES.get(status, f"HTTP_{status}"))

    def _parse(self, response: httpx.Response) -> ModelResponse:
        try:
            data = response.json()
            usage: Mapping[str, Any] = data.get("usageMetadata") or {}
            candidates: Sequence[Mapping[str, Any]] = data.get("candidates") or []
            if not isinstance(candidates, list):
                raise TypeError("candidates")
            parts: Sequence[Mapping[str, Any]] = []
            finish = "SAFETY" if data.get("promptFeedback", {}).get("blockReason") else "STOP"
            if candidates:
                parts = candidates[0].get("content", {}).get("parts") or []
                finish = str(candidates[0].get("finishReason", "STOP"))
            text = "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))
            calls = tuple(
                ToolCall(
                    id=str(
                        part["functionCall"].get("id")
                        or f"{_GENERATED_ID}{index}:{part['functionCall']['name']}"
                    ),
                    name=str(part["functionCall"]["name"]),
                    arguments=dict(part["functionCall"].get("args") or {}),
                    signature=part.get("thoughtSignature"),
                )
                for index, part in enumerate(p for p in parts if "functionCall" in p)
            )
            cached = int(usage.get("cachedContentTokenCount") or 0)
            stop = StopReason.TOOL_USE if calls else _STOP_REASONS.get(finish, StopReason.OTHER)
            return ModelResponse(
                text=text,
                tool_calls=calls,
                stop_reason=stop,
                usage=TokenUsage(
                    input_tokens=int(usage.get("promptTokenCount") or 0) - cached,
                    output_tokens=int(usage.get("candidatesTokenCount") or 0)
                    + int(usage.get("thoughtsTokenCount") or 0),
                    cache_read_tokens=cached,
                ),
                model=str(data.get("modelVersion") or self._model),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TransientAiError("BAD_RESPONSE") from None
