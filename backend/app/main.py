"""Proceso api: composition root de la interfaz HTTP (ADR-0001).

Ejecutar en local:  uv run uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app import __version__
from app.api.v1 import health
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación. Aquí, y solo aquí, se conectan adaptadores con casos de uso."""
    settings = settings or get_settings()
    docs_enabled = settings.environment != "production"
    app = FastAPI(
        title="SCZ-B2B-Core API",
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.include_router(health.router, prefix="/v1")
    return app


app = create_app()
