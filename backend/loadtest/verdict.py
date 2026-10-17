"""Veredicto de una corrida de carga o de caos: lo que vio k6 + lo que quedó en la BD.

Q-PERF-1: p99 del ack del webhook ≤ 250 ms (solo en carga, sin fallas inyectadas).
Q-REL-1: ningún mensaje aceptado (200) se pierde. Se verifica en la BD, no en k6: cada mensaje
aceptado quedó persistido una sola vez, con su evento (atomicidad) y su conversación con un
trabajo en la cola. En caos, además, ningún mensaje puede abandonarse (Meta reintenta) y la
falla inyectada tiene que haberse notado: un caos que no produjo errores no prueba nada.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

P99_SLO_MS = 250.0  # Q-PERF-1
MAX_ERROR_RATE = 0.001  # en carga: a lo sumo 1 de cada 1000 primeros intentos falla
MAX_DROPPED_RATIO = 0.01  # en carga: k6 tiene que sostener la tasa pedida


class Mode(StrEnum):
    LOAD = "load"  # 50 rps sostenidos: mide el SLO
    CHAOS_DB = "chaos-db"  # Postgres se reinicia en medio de la carga
    CHAOS_API = "chaos-api"  # el proceso api muere (SIGKILL) y vuelve


def _values(summary: Mapping[str, Any], name: str) -> Mapping[str, float]:
    metric = summary.get("metrics", {}).get(name)
    return metric.get("values", {}) if isinstance(metric, Mapping) else {}


@dataclass(frozen=True)
class K6Result:
    sent: int  # mensajes únicos enviados (sin contar reintentos ni reenvíos)
    acked: int  # de esos, los que terminaron con 200
    given_up: int  # los que nunca llegaron a 200 dentro del plazo de reintentos
    duplicates_sent: int  # reenvíos del mismo mensaje ya aceptado (como hace Meta)
    dropped: int  # iteraciones que k6 no pudo lanzar: la tasa no se sostuvo
    first_attempt_error_rate: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None  # del primer intento aceptado: lo que mide el SLO
    max_time_to_ack_ms: float | None  # con reintentos: cuánto tardó en recuperarse

    @classmethod
    def from_summary(cls, summary: Mapping[str, Any]) -> "K6Result":
        """Lee el JSON de handleSummary de loadtest/k6/webhook.js."""
        first = _values(summary, "first_attempt_ack_ms")
        return cls(
            sent=int(_values(summary, "messages_sent").get("count", 0)),
            acked=int(_values(summary, "messages_acked").get("count", 0)),
            given_up=int(_values(summary, "messages_given_up").get("count", 0)),
            duplicates_sent=int(_values(summary, "duplicates_sent").get("count", 0)),
            dropped=int(_values(summary, "dropped_iterations").get("count", 0)),
            first_attempt_error_rate=float(_values(summary, "first_attempt_failed").get("rate", 0.0)),
            p50_ms=first.get("med"),
            p95_ms=first.get("p(95)"),
            p99_ms=first.get("p(99)"),
            max_time_to_ack_ms=_values(summary, "time_to_ack_ms").get("max"),
        )


@dataclass(frozen=True)
class DbCounts:
    """Lo que quedó en la BD de los mensajes de esta corrida (wa_message_id con su prefijo)."""

    messages: int
    distinct_messages: int
    events: int
    conversations_without_job: int


@dataclass(frozen=True)
class Verdict:
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate(mode: Mode, k6: K6Result, db: DbCounts) -> Verdict:
    failures: list[str] = []

    # --- Invariantes de la BD (todos los modos): Q-REL-1 -----------------------------------
    if k6.sent == 0:
        failures.append("k6 no envió mensajes: la corrida no midió nada")
    if db.distinct_messages < k6.acked:
        failures.append(
            f"{k6.acked - db.distinct_messages} mensajes aceptados con 200 no están en la BD: perdidos"
        )
    if db.messages != db.distinct_messages or db.distinct_messages > k6.sent:
        failures.append(f"mensajes duplicados: {db.messages} filas para {k6.sent} mensajes únicos enviados")
    if db.events != db.messages:
        failures.append(f"{db.events} eventos para {db.messages} mensajes: el registro no fue atómico")
    if db.conversations_without_job:
        failures.append(
            f"{db.conversations_without_job} conversaciones con mensajes y sin trabajo en la cola"
        )

    # --- Carga: el SLO --------------------------------------------------------------------------
    if mode is Mode.LOAD:
        if k6.p99_ms is None:
            failures.append("sin latencias del primer intento: no se puede medir el p99")
        elif k6.p99_ms > P99_SLO_MS:
            failures.append(f"p99 del ack {k6.p99_ms:.0f} ms > {P99_SLO_MS:.0f} ms (Q-PERF-1)")
        if k6.first_attempt_error_rate > MAX_ERROR_RATE:
            failures.append(
                f"errores en el {k6.first_attempt_error_rate:.2%} de los primeros intentos "
                f"(máximo {MAX_ERROR_RATE:.1%})"
            )
        if k6.dropped > MAX_DROPPED_RATIO * max(k6.sent + k6.dropped, 1):
            failures.append(f"k6 no pudo sostener la tasa pedida: {k6.dropped} iteraciones sin lanzar")
        if k6.given_up:
            failures.append(f"{k6.given_up} mensajes abandonados sin un 200")

    # --- Caos: recuperación sin pérdidas ---------------------------------------------------------
    else:
        if k6.first_attempt_error_rate == 0:
            failures.append(
                "el caos no se produjo: ningún primer intento falló, así que no se probó la recuperación"
            )
        if k6.given_up:
            failures.append(f"{k6.given_up} mensajes abandonados: el servicio no se recuperó a tiempo")

    return Verdict(failures)


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} ms"


def render_markdown(
    mode: Mode,
    k6: K6Result,
    db: DbCounts,
    verdict: Verdict,
    *,
    target_rps: float,
    db_round_trip_ms: float | None = None,
) -> str:
    """Resumen para la consola y para el resumen del job de GitHub Actions."""
    status = "✅ PASA" if verdict.passed else "❌ FALLA"
    lines = [
        f"### Webhook · `{mode.value}` · {status}",
        "",
        "| Medida | Valor |",
        "| :--- | ---: |",
        f"| Tasa pedida | {target_rps:g} rps |",
        f"| Mensajes únicos enviados / aceptados / abandonados | {k6.sent} / {k6.acked} / {k6.given_up} |",
        f"| Reenvíos del mismo mensaje (como Meta) | {k6.duplicates_sent} |",
        f"| Iteraciones que k6 no pudo lanzar | {k6.dropped} |",
        f"| Primeros intentos con error | {k6.first_attempt_error_rate:.2%} |",
        f"| Ack del primer intento p50 / p95 / p99 | {_ms(k6.p50_ms)} / {_ms(k6.p95_ms)}"
        f" / {_ms(k6.p99_ms)} |",
        f"| Máximo hasta el 200, con reintentos | {_ms(k6.max_time_to_ack_ms)} |",
        f"| En la BD: mensajes / distintos / eventos | {db.messages} / {db.distinct_messages}"
        f" / {db.events} |",
        f"| Conversaciones sin trabajo en la cola | {db.conversations_without_job} |",
    ]
    if db_round_trip_ms is not None:
        # El webhook hace ~9 viajes a la BD por request: con viajes lentos el p99 no se puede cumplir
        lines.append(f"| Ida y vuelta a la BD (mediana de `SELECT 1`) | {db_round_trip_ms:.1f} ms |")
    if verdict.failures:
        lines += ["", "**Fallas:**", *(f"- {failure}" for failure in verdict.failures)]
    return "\n".join(lines) + "\n"
