# Runbook: tipo de cambio vencido

**Alerta:** `FxRateStale` (ticket): `fx_stale_tenants > 0` durante 10 min.
**Requisito:** Q-ACC. Un precio en USD se cotiza en Bs con el TC vigente del negocio; si está vencido, la BD **no cotiza** ese ítem (`private.unit_price_bs`), y es mejor eso que cotizar con un TC viejo.

## Síntoma

Uno o más negocios tienen ítems activos en USD y no tienen TC, o el último tiene más horas que su tolerancia (`organizations.fx_max_age_hours`, 24 h por defecto). El bot no puede dar precio de esos ítems.

## Diagnóstico

```sql
SELECT o.id, o.name, o.fx_max_age_hours, f.*
  FROM public.organizations o
  CROSS JOIN LATERAL private.current_fx(o.id) f
 WHERE o.is_active AND o.deleted_at IS NULL
   AND EXISTS (SELECT 1 FROM public.catalog_items ci
                WHERE ci.organization_id = o.id AND ci.is_active AND ci.price_currency = 'USD')
   AND coalesce(f.is_stale, true);
```

`private.fx_stale_tenants()` devuelve la misma cuenta que publica la métrica.

## Acción

* El TC lo carga **el negocio**, no la plataforma: un OWNER o ADMIN con MFA (`aal2`) inserta el TC del día en `public.exchange_rates` desde el dashboard. La tabla es append-only: corregir es insertar uno nuevo.
* Avisa al negocio por su canal de soporte: "tus ítems en USD no se están cotizando porque el tipo de cambio venció el …".
* Si el negocio actualiza el TC una vez por semana a propósito, sugiérele subir `fx_max_age_hours` (máximo 720 h) desde la configuración, sabiendo que cotizará con un TC más viejo.

## Verificación

`fx_stale_tenants` baja en el siguiente minuto y la consulta de diagnóstico ya no lista al negocio.

## Comunicación

Solo con el negocio afectado. No hay impacto para los demás.

## Postmortem

No hace falta, salvo que el TC vencido haya producido una cotización equivocada (eso sería un bug de la BD: abre un issue de severidad alta).
