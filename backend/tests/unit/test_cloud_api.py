"""Adaptador de la WhatsApp Cloud API contra un doble de Meta (httpx.MockTransport)."""

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.domain.errors import PermanentSendError, TransientSendError
from app.domain.outbound import OutboundKind, OutboundRequest, TemplateCategory
from app.infrastructure.whatsapp.cloud_api import CloudApiSender

TOKEN = "EAAG-token-de-prueba"
PHONE_ID = "106540352242922"


class FakeMeta:
    """Doble de la Graph API: guarda cada petición y responde lo que se le programe."""

    def __init__(self, status: int = 200, body: Any = None, error: Exception | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {"messages": [{"id": "wamid.SALIENTE1"}]}
        self.error = error
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body)


def _sender(meta: FakeMeta) -> CloudApiSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(meta.handler))
    return CloudApiSender(client, api_version="v23.0")


async def _send(meta: FakeMeta, request: OutboundRequest) -> str:
    return await _sender(meta).send(PHONE_ID, TOKEN, "59170000001", request.kind, request.payload)


async def test_texto_url_token_y_cuerpo_segun_la_cloud_api() -> None:
    meta = FakeMeta()

    wamid = await _send(meta, OutboundRequest.text(uuid4(), "k", "Su cotización COT-2026-000123"))

    [request] = meta.requests
    assert wamid == "wamid.SALIENTE1"
    assert str(request.url) == f"https://graph.facebook.com/v23.0/{PHONE_ID}/messages"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "59170000001",
        "type": "text",
        "text": {"body": "Su cotización COT-2026-000123", "preview_url": False},
    }


async def test_documento_por_media_id() -> None:
    meta = FakeMeta()

    await _send(meta, OutboundRequest.document(uuid4(), "k", "MEDIA-9", "COT-2026-000123.pdf", "Cotización"))

    body = json.loads(meta.requests[0].content)
    assert body["type"] == "document"
    assert body["document"] == {"id": "MEDIA-9", "filename": "COT-2026-000123.pdf", "caption": "Cotización"}


async def test_plantilla_con_idioma_y_componentes() -> None:
    meta = FakeMeta()
    components = [{"type": "body", "parameters": [{"type": "text", "text": "mañana 09:00"}]}]

    await _send(
        meta,
        OutboundRequest.template(
            uuid4(), "k", "recordatorio_cita", "es", TemplateCategory.UTILITY, components
        ),
    )

    body = json.loads(meta.requests[0].content)
    assert body["type"] == "template"
    assert body["template"] == {
        "name": "recordatorio_cita",
        "language": {"code": "es"},
        "components": components,
    }


@pytest.mark.parametrize(
    ("meta", "code"),
    [
        (FakeMeta(429, {"error": {"code": 130429, "message": "Rate limit"}}), "HTTP_429"),
        (FakeMeta(503, {"error": {"message": "Service unavailable"}}), "HTTP_503"),
        (FakeMeta(401, {"error": {"code": 190, "message": "Invalid OAuth access token"}}), "AUTH_190"),
        (FakeMeta(error=httpx.ConnectTimeout("timeout")), "NETWORK"),
        (FakeMeta(200, {"inesperado": True}), "BAD_RESPONSE"),
    ],
)
async def test_errores_que_vale_la_pena_reintentar(meta: FakeMeta, code: str) -> None:
    with pytest.raises(TransientSendError) as info:
        await _send(meta, OutboundRequest.text(uuid4(), "k", "hola"))

    assert info.value.code == code


@pytest.mark.parametrize(
    ("error_code", "code"),
    [(131047, "WINDOW_CLOSED"), (131026, "META_131026"), (132001, "META_132001")],
)
async def test_errores_permanentes_no_se_reintentan(error_code: int, code: str) -> None:
    meta = FakeMeta(
        400, {"error": {"code": error_code, "message": "detalle de Meta", "error_data": {"x": TOKEN}}}
    )

    with pytest.raises(PermanentSendError) as info:
        await _send(meta, OutboundRequest.text(uuid4(), "k", "hola"))

    assert info.value.code == code


async def test_el_token_nunca_aparece_en_un_error() -> None:
    meta = FakeMeta(error=httpx.ConnectError(f"no se pudo conectar con Bearer {TOKEN}"))

    with pytest.raises(TransientSendError) as info:
        await _send(meta, OutboundRequest.text(uuid4(), "k", "hola"))

    assert TOKEN not in str(info.value) and TOKEN not in repr(info.value)
    assert info.value.__cause__ is None  # no se encadena la excepción original (podría llevar el token)


@pytest.mark.parametrize("version", ["23.0", "v23", "latest", "v23.0/../x"])
def test_la_version_de_la_api_se_valida(version: str) -> None:
    with pytest.raises(ValueError):
        CloudApiSender(httpx.AsyncClient(), api_version=version)


async def test_phone_number_id_invalido_no_llega_a_la_url() -> None:
    meta = FakeMeta()

    with pytest.raises(PermanentSendError):
        await _sender(meta).send("../../me", TOKEN, "59170000001", OutboundKind.TEXT, {"body": "x"})
    assert meta.requests == []
