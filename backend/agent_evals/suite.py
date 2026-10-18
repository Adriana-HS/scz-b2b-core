"""Formato de la suite (evals/conversations/<rubro>.yaml). Validado con Pydantic: un error de
tipeo en un caso falla en la CI, no en medio de una corrida paga."""

from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.agent_tools import TOOL_NAMES

SUITES = Path(__file__).resolve().parents[2] / "evals" / "conversations"
TOOLS = frozenset(TOOL_NAMES)
OUTCOMES = frozenset({
    "replied", "limit", "blocked", "ungrounded", "sensitive_request", "quota_exceeded", "rate_limited",
    "ai_unavailable", "window_closed", "skipped_human", "skipped_consent", "nothing_pending",
})  # fmt: skip


class Category(StrEnum):
    SALUDO = "saludo"
    CATALOGO = "catalogo"
    FICHA = "ficha"
    COTIZACION = "cotizacion"
    HANDOFF = "handoff"
    FUERA_DE_ALCANCE = "fuera_de_alcance"
    INYECCION = "inyeccion"
    DATOS_SENSIBLES = "datos_sensibles"
    AGENDA = "agenda"  # solo en rubros con check_availability (#35)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedItem(_Strict):
    sku: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class ToolExpectation(_Strict):
    """Una herramienta que el bot debe usar. Los argumentos se revisan solo en lo que se indica."""

    name: str
    query_any: tuple[str, ...] = ()  # search_catalog: la consulta contiene alguna de estas palabras
    items: tuple[ExpectedItem, ...] = ()  # create_quote: estos códigos con estas cantidades
    reasons: tuple[str, ...] = ()  # handoff_to_human: alguno de estos motivos

    @model_validator(mode="after")
    def _known(self) -> Self:
        if self.name not in TOOLS:
            raise ValueError(f"herramienta desconocida: {self.name}")
        return self


class Expect(_Strict):
    tools: tuple[ToolExpectation, ...] = ()  # en este orden (pueden aparecer otras entre medio)
    forbidden_tools: tuple[str, ...] = ()
    no_tools: bool = False
    outcomes: tuple[str, ...] = ("replied",)
    reply_contains_any: tuple[str, ...] = ()
    reply_not_contains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.no_tools and self.tools:
            raise ValueError("no_tools y tools a la vez")
        if not set(self.outcomes) <= OUTCOMES or not set(self.forbidden_tools) <= TOOLS:
            raise ValueError("resultado o herramienta desconocidos")
        return self


class Turn(_Strict):
    role: str = Field(pattern="^(cliente|bot)$")
    text: str = Field(min_length=1, max_length=2000)


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z]{3}-\d{2}$")
    category: Category
    history: tuple[Turn, ...] = ()  # lo que ya pasó en la conversación (no se le pide al modelo)
    message: str = Field(min_length=1, max_length=2000)  # lo que escribe el cliente ahora
    expect: Expect


class Suite(_Strict):
    vertical: str
    organization_id: UUID
    phone_number_id: str
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de casos repetidos")
        return self


def load_suite(name: str, root: Path = SUITES) -> Suite:
    return Suite.model_validate(yaml.safe_load((root / f"{name}.yaml").read_text(encoding="utf-8")))
