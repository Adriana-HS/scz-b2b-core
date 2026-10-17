"""Pool de conexiones del rol `app_backend` (ADR-0002, ADR-0011)."""

import asyncpg


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Crea el pool. Apto para Supavisor en modo transacción:

    * `statement_cache_size=0`: sin prepared statements con nombre, que el pooler no
      puede garantizar entre transacciones (cada una puede ir a otra conexión física).
    * El tenant y el actor NO se fijan aquí: se fijan por transacción con `SET LOCAL`
      (set_config(..., true)) dentro de cada UnitOfWork, y mueren con el COMMIT.
    """
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        statement_cache_size=0,
        max_inactive_connection_lifetime=60,
        command_timeout=10,
        server_settings={"application_name": "scz-b2b-core"},
    )
    if pool is None:  # pragma: no cover - asyncpg solo devuelve None sin await
        raise RuntimeError("asyncpg.create_pool no devolvió un pool")
    return pool
