-- ============================================================================
-- 14 · Precios del catálogo: regla mixta de MFA (#50, ADR-0016)
--
-- Riesgo (SEC-01, riesgo residual): STAFF, OWNER y ADMIN editaban list_price sin
-- MFA. Con una sesión robada, un precio en 0 o una moneda cambiada se congela en
-- las cotizaciones nuevas, y la oferta obliga al negocio (Ley 453, art. 18.1).
--
-- Regla:
--   * Cambio normal (variación <= umbral): OWNER/ADMIN/STAFF, sin MFA.
--   * Cambio sensible: precio 0, cambio de moneda o variación > umbral del negocio
--     (organizations.price_change_mfa_threshold_pct, 30 % por defecto):
--     solo OWNER/ADMIN con aal2.
--   * El backend (BOT o SYSTEM) nunca cambia precios ni da de alta ítems a precio 0.
--   * Cada cambio de precio aplicado encola PRICE_CHANGE_ALERT para avisar al dueño;
--     audit_log ya guarda el antes y el después (tg_audit, migración 04).
-- Las conexiones de operación (postgres en el editor SQL, migraciones) no pasan por
-- la regla: ya tienen acceso total a la BD.
-- Pruebas: supabase/tests/database/11_catalog_price_mfa.test.sql
-- ============================================================================

-- a. Umbral por negocio (editarlo exige aal2: política org_admin_update, migración 09)
ALTER TABLE public.organizations
    ADD COLUMN price_change_mfa_threshold_pct numeric(5,2) NOT NULL DEFAULT 30
        CONSTRAINT organizations_price_threshold_range CHECK (price_change_mfa_threshold_pct BETWEEN 1 AND 100);

GRANT UPDATE (price_change_mfa_threshold_pct) ON public.organizations TO authenticated;

-- b. ¿Es sensible el cambio? Función pura: misma respuesta en el guard y en el aviso.
CREATE OR REPLACE FUNCTION private.price_change_is_sensitive(
    p_old_price numeric, p_new_price numeric,
    p_old_currency public.price_currency, p_new_currency public.price_currency,
    p_threshold_pct numeric)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = ''
AS $$
    SELECT p_new_price = 0
        OR (p_old_currency IS NOT NULL AND p_new_currency IS DISTINCT FROM p_old_currency)
        OR (p_old_price > 0 AND abs(p_new_price - p_old_price) * 100 / p_old_price > p_threshold_pct)
$$;
GRANT EXECUTE ON FUNCTION private.price_change_is_sensitive(numeric, numeric, public.price_currency,
                                                           public.price_currency, numeric)
    TO app_backend, authenticated;

-- c. Guard (SECURITY INVOKER: evalúa el rol y el JWT de quien escribe)
CREATE OR REPLACE FUNCTION private.tg_catalog_price_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_threshold numeric;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.list_price IS NOT DISTINCT FROM OLD.list_price
       AND NEW.price_currency IS NOT DISTINCT FROM OLD.price_currency THEN
        RETURN NEW;
    END IF;

    -- Backend (API o worker, también dentro de RPCs SECURITY DEFINER)
    IF current_user = 'app_backend' OR session_user = 'app_backend' THEN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'El backend no cambia precios del catálogo (ítem %)', OLD.sku
                USING ERRCODE = '42501', HINT = 'PRICE_CHANGE_FORBIDDEN';
        ELSIF NEW.list_price = 0 THEN
            RAISE EXCEPTION 'El backend no da de alta ítems a precio 0 (ítem %)', NEW.sku
                USING ERRCODE = '42501', HINT = 'PRICE_CHANGE_FORBIDDEN';
        END IF;
        RETURN NEW;
    END IF;

    -- Operación con acceso total (postgres, migraciones): fuera de la regla
    IF current_user <> 'authenticated' THEN
        RETURN NEW;
    END IF;

    SELECT o.price_change_mfa_threshold_pct INTO v_threshold
    FROM public.organizations o WHERE o.id = NEW.organization_id;

    IF private.price_change_is_sensitive(
           CASE WHEN TG_OP = 'UPDATE' THEN OLD.list_price END, NEW.list_price,
           CASE WHEN TG_OP = 'UPDATE' THEN OLD.price_currency END, NEW.price_currency,
           coalesce(v_threshold, 30)) THEN
        IF NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
           OR coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' THEN
            RAISE EXCEPTION 'Cambio de precio sensible en % (precio 0, cambio de moneda o variación mayor al % %%): requiere OWNER o ADMIN con MFA',
                NEW.sku, coalesce(v_threshold, 30)
                USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_catalog_price_guard
    BEFORE INSERT OR UPDATE OF list_price, price_currency ON public.catalog_items
    FOR EACH ROW EXECUTE FUNCTION private.tg_catalog_price_guard();

-- d. Aviso al dueño (SECURITY DEFINER: authenticated no escribe en private.jobs).
--    El worker lo entrega por el dashboard o por plantilla de utilidad (fase 1/5).
CREATE OR REPLACE FUNCTION private.tg_catalog_price_alert()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NEW.list_price IS NOT DISTINCT FROM OLD.list_price
       AND NEW.price_currency IS NOT DISTINCT FROM OLD.price_currency THEN
        RETURN NULL;
    END IF;
    INSERT INTO private.jobs (organization_id, kind, payload)
    SELECT NEW.organization_id, 'PRICE_CHANGE_ALERT', jsonb_build_object(
               'catalog_item_id', NEW.id,
               'sku',             NEW.sku,
               'old_price',       OLD.list_price,
               'new_price',       NEW.list_price,
               'old_currency',    OLD.price_currency,
               'new_currency',    NEW.price_currency,
               'sensitive',       private.price_change_is_sensitive(OLD.list_price, NEW.list_price,
                                      OLD.price_currency, NEW.price_currency, o.price_change_mfa_threshold_pct),
               'changed_by',      private.jwt_uid(),
               'db_role',         session_user)
    FROM public.organizations o WHERE o.id = NEW.organization_id;
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION private.tg_catalog_price_alert() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER trg_catalog_price_alert
    AFTER UPDATE OF list_price, price_currency ON public.catalog_items
    FOR EACH ROW EXECUTE FUNCTION private.tg_catalog_price_alert();
