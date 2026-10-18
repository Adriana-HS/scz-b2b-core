"""Las alertas de ops/alerts/ son código: nombres de métricas reales, runbook existente y prueba.

La sintaxis PromQL y el disparo de cada alerta los prueba promtool (job `alerts` de la CI).
Aquí se verifica lo que promtool no sabe: que cada métrica exista en la app y cada runbook
en el repositorio, y que ninguna alerta quede sin su prueba de disparo.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.telemetry import METRIC_NAMES

REPO = Path(__file__).resolve().parents[3]
ALERTS = REPO / "ops" / "alerts"
_EXPORT_SUFFIXES = ("_total", "_bucket", "_sum", "_count")  # OTLP -> Prometheus
_SELECTOR = re.compile(r"\b([a-z_][a-z0-9_]*)\s*[{\[]")  # nombre seguido de {etiquetas} o [rango]
_BARE_SELECTOR = re.compile(r"\(\s*([a-z_][a-z0-9_]*)\s*\)")  # sum(jobs_queued)
_GROUPING = re.compile(r"\b(?:by|without)\s*\([^)]*\)")


def _rules() -> list[dict[str, Any]]:
    data = yaml.safe_load((ALERTS / "slo-rules.yaml").read_text(encoding="utf-8"))
    return [rule for group in data["groups"] for rule in group["rules"]]


def _metric(selector: str) -> str:
    for suffix in _EXPORT_SUFFIXES:
        if selector.endswith(suffix) and selector.removesuffix(suffix) in METRIC_NAMES:
            return selector.removesuffix(suffix)
    return selector


def _metrics_in(expr: str) -> set[str]:
    expr = _GROUPING.sub(" ", expr)  # `by (le)` y `by (kind)` son etiquetas, no métricas
    names = set(_SELECTOR.findall(expr)) | set(_BARE_SELECTOR.findall(expr))
    return {_metric(name) for name in names}


RULES = _rules()


@pytest.mark.parametrize("rule", RULES, ids=[r["alert"] for r in RULES])
def test_cada_alerta_usa_metricas_que_la_app_emite(rule: dict[str, Any]) -> None:
    used = _metrics_in(rule["expr"])

    assert used, "la expresión no usa ninguna métrica"
    assert used <= METRIC_NAMES, f"métricas inexistentes: {used - METRIC_NAMES}"


@pytest.mark.parametrize("rule", RULES, ids=[r["alert"] for r in RULES])
def test_cada_alerta_tiene_severidad_slo_y_runbook(rule: dict[str, Any]) -> None:
    assert rule["labels"]["severity"] in {"page", "ticket"}
    assert re.fullmatch(r"Q-[A-Z]+(-\d+)?", rule["labels"]["slo"])
    assert rule["annotations"]["summary"]
    runbook = REPO / rule["annotations"]["runbook"]
    assert runbook.is_file(), f"no existe {runbook}"
    assert "## Diagnóstico" in runbook.read_text(encoding="utf-8")


def test_los_nombres_de_alerta_son_unicos() -> None:
    names = [r["alert"] for r in RULES]
    assert len(names) == len(set(names))


def test_cada_alerta_tiene_un_caso_que_dispara_y_uno_que_no() -> None:
    tests = yaml.safe_load((ALERTS / "slo-rules.test.yaml").read_text(encoding="utf-8"))
    fires: set[str] = set()
    silent: set[str] = set()
    for case in tests["tests"]:
        for check in case.get("alert_rule_test", []):
            (fires if check["exp_alerts"] else silent).add(check["alertname"])
    names = {r["alert"] for r in RULES}

    assert names <= fires, f"sin caso que dispare: {names - fires}"
    assert names <= silent, f"sin caso que no dispare: {names - silent}"


def test_las_alertas_del_system_design_estan_todas() -> None:
    """§10.2: p95 > 8 s, cualquier DEAD, cola > 50, TC vencido, firmas > 1 %. El costo de IA
    llega con las cuotas (#25) y queda anotado en el archivo de reglas."""
    names = {r["alert"] for r in RULES}
    required = {
        "ReplyLatencyP95High",
        "JobDead",
        "QueueBacklog",
        "FxRateStale",
        "WebhookSignatureFailuresHigh",
    }

    assert required <= names
    assert "#25" in (ALERTS / "slo-rules.yaml").read_text(encoding="utf-8")
