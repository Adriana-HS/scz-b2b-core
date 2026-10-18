"""Herramientas del agente contra Postgres real como `app_backend` (#24, §7.2)."""

import json
from typing import Any

import asyncpg
import pytest

from app.application.agent_tools import AgentToolbox, TurnContext
from app.domain.ai import ToolCall
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory

from .conftest import Seed

pytestmark = pytest.mark.integration


def _toolbox(pool: asyncpg.Pool, seed: Seed) -> AgentToolbox:
    context = TurnContext(seed.org_a, seed.customer_a, seed.conversation_a)
    return AgentToolbox(unit_of_work_factory(pool), context)


async def _run(toolbox: AgentToolbox, name: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await toolbox.execute(ToolCall(id="t1", name=name, arguments=arguments))
    data: dict[str, Any] = json.loads(result.content)
    return result.is_error, data


async def test_buscar_trae_el_precio_que_calcula_la_bd(pool: asyncpg.Pool, seed: Seed) -> None:
    is_error, data = await _run(_toolbox(pool, seed), "search_catalog", {"query": seed.sku_usd_a})

    assert not is_error
    item = next(i for i in data["items"] if i["item_id"] == str(seed.item_usd_a))
    assert item["unit_price_bs"] == "292.00"  # 25 USD por 11,68 Bs/USD


async def test_la_ficha_coincide_con_la_busqueda(pool: asyncpg.Pool, seed: Seed) -> None:
    toolbox = _toolbox(pool, seed)

    _, found = await _run(toolbox, "search_catalog", {"query": seed.sku_bob_a})
    _, details = await _run(toolbox, "get_item_details", {"item_id": str(seed.item_bob_a)})

    match = next(i for i in found["items"] if i["item_id"] == str(seed.item_bob_a))
    assert details["item"]["unit_price_bs"] == match["unit_price_bs"] == "480.00"
    assert details["item"]["stock_available"] == match["stock_available"]


async def test_un_item_de_otro_negocio_no_existe(pool: asyncpg.Pool, seed: Seed) -> None:
    toolbox = _toolbox(pool, seed)

    is_error, data = await _run(toolbox, "get_item_details", {"item_id": str(seed.item_b)})
    assert is_error and data["error"] == "ITEM_NOT_FOUND"

    is_error, data = await _run(
        toolbox, "create_quote", {"items": [{"item_id": str(seed.item_b), "quantity": 1}]}
    )
    assert is_error and data["error"] in {"ITEM_NOT_FOUND", "INVALID_ITEMS"} and data["customer_message"]


async def test_cotizar_reserva_stock_para_el_cliente_del_contexto(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    toolbox = _toolbox(pool, seed)
    _, before = await _run(toolbox, "get_item_details", {"item_id": str(seed.item_bob_a)})

    is_error, quote = await _run(
        toolbox, "create_quote", {"items": [{"item_id": str(seed.item_bob_a), "quantity": 1}]}
    )

    assert not is_error, quote
    assert quote["reference_code"].startswith("COT-")
    row = await admin.fetchrow(
        "SELECT customer_id, conversation_id, total_bs FROM public.orders WHERE reference_code = $1"
        " AND organization_id = $2",
        quote["reference_code"],
        seed.org_a,
    )
    assert row is not None and (row["customer_id"], row["conversation_id"]) == (
        seed.customer_a,
        seed.conversation_a,
    )
    assert str(row["total_bs"]) == quote["total_bs"]  # la cifra que recibe el modelo es la de la BD
    _, after = await _run(toolbox, "get_item_details", {"item_id": str(seed.item_bob_a)})
    assert after["item"]["stock_available"] == before["item"]["stock_available"] - 1


async def test_sin_stock_suficiente_hay_un_mensaje_para_el_cliente(pool: asyncpg.Pool, seed: Seed) -> None:
    is_error, data = await _run(
        _toolbox(pool, seed),
        "create_quote",
        {"items": [{"item_id": str(seed.item_usd_a), "quantity": 9_999}]},
    )

    assert is_error and data["error"] == "OUT_OF_STOCK" and data["customer_message"]


async def test_pasar_a_una_persona(pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed) -> None:
    is_error, data = await _run(
        _toolbox(pool, seed),
        "handoff_to_human",
        {"reason": "COMPLAINT", "summary": "Reclamo por una entrega."},
    )

    assert not is_error and data == {"ok": True}
    row = await admin.fetchrow(
        "SELECT mode::text, handoff_reason, handoff_summary FROM public.conversations WHERE id = $1",
        seed.conversation_a,
    )
    assert row is not None and tuple(row) == ("HUMAN", "COMPLAINT", "Reclamo por una entrega.")
    await admin.execute("UPDATE public.conversations SET mode = 'BOT' WHERE id = $1", seed.conversation_a)


# --- Cotizar por código (#89) ---------------------------------------------------------------------
async def test_cotizar_por_codigo_usa_el_item_del_negocio(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    toolbox = _toolbox(pool, seed)

    is_error, quote = await _run(
        toolbox, "create_quote", {"items": [{"sku": seed.sku_bob_a.lower(), "quantity": 1}]}
    )

    assert not is_error, quote
    item = await admin.fetchval(
        "SELECT oi.catalog_item_id FROM public.order_items oi JOIN public.orders o ON o.id = oi.order_id"
        " WHERE o.reference_code = $1 AND o.organization_id = $2",
        quote["reference_code"],
        seed.org_a,
    )
    assert item == seed.item_bob_a


async def test_un_codigo_de_otro_negocio_no_existe(
    pool: asyncpg.Pool, admin: asyncpg.Connection, seed: Seed
) -> None:
    other_sku = await admin.fetchval("SELECT sku FROM public.catalog_items WHERE id = $1", seed.item_b)
    toolbox = _toolbox(pool, seed)

    is_error, data = await _run(toolbox, "create_quote", {"items": [{"sku": other_sku, "quantity": 1}]})

    assert is_error and data["error"] == "ITEM_NOT_FOUND"
