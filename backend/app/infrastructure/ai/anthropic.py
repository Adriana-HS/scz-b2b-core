"""Adaptador de chat de Anthropic (Messages API). Capa anticorrupción del proveedor (#22).

POST https://api.anthropic.com/v1/messages con `x-api-key` y `anthropic-version` fijos. Se usa
httpx directo (como el adaptador de Meta), sin el SDK: menos dependencias en la cadena de
suministro y un solo lugar donde se decide qué se reintenta. Reintentos, circuit breaker,
trazas y costo son decoradores (app/application/ai.py), no viven aquí.

* El modelo va fijado por su identificador exacto (validate_model_id): nada de `-latest`.
* El prompt de sistema se marca para caché (`cache_control: ephemeral`): en un agente con el
  mismo sistema en cada turno, la parte cacheada se cobra mucho menos (§11).
* Clasificación: red, timeout, 429, 5xx y 529 -> TransientAiError (con retry-after si viene);
  400, 401/403, 404, 413 y el resto de 4xx -> PermanentAiError.
* Los errores llevan solo un código estable: nunca la clave, el prompt ni el detalle del proveedor.
Referencia: https://docs.anthropic.com/en/api/messages
"""

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

PROVIDER = "anthropic"
API_VERSION = "2023-06-01"
_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}
_PERMANENT_CODES = {400: "BAD_REQUEST", 401: "AUTH", 403: "AUTH", 404: "MODEL_NOT_FOUND", 413: "TOO_LARGE"}


def _message(message: ChatMessage) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if message.text:
        content.append({"type": "text", "text": message.text})
    content += [
        {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.arguments)}
        for call in message.tool_calls
    ]
    content += [
        {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
        for r in message.tool_results
    ]
    return {"role": "assistant" if message.role is Role.ASSISTANT else "user", "content": content}


def _retry_after(response: httpx.Response) -> float | None:
    try:
        value = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class AnthropicChatModel:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 1024,
        base_url: str = "https://api.anthropic.com",
        cache_system: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("falta la clave de la API de IA (LLM_API_KEY)")
        self._client = client
        self._api_key = api_key
        self._model = validate_model_id(model)
        self._max_tokens = max_tokens
        self._url = f"{base_url.rstrip('/')}/v1/messages"
        self._cache_system = cache_system

    @property
    def model(self) -> str:
        return self._model

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        system_block: dict[str, Any] = {"type": "text", "text": system}
        if self._cache_system:
            system_block["cache_control"] = {"type": "ephemeral"}
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": [system_block],
            "messages": [_message(m) for m in history],
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
                for t in tools
            ]
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION}
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
        if status == 529:
            raise TransientAiError("OVERLOADED", retry_after_s=_retry_after(response))
        if status >= 500:
            raise TransientAiError(f"HTTP_{status}", retry_after_s=_retry_after(response))
        raise PermanentAiError(_PERMANENT_CODES.get(status, f"HTTP_{status}"))

    def _parse(self, response: httpx.Response) -> ModelResponse:
        try:
            data = response.json()
            blocks: Sequence[Mapping[str, Any]] = data["content"]
            usage: Mapping[str, Any] = data["usage"]
            text = "".join(b["text"] for b in blocks if b.get("type") == "text")
            calls = tuple(
                ToolCall(id=b["id"], name=b["name"], arguments=dict(b["input"]))
                for b in blocks
                if b.get("type") == "tool_use"
            )
            return ModelResponse(
                text=text,
                tool_calls=calls,
                stop_reason=_STOP_REASONS.get(str(data.get("stop_reason")), StopReason.OTHER),
                usage=TokenUsage(
                    input_tokens=int(usage.get("input_tokens", 0)),
                    output_tokens=int(usage.get("output_tokens", 0)),
                    cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                    cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                ),
                model=str(data.get("model") or self._model),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TransientAiError("BAD_RESPONSE") from None
