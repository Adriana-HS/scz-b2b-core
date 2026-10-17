"""Caso de uso RecordInboundMessages con dobles (sin BD)."""

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from app.application.inbound import RecordInboundMessages
from app.domain.consent import CONFIRMATION_TEXT, ConsentKind, ConsentSource
from app.domain.messaging import InboundBatch, InboundMessage, WhatsAppChannel
from app.domain.outbound import OutboundKind, OutboundRequest
from app.domain.tenancy import Actor, TenantContext

ORG = uuid4()
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _msg(wamid: str, sender: str = "59170000001", text: str = "hola") -> InboundMessage:
    return InboundMessage(wamid, sender, "Cliente", "text", text, NOW)


class FakeInbound:
    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.calls: list[tuple[str, object]] = []
        self.customers: dict[str, UUID] = {}

    async def record_event(self, message: InboundMessage, phone_number_id: str) -> bool:
        self.calls.append(("event", message.wa_message_id))
        if message.wa_message_id in self.known:
            return False
        self.known.add(message.wa_message_id)
        return True

    async def upsert_customer(self, wa_id: str, profile_name: str | None, seen_at: datetime) -> UUID:
        self.calls.append(("customer", wa_id))
        return self.customers.setdefault(wa_id, uuid4())

    async def open_conversation(self, customer_id: UUID, seen_at: datetime, channel_id: UUID) -> UUID:
        self.calls.append(("conversation", customer_id))
        return customer_id  # una conversación por cliente

    async def add_message(self, conversation_id: UUID, message: InboundMessage) -> None:
        self.calls.append(("message", message.wa_message_id))

    async def enqueue_conversation(self, conversation_id: UUID) -> None:
        self.calls.append(("enqueue", conversation_id))


class FakeConsent:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    async def record(
        self, customer_id: UUID, kind: ConsentKind, source: ConsentSource, evidence: str | None
    ) -> None:
        self.calls.append(("consent", (kind, source, evidence)))


class FakeOutbound:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls
        self.requests: list[OutboundRequest] = []

    async def enqueue(self, request: OutboundRequest) -> int | None:
        self.calls.append(("reply", request.idempotency_key))
        self.requests.append(request)
        return 1


class FakeUnitOfWork:
    def __init__(self, context: TenantContext, inbound: FakeInbound) -> None:
        self.context = context
        self.inbound = inbound
        self.consent = FakeConsent(inbound.calls)
        self.outbound = FakeOutbound(inbound.calls)
        inbound.outbound = self.outbound  # type: ignore[attr-defined]

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None


class FakeResolver:
    def __init__(self, channel: WhatsAppChannel | None) -> None:
        self.channel = channel

    async def resolve(self, phone_number_id: str) -> WhatsAppChannel | None:
        return self.channel


def _use_case(
    channel: WhatsAppChannel | None, inbound: FakeInbound, contexts: list[TenantContext]
) -> RecordInboundMessages:
    def factory(context: TenantContext) -> FakeUnitOfWork:
        contexts.append(context)
        return FakeUnitOfWork(context, inbound)

    return RecordInboundMessages(resolver=FakeResolver(channel), unit_of_work=factory)  # type: ignore[arg-type]


CHANNEL = WhatsAppChannel(ORG, uuid4(), "vault:wa_token")


async def test_registra_evento_cliente_conversacion_mensaje_y_encola_en_ese_orden() -> None:
    inbound = FakeInbound(set())
    contexts: list[TenantContext] = []
    result = await _use_case(CHANNEL, inbound, contexts)(InboundBatch("PNID-1", (_msg("wamid.1"),)))

    assert result.accepted == 1 and result.duplicates == 0
    assert [kind for kind, _ in inbound.calls] == ["event", "customer", "conversation", "message", "enqueue"]
    assert contexts == [TenantContext(ORG, Actor.SYSTEM)]  # el webhook actúa como SYSTEM, nunca como BOT


async def test_un_duplicado_no_crea_nada_mas() -> None:
    inbound = FakeInbound({"wamid.1"})
    contexts: list[TenantContext] = []
    result = await _use_case(CHANNEL, inbound, contexts)(InboundBatch("PNID-1", (_msg("wamid.1"),)))

    assert result.duplicates == 1 and result.accepted == 0
    assert inbound.calls == [("event", "wamid.1")]


async def test_numero_desconocido_no_abre_transaccion() -> None:
    inbound = FakeInbound(set())
    contexts: list[TenantContext] = []
    result = await _use_case(None, inbound, contexts)(InboundBatch("PNID-X", (_msg("wamid.1"),)))

    assert result.unknown_channel
    assert contexts == [] and inbound.calls == []


async def test_un_lote_usa_una_sola_unidad_de_trabajo() -> None:
    inbound = FakeInbound(set())
    contexts: list[TenantContext] = []
    batch = InboundBatch("PNID-1", (_msg("wamid.1"), _msg("wamid.2"), _msg("wamid.3", sender="59170000002")))

    result = await _use_case(CHANNEL, inbound, contexts)(batch)

    assert result.accepted == 3
    assert len(contexts) == 1


async def test_baja_registra_la_evidencia_confirma_y_no_despierta_al_agente() -> None:
    inbound = FakeInbound(set())
    contexts: list[TenantContext] = []

    result = await _use_case(CHANNEL, inbound, contexts)(
        InboundBatch("PNID-1", (_msg("wamid.b", text="BAJA"),))
    )

    kinds = [kind for kind, _ in inbound.calls]
    assert kinds == ["event", "customer", "conversation", "message", "consent", "reply"]  # sin "enqueue"
    assert ("consent", (ConsentKind.OPTOUT, ConsentSource.WHATSAPP_KEYWORD, "wamid.b")) in inbound.calls
    [reply] = inbound.outbound.requests  # type: ignore[attr-defined]
    assert reply.kind is OutboundKind.TEXT and reply.idempotency_key == "consent:wamid.b"
    assert reply.payload["body"] == CONFIRMATION_TEXT[ConsentKind.OPTOUT]
    assert (result.accepted, result.consent_changes) == (1, 1)


async def test_alta_registra_opt_in_de_utilidad() -> None:
    inbound = FakeInbound(set())

    await _use_case(CHANNEL, inbound, [])(InboundBatch("PNID-1", (_msg("wamid.a", text="Alta"),)))

    assert (
        "consent",
        (ConsentKind.OPTIN_UTILITY, ConsentSource.WHATSAPP_KEYWORD, "wamid.a"),
    ) in inbound.calls


async def test_un_pedido_que_menciona_no_mas_sigue_al_agente() -> None:
    inbound = FakeInbound(set())

    await _use_case(CHANNEL, inbound, [])(
        InboundBatch("PNID-1", (_msg("wamid.p", text="no más frenos, quiero cotizar pastillas"),))
    )

    kinds = [kind for kind, _ in inbound.calls]
    assert "consent" not in kinds and kinds[-1] == "enqueue"
