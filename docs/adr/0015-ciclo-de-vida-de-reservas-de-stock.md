# ADR-0015: La vigencia de una reserva de stock la decide el estado de la orden

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Decisores:** AHS
* **Relacionado:** ADR-0004 (FSM en BD), ADR-0008 (confianza de pagos) · SYSTEM_DESIGN.md §5.3 (I-10, I-15, I-16), §6 · migración `20261015000010_ciclo_vida_reservas_stock.sql` · prueba `07_stock_lifecycle.test.sql`

## Contexto
La revisión del 15-oct-2026 encontró dos huecos en `public.stock_reservations` que permitían sobreventa, en contra de la invariante I-10 y de Q-ACC-3 (0 stock reservado por encima del disponible):

* **H1.** `search_catalog` y `create_quote` contaban como activas solo las reservas con `expires_at > now()`, y `expires_at` se fijaba igual a `valid_until`. Pero `expire_stale_orders` no vence órdenes en `PAYMENT_REVIEW`: esperan a que un humano revise el comprobante (ADR-0008). Pasado `valid_until`, una orden en revisión dejaba de bloquear su stock, otro cliente lo reservaba y, al aprobar el pago, `tg_payments_apply` llevaba `stock_on_hand` por debajo de 0. El `CHECK` hacía fallar la aprobación con un error técnico, sin código de negocio.
* **H2.** Solo el job de vencimiento liberaba reservas. Una cotización cancelada seguía bloqueando stock hasta 24 h. Una orden `CONFIRMED` cancelada o reembolsada nunca devolvía el stock comprometido. Además, confirmar sin pago (seña 0) no comprometía stock, porque eso solo ocurría dentro del trigger de `payments`.

## Decisión
* **Una reserva está activa mientras no se libere ni se comprometa** (`released_at IS NULL AND committed_at IS NULL`). Lo que la termina es el estado de la orden, no el reloj. `expires_at` queda como dato informativo.
* **Un solo trigger, `trg_orders_stock_lifecycle` (AFTER UPDATE OF status en `orders`), gobierna el ciclo de vida.** Al pasar a `CONFIRMED` compromete el stock; al pasar a `CANCELLED` o `EXPIRED` libera las reservas activas; de `CONFIRMED` a `CANCELLED` o `REFUNDED` devuelve el stock comprometido y marca `returned_at`.
* **Si al confirmar no hay stock físico, la confirmación falla con `HINT = 'STOCK_CONFLICT'`** (SQLSTATE P0001) y no se registra nada. Pasa, por ejemplo, si alguien bajó `stock_on_hand` a mano mientras la orden estaba en revisión. El dueño decide: reponer, reembolsar fuera de la plataforma o cancelar.
* **Devolver el stock al cancelar o reembolsar una orden `CONFIRMED` no cambia ninguna regla de negocio.** En la FSM, la entrega es `COMPLETED`. Desde `CONFIRMED` la mercadería sigue en el local. Una devolución después de la entrega (`COMPLETED`) no tiene transición y queda fuera del MVP.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| **Vigencia por estado de la orden (elegida)** | Un solo predicado, el mismo del índice parcial `idx_reservations_active`. Es imposible que una orden viva deje de bloquear stock. Ante una falla, gana no sobrevender: si el job de vencimiento se detiene, sobra stock bloqueado pero nunca falta. | Una cotización vencida bloquea su stock hasta que corre el job (cada minuto con pg_cron). Una orden en revisión bloquea su stock hasta que alguien la revise. |
| Extender `expires_at` al pasar a `AWAITING_PAYMENT`/`PAYMENT_REVIEW` | No cambia las consultas de disponibilidad. | Dos fuentes de verdad (estado y reloj) que hay que mantener sincronizadas. Si la evidencia llega después de `valid_until` y antes del job, la reserva "revive" cuando su stock quizá ya se asignó a otro cliente, y hay que volver a validar. Es fácil olvidar una transición. |
| Validar stock solo al confirmar (sin reservas) | Modelo simple. | La cotización no garantiza la mercadería: el cliente paga y luego se entera de que no hay. Contradice I-10. |

## Consecuencias
* (+) I-10 se cumple por construcción: el stock solo deja de estar reservado por una transición de la FSM, y toda transición que lo libera, compromete o devuelve pasa por el mismo trigger.
* (+) Cualquier camino a `CONFIRMED` (aprobación humana, banco, seña 0) compromete stock, y cualquier conflicto llega al backend como `STOCK_CONFLICT`, un código que puede mapear a un mensaje claro.
* (+) La devolución de stock queda auditada (`returned_at`, y `audit_log` sobre `catalog_items`).
* (−) Una orden en `PAYMENT_REVIEW` bloquea stock mientras nadie la revise. Mitigación: la bandeja de pagos (issue de la API de revisión) debe alertar sobre evidencias pendientes por más de N horas. Queda como métrica operativa.
* (−) El trigger es `SECURITY DEFINER`, porque quien cancela desde el dashboard no tiene privilegios sobre `stock_reservations`. Solo actúa sobre la orden que el llamador ya pudo actualizar (RLS y FSM), filtrando por su `organization_id`.

## Verificación
`supabase/tests/database/07_stock_lifecycle.test.sql` (19 casos, fallaba antes del fix): una orden en revisión con `valid_until` vencido sigue bloqueando su stock; cancelar libera; cancelar o reembolsar una orden `CONFIRMED` devuelve el stock; confirmar con seña 0 compromete el stock; aprobar sin stock físico falla con `STOCK_CONFLICT` sin efectos parciales.
