"""Reporte de una corrida y comparación con la anterior (#28: "reporte comparativo").

Métricas: Q-ACC-5 (casos que pasan, objetivo ≥ 95 %) y Q-ACC-6 (respuestas con cifras sin
herramienta que las respalde: objetivo 0; el grounding las bloquea, aquí se cuentan los intentos).
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_evals.scoring import CaseResult

TARGET_ACCURACY = 0.95


@dataclass(frozen=True, slots=True)
class Summary:
    suite: str
    model: str
    cases: int
    passed: int
    accuracy: float
    ungrounded: int  # turnos en que el modelo dijo una cifra inventada (Q-ACC-6)
    by_category: dict[str, list[int]]  # categoría -> [pasan, total]


def summarize(suite: str, model: str, results: Sequence[CaseResult]) -> Summary:
    by_category: dict[str, list[int]] = {}
    for result in results:
        counts = by_category.setdefault(result.category, [0, 0])
        counts[0] += int(result.passed)
        counts[1] += 1
    passed = sum(r.passed for r in results)
    outcomes = Counter(r.outcome for r in results)
    return Summary(
        suite=suite, model=model, cases=len(results), passed=passed,
        accuracy=round(passed / len(results), 4) if results else 0.0,
        ungrounded=outcomes["ungrounded"], by_category=dict(sorted(by_category.items())),
    )  # fmt: skip


def to_json(summary: Summary, results: Sequence[CaseResult]) -> dict[str, Any]:
    return {"summary": asdict(summary), "results": [asdict(r) for r in results]}


def compare(current: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """Markdown para el PR: métricas, y qué casos empezaron a fallar o a pasar."""
    now = current["summary"]
    lines = [
        f"## Evals del agente: {now['suite']} ({now['model']})",
        "",
        f"* **Q-ACC-5:** {now['passed']}/{now['cases']} = {now['accuracy']:.0%}"
        f" (objetivo ≥ {TARGET_ACCURACY:.0%})",
        f"* **Q-ACC-6:** {now['ungrounded']} respuestas con cifras sin herramienta (objetivo 0)",
    ]
    if previous is not None:
        before = previous["summary"]
        delta = now["accuracy"] - before["accuracy"]
        lines.append(f"* **Contra la corrida anterior ({before['model']}):** {before['accuracy']:.0%} → "
                     f"{now['accuracy']:.0%} ({delta:+.0%})")  # fmt: skip
        was = {r["case_id"]: r["passed"] for r in previous["results"]}
        broke = [
            r["case_id"] for r in current["results"] if was.get(r["case_id"]) is True and not r["passed"]
        ]
        fixed = [r["case_id"] for r in current["results"] if was.get(r["case_id"]) is False and r["passed"]]
        lines.append(f"* **Empezaron a fallar:** {', '.join(broke) or 'ninguno'}")
        lines.append(f"* **Empezaron a pasar:** {', '.join(fixed) or 'ninguno'}")
    lines += ["", "| Categoría | Pasan |", "| :--- | ---: |"]
    lines += [f"| {cat} | {ok}/{total} |" for cat, (ok, total) in now["by_category"].items()]
    failed = [r for r in current["results"] if not r["passed"]]
    if failed:
        lines += ["", "### Casos que fallan", ""]
        lines += [f"* `{r['case_id']}` ({r['outcome']}): {'; '.join(r['reasons'])}" for r in failed]
    return "\n".join(lines) + "\n"


def write(directory: Path, name: str, current: dict[str, Any]) -> tuple[Path, str]:
    """Guarda la corrida como `<suite>-latest.json` y devuelve el markdown comparado con la anterior."""
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / f"{name}-latest.json"
    previous = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
    markdown = compare(current, previous)
    latest.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / f"{name}-latest.md").write_text(markdown, encoding="utf-8")
    return latest, markdown
