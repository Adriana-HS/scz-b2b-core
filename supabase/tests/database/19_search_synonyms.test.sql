-- ============================================================================
-- 19 · Sinónimos por rubro en la búsqueda (#23, ADR-0020, migración 21)
--   * search_synonyms: datos de la plataforma por rubro (como tax_regimes); solo lectura
--   * la consulta se expande con los sinónimos del rubro del negocio, en singular o plural
--   * un rubro no usa los sinónimos de otro; la consulta no puede inyectar operadores
--   * sin embeddings (ADR-0020): no se encolan trabajos EMBED_ITEM
-- Usa los seeds de #27: importadora 5eed…0a y consultorio 5eed…0b.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(16);

-- --- La tabla ------------------------------------------------------------------------------
SELECT ok((SELECT relrowsecurity FROM pg_class WHERE oid = 'public.search_synonyms'::regclass),
          'search_synonyms tiene RLS');
SELECT ok(has_table_privilege('app_backend', 'public.search_synonyms', 'SELECT')
          AND NOT has_table_privilege('app_backend', 'public.search_synonyms', 'INSERT')
          AND NOT has_table_privilege('app_backend', 'public.search_synonyms', 'UPDATE')
          AND NOT has_table_privilege('app_backend', 'public.search_synonyms', 'DELETE'),
          'el backend solo lee los sinónimos: los mantiene la plataforma por migración');
SELECT ok(NOT has_table_privilege('authenticated', 'public.search_synonyms', 'INSERT'),
          'un usuario del panel no puede agregar sinónimos');
SELECT throws_ok($$INSERT INTO public.search_synonyms (vertical, group_key, term)
                   VALUES ('IMPORTADORA_REPUESTOS', 'x', 'Zapata ')$$,
                 '23514', NULL, 'un término se guarda en minúsculas, sin acentos ni espacios de más');

-- --- Expansión de la consulta --------------------------------------------------------------
SELECT ok(private.search_tsquery('IMPORTADORA_REPUESTOS', 'zapatas')::text LIKE '%pastill%',
          '"zapatas" (plural) se expande a pastilla');
SELECT ok(private.search_tsquery('IMPORTADORA_REPUESTOS', 'ZAPATA')::text LIKE '%balat%',
          'sin importar mayúsculas: zapata también es balata');
SELECT ok(private.search_tsquery('CLINICA_CONSULTORIO', 'zapatas')::text NOT LIKE '%pastill%',
          'el consultorio no usa los sinónimos de la importadora');
SELECT is(private.search_tsquery('IMPORTADORA_REPUESTOS', 'de la para'), NULL,
          'solo palabras vacías: sin consulta (no trae todo el catálogo)');
SELECT lives_ok($$SELECT private.search_tsquery('IMPORTADORA_REPUESTOS', 'freno & | ! ( ) : * <-> '' "')$$,
                'los operadores de tsquery en el texto del cliente no rompen ni se interpretan');

-- --- La búsqueda con los seeds -------------------------------------------------------------
SELECT tests.as_backend('5eed0000-0000-4000-8000-00000000000a', 'BOT');
SELECT ok((SELECT sku FROM public.search_catalog('zapatas delanteras') LIMIT 1) IN ('FRE-001', 'FRE-004'),
          'importadora: "zapatas delanteras" encuentra las pastillas delanteras');
SELECT ok((SELECT 'FTS' = ANY(matched_by) FROM public.search_catalog('burro de arranque') LIMIT 1),
          'importadora: "burro de arranque" llega por texto gracias al sinónimo');
SELECT is((SELECT sku FROM public.search_catalog('FRE003') LIMIT 1), 'FRE-003',
          'el código exacto sigue primero');
SELECT tests.as_backend('5eed0000-0000-4000-8000-00000000000b', 'BOT');
SELECT ok((SELECT sku FROM public.search_catalog('una calza') LIMIT 1) IN ('RES-001', 'RES-002'),
          'consultorio: "una calza" encuentra las restauraciones con resina');
SELECT is((SELECT count(*)::int FROM public.search_catalog('zapatas')), 0,
          'consultorio: los sinónimos de repuestos no le traen nada');
SELECT tests.reset();

-- --- Sin embeddings (ADR-0020) -------------------------------------------------------------
INSERT INTO public.catalog_items (organization_id, item_type, sku, name, category, price_currency, list_price,
                                  stock_on_hand)
VALUES ('a0000000-0000-4000-8000-000000000000', 'PRODUCT', 'NEW-001', 'Ítem nuevo', 'Frenos', 'BOB', 1.00, 1);
SELECT is((SELECT count(*)::int FROM private.jobs j
            WHERE j.kind = 'EMBED_ITEM' AND j.payload ->> 'catalog_item_id' =
                  (SELECT id::text FROM public.catalog_items WHERE sku = 'NEW-001')),
          0, 'un ítem nuevo no encola EMBED_ITEM: no hay quien lo procese');
SELECT is((SELECT count(*)::int FROM private.jobs WHERE kind = 'EMBED_ITEM' AND status = 'QUEUED'),
          0, 'no quedan EMBED_ITEM en cola');

SELECT * FROM finish();
ROLLBACK;
