"""Endpoint del webhook de WhatsApp (RF-01, SYSTEM_DESIGN.md §4.4, §8.2-§8.3, §10.1).

POST, en este orden y sin trabajo pesado (ack p99 <= 250 ms, Q-PERF-1):
  1. Cuerpo máximo de 1 MB              -> 413
  2. Firma HMAC sobre los bytes crudos  -> 401 (el JSON NO se parsea antes)
  3. JSON                               -> 400 si no es JSON (con firma válida, no debería pasar)
  4. Persistir + encolar (una transacción por número del negocio)
     -> 200; 500 si la BD falla, para que Meta reintente (es idempotente por wa_message_id)
Un número desconocido responde 200: con un 4xx, Meta reintentaría sin fin (§8.2).

Los logs llevan solo conteos y tipos de error: nunca teléfonos, nombres ni textos (§10.2).
"""

import hmac
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.v1.whatsapp.payload import parse_webhook
from app.api.v1.whatsapp.signature import is_valid_signature
from app.application.inbound import InboundHandler
from app.core.config import Settings

MAX_BODY_BYTES = 1024 * 1024
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")

logger = logging.getLogger("scz.webhook")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("", response_class=PlainTextResponse)
async def verify_subscription(
    request: Request,
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta llama a este GET al suscribir el webhook y espera el `hub.challenge` de vuelta."""
    expected = _settings(request).meta_webhook_verify_token
    token_ok = (
        expected is not None
        and token is not None
        and hmac.compare_digest(token.encode(), expected.get_secret_value().encode())
    )
    if mode != "subscribe" or not token_ok or challenge is None or not _CHALLENGE.fullmatch(challenge):
        logger.warning("webhook: verificación rechazada")
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(challenge)  # texto plano: el valor no se interpreta como HTML


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="cuerpo demasiado grande")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="cuerpo demasiado grande")
    return bytes(body)


@router.post("")
async def receive_events(request: Request) -> JSONResponse:
    body = await _read_body(request)

    secret = _settings(request).meta_app_secret
    app_secret = secret.get_secret_value().encode() if secret is not None else b""
    if not is_valid_signature(body, request.headers.get("x-hub-signature-256"), app_secret):
        logger.warning("webhook: firma inválida o ausente (%d bytes)", len(body))
        raise HTTPException(status_code=401, detail="firma inválida")

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    parsed = parse_webhook(payload)
    handler: InboundHandler | None = request.app.state.inbound_handler
    if parsed.batches and handler is None:
        logger.error("webhook: no hay persistencia configurada (DATABASE_URL)")
        raise HTTPException(status_code=503, detail="no disponible")

    accepted = duplicates = unknown = 0
    for batch in parsed.batches:
        assert handler is not None  # noqa: S101 - garantizado por el chequeo de arriba
        try:
            result = await handler(batch)
        except Exception as exc:
            # Solo el tipo: el mensaje de un error de BD puede traer datos del cliente
            logger.error("webhook: no se pudo registrar un lote (%s); Meta reintentará", type(exc).__name__)
            return JSONResponse({"status": "error"}, status_code=500)
        accepted += result.accepted
        duplicates += result.duplicates
        unknown += int(result.unknown_channel)

    logger.info(
        "webhook: accepted=%d duplicates=%d unknown_channels=%d statuses=%d invalid=%d ignored=%d",
        accepted, duplicates, unknown, parsed.statuses, parsed.invalid, parsed.ignored,
    )  # fmt: skip
    return JSONResponse({"status": "ok"})
