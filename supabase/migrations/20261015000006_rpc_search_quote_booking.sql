-- ============================================================================
-- SCZ-B2B-CORE · Migración 0006 · RPCs transaccionales (lo que invocan las tools)
-- Regla de oro (ADR-0004/0007): el LLM elige QUÉ (ítem, cantidad, horario);
-- la BD decide CUÁNTO cuesta y SI es posible, dentro de una sola transacción.
-- Errores de negocio: SQLSTATE P0001 + HINT con código estable que el backend mapea.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Búsqueda híbrida real: FTS + SKU exacto/trigram + nombre difuso + vector,
--    fusionados con Reciprocal Rank Fusion (RRF, k = 60).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.search_catalog(
    p_query            text,
    p_query_embedding  extensions.vector(1536) DEFAULT NULL,
    p_limit            integer DEFAULT 8,
    p_category         text DEFAULT NULL
)
RETURNS TABLE (
    item_id          uuid,
    sku              text,
    name             text,
    description      text,
    item_type        public.item_type,
    stock_available  integer,
    duration_minutes integer,
    unit_price_bs    numeric(12,2),
    price_status     text,              -- OK | FX_MISSING | FX_STALE
    applied_fx_rate  numeric(10,4),
    tax_mode         public.tax_mode,   -- el bot dice "IVA incluido" o "+ IVA" según esto
    score            double precision,
    matched_by       text[]
)
LANGUAGE plpgsql STABLE
SET search_path = ''
AS $$
DECLARE
    v_org   uuid := private.backend_org_id();
    v_fx    record;
    v_q     text := left(btrim(coalesce(p_query, '')), 200);
    v_qn    text;
    v_tsq   tsquery;
    v_k     constant integer := 60;
    v_pool  constant integer := 40;
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    p_limit := least(greatest(coalesce(p_limit, 8), 1), 20);
    v_qn    := upper(regexp_replace(v_q, '[^A-Za-z0-9]', '', 'g'));
    v_tsq   := websearch_to_tsquery('spanish'::regconfig, private.f_unaccent(v_q));
    SELECT * INTO v_fx FROM private.current_fx(v_org);

    RETURN QUERY
    WITH base AS (
        SELECT ci.* FROM public.catalog_items ci
        WHERE ci.organization_id = v_org AND ci.is_active
          AND (p_category IS NULL OR ci.category = p_category)
    ),
    fts AS (
        SELECT b.id, row_number() OVER (ORDER BY ts_rank_cd(b.search_text, v_tsq) DESC) AS r
        FROM base b WHERE v_q <> '' AND b.search_text @@ v_tsq
        ORDER BY r LIMIT v_pool
    ),
    sku AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY (b.sku_normalized = v_qn) DESC,
                            extensions.similarity(b.sku_normalized, v_qn) DESC) AS r
        FROM base b
        WHERE length(v_qn) >= 3
          AND (b.sku_normalized = v_qn OR extensions.similarity(b.sku_normalized, v_qn) >= 0.45)
        ORDER BY r LIMIT v_pool
    ),
    fuzzy AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY extensions.word_similarity(private.f_unaccent(lower(v_q)),
                                                       private.f_unaccent(lower(b.name))) DESC) AS r
        FROM base b
        WHERE length(v_q) >= 3
          AND extensions.word_similarity(private.f_unaccent(lower(v_q)),
                                         private.f_unaccent(lower(b.name))) >= 0.35
        ORDER BY r LIMIT v_pool
    ),
    vec AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY b.embedding OPERATOR(extensions.<=>) p_query_embedding) AS r
        FROM base b
        WHERE p_query_embedding IS NOT NULL AND b.embedding IS NOT NULL
        ORDER BY r LIMIT v_pool
    ),
    hits AS (
        SELECT id, r, 'FTS'::text AS src FROM fts
        UNION ALL SELECT id, r, 'SKU'    FROM sku
        UNION ALL SELECT id, r, 'FUZZY'  FROM fuzzy
        UNION ALL SELECT id, r, 'VECTOR' FROM vec
    ),
    fused AS (
        SELECT h.id,
               sum(1.0 / (v_k + h.r))
                 + CASE WHEN bool_or(h.src = 'SKU' AND h.r = 1)
                         AND EXISTS (SELECT 1 FROM base x WHERE x.id = h.id AND x.sku_normalized = v_qn)
                        THEN 1.0 ELSE 0 END          AS s,   -- SKU exacto siempre primero
               array_agg(DISTINCT h.src ORDER BY h.src) AS srcs
        FROM hits h GROUP BY h.id
    ),
    reserved AS (
        SELECT r.catalog_item_id, sum(r.quantity)::integer AS qty
        FROM public.stock_reservations r
        WHERE r.organization_id = v_org AND r.released_at IS NULL
          AND r.committed_at IS NULL AND r.expires_at > now()
        GROUP BY r.catalog_item_id
    )
    SELECT b.id, b.sku, b.name, b.description, b.item_type,
           CASE WHEN b.item_type = 'PRODUCT'
                THEN greatest(b.stock_on_hand - coalesce(rs.qty, 0), 0) END,
           b.duration_minutes,
           CASE WHEN b.price_currency = 'BOB' THEN private.unit_price_bs(b.price_currency, b.list_price, NULL)
                WHEN v_fx.rate IS NULL OR v_fx.is_stale THEN NULL
                ELSE private.unit_price_bs(b.price_currency, b.list_price, v_fx.rate) END,
           CASE WHEN b.price_currency = 'BOB' THEN 'OK'
                WHEN v_fx.rate IS NULL THEN 'FX_MISSING'
                WHEN v_fx.is_stale THEN 'FX_STALE'
                ELSE 'OK' END,
           CASE WHEN b.price_currency = 'USD' THEN v_fx.rate END,
           (private.tax_regime_at(now())).mode,
           f.s::double precision,
           f.srcs
    FROM fused f
    JOIN base b ON b.id = f.id
    LEFT JOIN reserved rs ON rs.catalog_item_id = b.id
    ORDER BY f.s DESC, b.name
    LIMIT p_limit;
END
$$;

-- ----------------------------------------------------------------------------
-- 2. Cotización atómica: valida ítems del tenant, congela precio y TC, reserva stock.
-- p_items: [{"item_id": "<uuid>", "quantity": 2}, ...]  (precios NUNCA vienen del LLM)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.create_quote(
    p_customer_id     uuid,
    p_conversation_id uuid,
    p_items           jsonb
)
RETURNS TABLE (order_id uuid, reference_code text, subtotal_bs numeric(12,2), iva_bs numeric(12,2),
               total_bs numeric(12,2), deposit_required_bs numeric(12,2),
               applied_fx_rate numeric(10,4), tax_mode public.tax_mode, valid_until timestamptz)
LANGUAGE plpgsql VOLATILE
SET search_path = ''
AS $$
DECLARE
    v_org       uuid := private.backend_org_id();
    v_orgrow    public.organizations%ROWTYPE;
    v_fx        record;
    v_req       record;
    v_item      public.catalog_items%ROWTYPE;
    v_reserved  integer;
    v_order_id  uuid;
    v_ref       text;
    v_valid     timestamptz;
    v_needs_fx  boolean;
    v_ids       uuid[];
    v_qtys      integer[];
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    IF jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'items debe ser un arreglo de 1 a 50 líneas' USING ERRCODE = 'P0001', HINT = 'INVALID_ITEMS';
    END IF;

    -- Validación estricta de la entrada del LLM (ids y cantidades; nada de precios)
    BEGIN
        SELECT array_agg((e->>'item_id')::uuid ORDER BY (e->>'item_id')::uuid),
               array_agg((e->>'quantity')::integer ORDER BY (e->>'item_id')::uuid)
          INTO v_ids, v_qtys
          FROM jsonb_array_elements(p_items) e;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'item_id/quantity inválidos' USING ERRCODE = 'P0001', HINT = 'INVALID_ITEMS';
    END;
    IF array_position(v_ids, NULL) IS NOT NULL OR array_position(v_qtys, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'item_id/quantity requeridos' USING ERRCODE = 'P0001', HINT = 'INVALID_ITEMS';
    END IF;
    IF (SELECT count(DISTINCT x) FROM unnest(v_ids) x) <> cardinality(v_ids) THEN
        RAISE EXCEPTION 'Ítem repetido en la cotización' USING ERRCODE = 'P0001', HINT = 'DUPLICATE_ITEM';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(v_qtys) q WHERE q NOT BETWEEN 1 AND 10000) THEN
        RAISE EXCEPTION 'Cantidad fuera de rango' USING ERRCODE = 'P0001', HINT = 'INVALID_QUANTITY';
    END IF;

    SELECT * INTO v_orgrow FROM public.organizations WHERE id = v_org;

    -- Bloquear filas de catálogo en orden determinista (evita deadlocks entre cotizaciones)
    PERFORM 1 FROM public.catalog_items ci
    WHERE ci.organization_id = v_org AND ci.id = ANY (v_ids)
    ORDER BY ci.id FOR UPDATE;

    IF (SELECT count(*) FROM public.catalog_items ci
        WHERE ci.organization_id = v_org AND ci.is_active AND ci.id = ANY (v_ids))
       <> cardinality(v_ids) THEN
        RAISE EXCEPTION 'Ítem inexistente o inactivo en este negocio' USING ERRCODE = 'P0001', HINT = 'ITEM_NOT_FOUND';
    END IF;

    SELECT EXISTS (SELECT 1 FROM public.catalog_items ci
                   WHERE ci.organization_id = v_org AND ci.id = ANY (v_ids)
                     AND ci.price_currency = 'USD') INTO v_needs_fx;
    SELECT * INTO v_fx FROM private.current_fx(v_org);
    IF v_needs_fx AND (v_fx.rate IS NULL OR v_fx.is_stale) THEN
        RAISE EXCEPTION 'Tipo de cambio ausente o vencido' USING ERRCODE = 'P0001', HINT = 'FX_STALE';
    END IF;

    v_valid := now() + make_interval(hours => v_orgrow.quote_validity_hours);
    v_ref   := private.next_reference(v_org, 'COT');

    INSERT INTO public.orders (organization_id, customer_id, conversation_id, kind, reference_code,
                               fx_rate_id, applied_fx_rate, valid_until)
    VALUES (v_org, p_customer_id, p_conversation_id, 'SALE', v_ref,
            CASE WHEN v_needs_fx THEN v_fx.fx_id END, CASE WHEN v_needs_fx THEN v_fx.rate END, v_valid)
    RETURNING id INTO v_order_id;

    FOR v_req IN SELECT u.item_id, u.quantity FROM unnest(v_ids, v_qtys) AS u(item_id, quantity) LOOP
        SELECT * INTO v_item FROM public.catalog_items ci
        WHERE ci.organization_id = v_org AND ci.id = v_req.item_id;

        IF v_item.item_type = 'PRODUCT' THEN
            SELECT coalesce(sum(r.quantity), 0) INTO v_reserved
            FROM public.stock_reservations r
            WHERE r.organization_id = v_org AND r.catalog_item_id = v_item.id
              AND r.released_at IS NULL AND r.committed_at IS NULL AND r.expires_at > now();
            IF v_item.stock_on_hand - v_reserved < v_req.quantity THEN
                RAISE EXCEPTION 'Stock insuficiente para % (disponible %)', v_item.sku,
                    v_item.stock_on_hand - v_reserved
                    USING ERRCODE = 'P0001', HINT = 'OUT_OF_STOCK', DETAIL = v_item.sku;
            END IF;
            INSERT INTO public.stock_reservations (organization_id, order_id, catalog_item_id, quantity, expires_at)
            VALUES (v_org, v_order_id, v_item.id, v_req.quantity, v_valid);
        END IF;

        INSERT INTO public.order_items (organization_id, order_id, catalog_item_id, sku_snapshot,
                                        name_snapshot, quantity, unit_price_bs)
        VALUES (v_org, v_order_id, v_item.id, v_item.sku, v_item.name, v_req.quantity,
                private.unit_price_bs(v_item.price_currency, v_item.list_price, v_fx.rate));
    END LOOP;

    UPDATE public.orders o SET status = 'QUOTED' WHERE o.id = v_order_id AND o.organization_id = v_org;

    RETURN QUERY
    SELECT o.id, o.reference_code, o.subtotal_bs, o.iva_bs, o.total_bs, o.deposit_required_bs,
           o.applied_fx_rate, tr.mode, o.valid_until
    FROM public.orders o JOIN public.tax_regimes tr ON tr.id = o.tax_regime_id
    WHERE o.id = v_order_id AND o.organization_id = v_org;
END
$$;

-- ----------------------------------------------------------------------------
-- 3. Disponibilidad de agenda (horario del recurso − citas activas), zona horaria del tenant
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.available_slots(
    p_service_item_id uuid,
    p_date            date,
    p_resource_id     uuid DEFAULT NULL
)
RETURNS TABLE (resource_id uuid, resource_name text, slot_start timestamptz, slot_end timestamptz)
LANGUAGE plpgsql STABLE
SET search_path = ''
AS $$
DECLARE
    v_org   uuid := private.backend_org_id();
    v_tz    text;
    v_step  integer;
    v_dur   integer;
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    SELECT o.timezone, coalesce((o.config->>'slot_step_minutes')::integer, 30)
      INTO v_tz, v_step FROM public.organizations o WHERE o.id = v_org;
    SELECT ci.duration_minutes INTO v_dur FROM public.catalog_items ci
    WHERE ci.organization_id = v_org AND ci.id = p_service_item_id
      AND ci.item_type = 'SERVICE' AND ci.is_active;
    IF v_dur IS NULL THEN
        RAISE EXCEPTION 'Servicio inexistente' USING ERRCODE = 'P0001', HINT = 'ITEM_NOT_FOUND';
    END IF;

    RETURN QUERY
    WITH candidates AS (
        SELECT r.id AS rid, r.name AS rname,
               gs AT TIME ZONE v_tz AS s_start,
               (gs + make_interval(mins => v_dur)) AT TIME ZONE v_tz AS s_end
        FROM public.resources r
        JOIN public.business_hours bh
          ON bh.organization_id = r.organization_id AND bh.resource_id = r.id
         AND bh.weekday = extract(dow FROM p_date)::smallint
        CROSS JOIN LATERAL generate_series(
            p_date + bh.opens_at,
            p_date + bh.closes_at - make_interval(mins => v_dur),
            make_interval(mins => v_step)) AS gs
        WHERE r.organization_id = v_org AND r.is_active
          AND (p_resource_id IS NULL OR r.id = p_resource_id)
    )
    SELECT c.rid, c.rname, c.s_start, c.s_end
    FROM candidates c
    WHERE c.s_start > now() + interval '15 minutes'
      AND NOT EXISTS (
          SELECT 1 FROM public.orders o
          WHERE o.organization_id = v_org AND o.kind = 'APPOINTMENT' AND o.resource_id = c.rid
            AND o.status IN ('HELD', 'AWAITING_PAYMENT', 'PAYMENT_REVIEW', 'CONFIRMED')
            AND o.slot && tstzrange(c.s_start, c.s_end, '[)'))
    ORDER BY c.s_start, c.rname
    LIMIT 20;
END
$$;

-- ----------------------------------------------------------------------------
-- 4. Retención de horario (HELD) con garantía anti doble-reserva del motor
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.hold_appointment(
    p_customer_id     uuid,
    p_conversation_id uuid,
    p_service_item_id uuid,
    p_resource_id     uuid,
    p_start           timestamptz
)
RETURNS TABLE (order_id uuid, reference_code text, slot_start timestamptz, slot_end timestamptz,
               total_bs numeric(12,2), deposit_required_bs numeric(12,2), valid_until timestamptz)
LANGUAGE plpgsql VOLATILE
SET search_path = ''
AS $$
DECLARE
    v_org      uuid := private.backend_org_id();
    v_orgrow   public.organizations%ROWTYPE;
    v_item     public.catalog_items%ROWTYPE;
    v_slot     tstzrange;
    v_local_s  timestamp;
    v_local_e  timestamp;
    v_order_id uuid;
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    SELECT * INTO v_orgrow FROM public.organizations WHERE id = v_org;
    SELECT * INTO v_item FROM public.catalog_items ci
    WHERE ci.organization_id = v_org AND ci.id = p_service_item_id AND ci.item_type = 'SERVICE' AND ci.is_active;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Servicio inexistente' USING ERRCODE = 'P0001', HINT = 'ITEM_NOT_FOUND';
    END IF;
    IF v_item.price_currency = 'USD' AND NOT EXISTS (
        SELECT 1 FROM private.current_fx(v_org) c WHERE c.rate IS NOT NULL AND NOT c.is_stale) THEN
        RAISE EXCEPTION 'Tipo de cambio ausente o vencido' USING ERRCODE = 'P0001', HINT = 'FX_STALE';
    END IF;
    IF p_start <= now() + interval '15 minutes' THEN
        RAISE EXCEPTION 'Horario en el pasado o demasiado próximo' USING ERRCODE = 'P0001', HINT = 'SLOT_IN_PAST';
    END IF;

    v_slot    := tstzrange(p_start, p_start + make_interval(mins => v_item.duration_minutes), '[)');
    v_local_s := lower(v_slot) AT TIME ZONE v_orgrow.timezone;
    v_local_e := upper(v_slot) AT TIME ZONE v_orgrow.timezone;

    IF NOT EXISTS (
        SELECT 1 FROM public.business_hours bh
        JOIN public.resources r ON r.organization_id = bh.organization_id AND r.id = bh.resource_id
        WHERE bh.organization_id = v_org AND bh.resource_id = p_resource_id AND r.is_active
          AND bh.weekday = extract(dow FROM v_local_s)::smallint
          AND v_local_s::date = v_local_e::date
          AND v_local_s::time >= bh.opens_at AND v_local_e::time <= bh.closes_at
    ) THEN
        RAISE EXCEPTION 'Fuera del horario de atención' USING ERRCODE = 'P0001', HINT = 'OUTSIDE_BUSINESS_HOURS';
    END IF;

    INSERT INTO public.orders (organization_id, customer_id, conversation_id, kind, reference_code,
                               resource_id, slot, valid_until)
    VALUES (v_org, p_customer_id, p_conversation_id, 'APPOINTMENT',
            private.next_reference(v_org, 'CITA'), p_resource_id, v_slot,
            now() + make_interval(mins => v_orgrow.hold_minutes))
    RETURNING id INTO v_order_id;

    INSERT INTO public.order_items (organization_id, order_id, catalog_item_id, sku_snapshot,
                                    name_snapshot, quantity, unit_price_bs)
    SELECT v_org, v_order_id, v_item.id, v_item.sku, v_item.name, 1,
           private.unit_price_bs(v_item.price_currency, v_item.list_price, fx.rate)
    FROM (SELECT (SELECT c.rate FROM private.current_fx(v_org) c) AS rate) fx;

    BEGIN
        UPDATE public.orders o SET status = 'HELD' WHERE o.id = v_order_id AND o.organization_id = v_org;
    EXCEPTION WHEN exclusion_violation THEN
        RAISE EXCEPTION 'El horario ya fue tomado' USING ERRCODE = 'P0001', HINT = 'SLOT_TAKEN';
    END;

    RETURN QUERY
    SELECT o.id, o.reference_code, lower(o.slot), upper(o.slot), o.total_bs, o.deposit_required_bs, o.valid_until
    FROM public.orders o WHERE o.id = v_order_id AND o.organization_id = v_org;
END
$$;

-- ----------------------------------------------------------------------------
-- 5. Revisión humana de evidencias de pago (dashboard, MFA obligatorio por defecto)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.approve_payment_evidence(
    p_evidence_id uuid,
    p_amount_bs   numeric DEFAULT NULL,
    p_note        text DEFAULT NULL
)
RETURNS public.order_status
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_uid    uuid := auth.uid();
    v_ev     public.payment_evidence%ROWTYPE;
    v_org    public.organizations%ROWTYPE;
    v_amount numeric(12,2);
    v_status public.order_status;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'Requiere sesión' USING ERRCODE = '42501', HINT = 'AUTH_REQUIRED';
    END IF;
    SELECT * INTO v_ev FROM public.payment_evidence WHERE id = p_evidence_id FOR UPDATE;
    IF NOT FOUND OR v_ev.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}')) THEN
        RAISE EXCEPTION 'Evidencia no encontrada' USING ERRCODE = '42501', HINT = 'NOT_FOUND';
    END IF;
    SELECT * INTO v_org FROM public.organizations WHERE id = v_ev.organization_id;
    IF coalesce((v_org.config->>'require_mfa_for_payments')::boolean, true)
       AND coalesce(auth.jwt()->>'aal', '') <> 'aal2' THEN
        RAISE EXCEPTION 'Aprobar pagos requiere MFA' USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
    END IF;
    IF v_ev.status NOT IN ('AUTO_CHECK_PASSED', 'NEEDS_REVIEW') THEN
        RAISE EXCEPTION 'Evidencia en estado %', v_ev.status USING ERRCODE = 'P0001', HINT = 'INVALID_STATE';
    END IF;
    v_amount := coalesce(p_amount_bs, v_ev.amount_bs);
    IF v_amount IS NULL OR v_amount <= 0 THEN
        RAISE EXCEPTION 'Monto inválido' USING ERRCODE = 'P0001', HINT = 'INVALID_AMOUNT';
    END IF;

    UPDATE public.payment_evidence
       SET status = 'APPROVED', reviewed_by = v_uid, reviewed_at = now(), review_note = p_note
     WHERE id = v_ev.id;

    INSERT INTO public.payments (organization_id, order_id, evidence_id, channel, amount_bs,
                                 bank_code, txn_number, confirmed_via, confirmed_by)
    VALUES (v_ev.organization_id, v_ev.order_id, v_ev.id, v_ev.channel, v_amount,
            v_ev.bank_code, v_ev.txn_number_normalized, 'HUMAN', v_uid);

    -- Pago parcial sin otras evidencias pendientes: vuelve a esperar pago
    UPDATE public.orders o SET status = 'AWAITING_PAYMENT'
    WHERE o.id = v_ev.order_id AND o.organization_id = v_ev.organization_id
      AND o.status = 'PAYMENT_REVIEW'
      AND NOT EXISTS (SELECT 1 FROM public.payment_evidence e
                      WHERE e.order_id = o.id AND e.status IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW'));

    SELECT status INTO v_status FROM public.orders WHERE id = v_ev.order_id;
    RETURN v_status;
END
$$;

CREATE OR REPLACE FUNCTION public.reject_payment_evidence(
    p_evidence_id uuid,
    p_status      public.evidence_status,
    p_note        text
)
RETURNS public.order_status
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_ev     public.payment_evidence%ROWTYPE;
    v_status public.order_status;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'Requiere sesión' USING ERRCODE = '42501', HINT = 'AUTH_REQUIRED';
    END IF;
    IF p_status NOT IN ('REJECTED_DUPLICATE', 'REJECTED_MISMATCH', 'REJECTED_FRAUD', 'REJECTED_OTHER')
       OR length(coalesce(p_note, '')) < 3 THEN
        RAISE EXCEPTION 'Estado de rechazo o motivo inválido' USING ERRCODE = 'P0001', HINT = 'INVALID_REJECTION';
    END IF;
    SELECT * INTO v_ev FROM public.payment_evidence WHERE id = p_evidence_id FOR UPDATE;
    IF NOT FOUND OR v_ev.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')) THEN
        RAISE EXCEPTION 'Evidencia no encontrada' USING ERRCODE = '42501', HINT = 'NOT_FOUND';
    END IF;
    IF v_ev.status NOT IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW') THEN
        RAISE EXCEPTION 'Evidencia en estado %', v_ev.status USING ERRCODE = 'P0001', HINT = 'INVALID_STATE';
    END IF;

    UPDATE public.payment_evidence
       SET status = p_status, reviewed_by = auth.uid(), reviewed_at = now(), review_note = p_note
     WHERE id = v_ev.id;

    UPDATE public.orders o SET status = 'AWAITING_PAYMENT'
    WHERE o.id = v_ev.order_id AND o.organization_id = v_ev.organization_id
      AND o.status = 'PAYMENT_REVIEW'
      AND NOT EXISTS (SELECT 1 FROM public.payment_evidence e
                      WHERE e.order_id = o.id AND e.status IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW'));

    SELECT status INTO v_status FROM public.orders WHERE id = v_ev.order_id;
    RETURN v_status;
END
$$;

-- ----------------------------------------------------------------------------
-- 6. Mantenimiento: vencimientos y cola de trabajos (los invoca el worker o pg_cron)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.expire_stale_orders()
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_count integer;
BEGIN
    PERFORM set_config('app.actor', 'SYSTEM', true);
    WITH expired AS (
        UPDATE public.orders SET status = 'EXPIRED'
        WHERE status IN ('QUOTED', 'HELD', 'AWAITING_PAYMENT') AND valid_until < now()
        RETURNING id, organization_id
    ), released AS (
        UPDATE public.stock_reservations r SET released_at = now()
        FROM expired e
        WHERE r.order_id = e.id AND r.organization_id = e.organization_id
          AND r.released_at IS NULL AND r.committed_at IS NULL
        RETURNING 1
    )
    SELECT count(*) INTO v_count FROM expired;
    RETURN v_count;
END
$$;

-- Encola (o pospone, para agrupar ráfagas) el procesamiento de una conversación.
CREATE OR REPLACE FUNCTION private.enqueue_conversation(
    p_org uuid, p_conversation_id uuid, p_debounce_ms integer DEFAULT 1500, p_max_wait_ms integer DEFAULT 4000)
RETURNS bigint
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    INSERT INTO private.jobs AS j (organization_id, kind, conversation_key, payload, run_after)
    VALUES (p_org, 'PROCESS_CONVERSATION', p_conversation_id::text,
            jsonb_build_object('conversation_id', p_conversation_id),
            now() + make_interval(secs => p_debounce_ms / 1000.0))
    ON CONFLICT (conversation_key) WHERE status = 'QUEUED' AND kind = 'PROCESS_CONVERSATION'
    DO UPDATE SET run_after = least(now() + make_interval(secs => p_debounce_ms / 1000.0),
                                    j.created_at + make_interval(secs => p_max_wait_ms / 1000.0))
    RETURNING j.id
$$;

-- Reclama trabajos listos sin pisar conversaciones que ya se están procesando.
CREATE OR REPLACE FUNCTION private.claim_jobs(p_worker text, p_batch integer DEFAULT 10)
RETURNS SETOF private.jobs
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    WITH ready AS (
        SELECT j.id FROM private.jobs j
        WHERE j.status = 'QUEUED' AND j.run_after <= now()
          AND (j.conversation_key IS NULL OR NOT EXISTS (
                SELECT 1 FROM private.jobs r
                WHERE r.status = 'RUNNING' AND r.conversation_key = j.conversation_key))
        ORDER BY j.run_after, j.id
        LIMIT least(greatest(p_batch, 1), 100)
        FOR UPDATE SKIP LOCKED
    )
    UPDATE private.jobs j
       SET status = 'RUNNING', locked_at = now(), locked_by = p_worker, attempts = j.attempts + 1
      FROM ready WHERE j.id = ready.id
    RETURNING j.*
$$;

CREATE OR REPLACE FUNCTION private.finish_job(p_job_id bigint, p_ok boolean, p_error text DEFAULT NULL)
RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    UPDATE private.jobs j SET
        status     = CASE WHEN p_ok THEN 'DONE'
                          WHEN j.attempts >= j.max_attempts THEN 'DEAD'
                          ELSE 'QUEUED' END,
        -- backoff exponencial con tope de 10 min
        run_after  = CASE WHEN p_ok THEN j.run_after
                          ELSE now() + least(interval '10 minutes', make_interval(secs => power(2, j.attempts))) END,
        last_error = CASE WHEN p_ok THEN NULL ELSE left(p_error, 2000) END,
        locked_at = NULL, locked_by = NULL
    WHERE j.id = p_job_id AND j.status = 'RUNNING'
$$;

-- Reaper: un worker que murió deja trabajos RUNNING; se reintentan (at-least-once).
CREATE OR REPLACE FUNCTION private.requeue_stale_jobs(p_timeout interval DEFAULT interval '5 minutes')
RETURNS integer
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    WITH s AS (
        UPDATE private.jobs SET status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'QUEUED' END,
                                locked_at = NULL, locked_by = NULL, last_error = 'lease expired'
        WHERE status = 'RUNNING' AND locked_at < now() - p_timeout
        RETURNING 1)
    SELECT count(*)::integer FROM s
$$;

-- Retención: eventos crudos 30 días, trabajos terminados 14 días
CREATE OR REPLACE FUNCTION private.purge_retention()
RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    DELETE FROM private.inbound_events WHERE received_at < now() - interval '30 days';
    DELETE FROM private.jobs WHERE status = 'DONE' AND updated_at < now() - interval '14 days';
$$;

-- ----------------------------------------------------------------------------
-- 7. Privilegios de ejecución (mínimos)
-- ----------------------------------------------------------------------------
REVOKE ALL ON FUNCTION public.search_catalog(text, extensions.vector, integer, text),
                       public.create_quote(uuid, uuid, jsonb),
                       public.available_slots(uuid, date, uuid),
                       public.hold_appointment(uuid, uuid, uuid, uuid, timestamptz),
                       public.approve_payment_evidence(uuid, numeric, text),
                       public.reject_payment_evidence(uuid, public.evidence_status, text),
                       private.expire_stale_orders(), private.enqueue_conversation(uuid, uuid, integer, integer),
                       private.claim_jobs(text, integer), private.finish_job(bigint, boolean, text),
                       private.requeue_stale_jobs(interval), private.purge_retention()
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.search_catalog(text, extensions.vector, integer, text),
                          public.create_quote(uuid, uuid, jsonb),
                          public.available_slots(uuid, date, uuid),
                          public.hold_appointment(uuid, uuid, uuid, uuid, timestamptz),
                          private.expire_stale_orders(), private.enqueue_conversation(uuid, uuid, integer, integer),
                          private.claim_jobs(text, integer), private.finish_job(bigint, boolean, text),
                          private.requeue_stale_jobs(interval), private.purge_retention()
    TO app_backend;

GRANT EXECUTE ON FUNCTION public.approve_payment_evidence(uuid, numeric, text),
                          public.reject_payment_evidence(uuid, public.evidence_status, text)
    TO authenticated;

-- ----------------------------------------------------------------------------
-- 8. Programación con pg_cron (solo si la extensión está habilitada en el proyecto)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        EXECUTE $c$SELECT cron.schedule('expire-stale-orders', '* * * * *', 'SELECT private.expire_stale_orders()')$c$;
        EXECUTE $c$SELECT cron.schedule('requeue-stale-jobs',  '* * * * *', 'SELECT private.requeue_stale_jobs()')$c$;
        EXECUTE $c$SELECT cron.schedule('purge-retention',     '17 3 * * *', 'SELECT private.purge_retention()')$c$;
    END IF;
END
$$;
