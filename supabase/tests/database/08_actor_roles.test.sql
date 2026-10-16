-- ============================================================================
-- 08 · SEC-02: actor por defecto, transiciones humanas por rol y FSM de evidencias
-- Regresión de la revisión del 15-oct-2026 (issue #53):
--   M1. Sin app.actor el backend actuaba como SYSTEM y podía registrar pagos BANK_API.
--   M2. STAFF (sin MFA) reembolsaba, cancelaba órdenes pagadas o en revisión y
--       confirmaba órdenes sin seña.
--   M3. app_backend podía marcar una evidencia APPROVED sin pasar por la RPC con MFA.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(40);

-- --- Preparación ------------------------------------------------------------------
-- AMORT-77 sin seña (para órdenes que se confirman sin pago); FIL-0001 con seña 50 %
UPDATE public.catalog_items SET deposit_percent_override = 0 WHERE id = 'ca000000-0000-4000-8000-000000000003';

CREATE TEMP TABLE ord (k text PRIMARY KEY, id uuid NOT NULL);
GRANT SELECT, INSERT ON ord TO app_backend, authenticated;

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
INSERT INTO ord SELECT 'C1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
INSERT INTO ord SELECT 'C2', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
UPDATE public.orders SET status = 'CONFIRMED' WHERE id = (SELECT id FROM ord WHERE k = 'C1');   -- seña 0
SELECT tests.reset();

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
-- Cinco cotizaciones de FIL-0001 × 1 (seña 29.14 Bs): Q1, X1, R1, R2, R3
INSERT INTO ord SELECT 'Q1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'X1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'R1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'R2', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'R3', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id IN (SELECT id FROM ord WHERE k IN ('R1', 'R2', 'R3'));
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id IN (SELECT id FROM ord WHERE k IN ('R1', 'R2'));
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
VALUES ('ee000000-0000-4000-8000-000000000081', 'a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R1'),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('1', 64), 'BNB', '808001', 29.14,
        'AUTO_CHECK_PASSED'),
       ('ee000000-0000-4000-8000-000000000082', 'a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R2'),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('2', 64), 'BNB', '808002', 5.00,
        'NEEDS_REVIEW');
SELECT tests.reset();

-- =================================================================================
-- M1 · Sin actor válido, el backend es BOT (menor privilegio)
-- =================================================================================
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', '');
SELECT is(private.actor_kind(), 'BOT', 'sin app.actor el backend actúa como BOT (falla cerrado)');
SELECT throws_like($$ UPDATE public.orders SET status = 'EXPIRED' WHERE id = (SELECT id FROM ord WHERE k = 'Q1') $$,
    '%no permitida%', 'sin app.actor el backend no ejecuta transiciones reservadas a SYSTEM');
SELECT set_config('app.actor', 'HUMAN', true);
SELECT is(private.actor_kind(), 'BOT', 'app.actor = HUMAN no convierte al backend en persona');
SELECT set_config('request.jwt.claims', json_build_object('sub', 'a1000000-0000-4000-8000-000000000001',
                  'role', 'authenticated', 'aal', 'aal2')::text, true);
SELECT is(private.actor_kind(), 'BOT', 'un JWT fijado por el propio backend tampoco lo convierte en persona');
SELECT throws_like($$ UPDATE public.orders SET status = 'REFUNDED' WHERE id = (SELECT id FROM ord WHERE k = 'C1') $$,
    '%no permitida%', 'el backend no se hace pasar por humano para reembolsar');
SELECT tests.reset();
SELECT is(private.actor_kind(), 'BOT', 'una sesión de mantenimiento sin app.actor también es BOT');

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT is(private.actor_kind(), 'SYSTEM', 'SYSTEM explícito se respeta');
SELECT throws_ok($$ INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, bank_code, txn_number, confirmed_via)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R3'), 'QR_DYNAMIC_BANK', 29.14,
            'BNB', '808099', 'BANK_API') $$,
    '42501', NULL, 'el backend no registra pagos bancarios: no hay integración bancaria en el MVP (ADR-0014)');
SELECT tests.reset();
SELECT ok(NOT has_table_privilege('app_backend', 'public.payments', 'INSERT'),
          'app_backend no tiene INSERT en payments hasta la fase 7');
SELECT throws_ok($$ INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, bank_code, txn_number, confirmed_via)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R3'), 'QR_DYNAMIC_BANK', 29.14,
            'BNB', '808098', 'BANK_API') $$,
    '42501', NULL, 'un pago BANK_API exige actor SYSTEM explícito, también en mantenimiento');

-- El job de vencimientos fija SYSTEM por sí mismo: sigue funcionando sin actor externo
UPDATE public.orders SET valid_until = now() - interval '1 minute' WHERE id = (SELECT id FROM ord WHERE k = 'X1');
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', '');
SELECT is(private.expire_stale_orders(), 1, 'expire_stale_orders sigue venciendo órdenes (fija SYSTEM explícitamente)');
SELECT tests.reset();

-- =================================================================================
-- M2 · STAFF solo cancela antes del pago; lo demás exige OWNER/ADMIN con MFA
-- =================================================================================
SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal2');   -- STAFF (incluso con MFA)
SELECT throws_ok($$ UPDATE public.orders SET status = 'REFUNDED' WHERE id = (SELECT id FROM ord WHERE k = 'C1') $$,
    '42501', NULL, 'STAFF no reembolsa');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'C1') $$,
    '42501', NULL, 'STAFF no cancela una orden CONFIRMED');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'R1') $$,
    '42501', NULL, 'STAFF no cancela una orden en revisión de pago');
SELECT throws_ok($$ UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT id FROM ord WHERE k = 'R1') $$,
    '42501', NULL, 'STAFF no saca de revisión una orden con evidencia pendiente (evita cancelar en dos pasos)');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CONFIRMED' WHERE id = (SELECT id FROM ord WHERE k = 'C2') $$,
    '42501', NULL, 'STAFF no confirma órdenes sin seña');
SELECT lives_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'Q1') $$,
    'STAFF cancela una cotización antes del pago');
SELECT is(public.reject_payment_evidence('ee000000-0000-4000-8000-000000000082', 'REJECTED_MISMATCH',
                                         'el monto no coincide')::text,
          'AWAITING_PAYMENT', 'STAFF rechaza evidencias y la orden vuelve a esperar pago');
SELECT tests.reset();

-- Pago parcial aprobado: la orden vuelve a AWAITING_PAYMENT con dinero registrado
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
UPDATE public.orders SET status = 'PAYMENT_REVIEW' WHERE id = (SELECT id FROM ord WHERE k = 'R2');
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
VALUES ('ee000000-0000-4000-8000-000000000083', 'a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R2'),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('3', 64), 'BNB', '808003', 10.00,
        'AUTO_CHECK_PASSED');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT is(public.approve_payment_evidence('ee000000-0000-4000-8000-000000000083')::text, 'AWAITING_PAYMENT',
          'pago parcial aprobado: la orden espera el saldo');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal2');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'R2') $$,
    '42501', NULL, 'STAFF no cancela una orden con un pago parcial registrado');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');   -- OWNER sin MFA
SELECT throws_ok($$ UPDATE public.orders SET status = 'REFUNDED' WHERE id = (SELECT id FROM ord WHERE k = 'C1') $$,
    '42501', NULL, 'OWNER sin MFA no reembolsa');
SELECT throws_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'R1') $$,
    '42501', NULL, 'OWNER sin MFA no cancela una orden en revisión de pago');
SELECT lives_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'C2') $$,
    'OWNER sin MFA sí cancela antes del pago (mismo alcance que STAFF)');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');   -- OWNER con MFA
SELECT lives_ok($$ UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT id FROM ord WHERE k = 'R1') $$,
    'OWNER con MFA cancela una orden en revisión de pago');
SELECT lives_ok($$ UPDATE public.orders SET status = 'REFUNDED' WHERE id = (SELECT id FROM ord WHERE k = 'C1') $$,
    'OWNER con MFA reembolsa una orden confirmada');
SELECT tests.reset();

-- =================================================================================
-- M3 · FSM de evidencias en la BD (docs/architecture/uml.md §7.1)
-- =================================================================================
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
UPDATE public.orders SET status = 'PAYMENT_REVIEW' WHERE id = (SELECT id FROM ord WHERE k = 'R3');
SELECT throws_like($$ INSERT INTO public.payment_evidence (organization_id, order_id, customer_id, channel, sha256, status)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R3'),
            'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('9', 64), 'APPROVED') $$,
    '%estado inicial%', 'el backend no crea evidencias ya aprobadas');
SELECT throws_ok($$ INSERT INTO public.payment_evidence (organization_id, order_id, customer_id, channel, sha256,
            reviewed_by, reviewed_at)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT id FROM ord WHERE k = 'R3'),
            'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('9', 64),
            'a1000000-0000-4000-8000-000000000001', now()) $$,
    '42501', NULL, 'el backend no firma revisiones al crear la evidencia');
SELECT lives_ok($$ INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256)
    VALUES ('ee000000-0000-4000-8000-000000000084', 'a0000000-0000-4000-8000-000000000000',
            (SELECT id FROM ord WHERE k = 'R3'), 'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT',
            repeat('4', 64)) $$,
    'el backend registra la evidencia recibida (RECEIVED)');
SELECT throws_like($$ UPDATE public.payment_evidence SET status = 'APPROVED'
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%no permitida%', 'RECEIVED -> APPROVED no existe');
SELECT lives_ok($$ UPDATE public.payment_evidence
                      SET status = 'OCR_DONE', bank_code = 'BNB', txn_number_normalized = '808004',
                          amount_bs = 29.14, ocr_confidence = 0.950
                    WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    'el backend registra el resultado del OCR (RECEIVED -> OCR_DONE)');
SELECT throws_like($$ UPDATE public.payment_evidence SET status = 'REJECTED_FRAUD'
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%no permitida%', 'un rechazo por fraude lo decide una persona');
SELECT lives_ok($$ UPDATE public.payment_evidence SET status = 'AUTO_CHECK_PASSED', checks = '{"amount": true}'
                    WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    'el backend deja la evidencia lista para revisión (OCR_DONE -> AUTO_CHECK_PASSED)');
SELECT throws_like($$ UPDATE public.payment_evidence SET amount_bs = 9999
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%congelados%', 'en revisión, los datos de la evidencia ya no cambian (la persona aprueba lo que vio)');
SELECT throws_ok($$ UPDATE public.payment_evidence SET reviewed_by = 'a1000000-0000-4000-8000-000000000001'
                    WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '42501', NULL, 'el backend no escribe reviewed_by');
SELECT throws_like($$ UPDATE public.payment_evidence SET status = 'APPROVED'
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%no permitida%', 'el agente (BOT) no aprueba evidencias');
SELECT tests.reset();

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_like($$ UPDATE public.payment_evidence SET status = 'APPROVED'
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%no permitida%', 'el worker (SYSTEM) tampoco aprueba evidencias: solo la RPC con MFA');
SELECT lives_ok($$ INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256)
    VALUES ('ee000000-0000-4000-8000-000000000085', 'a0000000-0000-4000-8000-000000000000',
            (SELECT id FROM ord WHERE k = 'R3'), 'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT',
            repeat('5', 64)) $$,
    'el worker registra otra evidencia recibida');
SELECT lives_ok($$ UPDATE public.payment_evidence
                      SET status = 'REJECTED_DUPLICATE', duplicate_of = 'ee000000-0000-4000-8000-000000000084'
                    WHERE id = 'ee000000-0000-4000-8000-000000000085' $$,
    'el worker marca duplicados (RECEIVED -> REJECTED_DUPLICATE)');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT is(public.approve_payment_evidence('ee000000-0000-4000-8000-000000000084')::text, 'CONFIRMED',
          'la aprobación real sigue funcionando por la RPC con MFA');
SELECT tests.reset();

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_like($$ UPDATE public.payment_evidence SET status = 'NEEDS_REVIEW'
                      WHERE id = 'ee000000-0000-4000-8000-000000000084' $$,
    '%estado final%', 'una evidencia aprobada es final');
SELECT tests.reset();

SELECT * FROM finish();
ROLLBACK;
