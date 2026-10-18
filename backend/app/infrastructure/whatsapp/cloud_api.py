"""Adaptador de envío de la WhatsApp Cloud API (ADR-0006). Capa anticorrupción de Meta.

POST https://graph.facebook.com/<versión>/<phone_number_id>/messages con el token del
número en `Authorization: Bearer`. Clasifica la respuesta en:
  * TransientSendError: red, 429, 5xx, token rechazado (operación puede renovarlo) ->
    el worker reintenta con backoff; si no se arregla, DEAD con alerta.
  * PermanentSendError: ventana cerrada en Meta (131047), destinatario inválido, plantilla
    rechazada... -> FAILED sin reintentos.
Los errores llevan solo un código estable: nunca el token, el cuerpo ni el detalle de Meta.
También sube archivos (POST .../<phone_number_id>/media, multipart) para enviarlos por media_id
(#26): el PDF de una cotización. La subida se clasifica igual que un envío.
Referencias: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages y
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
"""

import re
from typing import Any, NoReturn

import httpx

from app.domain.errors import PermanentSendError, TransientSendError
from app.domain.outbound import OutboundKind

_VERSION = re.compile(r"^v\d{1,3}\.\d{1,2}$")
_PHONE_NUMBER_ID = re.compile(r"^\d{5,30}$")
_AUTH_CODES = {190, 10, 200}  # token inválido o vencido, permisos
_RATE_LIMIT_CODES = {4, 80007, 130429, 131048, 131056}
_WINDOW_CLOSED = 131047  # más de 24 h desde el último mensaje del cliente


def _body(to: str, kind: OutboundKind, payload: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    if kind == OutboundKind.TEXT:
        return {**base, "type": "text", "text": {"body": payload["body"], "preview_url": False}}
    if kind == OutboundKind.DOCUMENT:
        document = {"id": payload["media_id"], "filename": payload["filename"]}
        if payload.get("caption"):
            document["caption"] = payload["caption"]
        return {**base, "type": "document", "document": document}
    if kind == OutboundKind.TEMPLATE:
        template = {
            "name": payload["name"],
            "language": {"code": payload["language"]},
            "components": payload.get("components", []),
        }
        return {**base, "type": "template", "template": template}
    raise PermanentSendError(f"KIND_{kind}")  # pragma: no cover - OutboundKind es cerrado


class CloudApiSender:
    def __init__(
        self, client: httpx.AsyncClient, *, api_version: str, base_url: str = "https://graph.facebook.com",
        timeout_s: float = 10.0,
    ) -> None:  # fmt: skip
        if not _VERSION.fullmatch(api_version):
            raise ValueError("api_version debe tener la forma vNN.N (p. ej. v23.0)")
        self._client = client
        self._base = base_url.rstrip("/")
        self._version = api_version
        self._timeout = timeout_s

    async def send(
        self, phone_number_id: str, token: str, to: str, kind: OutboundKind, payload: dict[str, Any]
    ) -> str:
        if not _PHONE_NUMBER_ID.fullmatch(phone_number_id):
            raise PermanentSendError("BAD_PHONE_NUMBER_ID")  # nunca se arma una URL con otro valor
        try:
            body = _body(to, kind, payload)
        except KeyError:
            raise PermanentSendError("BAD_PAYLOAD") from None
        url = f"{self._base}/{self._version}/{phone_number_id}/messages"
        try:
            response = await self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout
            )
        except httpx.HTTPError:
            # `from None`: la excepción original podría contener la URL o encabezados
            raise TransientSendError("NETWORK") from None

        if response.status_code == 200:
            try:
                wa_message_id = response.json()["messages"][0]["id"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise TransientSendError("BAD_RESPONSE") from None
            if not isinstance(wa_message_id, str) or not wa_message_id:
                raise TransientSendError("BAD_RESPONSE")
            return wa_message_id
        self._raise_for(response)

    async def upload_media(
        self, phone_number_id: str, token: str, content: bytes, mime_type: str, filename: str
    ) -> str:
        """Sube un archivo (el PDF de una cotización, #26) y devuelve su media_id. El mensaje que lo
        envía va después por el outbox. Meta guarda el archivo 30 días."""
        if not _PHONE_NUMBER_ID.fullmatch(phone_number_id):
            raise PermanentSendError("BAD_PHONE_NUMBER_ID")
        url = f"{self._base}/{self._version}/{phone_number_id}/media"
        try:
            response = await self._client.post(
                url,
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, content, mime_type)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            raise TransientSendError("NETWORK") from None
        if response.status_code == 200:
            try:
                media_id = response.json()["id"]
            except (ValueError, KeyError, TypeError):
                raise TransientSendError("BAD_RESPONSE") from None
            if not isinstance(media_id, str) or not media_id:
                raise TransientSendError("BAD_RESPONSE")
            return media_id
        self._raise_for(response)

    def _raise_for(self, response: httpx.Response) -> NoReturn:
        """La misma clasificación para envíos y subidas: solo un código estable, nunca el detalle."""
        code = self._meta_error_code(response)
        if response.status_code == 429 or response.status_code >= 500 or code in _RATE_LIMIT_CODES:
            raise TransientSendError(f"HTTP_{response.status_code}")
        if response.status_code in (401, 403) or code in _AUTH_CODES:
            raise TransientSendError(f"AUTH_{code if code is not None else response.status_code}")
        if code == _WINDOW_CLOSED:
            raise PermanentSendError("WINDOW_CLOSED")
        raise PermanentSendError(f"META_{code}" if code is not None else f"HTTP_{response.status_code}")

    @staticmethod
    def _meta_error_code(response: httpx.Response) -> int | None:
        try:
            code = response.json().get("error", {}).get("code")
        except (ValueError, AttributeError):
            return None
        return code if isinstance(code, int) else None
