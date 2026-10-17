# ADR-0017: Ante un resultado incierto, un mensaje al cliente se envía a lo sumo una vez

* **Estado:** Aceptado
* **Fecha:** 2026-10-17
* **Decisores:** AHS
* **Relacionado:** issue #18, #17 · ADR-0005, ADR-0006 · SYSTEM_DESIGN.md §10.1 · migración 16 · `backend/app/application/outbound.py`

## Contexto
La cola es *at-least-once* (ADR-0005): un trabajo puede ejecutarse más de una vez si el worker muere o vence su *lease*. Para la BD eso es inofensivo, porque cada handler trabaja dentro de una transacción y lo que no se confirmó se revierte. Enviar un mensaje por WhatsApp es distinto: es un efecto **externo** y la Cloud API **no acepta una clave de idempotencia**. Si el worker muere después de que Meta aceptó el mensaje y antes de registrar el `wa_message_id`, no hay forma de saber si salió.

Las dos opciones son reenviar (el cliente puede recibir dos veces la misma respuesta o el mismo recordatorio) o no reenviar (el mensaje puede no haber salido). El criterio de aceptación del #17 pide "sin respuesta duplicada".

## Decisión
El envío ocurre en tres pasos, con la llamada a Meta fuera de toda transacción:
1. Se bloquea el mensaje, se vuelve a verificar la ventana de 24 h y el consentimiento, y se marca **`SENDING`** con **COMMIT** antes de tocar la red.
2. Se llama a Meta.
3. Se marca `SENT` con el `wa_message_id`. Ante un error conocido de Meta: si es transitorio (red, 429, 5xx), vuelve a `PENDING` y se reintenta; si es permanente, queda en `FAILED` sin reintento.

Si un reintento encuentra el mensaje en `SENDING`, el resultado anterior es **incierto**: no se reenvía, queda en `FAILED` con `OUTCOME_UNKNOWN` y se registra el log `ALERTA outbound_outcome_unknown` para que lo revise una persona. Se elige "a lo sumo una vez" solo para ese caso; un error que Meta informó se sigue reintentando.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Reenviar siempre (*at-least-once*) | Ningún mensaje se pierde en silencio | Respuestas, cotizaciones o recordatorios duplicados al cliente; cada mensaje cuesta (EXT-04) |
| Deduplicar con los webhooks de estado | Sabría si salió | El `wa_message_id` llega en la respuesta que se perdió; los estados no traen nuestra clave |
| Una sola transacción alrededor de la llamada a Meta | Simple | Mantiene una conexión y bloqueos durante la red; tampoco resuelve el caso del worker muerto |

## Consecuencias
* (+) El cliente no recibe la misma respuesta dos veces aunque el worker muera a mitad del envío.
* (+) El caso incierto queda visible (`FAILED` + `OUTCOME_UNKNOWN` + alerta) en vez de perderse.
* (−) En ese caso raro, un mensaje que sí salió queda como `FAILED`, y uno que no salió no se reintenta solo. El equipo lo resuelve desde la bandeja de conversaciones (#41).
* (−) Si el worker muere después del envío y antes del paso 3, el estado `sent` que Meta mande después se ignora, porque no se conoce el `wa_message_id`.

## Verificación
* `tests/unit/test_send_outbound.py`: el orden de commits (SENDING antes de Meta, SENT después) y que un mensaje en `SENDING` no se reenvía. Se verificó con una mutación: si se reenvía, la prueba falla.
* `tests/integration/test_outbox_db.py`: el envío completo contra la BD; un reintento después del éxito no reenvía.
