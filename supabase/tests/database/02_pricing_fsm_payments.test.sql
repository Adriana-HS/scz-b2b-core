-- ============================================================================
-- 02 · Determinismo financiero, máquina de estados y antifraude de pagos
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(24);

-- --- Cotización atómica por el agente (actor BOT) ------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q1 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', 'c0000000-0000-4000-8000-00000000000a',
    '[{"item_id":"ca000000-0000-4000-8000-000000000001","quantity":2},
      {"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
-- 2 x round(25.00 * 11.68, 2) = 2 x 292.00 = 584.00 ; + 480.00 (precio en Bs) = 1064.00
SELECT is((SELECT total_bs FROM q1), 1064.00::numeric(12,2), 'total = 2×(25 USD×11.68) + 480 Bs, calculado en BD');
SELECT is((SELECT deposit_required_bs FROM q1), 532.00::numeric(12,2), 'seña 50% calculada por el motor');
SELECT is((SELECT applied_fx_rate FROM q1), 11.6800::numeric(10,4), 'TC aplicado congelado en la orden');
SELECT matches((SELECT reference_code FROM q1), '^COT-[0-9]{4}-[0-9]{6}$', 'código de referencia correlativo legible');
SELECT is((SELECT status::text FROM public.orders WHERE id = (SELECT order_id FROM q1)), 'QUOTED', 'orden queda en QUOTED');

-- Entradas maliciosas o inválidas del LLM
SELECT throws_like($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"cb000000-0000-4000-8000-000000000001","quantity":1}]') $$,
    '%inexistente%', 'no se puede cotizar un ítem de otro tenant');
SELECT throws_like($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000001","quantity":2}]') $$,
    '%Stock insuficiente%', 'reservas activas evitan sobreventa (3 en stock, 2 ya reservados)');
SELECT lives_ok($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1,"unit_price_bs":0.01}]') $$,
    'un "unit_price_bs" inyectado por el LLM se ignora (prompt injection financiero)');
SELECT is((SELECT unit_price_bs FROM public.order_items WHERE catalog_item_id = 'ca000000-0000-4000-8000-000000000002'),
          58.28::numeric(12,2), 'el precio sale del catálogo: round(4.99×11.68) = 58.28');
SELECT throws_like($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":0}]') $$,
    '%Cantidad%', 'cantidad 0 rechazada');

-- --- Precio congelado y FSM ----------------------------------------------------
SELECT throws_like($$ UPDATE public.orders SET total_bs = 1 WHERE id = (SELECT order_id FROM q1) $$,
    '%congelada%', 'los montos no cambian después de DRAFT');
SELECT throws_like($$ UPDATE public.orders SET status = 'CONFIRMED' WHERE id = (SELECT order_id FROM q1) $$,
    '%no permitida%', 'el agente IA no puede confirmar una orden');
SELECT throws_like($$ UPDATE public.orders SET amount_paid_bs = 9999 WHERE id = (SELECT order_id FROM q1) $$,
    '%libro de pagos%', 'nadie edita amount_paid_bs a mano');
SELECT throws_ok($$ INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, confirmed_via)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT order_id FROM q1), 'QR_STATIC_RECEIPT', 532, 'BANK_API') $$,
    '42501', NULL, 'el agente IA no puede registrar pagos');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT order_id FROM q1);
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id = (SELECT order_id FROM q1);

-- --- Evidencias de pago: doble candado ------------------------------------------
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
VALUES ('ee000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000000', (SELECT order_id FROM q1),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('a', 64), 'BNB', '778899', 532.00,
        'AUTO_CHECK_PASSED');
SELECT throws_ok($$ INSERT INTO public.payment_evidence (organization_id, order_id, customer_id, channel, sha256)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT order_id FROM q1), 'cc000000-0000-4000-8000-00000000000a',
            'QR_STATIC_RECEIPT', repeat('a', 64)) $$,
    '23505', NULL, 'candado 1: la misma imagen no respalda dos evidencias vigentes');
SELECT lives_ok($$ INSERT INTO public.payment_evidence (organization_id, order_id, customer_id, channel, sha256, status,
            duplicate_of)
    VALUES ('a0000000-0000-4000-8000-000000000000', (SELECT order_id FROM q1), 'cc000000-0000-4000-8000-00000000000a',
            'QR_STATIC_RECEIPT', repeat('a', 64), 'REJECTED_DUPLICATE', 'ee000000-0000-4000-8000-000000000001') $$,
    'el intento duplicado SÍ queda registrado como REJECTED_DUPLICATE (trazabilidad)');
SELECT tests.reset();

-- Mismo comprobante bancario usado en OTRO negocio
INSERT INTO public.orders (id, organization_id, customer_id, kind, reference_code, resource_id, slot)
VALUES ('0b000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000000',
        'cc000000-0000-4000-8000-00000000000b', 'APPOINTMENT', 'CITA-X', 'eb000000-0000-4000-8000-000000000001',
        tstzrange(now() + interval '2 days', now() + interval '2 days 30 minutes', '[)'));
SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_ok($$ INSERT INTO public.payment_evidence (organization_id, order_id, customer_id, channel, sha256,
            bank_code, txn_number_normalized, amount_bs, status)
    VALUES ('b0000000-0000-4000-8000-000000000000', '0b000000-0000-4000-8000-000000000001',
            'cc000000-0000-4000-8000-00000000000b', 'QR_STATIC_RECEIPT', repeat('b', 64), 'BNB', '778899', 532.00,
            'NEEDS_REVIEW') $$,
    '23505', NULL, 'candado 2: la misma transacción bancaria no se reutiliza en otro tenant');
SELECT is((SELECT count(*)::int FROM public.payment_evidence), 0,
          '…y el tenant B sigue sin poder ver la evidencia de A');
SELECT tests.reset();

-- --- Aprobación humana -----------------------------------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal2');   -- STAFF
SELECT throws_like($$ SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000001') $$,
    '%no encontrada%', 'STAFF no aprueba pagos');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');   -- OWNER sin MFA
SELECT throws_like($$ SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000001') $$,
    '%MFA%', 'OWNER sin MFA no aprueba pagos');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');   -- OWNER con MFA
SELECT is(public.approve_payment_evidence('ee000000-0000-4000-8000-000000000001')::text, 'CONFIRMED',
          'OWNER con MFA aprueba: la orden pasa a CONFIRMED por el libro de pagos');
SELECT tests.reset();
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000001'), 1,
          'al confirmar, la reserva descuenta stock (3 - 2 = 1)');
SELECT ok((SELECT count(*) > 0 FROM private.audit_log
           WHERE table_name = 'public.payments' AND actor_kind = 'HUMAN'
             AND actor_id = 'a1000000-0000-4000-8000-000000000001'),
          'la bitácora registra quién aprobó el pago');
SELECT throws_ok($$ UPDATE public.payments SET amount_bs = 1 $$, '42501', NULL, 'payments es append-only');

SELECT * FROM finish();
ROLLBACK;
