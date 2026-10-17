"""Redacción de datos personales y secretos en texto libre (SYSTEM_DESIGN.md §8.1, §10.2).

Se aplica a TODO lo que sale del proceso hacia logs y Sentry: el mensaje ya formateado, los
tracebacks y los textos de los eventos. Así no depende de que quien escribe un log se acuerde
de no poner un teléfono. Es una red de seguridad, no un permiso: el código igual debe loguear
identificadores y conteos, nunca contenido.

Se redacta:
  * teléfonos y documentos: 7 o más dígitos seguidos (con espacios o + opcionales), salvo
    dentro de identificadores (UUID, wamid, COT-2026-000123), que no son datos personales.
    Un número escrito con guiones (700-00001) NO se detecta: permitir guiones confundiría
    los identificadores. Por eso la regla primera sigue siendo no loguear texto del cliente;
  * correos electrónicos;
  * tokens: "Bearer <x>", tokens de Meta (EAA...) y parámetros que terminan en token, key,
    secret, password o pwd (hub.verify_token=, api_key=, access_token=...).
"""

import re

PHONE = "[teléfono]"
EMAIL = "[correo]"
SECRET = "[secreto]"  # noqa: S105 - es el reemplazo visible, no una contraseña

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {SECRET}"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), SECRET),
    # Cualquier parámetro que termine en token/key/secret/...: hub.verify_token=, api_key=, access_token=
    (re.compile(r"(?i)\b([\w.]*(?:token|key|secret|password|pwd))=([^&\s]+)"), rf"\1={SECRET}"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), EMAIL),
    # Dígitos: no si están pegados a letras, guiones o puntos de un identificador
    (re.compile(r"(?<![\w.-])\+?\d(?:[\d ]{5,}\d)(?![\w.-])"), PHONE),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
