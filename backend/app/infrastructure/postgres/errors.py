"""Traducción de errores de Postgres a errores de dominio (capa anticorrupción).

Las RPC y los triggers señalan sus reglas con SQLSTATE + HINT estables:
  * P0001 (raise_exception) -> BusinessRuleError
  * 42501 (insufficient_privilege, incluida la RLS) -> AccessDeniedError
Cualquier otro error de Postgres no es una regla de negocio y se deja pasar tal cual.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from asyncpg.exceptions import PostgresError

from app.domain.errors import AccessDeniedError, BusinessRuleError, CodedError, ErrorCode

_BUSINESS_RULE = "P0001"
_ACCESS_DENIED = "42501"
_KNOWN_CODES = {code.value for code in ErrorCode}


def translate_postgres_error(exc: PostgresError) -> CodedError | None:
    """Devuelve el error de dominio equivalente, o None si no es una regla del motor."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate not in (_BUSINESS_RULE, _ACCESS_DENIED):
        return None
    hint = getattr(exc, "hint", None)
    code = ErrorCode(hint) if hint in _KNOWN_CODES else ErrorCode.UNMAPPED
    error_type = BusinessRuleError if sqlstate == _BUSINESS_RULE else AccessDeniedError
    return error_type(
        code, getattr(exc, "message", None) or str(exc), detail=getattr(exc, "detail", None), raw_hint=hint
    )


@contextmanager
def translate_errors() -> Iterator[None]:
    """Envuelve las llamadas a la BD de un repositorio."""
    try:
        yield
    except PostgresError as exc:
        domain_error = translate_postgres_error(exc)
        if domain_error is None:
            raise
        raise domain_error from exc
