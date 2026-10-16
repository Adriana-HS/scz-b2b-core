-- ============================================================================
-- 09 · Una orden con pagos registrados no se cancela ni vence sin una persona
-- Regresión del seguimiento de SEC-02 (issue #57): BOT y SYSTEM podían llevar a
-- CANCELLED una orden con amount_paid_bs > 0 y expire_stale_orders la pasaba a
-- EXPIRED. Quedaba dinero en payments sin orden viva y sin que nadie decidiera el
-- reembolso (que en el MVP hace el negocio a mano, ADR-0013/0014).
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(18);

-- Devuelve el HINT del error que lanza p_sql (NULL si no falla)
CREATE OR REPLACE FUNCTION tests.hint_of(p_sql text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_hint text;
BEGIN
    EXECUTE p_sql;
    RETURN NULL;
EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    RETURN coalesce(nullif(v_hint, ''), '<sin hint: ' || SQLSTATE || '>');
END $$;
GRANT EXECUTE ON FUNCTION tests.hint_of(text) TO authenticated, app_backend;

-- --- Preparación ------------------------------------------------------------------
-- Cinco cotizaciones de FIL-0001 × 1 (seña 29.14 Bs):
--   P1, P2: AWAITING_PAYMENT con un pago parcial de 10 Bs aprobado por el dueño
--   P3:     QUOTED con un pago parcial de 10 Bs registrado por el sistema
--   U1, U2: AWAITING_PAYMENT sin pagos
CREATE TEMP TABLE ord (k text PRIMARY KEY, id uuid NOT NULL);
GRANT SELECT, INSERT ON ord TO app_backend, authenticated;

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
INSERT INTO ord SELECT 'P1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'P2', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'P3', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'U1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'U2', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id IN (SELECT id FROM ord WHERE k <> 'P3');
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id IN (SELECT id FROM ord WHERE k IN ('P1', 'P2'));
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
VALUES ('ee000000-0000-4000-8000-000000000091', 'a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'P1'),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('9', 64), 'BNB', '909001', 10.00,
        'AUTO_CHECK_PASSED'),
       ('ee000000-0000-4000-8000-000000000092', 'a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'P2'),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('a', 64), 'BNB', '909002', 10.00,
        'AUTO_CHECK_PASSED');
SELECT tests.reset();

-- El dueño aprueba los comprobantes parciales: las órdenes vuelven a esperar pago
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000091');
SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000092');
SELECT tests.reset();

-- Pago parcial confirmado por el sistema sobre una cotización (como postgres: SEC-02
-- le quita a app_backend el INSERT en payments; el actor SYSTEM va explícito)
SELECT set_config('app.actor', 'SYSTEM', true);
INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, bank_code, txn_number, confirmed_via)
SELECT 'a0000000-0000-4000-8000-000000000000', id, 'QR_DYNAMIC_BANK', 10.00, 'BNB', '909003', 'BANK_API'
  FROM ord WHERE k = 'P3';
SELECT tests.reset();

SELECT is((SELECT status::text || ' ' || amount_paid_bs FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P1')),
          'AWAITING_PAYMENT 10.00', 'precondición: P1 espera pago con 10 Bs ya registrados');
SELECT is((SELECT status::text || ' ' || amount_paid_bs FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P3')),
          'QUOTED 10.00', 'precondición: P3 está cotizada con 10 Bs ya registrados');

-- =================================================================================
-- Cancelar una orden con pagos: solo OWNER/ADMIN con aal2
-- =================================================================================
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$,
    '42501', NULL, 'el agente IA no cancela una orden con pagos registrados');
SELECT is(tests.hint_of($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$),
    'HUMAN_REVIEW_REQUIRED', 'el error trae un HINT estable para derivar a una persona');
SELECT tests.reset();

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT is(tests.hint_of($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$),
    'HUMAN_REVIEW_REQUIRED', 'el worker (SYSTEM) tampoco cancela una orden AWAITING_PAYMENT con pagos');
SELECT is(tests.hint_of($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P3') $$),
    'HUMAN_REVIEW_REQUIRED', 'ni una cotización QUOTED con pagos');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal2');   -- STAFF
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$,
    '42501', NULL, 'STAFF no cancela una orden con pagos (decide el reembolso OWNER/ADMIN)');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');   -- OWNER sin MFA
SELECT is(tests.hint_of($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$),
    'MFA_REQUIRED', 'OWNER sin MFA no cancela una orden con pagos');
SELECT tests.reset();

SELECT is((SELECT status::text FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P1')),
          'AWAITING_PAYMENT', 'tras los intentos rechazados P1 sigue viva');

-- Sin pagos, el agente sigue pudiendo cancelar (el cliente desiste)
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT lives_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'U2') $$,
    'el agente IA cancela una orden sin pagos');
SELECT tests.reset();

-- =================================================================================
-- El vencimiento automático omite las órdenes con pagos (quedan para revisión humana)
-- =================================================================================
UPDATE public.orders SET valid_until = now() - interval '1 minute'
 WHERE id IN (SELECT id FROM ord WHERE k IN ('P2', 'P3', 'U1'));
SELECT is(private.expire_stale_orders(), 1, 'el job vence solo la orden vencida sin pagos');
SELECT is((SELECT string_agg(o.k || '=' || x.status, ' ' ORDER BY o.k)
             FROM ord o JOIN public.orders x ON x.id = o.id WHERE o.k IN ('P2', 'P3', 'U1')),
          'P2=AWAITING_PAYMENT P3=QUOTED U1=EXPIRED',
          'las órdenes vencidas con pagos siguen vivas; la que no tiene pagos vence');
SELECT is(private.expire_stale_orders(), 0,
          'en la siguiente pasada el job no las vence ni falla (no bloquean el vencimiento del resto)');

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT lives_ok($$ UPDATE public.orders SET status = 'EXPIRED' WHERE id = (SELECT id FROM ord WHERE k = 'P2') $$,
    'un UPDATE directo a EXPIRED sobre una orden con pagos no falla');
SELECT tests.reset();
SELECT is((SELECT status::text FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P2')),
          'AWAITING_PAYMENT', 'y tampoco la vence (0 filas actualizadas)');

-- =================================================================================
-- Camino humano: OWNER con MFA cancela; el dinero queda registrado para el reembolso
-- =================================================================================
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT lives_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'P2') $$,
    'OWNER con MFA cancela una orden con pagos (reembolso manual del negocio)');
SELECT tests.reset();
SELECT is((SELECT status::text || ' ' || amount_paid_bs FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P2')),
          'CANCELLED 10.00', 'la orden cancelada conserva lo pagado');
SELECT is((SELECT count(*)::int FROM private.audit_log a
            WHERE a.table_name = 'public.orders' AND a.row_id = (SELECT id::text FROM ord WHERE k = 'P2')
              AND a.actor_kind = 'HUMAN' AND a.new_data ->> 'status' = 'CANCELLED'), 1,
          'la cancelación queda auditada a nombre de la persona');

SELECT * FROM finish();
ROLLBACK;
