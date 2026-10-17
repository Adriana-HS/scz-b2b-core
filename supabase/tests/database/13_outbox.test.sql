-- ============================================================================
-- 13 · Outbox (#18, RF-06, RF-08, ADR-0006)
--   * idempotency_key única
--   * fuera de la ventana de 24 h solo plantillas; las plantillas exigen consentimiento
--     y respetan la baja (falla cerrado hasta el #13, que afina por categoría)
--   * estado SENDING (se confirma antes de llamar a Meta: sin duplicados si el worker muere)
--   * la conversación recuerda su número, con FK compuesta por tenant
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(14);

UPDATE public.customers SET last_inbound_at = now() - interval '1 hour'
 WHERE id = 'cc000000-0000-4000-8000-00000000000a';

-- --- Ventana de 24 h ----------------------------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT lives_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-1', 'TEXT', '{"body":"hola"}') $$,
    'texto libre dentro de la ventana de 24 h');
SELECT throws_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-1', 'TEXT', '{"body":"otra vez"}') $$,
    '23505', NULL, 'la misma idempotency_key no crea un segundo envío');
SELECT tests.reset();

UPDATE public.customers SET last_inbound_at = now() - interval '25 hours'
 WHERE id = 'cc000000-0000-4000-8000-00000000000a';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-2', 'TEXT', '{"body":"tarde"}') $$,
    '%ventana de 24 h%', 'texto libre fuera de la ventana: rechazado');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-3', 'DOCUMENT', '{"media_id":"m"}') $$,
    '%ventana de 24 h%', 'un documento fuera de la ventana tampoco sale');
SELECT tests.reset();

UPDATE public.customers SET last_inbound_at = NULL WHERE id = 'cc000000-0000-4000-8000-00000000000a';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-4', 'TEXT', '{"body":"x"}') $$,
    '%ventana de 24 h%', 'si el cliente nunca escribió, no hay ventana');

-- --- Plantillas: consentimiento y baja ----------------------------------------------
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-5', 'TEMPLATE', '{"name":"recordatorio","category":"UTILITY"}') $$,
    '%consentimiento%', 'plantilla sin opt-in: rechazada');
SELECT tests.reset();
UPDATE public.customers SET optin_utility_at = now() - interval '3 days' WHERE id = 'cc000000-0000-4000-8000-00000000000a';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT lives_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-6', 'TEMPLATE', '{"name":"recordatorio","category":"UTILITY"}') $$,
    'con opt-in, la plantilla sale aunque la ventana esté cerrada');
SELECT tests.reset();
UPDATE public.customers SET optout_at = now() WHERE id = 'cc000000-0000-4000-8000-00000000000a';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_like($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-7', 'TEMPLATE', '{"name":"recordatorio","category":"UTILITY"}') $$,
    '%consentimiento%', 'la baja prevalece sobre el opt-in');

-- --- Estados y tenancy ----------------------------------------------------------------
SELECT lives_ok($$ UPDATE private.outbound_messages SET status = 'SENDING', attempts = attempts + 1 WHERE idempotency_key = 'k-1' $$,
    'el outbox acepta el estado SENDING');
SELECT tests.reset();
SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_ok($$ INSERT INTO private.outbound_messages (organization_id, conversation_id, idempotency_key, kind, payload)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 'k-8', 'TEXT', '{"body":"x"}') $$,
    '42501', NULL, 'el backend de B no encola mensajes en conversaciones de A');
SELECT is((SELECT count(*)::int FROM private.outbound_messages), 0, 'ni los ve');
SELECT tests.reset();

SELECT lives_ok($$ UPDATE public.conversations SET whatsapp_channel_id =
        (SELECT id FROM public.whatsapp_channels WHERE phone_number_id = 'PNID-A')
     WHERE id = 'c0000000-0000-4000-8000-00000000000a' $$,
    'la conversación recuerda el número de su negocio');
SELECT throws_ok($$ UPDATE public.conversations SET whatsapp_channel_id =
        (SELECT id FROM public.whatsapp_channels WHERE phone_number_id = 'PNID-B')
     WHERE id = 'c0000000-0000-4000-8000-00000000000a' $$,
    '23503', NULL, 'FK compuesta: una conversación de A no puede salir por el número de B');

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT throws_ok($$ SELECT count(*) FROM private.outbound_messages $$, '42501', NULL,
                 'el dashboard no lee el outbox interno');
SELECT tests.reset();

SELECT * FROM finish();
ROLLBACK;
