"""Redacción de PII y secretos (§8.1 "Fuga de PII en logs")."""

import pytest

from app.core.redaction import redact


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("cliente 59170000001 escribió", "59170000001"),
        ("teléfono +591 70000001", "70000001"),
        ("Key (wa_id)=(59170000001) already exists", "59170000001"),
        ("mi carnet es 1234567", "1234567"),
        ("escribir a ana.perez@example.com", "ana.perez@example.com"),
        ("Authorization: Bearer EAAGm0PX4ZCpsBAAtokendeprueba123456", "EAAGm0PX4ZCpsBAAtokendeprueba123456"),
        ("token EAAGm0PX4ZCpsBAAtokendeprueba123456 inválido", "EAAGm0PX4ZCpsBAAtokendeprueba123456"),
        ("GET /x?access_token=abc123secreto&y=1", "abc123secreto"),
        ("GET /v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=tok-verif-123", "tok-verif-123"),
        ("POST /hook?api_key=k3y-de-prueba", "k3y-de-prueba"),
    ],
)
def test_se_redacta(text: str, leaked: str) -> None:
    assert leaked not in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "org a0000000-0000-4000-8000-000000000001",  # UUID con un segmento solo de dígitos
        "orden COT-2026-000123",
        "wamid.HBgLNTkxNzAwMDAwMDAxFQIAEhgUM0E",
        "total 1064.00 Bs, 3 ítems, job id=90123",
        "trace 4bf92f3577b34da6a3ce929d0e0e4736",
    ],
)
def test_no_se_redactan_identificadores_ni_montos(text: str) -> None:
    assert redact(text) == text
