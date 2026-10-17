"""Logs JSON estructurados y sin PII (SYSTEM_DESIGN.md §10.2).

Cada línea es un objeto JSON con: ts (UTC), level, logger, msg y, cuando existen, trace_id,
span_id y job_id. El mensaje y el traceback pasan por `redact` antes de escribirse. Los
campos de contexto (job_id, organization_id) se fijan con `log_context(...)`; nunca se
loguean teléfonos ni textos de clientes.
"""

import contextlib
import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.core.redaction import redact

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("scz_log_context", default={})  # noqa: B039 - el default nunca se muta: se reemplaza


@contextlib.contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Agrega campos a cada log dentro del bloque (p. ej. job_id=..., organization_id=...)."""
    token = _context.set({**_context.get(), **{k: str(v) for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            entry["trace_id"] = format(span.trace_id, "032x")
            entry["span_id"] = format(span.span_id, "016x")
        for key, value in _context.get().items():  # el contexto no pisa los campos base
            entry.setdefault(key, redact(value))
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Un handler a stdout con formato JSON (12-Factor: logs como flujo). Idempotente.

    Reemplaza el handler por defecto de basicConfig y uno JSON previo; respeta los demás
    (por ejemplo, el de Sentry o el de captura de pytest).
    """
    root = logging.getLogger()
    root.handlers[:] = [
        h for h in root.handlers if type(h) is not logging.StreamHandler and not getattr(h, "scz_json", False)
    ]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.scz_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
