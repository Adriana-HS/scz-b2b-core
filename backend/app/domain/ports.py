"""Puertos de persistencia (docs/architecture/principios-y-patrones.md §2.1, ADR-0011).

Todo acceso a datos ocurre dentro de una UnitOfWork: una transacción con el tenant y el
actor ya fijados en la BD. Los repositorios no existen fuera de ella.
"""

from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from app.domain.catalog import CatalogMatch, CatalogSearch
from app.domain.orders import Quote, QuoteRequest
from app.domain.tenancy import TenantContext


class CatalogRepository(Protocol):
    async def search(self, criteria: CatalogSearch) -> list[CatalogMatch]: ...


class OrderRepository(Protocol):
    async def create_quote(self, request: QuoteRequest) -> Quote: ...

    async def count_for_customer(self, customer_id: UUID) -> int: ...


class UnitOfWork(Protocol):
    """Una transacción con tenant y actor. Confirma al salir sin error; si no, revierte."""

    @property
    def context(self) -> TenantContext: ...

    @property
    def catalog(self) -> CatalogRepository: ...

    @property
    def orders(self) -> OrderRepository: ...

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
