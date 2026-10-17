"""Caso de uso RecordInboundMessages con dobles (sin BD)."""

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from app.application.inbound import RecordInboundMessages
from app.domain.messaging import InboundBatch, InboundMessage, WhatsAppChannel
from app.domain.tenancy import Actor, TenantContext

ORG = uuid4()
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _msg(wamid: str, sender: str = "59170000001") -> InboundMessage:
    return InboundMessage(wamid, sender, "Cliente", "text", "hola", NOW)


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


class FakeUnitOfWork:
    def __init__(self, context: TenantContext, inbound: FakeInbound) -> None:
        self.context = context
        self.inbound = inbound

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
