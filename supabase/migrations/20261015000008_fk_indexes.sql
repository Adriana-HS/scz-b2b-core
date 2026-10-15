-- ============================================================================
-- 08 · Índices para claves foráneas
-- Regla `schema-foreign-key-indexes` (supabase/agent-skills): toda FK necesita un
-- índice cuyo prefijo sean sus columnas. Sin él, cada DELETE/UPDATE en la tabla
-- padre recorre la hija completa y los JOIN por tenant pierden el índice.
-- La prueba 05_schema_lint.test.sql impide que vuelva a aparecer una FK sin índice.
-- ============================================================================

-- FK compuestas por tenant (organization_id primero, igual que la FK)
CREATE INDEX IF NOT EXISTS orders_org_conversation_idx
    ON public.orders (organization_id, conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS orders_org_fx_rate_idx
    ON public.orders (organization_id, fx_rate_id);
CREATE INDEX IF NOT EXISTS order_items_org_catalog_item_idx
    ON public.order_items (organization_id, catalog_item_id);
CREATE INDEX IF NOT EXISTS stock_reservations_org_order_idx
    ON public.stock_reservations (organization_id, order_id);
CREATE INDEX IF NOT EXISTS payment_evidence_org_customer_idx
    ON public.payment_evidence (organization_id, customer_id);
CREATE INDEX IF NOT EXISTS payments_org_evidence_idx
    ON public.payments (organization_id, evidence_id) WHERE evidence_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS outbound_messages_org_conversation_idx
    ON private.outbound_messages (organization_id, conversation_id);

-- FK simples al tenant en tablas internas (borrado de un tenant, purgas por tenant)
CREATE INDEX IF NOT EXISTS inbound_events_org_idx    ON private.inbound_events (organization_id);
CREATE INDEX IF NOT EXISTS jobs_org_idx              ON private.jobs (organization_id);
CREATE INDEX IF NOT EXISTS outbound_messages_org_idx ON private.outbound_messages (organization_id);

-- Catálogo global de regímenes (pocas filas, pero la FK se consulta en cada orden)
CREATE INDEX IF NOT EXISTS orders_tax_regime_idx ON public.orders (tax_regime_id);

-- FK a auth.users (borrado de usuarios). Parciales: la mayoría de filas tienen NULL.
CREATE INDEX IF NOT EXISTS exchange_rates_created_by_idx
    ON public.exchange_rates (created_by) WHERE created_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS conversations_assigned_to_idx
    ON public.conversations (assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS payment_evidence_reviewed_by_idx
    ON public.payment_evidence (reviewed_by) WHERE reviewed_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS payments_confirmed_by_idx
    ON public.payments (confirmed_by) WHERE confirmed_by IS NOT NULL;
