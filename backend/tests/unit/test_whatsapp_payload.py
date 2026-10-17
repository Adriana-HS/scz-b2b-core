"""Traducción del JSON de Meta a mensajes del dominio (capa anticorrupción)."""

from datetime import UTC, datetime
from typing import Any

from app.api.v1.whatsapp.payload import parse_webhook

TS = "1792000000"  # 2026-10-17 aprox.


def _envelope(*values: dict[str, Any], obj: str = "whatsapp_business_account") -> dict[str, Any]:
    return {
        "object": obj,
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": v} for v in values]}],
    }


def _value(phone_number_id: str, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "59170000000", "phone_number_id": phone_number_id},
        "contacts": [{"profile": {"name": "Cliente Uno"}, "wa_id": "59170000001"}],
        "messages": messages,
        **extra,
    }


def _text(wamid: str, body: str = "hola", sender: str = "59170000001") -> dict[str, Any]:
    return {"from": sender, "id": wamid, "timestamp": TS, "type": "text", "text": {"body": body}}


def test_mensaje_de_texto() -> None:
    parsed = parse_webhook(_envelope(_value("PNID-1", [_text("wamid.1", "pastilla freno hilux")])))

    [batch] = parsed.batches
    [msg] = batch.messages
    assert batch.phone_number_id == "PNID-1"
    assert (msg.wa_message_id, msg.from_wa_id, msg.msg_type, msg.text) == (
        "wamid.1",
        "59170000001",
        "text",
        "pastilla freno hilux",
    )
    assert msg.profile_name == "Cliente Uno"
    assert msg.sent_at == datetime.fromtimestamp(int(TS), UTC)
    assert msg.raw["id"] == "wamid.1"


def test_respuestas_interactivas_y_de_boton_se_leen_como_texto() -> None:
    button = {
        "from": "59170000001",
        "id": "wamid.b",
        "timestamp": TS,
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "si", "title": "Sí, confirmo"}},
    }
    listed = {
        "from": "59170000001",
        "id": "wamid.l",
        "timestamp": TS,
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": "a", "title": "Agenda"}},
    }
    quick = {
        "from": "59170000001",
        "id": "wamid.q",
        "timestamp": TS,
        "type": "button",
        "button": {"text": "Ver cotización", "payload": "x"},
    }

    [batch] = parse_webhook(_envelope(_value("PNID-1", [button, listed, quick]))).batches

    assert [m.text for m in batch.messages] == ["Sí, confirmo", "Agenda", "Ver cotización"]


def test_imagen_y_audio_llegan_sin_texto_y_con_el_evento_original() -> None:
    image = {
        "from": "59170000001",
        "id": "wamid.i",
        "timestamp": TS,
        "type": "image",
        "image": {"id": "MEDIA-1", "mime_type": "image/jpeg", "sha256": "abc"},
    }

    [batch] = parse_webhook(_envelope(_value("PNID-1", [image]))).batches

    assert batch.messages[0].text is None
    assert batch.messages[0].raw["image"]["id"] == "MEDIA-1"  # el worker descarga la media


def test_mensajes_del_mismo_numero_en_varias_entradas_van_en_un_lote() -> None:
    parsed = parse_webhook(
        _envelope(_value("PNID-1", [_text("wamid.1")]), _value("PNID-1", [_text("wamid.2")]))
    )

    assert [(b.phone_number_id, len(b.messages)) for b in parsed.batches] == [("PNID-1", 2)]


def test_numeros_distintos_van_en_lotes_distintos() -> None:
    parsed = parse_webhook(
        _envelope(_value("PNID-1", [_text("wamid.1")]), _value("PNID-2", [_text("wamid.2")]))
    )

    assert sorted(b.phone_number_id for b in parsed.batches) == ["PNID-1", "PNID-2"]


def test_estados_de_mensajes_salientes_se_cuentan_pero_no_se_procesan_aqui() -> None:
    statuses = [{"id": "wamid.out", "status": "delivered", "timestamp": TS, "recipient_id": "59170000001"}]

    parsed = parse_webhook(_envelope(_value("PNID-1", [], statuses=statuses)))

    assert parsed.batches == ()
    assert parsed.statuses == 1  # los procesa el outbox (#18)


def test_mensajes_invalidos_se_descartan_sin_tumbar_el_lote() -> None:
    bad_sender = _text("wamid.x", sender="+591-700")  # no cumple wa_id ^[0-9]{8,15}$ de la BD
    no_id = {"from": "59170000001", "timestamp": TS, "type": "text", "text": {"body": "sin id"}}
    too_long = _text("wamid.y", body="x" * 4097)

    parsed = parse_webhook(_envelope(_value("PNID-1", [bad_sender, no_id, too_long, _text("wamid.ok")])))

    assert [m.wa_message_id for b in parsed.batches for m in b.messages] == ["wamid.ok"]
    assert parsed.invalid == 3


def test_otros_objetos_y_campos_se_ignoran() -> None:
    assert parse_webhook(_envelope(_value("PNID-1", [_text("wamid.1")]), obj="page")).batches == ()
    other_field = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "message_template_status_update", "value": {}}]}],
    }
    assert parse_webhook(other_field).ignored == 1


def test_estructura_inesperada_no_lanza() -> None:
    weird: object
    for weird in (
        {},
        {"object": "whatsapp_business_account", "entry": "x"},
        {"entry": [{"changes": [None]}]},
        [],
    ):
        parsed = parse_webhook(weird)
        assert parsed.batches == ()
