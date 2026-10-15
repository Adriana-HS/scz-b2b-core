-- ============================================================================
-- SCZ-B2B-CORE · Migración 0004 · Funciones auxiliares, invariantes y auditoría
-- Las reglas críticas del negocio se garantizan también en la BD (defensa en
-- profundidad): aunque el backend tenga un bug, el motor rechaza estados inválidos.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Contexto de seguridad
-- ----------------------------------------------------------------------------

-- Tenant activo del backend: SET LOCAL app.current_org_id = '<uuid>' en cada transacción.
-- Si no está definido devuelve NULL y las políticas RLS no dejan ver nada (falla cerrado).
CREATE OR REPLACE FUNCTION private.backend_org_id()
RETURNS uuid
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT nullif(current_setting('app.current_org_id', true), '')::uuid
$$;

-- Organizaciones del usuario autenticado del dashboard, opcionalmente filtradas por rol.
-- SECURITY DEFINER para leer organization_members sin recursión de RLS.
CREATE OR REPLACE FUNCTION private.user_org_ids(p_roles public.member_role[] DEFAULT NULL)
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT m.organization_id
    FROM public.organization_members m
    JOIN public.organizations o ON o.id = m.organization_id
    WHERE m.user_id = auth.uid()
      AND o.is_active
      AND o.deleted_at IS NULL
      AND (p_roles IS NULL OR m.role = ANY (p_roles))
$$;

-- Usuario del JWT (mismo criterio que auth.uid()) sin requerir acceso al esquema auth,
-- para que el rol app_backend pueda evaluarlo en triggers.
CREATE OR REPLACE FUNCTION private.jwt_uid()
RETURNS uuid
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT nullif(coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                           nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'), '')::uuid
$$;

-- ¿Quién está actuando? HUMAN (dashboard), BOT (agente IA) o SYSTEM (worker/banco).
-- Un JWT de usuario identifica a un humano incluso dentro de RPCs SECURITY DEFINER.
CREATE OR REPLACE FUNCTION private.actor_kind()
RETURNS text
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT CASE
        WHEN current_user = 'authenticated' OR private.jwt_uid() IS NOT NULL THEN 'HUMAN'
        WHEN current_setting('app.actor', true) IN ('BOT', 'SYSTEM', 'HUMAN')
             THEN current_setting('app.actor', true)
        ELSE 'SYSTEM'
    END
$$;

GRANT EXECUTE ON FUNCTION private.backend_org_id(), private.jwt_uid(), private.actor_kind() TO app_backend, authenticated;
GRANT EXECUTE ON FUNCTION private.user_org_ids(public.member_role[]) TO authenticated;

-- ----------------------------------------------------------------------------
-- Dinero y tipo de cambio (fuente única del cálculo — ADR-0007)
-- ----------------------------------------------------------------------------

-- Precio unitario en Bs. Redondeo comercial: 2 decimales, mitad lejos de cero
-- (numeric round de PostgreSQL == Decimal.quantize(ROUND_HALF_UP) en Python para valores >= 0).
CREATE OR REPLACE FUNCTION private.unit_price_bs(
    p_currency public.price_currency, p_list_price numeric, p_fx numeric)
RETURNS numeric(12,2)
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = ''
AS $$
    SELECT CASE p_currency
        WHEN 'BOB' THEN round(p_list_price, 2)
        WHEN 'USD' THEN round(p_list_price * p_fx, 2)
    END::numeric(12,2)
$$;

-- Tipo de cambio vigente del tenant (último con effective_at <= now()).
CREATE OR REPLACE FUNCTION private.current_fx(p_org uuid)
RETURNS TABLE (fx_id bigint, rate numeric(10,4), effective_at timestamptz, is_stale boolean)
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT r.id, r.rate_bs_per_usd, r.effective_at,
           r.effective_at < now() - make_interval(hours => o.fx_max_age_hours)
    FROM public.exchange_rates r
    JOIN public.organizations o ON o.id = r.organization_id
    WHERE r.organization_id = p_org AND r.effective_at <= now()
    ORDER BY r.effective_at DESC, r.id DESC
    LIMIT 1
$$;

-- Régimen de IVA vigente en un instante dado
CREATE OR REPLACE FUNCTION private.tax_regime_at(p_at timestamptz)
RETURNS public.tax_regimes
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT t.* FROM public.tax_regimes t
    WHERE t.effective_from <= p_at
    ORDER BY t.effective_from DESC
    LIMIT 1
$$;

-- Desglose de IVA (ADR-0012). Entrada: Σ importes de línea.
--   IVA_INCLUIDO : total = subtotal ; iva = round(total × tasa)       (Ley 843: tasa sobre precio con impuesto)
--   IVA_ADICIONAL: iva = round(subtotal × tasa) ; total = subtotal + iva   (Ley 1733)
CREATE OR REPLACE FUNCTION private.tax_breakdown(p_mode public.tax_mode, p_rate numeric, p_subtotal numeric)
RETURNS TABLE (subtotal_bs numeric(12,2), iva_bs numeric(12,2), total_bs numeric(12,2))
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = ''
AS $$
    SELECT round(p_subtotal, 2)::numeric(12,2),
           round(p_subtotal * p_rate / 100, 2)::numeric(12,2),
           CASE p_mode WHEN 'IVA_INCLUIDO' THEN round(p_subtotal, 2)
                       ELSE round(p_subtotal, 2) + round(p_subtotal * p_rate / 100, 2) END::numeric(12,2)
$$;

GRANT EXECUTE ON FUNCTION private.tax_regime_at(timestamptz),
                          private.tax_breakdown(public.tax_mode, numeric, numeric) TO app_backend, authenticated;

GRANT EXECUTE ON FUNCTION private.unit_price_bs(public.price_currency, numeric, numeric),
                          private.current_fx(uuid) TO app_backend, authenticated;

-- Correlativo legible por tenant/año: COT-2026-000001, CITA-2026-000001
CREATE OR REPLACE FUNCTION private.next_reference(p_org uuid, p_scope text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_year  integer := extract(year FROM now() AT TIME ZONE 'America/La_Paz')::integer;
    v_value bigint;
BEGIN
    IF p_scope NOT IN ('COT', 'CITA') THEN
        RAISE EXCEPTION 'scope inválido: %', p_scope USING ERRCODE = '22023';
    END IF;
    INSERT INTO private.org_counters AS c (organization_id, scope, year, last_value)
    VALUES (p_org, p_scope, v_year, 1)
    ON CONFLICT (organization_id, scope, year)
        DO UPDATE SET last_value = c.last_value + 1
    RETURNING last_value INTO v_value;
    RETURN p_scope || '-' || v_year || '-' || lpad(v_value::text, 6, '0');
END
$$;
REVOKE ALL ON FUNCTION private.next_reference(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION private.next_reference(uuid, text) TO app_backend;

-- ----------------------------------------------------------------------------
-- updated_at automático
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.tg_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'public.organizations', 'public.profiles', 'public.whatsapp_channels', 'public.bank_accounts',
        'public.resources', 'public.catalog_items', 'public.customers', 'public.conversations',
        'public.orders', 'public.payment_evidence', 'public.invoices', 'private.jobs'
    ] LOOP
        EXECUTE format('CREATE TRIGGER trg_updated_at BEFORE UPDATE ON %s
                        FOR EACH ROW EXECUTE FUNCTION private.tg_set_updated_at()', t);
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- Máquina de estados de órdenes (ADR-0004) aplicada por el motor
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.tg_orders_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_actor text := private.actor_kind();
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'FSM: una orden nace en DRAFT (recibido %)', NEW.status
                USING ERRCODE = 'P0001';
        END IF;
        IF NEW.amount_paid_bs <> 0 THEN
            RAISE EXCEPTION 'amount_paid_bs solo lo modifica el libro de pagos' USING ERRCODE = 'P0001';
        END IF;
        -- El régimen de IVA se congela al crear la orden
        IF NEW.tax_regime_id IS NULL THEN
            NEW.tax_regime_id := (private.tax_regime_at(now())).id;
        END IF;
        RETURN NEW;
    END IF;

    -- Montos pagados: solo vía trigger de `payments` (profundidad > 1)
    IF NEW.amount_paid_bs IS DISTINCT FROM OLD.amount_paid_bs AND pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION 'amount_paid_bs solo lo modifica el libro de pagos' USING ERRCODE = 'P0001';
    END IF;

    -- Precio congelado: totales y tipo de cambio solo cambian mientras la orden es DRAFT
    IF OLD.status <> 'DRAFT' AND (
           NEW.total_bs            IS DISTINCT FROM OLD.total_bs
        OR NEW.subtotal_bs         IS DISTINCT FROM OLD.subtotal_bs
        OR NEW.iva_bs              IS DISTINCT FROM OLD.iva_bs
        OR NEW.tax_regime_id       IS DISTINCT FROM OLD.tax_regime_id
        OR NEW.deposit_required_bs IS DISTINCT FROM OLD.deposit_required_bs
        OR NEW.applied_fx_rate     IS DISTINCT FROM OLD.applied_fx_rate
        OR NEW.fx_rate_id          IS DISTINCT FROM OLD.fx_rate_id) THEN
        RAISE EXCEPTION 'Orden % congelada: los montos no cambian después de DRAFT', OLD.reference_code
            USING ERRCODE = 'P0001';
    END IF;

    IF NEW.kind IS DISTINCT FROM OLD.kind OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
        RAISE EXCEPTION 'kind, organization_id y customer_id son inmutables' USING ERRCODE = 'P0001';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM private.fsm_transitions t
            WHERE t.kind = NEW.kind AND t.from_status = OLD.status AND t.to_status = NEW.status
              AND v_actor = ANY (t.allowed_actors)
        ) THEN
            RAISE EXCEPTION 'FSM: transición % -> % no permitida para % (actor %)',
                OLD.status, NEW.status, NEW.kind, v_actor USING ERRCODE = 'P0001';
        END IF;

        -- Invariante central de ADR-0004: CONFIRMED exige pago registrado suficiente
        IF NEW.status = 'CONFIRMED' AND NEW.amount_paid_bs < NEW.deposit_required_bs THEN
            RAISE EXCEPTION 'FSM: % no puede confirmarse: pagado % < requerido %',
                NEW.reference_code, NEW.amount_paid_bs, NEW.deposit_required_bs
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER trg_orders_guard
    BEFORE INSERT OR UPDATE ON public.orders
    FOR EACH ROW EXECUTE FUNCTION private.tg_orders_guard();

-- Las líneas solo se editan mientras la orden está en DRAFT; los totales se recalculan solos.
CREATE OR REPLACE FUNCTION private.tg_order_items_sync()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_order_id uuid := coalesce(NEW.order_id, OLD.order_id);
    v_org      uuid := coalesce(NEW.organization_id, OLD.organization_id);
    v_status   public.order_status;
BEGIN
    SELECT status INTO v_status FROM public.orders WHERE id = v_order_id AND organization_id = v_org;
    IF v_status IS DISTINCT FROM 'DRAFT' THEN
        RAISE EXCEPTION 'Las líneas de la orden solo se modifican en DRAFT (estado %)', v_status
            USING ERRCODE = 'P0001';
    END IF;

    -- Subtotal, IVA y total según el régimen congelado en la orden; la seña es el
    -- promedio ponderado de % de seña por línea aplicado sobre el total a pagar.
    UPDATE public.orders o SET
        subtotal_bs = tb.subtotal_bs,
        iva_bs      = tb.iva_bs,
        total_bs    = tb.total_bs,
        deposit_required_bs = least(
            CASE WHEN coalesce(s.subtotal, 0) = 0 THEN 0
                 ELSE round(s.deposit_weight * tb.total_bs / s.subtotal, 2) END,
            tb.total_bs)
    FROM (
        SELECT coalesce(sum(oi.line_total_bs), 0) AS subtotal,
               coalesce(sum(oi.line_total_bs
                            * coalesce(ci.deposit_percent_override, org.deposit_percent) / 100), 0) AS deposit_weight
        FROM public.order_items oi
        JOIN public.catalog_items ci ON ci.organization_id = oi.organization_id AND ci.id = oi.catalog_item_id
        JOIN public.organizations org ON org.id = oi.organization_id
        WHERE oi.order_id = v_order_id AND oi.organization_id = v_org
    ) s
    JOIN public.orders o2 ON o2.id = v_order_id AND o2.organization_id = v_org
    JOIN public.tax_regimes tr ON tr.id = o2.tax_regime_id
    CROSS JOIN LATERAL private.tax_breakdown(tr.mode, tr.iva_rate, s.subtotal) tb
    WHERE o.id = v_order_id AND o.organization_id = v_org;
    RETURN NULL;
END
$$;

CREATE TRIGGER trg_order_items_sync
    AFTER INSERT OR UPDATE OR DELETE ON public.order_items
    FOR EACH ROW EXECUTE FUNCTION private.tg_order_items_sync();

-- Libro de pagos: append-only, actualiza lo pagado y confirma cuando corresponde.
CREATE OR REPLACE FUNCTION private.tg_payments_apply()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_order public.orders%ROWTYPE;
BEGIN
    IF private.actor_kind() = 'BOT' THEN
        RAISE EXCEPTION 'El agente IA no puede registrar pagos' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_order FROM public.orders
    WHERE id = NEW.order_id AND organization_id = NEW.organization_id
    FOR UPDATE;

    IF v_order.status NOT IN ('QUOTED', 'HELD', 'AWAITING_PAYMENT', 'PAYMENT_REVIEW', 'CONFIRMED') THEN
        RAISE EXCEPTION 'No se aceptan pagos para la orden % en estado %',
            v_order.reference_code, v_order.status USING ERRCODE = 'P0001';
    END IF;

    UPDATE public.orders SET amount_paid_bs = amount_paid_bs + NEW.amount_bs
    WHERE id = v_order.id AND organization_id = v_order.organization_id;

    IF v_order.status <> 'CONFIRMED'
       AND v_order.amount_paid_bs + NEW.amount_bs >= v_order.deposit_required_bs THEN
        UPDATE public.orders SET status = 'CONFIRMED'
        WHERE id = v_order.id AND organization_id = v_order.organization_id;
        -- Al confirmar una venta, la reserva de stock se vuelve definitiva
        UPDATE public.catalog_items ci SET stock_on_hand = ci.stock_on_hand - r.quantity
        FROM public.stock_reservations r
        WHERE r.order_id = v_order.id AND r.organization_id = v_order.organization_id
          AND r.released_at IS NULL AND r.committed_at IS NULL
          AND ci.organization_id = r.organization_id AND ci.id = r.catalog_item_id;
        UPDATE public.stock_reservations SET committed_at = now()
        WHERE order_id = v_order.id AND organization_id = v_order.organization_id
          AND released_at IS NULL AND committed_at IS NULL;
    END IF;
    RETURN NULL;
END
$$;

CREATE TRIGGER trg_payments_apply
    AFTER INSERT ON public.payments
    FOR EACH ROW EXECUTE FUNCTION private.tg_payments_apply();

CREATE OR REPLACE FUNCTION private.tg_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    RAISE EXCEPTION '% es append-only: % no permitido', TG_TABLE_NAME, TG_OP USING ERRCODE = '42501';
END
$$;

CREATE TRIGGER trg_payments_append_only BEFORE UPDATE OR DELETE ON public.payments
    FOR EACH ROW EXECUTE FUNCTION private.tg_append_only();
CREATE TRIGGER trg_fx_append_only BEFORE UPDATE OR DELETE ON public.exchange_rates
    FOR EACH ROW EXECUTE FUNCTION private.tg_append_only();
CREATE TRIGGER trg_audit_append_only BEFORE UPDATE OR DELETE ON private.audit_log
    FOR EACH ROW EXECUTE FUNCTION private.tg_append_only();

-- ----------------------------------------------------------------------------
-- Auditoría genérica (quién cambió qué, cuándo y desde qué rol)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.tg_audit()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_new jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    v_old jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    v_row jsonb := coalesce(v_new, v_old);
BEGIN
    -- No duplicar embeddings ni vectores de búsqueda en la bitácora
    v_new := v_new - 'embedding' - 'search_text';
    v_old := v_old - 'embedding' - 'search_text';
    INSERT INTO private.audit_log (organization_id, table_name, row_id, action,
                                   actor_kind, actor_id, db_role, old_data, new_data)
    VALUES (
        CASE WHEN TG_TABLE_NAME = 'organizations' THEN (v_row->>'id')::uuid
             ELSE (v_row->>'organization_id')::uuid END,
        TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
        coalesce(v_row->>'id', v_row->>'user_id', '?'),
        TG_OP, private.actor_kind(), private.jwt_uid(), session_user, v_old, v_new
    );
    RETURN NULL;
END
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'public.organizations', 'public.organization_members', 'public.bank_accounts',
        'public.whatsapp_channels', 'public.exchange_rates', 'public.catalog_items',
        'public.orders', 'public.payment_evidence', 'public.payments', 'public.invoices'
    ] LOOP
        EXECUTE format('CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON %s
                        FOR EACH ROW EXECUTE FUNCTION private.tg_audit()', t);
    END LOOP;
END
$$;

-- Al editar nombre/descr. de un ítem, su embedding queda obsoleto y se re-encola.
CREATE OR REPLACE FUNCTION private.tg_catalog_embedding_stale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.name IS DISTINCT FROM OLD.name
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.category IS DISTINCT FROM OLD.category THEN
        IF TG_OP = 'UPDATE' THEN
            NEW.embedding := NULL;
            NEW.embedding_updated_at := NULL;
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_catalog_embedding_stale
    BEFORE INSERT OR UPDATE ON public.catalog_items
    FOR EACH ROW EXECUTE FUNCTION private.tg_catalog_embedding_stale();

CREATE OR REPLACE FUNCTION private.tg_catalog_enqueue_embedding()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NEW.embedding IS NULL THEN
        INSERT INTO private.jobs (organization_id, kind, payload)
        VALUES (NEW.organization_id, 'EMBED_ITEM', jsonb_build_object('catalog_item_id', NEW.id));
    END IF;
    RETURN NULL;
END
$$;

CREATE TRIGGER trg_catalog_enqueue_embedding
    AFTER INSERT OR UPDATE OF name, description, category ON public.catalog_items
    FOR EACH ROW EXECUTE FUNCTION private.tg_catalog_enqueue_embedding();
