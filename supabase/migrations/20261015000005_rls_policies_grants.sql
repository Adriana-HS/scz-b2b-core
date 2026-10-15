-- ============================================================================
-- SCZ-B2B-CORE · Migración 0005 · Row Level Security y privilegios mínimos
-- Modelo (ADR-0002):
--   * app_backend   -> ve SOLO el tenant fijado con SET LOCAL app.current_org_id.
--   * authenticated -> ve SOLO las organizaciones donde es miembro; escribe según rol.
--   * anon          -> sin acceso a datos de dominio.
--   * service_role  -> reservado para migraciones/operación manual, NUNCA en runtime.
-- Las políticas envuelven las funciones en (SELECT ...) para que el planner las
-- evalúe una sola vez por consulta (recomendación de rendimiento de Supabase).
-- ============================================================================

-- 0. Privilegios de tabla: primero cerrar todo, luego abrir lo mínimo ----------
REVOKE ALL ON ALL TABLES    IN SCHEMA public  FROM anon, authenticated, app_backend;
REVOKE ALL ON ALL TABLES    IN SCHEMA private FROM anon, authenticated, app_backend;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public  FROM anon;

-- Lectura para miembros del dashboard (RLS filtra por tenant)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated;

-- Escrituras del dashboard (columnas acotadas donde aplica)
GRANT UPDATE (name, legal_name, nit, timezone, deposit_percent, quote_validity_hours,
              hold_minutes, fx_max_age_hours, config) ON public.organizations TO authenticated;
GRANT UPDATE (full_name) ON public.profiles TO authenticated;
GRANT INSERT, UPDATE (role), DELETE ON public.organization_members TO authenticated;
GRANT INSERT, UPDATE, DELETE ON public.bank_accounts TO authenticated;
GRANT INSERT ON public.exchange_rates TO authenticated;
GRANT INSERT, UPDATE, DELETE ON public.resources, public.business_hours TO authenticated;
GRANT INSERT, UPDATE ON public.catalog_items TO authenticated;
GRANT UPDATE (full_name, billing_doc_type, billing_doc_number, billing_name, metadata)
    ON public.customers TO authenticated;
GRANT UPDATE (mode, assigned_to, bot_paused_until) ON public.conversations TO authenticated;
GRANT UPDATE (status, notes) ON public.orders TO authenticated;

-- Backend: lee/escribe dominio sin DELETE (los datos financieros no se borran)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_backend;
GRANT INSERT, UPDATE ON public.customers, public.conversations, public.messages,
                       public.orders, public.order_items, public.stock_reservations,
                       public.payment_evidence, public.invoices, public.catalog_items
    TO app_backend;
GRANT INSERT ON public.payments TO app_backend;            -- solo confirmaciones BANK_API (actor SYSTEM)
GRANT INSERT ON public.exchange_rates TO app_backend;      -- job opcional de referencia BCB
GRANT SELECT, INSERT          ON private.inbound_events, private.llm_calls TO app_backend;
GRANT SELECT, INSERT, UPDATE  ON private.jobs, private.outbound_messages   TO app_backend;
GRANT SELECT                  ON private.fsm_transitions                   TO app_backend, authenticated;

-- 1. Activar RLS en TODAS las tablas (públicas y privadas) ---------------------
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('public', 'private')
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
    END LOOP;
END
$$;

-- 2. Política estándar por tenant para tablas con organization_id -------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'public.whatsapp_channels', 'public.bank_accounts', 'public.exchange_rates',
        'public.resources', 'public.business_hours', 'public.catalog_items', 'public.customers',
        'public.conversations', 'public.messages', 'public.orders', 'public.order_items',
        'public.stock_reservations', 'public.payment_evidence', 'public.payments', 'public.invoices',
        'private.inbound_events', 'private.jobs', 'private.outbound_messages', 'private.llm_calls'
    ] LOOP
        EXECUTE format($p$
            CREATE POLICY tenant_backend ON %s TO app_backend
                USING (organization_id = (SELECT private.backend_org_id()))
                WITH CHECK (organization_id = (SELECT private.backend_org_id()))
        $p$, t);
    END LOOP;

    FOREACH t IN ARRAY ARRAY[
        'public.whatsapp_channels', 'public.bank_accounts', 'public.exchange_rates',
        'public.resources', 'public.business_hours', 'public.catalog_items', 'public.customers',
        'public.conversations', 'public.messages', 'public.orders', 'public.order_items',
        'public.stock_reservations', 'public.payment_evidence', 'public.payments', 'public.invoices'
    ] LOOP
        EXECUTE format($p$
            CREATE POLICY tenant_member_select ON %s FOR SELECT TO authenticated
                USING (organization_id IN (SELECT private.user_org_ids()))
        $p$, t);
    END LOOP;
END
$$;

-- Regímenes de IVA: globales y de solo lectura para los roles de runtime
CREATE POLICY tax_regimes_read ON public.tax_regimes FOR SELECT TO app_backend, authenticated USING (true);

-- El catálogo de transiciones es de solo lectura y global
CREATE POLICY fsm_read ON private.fsm_transitions FOR SELECT TO app_backend, authenticated USING (true);

-- 3. Organizaciones ------------------------------------------------------------
CREATE POLICY org_backend_select ON public.organizations FOR SELECT TO app_backend
    USING (id = (SELECT private.backend_org_id()));
CREATE POLICY org_member_select ON public.organizations FOR SELECT TO authenticated
    USING (id IN (SELECT private.user_org_ids()));
CREATE POLICY org_admin_update ON public.organizations FOR UPDATE TO authenticated
    USING (id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')))
    WITH CHECK (id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')));

-- 4. Perfiles y membresías -------------------------------------------------------
CREATE POLICY profile_self_or_colleague ON public.profiles FOR SELECT TO authenticated
    USING (id = (SELECT auth.uid()) OR id IN (
        SELECT m.user_id FROM public.organization_members m
        WHERE m.organization_id IN (SELECT private.user_org_ids())));
CREATE POLICY profile_self_update ON public.profiles FOR UPDATE TO authenticated
    USING (id = (SELECT auth.uid())) WITH CHECK (id = (SELECT auth.uid()));

CREATE POLICY members_select ON public.organization_members FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()) OR organization_id IN (SELECT private.user_org_ids()));
CREATE POLICY members_owner_insert ON public.organization_members FOR INSERT TO authenticated
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER}')));
CREATE POLICY members_owner_update ON public.organization_members FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER}')) AND user_id <> (SELECT auth.uid()))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER}')));
CREATE POLICY members_owner_delete ON public.organization_members FOR DELETE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER}')) AND user_id <> (SELECT auth.uid()));

-- 5. Escrituras por rol en el dashboard ----------------------------------------
-- Datos sensibles (cuentas bancarias, tipo de cambio): OWNER/ADMIN con MFA (aal2)
CREATE POLICY bank_admin_write ON public.bank_accounts FOR ALL TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
           AND (SELECT auth.jwt() ->> 'aal') = 'aal2')
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
           AND (SELECT auth.jwt() ->> 'aal') = 'aal2');

CREATE POLICY fx_admin_insert ON public.exchange_rates FOR INSERT TO authenticated
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
                AND created_by = (SELECT auth.uid()));

CREATE POLICY resources_admin_write ON public.resources FOR ALL TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')));
CREATE POLICY hours_admin_write ON public.business_hours FOR ALL TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}')));

CREATE POLICY catalog_staff_insert ON public.catalog_items FOR INSERT TO authenticated
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')));
CREATE POLICY catalog_staff_update ON public.catalog_items FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')));

CREATE POLICY customers_staff_update ON public.customers FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')));
CREATE POLICY conversations_staff_update ON public.conversations FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')));
CREATE POLICY orders_staff_update ON public.orders FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')))
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')));

-- 6. Defensa extra: el agente IA no inserta pagos aunque use el rol del backend
CREATE POLICY payments_not_bot ON public.payments AS RESTRICTIVE FOR INSERT TO app_backend
    WITH CHECK ((SELECT private.actor_kind()) <> 'BOT' AND confirmed_via = 'BANK_API');

-- 7. Resolución de tenant para el webhook (antes de conocer la organización) ---
CREATE OR REPLACE FUNCTION private.resolve_whatsapp_channel(p_phone_number_id text)
RETURNS TABLE (organization_id uuid, channel_id uuid, access_token_ref text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT c.organization_id, c.id, c.access_token_ref
    FROM public.whatsapp_channels c
    JOIN public.organizations o ON o.id = c.organization_id
    WHERE c.phone_number_id = p_phone_number_id
      AND c.is_active AND o.is_active AND o.deleted_at IS NULL
$$;
REVOKE ALL ON FUNCTION private.resolve_whatsapp_channel(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION private.resolve_whatsapp_channel(text) TO app_backend;
