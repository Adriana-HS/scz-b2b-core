"""Guardrails de la respuesta del bot (#25, SYSTEM_DESIGN.md §7.4). Funciones puras, sin I/O.

* **Grounding numérico (§7.4.1):** todo importe que diga el bot (con moneda o con decimales) tiene
  que coincidir con un valor que devolvió una herramienta en ese turno. Así no salen precios,
  descuentos ni totales inventados, ni los que el cliente intente dictar desde el chat.
* **Datos sensibles (RF-09, ADR-0014):** el bot nunca pide CI, NIT, tarjetas ni números de cuenta.

Formatos de Bolivia: coma decimal y punto de miles ("1.332,00 Bs"), pero también llega el formato
inglés ("1,332.00 USD"). Un número con un solo separador seguido de tres dígitos ("1.332") es
ambiguo: vale cualquiera de las dos lecturas. Una corrida que no es un número válido (una fecha
"25.10.2026") no es un importe.
"""

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

_RUN = re.compile(r"\d[\d.,]*\d|\d")  # corrida máxima de dígitos y separadores
_GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?$")
_PLAIN = re.compile(r"^\d+(?:[.,]\d+)?$")
_CURRENCY_BEFORE = re.compile(r"(?:\bBs\.?|\$us|US\$|\bUSD|\$)\s*$", re.IGNORECASE)
_CURRENCY_AFTER = re.compile(r"^\s*(?:Bs\b|bolivianos\b|\$us|USD\b|US\$|d[oó]lares\b)", re.IGNORECASE)
_PERCENT_AFTER = re.compile(r"^\s*%")
_NUMERIC_STRING = re.compile(r"^-?\d+(?:\.\d+)?$")

_SENSITIVE = re.compile(
    r"\b(?:CI|C\.I\.|NIT|carnet|c[eé]dula|tarjeta|CVV|PIN|contrase[ñn]a|"
    r"n[uú]mero de cuenta|cuenta bancaria)\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(?:env[ií]e(?:me|nos)?|m[aá]nde(?:me|nos)?|ind[ií]que(?:me|nos)?|(?:me|nos) indica|p[aá]se(?:me|nos)|"
    r"(?:me|nos) pasa|necesito|necesitamos|requiero|requerimos|proporci[oó]ne(?:me|nos)?|d[ií]ga(?:me|nos)|"
    r"escriba|comp[aá]rta(?:me|nos)?|facil[ií]te(?:me|nos)?|conf[ií]rme(?:me|nos)|podr[ií]a darme|"
    r"cu[aá]l es su|dame|p[aá]same|env[ií]ame|m[aá]ndame)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.?!\n]+")


def _readings(token: str) -> set[Decimal]:
    """Las lecturas posibles de un número escrito por una persona."""
    if "." in token and "," in token:
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        return {Decimal(token.replace(thousands, "").replace(decimal_sep, "."))}
    separators = [c for c in token if c in ".,"]
    if not separators:
        return {Decimal(token)}
    if len(separators) > 1:  # "1.234.567": solo miles
        return {Decimal(token.replace(separators[0], ""))}
    integer, fraction = re.split(r"[.,]", token)
    as_decimal = Decimal(f"{integer}.{fraction}")
    if len(fraction) == 3:  # "1.332": mil trescientos o uno coma trescientos
        return {Decimal(integer + fraction), as_decimal}
    return {as_decimal}


def _amount_tokens(text: str) -> list[tuple[str, set[Decimal]]]:
    found: list[tuple[str, set[Decimal]]] = []
    for match in _RUN.finditer(text):
        token = match.group()
        if not (_GROUPED.fullmatch(token) or _PLAIN.fullmatch(token)):
            continue  # una fecha u otra cosa que no es un número
        before, after = text[: match.start()], text[match.end() :]
        if _PERCENT_AFTER.match(after):
            continue  # porcentajes
        has_currency = bool(_CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.match(after))
        groups = re.split(r"[.,]", token)
        has_decimals = len(groups) > 1 and len(groups[-1]) <= 2  # "11,68" y "1.332,00", no "1.332"
        if has_currency or has_decimals:
            found.append((token, _readings(token)))
    return found


def amounts_in(text: str) -> list[set[Decimal]]:
    """Los importes del texto, cada uno con sus lecturas posibles."""
    return [readings for _, readings in _amount_tokens(text)]


def tool_values(contents: Iterable[str]) -> set[Decimal]:
    """Todos los números que devolvieron las herramientas (en JSON, como número o como texto exacto)."""
    values: set[Decimal] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, int | float):
            values.add(Decimal(str(node)))
        elif isinstance(node, str) and _NUMERIC_STRING.fullmatch(node):
            values.add(Decimal(node))

    for content in contents:
        try:
            walk(json.loads(content))
        except (ValueError, InvalidOperation):
            continue
    return values


def ungrounded_amounts(text: str, allowed: set[Decimal]) -> list[str]:
    """Los importes del texto que no salieron de ninguna herramienta (vacío = la respuesta puede salir)."""
    return [token for token, readings in _amount_tokens(text) if not readings & allowed]


def asks_for_sensitive_data(text: str) -> bool:
    """True si alguna frase pide un dato sensible (no basta con mencionarlo: "no necesita tarjeta")."""
    return any(
        _SENSITIVE.search(sentence) and _REQUEST.search(sentence) for sentence in _SENTENCE.findall(text)
    )
