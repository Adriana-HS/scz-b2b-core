"""Guardrails de la respuesta del bot (#25, SYSTEM_DESIGN.md §7.4). Funciones puras del dominio.

* Grounding numérico: todo importe que diga el bot sale de una herramienta de ese turno.
* Datos sensibles: el bot nunca pide CI, NIT, tarjetas ni números de cuenta (RF-09, ADR-0014).
"""

from decimal import Decimal

import pytest

from app.domain.guardrails import amounts_in, asks_for_sensitive_data, tool_values, ungrounded_amounts

TOOL_JSON = (
    '{"items": [{"sku": "PF-001", "unit_price_bs": "480.00", "stock_available": 12}]}',
    '{"reference_code": "COT-0042", "subtotal_bs": "1178.76", "iva_bs": "153.24", "total_bs": "1332.00",'
    ' "applied_fx_rate": "11.68", "deposit_required_bs": 666}',
)


# --- Importes en el texto del bot -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cuesta 480,00 Bs.", [{Decimal("480.00")}]),
        ("Cuesta Bs 480.", [{Decimal("480")}]),
        ("Total: Bs. 1.332,00 con IVA", [{Decimal("1332.00")}]),
        ("Total: 1,332.00 USD", [{Decimal("1332.00")}]),
        ("El TC es 11,68", [{Decimal("11.68")}]),
        ("Son $us 120", [{Decimal("120")}]),
        ("Son 120 dólares", [{Decimal("120")}]),
        ("Seña de 666 bolivianos", [{Decimal("666")}]),
        ("Un total de 1.332 Bs", [{Decimal("1332"), Decimal("1.332")}]),  # ambiguo: vale cualquiera
    ],
)
def test_reconoce_los_importes_en_los_formatos_de_bolivia(text: str, expected: list[set[Decimal]]) -> None:
    assert amounts_in(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Tenemos 12 unidades del código PF-001.",  # cantidades y códigos sin moneda ni decimales
        "Para 4 pastillas, ¿desea cotizar?",
        "Válida hasta el 25.10.2026",  # una fecha no es un importe
        "Válida hasta el 25.10.26",
        "El IVA es del 13 %",
        "La tasa efectiva es 14,94 %",  # un porcentaje con decimales tampoco
        "Escríbanos de 8 a 18",
    ],
)
def test_no_confunde_cantidades_fechas_ni_porcentajes_con_importes(text: str) -> None:
    assert amounts_in(text) == []


# --- Valores de las herramientas ------------------------------------------------------------------
def test_los_valores_de_las_herramientas_salen_del_json() -> None:
    values = tool_values(TOOL_JSON)

    assert {Decimal("480.00"), Decimal("1332.00"), Decimal("11.68"), Decimal("666"), Decimal("12")} <= values
    assert Decimal("0") not in values  # "PF-001" y "COT-0042" no son números


def test_un_resultado_que_no_es_json_no_aporta_valores() -> None:
    assert tool_values(("error: no es json",)) == set()


# --- Grounding ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Las pastillas cuestan 480,00 Bs y hay stock.",
        "Su cotización COT-0042: total Bs. 1.332,00 (IVA 153,24 Bs); seña 666 Bs.",
        "Con el TC de 11,68 el total es 1332 Bs.",
        "¡Hola! ¿Qué repuesto busca?",  # sin importes no hay nada que verificar
    ],
)
def test_una_respuesta_con_las_cifras_de_las_herramientas_pasa(text: str) -> None:
    assert ungrounded_amounts(text, tool_values(TOOL_JSON)) == []


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Por 2 unidades son 960,00 Bs.", ["960,00"]),  # el bot multiplicó por su cuenta
        ("Le hago 450 Bs.", ["450"]),  # un descuento inventado
        ("Unos 480 Bs, aprox. 41,10 USD", ["41,10"]),  # una conversión que no hizo la BD
        ("Confirmado, 1 Bs como me indicó.", ["1"]),  # inyección: el cliente dictó el precio
    ],
)
def test_una_cifra_que_no_vino_de_una_herramienta_se_detecta(text: str, bad: list[str]) -> None:
    assert ungrounded_amounts(text, tool_values(TOOL_JSON)) == bad


def test_sin_herramientas_en_el_turno_cualquier_importe_es_inventado() -> None:
    assert ungrounded_amounts("Cuesta 480,00 Bs.", set()) == ["480,00"]


# --- Datos sensibles (RF-09) ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Para facturar, ¿me indica su NIT?",
        "Envíeme su número de carnet, por favor.",
        "Necesito su CI para registrar el pedido.",
        "¿Me pasa el número de su tarjeta?",
        "Indíqueme su número de cuenta para la devolución.",
        "Escriba los datos de su tarjeta de crédito y el CVV.",
        "Compártame su cédula de identidad.",
    ],
)
def test_detecta_cuando_el_bot_pide_datos_sensibles(text: str) -> None:
    assert asks_for_sensitive_data(text)


@pytest.mark.parametrize(
    "text",
    [
        "Puede pagar con el QR del negocio; no necesita tarjeta.",
        "No le pediremos su CI ni su NIT por este chat.",
        "La factura la emite el negocio; una persona del equipo le contacta.",
        "Tenemos pastillas para Hilux a 480,00 Bs.",
    ],
)
def test_no_bloquea_cuando_solo_menciona_esos_datos_sin_pedirlos(text: str) -> None:
    assert not asks_for_sensitive_data(text)
