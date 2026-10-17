# Runbook: envío con resultado incierto

**Alerta:** `OutboundOutcomeUnknown` (ticket), además del log `ALERTA outbound_outcome_unknown id=…` del worker, que llega a Sentry.
**Decisión:** ADR-0017. La Cloud API no tiene clave de idempotencia. Si el worker murió durante la llamada a Meta, no sabemos si el mensaje salió. Por eso no se reenvía: preferimos un mensaje para revisar a un mensaje duplicado al cliente.

## Síntoma

Un mensaje del outbox quedó `FAILED` con `last_error = 'OUTCOME_UNKNOWN'`: estaba en `SENDING` cuando el reintento lo encontró.

## Diagnóstico

```sql
SELECT id, organization_id, conversation_id, kind, attempts, created_at
  FROM private.outbound_messages
 WHERE status = 'FAILED' AND last_error = 'OUTCOME_UNKNOWN'
 ORDER BY created_at DESC LIMIT 50;
```

* No hay forma automática de saber si salió: Meta no llegó a devolver el `wa_message_id`, así que sus webhooks de estado no se pueden asociar a este mensaje. Solo el cliente lo sabe.
* En la traza del trabajo (`trace_context` del job `SEND_OUTBOUND`), el span del envío muestra hasta dónde llegó y cuánto duró.
* Muchos casos juntos indican reinicios del worker durante los envíos (despliegue sin apagado ordenado, falta de memoria).

## Acción

* El negocio decide si reenvía: infórmale qué conversación quedó con un mensaje sin confirmar (sin copiar el contenido por canales externos). **No reencoles** estos mensajes desde la BD: podrías duplicar una cotización o un aviso de pago.
* Si son muchos, revisa que el worker se apague ordenado (SIGTERM → termina lo que tiene en curso) y que tenga memoria suficiente.

## Verificación

No aparecen casos nuevos después de corregir la causa de los reinicios.

## Comunicación

Con el negocio afectado, por su canal de soporte.

## Postmortem

Obligatorio si hubo más de 5 casos en un día.
