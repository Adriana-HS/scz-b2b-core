"""Rubros (§7.3): un VerticalPolicy por business_vertical. Agregar un rubro = un módulo más aquí."""

from collections.abc import Mapping

from app.domain.ports import VerticalPolicy
from app.verticals.clinica import ClinicaPolicy
from app.verticals.importadora import ImportadoraPolicy

VERTICAL_POLICIES: Mapping[str, VerticalPolicy] = {
    "IMPORTADORA_REPUESTOS": ImportadoraPolicy(),
    "CLINICA_CONSULTORIO": ClinicaPolicy(),
}
