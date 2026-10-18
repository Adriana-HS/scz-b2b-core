"""Políticas sobre los puertos de IA: reintento, circuit breaker, trazas y costo (#22).

Son decoradores (principios-y-patrones §3): envuelven un ChatModel y exponen la misma
interfaz, así que el agente no sabe si hay uno, cuatro o ninguno. El orden lo fija
`resilient_chat_model`, de afuera hacia adentro:

    Traced -> CostMetered -> CircuitBreaker -> Retrying -> adaptador

* El reintento va adentro: para el circuito y para el costo, una llamada reintentada es UNA.
* El circuito, afuera del reintento: con el proveedor caído se corta sin esperar los backoffs,
  y el agente degrada a plantilla + modo HUMAN (§10.3).
* El costo registra en private.llm_calls cada llamada lógica, también las fallidas (costo 0).
* La traza nunca lleva el prompt ni la respuesta: ahí van mensajes de clientes.
"""

import asyncio
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from app.core import telemetry
from app.domain.ai import AiPurpose, ChatMessage, LlmCall, ModelPricing, ModelResponse, TokenUsage, ToolSpec
from app.domain.errors import AiError, AiUnavailableError, TransientAiError
from app.domain.ports import ChatModel, LlmCallRecorder, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext

logger = logging.getLogger("scz.ai")

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


# --- Reintento ------------------------------------------------------------------------------------
class RetryingChatModel:
    """Reintenta los TransientAiError con backoff exponencial y *full jitter*, respetando el
    retry-after del proveedor, dentro de un presupuesto total: el cliente está esperando, y más
    vale degradar a tiempo que responder tarde. Nunca reintenta un PermanentAiError."""

    def __init__(
        self,
        inner: ChatModel,
        *,
        attempts: int = 3,
        base_delay_s: float = 0.5,
        max_delay_s: float = 4.0,
        budget_s: float = 20.0,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._attempts = attempts
        self._base = base_delay_s
        self._max = max_delay_s
        self._budget = budget_s
        self._sleep = sleep
        self._clock = clock
        self._rng = rng

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        started = self._clock()
        for attempt in range(self._attempts):
            try:
                return await self._inner.run_turn(system, history, tools, timeout_s=timeout_s)
            except TransientAiError as error:
                if attempt == self._attempts - 1:
                    raise
                delay = min(self._max, self._base * 2**attempt) * self._rng()
                if error.retry_after_s is not None:
                    delay = max(delay, error.retry_after_s)
                if self._clock() - started + delay > self._budget:
                    raise
                await self._sleep(delay)
        raise AssertionError("inalcanzable")  # pragma: no cover


# --- Circuit breaker ---------------------------------------------------------------------------------
State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """Cerrado -> (N fallas transitorias seguidas) -> abierto -> (espera) -> media apertura:
    pasa UNA llamada de prueba; si sale bien se cierra, si falla vuelve a abrirse.

    Se comparte entre todas las llamadas al mismo proveedor del proceso (uno por proveedor)."""

    def __init__(
        self, *, failure_threshold: int = 5, reset_after_s: float = 30.0, clock: Clock = time.monotonic
    ):
        self._threshold = failure_threshold
        self._reset_after = reset_after_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state()

    def _state(self) -> State:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self._opened_at >= self._reset_after else "open"

    def before_call(self) -> None:
        with self._lock:
            state = self._state()
            if state == "open" or (state == "half_open" and self._trial_in_flight):
                raise AiUnavailableError("CIRCUIT_OPEN")
            if state == "half_open":
                self._trial_in_flight = True

    def on_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def on_failure(self, error: AiError) -> None:
        with self._lock:
            was_trial = self._trial_in_flight
            self._trial_in_flight = False
            if not isinstance(error, TransientAiError):
                return  # un request mal armado es un bug nuestro, no una caída del proveedor
            self._failures += 1
            if was_trial or self._failures >= self._threshold:
                self._opened_at = self._clock()


class CircuitBreakerChatModel:
    def __init__(self, inner: ChatModel, breaker: CircuitBreaker) -> None:
        self._inner = inner
        self._breaker = breaker

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        self._breaker.before_call()
        try:
            response = await self._inner.run_turn(system, history, tools, timeout_s=timeout_s)
        except AiError as error:
            self._breaker.on_failure(error)
            raise
        self._breaker.on_success()
        return response


# --- Trazas -------------------------------------------------------------------------------------------
class TracedChatModel:
    """Span CLIENT con las convenciones gen_ai de OpenTelemetry: modelo, tokens y motivo de corte.
    Sin contenido (prompt, respuesta, argumentos de herramientas) y sin excepciones grabadas."""

    def __init__(self, inner: ChatModel, *, provider: str, model: str, purpose: AiPurpose) -> None:
        self._inner = inner
        self._provider = provider
        self._model = model
        self._purpose = purpose

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        attributes = {
            "gen_ai.system": self._provider,
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": self._model,
            "scz.ai.purpose": self._purpose.value,
        }
        with telemetry.tracer.start_as_current_span(
            f"chat {self._model}", kind=SpanKind.CLIENT, attributes=attributes, record_exception=False,
            set_status_on_exception=False,
        ) as span:  # fmt: skip
            try:
                response = await self._inner.run_turn(system, history, tools, timeout_s=timeout_s)
            except AiError as error:
                span.set_status(StatusCode.ERROR)
                span.set_attribute("error.type", error.code)
                raise
            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            span.set_attribute("gen_ai.response.finish_reasons", (response.stop_reason.value,))
            return response


# --- Costo -------------------------------------------------------------------------------------------
class CostMeteredChatModel:
    """Cada llamada lógica deja su fila en private.llm_calls (Q-COST-1) y suma a `llm_cost_usd`.

    Se construye por turno: lleva el tenant y la conversación de ese turno. Si la BD no puede
    registrar el costo, el turno sigue (la llamada ya se hizo y se pagó) y queda un log de error."""

    def __init__(
        self,
        inner: ChatModel,
        *,
        recorder: LlmCallRecorder,
        pricing: ModelPricing,
        provider: str,
        model: str,
        purpose: AiPurpose,
        organization_id: UUID,
        conversation_id: UUID | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._pricing = pricing
        self._provider = provider
        self._model = model
        self._purpose = purpose
        self._organization_id = organization_id
        self._conversation_id = conversation_id
        self._clock = clock

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        started = self._clock()
        try:
            response = await self._inner.run_turn(system, history, tools, timeout_s=timeout_s)
        except AiError as error:
            await self._record(TokenUsage(), Decimal("0"), 0, error.code, started)
            raise
        cost = self._pricing.cost(response.usage)
        telemetry.llm_cost_usd.add(
            float(cost),
            telemetry.attributes(
                purpose=self._purpose.value, model=self._model, organization_id=self._organization_id
            ),
        )
        await self._record(response.usage, cost, len(response.tool_calls), "OK", started)
        return response

    async def _record(
        self, usage: TokenUsage, cost: Decimal, tool_calls: int, outcome: str, started: float
    ) -> None:
        span = trace.get_current_span().get_span_context()
        call = LlmCall(
            organization_id=self._organization_id,
            conversation_id=self._conversation_id,
            purpose=self._purpose,
            provider=self._provider,
            model=self._model,
            usage=usage,
            cost_usd=cost,
            latency_ms=max(0, round((self._clock() - started) * 1000)),
            tool_calls=tool_calls,
            outcome=outcome,
            trace_id=format(span.trace_id, "032x") if span.is_valid else None,
        )
        try:
            await self._recorder.record(call)
        except Exception as exc:
            # Solo el tipo: el mensaje de un error de BD puede traer datos
            logger.error("ia: no se pudo registrar el costo de una llamada (%s)", type(exc).__name__)


class RecordLlmCalls:
    """LlmCallRecorder sobre la BD: una UnitOfWork corta en el tenant de la llamada, como SYSTEM."""

    def __init__(self, unit_of_work: UnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def record(self, call: LlmCall) -> None:
        async with self._unit_of_work(TenantContext(call.organization_id, Actor.SYSTEM)) as uow:
            await uow.llm_calls.add(call)


def resilient_chat_model(
    adapter: ChatModel,
    *,
    breaker: CircuitBreaker,
    recorder: LlmCallRecorder,
    pricing: ModelPricing,
    provider: str,
    model: str,
    purpose: AiPurpose,
    organization_id: UUID,
    conversation_id: UUID | None = None,
    attempts: int = 3,
    budget_s: float = 20.0,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> ChatModel:
    """El ChatModel que usa el agente (fase 2, #24/#25), con las políticas en el orden correcto."""
    retrying = RetryingChatModel(adapter, attempts=attempts, budget_s=budget_s, sleep=sleep, clock=clock)
    guarded = CircuitBreakerChatModel(retrying, breaker)
    metered = CostMeteredChatModel(
        guarded,
        recorder=recorder,
        pricing=pricing,
        provider=provider,
        model=model,
        purpose=purpose,
        organization_id=organization_id,
        conversation_id=conversation_id,
    )
    return TracedChatModel(metered, provider=provider, model=model, purpose=purpose)
