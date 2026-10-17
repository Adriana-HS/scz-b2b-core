# Pruebas

## Base de datos (pgTAP)

```bash
supabase db start      # solo Postgres + migraciones
supabase test db
```

En Windows, `.\scripts\test-db.ps1` corre lo mismo que la CI: migraciones, pgTAP, lint y `check_repo.py`. Requiere Docker Desktop. Si la Supabase CLI no está instalada, usa `npx supabase@<versión de la CI>` (requiere Node.js). También evita un fallo de la CLI 2.120.0 en Windows con la carpeta temporal (`makeTempDirectoryScoped NotFound`) usando `%LOCALAPPDATA%\scz-b2b-core-tmp`.

### Qué corre la CI (`.github/workflows/ci.yml`)

| Job | Qué verifica | Bloquea el PR |
| :--- | :--- | :---: |
| `database` | Migraciones y seeds sobre la imagen oficial de Supabase y las 281 pruebas pgTAP | Sí |
| `database` → lint | `supabase db lint --fail-on error` (plpgsql_check) sobre las funciones | Sí (el primer reporte, del 16-oct-2026, salió sin errores) |
| `backend` | `ruff`, `mypy --strict`, `import-linter`, `pytest` (cobertura ≥ 85 %) y `pip-audit` sobre `backend/` (ver abajo) | Sí |
| `alerts` | `promtool check rules` y `promtool test rules` sobre `ops/alerts/` (imagen oficial de Prometheus fijada por digest): sintaxis y disparo de cada alerta con series sintéticas | Sí |
| `repo` | `scripts/ci/check_repo.py`: backlog coherente, enlaces de Markdown, numeración de ADRs, nombres de migraciones, puentes de skills y **actions fijadas por SHA** | Sí |
| **Carga y caos** (`resiliencia.yml`, cada noche y en PRs que tocan el webhook) | `loadtest.run` con k6: 50 rps con p99 ≤ 250 ms; Postgres reiniciado y api muerta a mitad de la carga sin perder ni duplicar mensajes (`docs/load-testing.md`) | Sí, en los PRs donde corre |
| `secrets` | gitleaks sobre todo el historial (binario verificado por sha256). `.gitleaksignore` guarda solo las huellas de falsos positivos revisados uno por uno (commit:archivo:regla:línea), nunca reglas ni carpetas enteras | Sí |

La versión de Supabase CLI está fijada en `SUPABASE_CLI_VERSION`. Se actualiza en un PR propio, para que un cambio de la CLI no se mezcle con un cambio del esquema.

| Archivo | Cubre | Pruebas |
| :--- | :--- | ---: |
| `01_tenant_isolation.test.sql` | RLS por rol (anon, dashboard por rol, backend con y sin tenant), FK compuestas, Storage, `search_path`, RLS al 100 % | 21 |
| `02_pricing_fsm_payments.test.sql` | Totales exactos, TC congelado, inyección de precio, stock, FSM por actor, doble candado de evidencias, aprobación con MFA, auditoría, append-only | 24 |
| `03_booking_search_queue.test.sql` | Disponibilidad y zona horaria, EXCLUDE anti doble reserva, vencimientos, búsqueda híbrida, TC vencido, debounce y serialización de la cola | 18 |
| `04_tax_regimes.test.sql` | IVA incluido y por fuera (Ley 1733), régimen congelado por orden, seña sobre el total, régimen global de solo lectura | 11 |
| `05_schema_lint.test.sql` | Guardas de regresión: FK con índice, RLS en todas las tablas, `search_path` en SECURITY DEFINER, vistas con `security_invoker`, `anon` sin privilegios | 5 |
| `06_payment_mfa.test.sql` | SEC-01: MFA incondicional para aprobar pagos, editar la organización, cambiar membresías y cargar TC; el interruptor `require_mfa_for_payments` ya no existe | 11 |
| `07_stock_lifecycle.test.sql` | Reservas de stock (ADR-0015): una orden en revisión con `valid_until` vencido sigue bloqueando; cancelar libera; cancelar o reembolsar una confirmada devuelve el stock; seña 0 compromete; `STOCK_CONFLICT` sin efectos parciales | 19 |
| `08_actor_roles.test.sql` | SEC-02: actor por defecto `BOT` (el backend nunca es `HUMAN`), sin pagos bancarios en el MVP, transiciones humanas por rol (STAFF solo cancela antes del pago; el resto OWNER/ADMIN con MFA), FSM de evidencias y datos congelados | 40 |
| `09_paid_order_exit.test.sql` | Orden con pagos registrados (#57): `BOT`, `SYSTEM`, STAFF y OWNER sin MFA no la cancelan; el vencimiento la omite sin frenar al resto; OWNER/ADMIN con MFA la cancela y queda auditado | 18 |
| `10_expire_skips_paid.test.sql` | Seguimiento de #57: el `WHERE` de `expire_stale_orders` excluye `amount_paid_bs > 0`; con el trigger desactivado el job igual deja viva la orden pagada (y su reserva); el trigger sigue omitiendo un `UPDATE` directo a `EXPIRED` | 9 |
| `11_catalog_price_mfa.test.sql` | #50 (ADR-0016): STAFF cambia precios dentro del umbral sin MFA; precio 0, cambio de moneda o variación mayor al umbral exigen OWNER/ADMIN con aal2; umbral por negocio; el backend no cambia precios; aviso al dueño y bitácora | 20 |
| `12_worker_queue.test.sql` | #17: reclamo solo de los tipos pedidos y uno por conversación; *fencing* en `finish_job` (otro worker no cierra el trabajo; el cierre tardío del que perdió el *lease* se ignora); backoff, error truncado y DEAD informado; *reaper* con conteos; firmas viejas eliminadas; privilegios | 20 |
| `13_outbox.test.sql` | #18: `idempotency_key` única; fuera de la ventana de 24 h solo plantillas, y estas con opt-in y sin baja; estado `SENDING`; FK compuesta del número de la conversación; aislamiento | 14 |
| `14_consent.test.sql` | #13: `record_consent` deja estado y evidencia; las plantillas por categoría (el opt-in de utilidad no habilita marketing; sin categoría no sale); la baja prevalece y un opt-in explícito la levanta; evidencia append-only y aislada | 17 |
| `15_observability.test.sql` | #19: `jobs.trace_context` hereda el traceparent de la transacción (uno mal formado se descarta sin romper el INSERT); `queue_stats` por tipo, solo de los tipos pedidos y sin contar el backoff; `fx_stale_tenants`; solo `app_backend` | 12 |
| `16_llm_calls.test.sql` | #22: el backend registra el costo de IA en su tenant y no en otro; la conversación debe ser del mismo negocio (FK compuesta, migración 19); tokens, costo y latencia no negativos; aislamiento | 9 |
| `17_seeds.test.sql` | #27: la importadora y la clínica de ejemplo existen; ≥ 25 repuestos en USD y Bs de ≥ 5 categorías, con uno sin stock; TC inicial explícito y todo repuesto cotizable; búsqueda de texto; ≥ 15 servicios con duración y profesionales con horario; sin marcas, sin clientes y con números sintéticos | 13 |
| **Total** | | **281** |

`_fixtures.psql` crea dos tenants sintéticos y helpers (`tests.as_user`, `tests.as_backend`, `tests.as_anon`). Cada archivo corre dentro de `BEGIN … ROLLBACK`.

### Verificación local sin Docker

La CI corre sobre la imagen oficial de Supabase con PostgreSQL 17 (`supabase/config.toml`, `major_version = 17`). Antes de tener CI, el esquema se verificó en PostgreSQL 16 + pgvector 0.8 + pgTAP con el *shim* `scripts/local_supabase_shim.sql`, que emula roles y `auth.uid()` de Supabase. **No reemplaza** a `supabase test db`: es solo para entornos sin Docker.

```bash
createdb scz_test
psql -d scz_test -f scripts/local_supabase_shim.sql
for f in supabase/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -d scz_test -f "$f"; done
for f in supabase/tests/database/0*.test.sql; do psql -d scz_test -f "$f"; done
```

## Backend (Python)

Desde `backend/` (requiere [uv](https://docs.astral.sh/uv/)):

```bash
uv sync && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests loadtest && uv run lint-imports && uv run pytest
```

| Verificación | Qué asegura | Bloquea el PR |
| :--- | :--- | :---: |
| `ruff` | Estilo, errores comunes y reglas de seguridad (bandit) | Sí |
| `mypy --strict` | Tipos en `app/`, `tests/` y `loadtest/` | Sí |
| `lint-imports` | Regla de dependencias de la arquitectura hexagonal: el dominio no importa otras capas ni SDKs (5 contratos en `pyproject.toml`). También corre como prueba (`tests/architecture`) | Sí |
| `pytest` | Pruebas unitarias y de integración con cobertura mínima de 85 %, avisos tratados como errores y un máximo de 120 s por prueba (`pytest-timeout`: una prueba colgada falla con su traceback) | Sí |
| `pip-audit` | Vulnerabilidades conocidas en las dependencias de `uv.lock` | Sí |

### Webhook de WhatsApp (RF-01)

| Criterio | Prueba |
| :--- | :--- |
| Firma inválida → 401 sin parsear el JSON | `test_webhook_api.py` (JSON roto sin firma da 401, no 400) y `test_whatsapp_signature.py` (formatos, otro secreto, cuerpo alterado, sin secreto configurado) |
| Cuerpo > 1 MB → 413 | `test_webhook_api.py::test_cuerpo_de_mas_de_1_mb_es_413` |
| Un duplicado no crea filas nuevas | `test_inbound_db.py::test_un_reenvio_de_meta_no_crea_filas_nuevas` (BD real) |
| Atomicidad del lote | `test_inbound_db.py::test_si_falla_un_mensaje_no_queda_nada_del_lote` |
| Sin PII en logs ni en respuestas de error | `test_webhook_api.py` (`caplog`) |
| Ack p99 ≤ 250 ms a 50 rps | `loadtest.run load` en el workflow **Carga y caos** (ver `docs/load-testing.md`) |

Se comprobó con mutaciones temporales que las pruebas de los dos primeros criterios de la BD fallan si el código se rompe a propósito: parsear el JSON antes de verificar la firma, y quitar el `ON CONFLICT DO NOTHING` del evento.

### Worker y prueba de caos (#17, Q-REL-1)

`backend/tests/integration/test_worker_db.py::test_matar_el_worker_a_mitad_de_un_trabajo_no_pierde_ni_duplica`:
1. Lanza un proceso worker aparte (`chaos_worker.py`) que reclama un trabajo, abre una transacción, escribe un mensaje y se queda colgado.
2. La prueba **mata el proceso**. Lo escrito se revierte (0 filas) y el trabajo queda `RUNNING` con el *lease* del muerto.
3. El *reaper* lo libera y otro worker lo termina: queda `DONE`, con `attempts = 2` y el mensaje **una sola vez**.

Mutaciones comprobadas:
* Sin *fencing* en `finish_job`, fallan 4 pruebas pgTAP.
* Si se guarda el mensaje del error en vez de su tipo, fallan 5 pruebas unitarias.

### Outbox y envío por la Cloud API (#18)

* **Doble de Meta:** `tests/unit/test_cloud_api.py` usa `httpx.MockTransport`. Verifica la URL, el token en el encabezado, el cuerpo de texto, documento y plantilla, y la clasificación de cada respuesta (429/5xx/token → reintento; 131047 y otros 4xx → permanente). Comprueba que el token no aparezca en los errores.
* **Handler:** `tests/unit/test_send_outbound.py` cubre el orden de commits (`SENDING` antes de Meta), la ventana al enviar, el consentimiento, la falta de token y el caso de resultado incierto (no reenvía).
* **Contra la BD real:** `tests/integration/test_outbox_db.py` cubre que encolar es atómico y no duplica, el rechazo de la ventana, el envío completo por el número de la conversación y los estados que solo avanzan, también entre negocios.
* **Mutaciones comprobadas:**
  * reenviar un mensaje en `SENDING` → falla la prueba de duplicados;
  * estados sin orden → falla "solo avanzan";
  * trigger sin ventana → fallan 3 pruebas pgTAP.

### Consentimiento (#13, RF-08)

* **Palabras clave** (`test_consent.py`): "BAJA", "¡No MÁS!", "stop!" y "cancelar suscripción" cuentan como baja. "No más frenos, quiero cotizar", "la baja de precios" y "stop de luz trasero" **no**: se compara el mensaje completo normalizado.
* **Flujo** (`test_record_inbound.py`): una BAJA registra la evidencia, encola la confirmación y no despierta al agente.
* **Contra la BD** (`test_consent_db.py`):
  * BAJA por WhatsApp → `optout_at`, un evento con el `wa_message_id` y una confirmación en el outbox, sin turno del agente;
  * después de la baja ninguna plantilla sale;
  * ALTA habilita utilidad pero no marketing.
* **Mutaciones comprobadas:**
  * baja por subcadena → fallan 3 pruebas;
  * la BAJA despierta al agente → falla 1;
  * consentimiento sin categoría → fallan 2 pgTAP.

### Observabilidad (#19)

* **Telemetría en memoria:** `tests/conftest.py` configura OpenTelemetry una vez con `InMemorySpanExporter` e `InMemoryMetricReader`; los fixtures `spans` y `metric` leen lo emitido. Cada prueba compara el valor de una métrica antes y después.
* **Logs** (`test_observability.py`): JSON de una línea, redacción del mensaje, traceback y contexto, `trace_id` del span activo, `job_id` del worker.
* **Trazas:** el traceparent viaja webhook → UoW (`test_unit_of_work.py`) → `jobs.trace_context` (`test_observability_db.py`, BD real) → span del worker con el mismo `trace_id`; el `verify_token` de la query no llega al span.
* **Métricas:** resultado de cada trabajo, DEAD, gauges de la cola por tipo, mensajes cobrables por categoría (conjunto cerrado), latencia de respuesta solo para mensajes libres, límites de los histogramas en los umbrales de los SLO.
* **Sentry:** `scrub_event` y un envío real con un transporte de prueba: el evento no lleva el teléfono.
* **Alertas:** `test_alert_rules.py` verifica que cada alerta use métricas del catálogo (`METRIC_NAMES`), tenga severidad, SLO y runbook existente, y tenga un caso que dispara y otro que no en `slo-rules.test.yaml` (que corre `promtool`).
* **Mutaciones comprobadas** (9 de 9 detectadas):
  * log sin redactar, worker que ignora `trace_context`, cola que no lo lee, UoW sin `app.traceparent`, sin el procesador que redacta la URL, Sentry con encabezados, categoría sin conjunto cerrado → fallan pruebas de backend;
  * `queue_stats` contando el backoff → falla pgTAP;
  * umbral de latencia a 80 s → falla `promtool test rules`.

### Puertos de IA (#22)

* **Dominio** (`test_ai_domain.py`): costo exacto en `Decimal` y redondeado a 6 decimales como la columna; modelo fijado (se rechaza `-latest`); mensajes y herramientas válidos.
* **Adaptador de Anthropic** (`test_anthropic_chat.py`, con `httpx.MockTransport`): formato del request (clave, versión, modelo, sistema cacheado, herramientas e historial), parseo de texto y de herramientas, clasificación de cada error (429/5xx/529 transitorios con `retry-after`; 4xx permanentes) y ni la clave ni el contenido en los errores.
* **Decoradores** (`test_ai_resilience.py`): reintento con presupuesto y jitter; circuit breaker (abre, deja pasar una prueba, no se abre por errores permanentes); traza sin contenido; costo en `llm_calls` también para las llamadas fallidas, y si la BD falla el turno sigue; la composición cuenta una llamada reintentada como una.
* **Contrato** (`tests/contract/test_ai_ports_contract.py`): la misma suite contra el fake y contra el adaptador real.
* **Contra la BD** (`test_llm_calls_db.py`): costo exacto en el tenant; una conversación de otro negocio se rechaza.
* **Mutaciones comprobadas** (12 de 12 detectadas): reintentar errores permanentes; abrir el circuito con ellos; varias pruebas a la vez en media apertura; grabar la excepción en la traza; perder la respuesta si falla el registro; no registrar las fallidas; mandar un alias como modelo; tratar 529 como permanente; truncar el costo; aceptar un modelo sin precios; aceptar `-latest`; quitar la FK compuesta (pgTAP).

### Pruebas de integración (`backend/tests/integration`, marcador `integration`)

Corren contra el Postgres local de Supabase (`supabase db start`) **como `app_backend`**, igual que el backend real: RLS, FSM y triggers se aplican de verdad. El *fixture* se conecta como administrador local solo para crear datos sintéticos y habilitar el login de `app_backend` con una contraseña aleatoria de esa corrida; al terminar lo deja otra vez sin login.

* `SCZ_TEST_DATABASE_ADMIN_URL`: por defecto, el Postgres local de la CLI (`127.0.0.1:54322`). La prueba se niega a correr contra un host que no sea local.
* Sin BD, las pruebas se **omiten** con un aviso. En la CI, `SCZ_REQUIRE_DB=1` hace que **fallen**, para que una BD caída no dé un falso verde.
* **Filtración de contexto entre transacciones (ADR-0011):** el pool de asyncpg ejecuta `RESET ALL` al devolver una conexión y ocultaría una filtración, pero Supavisor en modo transacción no lo hace. La prueba usa un doble del pooler que entrega la misma conexión física sin *reset*. Se verificó que falla si `SET LOCAL` se cambia por un `SET` de sesión.

Dependabot propone actualizaciones semanales de `backend/uv.lock` y de las GitHub Actions.

## Frontend

Ver la estrategia en `SYSTEM_DESIGN.md` §13.1 (unitarias, propiedades, contrato, integración, seguridad, carga, caos, evals).
