# Backend de SCZ-B2B-Core

Monolito modular hexagonal en Python 3.12 + FastAPI, desplegado como dos procesos de la misma imagen (ADR-0001): `api` (webhooks y endpoints, sin trabajo pesado) y `worker` (cola durable, ADR-0005).

## Capas

| Paquete | Contiene | Puede importar |
| :--- | :--- | :--- |
| `app/domain` | Entidades, value objects, políticas, reglas antifraude, **puertos** (`Protocol`) | Solo stdlib y `pydantic` |
| `app/application` | Casos de uso, orquestador del agente, guardrails | `domain` |
| `app/infrastructure` | Adaptadores de salida: Postgres (`UnitOfWork`), WhatsApp, LLM, Storage, PDF | `domain`, `core` |
| `app/verticals` | Un `VerticalPolicy` por rubro | `domain`, `application` |
| `app/api`, `app/workers` | Adaptadores de entrada (HTTP y handlers de la cola); no se importan entre sí | `application`, `domain`, `core` |
| `app/core` | Configuración por entorno, logging, telemetría | stdlib y librerías |
| `app/main.py`, `app/worker.py` | *Composition root* de cada proceso: el único lugar donde se conectan adaptadores con casos de uso | Todo |

La regla de dependencias está en `[tool.importlinter]` de `pyproject.toml`. La verifican `lint-imports` y la prueba `tests/architecture/test_layers.py`.

## Comandos

Requisitos: [uv](https://docs.astral.sh/uv/). Desde esta carpeta:

```bash
uv sync                                  # entorno con las versiones exactas de uv.lock
uv run ruff check . && uv run ruff format --check .
uv run mypy app tests
uv run lint-imports
uv run pytest                            # unitarias + integración (requiere `supabase db start`; sin BD se omiten)
uv run uvicorn app.main:app --reload     # proceso api en http://127.0.0.1:8000/v1/health
uv run python -m app.worker              # proceso worker
```

## Acceso a datos (ADR-0011)

```python
async with uow_factory(TenantContext(organization_id, Actor.BOT)) as uow:
    matches = await uow.catalog.search(CatalogSearch(query="pastilla freno hilux"))
    quote = await uow.orders.create_quote(QuoteRequest(customer_id=..., conversation_id=..., lines=(...)))
```

* `PostgresUnitOfWork` abre la transacción y fija el tenant y el actor con `set_config(..., true)` (`SET LOCAL` parametrizado). Confirma al salir sin error y revierte si hay una excepción.
* Los repositorios solo funcionan dentro de su `UnitOfWork`; fuera lanzan `UnitOfWorkNotActiveError`.
* Los `HINT` de las RPC se traducen a `BusinessRuleError` (P0001) o `AccessDeniedError` (42501, incluida la RLS) con un `ErrorCode` estable. Una prueba falla si una migración agrega un `HINT` sin su código en `app/domain/errors.py`.
* El backend solo actúa como `BOT` o `SYSTEM`; nunca como `HUMAN` (SEC-02).

## Webhook de WhatsApp (RF-01)

* `GET /v1/webhooks/whatsapp`: verificación de la suscripción de Meta. Devuelve `hub.challenge` si `hub.verify_token` coincide con `META_WEBHOOK_VERIFY_TOKEN`; si no, 403.
* `POST /v1/webhooks/whatsapp`: 413 si el cuerpo pasa de 1 MB; 401 si la firma `X-Hub-Signature-256` no corresponde a `META_APP_SECRET` (se verifica antes de parsear); 200 si se registró, si era un duplicado o si el número no es de ningún negocio activo; 500 si la BD falla, para que Meta reintente.
* Traducción del formato de Meta: `app/api/v1/whatsapp/payload.py`. Caso de uso: `app/application/inbound.py`. Persistencia: `app/infrastructure/postgres/inbound.py` y `channels.py`.

## Worker (ADR-0005)

`uv run python -m app.worker` (requiere `DATABASE_URL`).
* **Handlers:** se registran en `build_handlers()` de `app/worker.py`. Todavía no hay ninguno: `EMBED_ITEM` llega con el #25 y `PROCESS_CONVERSATION` con el #26. Sin handler, el worker no reclama ese tipo; el trabajo espera en la cola sin gastar intentos.
* **Contrato de un handler (*at-least-once*):** puede ejecutarse más de una vez, así que debe ser idempotente. Trabaja dentro de una `UnitOfWork` (si el worker muere, se revierte) y envía mensajes por el outbox con clave de idempotencia (#18). `PermanentJobError` = no reintentar.
* **Variables:** `WORKER_CONCURRENCY` (16), `WORKER_POLL_INTERVAL_S` (0,25), `JOB_TIMEOUT_S` (60) y `JOB_LEASE_S` (300; debe ser mayor que el timeout).

Configuración: variables de entorno (ver `.env.example` en la raíz). En `staging` y `production`, el proceso no arranca sin `DATABASE_URL`, `META_APP_SECRET` y `META_WEBHOOK_VERIFY_TOKEN`.
