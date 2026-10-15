# ADR-0002: Multi-tenancy lógico con RLS aplicada también al backend

* **Estado:** Aceptado (reemplaza la implementación de ADR-002 de la v1)
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §5.2–5.3, §8.2 · migraciones 0001, 0002, 0005 · pruebas `01_tenant_isolation`

## Contexto
La v1 promete que "ninguna organización puede leer ni modificar registros de otra, garantizado por RLS". Pero el backend se conecta con **service role**, que en Supabase tiene `BYPASSRLS`: en el camino principal (WhatsApp → bot) la RLS **no se aplica** y el aislamiento depende de que cada consulta Python agregue `WHERE organization_id = …`. Además (verificado ejecutando el SQL de la v1):
* `processed_webhook_messages` no tenía RLS y era legible con la clave pública.
* `profiles` y `order_items` tenían RLS sin políticas (el dashboard no podía leerlas).
* Las políticas no distinguían roles: un AUDITOR podía cambiar el tipo de cambio.
* Las FK simples permitían que una orden de A apuntara a un cliente o ítem de B.

## Decisión
1. Una sola BD y un solo esquema, con `organization_id` en **todas** las tablas de negocio (se mantiene la decisión de la v1).
2. El backend usa el rol **`app_backend`** (sin BYPASSRLS ni DELETE). Cada transacción ejecuta `SET LOCAL app.current_org_id = '<uuid>'` y las políticas `TO app_backend` comparan contra ese valor. Sin valor, no se ve nada (falla cerrado).
3. El dashboard usa `authenticated`, con políticas por **rol de membresía** (`OWNER/ADMIN/STAFF/AUDITOR`) y MFA (`aal2`) para operaciones sensibles.
4. **FK compuestas `(organization_id, id)`** entre entidades del mismo tenant.
5. Las tablas internas viven en el esquema `private`, que no se expone por la API REST.
6. La única lectura previa al tenant es `private.resolve_whatsapp_channel(phone_number_id)` (SECURITY DEFINER, `search_path` fijo).

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Service role + filtros en código (v1) | Simple | Un `WHERE` olvidado = fuga entre tenants; la garantía declarada no existe |
| JWT por tenant emitido por el backend | Reutiliza las políticas de `authenticated` | Gestión de claves de firma; mezcla identidad de usuario con identidad de servicio |
| BD o esquema por tenant | Aislamiento físico | Migraciones N veces, costo, alta lenta (contradice el objetivo de alta en 5 minutos) |

## Consecuencias
* (+) La garantía de aislamiento la da el motor también para el bot; un bug de código no basta para filtrar datos.
* (+) Integridad referencial cross-tenant imposible por construcción.
* (−) Toda unidad de trabajo debe abrir una transacción explícita (ADR-0011). El *repository* lo encapsula.
* (−) La *service role key* queda solo para el `StorageAdapter` (riesgo residual P-7).

## Verificación
21 pruebas pgTAP (`01_tenant_isolation`) en cada PR, incluidas: el backend sin tenant ve 0 filas, no escribe en otro tenant, FK compuesta rechaza mezcla, RLS en el 100 % de las tablas y `search_path` fijo en todas las funciones SECURITY DEFINER.
