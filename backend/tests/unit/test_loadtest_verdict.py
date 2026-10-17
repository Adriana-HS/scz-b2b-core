"""Veredicto de las pruebas de carga y caos (#21, Q-PERF-1, Q-REL-1).

El veredicto se decide con dos fuentes: lo que vio k6 (latencias, aceptados, abandonados) y
lo que quedó en la BD (mensajes, eventos, trabajos). Una corrida "verde" que perdió mensajes,
o un caos que no llegó a producir fallas, deben salir en rojo.
"""

from typing import Any

import pytest

from loadtest.verdict import DbCounts, K6Result, Mode, evaluate, render_markdown

HEALTHY: dict[str, dict[str, float]] = {
    "messages_sent": {"count": 9000, "rate": 50.0},
    "messages_acked": {"count": 9000, "rate": 50.0},
    "duplicates_sent": {"count": 450, "rate": 2.5},
    "first_attempt_failed": {"rate": 0.0, "passes": 0, "fails": 9000},
    "first_attempt_ack_ms": {"med": 12.0, "p(95)": 40.0, "p(99)": 90.0, "max": 300.0},
    "time_to_ack_ms": {"med": 12.0, "p(95)": 40.0, "p(99)": 90.0, "max": 300.0},
}


def _result(**overrides: dict[str, float]) -> K6Result:
    metrics = {**HEALTHY, **overrides}
    summary: dict[str, Any] = {"metrics": {name: {"values": values} for name, values in metrics.items()}}
    return K6Result.from_summary(summary)


def _db(
    persisted: int = 9000, *, events: int | None = None, distinct: int | None = None, no_job: int = 0
) -> DbCounts:
    return DbCounts(
        messages=persisted,
        distinct_messages=persisted if distinct is None else distinct,
        events=persisted if events is None else events,
        conversations_without_job=no_job,
    )


def test_lee_el_resumen_de_k6() -> None:
    result = _result()

    assert (result.sent, result.acked, result.given_up, result.dropped) == (9000, 9000, 0, 0)
    assert result.p99_ms == 90.0 and result.p50_ms == 12.0
    assert result.first_attempt_error_rate == 0.0
    assert result.duplicates_sent == 450


def test_una_corrida_sana_pasa() -> None:
    verdict = evaluate(Mode.LOAD, _result(), _db())

    assert verdict.passed, verdict.failures


def test_p99_sobre_el_slo_falla() -> None:
    verdict = evaluate(Mode.LOAD, _result(first_attempt_ack_ms={"med": 20.0, "p(99)": 251.0}), _db())

    assert not verdict.passed
    assert any("p99" in f for f in verdict.failures)


def test_un_mensaje_aceptado_que_no_quedo_en_la_bd_es_perdida() -> None:
    verdict = evaluate(Mode.LOAD, _result(), _db(persisted=8999))

    assert not verdict.passed
    assert any("perdido" in f for f in verdict.failures)


def test_un_evento_sin_su_mensaje_rompe_la_atomicidad() -> None:
    verdict = evaluate(Mode.LOAD, _result(), _db(events=9001))

    assert not verdict.passed
    assert any("atómic" in f for f in verdict.failures)


def test_filas_repetidas_de_un_mismo_mensaje_fallan() -> None:
    verdict = evaluate(Mode.LOAD, _result(), _db(persisted=9001, distinct=9000, events=9001))

    assert not verdict.passed
    assert any("duplicad" in f for f in verdict.failures)


def test_mas_mensajes_que_los_enviados_es_un_duplicado() -> None:
    """Los reenvíos de Meta (duplicates_sent) nunca deben crear filas nuevas."""
    verdict = evaluate(Mode.LOAD, _result(), _db(persisted=9000 + 450))

    assert not verdict.passed
    assert any("duplicad" in f for f in verdict.failures)


def test_una_conversacion_sin_trabajo_falla() -> None:
    verdict = evaluate(Mode.LOAD, _result(), _db(no_job=1))

    assert not verdict.passed
    assert any("trabajo" in f for f in verdict.failures)


def test_en_carga_los_errores_y_las_iteraciones_perdidas_fallan() -> None:
    noisy = _result(first_attempt_failed={"rate": 0.01}, dropped_iterations={"count": 900, "rate": 5.0})

    verdict = evaluate(Mode.LOAD, noisy, _db())

    assert not verdict.passed
    assert any("errores" in f for f in verdict.failures)
    assert any("sostener" in f for f in verdict.failures)


@pytest.mark.parametrize("mode", [Mode.CHAOS_DB, Mode.CHAOS_API])
def test_en_caos_el_p99_no_cuenta_pero_ningun_mensaje_puede_abandonarse(mode: Mode) -> None:
    chaos = {
        "first_attempt_failed": {"rate": 0.08},
        "first_attempt_ack_ms": {"med": 15.0, "p(99)": 9000.0},
    }

    ok = evaluate(mode, _result(**chaos), _db())
    assert ok.passed, ok.failures

    gave_up = _result(**chaos, messages_given_up={"count": 3}, messages_acked={"count": 8997})
    verdict = evaluate(mode, gave_up, _db(persisted=8997))
    assert not verdict.passed
    assert any("abandon" in f for f in verdict.failures)


@pytest.mark.parametrize("mode", [Mode.CHAOS_DB, Mode.CHAOS_API])
def test_un_caos_que_no_produjo_fallas_no_prueba_nada(mode: Mode) -> None:
    """Si la falla inyectada no llegó a notarse, la corrida no demuestra la recuperación."""
    verdict = evaluate(mode, _result(), _db())

    assert not verdict.passed
    assert any("no se produjo" in f for f in verdict.failures)


def test_un_resumen_sin_metricas_no_pasa() -> None:
    verdict = evaluate(Mode.LOAD, K6Result.from_summary({"metrics": {}}), _db(persisted=0))

    assert not verdict.passed


def test_el_reporte_muestra_cifras_y_veredicto() -> None:
    result = _result()
    verdict = evaluate(Mode.LOAD, result, _db())

    report = render_markdown(Mode.LOAD, result, _db(), verdict, target_rps=50)

    assert "p99" in report and "90" in report and "9000" in report
    assert "PASA" in report
    assert "Ida y vuelta" not in report  # sin medición, no se inventa


def test_el_reporte_explica_el_p99_con_la_latencia_de_la_bd() -> None:
    result = _result()
    report = render_markdown(
        Mode.LOAD, result, _db(), evaluate(Mode.LOAD, result, _db()), target_rps=50, db_round_trip_ms=0.42
    )

    assert "Ida y vuelta a la BD" in report and "0.4 ms" in report
