"""Proceso api: composition root de la interfaz HTTP (ADR-0001).

Ejecutar en local:  uv run uvicorn app.main:app --reload
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app import __version__
from app.api.v1 import health
from app.api.v1.whatsapp import router as whatsapp
from app.application.inbound import InboundHandler, RecordInboundMessages
from app.application.outbound import RecordDeliveryStatuses, StatusHandler
from app.core import telemetry
from app.core.config import Settings, get_settings
from app.core.observability import setup_observability
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
        setup_observability(settings, service_name="scz-b2b-core-api")
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
        # Span de servidor nativo de FastAPI (raíz de la traza, nombre por ruta: baja cardinalidad).
        # Sin logs OTel (llevarían mensajes de excepción) ni spans internos; sin exportadores propios:
        # usa los de configure_telemetry, que redacta url.query y url.path antes de exportar.
        telemetry={"logs": False, "operation_spans": False, "auto_configure": False},
    )
    app.state.settings = settings
    app.state.inbound_handler = inbound_handler
    app.state.status_handler = status_handler
    app.include_router(health.router, prefix="/v1")
    app.include_router(whatsapp.router, prefix="/v1")

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """webhook_ack_ms del webhook de Meta (Q-PERF-1). El span del request lo crea FastAPI."""
        started = time.monotonic()
        response = await call_next(request)
        if request.url.path == WEBHOOK_PATH and request.method == "POST":
            elapsed_ms = (time.monotonic() - started) * 1000
            telemetry.webhook_ack_ms.record(elapsed_ms, telemetry.attributes(status=response.status_code))
        return response

    return app


WEBHOOK_PATH = "/v1/webhooks/whatsapp"


app = create_app()
