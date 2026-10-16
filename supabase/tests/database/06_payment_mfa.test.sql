-- ============================================================================
-- 06 · SEC-01: aprobar pagos exige MFA siempre (sin interruptor por tenant)
-- Regresión de la revisión del 15-oct-2026: un OWNER con sesión aal1 apagaba
-- config.require_mfa_for_payments y luego aprobaba evidencias sin segundo factor.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(11);

-- --- Preparación: orden en revisión con una evidencia lista para aprobar ---------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', 'c0000000-0000-4000-8000-00000000000a',
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
UPDATE public.orders SET status = 'AWAITING_PAYMENT' WHERE id = (SELECT order_id FROM q);
UPDATE public.orders SET status = 'PAYMENT_REVIEW'   WHERE id = (SELECT order_id FROM q);
INSERT INTO public.payment_evidence (id, organization_id, order_id, customer_id, channel, sha256,
        bank_code, txn_number_normalized, amount_bs, status)
VALUES ('ee000000-0000-4000-8000-000000000061', 'a0000000-0000-4000-8000-000000000000', (SELECT order_id FROM q),
        'cc000000-0000-4000-8000-00000000000a', 'QR_STATIC_RECEIPT', repeat('6', 64), 'BNB', '606060', 240.00,
        'AUTO_CHECK_PASSED');
SELECT tests.reset();
GRANT SELECT ON q TO authenticated;

-- --- 1-2. Sin MFA, el dueño no cambia la configuración del negocio ---------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');   -- OWNER sin MFA
UPDATE public.organizations
   SET config = config || '{"require_mfa_for_payments": false}', deposit_percent = 0
 WHERE id = 'a0000000-0000-4000-8000-000000000000';                    -- RLS: 0 filas
SELECT tests.reset();
SELECT ok(NOT ((SELECT config FROM public.organizations WHERE id = 'a0000000-0000-4000-8000-000000000000')
               ? 'require_mfa_for_payments'),
          'OWNER sin MFA no puede escribir la configuración del negocio');
SELECT is((SELECT deposit_percent FROM public.organizations WHERE id = 'a0000000-0000-4000-8000-000000000000'),
          50.00::numeric(5,2), 'OWNER sin MFA no puede cambiar el porcentaje de seña');

-- --- 3. Con MFA sí puede editar la organización -----------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
UPDATE public.organizations SET name = 'Importadora A (editada)'
 WHERE id = 'a0000000-0000-4000-8000-000000000000';
SELECT tests.reset();
SELECT is((SELECT name FROM public.organizations WHERE id = 'a0000000-0000-4000-8000-000000000000'),
          'Importadora A (editada)', 'OWNER con MFA edita los datos de su organización');

-- --- 4. El interruptor ya no existe: la BD rechaza la clave ----------------------
SELECT throws_ok($$ UPDATE public.organizations
                      SET config = config || '{"require_mfa_for_payments": false}'
                    WHERE id = 'a0000000-0000-4000-8000-000000000000' $$,
    '23514', NULL, 'la configuración no acepta require_mfa_for_payments (ni siquiera un administrador de BD)');

-- --- 5. Aunque un dato heredado traiga el interruptor apagado, aal1 no aprueba ----
ALTER TABLE public.organizations DROP CONSTRAINT IF EXISTS organizations_config_no_mfa_override;
UPDATE public.organizations SET config = config || '{"require_mfa_for_payments": false}'
 WHERE id = 'a0000000-0000-4000-8000-000000000000';
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');
SELECT throws_like($$ SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000061') $$,
    '%MFA%', 'aprobar exige aal2 aunque la configuración diga lo contrario');
SELECT tests.reset();

-- --- 6. Camino feliz intacto: OWNER con MFA aprueba ------------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT lives_ok($$ SELECT public.approve_payment_evidence('ee000000-0000-4000-8000-000000000061') $$,
    'OWNER con MFA aprueba la evidencia');
SELECT tests.reset();

-- --- 7-9. Mismo tipo de falla por otros caminos (revisión de seguridad del fix) ---
-- Sin MFA, el dueño no puede crear un aprobador nuevo (cuenta propia del atacante con MFA)
INSERT INTO auth.users (id, email) VALUES ('a1000000-0000-4000-8000-000000000009', 'nuevo.a@example.test');
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal1');
UPDATE public.organization_members SET role = 'ADMIN'
 WHERE organization_id = 'a0000000-0000-4000-8000-000000000000'
   AND user_id = 'a1000000-0000-4000-8000-000000000002';               -- RLS: 0 filas
SELECT throws_ok($$ INSERT INTO public.organization_members (organization_id, user_id, role)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'a1000000-0000-4000-8000-000000000009', 'ADMIN') $$,
    '42501', NULL, 'OWNER sin MFA no puede dar de alta un ADMIN');
SELECT throws_ok($$ INSERT INTO public.exchange_rates (organization_id, rate_bs_per_usd, source, created_by)
    VALUES ('a0000000-0000-4000-8000-000000000000', 1.00, 'REPOSICION_MANUAL', 'a1000000-0000-4000-8000-000000000001') $$,
    '42501', NULL, 'OWNER sin MFA no puede cargar un tipo de cambio');
SELECT tests.reset();
SELECT is((SELECT role::text FROM public.organization_members
            WHERE organization_id = 'a0000000-0000-4000-8000-000000000000'
              AND user_id = 'a1000000-0000-4000-8000-000000000002'),
          'STAFF', 'OWNER sin MFA no puede ascender a STAFF como ADMIN');

-- --- 10-11. Con MFA, las mismas operaciones funcionan -----------------------------
SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT lives_ok($$ INSERT INTO public.organization_members (organization_id, user_id, role)
    VALUES ('a0000000-0000-4000-8000-000000000000', 'a1000000-0000-4000-8000-000000000009', 'STAFF') $$,
    'OWNER con MFA da de alta un miembro');
SELECT lives_ok($$ INSERT INTO public.exchange_rates (organization_id, rate_bs_per_usd, source, created_by)
    VALUES ('a0000000-0000-4000-8000-000000000000', 11.70, 'REPOSICION_MANUAL', 'a1000000-0000-4000-8000-000000000001') $$,
    'OWNER con MFA carga el tipo de cambio');
SELECT tests.reset();

SELECT * FROM finish();
ROLLBACK;
