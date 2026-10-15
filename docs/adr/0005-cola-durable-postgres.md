# ADR-0005: Cola durable en PostgreSQL y outbox, en lugar de BackgroundTasks

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §4.4, §10.1 · migraciones 0003 (`jobs`, `outbound_messages`) y 0006 (`enqueue_conversation`, `claim_jobs`, `finish_job`, `requeue_stale_jobs`) · pruebas `03_*` #16–#18

## Contexto
La v1 responde `200 OK` y procesa en `BackgroundTasks` de FastAPI:
* Si el proceso se reinicia, el trabajo en memoria se pierde, y Meta no reintenta porque ya recibió el 200.
* En Cloud Run con facturación por petición, la CPU se limita después de enviar la respuesta, así que las tareas en segundo plano se congelan.
* La idempotencia insertaba `whatsapp_message_id` **antes** de procesar: si el procesamiento fallaba, el mensaje quedaba marcado como procesado y no se reintentaba nunca.
* Los mensajes en ráfaga de un mismo cliente se procesaban en paralelo y las respuestas llegaban desordenadas o duplicadas.

## Decisión
* El webhook persiste, en **una transacción**, el evento (`private.inbound_events`, PK = `wa_message_id`), el mensaje y un trabajo en `private.jobs`, y recién entonces responde 200.
* Los workers reclaman trabajos con `FOR UPDATE SKIP LOCKED`, sin tomar una conversación que ya tiene un trabajo `RUNNING`, y con *lease* de 5 min, reintentos con backoff exponencial y DLQ (`DEAD`).
* **Debounce:** un único trabajo `QUEUED` por conversación (índice único parcial); cada mensaje nuevo pospone su ejecución 1,5 s, con un máximo de 4 s.
* Envíos por **outbox** (`private.outbound_messages`, `idempotency_key UNIQUE`).
* La cola puede migrarse a `pgmq` (Supabase Queues) sin cambiar los casos de uso, porque está detrás de un puerto `JobQueue`.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Redis + Celery/ARQ | Maduro | Otra infraestructura que pagar y operar; no es transaccional con la BD |
| Cloud Tasks / SQS | Gestionado | Acoplamiento a la nube; el *enqueue* no es atómico con el INSERT del mensaje |
| pgmq (Supabase Queues) | Gestionado dentro de Postgres | Válido; se deja como evolución detrás del puerto |

## Consecuencias
* (+) Cero mensajes aceptados y perdidos (Q-REL-1), orden por conversación y reintentos.
* (+) Sin infraestructura extra: el encolado es atómico con los datos.
* (−) La cola compite con la BD transaccional. A la escala prevista es despreciable; se purga a los 14 días.

## Verificación
pgTAP `03_*` #16–#18; prueba de caos nocturna (matar el worker a mitad de un trabajo); alerta ante cualquier trabajo `DEAD`.
