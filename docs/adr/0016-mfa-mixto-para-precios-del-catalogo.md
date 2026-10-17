# ADR-0016: Regla mixta de MFA para los precios del catálogo

* **Estado:** Aceptado
* **Fecha:** 2026-10-16
* **Decisores:** AHS
* **Relacionado:** issue #50 · SYSTEM_DESIGN.md §5.3 (I-22), §8.1, §8.2 · ADR-0007, ADR-0014 · migración 14 · prueba `11_catalog_price_mfa`

## Contexto
Desde SEC-01 (migración 09), aprobar pagos, cargar el TC, editar la organización y cambiar membresías exigen MFA (`aal2`). Los precios del catálogo quedaron fuera a propósito: STAFF los actualiza a diario. Pero el riesgo es del mismo tipo que el del TC:
* Con una sesión robada de STAFF, alguien puede dejar un repuesto en Bs 0, pasar un precio de BOB a USD (que lo multiplica por el TC) o alterarlo mucho.
* `create_quote` congela ese precio en las cotizaciones nuevas (ADR-0007), y por la Ley 453 (art. 18.1) la oferta obliga al negocio.
* Además, el backend (`app_backend`) tenía UPDATE sobre el catálogo: nada en la BD impedía que un bug o una inyección en el agente cambiara un precio.

## Decisión
Regla mixta en la base de datos (`trg_catalog_price_guard`):
* **Cambio normal** (variación ≤ umbral): OWNER, ADMIN o STAFF, sin MFA.
* **Cambio sensible:** precio 0, cambio de moneda de lista o variación mayor a `organizations.price_change_mfa_threshold_pct` (30 % por defecto, rango 1–100). Solo OWNER/ADMIN con `aal2`. Dar de alta un ítem a precio 0 también es sensible.
* **Backend:** ni el agente (`BOT`) ni los jobs (`SYSTEM`) cambian precios ni dan de alta ítems a precio 0.
* **Aviso:** cada cambio de precio aplicado encola `PRICE_CHANGE_ALERT` (quién, antes, después, si fue sensible) para el dueño. `audit_log` ya guarda el antes y el después.
* El umbral es por negocio. Editarlo es editar la organización, así que exige `aal2` (política de la migración 09).
* Las conexiones de operación (`postgres`, migraciones) quedan fuera de la regla: ya tienen acceso total.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| MFA para todo cambio de precio | Máxima seguridad | Fricción diaria para STAFF; empuja a compartir la cuenta del dueño |
| Sin MFA, solo auditoría y aviso | Cero fricción | El precio dañado ya está congelado en cotizaciones antes de que el dueño lea el aviso |
| Límite de variación fijo para toda la plataforma | Simple | Cada rubro tiene volatilidad distinta (repuestos importados vs. servicios) |

## Consecuencias
* (+) Los cambios que más daño hacen (precio 0, moneda, saltos grandes) exigen segundo factor; el trabajo diario de STAFF no cambia.
* (+) El agente no puede alterar precios por ningún camino (defensa en profundidad junto a las tools sin precio, ADR-0007).
* (−) Un ajuste grande legítimo (devaluación, cambio de proveedor) lo hace OWNER/ADMIN con MFA, o el dueño sube el umbral por un tiempo.
* (−) Varios cambios pequeños seguidos pueden sumar una variación grande. El aviso al dueño y la bitácora lo hacen visible; si en el piloto aparece este patrón, se evalúa un límite acumulado por día.
* La importación CSV (RF-10, fase 5) pasa por la misma regla porque escribe con el JWT del usuario.

## Verificación
`supabase/tests/database/11_catalog_price_mfa.test.sql` (20 casos): cambios normales y sensibles por rol y nivel de MFA, umbral configurable, bloqueo del backend, aviso al dueño y bitácora.
