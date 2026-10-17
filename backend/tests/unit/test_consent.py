"""Palabras clave de consentimiento (RF-08): solo el mensaje completo, normalizado."""

from uuid import uuid4

import pytest

from app.domain.consent import ConsentKind, classify_keyword, normalize
from app.domain.outbound import OutboundRequest, TemplateCategory


@pytest.mark.parametrize(
    "text",
    ["BAJA", "baja", " Baja. ", "STOP", "stop!", "No más", "NO MAS", "no más mensajes", "Darme de baja",
     "cancelar suscripción", "¡No MÁS!"],
)  # fmt: skip
def test_bajas_reconocidas(text: str) -> None:
    assert classify_keyword(text) is ConsentKind.OPTOUT


@pytest.mark.parametrize("text", ["ALTA", "alta", "Start", "darme de alta"])
def test_altas_reconocidas(text: str) -> None:
    assert classify_keyword(text) is ConsentKind.OPTIN_UTILITY


@pytest.mark.parametrize(
    "text",
    [
        "no más frenos, quiero cotizar",  # contiene "no más" pero es un pedido
        "la baja de precios",
        "stop de luz trasero Hilux",
        "quiero darme de alta como cliente mayorista y cotizar 20 filtros",
        "",
        None,
        "baja " * 30,  # demasiado largo para ser una palabra clave
    ],
)
def test_mensajes_que_no_son_palabras_clave(text: str | None) -> None:
    assert classify_keyword(text) is None


def test_normalizacion() -> None:
    assert normalize("  ¡No   MÁS!!  ") == "no mas"


def test_una_plantilla_exige_su_categoria() -> None:
    request = OutboundRequest.template(uuid4(), "k", "recordatorio_cita", "es", TemplateCategory.UTILITY)

    assert request.payload["category"] == "UTILITY"
    with pytest.raises(ValueError, match="categoría"):
        OutboundRequest.template(uuid4(), "k", "recordatorio_cita", "es", "UTILITY")  # type: ignore[arg-type]
