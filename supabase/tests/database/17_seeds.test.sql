-- ============================================================================
-- 17 · Seeds sintéticos por rubro (#27, supabase/seed/)
--   Los cargan `supabase db start` y `supabase db reset`. Se verifica que existan,
--   que sirvan (cotizables, buscables, con agenda) y que no traigan marcas ni datos
--   que parezcan reales.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(13);

-- Ids fijos de los seeds (supabase/seed/README.md)
CREATE TEMP TABLE seed_orgs (id uuid, vertical public.business_vertical);
INSERT INTO seed_orgs VALUES
    ('5eed0000-0000-4000-8000-00000000000a', 'IMPORTADORA_REPUESTOS'),
    ('5eed0000-0000-4000-8000-00000000000b', 'CLINICA_CONSULTORIO');

SELECT is((SELECT count(*)::integer FROM public.organizations o JOIN seed_orgs s USING (id)
            WHERE o.vertical = s.vertical AND o.is_active),
          2, 'existen la importadora y la clínica de ejemplo, con su rubro');

-- --- Importadora --------------------------------------------------------------------------
SELECT cmp_ok((SELECT count(*)::integer FROM public.catalog_items
                WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a'
                  AND item_type = 'PRODUCT' AND is_active AND list_price > 0),
              '>=', 25, 'la importadora tiene al menos 25 repuestos con precio');
SELECT ok((SELECT count(DISTINCT price_currency) = 2 FROM public.catalog_items
            WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a'),
          'hay repuestos en USD y en Bs: el tipo de cambio se usa de verdad');
SELECT cmp_ok((SELECT count(DISTINCT category)::integer FROM public.catalog_items
                WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a'),
              '>=', 5, 'repuestos de al menos 5 categorías (frenos, filtros, suspensión...)');
SELECT ok(EXISTS (SELECT 1 FROM public.catalog_items
                   WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a' AND stock_on_hand = 0),
          'hay al menos un repuesto sin stock, para probar ese caso');
SELECT is((SELECT rate_bs_per_usd FROM public.exchange_rates
            WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a'
            ORDER BY effective_at DESC LIMIT 1),
          11.6800::numeric(10,4), 'la importadora tiene un tipo de cambio inicial explícito (TCO del 15-oct-2026)');
SELECT is((SELECT count(*)::integer FROM public.catalog_items ci
            WHERE ci.organization_id = '5eed0000-0000-4000-8000-00000000000a'
              AND private.unit_price_bs(ci.price_currency, ci.list_price,
                    (SELECT rate FROM private.current_fx(ci.organization_id))) IS NULL),
          0, 'todo repuesto se puede cotizar en Bs con el TC del seed');
SELECT cmp_ok((SELECT count(*)::integer FROM public.catalog_items
                WHERE organization_id = '5eed0000-0000-4000-8000-00000000000a'
                  AND search_text @@ websearch_to_tsquery('spanish', private.f_unaccent('pastilla freno'))),
              '>=', 1, 'la búsqueda de texto encuentra "pastilla freno"');

-- --- Clínica --------------------------------------------------------------------------------
SELECT cmp_ok((SELECT count(*)::integer FROM public.catalog_items
                WHERE organization_id = '5eed0000-0000-4000-8000-00000000000b'
                  AND item_type = 'SERVICE' AND is_active AND duration_minutes IS NOT NULL),
              '>=', 15, 'la clínica tiene al menos 15 servicios con duración');
SELECT is((SELECT count(*)::integer FROM public.resources r
            WHERE r.organization_id = '5eed0000-0000-4000-8000-00000000000b'
              AND NOT EXISTS (SELECT 1 FROM public.business_hours h
                               WHERE h.organization_id = r.organization_id AND h.resource_id = r.id)),
          0, 'cada profesional de la clínica tiene horario');

-- --- Sin datos reales -----------------------------------------------------------------------
SELECT is((SELECT count(*)::integer FROM public.catalog_items ci JOIN seed_orgs s ON s.id = ci.organization_id
            WHERE concat_ws(' ', ci.name, ci.description, ci.category, ci.attributes::text) ~*
                  '\m(toyota|nissan|mitsubishi|hyundai|kia|suzuki|ford|chevrolet|volkswagen|honda|mazda|isuzu|subaru|renault|peugeot|bosch|ngk|denso|brembo|monroe|kyb|castrol|mobil|shell|valvoline|colgate|sensodyne|oral-?b|listerine|invisalign)\M'),
          0, 'ningún ítem de los seeds menciona una marca real');
SELECT is((SELECT count(*)::integer FROM public.whatsapp_channels w JOIN seed_orgs s ON s.id = w.organization_id
            WHERE w.display_phone !~ '^\+5910000\d{4}$' OR w.phone_number_id !~ '^SEED-'),
          0, 'los números de WhatsApp de los seeds son sintéticos');
SELECT is((SELECT count(*)::integer FROM public.customers c JOIN seed_orgs s ON s.id = c.organization_id),
          0, 'los seeds no traen clientes: ningún dato personal');

SELECT * FROM finish();
ROLLBACK;
