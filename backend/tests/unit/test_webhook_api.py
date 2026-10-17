"""Endpoint del webhook de WhatsApp (RF-01, SYSTEM_DESIGN.md §8.2-§8.3, §10.1)."""

import hashlib
import hmac
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.messaging import InboundBatch, InboundResult
from app.domain.outbound import StatusBatch, StatusResult
from app.main import create_app

SECRET = "secreto-de-prueba"
VERIFY_TOKEN = "token-de-verificacion-de-prueba"
URL = "/v1/webhooks/whatsapp"
PHONE = "59170000001"


class RecordingHandler:
    def __init__(self, result: InboundResult | None = None, error: Exception | None = None) -> None:
        self.batches: list[InboundBatch] = []
        self.result = result or InboundResult(accepted=1)
        self.error = error

    async def __call__(self, batch: InboundBatch) -> InboundResult:
        self.batches.append(batch)
        if self.error:
            raise self.error
        return self.result


class RecordingStatusHandler:
    def __init__(self, error: Exception | None = None) -> None:
        self.batches: list[StatusBatch] = []
        self.error = error

    async def __call__(self, batch: StatusBatch) -> StatusResult:
        self.batches.append(batch)
        if self.error:
            raise self.error
        return StatusResult(applied=len(batch.updates))


def _client(
    handler: RecordingHandler | None = None,
    *,
    secret: str | None = SECRET,
    status_handler: RecordingStatusHandler | None = None,
) -> TestClient:
    settings = Settings(
        _env_file=None, environment="test", meta_app_secret=secret, meta_webhook_verify_token=VERIFY_TOKEN
    )
    app = create_app(settings, inbound_handler=handler, status_handler=status_handler)
    return TestClient(app, raise_server_exceptions=False)


def _body(text: str = "pastilla freno hilux", wamid: str = "wamid.1") -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "PNID-1"},
                            "contacts": [{"profile": {"name": "Cliente"}, "wa_id": PHONE}],
                            "messages": [
                                {
                                    "from": PHONE,
                                    "id": wamid,
                                    "timestamp": "1792000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


def _sig(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


# --- GET: verificación de la suscripción ---------------------------------------------
def test_verificacion_devuelve_el_challenge_en_texto_plano() -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"}

    response = _client().get(URL, params=params)

    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "<script>"},
    ],
)
def test_verificacion_rechazada(params: dict[str, str]) -> None:
    assert _client().get(URL, params=params).status_code == 403


# --- POST: firma antes que todo --------------------------------------------------------
def test_mensaje_firmado_se_registra() -> None:
    handler = RecordingHandler()
    body = _body()

    response = _client(handler).post(URL, content=body, headers=_sig(body))

    assert response.status_code == 200
    [batch] = handler.batches
    assert batch.phone_number_id == "PNID-1"
    assert batch.messages[0].text == "pastilla freno hilux"


@pytest.mark.parametrize("headers", [{}, {"X-Hub-Signature-256": "sha256=" + "0" * 64}])
def test_sin_firma_valida_es_401_y_no_se_procesa(headers: dict[str, str]) -> None:
    handler = RecordingHandler()

    response = _client(handler).post(URL, content=_body(), headers=headers)

    assert response.status_code == 401
    assert handler.batches == []


def test_la_firma_se_verifica_antes_de_parsear_el_json() -> None:
    # JSON roto y sin firma: debe ser 401 (firma), no 400 (JSON). El JSON nunca se parsea.
    assert _client().post(URL, content=b"{no es json", headers={}).status_code == 401


def test_firma_de_otro_cuerpo_es_401() -> None:
    assert _client().post(URL, content=_body("otro"), headers=_sig(_body())).status_code == 401


def test_sin_secreto_configurado_el_webhook_falla_cerrado() -> None:
    body = _body()

    assert (
        _client(RecordingHandler(), secret=None).post(URL, content=body, headers=_sig(body)).status_code
        == 401
    )


def test_cuerpo_de_mas_de_1_mb_es_413() -> None:
    body = b"{" + b" " * (1024 * 1024) + b"}"

    response = _client(RecordingHandler()).post(URL, content=body, headers=_sig(body))

    assert response.status_code == 413


def test_json_invalido_con_firma_valida_es_400() -> None:
    body = b"{no es json"

    assert _client(RecordingHandler()).post(URL, content=body, headers=_sig(body)).status_code == 400


def test_numero_desconocido_responde_200_para_que_meta_no_reintente() -> None:
    body = _body()
    handler = RecordingHandler(InboundResult(unknown_channel=True))

    assert _client(handler).post(URL, content=body, headers=_sig(body)).status_code == 200


def test_solo_estados_va_al_caso_de_uso_de_estados() -> None:
    handler = RecordingHandler()
    statuses = RecordingStatusHandler()
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PNID-1"},
                                "statuses": [{"id": "wamid.o", "status": "read", "timestamp": "1792000000"}],
                            },
                        }
                    ]
                }
            ],
        }
    ).encode()

    assert (
        _client(handler, status_handler=statuses).post(URL, content=body, headers=_sig(body)).status_code
        == 200
    )
    assert handler.batches == []
    assert [u.wa_message_id for b in statuses.batches for u in b.updates] == ["wamid.o"]


def test_si_falla_registrar_un_estado_es_500_para_que_meta_reintente() -> None:
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PNID-1"},
                                "statuses": [{"id": "wamid.o", "status": "read", "timestamp": "1792000000"}],
                            },
                        }
                    ]
                }
            ],
        }
    ).encode()
    statuses = RecordingStatusHandler(error=RuntimeError("bd caída"))

    assert (
        _client(RecordingHandler(), status_handler=statuses)
        .post(URL, content=body, headers=_sig(body))
        .status_code
        == 500
    )


def test_si_falla_la_persistencia_es_500_para_que_meta_reintente_y_no_se_loguea_pii(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _body(text="mi carnet es 1234567")
    handler = RecordingHandler(error=RuntimeError(f"Key (wa_id)=({PHONE}) mi carnet es 1234567"))
    caplog.set_level(logging.INFO)

    response = _client(handler).post(URL, content=body, headers=_sig(body))

    assert response.status_code == 500
    assert PHONE not in response.text and PHONE not in caplog.text
    assert "1234567" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_sin_caso_de_uso_configurado_es_503() -> None:
    body = _body()

    assert _client(None).post(URL, content=body, headers=_sig(body)).status_code == 503


def test_el_log_de_exito_tiene_conteos_y_no_contenido(caplog: pytest.LogCaptureFixture) -> None:
    body = _body(text="dato privado del cliente")
    caplog.set_level(logging.INFO, logger="scz.webhook")

    _client(RecordingHandler()).post(URL, content=body, headers=_sig(body))

    assert "accepted=1" in caplog.text
    assert "dato privado" not in caplog.text and PHONE not in caplog.text
