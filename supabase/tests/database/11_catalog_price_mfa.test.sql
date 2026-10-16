-- ============================================================================
-- 11 · Precios del catálogo con regla mixta de MFA (#50, ADR-0016)
-- Cambios normales: STAFF sin MFA. Cambios sensibles (precio 0, cambio de moneda,
-- variación mayor al umbral del negocio, 30 % por defecto): OWNER/ADMIN con aal2.
-- El backend (BOT/SYSTEM) nunca cambia precios. Cada cambio deja un aviso al dueño.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(20);

-- Ítem de prueba: AMORT-77, 480,00 Bs (fixture)
-- --- STAFF sin MFA: cambios normales sí, sensibles no ----------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal1');   -- STAFF
SELECT lives_ok($$ UPDATE public.catalog_items SET list_price = 500.00
                   WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    'STAFF sin MFA cambia un precio dentro del umbral (480 -> 500, +4 %)');
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 0
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'STAFF no deja un precio en 0');
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 750.00
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'STAFF no sube un precio más del umbral (+50 % > 30 %)');
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 300.00
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'STAFF no baja un precio más del umbral (−40 % > 30 %)');
SELECT throws_ok($$ UPDATE public.catalog_items SET price_currency = 'USD'
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'STAFF no cambia la moneda de lista (BOB -> USD multiplica el precio por el TC)');
SELECT throws_ok($$ INSERT INTO public.catalog_items (organization_id, item_type, sku, name,
                        price_currency, list_price, stock_on_hand)
                    VALUES ('a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'GRATIS-1',
                            'Ítem a precio cero', 'BOB', 0, 1) $$,
    '42501', NULL, 'STAFF no da de alta un ítem a precio 0');
SELECT lives_ok($$ INSERT INTO public.catalog_items (organization_id, item_type, sku, name,
                       price_currency, list_price, stock_on_hand)
                   VALUES ('a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'NUEVO-1',
                           'Ítem nuevo', 'BOB', 10.00, 1) $$,
    'STAFF da de alta un ítem con precio');
SELECT tests.reset();

-- --- OWNER: los cambios sensibles exigen MFA --------------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');   -- OWNER sin MFA
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 750.00
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'OWNER sin MFA no hace un cambio sensible');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');   -- OWNER con MFA
SELECT lives_ok($$ UPDATE public.catalog_items SET list_price = 750.00
                   WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    'OWNER con MFA hace el cambio sensible (500 -> 750, +50 %)');
SELECT lives_ok($$ UPDATE public.organizations SET price_change_mfa_threshold_pct = 60
                   WHERE id = 'a0000000-0000-4000-8000-000000000000' $$,
    'OWNER con MFA ajusta el umbral del negocio a 60 %');
SELECT throws_ok($$ UPDATE public.organizations SET price_change_mfa_threshold_pct = 0
                    WHERE id = 'a0000000-0000-4000-8000-000000000000' $$,
    '23514', NULL, 'el umbral no puede ser 0 (rango 1-100)');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000002', 'aal1');   -- STAFF
SELECT lives_ok($$ UPDATE public.catalog_items SET list_price = 1125.00
                   WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    'con umbral 60 %, STAFF sube +50 % sin MFA');
SELECT lives_ok($$ UPDATE public.catalog_items SET name = 'Filtro de aceite Toyota (rosca)'
                   WHERE id = 'ca000000-0000-4000-8000-000000000002' $$,
    'editar otros datos del ítem no pasa por la regla de precios');
SELECT tests.reset();

-- --- Backend: el bot y los jobs nunca cambian precios ------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 1.00
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'el agente IA no cambia precios aunque el backend tenga UPDATE');
SELECT tests.reset();
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'SYSTEM');
SELECT throws_ok($$ UPDATE public.catalog_items SET list_price = 1130.00
                    WHERE id = 'ca000000-0000-4000-8000-000000000003' $$,
    '42501', NULL, 'un job (SYSTEM) tampoco cambia precios, ni dentro del umbral');
SELECT throws_ok($$ INSERT INTO public.catalog_items (organization_id, item_type, sku, name,
                        price_currency, list_price, stock_on_hand)
                    VALUES ('a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'GRATIS-2',
                            'Ítem a precio cero', 'BOB', 0, 1) $$,
    '42501', NULL, 'el backend no da de alta ítems a precio 0');
SELECT tests.reset();

-- --- Resultado, aviso al dueño y auditoría ----------------------------------------
SELECT is((SELECT list_price FROM public.catalog_items WHERE id = 'ca000000-0000-4000-8000-000000000003'),
          1125.00::numeric(12,2), 'solo quedaron aplicados los cambios permitidos');
SELECT is((SELECT count(*)::int FROM private.jobs
            WHERE kind = 'PRICE_CHANGE_ALERT'
              AND payload->>'catalog_item_id' = 'ca000000-0000-4000-8000-000000000003'),
          3, 'cada cambio de precio aplicado deja un aviso al dueño (los rechazados y el cambio de nombre no)');
SELECT ok(EXISTS (SELECT 1 FROM private.jobs
                   WHERE kind = 'PRICE_CHANGE_ALERT'
                     AND payload->>'new_price' = '750.00' AND (payload->>'sensitive')::boolean
                     AND payload->>'changed_by' = 'a1000000-0000-4000-8000-000000000001'),
          'el aviso indica quién hizo el cambio y si fue sensible');
SELECT ok(EXISTS (SELECT 1 FROM private.audit_log
                   WHERE table_name = 'public.catalog_items' AND action = 'UPDATE'
                     AND row_id = 'ca000000-0000-4000-8000-000000000003'
                     AND old_data->>'list_price' = '500.00' AND new_data->>'list_price' = '750.00'
                     AND actor_id = 'a1000000-0000-4000-8000-000000000001'),
          'la bitácora guarda el precio anterior, el nuevo y quién lo cambió');

SELECT * FROM finish();
ROLLBACK;
