# ADR-0012: IVA modelado por régimen vigente y congelado en cada orden

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §5.4 · migraciones 0001 (`tax_mode`), 0002 (`tax_regimes`, `orders.subtotal_bs/iva_bs/tax_regime_id`), 0004 (`tax_regime_at`, `tax_breakdown`) · pruebas `04_tax_regimes` · `docs/research/2026-10-15-restricciones-externas.md` §3

## Contexto
La v1 y la primera versión de la v2 no modelaban el IVA. En Bolivia rige el IVA del 13 % **incluido en el precio** (Ley 843). La **Ley 1733 (27-may-2026)** lo pasa a **"por fuera"**: 13 % sobre el precio neto, separado en la factura. Su entrada en vigencia depende de la reglamentación; el SIN abrió pruebas piloto de sistemas entre el 1-oct y el 16-nov-2026. Una cotización emitida antes del cambio y cobrada después no puede cambiar de monto.

## Decisión
* Tabla **global** `public.tax_regimes(mode, iva_rate, effective_from, legal_reference)`, de solo lectura para los roles de runtime. Hoy tiene una fila: `IVA_INCLUIDO 13 %`. Cuando la norma fije la fecha, operación inserta `IVA_ADICIONAL 13 %` con esa fecha, mediante una migración revisada.
* Cada orden **congela** `tax_regime_id` al crearse y guarda `subtotal_bs` (Σ líneas), `iva_bs` y `total_bs` (a pagar):
  * `IVA_INCLUIDO`: total = subtotal; IVA informado = round(total × 13 %).
  * `IVA_ADICIONAL`: IVA = round(subtotal × 13 %); total = subtotal + IVA.
* La seña se calcula sobre el **total a pagar**.
* La búsqueda devuelve `tax_mode` para que el bot diga "IVA incluido" o "+ IVA".
* Al cambiar de régimen, cada negocio debe revisar sus precios de lista (pasan a ser netos). El dashboard mostrará un asistente para esa transición.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Ignorar el IVA y delegarlo al proveedor de facturación | Simple | La cotización mostraría un total distinto del facturado |
| Un indicador por tenant | Flexible | El régimen es nacional; se configuraría mal |
| Calcular el IVA en Python | Familiar | Rompe la fuente única de cálculo (ADR-0007) |

## Consecuencias
* (+) Las cotizaciones son exactas en ambos regímenes y la transición no altera órdenes ya emitidas.
* (−) El IT (3 %) es un costo del negocio y no se muestra al cliente; no se modela aquí. Los descuentos, cuando existan, deberán aplicarse antes del IVA (la base neta los excluye).

## Verificación
`04_tax_regimes.test.sql` (11 pruebas): ejemplos oficiales de cálculo, cotización en cada régimen, régimen congelado y prohibición de que un tenant modifique la tabla.
