-- ============================================================================
-- 04 · IVA: régimen vigente (Ley 843, IVA incluido) y transición a Ley 1733 (IVA por fuera)
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(11);

-- Función pura de desglose, con los ejemplos publicados para la Ley 1733
SELECT is((SELECT iva_bs FROM private.tax_breakdown('IVA_ADICIONAL', 13, 240.00)), 31.20::numeric(12,2),
          'IVA por fuera: 13 % sobre neto 240,00 = 31,20');
SELECT is((SELECT total_bs FROM private.tax_breakdown('IVA_ADICIONAL', 13, 240.00)), 271.20::numeric(12,2),
          'IVA por fuera: total 271,20');
SELECT is((SELECT iva_bs FROM private.tax_breakdown('IVA_INCLUIDO', 13, 275.86)), 35.86::numeric(12,2),
          'IVA incluido: 13 % de 275,86 = 35,86 (total sin cambio)');

-- Cotización bajo el régimen actual (IVA incluido)
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q_old AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
SELECT is((SELECT total_bs FROM q_old), 480.00::numeric(12,2), 'IVA incluido: el total es el precio de lista');
SELECT is((SELECT iva_bs FROM q_old), 62.40::numeric(12,2), 'IVA incluido: IVA informado = 13 % del total');
SELECT tests.reset();

-- Entra en vigencia el IVA por fuera (lo inserta operación cuando se publique el reglamento)
INSERT INTO public.tax_regimes (mode, iva_rate, effective_from, legal_reference)
VALUES ('IVA_ADICIONAL', 13.00, now() - interval '1 second', 'Ley 1733 (prueba)');

SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE q_new AS
SELECT * FROM public.create_quote('cc000000-0000-4000-8000-00000000000a', NULL,
    '[{"item_id":"ca000000-0000-4000-8000-000000000003","quantity":1}]');
SELECT is((SELECT total_bs FROM q_new), 542.40::numeric(12,2), 'IVA por fuera: 480,00 + 62,40 = 542,40');
SELECT is((SELECT deposit_required_bs FROM q_new), 271.20::numeric(12,2), 'la seña (50 %) se calcula sobre el total con IVA');
SELECT is((SELECT tax_mode::text FROM q_new), 'IVA_ADICIONAL', 'la cotización informa el régimen aplicado');
SELECT is((SELECT total_bs FROM public.orders WHERE id = (SELECT order_id FROM q_old)), 480.00::numeric(12,2),
          'una cotización emitida antes del cambio conserva su régimen (congelado)');
SELECT is((SELECT tax_mode::text FROM public.search_catalog('AMORT-77') LIMIT 1), 'IVA_ADICIONAL',
          'la búsqueda avisa al bot que el precio es "+ IVA"');
SELECT tests.reset();

SELECT tests.as_user('a1000000-0000-4000-8000-000000000001', 'aal2');
SELECT throws_ok($$ INSERT INTO public.tax_regimes (mode, iva_rate, effective_from, legal_reference)
                    VALUES ('IVA_INCLUIDO', 1, now(), 'hack') $$,
                 '42501', NULL, 'ningún tenant puede alterar el régimen tributario global');
SELECT tests.reset();

SELECT * FROM finish();
ROLLBACK;
