"""Verificación de `X-Hub-Signature-256` (SYSTEM_DESIGN.md §8.1, §8.3).

Meta firma el cuerpo CRUDO con HMAC-SHA256 y el App Secret. Se verifica sobre los bytes
tal como llegaron (antes de parsear el JSON) y con comparación en tiempo constante.
"""

import hashlib
import hmac

_PREFIX = "sha256="
_HEX_DIGITS = frozenset("0123456789abcdef")


def is_valid_signature(body: bytes, header: str | None, app_secret: bytes) -> bool:
    """True solo si la firma del encabezado corresponde a `body` con `app_secret`.

    Falla cerrado: sin secreto configurado, sin encabezado o con un formato distinto, es False.
    """
    if not app_secret or not header or not header.startswith(_PREFIX):
        return False
    provided = header[len(_PREFIX) :].lower()
    if len(provided) != hashlib.sha256().digest_size * 2 or not set(provided) <= _HEX_DIGITS:
        return False
    expected = hmac.new(app_secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
