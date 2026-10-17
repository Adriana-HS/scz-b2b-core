"""Integración contra el Postgres local de Supabase (`supabase db start`, migraciones aplicadas).

Se conecta como administrador local solo para preparar datos sintéticos y habilitar el login
de `app_backend` con una contraseña aleatoria de esta corrida. Las pruebas usan `app_backend`,
igual que el backend real: RLS, FSM y triggers se aplican de verdad.

Si la BD no está disponible, estas pruebas se omiten con un aviso.
"""

import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.infrastructure.postgres.pool import create_pool

# Credenciales por defecto del Postgres LOCAL de Supabase CLI (no son secretos; nunca apuntar a staging).
ADMIN_DSN = os.environ.get(
    "SCZ_TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


@dataclass(frozen=True)
class Seed:
    org_a: UUID
    org_b: UUID
    customer_a: UUID
    conversation_a: UUID
    item_bob_a: UUID  # 480,00 Bs, stock 10
    item_usd_a: UUID  # 25,00 USD, stock 3, TC 11,68
    item_b: UUID
    sku_bob_a: str
    sku_usd_a: str


def _backend_dsn(password: str) -> str:
    parts = urlsplit(ADMIN_DSN)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"app_backend:{password}@{host}{port}"))


@pytest.fixture(scope="session")
async def admin() -> AsyncIterator[asyncpg.Connection]:
    host = urlsplit(ADMIN_DSN).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("SCZ_TEST_DATABASE_ADMIN_URL debe apuntar a una BD local, nunca a staging o producción")
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        reason = f"BD local no disponible ({exc.__class__.__name__}); corre `supabase db start`"
        if os.environ.get("SCZ_REQUIRE_DB") == "1":  # en la CI, omitir sería un falso verde
            pytest.fail(reason)
        pytest.skip(reason)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def backend_dsn(admin: asyncpg.Connection) -> AsyncIterator[str]:
    password = secrets.token_urlsafe(24)
    literal = await admin.fetchval("SELECT quote_literal($1)", password)
    await admin.execute(f"ALTER ROLE app_backend WITH LOGIN PASSWORD {literal}")
    try:
        yield _backend_dsn(password)
    finally:
        await admin.execute("ALTER ROLE app_backend WITH NOLOGIN PASSWORD NULL")


@pytest.fixture(scope="session")
async def pool(backend_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    created = await create_pool(backend_dsn, min_size=1, max_size=4)
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture(scope="session")
async def seed(admin: asyncpg.Connection) -> Seed:
    """Dos tenants sintéticos, con identificadores únicos por corrida."""
    s = Seed(
        org_a=uuid4(), org_b=uuid4(), customer_a=uuid4(), conversation_a=uuid4(),
        item_bob_a=uuid4(), item_usd_a=uuid4(), item_b=uuid4(),
        sku_bob_a=f"AMORT-{secrets.token_hex(3)}", sku_usd_a=f"OEM-{secrets.token_hex(3)}",
    )  # fmt: skip
    wa_id = "591" + "".join(secrets.choice("0123456789") for _ in range(8))
    async with admin.transaction():
        await admin.execute(
            """INSERT INTO public.organizations (id, name, vertical, deposit_percent) VALUES
                   ($1, 'Integración A', 'IMPORTADORA_REPUESTOS', 50),
                   ($2, 'Integración B', 'IMPORTADORA_REPUESTOS', 0)""",
            s.org_a, s.org_b,
        )  # fmt: skip
        await admin.execute(
            """INSERT INTO public.exchange_rates (organization_id, rate_bs_per_usd, source, effective_at)
               VALUES ($1, 11.6800, 'BCB_OFICIAL', now() - interval '1 hour')""",
            s.org_a,
        )
        await admin.execute(
            """INSERT INTO public.catalog_items (id, organization_id, item_type, sku, name, category,
                                                 price_currency, list_price, stock_on_hand) VALUES
                   ($1, $4, 'PRODUCT', $6, 'Amortiguador trasero integración', 'Suspensión',
                    'BOB', 480.00, 10),
                   ($2, $4, 'PRODUCT', $7, 'Pastilla de freno integración', 'Frenos',
                    'USD', 25.00, 3),
                   ($3, $5, 'PRODUCT', 'B-' || $6, 'Amortiguador trasero del tenant B', 'Suspensión',
                    'BOB', 1, 1)""",
            s.item_bob_a, s.item_usd_a, s.item_b, s.org_a, s.org_b, s.sku_bob_a, s.sku_usd_a,
        )  # fmt: skip
        await admin.execute(
            "INSERT INTO public.customers (id, organization_id, wa_id, display_name)"
            " VALUES ($1, $2, $3, 'Cliente sintético')",
            s.customer_a, s.org_a, wa_id,
        )  # fmt: skip
        await admin.execute(
            "INSERT INTO public.conversations (id, organization_id, customer_id) VALUES ($1, $2, $3)",
            s.conversation_a, s.org_a, s.customer_a,
        )  # fmt: skip
    return s
