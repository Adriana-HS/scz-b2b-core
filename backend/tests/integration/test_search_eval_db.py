"""Eval de relevancia de la búsqueda (#23, ADR-0003, ADR-0020): MRR@5 con y sin sinónimos.

Corre las consultas de evals/search/*.yaml contra el catálogo de los seeds, como app_backend y
con la misma búsqueda que usa el agente. Criterio del issue: MRR@5 ≥ 0,8 por rubro. Además
compara con la búsqueda sin sinónimos (la línea de base): los sinónimos tienen que mejorarla.
El resultado queda impreso (pytest -s) y en docs/TESTING.md.
"""

from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest
import yaml

from app.domain.catalog import CatalogSearch
from app.domain.tenancy import Actor, TenantContext
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

pytestmark = pytest.mark.integration

EVALS = Path(__file__).resolve().parents[3] / "evals" / "search"
TARGET = 0.8


def _suite(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((EVALS / f"{name}.yaml").read_text(encoding="utf-8"))
    return data


async def _mrr_at_5(pool: asyncpg.Pool, suite: dict[str, Any]) -> tuple[float, list[str]]:
    total, misses = 0.0, []
    async with unit_of_work_factory(pool)(TenantContext(UUID(suite["organization_id"]), Actor.BOT)) as uow:
        for case in suite["queries"]:
            found = [m.sku for m in await uow.catalog.search(CatalogSearch(query=case["query"], limit=5))]
            rank = next((i for i, sku in enumerate(found, start=1) if sku in case["relevant"]), None)
            total += 1 / rank if rank else 0.0
            if rank is None:
                misses.append(case["query"])
    return total / len(suite["queries"]), misses


async def _seeds_loaded(admin: asyncpg.Connection, suite: dict[str, Any]) -> bool:
    count: int = await admin.fetchval(
        "SELECT count(*) FROM public.catalog_items WHERE organization_id = $1", UUID(suite["organization_id"])
    )
    return count > 0


@pytest.mark.parametrize("name", ["importadora", "clinica"])
async def test_mrr_at_5_con_sinonimos_cumple_el_objetivo_y_mejora_la_base(
    pool: asyncpg.Pool, admin: asyncpg.Connection, name: str
) -> None:
    suite = _suite(name)
    if not await _seeds_loaded(admin, suite):
        pytest.skip("sin los seeds de #27 (supabase db reset los carga)")

    with_synonyms, misses = await _mrr_at_5(pool, suite)
    # Línea de base: la misma búsqueda sin sinónimos (se restauran al terminar)
    saved = await admin.fetch("SELECT vertical, group_key, term FROM public.search_synonyms")
    try:
        await admin.execute("DELETE FROM public.search_synonyms")
        baseline, _ = await _mrr_at_5(pool, suite)
    finally:
        await admin.execute("DELETE FROM public.search_synonyms")
        await admin.executemany(
            "INSERT INTO public.search_synonyms (vertical, group_key, term) VALUES ($1, $2, $3)",
            [(r["vertical"], r["group_key"], r["term"]) for r in saved],
        )

    print(  # noqa: T201 - el eval informa su resultado (pytest -s)
        f"\n{name}: MRR@5 sin sinónimos {baseline:.2f} -> con sinónimos {with_synonyms:.2f}; fallan: {misses}"
    )
    assert with_synonyms >= TARGET, f"MRR@5 {with_synonyms:.2f} < {TARGET}; fallan: {misses}"
    assert with_synonyms > baseline
