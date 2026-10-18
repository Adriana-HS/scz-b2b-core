# Observabilidad

Logs JSON sin PII, trazas de punta a punta, métricas de los SLO, Sentry y alertas como código (#19, `SYSTEM_DESIGN.md` §10.2). El código vive en `backend/app/core/` (`structured_logging.py`, `redaction.py`, `telemetry.py`, `sentry.py` y `observability.py`). Las alertas están en `ops/alerts/` y los runbooks en `docs/runbooks/`.

## Configuración

| Variable | Qué hace | Sin valor |
| :--- | :--- | :--- |
| `LOG_LEVEL` | Nivel de los logs JSON (stdout) | `INFO` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Colector OTLP/HTTP (Grafana Cloud, un OpenTelemetry Collector, etc.). Se le agregan `/v1/traces` y `/v1/metrics`. Debe ser `https` en staging y producción | Las trazas y métricas existen en el proceso (los logs llevan `trace_id`) pero no se exportan |
| `OTEL_EXPORTER_OTLP_HEADERS` | Autenticación del colector (`Authorization=Basic …`). Es un secreto: va en el gestor de secretos | — |
| `SENTRY_DSN` | Errores y logs `ERROR` a Sentry. Es un secreto | Sentry queda apagado |

Todo se configura al **arrancar** el proceso (lifespan de la api, `main()` del worker), no al importar, para que las pruebas puedan instalar exportadores en memoria antes.

## Logs

Una línea JSON por evento en stdout (12-Factor):

```json
{"ts": "2026-10-17T14:03:11.204+00:00", "level": "ERROR", "logger": "scz.worker",
 "msg": "ALERTA job_dead id=812 kind=SEND_OUTBOUND attempts=8",
 "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7",
 "job_id": "812", "job_kind": "SEND_OUTBOUND", "organization_id": "a0000000-…"}
```

* `trace_id` y `span_id` salen del span activo: un log se busca en su traza y viceversa.
* El worker agrega `job_id`, `job_kind` y `organization_id` con `log_context(...)`. Los campos de contexto no pisan los campos base y también se redactan.
* **Redacción** (`redact`): el mensaje, el traceback y el contexto pasan por patrones de teléfono, correo, `Bearer …`, tokens de Meta (`EAA…`) y `token=`/`key=`/`secret=` en URLs. Los UUID, los `wamid`, los números de cotización y los montos no se tocan. La redacción es la **segunda** línea de defensa: la primera es no loguear datos del cliente (los logs del webhook llevan solo conteos).

## Trazas

```
POST /v1/webhooks/whatsapp      (span de servidor de FastAPI)
 └─ UnitOfWork: SET LOCAL app.traceparent = <traceparent W3C>
     └─ INSERT en private.jobs → trace_context (DEFAULT de la columna, migración 18)
         └─ worker: span "job SEND_OUTBOUND" con ese padre → el mismo trace_id
```

* El span de servidor lo crea FastAPI (instrumentación nativa), con nombre por ruta (`POST /v1/webhooks/whatsapp`; una ruta desconocida queda como `GET`, sin crear nombres nuevos). Se desactivaron sus logs OTel (llevarían mensajes de excepción) y sus spans internos.
* **`RedactingSpanProcessor`** corre primero, al iniciar cada span: `url.query` queda solo con las claves (`hub.verify_token=REDACTED`) y `url.path` pasa por `redact`. Sin él, el `verify_token` de la verificación de Meta llegaría al colector.
* La traza cruza la cola **sin tocar cada INSERT**: la UnitOfWork fija `app.traceparent` en la transacción y la columna `jobs.trace_context` lo toma por defecto. Un valor mal formado se descarta (NULL) y nunca rompe el webhook. Sin traza activa, queda NULL y el worker abre una traza nueva.
* El span del trabajo no registra excepciones (`record_exception=False`): el mensaje de error puede traer datos del cliente. El tipo de error ya queda en `jobs.last_error` y en `jobs_processed{outcome}`.
* FastAPI acepta un `traceparent` entrante. Meta no lo envía; un tercero podría poner uno (el POST sin firma igual se rechaza con 401). El efecto se limita a elegir el `trace_id` de su propio request.

## Métricas

Al exportar a Prometheus, los contadores llevan `_total` y los histogramas `_bucket`/`_sum`/`_count`. La unidad va en el nombre (`_ms`, `_s`, `_usd`). Los atributos salen de conjuntos cerrados (tipo de trabajo, resultado, categoría, código HTTP): nunca un teléfono, un texto ni una ruta libre.

| Métrica | Tipo | Atributos | Fuente | Para |
| :--- | :--- | :--- | :--- | :--- |
| `webhook_ack_ms` | histograma (límite en 250) | `status` | middleware de la api | Q-PERF-1 |
| `webhook_requests` | contador | — | webhook | tasa de firmas |
| `webhook_signature_failures` | contador | — | webhook (401) | Q-SEC |
| `reply_latency_ms` | histograma (límite en 8000) | — | outbox: de `last_inbound_at` a Meta acepta, solo mensajes libres | Q-PERF-2 |
| `wa_billable_messages` | contador | `category` (`service`, `utility`, `marketing`, `unknown`) | outbox | costo de Meta (EXT-04) |
| `outbound_outcome_unknown` | contador | — | outbox (ADR-0017) | revisión humana |
| `jobs_processed` | contador | `kind`, `outcome` (`done`/`retried`/`dead`/`lost`) | worker | — |
| `jobs_dead` | contador | `kind` (`lease_vencido` si lo detecta el reaper) | worker | Q-REL-1 |
| `job_duration_ms` | histograma | `kind` | worker | — |
| `jobs_queued`, `jobs_running`, `jobs_dead_pending` | gauge | `kind` | `private.queue_stats` cada minuto | cola |
| `jobs_oldest_ready_s` | gauge | `kind` | ídem: espera del trabajo **listo** más viejo (sin contar backoff) | cola |
| `fx_stale_tenants` | gauge | — | `private.fx_stale_tenants` cada minuto | Q-ACC |
| `llm_cost_usd` | contador | (lo define el agente, fase 2) | agente | Q-COST-1 |

Los gauges de la cola se miden **solo para los tipos que el worker procesa**: un tipo sin handler todavía (`PROCESS_CONVERSATION`, hasta que exista el agente) espera a propósito y no debe disparar "cola atascada". Un tipo procesado sin filas se informa en cero, así la alerta se apaga sola.

`METRIC_NAMES` en `telemetry.py` es el catálogo: una prueba verifica que coincida con los instrumentos creados y que las alertas solo usen esos nombres.

**Pendientes de §10.2**, con la funcionalidad que los produce: `evidence_latency_ms` y `needs_review_rate` (comprobantes), `handoff_rate` y `grounding_violations` (agente, #24 y #25), y la alerta de costo de IA por tenant (necesita el tope por plan como métrica, #25). El p99 del ack bajo carga se mide con k6 en el workflow *Carga y caos* (`docs/load-testing.md`).

## Sentry

* Se activa solo con `SENTRY_DSN`. Los logs `ERROR` (`ALERTA job_dead`, `ALERTA outbound_outcome_unknown`) llegan como eventos: la alerta de Sentry sobre ellos es inmediata, sin esperar a las métricas.
* `data_collection` cerrado (reemplaza a `send_default_pii`, que sentry-sdk 2.72 marca deprecado): ni datos del usuario, ni cookies, ni encabezados, ni query, ni cuerpos, ni variables locales, ni datos de consultas, ni entradas o salidas de IA.
* `scrub_event` (en `before_send`) quita además `request.data/cookies/headers/query_string/env` y `user`, y redacta mensaje, excepciones, breadcrumbs, `extra` y `contexts`. Una prueba manda una excepción con un teléfono por un transporte de prueba y verifica que el evento enviado no lo tenga.
* `traces_sample_rate=0`: las trazas van por OpenTelemetry, no por Sentry.

## Alertas

`ops/alerts/slo-rules.yaml` (formato Prometheus: Prometheus, Grafana Cloud o Mimir):

| Alerta | Condición | Severidad | Runbook |
| :--- | :--- | :--- | :--- |
| `ReplyLatencyP95High` | p95 de `reply_latency_ms` > 8 s durante 15 min | page | [latencia](runbooks/latencia.md) |
| `WebhookAckP99High` | p99 de `webhook_ack_ms{status="200"}` > 250 ms durante 10 min | ticket | [latencia](runbooks/latencia.md) |
| `JobDead` | algún `jobs_dead` en 5 min | page | [trabajos DEAD](runbooks/trabajos-dead.md) |
| `JobsDeadPending` | DEAD sin resolver durante 24 h | ticket | [trabajos DEAD](runbooks/trabajos-dead.md) |
| `QueueBacklog` | más de 50 en cola durante 5 min | page | [cola atrasada](runbooks/cola-atrasada.md) |
| `QueueWaitHigh` | un trabajo listo espera más de 2 min, durante 5 min | ticket | [cola atrasada](runbooks/cola-atrasada.md) |
| `FxRateStale` | `fx_stale_tenants > 0` durante 10 min | ticket | [TC vencido](runbooks/tc-vencido.md) |
| `WebhookSignatureFailuresHigh` | más del 1 % de firmas inválidas en 15 min, con al menos 20 POST | ticket | [firma del webhook](runbooks/firma-webhook.md) |
| `OutboundOutcomeUnknown` | algún envío incierto en 15 min | ticket | [envío incierto](runbooks/envio-incierto.md) |

`page` = atender ya; `ticket` = en horario hábil.

### Probar las alertas

`ops/alerts/slo-rules.test.yaml` tiene series sintéticas: cada alerta tiene un caso que dispara y uno que no (lo verifica `test_alert_rules.py`). La CI las corre en el job `alerts` con la imagen oficial de Prometheus fijada por digest. En local (Docker):

```bash
docker run --rm -v "$PWD/ops/alerts:/rules:ro" -w /rules --entrypoint promtool prom/prometheus:v3.5.0 test rules slo-rules.test.yaml
```

## Qué nunca sale del proceso

| Dato | Logs | Trazas | Métricas | Sentry |
| :--- | :--- | :--- | :--- | :--- |
| Teléfono o texto del cliente | redactado y no se loguea | no se agrega; `url.path` redactado | nunca (atributos cerrados) | `data_collection` + `scrub_event` |
| Tokens y secretos (`verify_token`, `Bearer`, `EAA…`) | redactados | `url.query` sin valores | — | quitados |
| Mensaje de una excepción | redactado | no se registra | — | redactado |
