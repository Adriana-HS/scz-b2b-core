"""Proceso worker: composition root de los handlers de la cola durable (ADR-0001, ADR-0005).

El bucle claim/lease/backoff/DLQ llega con el issue #17. Este punto de entrada ya valida
la configuración, para que un despliegue mal configurado falle al arrancar y no después.

Ejecutar en local:  uv run python -m app.worker
"""

import logging

from app.core.config import Settings, get_settings

logger = logging.getLogger("scz.worker")


def main(settings: Settings | None = None) -> int:
    """Arranca el worker y devuelve el código de salida del proceso."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("worker iniciado (entorno=%s); sin handlers registrados todavía (#17)", settings.environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
