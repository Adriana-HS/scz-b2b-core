-- ============================================================================
-- 20 · Herramientas del agente: ficha de un ítem y paso a una persona (#24, §7.2)
--
--   a. public.get_item_details(item): la ficha de un ítem activo del tenant, con el
--      precio en Bs, el estado del precio y el stock disponible calculados igual que en
--      search_catalog (la prueba 18 verifica que coincidan ítem por ítem). Solo devuelve
--      los atributos públicos de una lista cerrada: nunca costos ni márgenes.
--   b. conversations.handoff_reason/handoff_summary/handoff_at: para que el equipo sepa
--      por qué el bot le pasó la conversación (RF-04).
--   c. public.request_handoff(conversation, reason, summary): modo HUMAN con esos datos.
--   SECURITY INVOKER: corren con la RLS de app_backend; el tenant sale de
--   private.backend_org_id(), nunca de un parámetro.
-- Pruebas: supabase/tests/database/18_agent_tools.test.sql
-- ============================================================================

-- a. Ficha de un ítem -----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_item_details(p_item_id uuid)
RETURNS TABLE (
    item_id          uuid,
    sku              text,
    name             text,
    description      text,
    category         text,
    item_type        public.item_type,
    stock_available  integer,
    duration_minutes integer,
    unit_price_bs    numeric(12,2),
    price_status     text,              -- OK | FX_MISSING | FX_STALE (igual que search_catalog)
    applied_fx_rate  numeric(10,4),
    tax_mode         public.tax_mode,
    attributes       jsonb
)
LANGUAGE plpgsql STABLE
SET search_path = ''
AS $$
DECLARE
    v_org uuid := private.backend_org_id();
    v_fx  record;
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    SELECT * INTO v_fx FROM private.current_fx(v_org);

    RETURN QUERY
    SELECT ci.id, ci.sku, ci.name, ci.description, ci.category, ci.item_type,
           CASE WHEN ci.item_type = 'PRODUCT'
                THEN greatest(ci.stock_on_hand - coalesce((
                         SELECT sum(r.quantity)::integer FROM public.stock_reservations r
                          WHERE r.organization_id = v_org AND r.catalog_item_id = ci.id
                            AND r.released_at IS NULL AND r.committed_at IS NULL), 0), 0) END,
           ci.duration_minutes,
           CASE WHEN ci.price_currency = 'BOB' THEN private.unit_price_bs(ci.price_currency, ci.list_price, NULL)
                WHEN v_fx.rate IS NULL OR v_fx.is_stale THEN NULL
                ELSE private.unit_price_bs(ci.price_currency, ci.list_price, v_fx.rate) END,
           CASE WHEN ci.price_currency = 'BOB' THEN 'OK'
                WHEN v_fx.rate IS NULL THEN 'FX_MISSING'
                WHEN v_fx.is_stale THEN 'FX_STALE'
                ELSE 'OK' END,
           CASE WHEN ci.price_currency = 'USD' THEN v_fx.rate END,
           (private.tax_regime_at(now())).mode,
           -- Lista cerrada de atributos que el cliente puede ver
           coalesce((SELECT jsonb_object_agg(a.key, a.value) FROM jsonb_each(ci.attributes) a
                      WHERE a.key IN ('compatibilidad', 'medidas', 'material', 'garantia', 'presentacion')),
                    '{}'::jsonb)
    FROM public.catalog_items ci
    WHERE ci.organization_id = v_org AND ci.id = p_item_id AND ci.is_active;
END
$$;

-- b. Datos del paso a una persona ---------------------------------------------------------------
ALTER TABLE public.conversations
    ADD COLUMN handoff_reason  text CHECK (handoff_reason IN
        ('CUSTOMER_REQUEST', 'OUT_OF_SCOPE', 'COMPLAINT', 'TOOL_ERROR', 'PAYMENT_ISSUE', 'OTHER')),
    ADD COLUMN handoff_summary text CHECK (length(handoff_summary) <= 1000),
    ADD COLUMN handoff_at      timestamptz;

-- c. Paso a una persona ------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.request_handoff(p_conversation_id uuid, p_reason text, p_summary text)
RETURNS void
LANGUAGE plpgsql VOLATILE
SET search_path = ''
AS $$
DECLARE
    v_org uuid := private.backend_org_id();
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    UPDATE public.conversations
       SET mode = 'HUMAN', handoff_reason = p_reason, handoff_summary = p_summary,
           handoff_at = now(), bot_paused_until = NULL, updated_at = now()
     WHERE id = p_conversation_id AND organization_id = v_org AND mode <> 'CLOSED';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Conversación no encontrada' USING ERRCODE = '42501', HINT = 'NOT_FOUND';
    END IF;
END
$$;

REVOKE ALL ON FUNCTION public.get_item_details(uuid), public.request_handoff(uuid, text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_item_details(uuid), public.request_handoff(uuid, text, text)
    TO app_backend;
