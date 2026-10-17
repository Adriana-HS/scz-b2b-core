-- ============================================================================
-- 18 · Observabilidad: traza a través de la cola y métricas de plataforma (#19, §10.2)
--
--   a. jobs.trace_context: el traceparent W3C de la transacción que creó el trabajo. Su
--      DEFAULT lo toma de app.traceparent (lo fija la UnitOfWork), así TODO trabajo que
--      nace del webhook -turno del agente, envío del outbox- hereda la traza sin tocar cada
--      INSERT. Uno mal formado se descarta (NULL): nunca rompe el webhook.
--   b. queue_stats(kinds): por tipo, en cola, en curso, DEAD y la espera del trabajo LISTO
--      más viejo (run_after ya vencido). Solo los tipos que el worker procesa: un tipo sin
--      handler todavía (p. ej. PROCESS_CONVERSATION hasta #26) espera a propósito y no debe
--      disparar "cola atascada"; uno en backoff tampoco cuenta como espera.
--   c. fx_stale_tenants(): negocios con ítems en USD activos y TC ausente o vencido (§10.2).
--   Solo app_backend; SECURITY DEFINER porque son métricas de toda la plataforma (sin tenant).
-- Pruebas: supabase/tests/database/15_observability.test.sql
-- ============================================================================

-- a. Traza --------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.current_traceparent()
RETURNS text
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT CASE WHEN current_setting('app.traceparent', true) ~ '^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$'
                THEN current_setting('app.traceparent', true) END
$$;
GRANT EXECUTE ON FUNCTION private.current_traceparent() TO app_backend, authenticated;

ALTER TABLE private.jobs ADD COLUMN trace_context text DEFAULT private.current_traceparent();

-- b. Cola -----------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.queue_stats(p_kinds text[])
RETURNS TABLE (kind text, queued integer, running integer, dead integer, oldest_ready_s numeric)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT j.kind,
           count(*) FILTER (WHERE j.status = 'QUEUED')::integer,
           count(*) FILTER (WHERE j.status = 'RUNNING')::integer,
           count(*) FILTER (WHERE j.status = 'DEAD')::integer,
           coalesce(extract(epoch FROM now() - min(j.run_after)
                            FILTER (WHERE j.status = 'QUEUED' AND j.run_after <= now())), 0)::numeric
    FROM private.jobs j
    WHERE j.status IN ('QUEUED', 'RUNNING', 'DEAD') AND j.kind = ANY (p_kinds)
    GROUP BY j.kind
$$;

-- c. Tipo de cambio ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION private.fx_stale_tenants()
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT count(*)::integer
    FROM public.organizations o
    WHERE o.is_active AND o.deleted_at IS NULL
      AND EXISTS (SELECT 1 FROM public.catalog_items ci
                  WHERE ci.organization_id = o.id AND ci.is_active AND ci.price_currency = 'USD')
      AND coalesce((SELECT f.is_stale FROM private.current_fx(o.id) f), true)
$$;

REVOKE ALL ON FUNCTION private.queue_stats(text[]), private.fx_stale_tenants() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION private.queue_stats(text[]), private.fx_stale_tenants() TO app_backend;
