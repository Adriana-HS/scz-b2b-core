-- ============================================================================
-- 15 · Observabilidad en la BD (#19, SYSTEM_DESIGN.md §10.2)
--   * jobs.trace_context hereda el traceparent de la transacción (traza de punta a punta
--     a través de la cola); uno mal formado se descarta y nunca rompe el INSERT
--   * queue_stats (por tipo, solo los que procesa el worker) y fx_stale_tenants alimentan
--     métricas y alertas; solo app_backend
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(12);

-- --- Traza a través de la cola ------------------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT set_config('app.traceparent', '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01', true);
SELECT private.enqueue_conversation('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 0, 0);
SELECT is((SELECT trace_context FROM private.jobs WHERE conversation_key = 'c0000000-0000-4000-8000-00000000000a'),
          '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',
          'el trabajo encolado hereda el traceparent de la transacción del webhook');
INSERT INTO private.jobs (organization_id, kind) VALUES ('a0000000-0000-4000-8000-000000000000', 'T_OBS');
SELECT is((SELECT trace_context FROM private.jobs WHERE kind = 'T_OBS'),
          '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',
          'cualquier INSERT en la cola dentro de esa transacción también (default de la columna)');
SELECT set_config('app.traceparent', 'esto no es un traceparent; DROP TABLE x', true);
SELECT lives_ok($$ INSERT INTO private.jobs (organization_id, kind) VALUES ('a0000000-0000-4000-8000-000000000000', 'T_OBS_BAD') $$,
                'un traceparent mal formado no rompe el INSERT');
SELECT is((SELECT trace_context FROM private.jobs WHERE kind = 'T_OBS_BAD'), NULL, '…y se descarta');
SELECT set_config('app.traceparent', '', true);
INSERT INTO private.jobs (organization_id, kind) VALUES ('a0000000-0000-4000-8000-000000000000', 'T_OBS_NONE');
SELECT is((SELECT trace_context FROM private.jobs WHERE kind = 'T_OBS_NONE'), NULL, 'sin traza, NULL');

-- --- Métricas de la cola ----------------------------------------------------------------
SELECT tests.reset();
INSERT INTO private.jobs (kind, status, attempts) VALUES ('T_OBS_DEAD', 'DEAD', 8), ('T_OBS_DEAD', 'DEAD', 8);
UPDATE private.jobs SET run_after = now() - interval '10 minutes', created_at = now() - interval '10 minutes'
 WHERE kind = 'T_OBS';
SELECT tests.as_backend(NULL, 'SYSTEM');   -- el worker sin tenant: la cola es de plataforma
SELECT ok((SELECT queued = 1 AND oldest_ready_s >= 590 FROM private.queue_stats(ARRAY['T_OBS', 'T_OBS_DEAD'])
            WHERE kind = 'T_OBS')
          AND (SELECT dead = 2 FROM private.queue_stats(ARRAY['T_OBS', 'T_OBS_DEAD']) WHERE kind = 'T_OBS_DEAD'),
          'queue_stats cuenta por tipo en cola, DEAD y la espera del trabajo listo más viejo');
SELECT tests.reset();
UPDATE private.jobs SET run_after = now() + interval '5 minutes' WHERE kind = 'T_OBS';
SELECT tests.as_backend(NULL, 'SYSTEM');
SELECT is((SELECT oldest_ready_s FROM private.queue_stats(ARRAY['T_OBS']) WHERE kind = 'T_OBS'), 0::numeric,
          'un trabajo en backoff (run_after futuro) no cuenta como espera: no dispara "cola atascada"');
SELECT is((SELECT count(*)::integer FROM private.queue_stats(ARRAY['T_OBS_DEAD'])
            WHERE kind IN ('T_OBS', 'PROCESS_CONVERSATION')), 0,
          'solo los tipos pedidos: los que aún no tienen handler esperan sin alertar');

-- --- TC vencido en tenants con ítems en USD -------------------------------------------------
SELECT tests.reset();
SELECT is(private.fx_stale_tenants(), 0, 'con TC fresco no hay tenants con TC vencido');
UPDATE public.organizations SET fx_max_age_hours = 1 WHERE id = 'a0000000-0000-4000-8000-000000000000';
SELECT tests.as_backend(NULL, 'SYSTEM');
SELECT is(private.fx_stale_tenants(), 1, 'el TC de A (2 h) con tolerancia de 1 h cuenta como vencido');
SELECT tests.reset();

-- --- Privilegios -----------------------------------------------------------------------------
SELECT ok(has_function_privilege('app_backend', 'private.queue_stats(text[])', 'EXECUTE')
          AND has_function_privilege('app_backend', 'private.fx_stale_tenants()', 'EXECUTE')
          AND NOT has_function_privilege('authenticated', 'private.queue_stats(text[])', 'EXECUTE')
          AND NOT has_function_privilege('anon', 'private.fx_stale_tenants()', 'EXECUTE'),
          'las métricas de plataforma solo las lee el backend');
SELECT ok((SELECT prosecdef FROM pg_proc WHERE oid = 'private.queue_stats(text[])'::regprocedure),
          'queue_stats es SECURITY DEFINER (ve toda la cola sin tenant) y 05 verifica su search_path');

SELECT * FROM finish();
ROLLBACK;
