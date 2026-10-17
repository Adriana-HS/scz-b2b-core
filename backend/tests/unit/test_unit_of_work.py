"""Ciclo de vida de PostgresUnitOfWork con dobles de prueba (sin BD).

La integración real contra Postgres está en tests/integration/test_unit_of_work_db.py.
"""

from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from app.domain.catalog import CatalogSearch
from app.domain.errors import UnitOfWorkNotActiveError
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork, unit_of_work_factory

ORG = uuid4()


class FakeTransaction:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def start(self) -> None:
        self.log.append("begin")

    async def commit(self) -> None:
        self.log.append("commit")

    async def rollback(self) -> None:
        self.log.append("rollback")


class FakeConnection:
    def __init__(self, log: list[str], *, fail_on_execute: bool = False) -> None:
        self.log = log
        self.fail_on_execute = fail_on_execute
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self.log)

    async def execute(self, query: str, *args: Any) -> str:
        self.log.append("set_config")
        self.executed.append((query, args))
        if self.fail_on_execute:
            raise ConnectionError("conexión caída")
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.log.append("fetch")
        return []


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.released = 0

    async def acquire(self) -> FakeConnection:
        self.conn.log.append("acquire")
        return self.conn

    async def release(self, conn: FakeConnection) -> None:
        self.conn.log.append("release")
        self.released += 1


def _uow(pool: FakePool, actor: Actor = Actor.BOT) -> PostgresUnitOfWork:
    return PostgresUnitOfWork(cast(asyncpg.Pool, pool), TenantContext(ORG, actor))


async def test_fija_tenant_y_actor_antes_de_cualquier_consulta() -> None:
    log: list[str] = []
    conn = FakeConnection(log)

    async with _uow(FakePool(conn), Actor.SYSTEM) as uow:
        await uow.catalog.search(CatalogSearch(query="freno"))

    assert log == ["acquire", "begin", "set_config", "fetch", "commit", "release"]
    query, args = conn.executed[0]
    assert "set_config('app.current_org_id', $1, true)" in query
    assert "set_config('app.actor', $2, true)" in query
    assert args == (str(ORG), "SYSTEM")


async def test_un_error_revierte_y_libera_la_conexion() -> None:
    log: list[str] = []
    pool = FakePool(FakeConnection(log))

    with pytest.raises(ValueError, match="falla del caso de uso"):
        async with _uow(pool):
            raise ValueError("falla del caso de uso")

    assert log[-2:] == ["rollback", "release"]
    assert "commit" not in log


async def test_si_falla_fijar_el_contexto_la_conexion_se_libera() -> None:
    log: list[str] = []
    pool = FakePool(FakeConnection(log, fail_on_execute=True))

    with pytest.raises(ConnectionError):
        async with _uow(pool):
            pass  # pragma: no cover - no se llega

    assert pool.released == 1
    assert log[-2:] == ["rollback", "release"]


async def test_repositorio_fuera_de_su_unidad_de_trabajo_falla() -> None:
    uow = _uow(FakePool(FakeConnection([])))
    async with uow:
        catalog = uow.catalog

    with pytest.raises(UnitOfWorkNotActiveError):
        await catalog.search(CatalogSearch(query="freno"))


async def test_no_hay_repositorios_antes_de_abrir_la_unidad_de_trabajo() -> None:
    with pytest.raises(UnitOfWorkNotActiveError):
        _ = _uow(FakePool(FakeConnection([]))).orders


async def test_una_unidad_de_trabajo_no_se_reutiliza() -> None:
    uow = _uow(FakePool(FakeConnection([])))
    async with uow:
        pass

    with pytest.raises(UnitOfWorkNotActiveError):
        async with uow:
            pass  # pragma: no cover - no se llega


async def test_la_fabrica_crea_una_unidad_por_contexto() -> None:
    factory = unit_of_work_factory(cast(asyncpg.Pool, FakePool(FakeConnection([]))))
    context = TenantContext(ORG, Actor.BOT)

    uow = factory(context)

    assert isinstance(uow, PostgresUnitOfWork)
    assert uow.context == context
