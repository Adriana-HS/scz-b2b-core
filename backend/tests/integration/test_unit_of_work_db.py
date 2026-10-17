"""UnitOfWork y repositorios contra Postgres real, como `app_backend` (ADR-0002, ADR-0011)."""

from decimal import Decimal
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from app.domain.catalog import CatalogSearch, PriceStatus, TaxMode
from app.domain.errors import AccessDeniedError, BusinessRuleError, ErrorCode, UnitOfWorkNotActiveError
from app.domain.orders import QuoteLine, QuoteRequest
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.pool import create_pool
from app.infrastructure.postgres.unit_of_work import PostgresUnitOfWork

from .conftest import Seed

pytestmark = pytest.mark.integration


def _quote(seed: Seed, *lines: tuple[object, int]) -> QuoteRequest:
    return QuoteRequest(
        customer_id=seed.customer_a,
        conversation_id=seed.conversation_a,
        lines=tuple(QuoteLine.model_validate({"item_id": i, "quantity": q}) for i, q in lines),
    )


async def test_la_busqueda_solo_ve_el_catalogo_del_propio_tenant(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        found_a = await uow.catalog.search(CatalogSearch(query="amortiguador trasero"))
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_b, Actor.BOT)) as uow:
        found_b = await uow.catalog.search(CatalogSearch(query="amortiguador trasero"))

    assert seed.item_bob_a in {m.item_id for m in found_a}
    assert seed.item_b not in {m.item_id for m in found_a}
    assert {m.item_id for m in found_b} == {seed.item_b}


async def test_el_precio_de_la_busqueda_lo_calcula_la_bd(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        [match] = await uow.catalog.search(CatalogSearch(query=seed.sku_usd_a, limit=1))

    assert match.unit_price_bs == Decimal("292.00")  # round(25,00 x 11,68)
    assert isinstance(match.unit_price_bs, Decimal)
    assert match.price_status is PriceStatus.OK
    assert match.tax_mode is TaxMode.IVA_INCLUIDO


async def test_la_cotizacion_devuelve_los_montos_de_la_bd_en_decimal(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        quote = await uow.orders.create_quote(_quote(seed, (seed.item_bob_a, 2)))

    assert quote.total_bs == Decimal("960.00")
    assert quote.deposit_required_bs == Decimal("480.00")  # seña 50 % del tenant
    assert quote.iva_bs == Decimal("124.80")  # 13 % del total (IVA incluido)
    assert quote.reference_code.startswith("COT-")
    assert quote.valid_until.tzinfo is not None


@pytest.mark.parametrize(
    ("item", "quantity", "code"),
    [("item_usd_a", 4, ErrorCode.OUT_OF_STOCK), ("item_b", 1, ErrorCode.ITEM_NOT_FOUND)],
)
async def test_los_hint_de_la_rpc_llegan_como_errores_de_dominio(
    pool: asyncpg.Pool, seed: Seed, item: str, quantity: int, code: ErrorCode
) -> None:
    with pytest.raises(BusinessRuleError) as info:
        async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
            await uow.orders.create_quote(_quote(seed, (getattr(seed, item), quantity)))

    assert info.value.code is code


async def test_un_item_repetido_lo_rechaza_la_bd(pool: asyncpg.Pool, seed: Seed) -> None:
    with pytest.raises(BusinessRuleError) as info:
        async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
            await uow.orders.create_quote(_quote(seed, (seed.item_bob_a, 1), (seed.item_bob_a, 1)))

    assert info.value.code is ErrorCode.DUPLICATE_ITEM


async def test_un_error_revierte_toda_la_unidad_de_trabajo(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        before = await uow.orders.count_for_customer(seed.customer_a)

    with pytest.raises(BusinessRuleError):
        async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
            await uow.orders.create_quote(_quote(seed, (seed.item_bob_a, 1)))  # esta sí se crea...
            await uow.orders.create_quote(_quote(seed, (seed.item_usd_a, 999)))  # ...y esta falla

    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        assert await uow.orders.count_for_customer(seed.customer_a) == before


async def test_el_actor_y_el_tenant_quedan_fijados_en_la_transaccion(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.SYSTEM)) as uow:
        conn = uow._session.connection()  # solo para verificar el contexto en la BD
        row = await conn.fetchrow("SELECT private.backend_org_id() AS org, private.actor_kind() AS actor")

    assert row is not None
    assert (row["org"], row["actor"]) == (seed.org_a, "SYSTEM")


class TransactionModePooler:
    """Emula Supavisor en modo transacción: entrega SIEMPRE la misma conexión física y, al
    devolverla, no ejecuta RESET ALL. (El pool de asyncpg sí hace RESET ALL al liberar,
    y por eso no sirve para probar esto: ocultaría una filtración.)"""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection

    async def acquire(self) -> asyncpg.Connection:
        return self.connection

    async def release(self, connection: asyncpg.Connection) -> None:
        return None


async def _session_settings(conn: asyncpg.Connection) -> tuple[object, object]:
    row = await conn.fetchrow(
        "SELECT current_setting('app.current_org_id', true) AS org,"
        " current_setting('app.actor', true) AS actor"
    )
    assert row is not None
    return row["org"], row["actor"]


async def test_el_contexto_no_se_filtra_a_la_siguiente_transaccion(backend_dsn: str, seed: Seed) -> None:
    """La transacción siguiente en la misma conexión física (otro cliente del pooler) no hereda
    el tenant ni el actor, ni después de un COMMIT ni después de un ROLLBACK."""
    conn = await asyncpg.connect(backend_dsn, statement_cache_size=0)
    pooler = cast(asyncpg.Pool, TransactionModePooler(conn))
    try:
        async with PostgresUnitOfWork(pooler, TenantContext(seed.org_a, Actor.SYSTEM)) as uow:
            await uow.catalog.search(CatalogSearch(query="amortiguador"))
        after_commit = await _session_settings(conn)
        visible_after_commit = await conn.fetchval("SELECT count(*) FROM public.catalog_items")

        with pytest.raises(RuntimeError):
            async with PostgresUnitOfWork(pooler, TenantContext(seed.org_a, Actor.SYSTEM)):
                raise RuntimeError("falla a mitad de la transacción")
        after_rollback = await _session_settings(conn)
    finally:
        await conn.close()

    assert after_commit[0] in (None, "") and after_commit[1] in (None, "")
    assert after_rollback[0] in (None, "") and after_rollback[1] in (None, "")
    assert visible_after_commit == 0  # sin tenant fijado, app_backend no ve nada (falla cerrado)


async def test_el_siguiente_tenant_en_la_misma_conexion_solo_ve_lo_suyo(backend_dsn: str, seed: Seed) -> None:
    conn = await asyncpg.connect(backend_dsn, statement_cache_size=0)
    pooler = cast(asyncpg.Pool, TransactionModePooler(conn))
    try:
        async with PostgresUnitOfWork(pooler, TenantContext(seed.org_a, Actor.BOT)) as uow:
            found_a = await uow.catalog.search(CatalogSearch(query="amortiguador trasero"))
        async with PostgresUnitOfWork(pooler, TenantContext(seed.org_b, Actor.BOT)) as uow:
            found_b = await uow.catalog.search(CatalogSearch(query="amortiguador trasero"))
    finally:
        await conn.close()

    assert seed.item_bob_a in {m.item_id for m in found_a}
    assert {m.item_id for m in found_b} == {seed.item_b}


async def test_sin_tenant_la_rpc_se_niega(backend_dsn: str) -> None:
    single = await create_pool(backend_dsn, min_size=1, max_size=1)
    try:
        async with PostgresUnitOfWork(single, TenantContext(uuid4(), Actor.BOT)) as uow:
            conn = uow._session.connection()
            await conn.execute("SELECT set_config('app.current_org_id', '', true)")
            with pytest.raises(AccessDeniedError) as info:
                await uow.catalog.search(CatalogSearch(query="freno"))
    finally:
        await single.close()

    assert info.value.code is ErrorCode.TENANT_REQUIRED


async def test_un_repositorio_guardado_no_sirve_despues_del_commit(pool: asyncpg.Pool, seed: Seed) -> None:
    async with PostgresUnitOfWork(pool, TenantContext(seed.org_a, Actor.BOT)) as uow:
        orders = uow.orders

    with pytest.raises(UnitOfWorkNotActiveError):
        await orders.count_for_customer(seed.customer_a)
