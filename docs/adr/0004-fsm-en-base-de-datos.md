# ADR-0004: Máquina de estados aplicada por el motor, con actores

* **Estado:** Aceptado (refuerza ADR-004 de la v1)
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §6 · migraciones 0003 (`fsm_transitions`), 0004 (`tg_orders_guard`, `tg_payments_apply`) · pruebas `02_*`

## Contexto
La v1 decidió correctamente que el LLM no decide estados. Pero la FSM vivía solo en Python, los nombres de estado del diagrama (`PRE_VERIFIED`, `FLAGGED_MANUAL`, `VERIFIED`) no existían en el enum del DDL y el estado `REJECTED_DUPLICATE` era inalcanzable (el UNIQUE impedía registrar el intento). Faltaban estados reales del negocio: `HELD` (retención de horario), `EXPIRED`, `NO_SHOW`, `REFUNDED`.

## Decisión
* Un enum `order_status` único y una tabla `private.fsm_transitions(kind, from, to, allowed_actors)` como **fuente única de verdad**.
* El trigger `tg_orders_guard` rechaza toda transición no listada para el actor (`BOT`, `SYSTEM`, `HUMAN`). También congela los montos después de `DRAFT`, impide editar `amount_paid_bs` fuera del libro de pagos y exige `amount_paid_bs ≥ deposit_required_bs` para entrar en `CONFIRMED`.
* El paso a `CONFIRMED` lo hace el trigger del libro `payments` (append-only), nunca el agente.
* El backend Python carga `fsm_transitions` y una prueba de paridad verifica que coinciden.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| FSM solo en Python (v1) | Más fácil de leer | Un bug o un camino nuevo puede saltársela |
| Biblioteca de *workflow* externa | Funcionalidad rica | Otra pieza móvil; no protege la BD |

## Consecuencias
* (+) "El bot confirmó una cita sin pago" es imposible por construcción.
* (−) Agregar un estado exige una migración (es intencional: es un cambio de reglas de negocio).

## Verificación
pgTAP `02_*` #11–#14 y #21; prueba de paridad Python↔BD; pruebas de propiedades (Hypothesis) que generan secuencias aleatorias de transiciones.
