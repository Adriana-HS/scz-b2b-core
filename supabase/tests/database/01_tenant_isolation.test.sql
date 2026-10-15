-- ============================================================================
-- 01 · Aislamiento multi-tenant (RLS + FK compuestas + privilegios)
-- Ejecutar: supabase test db   (o psql -f, ver docs/TESTING.md)
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(21);

-- --- anon: nada visible ------------------------------------------------------
SELECT tests.as_anon();
SELECT throws_ok($$ SELECT count(*) FROM public.catalog_items $$, '42501', NULL,
                 'anon no tiene privilegio sobre catalog_items');
SELECT throws_ok($$ SELECT count(*) FROM private.inbound_events $$, '42501', NULL,
                 'anon no accede a tablas internas (esquema private)');
SELECT tests.reset();

-- --- dashboard: OWNER A ------------------------------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001');
SELECT is((SELECT count(*)::int FROM public.catalog_items), 3, 'OWNER A ve solo sus 3 ítems');
SELECT is((SELECT count(*)::int FROM public.catalog_items
           WHERE organization_id = 'b0000000-0000-4000-8000-000000000000'), 0, 'OWNER A no ve el catálogo de B');
SELECT is((SELECT count(*)::int FROM public.organizations), 1, 'OWNER A ve solo su organización');
SELECT is((SELECT count(*)::int FROM public.organization_members), 3,
          'OWNER A ve las 3 membresías de su organización y ninguna de B');
SELECT throws_ok($$ SELECT count(*) FROM private.jobs $$, '42501', NULL,
                 'usuario del dashboard no lee la cola interna');
SELECT throws_ok(
    $$ INSERT INTO public.catalog_items (organization_id, item_type, sku, name, price_currency, list_price, stock_on_hand)
       VALUES ('b0000000-0000-4000-8000-000000000000', 'PRODUCT', 'HACK', 'Inyectado', 'BOB', 1, 1) $$,
    '42501', NULL, 'OWNER A no puede insertar en el catálogo de B');
SELECT tests.reset();

-- --- dashboard: AUDITOR A (solo lectura) ---------------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000003');
SELECT is((SELECT count(*)::int FROM public.catalog_items), 3, 'AUDITOR lee el catálogo de su tenant');
UPDATE public.organizations SET deposit_percent = 0;   -- RLS: 0 filas afectadas
SELECT tests.reset();
SELECT is((SELECT deposit_percent FROM public.organizations WHERE id = 'a0000000-0000-4000-8000-000000000000'),
          50.00::numeric(5,2), 'AUDITOR no logra modificar la organización');
SELECT tests.as_user('a1000000-0000-4000-8000-000000000003');
SELECT throws_ok(
    $$ INSERT INTO public.exchange_rates (organization_id, rate_bs_per_usd, source, created_by)
       VALUES ('a0000000-0000-4000-8000-000000000000', 1.00, 'REPOSICION_MANUAL', 'a1000000-0000-4000-8000-000000000003') $$,
    '42501', NULL, 'AUDITOR no puede cambiar el tipo de cambio');
SELECT tests.reset();

-- --- dashboard: STAFF no toca cuentas bancarias; OWNER sin MFA tampoco ---------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');
SELECT throws_ok(
    $$ INSERT INTO public.bank_accounts (organization_id, bank_code, account_holder, account_last4)
       VALUES ('a0000000-0000-4000-8000-000000000000', 'BNB', 'Atacante', '9999') $$,
    '42501', NULL, 'cambiar cuenta bancaria receptora exige MFA (aal2)');
SELECT tests.reset();
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT lives_ok(
    $$ INSERT INTO public.bank_accounts (organization_id, bank_code, account_holder, account_last4, is_default)
       VALUES ('a0000000-0000-4000-8000-000000000000', 'BNB', 'Importadora Test A', '1234', true) $$,
    'OWNER con MFA registra la cuenta receptora');
SELECT tests.reset();

-- --- backend: falla cerrado sin tenant y aísla con tenant ----------------------
SELECT tests.as_backend(NULL);
SELECT is((SELECT count(*)::int FROM public.catalog_items), 0, 'backend sin app.current_org_id no ve nada');
SELECT tests.reset();
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000');
SELECT is((SELECT count(*)::int FROM public.customers), 1, 'backend con tenant A ve solo clientes de A');
SELECT throws_ok(
    $$ INSERT INTO public.customers (organization_id, wa_id) VALUES ('b0000000-0000-4000-8000-000000000000', '59100000099') $$,
    '42501', NULL, 'backend con tenant A no puede escribir en B');
SELECT is((SELECT organization_id FROM private.resolve_whatsapp_channel('PNID-B')),
          'b0000000-0000-4000-8000-000000000000'::uuid,
          'resolución de canal por phone_number_id funciona antes de fijar tenant');
SELECT tests.reset();

-- --- integridad referencial cross-tenant (aunque el código tenga un bug) ------
SELECT throws_ok(
    $$ INSERT INTO public.orders (organization_id, customer_id, kind, reference_code)
       VALUES ('a0000000-0000-4000-8000-000000000000', 'cc000000-0000-4000-8000-00000000000b', 'SALE', 'X-1') $$,
    '23503', NULL, 'FK compuesta: una orden de A no puede apuntar a un cliente de B');

-- --- sin funciones SECURITY DEFINER con search_path mutable ---------------------
SELECT is((SELECT count(*)::int FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname IN ('public', 'private') AND p.prosecdef
             AND NOT coalesce(p.proconfig::text ILIKE '%search_path=%', false)),
          0, 'toda función SECURITY DEFINER fija search_path');

-- --- Storage: el primer segmento de la ruta es el tenant -----------------------
INSERT INTO storage.objects (bucket_id, name) VALUES
    ('qr-receipts', 'a0000000-0000-4000-8000-000000000000/2026/10/recibo-a.jpg'),
    ('qr-receipts', 'b0000000-0000-4000-8000-000000000000/2026/10/recibo-b.jpg');
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001');
SELECT is((SELECT count(*)::int FROM storage.objects), 1, 'OWNER A solo ve comprobantes de su tenant en Storage');
SELECT tests.reset();

-- --- todas las tablas con RLS ----------------------------------------------------
SELECT is((SELECT count(*)::int FROM pg_tables
           WHERE schemaname IN ('public', 'private') AND NOT rowsecurity),
          0, 'RLS activo en el 100% de las tablas');

SELECT * FROM finish();
ROLLBACK;
