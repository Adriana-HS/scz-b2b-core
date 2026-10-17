"""Proceso api: composition root de la interfaz HTTP (ADR-0001).

Ejecutar en local:  uv run uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.v1 import health
from app.api.v1.whatsapp import router as whatsapp
from app.application.inbound import InboundHandler, RecordInboundMessages
from app.application.outbound import RecordDeliveryStatuses, StatusHandler
from app.core.config import Settings, get_settings
from app.infrastructure.postgres.channels import PostgresChannelResolver
from app.infrastructure.postgres.pool import create_pool
from app.infrastructure.postgres.unit_of_work import unit_of_work_factory


def create_app(
    settings: Settings | None = None,
    *,
    inbound_handler: InboundHandler | None = None,
    status_handler: StatusHandler | None = None,
) -> FastAPI:
    """Construye la aplicación. Aquí, y solo aquí, se conectan adaptadores con casos de uso.

    `inbound_handler` permite inyectar el caso de uso en pruebas. Si no se pasa y hay
    DATABASE_URL, se construye con Postgres al arrancar; sin BD, el webhook responde 503.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.inbound_handler is not None or settings.database_url is None:
            yield
            return
        pool = await create_pool(settings.database_url.get_secret_value())
        resolver, uow = PostgresChannelResolver(pool), unit_of_work_factory(pool)
        app.state.inbound_handler = RecordInboundMessages(resolver=resolver, unit_of_work=uow)
        app.state.status_handler = RecordDeliveryStatuses(resolver=resolver, unit_of_work=uow)
        try:
            yield
        finally:
            await pool.close()

    docs_enabled = settings.environment != "production"
    app = FastAPI(
        title="SCZ-B2B-Core API",
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.inbound_handler = inbound_handler
    app.state.status_handler = status_handler
    app.include_router(health.router, prefix="/v1")
    app.include_router(whatsapp.router, prefix="/v1")
    return app


app = create_app()
