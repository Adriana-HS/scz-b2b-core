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
| `database` | Migraciones y seeds sobre la imagen oficial de Supabase y las 313 pruebas pgTAP | Sí |
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
| `18_agent_tools.test.sql` | #24: la ficha de un ítem da el mismo precio, stock y estado que la búsqueda; descuenta reservas; con el TC vencido no hay precio en USD; solo atributos públicos; nada de otro negocio ni de ítems dados de baja; el paso a una persona guarda motivo y resumen, con motivo de una lista cerrada y resumen acotado; solo el backend | 16 |
| `19_search_synonyms.test.sql` | #23: sinónimos por rubro de solo lectura y normalizados; expansión en singular o plural; un rubro no usa los de otro; sin operadores inyectados; búsquedas con los seeds; sin trabajos `EMBED_ITEM` (ADR-0020) | 16 |
| **Total** | | **313** |

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

### Herramientas del agente (#24)

* **Frontera con el LLM** (`test_agent_tools.py`): los esquemas no aceptan negocio, cliente, conversación ni precios; los argumentos inválidos (vacíos, fuera de rango, campos de más como un precio o un `customer_id` inyectados) no abren una transacción; el error de validación no repite lo que mandó el modelo.
* **Contexto del servidor:** la cotización usa el cliente y la conversación del turno; todo corre como `BOT`.
* **Respuestas:** montos como texto exacto ("332.88"); aviso de "precio en actualización" si el TC venció; cada error de la BD con su código y un mensaje para el cliente; un error inesperado no filtra detalles.
* **Contra la BD** (`test_agent_tools_db.py`): precio calculado por la BD, ficha igual a la búsqueda, ítems de otro negocio inexistentes, la cotización reserva stock para el cliente del contexto, el paso a una persona deja la conversación en `HUMAN`.
* **Mutaciones comprobadas** (9 de 9 detectadas): sin `extra="forbid"`; actor `SYSTEM` en vez de `BOT`; repetir el valor inválido; devolver el mensaje de un error inesperado; montos como número; otra conversación en la cotización; perder el aviso de precio; atributos internos visibles (pgTAP); stock sin descontar reservas (pgTAP).

### Adaptador de Gemini (#83, ADR-0019)

* **Formato** (`test_gemini_chat.py`): URL de generateContent con el modelo fijado; clave en `x-goog-api-key` y nunca en la URL; sistema, historial (`user`/`model`), herramientas con JSON Schema y `$ref` expandidos; resultados como objeto; un resultado sin su pedido falla.
* **Firmas:** la firma de cada pedido de herramienta vuelve tal cual en la ronda siguiente; los ids que inventa el adaptador no se mandan.
* **Respuesta:** sin los pensamientos; STOP con herramientas = TOOL_USE; motivos de corte; prompt bloqueado = rechazo; tokens cacheados y de razonamiento.
* **Errores:** 429 (con `retry-after` o `RetryInfo`), 5xx, red y timeout transitorios; 4xx permanentes; la clave y el contenido nunca en un error.
* **Configuración** (`test_config.py`): `gemini` solo en `local` y `test`; un proveedor desconocido se rechaza. El worker elige el adaptador (`test_worker.py`).
* **Mutaciones comprobadas** (14 de 14 detectadas): no devolver la firma; perderla al leer; la clave en la URL; STOP con herramientas como END_TURN; los pensamientos en el texto; mandar los ids inventados; no expandir los `$ref`; el 429 permanente; ignorar `RetryInfo`; no cobrar el razonamiento; cobrar el cacheado como entrada; adivinar el nombre de un resultado huérfano; aceptar Gemini en producción; el worker siempre con Anthropic.

### Orquestador del agente (#81)

* **Turno** (`test_agent_turn.py`, con modelo guionado y herramientas falsas): responde una vez y marca los mensajes; junta los mensajes seguidos del mismo lado y empieza por el cliente; el prompt del rubro y sus herramientas; el contexto del turno lo pone el servidor.
* **Cuándo no responde:** nada pendiente; `HUMAN`, `CLOSED` o pausa vigente (marca los mensajes); solo BAJA/ALTA pendientes; ventana de 24 h cerrada.
* **Límites:** más de 4 herramientas, 3 rondas (buscar, cotizar y responder sí entran, #89), sin tiempo, respuesta vacía o de más de 4096 caracteres → plantilla. Una respuesta con un bloque de código o que copia una línea del prompt (sin importar mayúsculas) → "solo atiendo lo del negocio"; nombrar al negocio sí sale.
* **Trato de usted** (`test_customer_texts.py`): ningún texto fijo que lee el cliente tutea (plantillas, errores de las herramientas, confirmaciones de BAJA/ALTA); todo prompt pide trato de usted. 3 mutaciones detectadas.
* **Prompts por rubro** (`test_verticals.py`): todo rubro termina con las reglas de alcance y confidencialidad, con el nombre del negocio y sin marcadores sin llenar; solo herramientas que existen y siempre `handoff_to_human`. IA no disponible → plantilla y paso a una persona. Rubro sin política o trabajo mal formado → error permanente.
* **Contra la BD** (`test_agent_turn_db.py`): webhook → turno → una respuesta en el outbox y el historial, el precio desde la herramienta y el costo en `llm_calls`; repetir el trabajo no duplica; con una persona a cargo no se llama al modelo.
* **Mutaciones comprobadas** (9 de 9 detectadas): clave de idempotencia sin el último `wa_message_id`; responder con una persona a cargo; sin tope de herramientas; sin presupuesto de tiempo; no marcar los mensajes con una persona a cargo; no pasar a una persona si la IA cae; guardar en el historial lo que el outbox rechazó; una BAJA despierta al agente; mandar una respuesta vacía. Y 7 de 7 de la barrera de alcance: dejar pasar código; dejar pasar fragmentos del prompt; comparar con mayúsculas; bloquear líneas cortas como el nombre del negocio; un rubro sin las reglas; las reglas al principio del prompt; quitar "no escribes código".

### Evals golden del agente (#28)

* **Suite** (`test_agent_evals.py`): ≥ 30 casos por rubro con todas las categorías; los códigos esperados existen en los seeds; solo se esperan herramientas que el rubro tiene; un caso mal escrito falla al cargar.
* **Puntaje:** pasa solo si cumple todo: las herramientas en orden (con otras entre medio permitidas), los argumentos (palabras de la consulta, códigos y cantidades, motivo del paso a una persona), ninguna herramienta prohibida, el resultado y la respuesta, sin importar mayúsculas ni acentos.
* **Reporte:** Q-ACC-5 y Q-ACC-6, por categoría, y qué casos empezaron a fallar o a pasar contra la corrida anterior.
* **Runner contra la BD** (`test_agent_evals_db.py`, modelo guionado): una cotización por código pasa con las herramientas reales; el historial se carga y solo el último mensaje queda pendiente; un turno que no cumple falla con su motivo.
* **Con IA, a mano** (`test_agent_evals_live.py`, `SCZ_RUN_EVALS=1`): ver `evals/conversations/README.md`.
* **Mutaciones comprobadas** (7 de 7): ignorar el orden de las herramientas; no revisar la consulta, los ítems ni las prohibidas; comparar con acentos; no contar las cifras inventadas; no detectar los casos que empezaron a fallar.

### Cotizar por código (#89)

* **Herramienta** (`test_agent_tools.py`): el código se resuelve en el negocio (sin importar mayúsculas ni guiones); con códigos parecidos gana el exacto; uno inexistente o ambiguo da `ITEM_NOT_FOUND` sin llegar a cotizar; se pueden mezclar código e `item_id`; cada línea lleva uno de los dos.
* **Turno** (`test_agent_turn.py`): buscar, cotizar y responder entran en un turno; una cuarta ronda no.
* **Contra la BD** (`test_agent_tools_db.py`): cotizar por código usa el ítem del negocio; el código de otro negocio no existe.
* **Mutaciones comprobadas** (5 de 5): no preferir el exacto; aceptar un código ambiguo; volver a 2 rondas; aceptar `sku` e `item_id` juntos; aceptar ninguno.

### Sinónimos de búsqueda (#23, ADR-0020)

* **pgTAP** (`19_search_synonyms`): solo lectura para el backend y el panel; términos normalizados; plural y mayúsculas; un rubro no usa los sinónimos de otro; solo palabras vacías → sin consulta; los operadores de tsquery no se interpretan; búsquedas reales con los seeds; un ítem nuevo no encola `EMBED_ITEM`.
* **Eval** (`test_search_eval_db.py`, `evals/search/*.yaml`): MRR@5 con y sin sinónimos sobre el catálogo de los seeds. Hoy: importadora 0,86 → 1,00; consultorio 0,71 → 0,92 (falla "quisiera una limpieza para mi hijo"). Correr con `-s` para ver el resultado. Necesita los seeds (`supabase db reset`).
* **Mutaciones comprobadas** (4 de 4, aplicadas en la BD local): no expandir; usar sinónimos de cualquier rubro; comparar el término sin el stemmer (falla el plural); volver a encolar `EMBED_ITEM`.
* **Ojo con la base local:** las pruebas de integración dejan trabajos en cola; antes de `supabase test db` conviene `supabase db reset` (la prueba 03 cuenta trabajos en cola).

### PDF de cotización (#26)

* **Documento** (`test_quote_pdf.py`): emisor y NIT, referencia, fechas en la hora del negocio, montos en formato boliviano tal como los calculó la BD, TC solo si hay ítems en USD, IVA según el régimen, seña solo si se pide, "no es una factura", 50 líneas en varias páginas, texto del catálogo escapado, nombre de archivo seguro y mismos bytes para el mismo documento.
* **Snapshot:** `tests/snapshots/cotizacion_iva_incluido.pdf`, comparado byte a byte. Si el cambio de diseño es intencional: `SCZ_UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_quote_pdf.py` y revisar el PDF a mano.
* **Envío** (`test_send_quote_document.py`): genera en un thread pool; red fuera de las transacciones; sube con el token del canal y encola un `DOCUMENT` por `media_id` con clave por orden; sin cotización o conversación no hace nada; sin canal o token, reintenta; un rechazo de Meta va a DEAD; con la ventana cerrada no reintenta.
* **Subida** (`test_cloud_api.py`): multipart a `/media` con el token; errores clasificados como los envíos; sin filtrar el token.
* **Contra la BD** (`test_quote_document_db.py`): cliente escribe → `create_quote` → trabajo con `conversation_key` → PDF real con el total de la BD → `DOCUMENT` en el outbox; otro negocio no lee la cotización.
* **Mutaciones comprobadas** (16 de 17 detectadas): generar en el event loop; clave sin la orden; reintentar un rechazo de Meta; reintentar con la ventana cerrada; subir sin token; montos en formato inglés; fechas en UTC; no escapar el catálogo; sin el TC; siempre "IVA incluido"; seña en cero; PDF no determinista; subir a otra URL; no validar el `phone_number_id`; `create_quote` sin pedir el PDF; trabajo sin `conversation_key` (BD). **Sobrevive** quitar `organization_id = private.backend_org_id()` de la consulta: RLS sigue impidiendo leer la cotización de otro negocio (`test_una_cotizacion_de_otro_negocio_no_se_lee`), así que ese filtro es una segunda capa.

### Aviso de IA (#29)

* **Turno** (`test_agent_turn.py`): la primera respuesta lleva el aviso en el mismo mensaje y se registra en la misma transacción; con el aviso ya dado no se repite; también va con una plantilla; un trabajo repetido no lo registra otra vez; sin respuesta no hay aviso; el texto aprobado por el negocio reemplaza al de por defecto; si con el aviso no entra en 4096 caracteres, va la plantilla.
* **Texto** (`test_verticals.py`, `test_customer_texts.py`): dice que es un asistente automático con IA, cómo pedir una persona y para qué se usan los mensajes; un texto propio inválido usa el de por defecto; de usted.
* **Contra la BD** (`test_agent_turn_db.py`): el turno lee `privacy_notice_at` y `config.ai_notice`; la primera respuesta llega con el aviso y deja la fecha; la fecha del primer aviso no se pisa.
* **Mutaciones comprobadas** (12 de 12 detectadas): nunca dar el aviso; darlo siempre; no registrarlo; registrarlo aunque el outbox rechace la respuesta; pasarse del largo de WhatsApp; ignorar el texto del negocio; aceptar un texto propio vacío o enorme; quitar cómo pedir una persona; tutear; pisar la fecha (BD); no leer el texto del negocio (BD).

### Guardrails y cuotas (#25)

* **Reglas puras** (`test_guardrails.py`): importes en formato boliviano e inglés, con moneda o decimales, y lecturas ambiguas; cantidades, códigos, fechas y porcentajes no son importes; valores de las herramientas desde su JSON; cifras inventadas, conversiones propias e inyecciones ("el precio es 1 Bs") detectadas; pedir CI, NIT, tarjeta o cuenta se detecta y mencionarlos no.
* **Turno** (`test_agent_turn.py`): una cifra sin herramienta o un pedido de dato sensible no sale; con la cuota del día agotada no se llama a la IA y se pasa a una persona; aviso al 80 % (y no al 79 %); el límite por cliente responde un aviso sin IA, una sola vez, y justo en el límite responde normal.
* **Contra la BD** (`test_agent_turn_db.py`): el costo del día suma solo hoy (día de Bolivia) y solo el negocio; con la cuota agotada el cliente recibe la plantilla y la conversación queda en `HUMAN`. Los costos de prueba se borran al terminar, para no agotar la cuota de las demás pruebas.
* **Alertas:** `LlmBudgetNearLimit` y `LlmBudgetExceeded` con su caso de disparo en `slo-rules.test.yaml`.
* **Mutaciones comprobadas** (17 de 17 detectadas): sin grounding; sin juntar lo que devolvieron las herramientas; sin el control de datos sensibles; bloquear con solo mencionar un dato sensible; sin la lectura ambigua de miles; no reconocer "bolivianos"; tratar un porcentaje o una fecha como importe; cortar la cuota recién por encima del tope; avisar al 90 %; no pasar a una persona con la cuota agotada; cortar justo en el límite de mensajes; dejar pasar el turno 30; repetir el aviso de límite; el worker sin los topes; otro tope por defecto; sumar el costo de todos los días (contra la BD).

### Pruebas de integración (`backend/tests/integration`, marcador `integration`)

Corren contra el Postgres local de Supabase (`supabase db start`) **como `app_backend`**, igual que el backend real: RLS, FSM y triggers se aplican de verdad. El *fixture* se conecta como administrador local solo para crear datos sintéticos y habilitar el login de `app_backend` con una contraseña aleatoria de esa corrida; al terminar lo deja otra vez sin login.

* `SCZ_TEST_DATABASE_ADMIN_URL`: por defecto, el Postgres local de la CLI (`127.0.0.1:54322`). La prueba se niega a correr contra un host que no sea local.
* Sin BD, las pruebas se **omiten** con un aviso. En la CI, `SCZ_REQUIRE_DB=1` hace que **fallen**, para que una BD caída no dé un falso verde.
* **Filtración de contexto entre transacciones (ADR-0011):** el pool de asyncpg ejecuta `RESET ALL` al devolver una conexión y ocultaría una filtración, pero Supavisor en modo transacción no lo hace. La prueba usa un doble del pooler que entrega la misma conexión física sin *reset*. Se verificó que falla si `SET LOCAL` se cambia por un `SET` de sesión.

Dependabot propone actualizaciones semanales de `backend/uv.lock` y de las GitHub Actions.

## Frontend

Ver la estrategia en `SYSTEM_DESIGN.md` §13.1 (unitarias, propiedades, contrato, integración, seguridad, carga, caos, evals).
