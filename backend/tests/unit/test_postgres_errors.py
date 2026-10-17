import re
from pathlib import Path

import pytest
from asyncpg.exceptions import PostgresError

from app.domain.errors import AccessDeniedError, BusinessRuleError, ErrorCode
from app.infrastructure.postgres.errors import translate_errors, translate_postgres_error

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def _pg_error(sqlstate: str, hint: str | None = None, detail: str | None = None) -> PostgresError:
    fields = {"C": sqlstate, "M": "mensaje de la BD"}
    if hint:
        fields["H"] = hint
    if detail:
        fields["D"] = detail
    return PostgresError.new(fields)


def test_regla_de_negocio_se_traduce_con_codigo_y_detalle() -> None:
    error = translate_postgres_error(_pg_error("P0001", "OUT_OF_STOCK", "OEM-4451"))

    assert isinstance(error, BusinessRuleError)
    assert error.code is ErrorCode.OUT_OF_STOCK
    assert error.detail == "OEM-4451"


def test_permiso_denegado_se_traduce_a_access_denied() -> None:
    error = translate_postgres_error(_pg_error("42501", "MFA_REQUIRED"))

    assert isinstance(error, AccessDeniedError)
    assert error.code is ErrorCode.MFA_REQUIRED


def test_rls_sin_hint_tambien_es_acceso_denegado() -> None:
    # "new row violates row-level security policy": 42501 sin HINT -> falla cerrado
    error = translate_postgres_error(_pg_error("42501"))

    assert isinstance(error, AccessDeniedError)
    assert error.code is ErrorCode.UNMAPPED


def test_hint_desconocido_conserva_el_valor_crudo() -> None:
    error = translate_postgres_error(_pg_error("P0001", "CODIGO_NUEVO"))

    assert isinstance(error, BusinessRuleError)
    assert error.code is ErrorCode.UNMAPPED
    assert error.raw_hint == "CODIGO_NUEVO"


def test_otros_errores_de_postgres_no_se_disfrazan() -> None:
    assert translate_postgres_error(_pg_error("23505")) is None


def test_translate_errors_relanza_como_error_de_dominio() -> None:
    with pytest.raises(BusinessRuleError) as info, translate_errors():
        raise _pg_error("P0001", "FX_STALE")

    assert info.value.code is ErrorCode.FX_STALE
    assert isinstance(info.value.__cause__, PostgresError)


def test_translate_errors_deja_pasar_lo_que_no_es_suyo() -> None:
    with pytest.raises(PostgresError), translate_errors():
        raise _pg_error("23505")


def test_todo_hint_de_las_migraciones_tiene_su_codigo_en_python() -> None:
    hints = {
        match
        for sql in MIGRATIONS.glob("*.sql")
        for match in re.findall(r"HINT\s*=\s*'([A-Z_]+)'", sql.read_text(encoding="utf-8"))
    }

    assert hints, "no se encontraron HINT en las migraciones"
    assert hints <= {code.value for code in ErrorCode}, (
        f"faltan en ErrorCode: {sorted(hints - set(ErrorCode))}"
    )
