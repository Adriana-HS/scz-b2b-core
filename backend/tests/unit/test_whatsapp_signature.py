"""Firma X-Hub-Signature-256 del webhook de Meta (SYSTEM_DESIGN.md §8.1, §8.3)."""

import hashlib
import hmac

import pytest

from app.api.v1.whatsapp.signature import is_valid_signature

SECRET = b"secreto-de-prueba"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def _sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_firma_correcta() -> None:
    assert is_valid_signature(BODY, _sign(BODY), SECRET)


def test_firma_en_mayusculas_tambien_vale() -> None:
    assert is_valid_signature(BODY, _sign(BODY).upper().replace("SHA256=", "sha256="), SECRET)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "sha1=" + hashlib.sha1(BODY, usedforsecurity=False).hexdigest(),
        _sign(BODY, b"otro-secreto"),
        _sign(BODY + b" "),  # un byte distinto en el cuerpo
        _sign(BODY)[:-1],  # firma truncada
        "sha256=" + "z" * 64,  # no es hexadecimal
    ],
)
def test_firmas_invalidas(header: str | None) -> None:
    assert not is_valid_signature(BODY, header, SECRET)


def test_sin_secreto_configurado_no_se_acepta_nada() -> None:
    assert not is_valid_signature(BODY, _sign(BODY, b""), b"")
