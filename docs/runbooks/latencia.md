# Runbook: latencia de respuesta o del ack del webhook

**Alertas:**
* `ReplyLatencyP95High` (page): p95 de `reply_latency_ms` > 8 s durante 15 min (Q-PERF-2).
* `WebhookAckP99High` (ticket): p99 de `webhook_ack_ms` (respuestas 200) > 250 ms durante 10 min (Q-PERF-1).

## Síntoma

* **Respuesta lenta:** los clientes esperan más de 8 s entre su último mensaje y la respuesta del bot. Se mide desde `customers.last_inbound_at` hasta que Meta acepta el envío.
* **Ack lento:** el webhook tarda en responder 200 a Meta. Pasados unos segundos, Meta lo da por fallido y lo reintenta, lo que duplica la carga (la deduplicación por `wa_message_id` evita mensajes dobles, no el trabajo extra).

## Diagnóstico

1. **¿Es la cola?** Mira `jobs_queued` y `jobs_oldest_ready_s` por tipo. Si crecen, sigue el runbook [cola atrasada](cola-atrasada.md).
2. **¿Es un paso del trabajo?** En las trazas, ordena los spans `job SEND_OUTBOUND` (y, con el agente, los del LLM) por duración. `job_duration_ms` por `kind` muestra cuál se volvió lento.
3. **¿Es Meta?** Si el span del envío se alarga y aparecen `TransientSendError:HTTP_5xx` o `NETWORK` en `last_error`, revisa el [estado de Meta](https://metastatus.com/).
4. **¿Es la BD?** (para el ack) El webhook solo escribe en una transacción. Revisa en Supabase → Reports → Database el uso de CPU, las conexiones y las consultas lentas; un `webhook_ack_ms` alto con 200 casi siempre es la BD o el pool.

## Acción

* Cola: escala el worker (más réplicas o más `WORKER_CONCURRENCY`, cuidando las conexiones del pool: cada réplica usa `WORKER_CONCURRENCY + 2`).
* BD: si son conexiones, revisa el tamaño de los pools (`api` y `worker`) frente al límite del plan de Supabase; si es una consulta, abre un issue con su plan (`EXPLAIN`), sin datos de clientes.
* Meta caída: no hay nada que hacer del lado nuestro; los reintentos con backoff ya están en marcha. Avisa si dura más de 30 min.

## Verificación

El p95 (o el p99) vuelve bajo el umbral y la alerta se resuelve sola.

## Comunicación

Si la respuesta lenta dura más de 30 min, avisa a los negocios activos que el bot está respondiendo con demora.

## Postmortem

Obligatorio si `ReplyLatencyP95High` estuvo activa más de 1 h.
