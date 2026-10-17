"""UnitOfWork sobre asyncpg: una transacción con tenant y actor fijados (ADR-0011).

Ciclo de vida (una sola vez por instancia):
  acquire -> BEGIN -> set_config(tenant, actor, local) -> repositorios -> COMMIT | ROLLBACK -> release

`set_config(..., true)` es el `SET LOCAL` parametrizado: el valor viaja como parámetro (sin
riesgo de inyección) y desaparece al terminar la transacción, también con Supavisor en modo
transacción, donde la siguiente transacción puede caer en la misma conexión física.
"""

from types import TracebackType
from typing import Literal, Self

import asyncpg
from asyncpg.pool import PoolConnectionProxy
from asyncpg.transaction import Transaction

from app.core.telemetry import current_traceparent
from app.domain.errors import UnitOfWorkNotActiveError
from app.domain.ports import UnitOfWorkFactory
from app.domain.tenancy import TenantContext
from app.infrastructure.postgres.consent import PostgresConsentRepository
from app.infrastructure.postgres.inbound import PostgresInboundRepository
from app.infrastructure.postgres.outbound import PostgresOutboundRepository
from app.infrastructure.postgres.repositories import PostgresCatalogRepository, PostgresOrderRepository

# app.traceparent: la BD lo guarda en jobs.trace_context de cada trabajo creado en esta transacción
_SET_CONTEXT = (
    "SELECT set_config('app.current_org_id', $1, true), set_config('app.actor', $2, true),"
    " set_config('app.traceparent', $3, true)"
)

_State = Literal["new", "active", "closed"]

# Conexión prestada por el pool: se devuelve con pool.release() al cerrar la UnitOfWork.
# `type` se evalúa de forma diferida: en ejecución PoolConnectionProxy no admite subíndices.
type Connection = PoolConnectionProxy[asyncpg.Record]


class Session:
    """Conexión de la transacción en curso. Los repositorios solo la usan mientras esté activa."""

    def __init__(self) -> None:
        self._connection: Connection | None = None

    def bind(self, connection: Connection) -> None:
        self._connection = connection

    def close(self) -> None:
        self._connection = None

    def connection(self) -> Connection:
        if self._connection is None:
            raise UnitOfWorkNotActiveError("repositorio usado fuera de su UnitOfWork (ADR-0011)")
        return self._connection


class PostgresUnitOfWork:
    def __init__(self, pool: asyncpg.Pool, context: TenantContext) -> None:
        self._pool = pool
        self._context = context
        self._state: _State = "new"
        self._session = Session()
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self._catalog = PostgresCatalogRepository(self._session)
        self._orders = PostgresOrderRepository(self._session)
        self._inbound = PostgresInboundRepository(self._session)
        self._outbound = PostgresOutboundRepository(self._session)
        self._consent = PostgresConsentRepository(self._session)

    @property
    def context(self) -> TenantContext:
        return self._context

    @property
    def catalog(self) -> PostgresCatalogRepository:
        self._require_active()
        return self._catalog

    @property
    def orders(self) -> PostgresOrderRepository:
        self._require_active()
        return self._orders

    @property
    def inbound(self) -> PostgresInboundRepository:
        self._require_active()
        return self._inbound

    @property
    def outbound(self) -> PostgresOutboundRepository:
        self._require_active()
        return self._outbound

    @property
    def consent(self) -> PostgresConsentRepository:
        self._require_active()
        return self._consent

    async def __aenter__(self) -> Self:
        if self._state != "new":
            raise UnitOfWorkNotActiveError("una UnitOfWork se usa una sola vez; abre otra")
        self._state = "closed"  # queda inutilizable si algo falla antes de terminar de abrirla
        connection: Connection = await self._pool.acquire()
        self._connection = connection
        transaction = connection.transaction()
        self._transaction = transaction
        try:
            await transaction.start()
            await connection.execute(
                _SET_CONTEXT,
                str(self._context.organization_id),
                self._context.actor.value,
                current_traceparent(),
            )
        except BaseException:
            await self._finish(commit=False)
            raise
        self._session.bind(connection)
        self._state = "active"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._finish(commit=exc_type is None)

    async def _finish(self, *, commit: bool) -> None:
        self._session.close()
        self._state = "closed"
        try:
            if self._transaction is not None:
                if commit:
                    await self._transaction.commit()
                else:
                    await self._transaction.rollback()
        finally:
            if self._connection is not None:
                await self._pool.release(self._connection)
            self._transaction = None
            self._connection = None

    def _require_active(self) -> None:
        if self._state != "active":
            raise UnitOfWorkNotActiveError("los repositorios solo existen dentro de una UnitOfWork activa")


def unit_of_work_factory(pool: asyncpg.Pool) -> UnitOfWorkFactory:
    """Lo que el composition root entrega a los casos de uso."""

    def open_unit_of_work(context: TenantContext) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(pool, context)

    return open_unit_of_work
