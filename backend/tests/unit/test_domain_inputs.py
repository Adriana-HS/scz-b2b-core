"""Las entradas que vienen del LLM se validan antes de llegar a la BD (SYSTEM_DESIGN.md §7.2, §7.4)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.catalog import CatalogSearch
from app.domain.orders import QuoteLine, QuoteRequest
from app.domain.tenancy import Actor, TenantContext


def test_una_linea_no_acepta_precios_inyectados() -> None:
    with pytest.raises(ValidationError, match="extra"):
        QuoteLine.model_validate({"item_id": str(uuid4()), "quantity": 1, "unit_price_bs": "0.01"})


@pytest.mark.parametrize("quantity", [0, -1, 10_001])
def test_cantidad_fuera_de_rango(quantity: int) -> None:
    with pytest.raises(ValidationError):
        QuoteLine(item_id=uuid4(), quantity=quantity)


def test_una_cotizacion_tiene_entre_1_y_50_lineas() -> None:
    with pytest.raises(ValidationError):
        QuoteRequest(customer_id=uuid4(), conversation_id=None, lines=())
    with pytest.raises(ValidationError):
        QuoteRequest(
            customer_id=uuid4(),
            conversation_id=None,
            lines=tuple(QuoteLine(item_id=uuid4(), quantity=1) for _ in range(51)),
        )


def test_la_busqueda_limita_largo_y_cantidad_de_resultados() -> None:
    with pytest.raises(ValidationError):
        CatalogSearch(query="x" * 201)
    with pytest.raises(ValidationError):
        CatalogSearch(query="freno", limit=21)


def test_el_contexto_solo_acepta_actores_del_backend() -> None:
    with pytest.raises(TypeError):
        TenantContext(uuid4(), "HUMAN")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        TenantContext("no-es-uuid", Actor.BOT)  # type: ignore[arg-type]
