-- ============================================================================
-- 16 · Costo de IA por tenant en private.llm_calls (#22, Q-COST-1)
--   * el backend registra en su tenant y no en otro; sin tenant no ve nada
--   * la conversación debe ser del mismo negocio (FK compuesta, migración 19)
--   * tokens, costo y latencia no pueden ser negativos
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(9);

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT lives_ok($$
    INSERT INTO private.llm_calls (organization_id, conversation_id, purpose, provider, model,
                                   input_tokens, output_tokens, cached_tokens, cost_usd, latency_ms, outcome)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'CHAT',
            'anthropic', 'claude-sonnet-5-5', 1200, 350, 8000, 0.011625, 1840, 'OK') $$,
    'el backend registra una llamada en su tenant, con su conversación');
SELECT is((SELECT cost_usd FROM private.llm_calls WHERE model = 'claude-sonnet-5-5'), 0.011625::numeric,
          'el costo queda exacto (numeric(12,6))');

SELECT throws_ok($$
    INSERT INTO private.llm_calls (organization_id, conversation_id, purpose, provider, model)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000b', 'CHAT', 'anthropic', 'm') $$,
    '23503', NULL, 'una conversación de otro negocio no pasa la FK compuesta');
SELECT throws_ok($$
    INSERT INTO private.llm_calls (organization_id, purpose, provider, model)
    VALUES ('b0000000-0000-4000-8000-000000000000', 'CHAT', 'anthropic', 'm') $$,
    '42501', NULL, 'el backend no registra costo a nombre de otro negocio (RLS)');

SELECT throws_ok($$
    INSERT INTO private.llm_calls (organization_id, purpose, provider, model, input_tokens)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'CHAT', 'anthropic', 'm', -1) $$,
    '23514', NULL, 'tokens negativos no');
SELECT throws_ok($$
    INSERT INTO private.llm_calls (organization_id, purpose, provider, model, cost_usd)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'CHAT', 'anthropic', 'm', -0.000001) $$,
    '23514', NULL, 'costo negativo no');
SELECT throws_ok($$
    INSERT INTO private.llm_calls (organization_id, purpose, provider, model, latency_ms)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'CHAT', 'anthropic', 'm', -5) $$,
    '23514', NULL, 'latencia negativa no');

SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT is((SELECT count(*)::integer FROM private.llm_calls), 0, 'el otro negocio no ve el costo de A');
SELECT tests.as_backend(NULL, 'SYSTEM');
SELECT is((SELECT count(*)::integer FROM private.llm_calls), 0, 'el backend sin tenant no ve nada');

SELECT * FROM finish();
ROLLBACK;
