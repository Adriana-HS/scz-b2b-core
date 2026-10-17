# Runbook: trabajos en DLQ (`DEAD`)

**Alertas:** `JobDead` (page, al instante) y `JobsDeadPending` (ticket, DEAD sin resolver por más de 24 h). También llega un evento a Sentry por el log `ALERTA job_dead` del worker.
**SLO:** Q-REL-1, ningún mensaje aceptado queda sin procesar.

## Síntoma

Un trabajo agotó sus intentos (`max_attempts`) o falló con un error permanente, y quedó en `status = 'DEAD'`. Mientras siga ahí, lo que ese trabajo debía hacer no pasó: un mensaje que no salió, una conversación que el bot no atendió, un ítem sin embedding.

## Diagnóstico

1. En la traza, busca el `trace_id` que trae el log `ALERTA job_dead` (campo `trace_id` del JSON). Ahí ves el recorrido completo: webhook → trabajo → envío.
2. En la BD, como administrador (SQL editor de Supabase):
   ```sql
   SELECT id, kind, organization_id, attempts, max_attempts, last_error, updated_at, trace_context
     FROM private.jobs WHERE status = 'DEAD' ORDER BY updated_at DESC LIMIT 50;
   ```
   `last_error` guarda solo el tipo de error y su código (por ejemplo `TransientSendError:HTTP_503`), nunca el mensaje del cliente.
3. Clasifica la causa:
   * `PermanentJobError`: el payload no sirve (falta `outbound_id`, por ejemplo). Es un bug: abre un issue.
   * `TransientSendError:*` o `TimeoutError` repetidos: Meta o la red fallaron durante todos los reintentos. Revisa el [estado de la plataforma de Meta](https://metastatus.com/).
   * `LookupError`: el canal no tiene token o número configurado (`whatsapp_channels.access_token_ref` o el secreto en el entorno del worker).
   * `last_error = 'lease expired'` (en la métrica, `jobs_dead_total{kind="lease_vencido"}`): el worker murió en cada intento de ese trabajo. Busca un handler que se cuelga o que agota la memoria.

## Acción

* Si la causa ya se corrigió (token cargado, Meta de vuelta), reencola **solo** esos trabajos:
  ```sql
  UPDATE private.jobs SET status = 'QUEUED', attempts = 0, run_after = now(), last_error = NULL
   WHERE status = 'DEAD' AND id = ANY ('{…}'::bigint[]);
  ```
  Es seguro: los handlers son idempotentes (ADR-0005). Un `SEND_OUTBOUND` cuyo mensaje quedó `SENDING` no se reenvía (ADR-0017).
* Si es un bug, déjalo en DEAD, abre un issue con el `id` y el `trace_id` (sin datos del cliente) y corrige primero.
* Si un mensaje al cliente no salió y ya cerró la ventana de 24 h, avísale al negocio desde el dashboard para que retome la conversación con una plantilla.

## Verificación

`jobs_dead_pending` vuelve a 0 para ese tipo en el siguiente minuto (lo publica el mantenimiento del worker), y el trabajo reencolado queda `DONE`.

## Comunicación

Si hubo mensajes de clientes sin responder, avisa al negocio afectado con cuántas conversaciones fueron y desde cuándo. Nunca compartas el contenido.

## Postmortem

Obligatorio si hubo más de 10 trabajos DEAD o si afectó a más de un negocio: línea de tiempo, causa raíz, qué detectó el problema, qué faltó y acciones (cada una con su issue). Sin datos de clientes.
