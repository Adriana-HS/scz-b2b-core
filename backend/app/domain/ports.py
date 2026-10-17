"""Puertos de persistencia (docs/architecture/principios-y-patrones.md §2.1, ADR-0011).

Todo acceso a datos ocurre dentro de una UnitOfWork: una transacción con el tenant y el
actor ya fijados en la BD. Los repositorios no existen fuera de ella.
"""

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from app.domain.catalog import CatalogMatch, CatalogSearch
from app.domain.messaging import InboundMessage, WhatsAppChannel
from app.domain.orders import Quote, QuoteRequest
from app.domain.tenancy import TenantContext


class CatalogRepository(Protocol):
    async def search(self, criteria: CatalogSearch) -> list[CatalogMatch]: ...


class OrderRepository(Protocol):
    async def create_quote(self, request: QuoteRequest) -> Quote: ...

    async def count_for_customer(self, customer_id: UUID) -> int: ...


class InboundRepository(Protocol):
    """Persistencia de lo que entra por el webhook (ADR-0005): todo en la misma transacción."""

    async def record_event(self, message: InboundMessage, phone_number_id: str) -> bool:
        """Registra el evento. False si ya existía (Meta lo reenvió): no hay que hacer nada más."""
        ...

    async def upsert_customer(self, wa_id: str, profile_name: str | None, seen_at: datetime) -> UUID: ...

    async def open_conversation(self, customer_id: UUID, seen_at: datetime) -> UUID:
        """La conversación abierta del cliente (una sola por cliente), o una nueva."""
        ...

    async def add_message(self, conversation_id: UUID, message: InboundMessage) -> None: ...

    async def enqueue_conversation(self, conversation_id: UUID) -> None:
        """Encola (o pospone, para agrupar ráfagas) el turno del agente para esa conversación."""
        ...


class ChannelResolver(Protocol):
    """La única lectura previa a conocer el tenant (ADR-0002 §6): phone_number_id -> negocio."""

    async def resolve(self, phone_number_id: str) -> WhatsAppChannel | None: ...


class UnitOfWork(Protocol):
    """Una transacción con tenant y actor. Confirma al salir sin error; si no, revierte."""

    @property
    def context(self) -> TenantContext: ...

    @property
    def catalog(self) -> CatalogRepository: ...

    @property
    def orders(self) -> OrderRepository: ...

    @property
    def inbound(self) -> InboundRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Lo que reciben los casos de uso: una forma de abrir unidades de trabajo por tenant."""

    def __call__(self, context: TenantContext) -> UnitOfWork: ...
