# ADR-0007: Dinero calculado en un solo lugar y tipo de cambio con historial

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §5.4 · migraciones 0002 (`exchange_rates`, `orders`, `order_items`), 0004 (`unit_price_bs`, `current_fx`), 0006 (`create_quote`) · pruebas `02_*` #1–#10, `03_*` #14–#15

## Contexto
* La v1 calculaba precios en `pricing_engine.py` **y** en SQL: dos implementaciones del redondeo que pueden divergir.
* `organizations.exchange_rate_bs` tenía un valor por defecto de 6,96 y sin historial. El 15-oct-2026 el BCB publicó un TCO de 11,68: un tenant nuevo con el valor por defecto vendería con un tipo de cambio casi un 40 % menor.
* Las cotizaciones no tenían vigencia ni reserva de stock, y `subtotal_bs` no se verificaba (se insertó 999 para 2 × 100 sin error).
* Un ítem podía tener `base_price_usd` y `fixed_price_bs` ambos NULL (precio NULL).

## Decisión
* Precio de lista con moneda explícita (`price_currency` + `list_price NOT NULL`).
* **Una función** `private.unit_price_bs()` (2 decimales, mitad lejos de cero), usada por la búsqueda y por la cotización. Python solo formatea, con `Decimal`.
* `line_total_bs` como columna generada; `total_bs` y `deposit_required_bs` mantenidos por trigger.
* `exchange_rates` append-only con origen y autor. Sin valor por defecto. Control de antigüedad (`fx_max_age_hours`): sin TC fresco no se cotizan ítems en USD.
* Cotización atómica (`create_quote`): bloqueo ordenado de filas, reserva de stock con vencimiento, TC y precio congelados en la orden, `valid_until`.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Cálculo en Python (`Decimal`) y la BD solo guarda | Más testeable en Python | No es atómico con la reserva de stock; dos fuentes si la búsqueda también calcula |
| TC global de la plataforma | Simple | Cada negocio cotiza con su propio TC de reposición |

## Consecuencias
* (+) Un único punto de verdad monetario, comprobable con pruebas de propiedades.
* (+) Protección explícita frente a la volatilidad cambiaria (el problema 1 del §1.1).
* (−) Las reglas de precio complejas (listas por cliente, descuentos por volumen) deberán implementarse también en SQL o en una tabla de reglas; se diseñarán con su propio ADR.

## Verificación
pgTAP `02_*` (totales exactos, inyección de precio ignorada, montos congelados) y `03_*` #15 (TC vencido); prueba de propiedades de redondeo con Hypothesis comparando la función SQL y el formateador Python.
