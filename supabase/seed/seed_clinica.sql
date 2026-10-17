-- ============================================================================
-- Seed: clínica / consultorio odontológico de ejemplo (#27). Datos SINTÉTICOS.
--   * Sin pacientes ni datos personales. Profesionales con nombres genéricos.
--   * Servicios en Bs con duración; seña del 30 %; turnos cada 30 min.
--   * Horario: lunes a viernes 08:00-18:00 y sábados 08:00-12:00 (weekday 0 = domingo).
-- Lo carga `supabase db start` / `supabase db reset`. Lo verifica 17_seeds.test.sql.
-- ============================================================================

INSERT INTO public.organizations (id, name, vertical, deposit_percent, config)
VALUES ('5eed0000-0000-4000-8000-00000000000b', 'Consultorio de Ejemplo', 'CLINICA_CONSULTORIO', 30,
        '{"slot_step_minutes": 30}');

INSERT INTO public.whatsapp_channels (organization_id, phone_number_id, waba_id, display_phone, access_token_ref)
VALUES ('5eed0000-0000-4000-8000-00000000000b', 'SEED-CLI-0001', 'SEED-WABA-CLI', '+59100009002',
        'WA_TOKEN_SEED_CLINICA');

INSERT INTO public.catalog_items
    (organization_id, item_type, sku, name, description, category, price_currency, list_price, duration_minutes)
SELECT '5eed0000-0000-4000-8000-00000000000b', 'SERVICE', sku, name, description, category, 'BOB', price, minutes
FROM (VALUES
    ('CON-001', 'Consulta odontológica general', 'Revisión y diagnóstico', 'Consultas', 150.00, 30),
    ('CON-002', 'Consulta de urgencia', 'Dolor o traumatismo dental', 'Consultas', 200.00, 30),
    ('CON-003', 'Control de ortodoncia', 'Ajuste mensual de brackets', 'Ortodoncia', 180.00, 30),
    ('PRE-001', 'Limpieza dental (profilaxis)', 'Con ultrasonido y pulido', 'Prevención', 250.00, 30),
    ('PRE-002', 'Limpieza dental profunda', 'Raspado y alisado por cuadrante', 'Prevención', 350.00, 60),
    ('PRE-003', 'Aplicación de flúor', 'Barniz de flúor', 'Prevención', 100.00, 30),
    ('PRE-004', 'Sellantes de fosas y fisuras', 'Por pieza', 'Prevención', 120.00, 30),
    ('RES-001', 'Restauración con resina simple', 'Una superficie', 'Restauraciones', 280.00, 30),
    ('RES-002', 'Restauración con resina compuesta', 'Dos o más superficies', 'Restauraciones', 380.00, 60),
    ('END-001', 'Endodoncia unirradicular', 'Tratamiento de conducto, una raíz', 'Endodoncia', 900.00, 90),
    ('END-002', 'Endodoncia multirradicular', 'Tratamiento de conducto, varias raíces', 'Endodoncia', 1400.00, 120),
    ('CIR-001', 'Extracción simple', 'Pieza erupcionada', 'Cirugía', 300.00, 30),
    ('CIR-002', 'Extracción de tercer molar', 'Pieza retenida o semi-retenida', 'Cirugía', 900.00, 90),
    ('EST-001', 'Blanqueamiento dental en consultorio', 'Una sesión', 'Estética', 1200.00, 90),
    ('RAD-001', 'Radiografía periapical', 'Una toma', 'Diagnóstico por imagen', 60.00, 15),
    ('RAD-002', 'Radiografía panorámica', 'Una toma', 'Diagnóstico por imagen', 200.00, 15)
) AS services(sku, name, description, category, price, minutes);

INSERT INTO public.resources (id, organization_id, name) VALUES
    ('5eed0000-0000-4000-8000-0000000000b1', '5eed0000-0000-4000-8000-00000000000b', 'Dra. Ejemplo Uno'),
    ('5eed0000-0000-4000-8000-0000000000b2', '5eed0000-0000-4000-8000-00000000000b', 'Dr. Ejemplo Dos');

INSERT INTO public.business_hours (organization_id, resource_id, weekday, opens_at, closes_at)
SELECT '5eed0000-0000-4000-8000-00000000000b', r.id, d.weekday, d.opens_at, d.closes_at
FROM (VALUES ('5eed0000-0000-4000-8000-0000000000b1'::uuid), ('5eed0000-0000-4000-8000-0000000000b2'::uuid)) AS r(id)
CROSS JOIN (VALUES
    (1, '08:00'::time, '18:00'::time), (2, '08:00', '18:00'), (3, '08:00', '18:00'),
    (4, '08:00', '18:00'), (5, '08:00', '18:00'), (6, '08:00', '12:00')
) AS d(weekday, opens_at, closes_at);
