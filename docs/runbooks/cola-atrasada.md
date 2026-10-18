# Runbook: cola atrasada

**Alertas:**
* `QueueBacklog` (page): más de 50 trabajos en cola durante 5 min.
* `QueueWaitHigh` (ticket): un trabajo **listo** de un tipo espera más de 2 min.

Ambas se calculan solo con los tipos que el worker procesa (los que tienen handler). Un tipo sin handler, como `PROCESS_CONVERSATION` en un worker sin la configuración de IA, espera a propósito y no alerta. Un trabajo en backoff (`run_after` en el futuro) no cuenta como espera.

## Síntoma

Los mensajes salen tarde o no salen, porque los trabajos se acumulan más rápido de lo que el worker los procesa.

## Diagnóstico

```sql
SELECT kind, status, count(*), min(run_after) AS listo_desde
  FROM private.jobs WHERE status IN ('QUEUED', 'RUNNING')
 GROUP BY kind, status ORDER BY kind, status;
```

* **¿Hay worker?** Si no hay trabajos `RUNNING` y nadie reclama, el worker está caído o sin conexión. Revisa sus logs: busca `worker iniciado` y errores de conexión (`ConnectionError`).
* **¿Está saturado?** Si hay exactamente `WORKER_CONCURRENCY` trabajos `RUNNING` por réplica, todo va al tope.
* **¿Un trabajo bloquea?** Un trabajo de la misma conversación espera al anterior (`conversation_key`). Si uno se cuelga hasta el `JOB_TIMEOUT_S`, frena los de esa conversación.
* **¿Hay un pico?** Mira `webhook_requests_total`: una campaña del negocio o un reintento masivo de Meta genera picos legítimos.

## Acción

* Worker caído: reinícialo. Al arrancar, el mantenimiento libera los trabajos con el lease vencido.
* Saturado: agrega réplicas del worker (respetando las conexiones del plan de Supabase) o sube `WORKER_CONCURRENCY`.
* Un trabajo colgado: el timeout lo corta y lo reintenta; si se repite, terminará en DEAD (ver [trabajos DEAD](trabajos-dead.md)).

## Verificación

`jobs_queued` baja y `jobs_oldest_ready_s` vuelve a segundos.

## Comunicación

Si la demora supera los 15 min, avisa a los negocios activos.

## Postmortem

Obligatorio si `QueueBacklog` estuvo activa más de 30 min.
