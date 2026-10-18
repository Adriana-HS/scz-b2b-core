"""Puntaje de un caso: pasa solo si se cumple TODO lo esperado. Sin puntos parciales: Q-ACC-5 mide
la proporción de conversaciones en las que el bot hizo exactamente lo correcto."""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_evals.suite import Case, ToolExpectation


@dataclass(frozen=True, slots=True)
class ToolUse:
    name: str
    arguments: Mapping[str, Any]
    skus: tuple[tuple[str, int], ...] = ()  # create_quote: (código, cantidad), ya resueltos por el runner


@dataclass(frozen=True, slots=True)
class Observation:
    """Lo que hizo el bot en el turno."""

    tools: tuple[ToolUse, ...]
    outcome: str
    reply: str | None


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    category: str
    passed: bool
    outcome: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _plain(text: str) -> str:
    """Minúsculas y sin acentos, para comparar como lo haría una persona."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _sku(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", code).upper()


def _arguments_ok(expected: ToolExpectation, use: ToolUse) -> list[str]:
    problems: list[str] = []
    if expected.query_any:
        query = _plain(str(use.arguments.get("query", "")))
        if not any(_plain(word) in query for word in expected.query_any):
            problems.append(f"{use.name}: la consulta no menciona {list(expected.query_any)}")
    if expected.items:
        got = sorted((_sku(code), qty) for code, qty in use.skus)
        want = sorted((_sku(item.sku), item.quantity) for item in expected.items)
        if got != want:
            problems.append(f"create_quote: ítems {got} en vez de {want}")
    if expected.reasons and use.arguments.get("reason") not in expected.reasons:
        problems.append(
            f"handoff_to_human: motivo {use.arguments.get('reason')} no está en {list(expected.reasons)}"
        )
    return problems


def _tools_ok(expected: Sequence[ToolExpectation], used: Sequence[ToolUse]) -> list[str]:
    """Las esperadas aparecen en ese orden (subsecuencia), cada una con sus argumentos."""
    problems: list[str] = []
    position = 0
    for want in expected:
        match = next((i for i in range(position, len(used)) if used[i].name == want.name), None)
        if match is None:
            problems.append(f"no usó {want.name}")
            continue
        problems += _arguments_ok(want, used[match])
        position = match + 1
    return problems


def score(case: Case, observation: Observation) -> CaseResult:
    expect = case.expect
    names = [use.name for use in observation.tools]
    problems = _tools_ok(expect.tools, observation.tools)
    if expect.no_tools and names:
        problems.append(f"no debía usar herramientas y usó {names}")
    problems += [f"usó {name}, que no debía" for name in expect.forbidden_tools if name in names]
    if observation.outcome not in expect.outcomes:
        problems.append(f"resultado {observation.outcome}, se esperaba {list(expect.outcomes)}")
    reply = _plain(observation.reply or "")
    if expect.reply_contains_any and not any(_plain(word) in reply for word in expect.reply_contains_any):
        problems.append(f"la respuesta no menciona {list(expect.reply_contains_any)}")
    problems += [
        f"la respuesta contiene {word!r}" for word in expect.reply_not_contains if _plain(word) in reply
    ]
    return CaseResult(case.id, case.category.value, not problems, observation.outcome, tuple(problems))
