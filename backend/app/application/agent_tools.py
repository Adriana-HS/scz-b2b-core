"""Herramientas del agente: la superficie mínima y segura que ve el LLM (#24, §7.2).

`AgentToolbox` es una fachada (principios-y-patrones §3): expone al modelo solo las
herramientas habilitadas del rubro y ejecuta cada pedido así:

  1. Valida los argumentos con Pydantic (`extra="forbid"`): un campo de más (un precio, otro
     negocio, otro cliente) o fuera de rango se rechaza sin tocar la BD.
  2. Completa lo que el LLM nunca decide: negocio, cliente y conversación salen del
     `TurnContext` del servidor; la transacción va con actor BOT (SEC-02).
  3. Corre la operación en una UnitOfWork propia; la BD calcula precios, stock e impuestos.
  4. Devuelve JSON: montos como texto exacto ("480.00"), para que el grounding numérico (#25)
     compare cifras sin redondeos. Un error trae su código estable y un mensaje para el cliente;
     nunca el detalle de la BD ni lo que mandó el LLM.

La subida del PDF de la cotización a Meta llega con #26; el aviso al equipo, con el dashboard (#41).
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.core import telemetry
from app.domain.agent_tools import CreateQuoteArgs, GetItemDetailsArgs, HandoffArgs, SearchCatalogArgs
from app.domain.ai import ToolCall, ToolResult, ToolSpec
from app.domain.catalog import CatalogMatch, CatalogSearch, ItemDetails, PriceStatus
from app.domain.errors import CodedError, ErrorCode
from app.domain.orders import QuoteRequest
from app.domain.ports import UnitOfWork, UnitOfWorkFactory
from app.domain.tenancy import Actor, TenantContext

logger = logging.getLogger("scz.agent.tools")

TOOL_NAMES = ("search_catalog", "get_item_details", "create_quote", "handoff_to_human")
# Lo que el modelo sabe del PDF (#26): sale solo por este chat, después de su respuesta
QUOTE_DOCUMENT_NOTE = "El PDF de esta cotización le llega al cliente por este chat en unos segundos."

# Lo que el bot le dice al cliente ante cada error, de usted (el modelo lo reformula, sin inventar cifras)
CUSTOMER_MESSAGES: Mapping[str, str] = {
    ErrorCode.ITEM_NOT_FOUND: "No encontré ese producto en el catálogo del negocio.",
    ErrorCode.INVALID_ITEMS: "Alguno de los productos ya no está disponible en el catálogo.",
    ErrorCode.OUT_OF_STOCK: "No hay stock suficiente de alguno de los productos para esa cantidad.",
    ErrorCode.STOCK_CONFLICT: "Otro cliente acaba de reservar ese stock. Puedo volver a intentarlo.",
    ErrorCode.FX_STALE: "El precio en bolivianos está en actualización; una persona del equipo lo confirma.",
    ErrorCode.INVALID_QUANTITY: "La cantidad no es válida.",
    ErrorCode.DUPLICATE_ITEM: "El mismo producto aparece dos veces; lo junto en una sola línea.",
    ErrorCode.NOT_FOUND: "No encontré esa conversación.",
    "INVALID_ARGUMENTS": "No pude procesar ese pedido; ¿me lo podría repetir con más detalle?",
    "UNKNOWN_TOOL": "Eso no lo puedo hacer desde aquí; puedo comunicarle con una persona del equipo.",
}
_DEFAULT_MESSAGE = "Tuve un problema para hacerlo. Puedo comunicarle con una persona del equipo."
_PRICE_NOTES: Mapping[PriceStatus, str] = {
    PriceStatus.FX_STALE: "Precio en actualización: lo confirma una persona del equipo.",
    PriceStatus.FX_MISSING: "Precio en actualización: lo confirma una persona del equipo.",
}

_SPEC_TEXT: Mapping[str, str] = {
    "search_catalog": "Busca productos o servicios del catálogo del negocio por texto o código. Devuelve "
    "precio en Bs (o un aviso si está en actualización), stock disponible e indicación de IVA.",
    "get_item_details": "Ficha de un ítem devuelto por search_catalog: descripción, compatibilidad, precio "
    "y stock disponible.",
    "create_quote": "Crea una cotización con ítems y cantidades del catálogo. La base de datos calcula "
    "subtotal, IVA, total, seña y vigencia; usa solo esas cifras al responder.",
    "handoff_to_human": "Pasa la conversación a una persona del equipo cuando el cliente lo pide, cuando "
    "algo no se puede resolver aquí o ante un reclamo.",
}


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Lo que el servidor sabe del turno y el LLM nunca elige."""

    organization_id: UUID
    customer_id: UUID
    conversation_id: UUID


def _plain(value: Any) -> Any:
    """JSON sin pérdida: Decimal y UUID como texto exacto, fechas ISO 8601."""
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if hasattr(value, "value"):  # StrEnum
        return value.value
    return value


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)  # la descripción va en ToolSpec.description
    return schema


def _item(match: CatalogMatch | ItemDetails) -> dict[str, Any]:
    data = _plain(match.model_dump())
    note = _PRICE_NOTES.get(match.price_status)
    if note:
        data["price_note"] = note
    return dict(data)


class _ToolError(Exception):
    def __init__(self, code: str, *, fields: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields or []


Handler = Callable[[UnitOfWork, BaseModel], Awaitable[dict[str, Any]]]


class AgentToolbox:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        context: TurnContext,
        *,
        enabled: Sequence[str] = TOOL_NAMES,
    ) -> None:
        unknown = set(enabled) - set(TOOL_NAMES)
        if unknown:
            raise ValueError(f"herramientas inexistentes: {sorted(unknown)}")
        self._unit_of_work = unit_of_work
        self._context = context
        self._enabled = tuple(name for name in TOOL_NAMES if name in enabled) if enabled else ()
        self._tools: Mapping[str, tuple[type[BaseModel], Handler]] = {
            "search_catalog": (SearchCatalogArgs, self._search),
            "get_item_details": (GetItemDetailsArgs, self._details),
            "create_quote": (CreateQuoteArgs, self._quote),
            "handoff_to_human": (HandoffArgs, self._handoff),
        }

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            ToolSpec(name=name, description=_SPEC_TEXT[name], input_schema=_schema(self._tools[name][0]))
            for name in self._enabled
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            if call.name not in self._enabled:
                raise _ToolError("UNKNOWN_TOOL")
            model, handler = self._tools[call.name]
            try:
                args = model.model_validate(dict(call.arguments))
            except ValidationError as exc:
                # Solo la ubicación del error: el valor puede traer datos del cliente
                fields = sorted({".".join(str(p) for p in e["loc"]) or "(raíz)" for e in exc.errors()})
                raise _ToolError("INVALID_ARGUMENTS", fields=fields) from None
            async with self._unit_of_work(TenantContext(self._context.organization_id, Actor.BOT)) as uow:
                data = await handler(uow, args)
        except _ToolError as error:
            return self._error(call, error.code, fields=error.fields)
        except CodedError as error:
            return self._error(call, error.code.value)
        except Exception as error:
            logger.error("agente: la herramienta %s falló (%s)", call.name, type(error).__name__)
            return self._error(call, "INTERNAL_ERROR")
        telemetry.agent_tool_calls.add(1, telemetry.attributes(tool=call.name, outcome="OK"))
        return ToolResult(tool_call_id=call.id, content=json.dumps(data, ensure_ascii=False))

    def _error(self, call: ToolCall, code: str, *, fields: list[str] | None = None) -> ToolResult:
        tool = call.name if call.name in TOOL_NAMES else "unknown"
        telemetry.agent_tool_calls.add(1, telemetry.attributes(tool=tool, outcome=code))
        body: dict[str, Any] = {
            "error": code,
            "customer_message": CUSTOMER_MESSAGES.get(code, _DEFAULT_MESSAGE),
        }
        if fields:
            body["fields"] = fields
        return ToolResult(tool_call_id=call.id, content=json.dumps(body, ensure_ascii=False), is_error=True)

    # --- Herramientas -------------------------------------------------------------------------------
    async def _search(self, uow: UnitOfWork, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, SearchCatalogArgs)  # noqa: S101 - lo garantiza la tabla de herramientas
        matches = await uow.catalog.search(CatalogSearch(query=args.query, category=args.category))
        return {"items": [_item(m) for m in matches]}

    async def _details(self, uow: UnitOfWork, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, GetItemDetailsArgs)  # noqa: S101
        details = await uow.catalog.get_details(args.item_id)
        if details is None:
            raise _ToolError(ErrorCode.ITEM_NOT_FOUND.value)
        return {"item": _item(details)}

    async def _quote(self, uow: UnitOfWork, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, CreateQuoteArgs)  # noqa: S101
        quote = await uow.orders.create_quote(
            QuoteRequest(
                customer_id=self._context.customer_id,
                conversation_id=self._context.conversation_id,
                lines=args.items,
            )
        )
        # Mismo commit que la cotización: si se revierte, no queda un PDF de una cotización inexistente
        await uow.orders.request_quote_document(quote.order_id, self._context.conversation_id)
        data = _plain(quote.model_dump(exclude={"order_id"}))  # el id interno no le sirve al modelo
        return {**data, "document": QUOTE_DOCUMENT_NOTE}

    async def _handoff(self, uow: UnitOfWork, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, HandoffArgs)  # noqa: S101
        await uow.conversations.request_handoff(
            self._context.conversation_id, args.reason.value, args.summary
        )
        return {"ok": True}
