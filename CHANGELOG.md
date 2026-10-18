# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Decisiones
- **ADR-0019 (aceptado):** Gemini con el plan gratuito de Google AI Studio, **solo para pruebas de desarrollo** del agente, sin costo. Anthropic sigue en producción (ADR-0018). El plan gratuito permite a Google usar y leer lo enviado: solo datos sintéticos, y la configuración no arranca con Gemini en staging ni producción.
- **ADR-0018 (aceptado):** Anthropic es el único proveedor de IA del agente; no se hace comparación con otros proveedores, para no sumar costo ni cuentas. Modelo inicial: Claude Haiku 5.5, confirmado por AHS; si las evals (#28) muestran que no alcanza, se revisa. Se descartan DeepSeek (datos en China) y correr un modelo en una computadora local (sin GPU no alcanza el tiempo de respuesta).
- **Referencias corregidas:** varios documentos y comentarios atribuían el agente al #26 (que es el PDF) y EMBED_ITEM al #25. Ahora dicen #23 (embeddings), #24 (herramientas), #25 (guardrails y cuotas) y **#81**, el issue nuevo del orquestador del agente (`PROCESS_CONVERSATION`), que faltaba en el backlog (también en `scripts/github/backlog.json`). Los comentarios de las migraciones 18 y 19 quedan como están: las migraciones aplicadas no se editan.

### Seguridad (CI)
- **Actions fijadas por SHA:**
  - `actions/checkout` v7.0.1, `actions/setup-python` v7.0.0, `supabase/setup-cli` v3.0.1 y `astral-sh/setup-uv` v10.2.0, con la versión en un comentario.
  - Los PRs de Dependabot #66-#69 subieron estas actions **por etiqueta** (`@v7`, `@v3`). Este cambio las fija por SHA, con los mismos commits que esas etiquetas para checkout, setup-python y setup-cli.
  - `setup-uv` pasa de `@v7` (lo que trajo #68) a v10.2.0: desde la v8 ya no publica etiquetas mayores, justamente para obligar a fijar por SHA, y por eso Dependabot no podía proponer más que `@v7`.
- `check_repo.py` rechaza cualquier `uses:` sin SHA de 40 caracteres o sin la versión en un comentario.
- `persist-credentials: false` en todos los `checkout`; `prune-cache: true` en `setup-uv` (desde v9 el default es `false`).
- El binario de gitleaks se verifica por sha256 antes de extraerlo.

### Agregado (base de datos)
- **Seeds sintéticos por rubro (#27):** `supabase/seed/seed_importadora.sql` (30 repuestos de 8 categorías en USD y Bs, sin marcas, TC inicial 11,68 Bs/USD) y `seed_clinica.sql` (16 servicios, 2 profesionales con horario). Sin clientes ni datos personales; números y tokens ficticios. Los cargan `supabase db start` y `db reset`.
  - Prueba pgTAP `17_seeds` (13 casos); 281 pgTAP en total (con las de #22). Dos mutaciones comprobadas: con una marca real, o sin el TC, falla.
  - Las pruebas 03 y 15 dejan de suponer una base vacía: la 03 reclama solo `PROCESS_CONVERSATION`, como el worker real (los seeds dejan `EMBED_ITEM` en cola), y la 15 cuenta el TC vencido contra una línea de base.

### Agregado (backend)
- **Aviso de IA y privacidad en el primer contacto (#29, RF-03):** la primera respuesta del bot a cada cliente empieza con el aviso (asistente automático con IA, cómo pedir una persona y para qué se usan sus mensajes), de usted y en el mismo mensaje. `customers.privacy_notice_at` se registra en la misma transacción, solo la primera vez. El negocio puede aprobar su propio texto en `organizations.config.ai_notice`; si falta o no es válido, va el de por defecto. Sin migración.
  - Pruebas: 612 de backend (20 nuevas); 12 mutaciones comprobadas.
- **Guardrails y cuotas del agente (#25, §7.4, RF-07, RF-09, RF-51):**
  - **Grounding numérico:** todo importe que diga el bot (con moneda o decimales, en formato boliviano o inglés) tiene que venir de una herramienta de ese turno; si no, plantilla. Cubre precios y descuentos inventados, cuentas propias del modelo y precios dictados por el cliente (inyección).
  - **Datos sensibles:** si el bot pide CI, NIT, tarjeta o número de cuenta, la respuesta no sale.
  - **Cuotas, decididas por AHS (18-oct-2026):** US$ 0,75 de IA por negocio y día de Bolivia (`AGENT_DAILY_BUDGET_USD`); al 80 % alerta, al 100 % plantilla y paso a una persona sin llamar a la IA. Por cliente, 10 mensajes por minuto y 30 respuestas por hora; si se pasa, un aviso sin IA y una sola vez. Iguales para todos hasta que haya planes de cobro.
  - Métrica `llm_budget_alerts{level}`, alertas `LlmBudgetNearLimit` y `LlmBudgetExceeded`, runbook `cuota-ia.md`. Sin migración: el costo sale de `private.llm_calls`.
  - Pruebas: 592 de backend (64 nuevas); 17 mutaciones comprobadas; alertas probadas con promtool.
- **Adaptador de Gemini (#83, ADR-0019):** `GeminiChatModel` (generateContent con httpx, sin SDK) y `LLM_PROVIDER` (`anthropic` por defecto, o `gemini`).
  - Herramientas con `parametersJsonSchema` y los `$ref` de Pydantic expandidos; firmas de razonamiento (`thoughtSignature`) devueltas en la ronda siguiente por `ToolCall.signature`; STOP con herramientas se trata como TOOL_USE; el prompt cacheado y el razonamiento se cuentan aparte en el costo.
  - Errores con código estable: 429 y 5xx transitorios (con la espera de `RetryInfo`), 4xx permanentes; la clave va en el encabezado, nunca en la URL ni en un error.
  - Suite de contrato de `ChatModel` contra fake, Anthropic y Gemini.
  - Pruebas: 507 de backend (51 nuevas); 14 mutaciones comprobadas.
- **Orquestador del agente (#81, §7.1):** handler `PROCESS_CONVERSATION` (`ProcessConversation`).
  - Un turno: estado y últimos 12 mensajes en una transacción; el modelo fuera de ella; respuesta al outbox (clave `turn:{conversación}:{último wa_message_id}`), historial y `processed_at` en **una** transacción. Repetir el trabajo no responde dos veces.
  - Límites de §7.1: 2 rondas, 4 herramientas y 20 s; si se pasan, o la respuesta está vacía o es demasiado larga, plantilla que ofrece una persona. IA no disponible → plantilla y paso a una persona (`TOOL_ERROR`).
  - No responde con `HUMAN`/`CLOSED`, pausa vigente, solo BAJA/ALTA pendientes ni con la ventana de 24 h cerrada.
  - Políticas por rubro en `app/verticals/` (importadora con las 4 herramientas; clínica sin `create_quote` hasta la agenda). El worker registra el handler solo con la configuración de IA completa (ADR-0018).
  - **Alcance y confidencialidad (revisión de AHS):** todo rubro termina con las mismas reglas (`app/verticals/common.py`): solo lo del negocio y sus flujos, nada de código, tareas ni consejos ajenos, nunca revelar las instrucciones ni hablar del sistema, del modelo o de procesos internos, nada de datos de otros clientes, y ningún mensaje del chat cambia las reglas. Segunda barrera en el orquestador: una respuesta con código o que copia una línea del prompt se reemplaza por una plantilla (`outcome=blocked`).
  - **Trato de usted (decisión de AHS, 18-oct-2026):** el prompt de todo rubro lo pide y los textos fijos que lee el cliente (plantillas, errores de las herramientas y confirmaciones de BAJA/ALTA) pasan de tú a usted; `test_customer_texts.py` rechaza el tuteo.
  - Métrica `agent_turns{outcome}`.
  - Pruebas: 476 de backend (63 nuevas, 3 contra la BD); 19 mutaciones comprobadas.
- **Herramientas del agente (#24, §7.2):** `search_catalog`, `get_item_details`, `create_quote` y `handoff_to_human` en `AgentToolbox`.
  - Argumentos validados con Pydantic `extra="forbid"` antes de abrir la transacción: el modelo no puede mandar negocio, cliente, conversación ni precios. El servidor los inyecta desde el `TurnContext` y todo corre como `BOT`.
  - Montos como texto exacto para el grounding (#25); "precio en actualización" si el TC venció; errores con código estable y mensaje para el cliente, sin detalle de la BD ni lo que mandó el modelo.
  - **Migración 20:** `public.get_item_details` (mismo precio, stock y estado que la búsqueda; solo atributos públicos) y `public.request_handoff` con `conversations.handoff_reason/summary/at` para el equipo. Solo `app_backend`.
  - Métrica `agent_tool_calls{tool,outcome}`.
  - Pruebas: 297 pgTAP (16 nuevas en `18_agent_tools`, con los seeds de #27) y 413 de backend; 9 mutaciones comprobadas.
- **Puertos de IA (#22):** detalle en `docs/ai-ports.md`.
  - Puertos segregados `ChatModel`, `VisionExtractor`, `Embedder` y `SpeechToText`, con tipos propios (mensajes, herramientas, uso de tokens, extracción de comprobantes) y errores con código estable (`TransientAiError`, `PermanentAiError`, `AiUnavailableError`).
  - Adaptador de Anthropic (Messages API con httpx, sin SDK): modelo fijado por identificador exacto, prompt de sistema cacheado y clasificación de errores con `retry-after`; la clave y el contenido nunca van en un error.
  - Decoradores en orden fijo (`resilient_chat_model`): trazas `gen_ai` sin contenido → costo por llamada en `private.llm_calls` y `llm_cost_usd` → circuit breaker → reintento con presupuesto.
  - `LLM_MODEL` rechaza alias como `-latest`; con modelo y sin los cuatro precios el proceso no arranca.
  - **Migración 19:** FK compuesta de `llm_calls` con `conversations` y `CHECK` de tokens, costo y latencia.
  - Suite de contrato compartida entre fakes y adaptadores.
  - Pruebas: 268 pgTAP y 377 de backend (con las de carga de #21); 12 mutaciones comprobadas.
- **Pruebas de carga y de caos del webhook (#21, #16, Q-PERF-1, Q-REL-1):** detalle en `docs/load-testing.md`.
  - `backend/loadtest`: k6 1.3.0 (imagen oficial por digest) simula a Meta: mensajes firmados con HMAC, reintentos con backoff y reenvíos del mismo mensaje. `run.py` prepara un negocio sintético, levanta la api real y verifica la BD al final: 0 perdidos, 0 duplicados, eventos atómicos y todo mensaje con su trabajo.
  - Tres escenarios: `load` (50 rps, p99 ≤ 250 ms), `chaos-db` (Postgres reiniciado en medio de la carga) y `chaos-api` (api muerta con SIGKILL). Un caos que no llegó a producir fallas sale en rojo.
  - Workflow **Carga y caos** (`resiliencia.yml`): cada noche, a mano y en los PRs que tocan el camino del webhook.
  - El reporte mide la latencia ida y vuelta a la BD: el webhook hace ~9 viajes por request, así que con Docker Desktop (~49 ms por viaje) el SLO no se puede medir en local; se mide en Linux (CI).
  - Pruebas: 16 del veredicto (284 de backend en total); mutaciones contra el sistema real (responder 200 sin persistir → 8 perdidos detectados; no encolar el trabajo → 100 conversaciones detectadas).
- **Observabilidad (#19, §10.2):** detalle en `docs/observability.md`.
  - **Logs JSON** por stdout con `trace_id`, `span_id` y el contexto del trabajo (`job_id`, `job_kind`, `organization_id`). El mensaje, el traceback y el contexto pasan por `redact`: teléfonos, correos, `Bearer`, tokens de Meta y parámetros `*token`/`*key`/`*secret` (incluido `hub.verify_token`).
  - **Trazas de punta a punta:** span de servidor nativo de FastAPI → la UnitOfWork fija `app.traceparent` → **migración 18:** `jobs.trace_context` lo toma por defecto (uno mal formado se descarta) → el worker continúa la traza. `RedactingSpanProcessor` deja `url.query` sin valores y redacta `url.path` antes de exportar.
  - **Métricas OTLP** de los SLO: ack del webhook, latencia de respuesta, firmas inválidas, mensajes cobrables por categoría, envíos inciertos, resultado y duración de cada trabajo, DEAD, y gauges de la cola por tipo y de TC vencido. **Migración 18:** `private.queue_stats(kinds)` (por tipo, solo los que tienen handler, espera desde `run_after`) y `private.fx_stale_tenants()`, solo para `app_backend`. Atributos de conjuntos cerrados; histogramas con límites en los umbrales de los SLO.
  - **Sentry** solo con `SENTRY_DSN`: `data_collection` cerrado y `scrub_event` en `before_send`. Los logs `ALERTA …` llegan como eventos.
  - **Alertas como código** (`ops/alerts/slo-rules.yaml`, 9 reglas) con pruebas de `promtool` (dispara y no dispara) en el job de CI `alerts`; **6 runbooks** en `docs/runbooks/`.
  - `OTEL_EXPORTER_OTLP_ENDPOINT` debe ser https en staging y producción.
  - Pruebas: 259 pgTAP y 268 de backend; 9 mutaciones comprobadas.
- **Consentimiento con evidencia (#13, RF-08, EXT-16):**
  - **Migración 17:** tabla `consent_events` (append-only, con RLS y FK compuesta); `private.record_consent` actualiza el estado y la evidencia en una transacción; `private.template_consent_ok` es la única regla de plantillas por categoría (UTILITY y MARKETING; la baja prevalece; sin categoría no sale), y la usan el trigger y el worker.
  - BAJA, STOP y NO MÁS (y ALTA o START) por WhatsApp se reconocen solo si son el mensaje completo; se confirman al cliente y no despiertan al agente.
  - `OutboundRequest.template` exige la categoría.
  - `pytest-timeout` (120 s por prueba).
  - Pruebas: 247 pgTAP y 193 de backend; tres mutaciones comprobadas.
- **Outbox y envío por la WhatsApp Cloud API (#18, RF-06, ADR-0017):**
  - Encolar es idempotente por `idempotency_key` y crea el mensaje y su trabajo `SEND_OUTBOUND` en una transacción.
  - **Migración 16:** trigger de ventana de 24 h y de consentimiento para plantillas; estado `SENDING`; la conversación guarda su número (FK compuesta); el backend solo actualiza las columnas del envío.
  - **Handler del worker:** vuelve a verificar la ventana al enviar; marca `SENDING` antes de llamar a Meta y no reenvía si el resultado es incierto (`OUTCOME_UNKNOWN` con alerta); reintenta los errores transitorios; deja `FAILED` los permanentes.
  - Adaptador de la Cloud API con `httpx` y versión fijada (`META_GRAPH_API_VERSION`). El token sale de `WA_TOKEN_*` y nunca aparece en logs.
  - **Webhook de estados:** solo avanzan y no cruzan negocios.
  - Pruebas: 230 pgTAP y 163 de backend; doble de Meta con `httpx.MockTransport`; tres mutaciones comprobadas.
- **Worker (#17, ADR-0005):**
  - `JobRunner` con reclamo continuo y concurrencia acotada, timeout por trabajo y reintento con backoff; `PermanentJobError` va directo a DEAD.
  - En `last_error` se guarda solo el tipo de error. Un DEAD emite el log `ALERTA job_dead`.
  - Corre el mantenimiento (*reaper*, vencimientos y retención), porque Supabase no trae `pg_cron` activo, y se apaga ordenado con SIGTERM.
  - **Migración 15:** `finish_job` con *fencing* (solo cierra quien tiene el *lease*); `claim_jobs` filtra por tipos y saca uno por conversación; `finish_job` y `requeue_stale_jobs` informan los pasos a DEAD. Se eliminan las firmas viejas.
  - **Prueba de caos real:** un proceso worker muerto a mitad de un trabajo no pierde ni duplica.
  - 216 pgTAP y 117 pruebas de backend; mutaciones comprobadas para el *fencing* y para la ausencia de PII.
- **Webhook de WhatsApp (#16, RF-01):** `GET` y `POST /v1/webhooks/whatsapp`.
  - Orden de chequeos: límite de 1 MB (413), firma HMAC-SHA256 sobre los bytes crudos en tiempo constante y antes de parsear (401), traducción del formato de Meta con validación por mensaje.
  - Los mensajes de un número se registran en una sola transacción como `SYSTEM`: evento idempotente por `wa_message_id`, cliente, conversación abierta, mensaje y trabajo con *debounce*.
  - Un número desconocido o inactivo responde 200 sin escribir nada; un fallo de BD, 500 para que Meta reintente.
  - Los logs llevan solo conteos y tipos de error.
  - `META_WEBHOOK_VERIFY_TOKEN` pasa a ser obligatorio en staging y producción.
  - 49 pruebas nuevas (93 en total), con integración contra la BD; mutaciones comprobadas para la firma y la idempotencia. El ack p99 con k6 queda para el #21.
- **UnitOfWork y repositorios asyncpg (#15, ADR-0011):** `PostgresUnitOfWork` abre una transacción como `app_backend` y fija el tenant y el actor con `SET LOCAL` parametrizado; los repositorios de catálogo (`search_catalog`) y órdenes (`create_quote`) no funcionan fuera de ella. Los `HINT` de la BD se traducen a `BusinessRuleError` y `AccessDeniedError` con códigos estables, y una prueba falla si una migración agrega un `HINT` sin código. El pool no cachea *prepared statements* (Supavisor en modo transacción).
- Pruebas de integración contra Postgres real como `app_backend` (12), incluida la de no filtrar el contexto entre transacciones con un doble del pooler en modo transacción. Se comprobó que falla con un `SET` de sesión. La CI levanta Postgres en el job `backend` y no permite omitirlas (`SCZ_REQUIRE_DB=1`). 44 pruebas en total; cobertura 98,5 %.
- **Scaffold del backend hexagonal (#14):** `backend/` con Python 3.12, FastAPI y uv (`uv.lock`). Capas `domain`, `application`, `infrastructure`, `api`, `workers`, `verticals` y `core`; *composition root* en `app/main.py` (proceso api, `GET /v1/health`) y `app/worker.py`. La configuración falla cerrado: en staging y producción no arranca sin `DATABASE_URL` y `META_APP_SECRET`, y la documentación de la API no se publica en producción.
- 5 contratos de `import-linter` para la regla de dependencias (el dominio solo usa stdlib y pydantic). Se comprobó que detectan una violación real.
- Job de CI `backend`: ruff, `mypy --strict`, `lint-imports`, pytest (cobertura ≥ 85 %, avisos como errores) y pip-audit. Dependabot para `uv` y GitHub Actions.

### Corregido
- **Reservas de stock (Alta, H1):** una orden en `PAYMENT_REVIEW` con `valid_until` vencido dejaba de bloquear su stock; otro cliente podía reservarlo y, al aprobar el pago, `stock_on_hand` quedaba < 0 (la aprobación fallaba por `CHECK`) o había sobreventa. Ahora una reserva está activa mientras no se libere ni se comprometa; la termina el estado de la orden, no el reloj (ADR-0015).
- **Reservas de stock (Alta, H2):** cancelar no liberaba la reserva hasta su vencimiento, y cancelar o reembolsar una orden `CONFIRMED` nunca devolvía el stock. Confirmar con seña 0 tampoco comprometía stock. Migración 10: trigger `trg_orders_stock_lifecycle` que compromete, libera o devuelve el stock en cada cambio de estado; columna `stock_reservations.returned_at`. Si al confirmar no hay stock físico, el error es `HINT = STOCK_CONFLICT`, sin efectos parciales.
- Prueba `07_stock_lifecycle.test.sql` (19 casos; fallaba antes del fix). 109 pruebas pgTAP en total.

### Seguridad
- **Precios del catálogo (#50, ADR-0016):** STAFF, OWNER y ADMIN cambiaban precios sin MFA; con una sesión robada, un precio en 0 se congelaba en las cotizaciones nuevas. Migración 14, regla mixta: los cambios dentro del umbral siguen sin MFA; precio 0, cambio de moneda o variación mayor a `organizations.price_change_mfa_threshold_pct` (30 % por defecto) exigen OWNER/ADMIN con aal2; el backend no cambia precios; cada cambio aplicado encola `PRICE_CHANGE_ALERT` para el dueño. Prueba `11_catalog_price_mfa.test.sql` (20 casos). 196 pruebas pgTAP en total.
- **SEC-02 (Media, #53):** tres caminos de menor privilegio sin cerrar en la BD. (M1) Sin `app.actor`, `private.actor_kind()` devolvía `SYSTEM` y el backend podía registrar un pago `BANK_API`; además podía declararse `HUMAN`. (M2) STAFF sin MFA podía reembolsar, cancelar órdenes pagadas o en revisión y confirmar órdenes sin seña. (M3) `app_backend` podía marcar una evidencia `APPROVED` sin la RPC con MFA. Migración 11: actor por defecto `BOT`, `app_backend` sin INSERT en `payments` hasta la fase 7 (ADR-0014), guarda de actor en `payments`, trigger de roles en `orders` y FSM de evidencias en la BD (`private.evidence_transitions`) con columnas acotadas y datos congelados al pasar a revisión. Prueba `08_actor_roles.test.sql` (40 casos; fallaba antes del fix). 149 pruebas pgTAP en total.
- **Orden con pago parcial (Media, #57):** `BOT` y `SYSTEM` podían cancelar una orden con `amount_paid_bs > 0` y `expire_stale_orders` la pasaba a `EXPIRED`: quedaba dinero en `payments` sin orden viva y sin que una persona decidiera el reembolso. Migración 12 (`trg_orders_paid_exit_guard`): cancelarla exige OWNER/ADMIN con aal2 (HINT `HUMAN_REVIEW_REQUIRED` para el backend) y el vencimiento la omite, sin frenar el resto del job. Prueba `09_paid_order_exit.test.sql` (18 casos; fallaba antes del fix). 167 pruebas pgTAP en total.
- **Vencimiento de órdenes con pagos (seguimiento de #57):** `expire_stale_orders` seguía seleccionando cada minuto las órdenes vencidas con `amount_paid_bs > 0`; `trg_orders_paid_exit_guard` las omitía, pero el job bloqueaba esas filas en cada pasada sin efecto. Migración 13: `AND amount_paid_bs = 0` en el `WHERE` del job (mismo cuerpo que en la migración 10); el trigger queda como defensa en profundidad. Prueba `10_expire_skips_paid.test.sql` (9 casos; fallaba antes del fix). 176 pruebas pgTAP en total.
- **SEC-01 (Alta):** una sesión de OWNER/ADMIN sin MFA podía apagar `config.require_mfa_for_payments` y aprobar pagos sin segundo factor. La revisión del fix encontró dos caminos más: dar de alta o ascender un ADMIN, y cargar un tipo de cambio, ambos sin MFA. Migración 09: `aal2` incondicional en la aprobación, en la edición de la organización, en membresías y en el TC; la clave queda prohibida por `CHECK`. Prueba `06_payment_mfa.test.sql` (11 casos; fallaba antes del fix). 90 pruebas pgTAP en total.

### Cambiado (documentación y herramientas)
- `supabase db lint` bloquea el PR (`--fail-on error`; sin esa opción la CLI sale con 0 aunque haya errores). El primer reporte salió limpio. `scripts/test-db.ps1` también falla si el lint falla.
- Skills visibles en Claude Code: puentes en `.claude/skills/` con el mismo frontmatter que `.agents/skills/` (fuente única); `check_repo.py` verifica que coincidan.
- `SYSTEM_DESIGN.md`: 9 migraciones, EXT-01 … EXT-17, la CI corre sobre PostgreSQL 17, `request_payment` usa el QR estático del negocio en el MVP y los diagramas C4 marcan banco y SIAT como fase de escala (ADR-0014). SRS: EXT-01 … EXT-17.
- `supabase/seed/README.md`: los seeds siguen pendientes (issue #27).
- Regla de autoría en `AGENTS.md` y `CONTRIBUTING.md`: AHS es la única autora; ningún asistente de IA figura como coautor en commits ni PRs.

### Agregado (CI)
- CI con versiones fijas (Supabase CLI 2.120.0, gitleaks 8.21.2), cancelación de runs viejos, lint de funciones (informativo) y logs de Postgres si falla.
- Job `repo` con `scripts/ci/check_repo.py`: backlog, enlaces de Markdown, ADRs y nombres de migraciones.
- gitleaks como binario en lugar de `gitleaks-action` (esta pide licencia en repos de organizaciones).
- `scripts/test-db.ps1`: la misma verificación en Windows.
- Migración 08: índices para las 15 claves foráneas que no tenían (regla de supabase/agent-skills).
- Prueba `05_schema_lint.test.sql` (5 guardas de regresión); 79 pruebas pgTAP en total.
- EXT-17: desde el 30-oct-2026 Supabase no expone tablas nuevas a la Data API sin GRANT explícito (el esquema ya usa GRANT explícitos).
- `docs/HERRAMIENTAS_IA.md`: skills oficiales de Supabase y nombres corregidos de trailofbits/skills.
- `bootstrap.py` renombra issues y milestones con título anterior (campo `replaces`) en vez de duplicarlos.

### Cambiado (v2.2 — mínimo regulatorio)
- ADR-0014: MVP con QR estático del negocio y aprobación humana; sin OpenBCB, sin facturación SIAT y sin comisiones por transacción; sin pedir CI/NIT por el chat; WhatsApp en modelo directo para el piloto; importadoras primero.
- `docs/legal/marco-legal-y-modelo-de-negocio.md`: regulación aplicable, requisitos para constituir la empresa, modelo de ingresos y checklist legal.
- Columnas de consentimiento en `customers` (`optin_utility_at`, `optin_marketing_at`, `optout_at`).
- Backlog: 47 issues (nuevos: acuerdo de socios, constitución de la empresa, contratos y privacidad, onboarding directo, opt-in/opt-out).

### Agregado (v2.1)
- ADR-0012 (IVA por régimen vigente, Ley 1733) y ADR-0013 (la plataforma no custodia fondos).
- Modelo de IVA en la BD: `tax_regimes`, `orders.subtotal_bs/iva_bs/tax_regime_id`, `private.tax_breakdown`; 11 pruebas pgTAP nuevas (74 en total).
- Registro de restricciones externas (`docs/research/`), SRS con RF-xx, principios SOLID, patrones y UML (`docs/architecture/`).
- Gestión del proyecto: plantillas de issues, backlog de 47 issues en 8 fases y script `scripts/github/bootstrap.py`.
- `AGENTS.md`, `CLAUDE.md` y 5 skills del proyecto en `.agents/skills/`; guía `docs/HERRAMIENTAS_IA.md`.

### Agregado
- `SYSTEM_DESIGN.md` v2.0: requisitos medibles, arquitectura C4, FSM, agente IA, seguridad STRIDE, SLOs, costos.
- 11 ADRs en `docs/adr/`.
- Revisión de la v1 con evidencia reproducible (`docs/reviews/`).
- Esquema de base de datos en 7 migraciones (`supabase/migrations/`): multi-tenancy con RLS también para el backend, FK compuestas, historial de tipo de cambio, cotización atómica con reserva de stock, agenda sin doble reserva, evidencias y libro de pagos, auditoría, cola durable y outbox.
- 63 pruebas pgTAP (`supabase/tests/database/`), luego ampliadas a 74 con las de IVA.
- CI de base de datos, plantilla de PR, guías de seguridad, contribución y pruebas.

### Cambiado
- La sección "Guía de ejecución en Claude Pro" de la v1 pasa a `docs/ROADMAP.md` como plan por fases.
