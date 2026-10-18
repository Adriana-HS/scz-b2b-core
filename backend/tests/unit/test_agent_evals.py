"""Evals golden (#28) sin IA: la suite es válida, el puntaje es estricto y el reporte compara.

Esto corre en la CI (modo offline). La corrida con un modelo de verdad es a mano (SCZ_RUN_EVALS=1).
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_evals.report import compare, summarize, to_json, write
from agent_evals.scoring import CaseResult, Observation, ToolUse, score
from agent_evals.suite import Case, Category, Suite, load_suite
from app.verticals import VERTICAL_POLICIES

SEEDS = Path(__file__).resolve().parents[3] / "supabase" / "seed"
SUITES = {"importadora": "seed_importadora.sql", "clinica": "seed_clinica.sql"}


# --- La suite ------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SUITES))
def test_cada_rubro_tiene_al_menos_30_conversaciones_de_todas_las_categorias(name: str) -> None:
    suite = load_suite(name)
    schedules = "check_availability" in VERTICAL_POLICIES[suite.vertical].enabled_tools
    required = set(Category) - (set() if schedules else {Category.AGENDA})

    assert len(suite.cases) >= 30
    per_category = Counter(case.category for case in suite.cases)
    assert set(per_category) == required, f"categorías: {set(per_category) ^ required}"
    assert min(per_category.values()) >= 2


@pytest.mark.parametrize("name", sorted(SUITES))
def test_los_codigos_esperados_existen_en_los_seeds(name: str) -> None:
    seed = (SEEDS / SUITES[name]).read_text(encoding="utf-8")
    suite = load_suite(name)

    skus = {item.sku for case in suite.cases for tool in case.expect.tools for item in tool.items}
    assert all(f"'{sku}'" in seed for sku in skus), skus - set(re.findall(r"'([A-Z]{3}-\d{3})'", seed))
    assert str(suite.organization_id) in seed and suite.phone_number_id in seed


@pytest.mark.parametrize("name", sorted(SUITES))
def test_solo_espera_herramientas_que_el_rubro_tiene(name: str) -> None:
    suite = load_suite(name)
    enabled = set(VERTICAL_POLICIES[suite.vertical].enabled_tools)

    expected = {tool.name for case in suite.cases for tool in case.expect.tools}
    assert expected <= enabled


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"expect": {"tools": [{"name": "borrar_todo"}]}}, "herramienta desconocida"),
        ({"expect": {"no_tools": True, "tools": [{"name": "search_catalog"}]}}, "no_tools"),
        ({"expect": {"outcomes": ["inventado"]}}, "desconocidos"),
        ({"precio": 1}, "Extra inputs"),
    ],
)
def test_un_caso_mal_escrito_falla_al_cargar(change: dict[str, Any], error: str) -> None:
    case = {"id": "imp-99", "category": "saludo", "message": "hola", "expect": {}, **change}

    with pytest.raises(ValidationError, match=error):
        Case.model_validate(case)


def test_ids_repetidos_no_se_aceptan() -> None:
    case = {"id": "imp-01", "category": "saludo", "message": "hola", "expect": {}}
    with pytest.raises(ValidationError, match="repetidos"):
        Suite.model_validate({"vertical": "X", "organization_id": "5eed0000-0000-4000-8000-00000000000a",
                              "phone_number_id": "P", "cases": [case, case]})  # fmt: skip


# --- El puntaje ----------------------------------------------------------------------------------
QUOTE_CASE = Case.model_validate({
    "id": "imp-90", "category": "cotizacion", "message": "coticeme 2 FRE-001",
    "expect": {"tools": [{"name": "search_catalog", "query_any": ["pastilla"]},
                         {"name": "create_quote", "items": [{"sku": "FRE-001", "quantity": 2}]}],
               "forbidden_tools": ["handoff_to_human"], "reply_contains_any": ["cotización"],
               "reply_not_contains": ["NIT"]},
})  # fmt: skip
GOOD = Observation(
    tools=(ToolUse("search_catalog", {"query": "Pastillas de freno"}), ToolUse("get_item_details", {}),
           ToolUse("create_quote", {}, (("fre-001", 2),))),
    outcome="replied", reply="Listo, le envío su COTIZACION.",
)  # fmt: skip


def _with(**changes: Any) -> Observation:
    from dataclasses import replace

    return replace(GOOD, **changes)


def test_un_turno_correcto_pasa_aunque_use_herramientas_de_mas_entre_medio() -> None:
    result = score(QUOTE_CASE, GOOD)

    assert result.passed, result.reasons


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (_with(tools=GOOD.tools[:2]), "no usó create_quote"),
        (_with(tools=(GOOD.tools[2], GOOD.tools[0])), "no usó create_quote"),  # orden invertido
        (_with(tools=(ToolUse("search_catalog", {"query": "frenos"}), GOOD.tools[2])), "no menciona"),
        (_with(tools=(GOOD.tools[0], ToolUse("create_quote", {}, (("FRE-001", 3),)))), "ítems"),
        (_with(tools=(*GOOD.tools, ToolUse("handoff_to_human", {"reason": "OTHER"}))), "que no debía"),
        (_with(outcome="ungrounded"), "resultado ungrounded"),
        (_with(reply="Listo."), "no menciona"),
        (_with(reply="Cotización lista, ¿me pasa su NIT?"), "contiene 'NIT'"),
    ],
)
def test_basta_un_detalle_mal_para_fallar(observation: Observation, reason: str) -> None:
    result = score(QUOTE_CASE, observation)

    assert not result.passed
    assert any(reason in r for r in result.reasons), result.reasons


def test_sin_herramientas_cuando_no_correspondia() -> None:
    case = Case.model_validate(
        {"id": "imp-91", "category": "saludo", "message": "hola", "expect": {"no_tools": True}}
    )

    assert score(case, Observation((), "replied", "Hola")).passed
    assert not score(
        case, Observation((ToolUse("search_catalog", {"query": "x"}),), "replied", "Hola")
    ).passed


def test_el_motivo_del_paso_a_una_persona() -> None:
    case = Case.model_validate({
        "id": "imp-92", "category": "handoff", "message": "reclamo",
        "expect": {"tools": [{"name": "handoff_to_human", "reasons": ["COMPLAINT"]}]},
    })  # fmt: skip

    assert score(
        case, Observation((ToolUse("handoff_to_human", {"reason": "COMPLAINT"}),), "replied", "")
    ).passed
    assert not score(
        case, Observation((ToolUse("handoff_to_human", {"reason": "OTHER"}),), "replied", "")
    ).passed


# --- El reporte ----------------------------------------------------------------------------------
RESULTS = [
    CaseResult("imp-01", "saludo", True, "replied"),
    CaseResult("imp-02", "catalogo", False, "ungrounded", ("resultado ungrounded",)),
    CaseResult("imp-03", "catalogo", True, "replied"),
]


def test_el_resumen_mide_q_acc_5_y_q_acc_6() -> None:
    summary = summarize("importadora", "gemini-3.8-flash", RESULTS)

    assert (summary.cases, summary.passed, summary.accuracy, summary.ungrounded) == (3, 2, 0.6667, 1)
    assert summary.by_category == {"catalogo": [1, 2], "saludo": [1, 1]}


def test_el_reporte_compara_con_la_corrida_anterior() -> None:
    before = to_json(summarize("importadora", "m-1", RESULTS), RESULTS)
    now_results = [RESULTS[0], CaseResult("imp-02", "catalogo", True, "replied"),
                   CaseResult("imp-03", "catalogo", False, "limit", ("resultado limit",))]  # fmt: skip
    now = to_json(summarize("importadora", "m-2", now_results), now_results)

    markdown = compare(now, before)

    assert "Empezaron a fallar:** imp-03" in markdown and "Empezaron a pasar:** imp-02" in markdown
    assert "67% → 67% (+0%)" in markdown
    assert "`imp-03` (limit): resultado limit" in markdown


def test_la_corrida_queda_guardada_como_la_ultima(tmp_path: Path) -> None:
    first = to_json(summarize("clinica", "m-1", RESULTS), RESULTS)
    write(tmp_path, "clinica", first)
    second = to_json(summarize("clinica", "m-2", RESULTS[:1]), RESULTS[:1])

    _, markdown = write(tmp_path, "clinica", second)

    assert (
        json.loads((tmp_path / "clinica-latest.json").read_text(encoding="utf-8"))["summary"]["model"]
        == "m-2"
    )
    assert "Contra la corrida anterior (m-1)" in markdown
    assert (tmp_path / "clinica-latest.md").read_text(encoding="utf-8") == markdown
