-- ============================================================================
-- 14 · Consentimiento (#13, RF-08, EXT-16)
--   * record_consent actualiza el estado del cliente y deja evidencia append-only
--   * la baja prevalece; un nuevo opt-in la levanta
--   * plantillas por categoría: UTILITY exige opt-in de utilidad, MARKETING el de marketing
--   * aislamiento por tenant de la evidencia
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(17);

UPDATE public.customers SET last_inbound_at = now() - interval '2 days'   -- ventana cerrada: solo plantillas
 WHERE id = 'cc000000-0000-4000-8000-00000000000a';

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');

-- --- Sin consentimiento ---------------------------------------------------------------
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-1', 'TEMPLATE',
            '{"name":"recordatorio","category":"UTILITY"}') $$,
    '%consentimiento%', 'plantilla de utilidad sin opt-in: rechazada');

-- --- Opt-in de utilidad ----------------------------------------------------------------
SELECT lives_ok($$ SELECT private.record_consent('cc000000-0000-4000-8000-00000000000a', 'OPTIN_UTILITY',
                                                 'WHATSAPP_KEYWORD', 'wamid.alta') $$,
    'registrar el opt-in de utilidad');
SELECT ok((SELECT optin_utility_at IS NOT NULL AND optin_marketing_at IS NULL AND optout_at IS NULL
             FROM public.customers WHERE id = 'cc000000-0000-4000-8000-00000000000a'),
          'el estado del cliente refleja solo el opt-in de utilidad');
SELECT is((SELECT count(*)::int FROM public.consent_events
            WHERE customer_id = 'cc000000-0000-4000-8000-00000000000a' AND kind = 'OPTIN_UTILITY'
              AND source = 'WHATSAPP_KEYWORD' AND evidence = 'wamid.alta'), 1,
          'queda la evidencia: tipo, origen y mensaje que lo respalda');
SELECT lives_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-2', 'TEMPLATE',
            '{"name":"recordatorio","category":"UTILITY"}') $$,
    'con opt-in de utilidad sale una plantilla de utilidad');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-3', 'TEMPLATE',
            '{"name":"promo","category":"MARKETING"}') $$,
    '%consentimiento%', 'el opt-in de utilidad no habilita marketing');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-4', 'TEMPLATE',
            '{"name":"sin_categoria"}') $$,
    '%consentimiento%', 'una plantilla sin categoría se rechaza (falla cerrado)');

-- --- Marketing ----------------------------------------------------------------------------
SELECT lives_ok($$ SELECT private.record_consent('cc000000-0000-4000-8000-00000000000a', 'OPTIN_MARKETING', 'AGENT', 'wamid.si') $$,
    'registrar el opt-in de marketing');
SELECT lives_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-5', 'TEMPLATE',
            '{"name":"promo","category":"MARKETING"}') $$,
    'con opt-in de marketing sale la promoción');

-- --- Baja: prevalece sobre todo -------------------------------------------------------
SELECT lives_ok($$ SELECT private.record_consent('cc000000-0000-4000-8000-00000000000a', 'OPTOUT', 'WHATSAPP_KEYWORD', 'wamid.baja') $$,
    'registrar la baja');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'c-6', 'TEMPLATE',
            '{"name":"recordatorio","category":"UTILITY"}') $$,
    '%consentimiento%', 'después de la baja no sale ninguna plantilla');
SELECT ok(private.template_consent_ok(now(), now(), now(), 'UTILITY') = false
          AND private.template_consent_ok(now(), NULL, NULL, 'UTILITY')
          AND NOT private.template_consent_ok(now(), NULL, NULL, 'MARKETING')
          AND NOT private.template_consent_ok(now(), now(), NULL, 'AUTHENTICATION')
          AND NOT private.template_consent_ok(now(), now(), NULL, NULL),
          'la regla en una sola función: baja prevalece, categoría obligatoria y sin categorías desconocidas');

-- Un nuevo opt-in explícito levanta la baja (solo para esa categoría)
SELECT lives_ok($$ SELECT private.record_consent('cc000000-0000-4000-8000-00000000000a', 'OPTIN_UTILITY', 'WHATSAPP_KEYWORD', 'wamid.alta2') $$,
    'el cliente vuelve a darse de alta');
SELECT ok((SELECT optout_at IS NULL FROM public.customers WHERE id = 'cc000000-0000-4000-8000-00000000000a'),
          'la baja se levanta con un opt-in explícito');

-- --- Evidencia append-only y aislada ------------------------------------------------------
SELECT throws_ok($$ UPDATE public.consent_events SET kind = 'OPTIN_MARKETING' $$, '42501', NULL,
                 'la evidencia de consentimiento no se modifica');
SELECT tests.reset();
SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT is((SELECT count(*)::int FROM public.consent_events), 0, 'otro negocio no ve la evidencia de A');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000003', 'aal1');   -- AUDITOR de A
SELECT is((SELECT count(*)::int FROM public.consent_events), 4, 'el auditor de A ve la evidencia de su negocio');
SELECT tests.reset();

SELECT * FROM finish();
ROLLBACK;
