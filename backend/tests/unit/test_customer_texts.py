"""Tono de lo que lee el cliente: siempre de usted (decisión de AHS, 18-oct-2026).

Revisa todos los textos fijos que el bot o el sistema le mandan al cliente: plantillas del
orquestador, mensajes de error de las herramientas y confirmaciones de BAJA/ALTA. Lo que redacta
el modelo lo pide el prompt de cada rubro (test_verticals.py) y lo miden las evals (#28).
"""

import re

import pytest

from app.application import agent
from app.application.agent_tools import CUSTOMER_MESSAGES
from app.domain.consent import CONFIRMATION_TEXT

# Formas de tuteo que no deben aparecer (pronombres y verbos frecuentes en un chat de atención)
_TUTEO = re.compile(
    r"\b(te|ti|tú|tu|tus|contigo|quieres|puedes|necesitas|tienes|buscas|prefieres|escribes|"
    r"repites|avísanos|escríbenos|cuéntame|dime|ayudarte|pasarte|comunicarte|atenderte)\b",
    re.IGNORECASE,
)

TEXTS = {
    "TEMPLATE_LIMIT": agent.TEMPLATE_LIMIT,
    "TEMPLATE_AI_UNAVAILABLE": agent.TEMPLATE_AI_UNAVAILABLE,
    "TEMPLATE_OUT_OF_SCOPE": agent.TEMPLATE_OUT_OF_SCOPE,
    **{f"tool:{code}": text for code, text in CUSTOMER_MESSAGES.items()},
    **{f"consent:{kind}": text for kind, text in CONFIRMATION_TEXT.items()},
}


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_los_textos_al_cliente_no_tutean(name: str) -> None:
    found = _TUTEO.findall(TEXTS[name])

    assert not found, f"{name} tutea: {found}"


@pytest.mark.parametrize(
    "text",
    ["¿Quieres que te comunique con alguien?", "Si nos escribes, te seguimos atendiendo.", "Puedo pasarte"],
)
def test_el_detector_reconoce_el_tuteo(text: str) -> None:
    assert _TUTEO.search(text)


def test_el_detector_no_confunde_el_trato_de_usted() -> None:
    assert not _TUTEO.search(
        "¿Desea que le comunique con una persona? Escriba ALTA para volver a recibirlos."
    )
