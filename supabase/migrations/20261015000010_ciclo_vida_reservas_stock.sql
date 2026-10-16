-- ============================================================================
-- 10 · Ciclo de vida de las reservas de stock (invariante I-10, Q-ACC-3, ADR-0015)
--
-- Fallas corregidas (revisión del 15-oct-2026):
--   H1. search_catalog y create_quote solo contaban reservas con expires_at > now(),
--       pero expire_stale_orders no vence órdenes en PAYMENT_REVIEW. Pasado
--       valid_until, una orden en revisión dejaba de bloquear su stock, otro cliente
--       lo reservaba y al aprobar el pago stock_on_hand quedaba < 0 (violación de
--       CHECK: la aprobación fallaba sin un error de negocio) o había sobreventa.
--   H2. Solo expire_stale_orders liberaba reservas. Cancelar no liberaba nada hasta
--       expires_at, y cancelar o reembolsar una orden CONFIRMED nunca devolvía el
--       stock comprometido. Además, confirmar sin pago (seña 0) no comprometía stock.
--
-- Corrección:
--   a. Una reserva está activa mientras no se libera ni se compromete; su vigencia la
--      decide el estado de la orden, no el reloj. expires_at queda como dato
--      informativo (la fecha en que el job vencerá la orden si sigue sin pagar).
--   b. Un solo trigger AFTER UPDATE OF status en orders gobierna el ciclo de vida:
--        -> CONFIRMED               compromete (descuenta stock) o falla con HINT
--                                   STOCK_CONFLICT si no hay stock físico;
--        -> CANCELLED / EXPIRED     libera las reservas activas;
--        CONFIRMED -> CANCELLED / REFUNDED  devuelve el stock comprometido
--                                   (la mercadería no se entregó: eso es COMPLETED).
--   c. tg_payments_apply y expire_stale_orders delegan en el trigger.
-- Pruebas: supabase/tests/database/07_stock_lifecycle.test.sql
-- ============================================================================

-- a. Trazabilidad de la devolución de stock ---------------------------------------
ALTER TABLE public.stock_reservations
    ADD COLUMN returned_at timestamptz,
    ADD CONSTRAINT stock_reservations_returned_requires_commit
        CHECK (returned_at IS NULL OR committed_at IS NOT NULL);

COMMENT ON COLUMN public.stock_reservations.expires_at IS
    'Informativo: vigencia de la cotización al reservar. La reserva se libera por el estado de la orden (ADR-0015).';
COMMENT ON COLUMN public.stock_reservations.returned_at IS
    'Momento en que el stock comprometido volvió al inventario (orden CONFIRMED cancelada o reembolsada).';

-- b. Ciclo de vida del stock gobernado por el estado de la orden -------------------
-- SECURITY DEFINER: quien cancela desde el dashboard (authenticated) no tiene
-- privilegios sobre stock_reservations. Solo toca filas de la orden que el llamador
-- ya pudo actualizar (RLS + FSM en tg_orders_guard), filtrando por su organization_id.
CREATE OR REPLACE FUNCTION private.tg_orders_stock_lifecycle()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_short record;
BEGIN
    IF NEW.status = 'CONFIRMED' THEN
        -- Mismo orden de bloqueo que create_quote (evita deadlocks)
        PERFORM 1 FROM public.catalog_items ci
        WHERE ci.organization_id = NEW.organization_id
          AND ci.id IN (SELECT r.catalog_item_id FROM public.stock_reservations r
                        WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
                          AND r.released_at IS NULL AND r.committed_at IS NULL)
        ORDER BY ci.id FOR UPDATE;

        SELECT ci.sku, ci.stock_on_hand, x.qty INTO v_short
        FROM (SELECT r.catalog_item_id, sum(r.quantity)::integer AS qty
              FROM public.stock_reservations r
              WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
                AND r.released_at IS NULL AND r.committed_at IS NULL
              GROUP BY r.catalog_item_id) x
        JOIN public.catalog_items ci ON ci.organization_id = NEW.organization_id AND ci.id = x.catalog_item_id
        WHERE ci.stock_on_hand < x.qty
        ORDER BY ci.sku
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'Stock insuficiente para confirmar % (% en existencia, % reservados)',
                NEW.reference_code, v_short.stock_on_hand, v_short.qty
                USING ERRCODE = 'P0001', HINT = 'STOCK_CONFLICT', DETAIL = v_short.sku;
        END IF;

        UPDATE public.catalog_items ci SET stock_on_hand = ci.stock_on_hand - x.qty
        FROM (SELECT r.catalog_item_id, sum(r.quantity)::integer AS qty
              FROM public.stock_reservations r
              WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
                AND r.released_at IS NULL AND r.committed_at IS NULL
              GROUP BY r.catalog_item_id) x
        WHERE ci.organization_id = NEW.organization_id AND ci.id = x.catalog_item_id;
        UPDATE public.stock_reservations r SET committed_at = now()
        WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
          AND r.released_at IS NULL AND r.committed_at IS NULL;

    ELSIF NEW.status IN ('CANCELLED', 'EXPIRED') THEN
        UPDATE public.stock_reservations r SET released_at = now()
        WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
          AND r.released_at IS NULL AND r.committed_at IS NULL;
    END IF;

    -- Venta confirmada que no se entregó: la mercadería vuelve al inventario
    IF OLD.status = 'CONFIRMED' AND NEW.status IN ('CANCELLED', 'REFUNDED') THEN
        PERFORM 1 FROM public.catalog_items ci
        WHERE ci.organization_id = NEW.organization_id
          AND ci.id IN (SELECT r.catalog_item_id FROM public.stock_reservations r
                        WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
                          AND r.committed_at IS NOT NULL AND r.returned_at IS NULL)
        ORDER BY ci.id FOR UPDATE;

        UPDATE public.catalog_items ci SET stock_on_hand = ci.stock_on_hand + x.qty
        FROM (SELECT r.catalog_item_id, sum(r.quantity)::integer AS qty
              FROM public.stock_reservations r
              WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
                AND r.committed_at IS NOT NULL AND r.returned_at IS NULL
              GROUP BY r.catalog_item_id) x
        WHERE ci.organization_id = NEW.organization_id AND ci.id = x.catalog_item_id;
        UPDATE public.stock_reservations r SET returned_at = now()
        WHERE r.organization_id = NEW.organization_id AND r.order_id = NEW.id
          AND r.committed_at IS NOT NULL AND r.returned_at IS NULL;
    END IF;

    RETURN NULL;
END
$$;

REVOKE ALL ON FUNCTION private.tg_orders_stock_lifecycle() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER trg_orders_stock_lifecycle
    AFTER UPDATE OF status ON public.orders
    FOR EACH ROW WHEN (NEW.status IS DISTINCT FROM OLD.status)
    EXECUTE FUNCTION private.tg_orders_stock_lifecycle();

-- c1. El libro de pagos ya no toca stock: lo hace el trigger al pasar a CONFIRMED ----
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

    -- Al confirmar, trg_orders_stock_lifecycle compromete el stock (o falla con STOCK_CONFLICT)
    IF v_order.status <> 'CONFIRMED'
       AND v_order.amount_paid_bs + NEW.amount_bs >= v_order.deposit_required_bs THEN
        UPDATE public.orders SET status = 'CONFIRMED'
        WHERE id = v_order.id AND organization_id = v_order.organization_id;
    END IF;
    RETURN NULL;
END
$$;

-- c2. El vencimiento solo cambia el estado; el trigger libera las reservas ----------
CREATE OR REPLACE FUNCTION private.expire_stale_orders()
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_count integer;
BEGIN
    PERFORM set_config('app.actor', 'SYSTEM', true);
    -- PAYMENT_REVIEW no vence por tiempo: un humano decide (ADR-0008)
    UPDATE public.orders SET status = 'EXPIRED'
    WHERE status IN ('QUOTED', 'HELD', 'AWAITING_PAYMENT') AND valid_until < now();
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$;

-- a2. Disponibilidad = stock − reservas activas (sin filtro por expires_at) --------
-- Mismo cuerpo que en la migración 06 salvo el predicado de reserva activa, que ahora
-- coincide con el índice parcial idx_reservations_active.
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
          AND r.committed_at IS NULL
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
              AND r.released_at IS NULL AND r.committed_at IS NULL;
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
