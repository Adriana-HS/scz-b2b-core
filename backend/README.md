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
uv run mypy app tests loadtest
uv run lint-imports
uv run pytest                            # unitarias + integración (requiere `supabase db start`; sin BD se omiten)
uv run uvicorn app.main:app --reload     # proceso api en http://127.0.0.1:8000/v1/health
uv run python -m app.worker              # proceso worker
uv run python -m loadtest.run load       # carga y caos del webhook (requiere Docker; ver docs/load-testing.md)
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

## Outbox: envío de mensajes (#18, ADR-0017)

Nada se envía directo a Meta. Cualquier parte del sistema encola, y el worker envía:

```python
async with uow_factory(TenantContext(org_id, Actor.BOT)) as uow:
    await uow.outbound.enqueue(
        OutboundRequest.text(conversation_id, f"turno:{conversation_id}:{wamid}", texto)
    )
```

* **`idempotency_key`:** la misma clave no encola dos veces. Para una respuesta del agente: `conversación + último wa_message_id consumido` (§10.1).
* **Fuera de la ventana de 24 h solo `OutboundRequest.template(...)`,** con consentimiento. Si no, la BD rechaza con `WINDOW_CLOSED` o `CONSENT_REQUIRED`.
* **Plantillas:** `OutboundRequest.template(..., category=TemplateCategory.UTILITY | MARKETING)`. La categoría define el consentimiento exigido (#13).
* **Consentimiento:** `uow.consent.record(customer_id, ConsentKind.OPTIN_MARKETING, ConsentSource.AGENT, evidencia)` actualiza el estado y deja la evidencia en `consent_events`. El webhook ya maneja BAJA/ALTA por WhatsApp.
* **Configuración:** el worker envía solo si hay `META_GRAPH_API_VERSION` (por ejemplo `v23.0`). El token de cada número es la variable de entorno que nombra `whatsapp_channels.access_token_ref` (`WA_TOKEN_*`).

## IA (#22)

`LLM_PROVIDER` elige el adaptador: `anthropic` (por defecto, producción, ADR-0018) o `gemini` (**solo `local` y `test`**, plan gratuito de Google AI Studio, ADR-0019; solo con datos sintéticos). Para probar con Gemini: una clave de AI Studio en `LLM_API_KEY` (en tu `.env`, nunca en el repositorio), `LLM_MODEL` con un modelo estable (p. ej. `gemini-3.8-flash`) y los `LLM_PRICE_*` del plan pago, para que `llm_calls` muestre lo que costaría.

Puertos `ChatModel`, `VisionExtractor`, `Embedder` y `SpeechToText` en `app/domain/ports.py`. El agente usa `resilient_chat_model(...)` (`app/application/ai.py`), que envuelve el adaptador de Anthropic (`app/infrastructure/ai/anthropic.py`) con reintento, circuit breaker, costo en `private.llm_calls` y trazas. Configuración: `LLM_API_KEY`, `LLM_MODEL` (identificador exacto) y los cuatro `LLM_PRICE_*_USD_PER_MTOK`. Detalle: [`docs/ai-ports.md`](../docs/ai-ports.md).

## Herramientas del agente (#24)

`AgentToolbox(uow_factory, TurnContext(org, customer, conversation), enabled=...)` (`app/application/agent_tools.py`): `specs()` entrega los `ToolSpec` para el modelo y `execute(tool_call)` devuelve un `ToolResult` en JSON. Para una herramienta nueva: sus argumentos en `app/domain/agent_tools.py` (con `extra="forbid"`), su handler, su texto y sus mensajes de error; el negocio y el cliente nunca van en los argumentos.

## Orquestador del agente (#81)

`ProcessConversation` (`app/application/agent.py`) atiende un turno: lee el estado, llama al modelo con el prompt del rubro y las herramientas habilitadas (2 rondas, 4 herramientas y 20 s como máximo) y deja en una transacción la respuesta en el outbox, el historial y los mensajes atendidos. Si se pasa de los límites o la IA no responde, contesta una plantilla (`TEMPLATE_LIMIT`, `TEMPLATE_AI_UNAVAILABLE`). Para un rubro nuevo: una clase con `enabled_tools` y `system_prompt(business_name)` en `app/verticals/` y su entrada en `VERTICAL_POLICIES`; un rubro sin política es un error permanente del trabajo, no una respuesta inventada.

### Aviso de IA (#29)

La primera respuesta del bot a cada cliente empieza con el aviso de IA (RF-03) y registra `customers.privacy_notice_at`. Para que un negocio use su propio texto: `organizations.config.ai_notice` (de 30 a 500 caracteres, de usted, diciendo cómo pedir una persona). El texto por defecto está en `app/verticals/common.py` y lo revisa AHS.

### Guardrails y cuotas (#25)

Antes de enviar, el orquestador verifica la respuesta con `app/domain/guardrails.py`: ningún importe sin herramienta que lo respalde y ningún pedido de datos sensibles. Antes de llamar a la IA, revisa las cuotas: `AGENT_DAILY_BUDGET_USD` (por negocio y día), `AGENT_CUSTOMER_MESSAGES_PER_MINUTE` y `AGENT_CUSTOMER_TURNS_PER_HOUR` (por cliente). Cambiar un tope es una decisión de presupuesto (AHS).

## Worker (ADR-0005)

`uv run python -m app.worker` (requiere `DATABASE_URL`).
* **Handlers:** se registran en `build_handlers()` de `app/worker.py`. `SEND_OUTBOUND` (outbox, #18) se registra si hay `META_GRAPH_API_VERSION`; `PROCESS_CONVERSATION` (orquestador del agente, #81) si hay `LLM_API_KEY`, `LLM_MODEL` y los cuatro precios; si falta algo, el worker lo avisa en el log y no lo reclama. `EMBED_ITEM` llega con el #23. Sin handler, el worker no reclama ese tipo; el trabajo espera en la cola sin gastar intentos.
* **Contrato de un handler (*at-least-once*):** puede ejecutarse más de una vez, así que debe ser idempotente. Trabaja dentro de una `UnitOfWork` (si el worker muere, se revierte) y envía mensajes por el outbox con clave de idempotencia (#18). `PermanentJobError` = no reintentar.
* **Variables:** `WORKER_CONCURRENCY` (16), `WORKER_POLL_INTERVAL_S` (0,25), `JOB_TIMEOUT_S` (60) y `JOB_LEASE_S` (300; debe ser mayor que el timeout).

## Observabilidad (#19)

Logs JSON sin PII por stdout, trazas y métricas OpenTelemetry (se exportan solo con `OTEL_EXPORTER_OTLP_ENDPOINT`) y Sentry (solo con `SENTRY_DSN`). Se configuran al arrancar cada proceso con `setup_observability` (`app/core/observability.py`). Detalle, catálogo de métricas y alertas: [`docs/observability.md`](../docs/observability.md).

* **Una métrica nueva** se crea en `app/core/telemetry.py` y se agrega a `METRIC_NAMES`; sus atributos salen de un conjunto cerrado (nunca un teléfono, un texto ni una ruta libre).
* **Un log** lleva identificadores y conteos, nunca contenido del cliente. `redact` es la red de seguridad, no un permiso.
* **Un trabajo nuevo** hereda la traza de quien lo encola sin código extra (`jobs.trace_context`).

Configuración: variables de entorno (ver `.env.example` en la raíz). En `staging` y `production`, el proceso no arranca sin `DATABASE_URL`, `META_APP_SECRET` y `META_WEBHOOK_VERIFY_TOKEN`.
