-- ============================================================================
-- Seed: importadora de repuestos de ejemplo (#27). Datos SINTÉTICOS.
--   * Sin marcas: la compatibilidad se describe por tipo de vehículo y motor.
--   * Sin clientes ni datos personales. Número de WhatsApp y token ficticios.
--   * Tipo de cambio inicial explícito (no existe valor por defecto): 11,68 Bs/USD, el
--     TCO del 15-oct-2026, cargado como REPOSICION_MANUAL. Vence a las 24 h
--     (fx_max_age_hours): para cotizar en USD otro día, cargar el TC del día.
-- Lo carga `supabase db start` / `supabase db reset`. Lo verifica 17_seeds.test.sql.
-- ============================================================================

INSERT INTO public.organizations (id, name, vertical, deposit_percent, quote_validity_hours, config)
VALUES ('5eed0000-0000-4000-8000-00000000000a', 'Importadora de Ejemplo', 'IMPORTADORA_REPUESTOS', 50, 24, '{}');

INSERT INTO public.whatsapp_channels (organization_id, phone_number_id, waba_id, display_phone, access_token_ref)
VALUES ('5eed0000-0000-4000-8000-00000000000a', 'SEED-IMP-0001', 'SEED-WABA-IMP', '+59100009001',
        'WA_TOKEN_SEED_IMPORTADORA');

INSERT INTO public.exchange_rates (organization_id, rate_bs_per_usd, source, effective_at, note)
VALUES ('5eed0000-0000-4000-8000-00000000000a', 11.6800, 'REPOSICION_MANUAL', now(),
        'Valor de ejemplo para desarrollo (TCO del 15-oct-2026). Cargar el del día para cotizar en USD.');

INSERT INTO public.catalog_items
    (organization_id, item_type, sku, name, description, category, price_currency, list_price, stock_on_hand, attributes)
SELECT '5eed0000-0000-4000-8000-00000000000a', 'PRODUCT', sku, name, description, category,
       currency::public.price_currency, price, stock, jsonb_build_object('compatibilidad', compat)
FROM (VALUES
    -- Frenos
    ('FRE-001', 'Pastilla de freno delantera cerámica', 'Juego de 4 pastillas, bajo polvo', 'Frenos', 'USD', 28.50, 12, 'Camioneta 4x4 diésel 2.4 (2015-2022)'),
    ('FRE-002', 'Pastilla de freno trasera semimetálica', 'Juego de 4 pastillas', 'Frenos', 'USD', 22.00, 8, 'Camioneta 4x4 diésel 2.4 (2015-2022)'),
    ('FRE-003', 'Disco de freno delantero ventilado', 'Unidad, 296 mm', 'Frenos', 'USD', 41.00, 6, 'Camioneta 4x4 diésel 2.4 (2015-2022)'),
    ('FRE-004', 'Pastilla de freno delantera para sedán', 'Juego de 4 pastillas', 'Frenos', 'BOB', 180.00, 20, 'Sedán 1.6 gasolina (2012-2020)'),
    ('FRE-005', 'Líquido de frenos DOT 4', 'Botella de 500 ml', 'Frenos', 'BOB', 45.00, 40, 'Universal'),
    -- Filtros
    ('FIL-001', 'Filtro de aceite roscado', 'Rosca 3/4"-16', 'Filtros', 'BOB', 38.00, 60, 'Camioneta 4x4 diésel 2.4'),
    ('FIL-002', 'Filtro de aire de motor', 'Panel de papel', 'Filtros', 'BOB', 75.00, 25, 'Camioneta 4x4 diésel 2.4'),
    ('FIL-003', 'Filtro de combustible diésel', 'Con separador de agua', 'Filtros', 'USD', 14.90, 18, 'Camioneta 4x4 diésel 2.4'),
    ('FIL-004', 'Filtro de cabina con carbón activado', 'Retiene olores y polvo', 'Filtros', 'BOB', 65.00, 15, 'Sedán 1.6 gasolina'),
    ('FIL-005', 'Filtro de aceite para minibús', 'Cartucho', 'Filtros', 'BOB', 55.00, 0, 'Minibús 2.7 gasolina'),
    -- Suspensión
    ('SUS-001', 'Amortiguador trasero a gas', 'Unidad', 'Suspensión', 'BOB', 480.00, 10, 'Camioneta 4x4 diésel 2.4'),
    ('SUS-002', 'Amortiguador delantero a gas', 'Unidad', 'Suspensión', 'BOB', 520.00, 7, 'Camioneta 4x4 diésel 2.4'),
    ('SUS-003', 'Rótula inferior de suspensión', 'Unidad, con grasera', 'Suspensión', 'USD', 18.75, 14, 'Camioneta 4x4 diésel 2.4'),
    ('SUS-004', 'Bieleta de barra estabilizadora', 'Par', 'Suspensión', 'BOB', 140.00, 9, 'Sedán 1.6 gasolina'),
    ('SUS-005', 'Buje de paquete de resortes', 'Juego de 4', 'Suspensión', 'BOB', 95.00, 16, 'Camioneta 4x4 diésel 2.4'),
    -- Motor
    ('MOT-001', 'Correa de distribución', 'Con 123 dientes', 'Motor', 'USD', 35.00, 5, 'Sedán 1.6 gasolina'),
    ('MOT-002', 'Kit de distribución con tensor', 'Correa, tensor y polea', 'Motor', 'USD', 89.00, 3, 'Sedán 1.6 gasolina'),
    ('MOT-003', 'Bujía de iridio', 'Unidad', 'Motor', 'BOB', 62.00, 48, 'Sedán 1.6 gasolina'),
    ('MOT-004', 'Junta de tapa de válvulas', 'Goma', 'Motor', 'BOB', 110.00, 6, 'Camioneta 4x4 diésel 2.4'),
    -- Embrague
    ('EMB-001', 'Kit de embrague completo', 'Disco, prensa y collarín', 'Embrague', 'USD', 165.00, 2, 'Camioneta 4x4 diésel 2.4'),
    ('EMB-002', 'Cilindro maestro de embrague', 'Unidad', 'Embrague', 'USD', 42.00, 4, 'Camioneta 4x4 diésel 2.4'),
    -- Refrigeración
    ('REF-001', 'Bomba de agua', 'Con empaque', 'Refrigeración', 'USD', 54.00, 5, 'Camioneta 4x4 diésel 2.4'),
    ('REF-002', 'Termostato de motor', '82 °C', 'Refrigeración', 'BOB', 120.00, 11, 'Sedán 1.6 gasolina'),
    ('REF-003', 'Refrigerante concentrado', 'Galón de 3,78 l', 'Refrigeración', 'BOB', 85.00, 30, 'Universal'),
    -- Eléctrico
    ('ELE-001', 'Alternador remanufacturado', '12 V, 90 A', 'Eléctrico', 'USD', 145.00, 2, 'Camioneta 4x4 diésel 2.4'),
    ('ELE-002', 'Motor de arranque remanufacturado', '12 V', 'Eléctrico', 'USD', 130.00, 1, 'Camioneta 4x4 diésel 2.4'),
    ('ELE-003', 'Foco halógeno H4', 'Par, 60/55 W', 'Eléctrico', 'BOB', 70.00, 35, 'Universal'),
    -- Transmisión
    ('TRA-001', 'Junta homocinética exterior', 'Unidad con fuelle', 'Transmisión', 'USD', 48.00, 6, 'Sedán 1.6 gasolina'),
    ('TRA-002', 'Fuelle de junta homocinética', 'Con abrazaderas', 'Transmisión', 'BOB', 60.00, 22, 'Universal'),
    ('TRA-003', 'Cruceta de cardán', 'Unidad', 'Transmisión', 'BOB', 150.00, 8, 'Camioneta 4x4 diésel 2.4')
) AS items(sku, name, description, category, currency, price, stock, compat);
