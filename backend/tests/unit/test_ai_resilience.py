"""Decoradores de los puertos de IA (#22, principios-y-patrones §3: Decorator).

Reintento con presupuesto, circuit breaker para degradar rápido (§10.3: si el LLM cae, plantilla
y modo HUMAN), trazas sin contenido y costo por tenant en private.llm_calls (Q-COST-1).
"""

import contextlib
import logging
from decimal import Decimal
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.application.ai import (
    CircuitBreaker,
    CircuitBreakerChatModel,
    CostMeteredChatModel,
    RetryingChatModel,
    TracedChatModel,
    resilient_chat_model,
)
from app.domain.ai import AiPurpose, ChatMessage, ModelPricing, TokenUsage
from app.domain.errors import AiUnavailableError, PermanentAiError, TransientAiError

from ..conftest import MetricValue
from ..doubles.ai import RecordingLlmCalls, ScriptedChatModel, text_response

ORG = uuid4()
PHONE = "59170000001"
HISTORY = (ChatMessage.user(f"mi número es {PHONE}, ¿tienen frenos?"),)
PRICING = ModelPricing(Decimal("3"), Decimal("15"), Decimal("0.30"), Decimal("3.75"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSleep:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock.now += delay


async def _turn(model: object) -> object:
    return await model.run_turn("sistema", HISTORY, (), timeout_s=10)  # type: ignore[attr-defined]


# --- Reintento --------------------------------------------------------------------------------
def _retrying(inner: ScriptedChatModel, **kwargs: object) -> tuple[RetryingChatModel, FakeSleep]:
    clock = FakeClock()
    sleep = FakeSleep(clock)
    model = RetryingChatModel(inner, sleep=sleep, clock=clock, rng=lambda: 1.0, **kwargs)  # type: ignore[arg-type]
    return model, sleep


async def test_un_error_transitorio_se_reintenta_con_backoff() -> None:
    inner = ScriptedChatModel(TransientAiError("HTTP_529"), TransientAiError("HTTP_529"), text_response())
    model, sleep = _retrying(inner)

    response = await _turn(model)

    assert response == text_response()
    assert len(inner.calls) == 3
    assert sleep.delays == [0.5, 1.0]  # exponencial (con rng=1, el máximo del jitter)


async def test_un_error_permanente_no_se_reintenta() -> None:
    inner = ScriptedChatModel(PermanentAiError("BAD_REQUEST"), text_response())
    model, sleep = _retrying(inner)

    with pytest.raises(PermanentAiError):
        await _turn(model)

    assert len(inner.calls) == 1 and sleep.delays == []


async def test_se_rinde_al_agotar_los_intentos() -> None:
    inner = ScriptedChatModel(TransientAiError("HTTP_500"))
    model, _ = _retrying(inner, attempts=3)

    with pytest.raises(TransientAiError, match="HTTP_500"):
        await _turn(model)

    assert len(inner.calls) == 3


async def test_respeta_el_retry_after_del_proveedor() -> None:
    inner = ScriptedChatModel(TransientAiError("RATE_LIMITED", retry_after_s=3.0), text_response())
    model, sleep = _retrying(inner)

    await _turn(model)

    assert sleep.delays == [3.0]


async def test_no_reintenta_si_la_espera_supera_el_presupuesto() -> None:
    """El cliente espera la respuesta: más vale degradar a tiempo que responder tarde."""
    inner = ScriptedChatModel(TransientAiError("RATE_LIMITED", retry_after_s=60.0), text_response())
    model, sleep = _retrying(inner, budget_s=20.0)

    with pytest.raises(TransientAiError):
        await _turn(model)

    assert sleep.delays == [] and len(inner.calls) == 1


async def test_el_jitter_reparte_las_esperas() -> None:
    inner = ScriptedChatModel(TransientAiError("HTTP_500"), text_response())
    clock = FakeClock()
    sleep = FakeSleep(clock)
    model = RetryingChatModel(inner, sleep=sleep, clock=clock, rng=lambda: 0.25)

    await _turn(model)

    assert sleep.delays == [0.125]  # full jitter: rng x base


# --- Circuit breaker -----------------------------------------------------------------------------
def _breaker(clock: FakeClock, threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=threshold, reset_after_s=30, clock=clock)


async def test_el_circuito_se_abre_tras_fallas_seguidas_y_no_llama_al_proveedor() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    inner = ScriptedChatModel(TransientAiError("HTTP_529"))
    model = CircuitBreakerChatModel(inner, breaker)

    for _ in range(3):
        with pytest.raises(TransientAiError):
            await _turn(model)
    with pytest.raises(AiUnavailableError, match="CIRCUIT_OPEN"):
        await _turn(model)

    assert breaker.state == "open"
    assert len(inner.calls) == 3  # la cuarta ni siquiera salió: el agente degrada al instante


async def test_tras_la_espera_deja_pasar_una_prueba_y_se_cierra_si_sale_bien() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    inner = ScriptedChatModel(TransientAiError("HTTP_529"), text_response())
    model = CircuitBreakerChatModel(inner, breaker)
    with pytest.raises(TransientAiError):
        await _turn(model)

    clock.now = 31
    states = [breaker.state]
    assert await _turn(model) == text_response()
    states.append(breaker.state)
    assert states == ["half_open", "closed"]


async def test_si_la_prueba_falla_vuelve_a_abrirse() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    model = CircuitBreakerChatModel(ScriptedChatModel(TransientAiError("HTTP_529")), breaker)
    with pytest.raises(TransientAiError):
        await _turn(model)

    clock.now = 31
    with pytest.raises(TransientAiError):
        await _turn(model)

    assert breaker.state == "open"
    clock.now = 45
    with pytest.raises(AiUnavailableError):
        await _turn(model)


async def test_en_media_apertura_pasa_una_sola_prueba_a_la_vez() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    breaker.on_failure(TransientAiError("HTTP_529"))
    clock.now = 31

    breaker.before_call()  # la prueba
    with pytest.raises(AiUnavailableError):
        breaker.before_call()  # otra llamada concurrente no pasa mientras la prueba sigue


async def test_un_error_permanente_no_abre_el_circuito() -> None:
    """Un request mal armado es un bug nuestro, no una caída del proveedor."""
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    model = CircuitBreakerChatModel(ScriptedChatModel(PermanentAiError("BAD_REQUEST")), breaker)

    with pytest.raises(PermanentAiError):
        await _turn(model)

    assert breaker.state == "closed"


async def test_un_exito_reinicia_la_cuenta_de_fallas() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=2)
    inner = ScriptedChatModel(TransientAiError("HTTP_500"), text_response(), TransientAiError("HTTP_500"))
    model = CircuitBreakerChatModel(inner, breaker)

    for _ in range(3):
        with contextlib.suppress(TransientAiError):
            await _turn(model)

    assert breaker.state == "closed"  # 1 falla, éxito, 1 falla: nunca 2 seguidas


# --- Trazas -----------------------------------------------------------------------------------------
async def test_la_traza_lleva_modelo_y_tokens_pero_nunca_el_contenido(spans: InMemorySpanExporter) -> None:
    model = TracedChatModel(
        ScriptedChatModel(text_response(f"te llamo al {PHONE}")),
        provider="anthropic",
        model="claude-sonnet-5-5",
        purpose=AiPurpose.CHAT,
    )

    await _turn(model)

    [span] = [s for s in spans.get_finished_spans() if s.name == "chat claude-sonnet-5-5"]
    attributes = dict(span.attributes or {})
    assert span.kind == SpanKind.CLIENT
    assert attributes["gen_ai.system"] == "anthropic"
    assert attributes["gen_ai.request.model"] == "claude-sonnet-5-5"
    assert attributes["gen_ai.usage.input_tokens"] == 100
    assert attributes["gen_ai.usage.output_tokens"] == 20
    assert PHONE not in str(attributes) and "frenos" not in str(attributes)
    assert not span.events  # sin excepciones grabadas (podrían traer texto del cliente)


async def test_un_error_marca_el_span_con_su_codigo(spans: InMemorySpanExporter) -> None:
    model = TracedChatModel(
        ScriptedChatModel(TransientAiError("HTTP_529")),
        provider="anthropic",
        model="m-1",
        purpose=AiPurpose.CHAT,
    )

    with pytest.raises(TransientAiError):
        await _turn(model)

    [span] = [s for s in spans.get_finished_spans() if s.name == "chat m-1"]
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["error.type"] == "HTTP_529"
    assert not span.events


# --- Costo ------------------------------------------------------------------------------------------
def _metered(inner: ScriptedChatModel, recorder: RecordingLlmCalls, **kwargs: object) -> CostMeteredChatModel:
    return CostMeteredChatModel(
        inner,
        recorder=recorder,
        pricing=PRICING,
        provider="anthropic",
        model="claude-sonnet-5-5",
        purpose=AiPurpose.CHAT,
        organization_id=ORG,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_cada_llamada_queda_en_llm_calls_con_su_costo(metric: MetricValue) -> None:
    recorder = RecordingLlmCalls()
    conversation = uuid4()
    before = metric("llm_cost_usd", organization_id=str(ORG))

    await _turn(_metered(ScriptedChatModel(text_response()), recorder, conversation_id=conversation))

    [call] = recorder.calls
    assert call.organization_id == ORG and call.conversation_id == conversation
    assert (call.purpose, call.provider, call.model, call.outcome) == (
        AiPurpose.CHAT, "anthropic", "claude-sonnet-5-5", "OK",
    )  # fmt: skip
    assert call.usage == TokenUsage(input_tokens=100, output_tokens=20)
    assert call.cost_usd == Decimal("0.000600")  # 100*3 + 20*15 = 600 por millón
    assert call.latency_ms >= 0
    assert metric("llm_cost_usd", organization_id=str(ORG)) == pytest.approx(before + 0.0006)


async def test_una_llamada_fallida_queda_registrada_sin_costo() -> None:
    recorder = RecordingLlmCalls()

    with pytest.raises(TransientAiError):
        await _turn(_metered(ScriptedChatModel(TransientAiError("HTTP_529")), recorder))

    [call] = recorder.calls
    assert (call.outcome, call.cost_usd, call.usage) == ("HTTP_529", Decimal("0"), TokenUsage())


async def test_si_no_se_puede_registrar_el_costo_el_turno_sigue(caplog: pytest.LogCaptureFixture) -> None:
    """La llamada ya se hizo (y se pagó): perder la respuesta por la BD sería peor."""
    caplog.set_level(logging.ERROR)
    recorder = RecordingLlmCalls(error=ConnectionError(f"bd caída {PHONE}"))

    response = await _turn(_metered(ScriptedChatModel(text_response()), recorder))

    assert response == text_response()
    assert "ConnectionError" in caplog.text and PHONE not in caplog.text


# --- Composición ------------------------------------------------------------------------------------
async def test_la_composicion_reintenta_adentro_y_cuenta_una_sola_falla_afuera(
    spans: InMemorySpanExporter,
) -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=30, clock=clock)
    recorder = RecordingLlmCalls()
    adapter = ScriptedChatModel(TransientAiError("HTTP_529"))
    model = resilient_chat_model(
        adapter,
        breaker=breaker,
        recorder=recorder,
        pricing=PRICING,
        provider="anthropic",
        model="m-1",
        purpose=AiPurpose.CHAT,
        organization_id=ORG,
        sleep=FakeSleep(clock),
        clock=clock,
    )

    with pytest.raises(TransientAiError):
        await _turn(model)

    assert len(adapter.calls) == 3  # 3 intentos adentro
    assert breaker.state == "closed"  # pero para el circuito fue UNA falla
    assert [c.outcome for c in recorder.calls] == ["HTTP_529"]  # y un solo registro
    assert len([s for s in spans.get_finished_spans() if s.name == "chat m-1"]) == 1
