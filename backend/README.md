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
uv run pytest                            # incluye cobertura (mínimo 85 %)
uv run uvicorn app.main:app --reload     # proceso api en http://127.0.0.1:8000/v1/health
uv run python -m app.worker              # proceso worker
```

Configuración: variables de entorno (ver `.env.example` en la raíz). En `staging` y `production`, el proceso no arranca sin `DATABASE_URL` y `META_APP_SECRET`.
