"""Agenda por WhatsApp (#35, RF-22, RF-23): horarios como identificadores opacos, lista interactiva
de Meta y la respuesta del cliente con el horario elegido."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.v1.whatsapp.payload import parse_webhook
from app.domain.booking import parse_slot_token, slot_label, slot_token
from app.domain.outbound import ListRow, OutboundKind, OutboundRequest
from app.infrastructure.whatsapp.cloud_api import CloudApiSender

RESOURCE = UUID("5eed0000-0000-4000-8000-0000000000b1")
START = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)  # 10:00 en Santa Cruz


# --- El horario como identificador ----------------------------------------------------------------
def test_el_slot_va_y_vuelve_sin_perder_nada() -> None:
    token = slot_token(RESOURCE, START)

    assert token == f"slot:{RESOURCE}:20261021T1400Z"
    assert parse_slot_token(token) == (RESOURCE, START)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "slot:",
        f"slot:{RESOURCE}:2026-10-21T14:00Z",
        "slot:x:20261021T1400Z",
        f"otro:{RESOURCE}:20261021T1400Z",
    ],
)
def test_un_slot_que_no_ofrecimos_se_rechaza(token: str) -> None:
    with pytest.raises(ValueError, match="horario"):
        parse_slot_token(token)


def test_la_etiqueta_va_en_la_hora_del_negocio_y_entra_en_una_fila() -> None:
    label = slot_label(START, "America/La_Paz")

    assert label == "mié 21/10 10:00"
    assert len(label) <= 24


# --- La lista interactiva ------------------------------------------------------------------------
ROWS = (ListRow(slot_token(RESOURCE, START), "mié 21/10 10:00", "Dra. Ejemplo Uno"),)


def test_la_lista_respeta_los_limites_de_whatsapp() -> None:
    request = OutboundRequest.interactive_list(uuid4(), "k", "Estos son los horarios:", "Ver horarios", ROWS)

    assert request.kind is OutboundKind.INTERACTIVE
    assert request.payload["rows"] == [
        {"id": ROWS[0].id, "title": "mié 21/10 10:00", "description": "Dra. Ejemplo Uno"}
    ]


@pytest.mark.parametrize(
    ("body", "button", "rows"),
    [("", "Ver", ROWS), ("x" * 1025, "Ver", ROWS), ("Hola", "x" * 21, ROWS), ("Hola", "Ver", ()),
     ("Hola", "Ver", ROWS * 11), ("Hola", "Ver", ROWS * 2)],
)  # fmt: skip
def test_una_lista_fuera_de_limites_no_se_arma(body: str, button: str, rows: tuple[ListRow, ...]) -> None:
    with pytest.raises(ValueError):
        OutboundRequest.interactive_list(uuid4(), "k", body, button, rows)


@pytest.mark.parametrize(
    "row",
    [
        {"id": "", "title": "x"},
        {"id": "a", "title": "x" * 25},
        {"id": "a", "title": "x", "description": "d" * 73},
    ],
)
def test_una_fila_fuera_de_limites_no_se_arma(row: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ListRow(**row)


async def test_la_lista_sale_a_meta_como_interactive_list() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.LISTA"}]})

    sender = CloudApiSender(httpx.AsyncClient(transport=httpx.MockTransport(handler)), api_version="v23.0")
    request = OutboundRequest.interactive_list(uuid4(), "k", "Horarios:", "Ver horarios", ROWS)

    await sender.send("106540352242922", "token", "59170000001", request.kind, request.payload)

    body: dict[str, Any] = json.loads(sent[0].content)
    assert body["type"] == "interactive"
    sections = [{"title": "Opciones", "rows": request.payload["rows"]}]
    assert body["interactive"] == {
        "type": "list",
        "body": {"text": "Horarios:"},
        "action": {"button": "Ver horarios", "sections": sections},
    }


# --- La respuesta del cliente --------------------------------------------------------------------
def _webhook(reply: dict[str, Any]) -> dict[str, Any]:
    message = {"from": "59170000001", "id": "wamid.IN", "timestamp": "1760000000", "type": "interactive",
               "interactive": {"type": "list_reply", "list_reply": reply}}  # fmt: skip
    value = {"metadata": {"phone_number_id": "106540352242922"}, "messages": [message]}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": value}]}],
    }


def test_el_horario_elegido_llega_con_su_slot_para_el_modelo() -> None:
    token = slot_token(RESOURCE, START)

    [batch] = parse_webhook(_webhook({"id": token, "title": "mié 21/10 10:00"})).batches

    assert batch.messages[0].text == f"mié 21/10 10:00 [{token}]"


def test_otras_opciones_llegan_solo_con_su_titulo() -> None:
    [batch] = parse_webhook(_webhook({"id": "menu-precios", "title": "Precios"})).batches

    assert batch.messages[0].text == "Precios"
