"""Handler SEND_OUTBOUND con dobles: orden de commits, ventana al enviar, sin duplicados, errores."""

import logging
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

import pytest

from app.application.outbound import SendOutbound
from app.domain.errors import PermanentSendError, TransientSendError
from app.domain.jobs import Job
from app.domain.outbound import OutboundKind, OutboundStatus, PendingSend
from app.domain.tenancy import Actor, TenantContext

ORG = uuid4()


def _pending(**overrides: Any) -> PendingSend:
    values: dict[str, Any] = {
        "outbound_id": 41,
        "status": OutboundStatus.PENDING,
        "kind": OutboundKind.TEXT,
        "payload": {"body": "hola"},
        "to_wa_id": "59170000001",
        "phone_number_id": "106540352242922",
        "access_token_ref": "WA_TOKEN_A",
        "window_open": True,
        "consent_ok": False,
    }
    values.update(overrides)
    return PendingSend(**values)


class Log(list[str]):
    pass


class FakeOutbound:
    def __init__(self, log: Log, row: PendingSend | None) -> None:
        self.log, self.row = log, row

    async def lock_for_send(self, outbound_id: int) -> PendingSend | None:
        self.log.append("lock")
        return self.row

    async def mark_sending(self, outbound_id: int) -> None:
        self.log.append("mark_sending")

    async def mark_sent(self, outbound_id: int, wa_message_id: str) -> None:
        self.log.append(f"mark_sent:{wa_message_id}")

    async def mark_retry(self, outbound_id: int, error: str) -> None:
        self.log.append(f"mark_retry:{error}")

    async def mark_failed(self, outbound_id: int, reason: str) -> None:
        self.log.append(f"mark_failed:{reason}")


class FakeUnitOfWork:
    def __init__(self, log: Log, outbound: FakeOutbound, context: TenantContext) -> None:
        self.log, self.outbound, self.context = log, outbound, context

    async def __aenter__(self) -> Self:
        self.log.append("begin")
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.log.append("commit" if exc_type is None else "rollback")


class FakeSender:
    def __init__(self, log: Log, error: Exception | None = None) -> None:
        self.log, self.error = log, error
        self.calls: list[tuple[str, str, str, OutboundKind, dict[str, Any]]] = []

    async def send(
        self, phone_number_id: str, token: str, to: str, kind: OutboundKind, payload: dict[str, Any]
    ) -> str:
        self.log.append("meta")
        self.calls.append((phone_number_id, token, to, kind, payload))
        if self.error:
            raise self.error
        return "wamid.OUT"


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        return self.values.get(name)


def _setup(row: PendingSend | None, *, error: Exception | None = None, secrets: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    log = Log()
    outbound = FakeOutbound(log, row)
    contexts: list[TenantContext] = []

    def factory(context: TenantContext) -> FakeUnitOfWork:
        contexts.append(context)
        return FakeUnitOfWork(log, outbound, context)

    sender = FakeSender(log, error)
    handler = SendOutbound(
        unit_of_work=factory,  # type: ignore[arg-type]  # doble mínimo: solo expone outbound
        sender=sender,
        secrets=FakeSecrets({"WA_TOKEN_A": "tok"} if secrets is None else secrets),
    )
    return handler, log, sender, contexts


def _job(attempts: int = 1, max_attempts: int = 8) -> Job:
    return Job(
        id=9,
        kind="SEND_OUTBOUND",
        organization_id=ORG,
        conversation_key=None,
        payload={"outbound_id": 41},
        attempts=attempts,
        max_attempts=max_attempts,
    )


async def test_sending_se_confirma_antes_de_llamar_a_meta_y_sent_despues() -> None:
    handler, log, sender, contexts = _setup(_pending())

    await handler(_job())

    assert log == [
        "begin",
        "lock",
        "mark_sending",
        "commit",
        "meta",
        "begin",
        "mark_sent:wamid.OUT",
        "commit",
    ]
    assert sender.calls[0][:3] == ("106540352242922", "tok", "59170000001")
    assert contexts == [TenantContext(ORG, Actor.SYSTEM)] * 2


@pytest.mark.parametrize(
    "status", [OutboundStatus.SENT, OutboundStatus.DELIVERED, OutboundStatus.READ, OutboundStatus.FAILED]
)
async def test_un_mensaje_ya_resuelto_no_se_reenvia(status: OutboundStatus) -> None:
    handler, log, sender, _ = _setup(_pending(status=status))

    await handler(_job(attempts=2))

    assert sender.calls == []
    assert log == ["begin", "lock", "commit"]


async def test_si_el_worker_murio_durante_el_envio_no_se_reenvia_y_se_alerta(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="scz.outbox")
    handler, log, sender, _ = _setup(_pending(status=OutboundStatus.SENDING))

    await handler(_job(attempts=2))

    assert sender.calls == []  # mejor un mensaje en revisión que un mensaje duplicado
    assert "mark_failed:OUTCOME_UNKNOWN" in log
    assert "ALERTA outbound_outcome_unknown" in caplog.text


async def test_la_ventana_se_vuelve_a_mirar_al_enviar() -> None:
    handler, log, sender, _ = _setup(_pending(window_open=False))

    await handler(_job())

    assert sender.calls == []
    assert "mark_failed:WINDOW_CLOSED" in log


async def test_una_plantilla_sin_consentimiento_no_sale() -> None:
    handler, log, sender, _ = _setup(
        _pending(kind=OutboundKind.TEMPLATE, window_open=False, consent_ok=False)
    )

    await handler(_job())

    assert sender.calls == [] and "mark_failed:CONSENT_REQUIRED" in log


async def test_una_plantilla_con_consentimiento_sale_fuera_de_ventana() -> None:
    handler, _, sender, _ = _setup(_pending(kind=OutboundKind.TEMPLATE, window_open=False, consent_ok=True))

    await handler(_job())

    assert len(sender.calls) == 1


async def test_sin_token_no_se_marca_sending_y_se_reintenta() -> None:
    handler, log, sender, _ = _setup(_pending(), secrets={})

    with pytest.raises(LookupError):
        await handler(_job())

    assert "mark_sending" not in log and sender.calls == []


async def test_error_transitorio_vuelve_a_pending_y_se_reintenta() -> None:
    handler, log, _, _ = _setup(_pending(), error=TransientSendError("HTTP_503"))

    with pytest.raises(TransientSendError):
        await handler(_job())

    assert log[-3:] == ["begin", "mark_retry:HTTP_503", "commit"]


async def test_error_transitorio_en_el_ultimo_intento_queda_failed() -> None:
    handler, log, _, _ = _setup(_pending(), error=TransientSendError("HTTP_503"))

    with pytest.raises(TransientSendError):
        await handler(_job(attempts=8, max_attempts=8))

    assert "mark_failed:HTTP_503" in log


async def test_error_permanente_queda_failed_sin_reintentar() -> None:
    handler, log, _, _ = _setup(_pending(), error=PermanentSendError("META_131026"))

    await handler(_job())  # no lanza: reintentar no lo arreglaría

    assert log[-3:] == ["begin", "mark_failed:META_131026", "commit"]


async def test_mensaje_inexistente_o_de_otro_negocio_no_hace_nada() -> None:
    handler, log, sender, _ = _setup(None)

    await handler(_job())

    assert sender.calls == [] and log == ["begin", "lock", "commit"]
