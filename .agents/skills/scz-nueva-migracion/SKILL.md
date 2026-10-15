---
name: scz-nueva-migracion
description: Crea o revisa una migración SQL de Supabase para SCZ-B2B-Core con aislamiento multi-tenant, privilegios mínimos y pruebas pgTAP. Úsala para cualquier tabla, columna, función, trigger, política RLS o índice nuevo o modificado.
---

# Nueva migración (Supabase / PostgreSQL)

## Archivo
`supabase/migrations/AAAAMMDDHHMMSS_<descripcion>.sql`. Solo hacia adelante: nunca edites una migración ya aplicada en staging o producción.

## Checklist para tablas de negocio
- [ ] `organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT`
- [ ] `UNIQUE (organization_id, id)` si otras tablas la referencian
- [ ] FK a entidades del mismo tenant **compuestas**: `FOREIGN KEY (organization_id, x_id) REFERENCES … (organization_id, id)`
- [ ] Dinero `numeric(12,2)`, TC `numeric(10,4)`, fechas `timestamptz`; `CHECK` de rangos
- [ ] `created_at`/`updated_at` + trigger `private.tg_set_updated_at`
- [ ] Índices en FKs y filtros calientes; índices parciales donde corresponda
- [ ] `ALTER TABLE … ENABLE ROW LEVEL SECURITY`
- [ ] Política `tenant_backend … TO app_backend USING/WITH CHECK (organization_id = (SELECT private.backend_org_id()))`
- [ ] Política `tenant_member_select … TO authenticated USING (organization_id IN (SELECT private.user_org_ids()))` + escrituras por rol
- [ ] `GRANT` explícitos y mínimos (sin DELETE para runtime en datos financieros); nada para `anon`
- [ ] ¿Es financiera? → trigger `private.tg_audit` y, si corresponde, `private.tg_append_only`
- [ ] Tablas internas → esquema `private`

## Funciones
- `SECURITY INVOKER` por defecto; `SECURITY DEFINER` solo si es imprescindible y **siempre** con `SET search_path = ''` y nombres calificados (`public.`, `private.`, `extensions.`)
- El tenant sale de `private.backend_org_id()` o `auth.uid()`, **nunca** de un parámetro
- Errores de negocio: `RAISE EXCEPTION … USING ERRCODE = 'P0001', HINT = '<CODIGO_ESTABLE>'`
- `REVOKE ALL … FROM PUBLIC, anon` + `GRANT EXECUTE` al rol que corresponda

## Pruebas pgTAP (obligatorias)
- Aislamiento: el otro tenant no ve ni escribe; el backend sin tenant ve 0 filas
- Invariantes nuevas y casos límite; concurrencia si hay bloqueos o EXCLUDE
- Correr: `supabase test db`. La prueba 01 verifica automáticamente RLS al 100 % y el `search_path` de las DEFINER

## Documentación
Actualizar `SYSTEM_DESIGN.md` §5 (invariantes si aplica), el ADR relacionado y `CHANGELOG.md`.
