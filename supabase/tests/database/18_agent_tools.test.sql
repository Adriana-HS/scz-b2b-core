-- ============================================================================
-- 18 · Herramientas del agente en la BD (#24, SYSTEM_DESIGN.md §7.2, migración 20)
--   * get_item_details: la ficha de un ítem con precio y stock calculados por la BD,
--     idénticos a los de search_catalog (no pueden divergir); sin atributos internos
--   * request_handoff: pasa la conversación a una persona con motivo y resumen
--   * solo el tenant del backend; solo app_backend ejecuta
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(16);

-- Un ítem con atributos internos y uno inactivo (A)
INSERT INTO public.catalog_items (id, organization_id, item_type, sku, name, category, price_currency,
                                  list_price, stock_on_hand, attributes, is_active) VALUES
    ('ca000000-0000-4000-8000-000000000004', 'a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'SUS-900',
     'Rótula de suspensión', 'Suspensión', 'BOB', 120.00, 5,
     '{"compatibilidad": "Camioneta 4x4 diésel", "costo_proveedor": 41.5, "margen": 0.3}', true),
    ('ca000000-0000-4000-8000-000000000005', 'a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'OLD-001',
     'Ítem dado de baja', 'Frenos', 'BOB', 10.00, 1, '{}', false);

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');

-- --- get_item_details ----------------------------------------------------------------------
SELECT is((SELECT unit_price_bs FROM public.get_item_details('ca000000-0000-4000-8000-000000000001')),
          292.00::numeric(12,2), 'el precio en Bs lo calcula la BD con el TC vigente (25 USD × 11,68)');
SELECT is((SELECT count(*)::integer
             FROM public.catalog_items ci
             CROSS JOIN LATERAL public.get_item_details(ci.id) d
             JOIN LATERAL (SELECT s.unit_price_bs, s.stock_available, s.price_status
                             FROM public.search_catalog(ci.sku, NULL, 20, NULL) s WHERE s.item_id = ci.id) s ON true
            WHERE d.unit_price_bs IS DISTINCT FROM s.unit_price_bs
               OR d.stock_available IS DISTINCT FROM s.stock_available
               OR d.price_status IS DISTINCT FROM s.price_status),
          0, 'precio, stock y estado del precio coinciden con search_catalog en cada ítem activo');
SELECT is((SELECT attributes FROM public.get_item_details('ca000000-0000-4000-8000-000000000004')),
          '{"compatibilidad": "Camioneta 4x4 diésel"}'::jsonb,
          'solo se muestran los atributos públicos: nunca costos ni márgenes');
SELECT is((SELECT count(*)::integer FROM public.get_item_details('ca000000-0000-4000-8000-000000000005')),
          0, 'un ítem dado de baja no se muestra');
SELECT is((SELECT count(*)::integer FROM public.get_item_details('cb000000-0000-4000-8000-000000000001')),
          0, 'un ítem de otro negocio no existe para este tenant');
SELECT is((SELECT count(*)::integer FROM public.get_item_details('ca000000-0000-4000-8000-0000000000ff')),
          0, 'un id inventado devuelve 0 filas');

SELECT public.create_quote('cc000000-0000-4000-8000-00000000000a', 'c0000000-0000-4000-8000-00000000000a',
                           '[{"item_id": "ca000000-0000-4000-8000-000000000001", "quantity": 2}]');
SELECT is((SELECT stock_available FROM public.get_item_details('ca000000-0000-4000-8000-000000000001')),
          1, 'el stock disponible descuenta las reservas activas (3 - 2 cotizados)');

SELECT tests.reset();
UPDATE public.organizations SET fx_max_age_hours = 1 WHERE id = 'a0000000-0000-4000-8000-000000000000';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT ok((SELECT unit_price_bs IS NULL AND price_status = 'FX_STALE'
             FROM public.get_item_details('ca000000-0000-4000-8000-000000000001')),
          'con el TC vencido no hay precio en Bs para un ítem en USD (FX_STALE)');
SELECT is((SELECT unit_price_bs FROM public.get_item_details('ca000000-0000-4000-8000-000000000003')),
          480.00::numeric(12,2), '…pero un ítem en Bs sigue teniendo precio');

SELECT tests.as_backend(NULL, 'BOT');
SELECT throws_ok($$ SELECT * FROM public.get_item_details('ca000000-0000-4000-8000-000000000001') $$,
                 '42501', NULL, 'sin tenant no hay ficha (TENANT_REQUIRED)');

-- --- request_handoff ---------------------------------------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT lives_ok($$ SELECT public.request_handoff('c0000000-0000-4000-8000-00000000000a', 'CUSTOMER_REQUEST',
                                                 'El cliente pide hablar con una persona por un envío.') $$,
                'el bot pasa la conversación a una persona');
SELECT tests.reset();
SELECT ok((SELECT mode = 'HUMAN' AND handoff_reason = 'CUSTOMER_REQUEST' AND handoff_at IS NOT NULL
                  AND handoff_summary LIKE 'El cliente pide%'
             FROM public.conversations WHERE id = 'c0000000-0000-4000-8000-00000000000a'),
          'queda en modo HUMAN con motivo, resumen y hora para el equipo');

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT throws_ok($$ SELECT public.request_handoff('c0000000-0000-4000-8000-00000000000b', 'CUSTOMER_REQUEST', 'x') $$,
                 '42501', NULL, 'no se puede pasar a una persona la conversación de otro negocio (NOT_FOUND)');
SELECT throws_ok($$ SELECT public.request_handoff('c0000000-0000-4000-8000-00000000000a', 'INVENTADO', 'x') $$,
                 '23514', NULL, 'el motivo sale de una lista cerrada');
SELECT throws_ok($$ SELECT public.request_handoff('c0000000-0000-4000-8000-00000000000a', 'OTHER', repeat('x', 1001)) $$,
                 '23514', NULL, 'el resumen tiene un tope de 1000 caracteres');

-- --- Privilegios ---------------------------------------------------------------------------------
SELECT tests.reset();
SELECT ok(has_function_privilege('app_backend', 'public.get_item_details(uuid)', 'EXECUTE')
          AND has_function_privilege('app_backend', 'public.request_handoff(uuid, text, text)', 'EXECUTE')
          AND NOT has_function_privilege('authenticated', 'public.get_item_details(uuid)', 'EXECUTE')
          AND NOT has_function_privilege('anon', 'public.request_handoff(uuid, text, text)', 'EXECUTE'),
          'solo el backend ejecuta las herramientas del agente');

SELECT * FROM finish();
ROLLBACK;
