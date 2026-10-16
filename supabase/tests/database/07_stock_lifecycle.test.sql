-- ============================================================================
-- 07 · Ciclo de vida de las reservas de stock (invariante I-10, Q-ACC-3)
-- Regresión de la revisión del 15-oct-2026:
--   H1. Una orden en PAYMENT_REVIEW con valid_until vencido dejaba de bloquear su
--       stock; otro cliente lo reservaba y al aprobar el pago el stock quedaba < 0.
--   H2. Cancelar o reembolsar una orden no devolvía el stock.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(19);

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

-- Stock inicial del fixture: OEM-4451 = 3, FIL-0001 = 100, AMORT-77 = 10

-- --- H1. Orden en revisión de pago, vencida por tiempo, sigue bloqueando ---------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q1 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', 'c0000000-0000-4000-8000-00000000000a',
    '[{"item_id":"ca000000-0000-4000-8000-000000000001","quantity":2}]');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT order_id FROM q1);
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id = (SELECT order_id FROM q1);
SELECT tests.reset();
GRANT SELECT ON q1 TO authenticated, app_backend;

-- Pasa la vigencia mientras el dueño todavía no revisó el comprobante
UPDATE public.orders SET valid_until = now() - interval '1 minute' WHERE id = (SELECT order_id FROM q1);
UPDATE public.stock_reservations SET expires_at = now() - interval '1 minute'
 WHERE order_id = (SELECT order_id FROM q1);
SELECT is(private.expire_stale_orders(), 0, 'una orden en PAYMENT_REVIEW no vence por tiempo');

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT is((SELECT stock_available FROM public.search_catalog('OEM-4451') WHERE sku = 'OEM-4451'), 1,
          'la búsqueda descuenta la reserva de la orden en revisión aunque valid_until haya pasado (3 - 2)');
SELECT throws_like($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000001","quantity":2}]') $$,
    '%Stock insuficiente%', 'otro cliente no puede reservar el stock de una orden en revisión');
SELECT tests.reset();

-- --- H2. Cancelar libera la reserva ------------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q2 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":10}]');
SELECT throws_like($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]') $$,
    '%Stock insuficiente%', 'con todo el stock reservado no se cotiza ni una unidad más');
UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT order_id FROM q2);   -- el cliente desiste
SELECT ok((SELECT bool_and(released_at IS NOT NULL) FROM public.stock_reservations
            WHERE order_id = (SELECT order_id FROM q2)),
          'cancelar la cotización libera su reserva en el acto');
SELECT is((SELECT stock_available FROM public.search_catalog('AMORT-77') WHERE sku = 'AMORT-77'), 10,
          'el stock liberado vuelve a estar disponible en la búsqueda');
SELECT lives_ok($$ SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":10}]') $$,
    'otro cliente puede cotizar el stock liberado');
SELECT tests.reset();

-- --- H2. Cancelar una orden CONFIRMED devuelve el stock comprometido --------------
CREATE TEMP TABLE q3 AS
SELECT r.order_id FROM public.stock_reservations r
 WHERE r.catalog_item_id = 'ca000000-0000-4000-8000-000000000003'
   AND r.released_at IS NULL AND r.committed_at IS NULL;
GRANT SELECT ON q3 TO authenticated, app_backend;
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT order_id FROM q3);
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id = (SELECT order_id FROM q3);
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
SELECT 'ee000000-0000-4000-8000-000000000071', o.organization_id, o.id, o.customer_id, 'QR_STATIC_RECEIPT',
       repeat('7', 64), 'BNB', '707071', o.deposit_required_bs, 'AUTO_CHECK_PASSED'
  FROM public.orders o WHERE o.id = (SELECT order_id FROM q3);          -- seña: 50 % de 4800 Bs
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');   -- OWNER con MFA
SELECT is(public.approve_payment_evidence('ee000000-0000-4000-8000-000000000071')::text, 'CONFIRMED',
          'la seña aprobada confirma la orden');
SELECT tests.reset();
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000003'), 0,
          'al confirmar, el stock se descuenta (10 - 10)');

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
UPDATE public.orders SET status = 'CANCELLED' WHERE id = (SELECT order_id FROM q3);   -- el negocio anula la venta
SELECT tests.reset();
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000003'), 10,
          'cancelar una orden CONFIRMED (no entregada) devuelve el stock');
SELECT ok((SELECT bool_and((to_jsonb(r) ->> 'returned_at') IS NOT NULL) FROM public.stock_reservations r
            WHERE r.order_id = (SELECT order_id FROM q3)),
          'la reserva comprometida queda marcada como devuelta (returned_at)');

-- --- El vencimiento sigue liberando (ahora por el mismo trigger) -----------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q6 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":10}]');
SELECT tests.reset();
UPDATE public.orders SET valid_until = now() - interval '1 minute' WHERE id = (SELECT order_id FROM q6);
SELECT is(private.expire_stale_orders(), 1, 'el job vence la cotización QUOTED (y no la que está en revisión)');
SELECT ok((SELECT bool_and(released_at IS NOT NULL) FROM public.stock_reservations
            WHERE order_id = (SELECT order_id FROM q6)),
          'la cotización vencida libera su reserva');

-- --- Reembolso y confirmación sin seña --------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q4 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":5}]');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT order_id FROM q4);
SELECT tests.reset();
-- Confirmación bancaria registrada por el sistema. Como postgres y no como app_backend:
-- SEC-02 (#53) le quita a app_backend el INSERT en payments hasta la fase de escala.
SELECT set_config('app.actor', 'SYSTEM', true);
INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, bank_code, txn_number, confirmed_via)
SELECT 'a0000000-0000-4000-8000-000000000000', order_id, 'QR_DYNAMIC_BANK', deposit_required_bs, 'BNB', '707074',
       'BANK_API'
  FROM q4;
SELECT tests.reset();
GRANT SELECT ON q4 TO authenticated;
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000002'), 95,
          'el pago bancario confirma y descuenta stock (100 - 5)');
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
UPDATE public.orders SET status = 'REFUNDED' WHERE id = (SELECT order_id FROM q4);
SELECT tests.reset();
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000002'), 100,
          'reembolsar una orden CONFIRMED devuelve el stock');

UPDATE public.catalog_items SET deposit_percent_override = 0 WHERE id = 'ca000000-0000-4000-8000-000000000002';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q5 AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
SELECT tests.reset();
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
UPDATE public.orders SET status = 'CONFIRMED' WHERE id = (SELECT order_id FROM q5);    -- sin seña requerida
SELECT tests.reset();
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000002'), 99,
          'confirmar sin pago (seña 0) también compromete el stock');

-- --- Conflicto al aprobar: error de negocio estable, no violación de CHECK --------
-- Ajuste de inventario: se rompen dos unidades mientras la orden de q1 está en revisión
UPDATE public.catalog_items SET stock_on_hand = 1 WHERE id = 'ca000000-0000-4000-8000-000000000001';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
SELECT 'ee000000-0000-4000-8000-000000000072', o.organization_id, o.id, o.customer_id, 'QR_STATIC_RECEIPT',
       repeat('8', 64), 'BNB', '707072', o.deposit_required_bs, 'AUTO_CHECK_PASSED'
  FROM public.orders o WHERE o.id = (SELECT order_id FROM q1);          -- seña completa: confirmaría
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT is(tests.hint_of($$ SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000072') $$),
          'STOCK_CONFLICT', 'aprobar sin stock físico falla con HINT STOCK_CONFLICT');
SELECT tests.reset();
SELECT is((SELECT status::text FROM public.orders WHERE id = (SELECT order_id FROM q1)), 'PAYMENT_REVIEW',
          'la orden sigue en revisión: el dueño decide (reponer, reembolsar o cancelar)');
SELECT is((SELECT stock_on_hand FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000001'), 1,
          'el stock no queda negativo ni se descuenta a medias');

SELECT * FROM finish();
ROLLBACK;
