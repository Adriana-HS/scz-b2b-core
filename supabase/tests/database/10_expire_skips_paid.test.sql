-- ============================================================================
-- 10 · expire_stale_orders no toma las órdenes con pagos registrados
-- Seguimiento de #57: trg_orders_paid_exit_guard (migración 12) omite el paso a
-- EXPIRED de una orden con amount_paid_bs > 0 devolviendo NULL, pero el job seguía
-- seleccionándola (y bloqueando su fila) en cada pasada, cada minuto. El filtro va
-- en el WHERE del job; el trigger queda como defensa en profundidad.
-- Para probar que el filtro vive en el job, el trigger se desactiva dentro de esta
-- transacción (se revierte con el ROLLBACK): sin el filtro, el job vencería P1.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(9);

-- --- Preparación ------------------------------------------------------------------
-- Dos cotizaciones de FIL-0001 × 1, ambas vencidas:
--   P1: QUOTED con un pago parcial de 10 Bs registrado por el sistema
--   U1: QUOTED sin pagos
CREATE TEMP TABLE ord (k text PRIMARY KEY, id uuid NOT NULL);
GRANT SELECT, INSERT ON ord TO app_backend;

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
INSERT INTO ord SELECT 'P1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
INSERT INTO ord SELECT 'U1', order_id FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000002","quantity":1}]');
SELECT tests.reset();

-- Pago parcial (como postgres: app_backend no tiene INSERT en payments; actor SYSTEM explícito)
SELECT set_config('app.actor', 'SYSTEM', true);
INSERT INTO public.payments (organization_id, order_id, channel, amount_bs, bank_code, txn_number, confirmed_via)
SELECT 'a0000000-0000-4000-8000-000000000000', id, 'QR_DYNAMIC_BANK', 10.00, 'BNB', '910001', 'BANK_API'
  FROM ord WHERE k = 'P1';
SELECT tests.reset();

UPDATE public.orders SET valid_until = now() - interval '1 minute' WHERE id IN (SELECT id FROM ord);

SELECT is((SELECT string_agg(o.k || '=' || x.status || ' ' || x.amount_paid_bs, ' ' ORDER BY o.k)
             FROM ord o JOIN public.orders x ON x.id = o.id),
          'P1=QUOTED 10.00 U1=QUOTED 0.00', 'precondición: dos cotizaciones vencidas, una con pagos');

-- =================================================================================
-- La definición del job excluye las órdenes con pagos
-- =================================================================================
SELECT matches(pg_get_functiondef('private.expire_stale_orders()'::regprocedure),
               'amount_paid_bs\s*=\s*0',
               'el WHERE de expire_stale_orders filtra amount_paid_bs = 0');
SELECT is((SELECT prosecdef FROM pg_proc WHERE oid = 'private.expire_stale_orders()'::regprocedure), true,
          'expire_stale_orders sigue siendo SECURITY DEFINER (corre desde pg_cron sin tenant)');
SELECT ok(has_function_privilege('app_backend', 'private.expire_stale_orders()', 'EXECUTE')
          AND NOT has_function_privilege('authenticated', 'private.expire_stale_orders()', 'EXECUTE')
          AND NOT has_function_privilege('anon', 'private.expire_stale_orders()', 'EXECUTE'),
          'CREATE OR REPLACE conserva los privilegios de la migración 06 (solo app_backend ejecuta el job)');

-- =================================================================================
-- Sin el trigger, el filtro del job basta: la orden pagada no se toca
-- =================================================================================
ALTER TABLE public.orders DISABLE TRIGGER trg_orders_paid_exit_guard;
SELECT is(private.expire_stale_orders(), 1, 'con el trigger desactivado el job vence solo la orden sin pagos');
SELECT is((SELECT string_agg(o.k || '=' || x.status, ' ' ORDER BY o.k)
             FROM ord o JOIN public.orders x ON x.id = o.id),
          'P1=QUOTED U1=EXPIRED', 'la orden con pagos sigue viva por el filtro del job, no por el trigger');
SELECT is((SELECT count(*)::int FROM public.stock_reservations r
            WHERE r.order_id = (SELECT id FROM ord WHERE k = 'P1') AND r.released_at IS NULL), 1,
          'y conserva su reserva de stock para la revisión humana');
ALTER TABLE public.orders ENABLE TRIGGER trg_orders_paid_exit_guard;

-- =================================================================================
-- El trigger sigue como defensa en profundidad para cualquier otro UPDATE a EXPIRED
-- =================================================================================
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT lives_ok($$ UPDATE public.orders SET status = 'EXPIRED' WHERE id = (SELECT id FROM ord WHERE k = 'P1') $$,
    'un UPDATE directo a EXPIRED sobre la orden con pagos no falla');
SELECT tests.reset();
SELECT is((SELECT status::text FROM public.orders WHERE id = (SELECT id FROM ord WHERE k = 'P1')),
          'QUOTED', 'y el trigger la omite (0 filas actualizadas)');

SELECT * FROM finish();
ROLLBACK;
