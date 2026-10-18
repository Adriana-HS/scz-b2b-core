"""Agenda (#35, RF-22, RF-23, SYSTEM_DESIGN §7.2). Sin I/O.

Un horario disponible viaja como un identificador opaco, `slot:<profesional>:<inicio UTC>`: va en
la lista interactiva de WhatsApp, vuelve en la respuesta del cliente y el modelo lo pasa tal cual a
hold_appointment. El modelo no arma horarios: la BD valida horario de atención, anticipación y
doble reserva (restricción de exclusión) al reservar.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

_TOKEN = re.compile(r"^slot:([0-9a-f-]{36}):(\d{8}T\d{4}Z)$")
_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


@dataclass(frozen=True, slots=True)
class Slot:
    resource_id: UUID
    resource_name: str
    start: datetime
    end: datetime

    @property
    def token(self) -> str:
        return slot_token(self.resource_id, self.start)


@dataclass(frozen=True, slots=True)
class Appointment:
    """Una cita retenida (HELD): vence en `valid_until` si no se confirma (seña, Fase 3)."""

    reference_code: str
    resource_name: str
    start: datetime
    end: datetime
    total_bs: Decimal
    deposit_required_bs: Decimal
    valid_until: datetime


def slot_token(resource_id: UUID, start: datetime) -> str:
    return f"slot:{resource_id}:{start.astimezone(UTC):%Y%m%dT%H%MZ}"


def parse_slot_token(token: str) -> tuple[UUID, datetime]:
    """(profesional, inicio). ValueError si no es un horario que hayamos ofrecido nosotros."""
    match = _TOKEN.fullmatch(token.strip())
    if match is None:
        raise ValueError("horario inválido")
    start = datetime.strptime(match.group(2), "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
    return UUID(match.group(1)), start


def slot_label(moment: datetime, timezone: str) -> str:
    """ "mar 21/10 10:00" en la hora del negocio: corto, entra en el título de una fila (24)."""
    local = moment.astimezone(ZoneInfo(timezone))
    return f"{_WEEKDAYS[local.weekday()]} {local:%d/%m %H:%M}"
