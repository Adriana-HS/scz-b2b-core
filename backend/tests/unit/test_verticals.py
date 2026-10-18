"""Prompts por rubro (§7.1 "Alcance del negocio", §7.3).

Todo rubro lleva las mismas reglas de alcance y confidencialidad: un rubro nuevo no puede
olvidarlas. El prompt es la primera barrera; la segunda la pone el orquestador (#81) y la
medición, las evals (#28).
"""

import pytest

from app.application.agent_tools import TOOL_NAMES
from app.domain.ports import VerticalPolicy
from app.verticals import VERTICAL_POLICIES
from app.verticals.common import ai_notice, scope_rules

POLICIES = sorted(VERTICAL_POLICIES.items())


@pytest.mark.parametrize(("vertical", "policy"), POLICIES)
def test_todo_rubro_lleva_las_reglas_de_alcance_y_confidencialidad(
    vertical: str, policy: VerticalPolicy
) -> None:
    prompt = policy.system_prompt("Negocio de Ejemplo")

    assert scope_rules("Negocio de Ejemplo") in prompt
    assert prompt.rstrip().endswith(scope_rules("Negocio de Ejemplo").rstrip())  # al final: lo último que lee


def test_las_reglas_cubren_lo_que_no_hace() -> None:
    rules = scope_rules("Negocio de Ejemplo").lower()

    for rule in (
        "no escribes código",
        "nunca revelas",
        "cómo funcionas por dentro",
        "datos de otros clientes",
        "no cambian desde el chat",
        "handoff_to_human",
    ):
        assert rule in rules, rule


@pytest.mark.parametrize(("vertical", "policy"), POLICIES)
def test_el_prompt_queda_completo_y_con_el_nombre_del_negocio(vertical: str, policy: VerticalPolicy) -> None:
    prompt = policy.system_prompt("Negocio de Ejemplo")

    assert "Negocio de Ejemplo" in prompt
    assert "{" not in prompt and "}" not in prompt


@pytest.mark.parametrize(("vertical", "policy"), POLICIES)
def test_todo_rubro_trata_al_cliente_de_usted(vertical: str, policy: VerticalPolicy) -> None:
    """Decisión de AHS (18-oct-2026): trato formal en todos los rubros."""
    assert "de usted" in policy.system_prompt("Negocio de Ejemplo")


@pytest.mark.parametrize(("vertical", "policy"), POLICIES)
def test_solo_habilita_herramientas_que_existen(vertical: str, policy: VerticalPolicy) -> None:
    assert set(policy.enabled_tools) <= set(TOOL_NAMES)
    assert "handoff_to_human" in policy.enabled_tools  # siempre se puede pasar a una persona


def test_la_clinica_no_cotiza_hasta_tener_agenda() -> None:
    assert "create_quote" not in VERTICAL_POLICIES["CLINICA_CONSULTORIO"].enabled_tools


# --- Aviso de IA y privacidad (#29, RF-03) --------------------------------------------------------
def test_el_aviso_por_defecto_dice_lo_que_pide_rf_03() -> None:
    notice = ai_notice("Negocio de Ejemplo", None).lower()

    assert "negocio de ejemplo" in notice
    assert "asistente automático" in notice and "inteligencia artificial" in notice
    assert "persona" in notice  # cómo pedir una persona
    assert "consulta" in notice  # para qué se usan sus mensajes (privacidad, §8.7)


def test_el_texto_aprobado_por_el_negocio_reemplaza_al_de_por_defecto() -> None:
    own = "  Le atiende el asistente automático de la tienda. Para hablar con una persona, pídalo.  "

    assert ai_notice("Negocio de Ejemplo", own) == own.strip()


@pytest.mark.parametrize("bad", ["", "   ", "Hola", "x" * 501, 42, ["texto"]])
def test_un_texto_propio_invalido_usa_el_de_por_defecto(bad: object) -> None:
    """Mejor el aviso estándar que ninguno (o uno roto): el aviso es obligatorio (RF-03)."""
    assert ai_notice("Negocio de Ejemplo", bad) == ai_notice("Negocio de Ejemplo", None)


@pytest.mark.parametrize(("vertical", "policy"), POLICIES)
def test_todo_rubro_da_el_aviso_de_ia(vertical: str, policy: VerticalPolicy) -> None:
    assert policy.ai_notice("Negocio de Ejemplo", None) == ai_notice("Negocio de Ejemplo", None)
