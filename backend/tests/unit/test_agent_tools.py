"""Herramientas del agente con dobles (#24, SYSTEM_DESIGN.md §7.2 y §7.4).

El LLM elige QUÉ (consulta, ítem, cantidad); el servidor pone QUIÉN (negocio, cliente y
conversación) y la BD decide CUÁNTO. Estas pruebas fijan esa frontera.
"""

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.application.agent_tools import QUOTE_DOCUMENT_NOTE, TOOL_NAMES, AgentToolbox, TurnContext
from app.domain.ai import ToolCall
from app.domain.booking import Appointment, Slot, slot_token
from app.domain.catalog import CatalogMatch, CatalogSearch, ItemDetails, PriceStatus, TaxMode
from app.domain.errors import AccessDeniedError, BusinessRuleError, ErrorCode
from app.domain.orders import Quote, QuoteRequest
from app.domain.tenancy import Actor, TenantContext

from ..conftest import MetricValue

ORG, CUSTOMER, CONVERSATION = uuid4(), uuid4(), uuid4()
ITEM = UUID("00000000-0000-4000-8000-0000000000aa")
CONTEXT = TurnContext(organization_id=ORG, customer_id=CUSTOMER, conversation_id=CONVERSATION)
MATCH = CatalogMatch(
    item_id=ITEM, sku="FRE-001", name="Pastilla de freno delantera", item_type="PRODUCT",
    stock_available=12, duration_minutes=None, unit_price_bs=Decimal("332.88"),
    price_status=PriceStatus.OK, tax_mode=TaxMode.IVA_INCLUIDO,
)  # fmt: skip
DETAILS = ItemDetails(
    item_id=ITEM, sku="FRE-001", name="Pastilla de freno delantera", description="Juego de 4",
    category="Frenos", item_type="PRODUCT", stock_available=12, duration_minutes=None,
    unit_price_bs=Decimal("332.88"), price_status=PriceStatus.OK, tax_mode=TaxMode.IVA_INCLUIDO,
    attributes={"compatibilidad": "Camioneta 4x4 diésel 2.4"},
)  # fmt: skip
QUOTE = Quote(
    order_id=uuid4(), reference_code="COT-2026-000123", subtotal_bs=Decimal("665.76"),
    iva_bs=Decimal("86.55"),
    total_bs=Decimal("665.76"), deposit_required_bs=Decimal("332.88"), applied_fx_rate=Decimal("11.6800"),
    tax_mode=TaxMode.IVA_INCLUIDO, valid_until=datetime(2026, 10, 18, 15, 0, tzinfo=UTC),
)  # fmt: skip


# Catálogo del doble para resolver códigos: dos ítems cuyo código normalizado coincide (FRE-001 y FRE001)
OTHER = UUID("00000000-0000-4000-8000-0000000000bb")
SKUS = [("FRE-001", ITEM), ("FRE001", OTHER), ("FIL-002", UUID("00000000-0000-4000-8000-0000000000cc"))]


def _norm(sku: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", sku).upper()


# Agenda (#35): dos horarios libres pasado mañana a las 10:00 y 10:30 en La Paz (14:00 y 14:30 UTC)
LA_PAZ = ZoneInfo("America/La_Paz")
RESOURCE = UUID("00000000-0000-4000-8000-0000000000d1")
DAY = (datetime.now(LA_PAZ) + timedelta(days=2)).date()
SLOTS = [
    Slot(RESOURCE, "Dra. Ejemplo Uno", start, start + timedelta(minutes=30))
    for start in (datetime(DAY.year, DAY.month, DAY.day, 10, minute, tzinfo=LA_PAZ) for minute in (0, 30))
]


class Calls(list[tuple[str, Any]]):
    pass


class FakeCatalog:
    def __init__(self, calls: Calls, error: Exception | None, matches: list[CatalogMatch]) -> None:
        self.calls, self.error, self.matches = calls, error, matches

    async def search(self, criteria: CatalogSearch) -> list[CatalogMatch]:
        self.calls.append(("search", criteria))
        if self.error:
            raise self.error
        return self.matches

    async def get_details(self, item_id: UUID) -> ItemDetails | None:
        self.calls.append(("details", item_id))
        if self.error:
            raise self.error
        return DETAILS if item_id == ITEM else None

    async def find_by_skus(self, skus: Sequence[str]) -> list[tuple[str, UUID]]:
        self.calls.append(("skus", tuple(skus)))
        return [(sku, item) for sku, item in SKUS if any(_norm(sku) == _norm(s) for s in skus)]


class FakeOrders:
    def __init__(self, calls: Calls, error: Exception | None) -> None:
        self.calls, self.error = calls, error

    async def create_quote(self, request: QuoteRequest) -> Quote:
        self.calls.append(("quote", request))
        if self.error:
            raise self.error
        return QUOTE

    async def request_quote_document(self, order_id: UUID, conversation_id: UUID) -> None:
        self.calls.append(("quote_document", (order_id, conversation_id)))

    async def business_timezone(self) -> str:
        return "America/La_Paz"

    async def available_slots(self, service_item_id: UUID, day: date) -> list[Slot]:
        self.calls.append(("slots", (service_item_id, day)))
        return [s for s in SLOTS if s.start.astimezone(LA_PAZ).date() == day]

    async def hold_appointment(
        self,
        customer_id: UUID,
        conversation_id: UUID,
        service_item_id: UUID,
        resource_id: UUID,
        start: datetime,
    ) -> Appointment:
        self.calls.append(("hold", (customer_id, conversation_id, service_item_id, resource_id, start)))
        if self.error:
            raise self.error
        return Appointment("CITA-2026-000007", "Dra. Ejemplo Uno", start, start + timedelta(minutes=30),
                           Decimal("150.00"), Decimal("45.00"), start - timedelta(hours=20))  # fmt: skip


class FakeConversations:
    def __init__(self, calls: Calls, error: Exception | None) -> None:
        self.calls, self.error = calls, error

    async def request_handoff(self, conversation_id: UUID, reason: str, summary: str) -> None:
        self.calls.append(("handoff", (conversation_id, reason, summary)))
        if self.error:
            raise self.error


class FakeUnitOfWork:
    def __init__(
        self, calls: Calls, context: TenantContext, error: Exception | None, matches: list[CatalogMatch]
    ) -> None:
        self.calls, self.context = calls, context
        self.catalog = FakeCatalog(calls, error, matches)
        self.orders = FakeOrders(calls, error)
        self.conversations = FakeConversations(calls, error)

    async def __aenter__(self) -> Self:
        self.calls.append(("begin", self.context))
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.calls.append(("end", "rollback" if exc_type else "commit"))


def _toolbox(
    error: Exception | None = None, matches: list[CatalogMatch] | None = None, **kwargs: Any
) -> tuple[AgentToolbox, Calls]:
    calls = Calls()

    def factory(context: TenantContext) -> FakeUnitOfWork:
        return FakeUnitOfWork(calls, context, error, [MATCH] if matches is None else matches)

    return AgentToolbox(factory, CONTEXT, **kwargs), calls  # type: ignore[arg-type]  # doble mínimo


async def _run(toolbox: AgentToolbox, name: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await toolbox.execute(ToolCall(id="toolu_1", name=name, arguments=arguments))
    assert result.tool_call_id == "toolu_1"
    data: dict[str, Any] = json.loads(result.content)
    return result.is_error, data


# --- Especificaciones -----------------------------------------------------------------------------
def test_expone_las_cuatro_herramientas_con_esquemas_cerrados() -> None:
    toolbox, _ = _toolbox()

    specs = {spec.name: spec for spec in toolbox.specs()}

    assert (
        set(specs)
        == set(TOOL_NAMES)
        == {
            "search_catalog",
            "get_item_details",
            "create_quote",
            "check_availability",
            "hold_appointment",
            "handoff_to_human",
        }
    )
    for spec in specs.values():
        assert spec.input_schema["additionalProperties"] is False
        text = json.dumps(spec.input_schema)
        for forbidden in ("organization_id", "customer_id", "conversation_id", "price", "precio"):
            assert forbidden not in text, f"{spec.name} no debe aceptar {forbidden}"
        assert spec.description


def test_un_rubro_puede_habilitar_solo_algunas_herramientas() -> None:
    toolbox, _ = _toolbox(enabled=("search_catalog", "handoff_to_human"))

    assert [s.name for s in toolbox.specs()] == ["search_catalog", "handoff_to_human"]


async def test_una_herramienta_no_habilitada_no_se_ejecuta() -> None:
    toolbox, calls = _toolbox(enabled=("search_catalog",))

    is_error, data = await _run(toolbox, "create_quote", {"items": [{"item_id": str(ITEM), "quantity": 1}]})

    assert is_error and data["error"] == "UNKNOWN_TOOL"
    assert calls == []


# --- search_catalog -------------------------------------------------------------------------------
async def test_buscar_devuelve_precios_exactos_como_texto() -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "search_catalog", {"query": "pastilla freno", "category": "Frenos"})

    assert not is_error
    [item] = data["items"]
    assert item["unit_price_bs"] == "332.88"  # texto exacto: el grounding compara cifras (#25)
    assert (
        item["item_id"] == str(ITEM) and item["price_status"] == "OK" and item["tax_mode"] == "IVA_INCLUIDO"
    )
    assert calls[0] == ("begin", TenantContext(ORG, Actor.BOT))
    assert calls[1] == ("search", CatalogSearch(query="pastilla freno", category="Frenos"))


async def test_un_precio_que_no_se_puede_ofrecer_trae_su_aviso() -> None:
    stale = MATCH.model_copy(update={"unit_price_bs": None, "price_status": PriceStatus.FX_STALE})
    toolbox, _ = _toolbox(matches=[stale])

    is_error, data = await _run(toolbox, "search_catalog", {"query": "pastilla"})

    assert not is_error
    assert data["items"][0]["unit_price_bs"] is None
    assert "actualización" in data["items"][0]["price_note"]  # RF-02: "precio en actualización"


# --- Validación de lo que pide el LLM -------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_catalog", {"query": ""}),
        ("search_catalog", {"query": "x" * 201}),
        ("search_catalog", {"query": "freno", "organization_id": str(uuid4())}),  # otro negocio
        ("get_item_details", {"item_id": "no-es-un-uuid"}),
        ("create_quote", {"items": []}),
        ("create_quote", {"items": [{"item_id": str(ITEM), "quantity": 0}]}),
        ("create_quote", {"items": [{"item_id": str(ITEM), "quantity": 10_001}]}),
        (
            "create_quote",
            {"items": [{"item_id": str(ITEM), "quantity": 1, "unit_price_bs": "1.00"}]},
        ),  # precio inyectado
        ("create_quote", {"items": [{"item_id": str(ITEM), "quantity": 1}], "customer_id": str(uuid4())}),
        ("create_quote", {"items": [{"item_id": str(ITEM), "quantity": 1}] * 51}),
        ("handoff_to_human", {"reason": "INVENTADO", "summary": "x"}),
        ("handoff_to_human", {"reason": "OTHER", "summary": ""}),
    ],
)
async def test_argumentos_invalidos_no_llegan_a_la_bd(name: str, arguments: dict[str, Any]) -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, name, arguments)

    assert is_error and data["error"] == "INVALID_ARGUMENTS"
    assert data["fields"]  # qué campo falló, sin repetir el valor
    assert calls == []  # ni siquiera se abrió una transacción


async def test_el_error_de_validacion_no_repite_lo_que_mando_el_llm() -> None:
    secret = "59170000001-dato-del-cliente"
    toolbox, _ = _toolbox()

    _, data = await _run(toolbox, "search_catalog", {"query": secret * 10})

    assert secret not in json.dumps(data)


async def test_una_herramienta_desconocida() -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "approve_payment", {"order": "COT-1"})

    assert is_error and data["error"] == "UNKNOWN_TOOL" and calls == []


# --- create_quote por código (#89): el servidor resuelve el SKU dentro del negocio -----------------
async def test_cotizar_por_codigo_resuelve_el_item_del_negocio() -> None:
    toolbox, calls = _toolbox()

    is_error, _ = await _run(toolbox, "create_quote", {"items": [{"sku": "fil-002", "quantity": 3}]})

    assert not is_error
    request = next(arg for kind, arg in calls if kind == "quote")
    assert [(line.item_id, line.quantity) for line in request.lines] == [
        (UUID("00000000-0000-4000-8000-0000000000cc"), 3)
    ]


async def test_con_codigos_parecidos_gana_el_exacto() -> None:
    toolbox, calls = _toolbox()

    await _run(toolbox, "create_quote", {"items": [{"sku": "FRE-001", "quantity": 1}]})

    request = next(arg for kind, arg in calls if kind == "quote")
    assert request.lines[0].item_id == ITEM


@pytest.mark.parametrize("sku", ["NO-EXISTE", "fre 001"])  # inexistente, o ambiguo sin coincidencia exacta
async def test_un_codigo_que_no_se_puede_resolver_no_cotiza(sku: str) -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "create_quote", {"items": [{"sku": sku, "quantity": 1}]})

    assert is_error and data["error"] == "ITEM_NOT_FOUND"
    assert not [kind for kind, _ in calls if kind == "quote"]  # no llega a la BD de órdenes


async def test_se_pueden_mezclar_codigo_e_item_id() -> None:
    toolbox, calls = _toolbox()

    await _run(toolbox, "create_quote", {"items": [{"sku": "FIL-002", "quantity": 1},
                                                    {"item_id": str(ITEM), "quantity": 2}]})  # fmt: skip

    request = next(arg for kind, arg in calls if kind == "quote")
    assert [line.quantity for line in request.lines] == [1, 2]


@pytest.mark.parametrize(
    "item",
    [{"quantity": 1}, {"sku": "FRE-001", "item_id": str(ITEM), "quantity": 1}, {"sku": "", "quantity": 1},
     {"sku": "x" * 65, "quantity": 1}],
)  # fmt: skip
async def test_cada_linea_lleva_codigo_o_item_id_pero_no_los_dos(item: dict[str, Any]) -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "create_quote", {"items": [item]})

    assert is_error and data["error"] == "INVALID_ARGUMENTS"
    assert not [kind for kind, _ in calls if kind in ("quote", "skus")]


# --- create_quote: el servidor pone el cliente y la conversación ----------------------------------
async def test_cotizar_usa_el_cliente_y_la_conversacion_del_contexto() -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "create_quote", {"items": [{"item_id": str(ITEM), "quantity": 2}]})

    assert not is_error
    request = next(arg for kind, arg in calls if kind == "quote")
    assert (request.customer_id, request.conversation_id) == (CUSTOMER, CONVERSATION)
    assert [(line.item_id, line.quantity) for line in request.lines] == [(ITEM, 2)]
    assert data == {
        "reference_code": "COT-2026-000123", "subtotal_bs": "665.76", "iva_bs": "86.55", "total_bs": "665.76",
        "deposit_required_bs": "332.88", "applied_fx_rate": "11.6800", "tax_mode": "IVA_INCLUIDO",
        "valid_until": "2026-10-18T15:00:00+00:00", "document": QUOTE_DOCUMENT_NOTE,
    }  # fmt: skip
    assert "order_id" not in data  # el id interno no le sirve al modelo


async def test_cotizar_pide_el_pdf_en_la_misma_transaccion() -> None:
    """#26: si la cotización se confirma, su PDF queda en cola; si se revierte, no."""
    toolbox, calls = _toolbox()

    await _run(toolbox, "create_quote", {"items": [{"item_id": str(ITEM), "quantity": 2}]})

    assert ("quote_document", (QUOTE.order_id, CONVERSATION)) in calls
    kinds = [kind for kind, _ in calls]
    assert kinds.index("quote") < kinds.index("quote_document")


async def test_una_cotizacion_que_falla_no_pide_pdf() -> None:
    toolbox, calls = _toolbox(error=BusinessRuleError(ErrorCode.OUT_OF_STOCK, "detalle"))

    await _run(toolbox, "create_quote", {"items": [{"item_id": str(ITEM), "quantity": 2}]})

    assert not [kind for kind, _ in calls if kind == "quote_document"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (BusinessRuleError(ErrorCode.OUT_OF_STOCK, "detalle de la BD"), "OUT_OF_STOCK"),
        (BusinessRuleError(ErrorCode.FX_STALE, "detalle"), "FX_STALE"),
        (BusinessRuleError(ErrorCode.ITEM_NOT_FOUND, "detalle"), "ITEM_NOT_FOUND"),
        (BusinessRuleError(ErrorCode.STOCK_CONFLICT, "detalle"), "STOCK_CONFLICT"),
        (AccessDeniedError(ErrorCode.TENANT_REQUIRED, "detalle"), "TENANT_REQUIRED"),
    ],
)
async def test_los_errores_de_la_bd_llegan_con_codigo_y_mensaje_para_el_cliente(
    error: Exception, code: str
) -> None:
    toolbox, calls = _toolbox(error=error)

    is_error, data = await _run(toolbox, "create_quote", {"items": [{"item_id": str(ITEM), "quantity": 1}]})

    assert is_error and data["error"] == code
    assert data["customer_message"] and "detalle" not in data["customer_message"]
    assert ("end", "rollback") in calls


async def test_un_error_inesperado_no_filtra_detalles(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    toolbox, _ = _toolbox(error=RuntimeError("Key (wa_id)=(59170000001) password=secreto"))

    is_error, data = await _run(toolbox, "search_catalog", {"query": "freno"})

    assert is_error and data["error"] == "INTERNAL_ERROR"
    text = json.dumps(data) + caplog.text
    assert "59170000001" not in text and "secreto" not in text
    assert "RuntimeError" in caplog.text  # el tipo sí queda en el log


# --- get_item_details y handoff -------------------------------------------------------------------
async def test_la_ficha_de_un_item() -> None:
    toolbox, _ = _toolbox()

    is_error, data = await _run(toolbox, "get_item_details", {"item_id": str(ITEM)})

    assert not is_error
    assert data["item"]["unit_price_bs"] == "332.88"
    assert data["item"]["attributes"] == {"compatibilidad": "Camioneta 4x4 diésel 2.4"}


async def test_un_item_que_no_existe_en_el_negocio() -> None:
    toolbox, _ = _toolbox()

    is_error, data = await _run(toolbox, "get_item_details", {"item_id": str(uuid4())})

    assert is_error and data["error"] == "ITEM_NOT_FOUND" and data["customer_message"]


async def test_pasar_a_una_persona_usa_la_conversacion_del_contexto() -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(
        toolbox, "handoff_to_human", {"reason": "CUSTOMER_REQUEST", "summary": "Pide hablar con una persona."}
    )

    assert not is_error and data == {"ok": True}
    assert ("handoff", (CONVERSATION, "CUSTOMER_REQUEST", "Pide hablar con una persona.")) in calls


# --- Métricas -------------------------------------------------------------------------------------
async def test_cada_llamada_cuenta_por_herramienta_y_resultado(metric: MetricValue) -> None:
    ok_before = metric("agent_tool_calls", tool="search_catalog", outcome="OK")
    invalid_before = metric("agent_tool_calls", tool="create_quote", outcome="INVALID_ARGUMENTS")
    toolbox, _ = _toolbox()

    await _run(toolbox, "search_catalog", {"query": "freno"})
    await _run(toolbox, "create_quote", {"items": []})

    assert metric("agent_tool_calls", tool="search_catalog", outcome="OK") == ok_before + 1
    assert metric("agent_tool_calls", tool="create_quote", outcome="INVALID_ARGUMENTS") == invalid_before + 1


# --- Agenda (#35): horarios libres y reserva ------------------------------------------------------
async def test_los_horarios_salen_con_etiqueta_en_hora_del_negocio_y_su_slot() -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(
        toolbox, "check_availability", {"service_item_id": str(ITEM), "day": DAY.isoformat()}
    )

    assert not is_error
    assert ("slots", (ITEM, DAY)) in calls
    first = data["slots"][0]
    assert first["slot"] == slot_token(RESOURCE, SLOTS[0].start)
    assert first["label"].endswith("10:00") and first["professional"] == "Dra. Ejemplo Uno"


async def test_el_servicio_se_puede_pedir_por_codigo() -> None:
    toolbox, calls = _toolbox()

    await _run(toolbox, "check_availability", {"service_sku": "fil-002", "day": DAY.isoformat()})

    assert ("slots", (UUID("00000000-0000-4000-8000-0000000000cc"), DAY)) in calls


@pytest.mark.parametrize("days", [-3, 91])
async def test_solo_ofrece_desde_hoy_hasta_tres_meses(days: int) -> None:
    toolbox, calls = _toolbox()
    day = (datetime.now(UTC) + timedelta(days=days)).date().isoformat()

    is_error, data = await _run(toolbox, "check_availability", {"service_item_id": str(ITEM), "day": day})

    assert is_error and data["error"] == "INVALID_DATE"
    assert not [kind for kind, _ in calls if kind == "slots"]


async def test_reservar_usa_el_slot_y_el_cliente_del_contexto() -> None:
    toolbox, calls = _toolbox()
    slot = slot_token(RESOURCE, SLOTS[0].start)

    is_error, data = await _run(toolbox, "hold_appointment", {"service_item_id": str(ITEM), "slot": slot})

    assert not is_error, data
    hold = next(arg for kind, arg in calls if kind == "hold")
    assert hold == (CUSTOMER, CONVERSATION, ITEM, RESOURCE, SLOTS[0].start)
    assert data["reference_code"] == "CITA-2026-000007" and data["start"].endswith("10:00")
    assert (data["total_bs"], data["deposit_required_bs"]) == ("150.00", "45.00")  # texto exacto (grounding)


async def test_si_el_horario_se_ocupo_devuelve_alternativas_del_mismo_dia() -> None:
    toolbox, calls = _toolbox(error=BusinessRuleError(ErrorCode.SLOT_TAKEN, "exclusion"))
    late = datetime(DAY.year, DAY.month, DAY.day, 20, 30, tzinfo=LA_PAZ)  # en UTC ya es el día siguiente
    slot = slot_token(RESOURCE, late)

    is_error, data = await _run(toolbox, "hold_appointment", {"service_item_id": str(ITEM), "slot": slot})

    assert is_error and data["error"] == "SLOT_TAKEN" and data["customer_message"]
    assert [s["slot"] for s in data["slots"]] == [s.token for s in SLOTS]
    assert ("slots", (ITEM, DAY)) in calls  # el día en la hora del negocio, no en UTC


@pytest.mark.parametrize(
    "arguments",
    [
        {"service_item_id": str(uuid4()), "slot": "slot:no-es-un-horario"},
        {"service_item_id": str(uuid4()), "slot": f"slot:{RESOURCE}:2026-10-21 10:00"},
        {"slot": f"slot:{RESOURCE}:20261021T1400Z"},  # sin servicio
        {"service_item_id": str(ITEM), "service_sku": "X", "slot": f"slot:{RESOURCE}:20261021T1400Z"},
    ],
)
async def test_un_slot_o_servicio_mal_formado_no_reserva(arguments: dict[str, Any]) -> None:
    toolbox, calls = _toolbox()

    is_error, data = await _run(toolbox, "hold_appointment", arguments)

    assert is_error and data["error"] == "INVALID_ARGUMENTS"
    assert not [kind for kind, _ in calls if kind == "hold"]
