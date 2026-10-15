\set QUIET on
\pset footer off
-- datos de prueba: dos tenants
INSERT INTO auth.users VALUES ('aaaaaaaa-0000-0000-0000-000000000001','owner_a@x'),('aaaaaaaa-0000-0000-0000-000000000002','auditor_a@x'),('bbbbbbbb-0000-0000-0000-000000000001','owner_b@x');
INSERT INTO organizations(id,name,vertical,whatsapp_phone_number_id) VALUES
 ('a0000000-0000-0000-0000-000000000000','Importadora A','IMPORTADORA_REPUESTOS','111'),
 ('b0000000-0000-0000-0000-000000000000','Clinica B','CLINICA_CONSULTORIO','222');
INSERT INTO profiles VALUES ('aaaaaaaa-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000000','Owner A','OWNER',now()),
 ('aaaaaaaa-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000000','Auditor A','AUDITOR',now()),
 ('bbbbbbbb-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000000','Owner B','OWNER',now());
INSERT INTO catalog_items(id,organization_id,sku_or_code,name,base_price_usd,embedding) VALUES
 ('c0000000-0000-0000-0000-00000000000a','a0000000-0000-0000-0000-000000000000','OEM-4451','Pastilla de freno delantera Hilux',25,array_fill(0.1::real,ARRAY[1536])::vector),
 ('c0000000-0000-0000-0000-00000000000b','a0000000-0000-0000-0000-000000000000','FIL-001','Filtro de aceite (SIN EMBEDDING)',5,NULL),
 ('c0000000-0000-0000-0000-0000000000b1','b0000000-0000-0000-0000-000000000000','LIMP-01','Limpieza dental',NULL,NULL);
INSERT INTO customers(id,organization_id,whatsapp_wa_id) VALUES
 ('d0000000-0000-0000-0000-00000000000a','a0000000-0000-0000-0000-000000000000','59170000001'),
 ('d0000000-0000-0000-0000-0000000000b1','b0000000-0000-0000-0000-000000000000','59170000002');
INSERT INTO processed_webhook_messages VALUES ('wamid.SECRETO','b0000000-0000-0000-0000-000000000000',now());

\echo '--- H1: rol anon (clave pública) lee processed_webhook_messages (sin RLS) ---'
SET ROLE anon; SELECT * FROM processed_webhook_messages; RESET ROLE;

\echo '--- H2: OWNER A no puede leer su propio perfil ni order_items (RLS sin políticas) ---'
SET ROLE authenticated; SELECT set_config('request.jwt.claims','{"sub":"aaaaaaaa-0000-0000-0000-000000000001"}',false);
SELECT count(*) AS perfiles_visibles FROM profiles;
RESET ROLE;

\echo '--- H3: AUDITOR (solo lectura en teoría) modifica el tipo de cambio de la empresa ---'
SET ROLE authenticated; SELECT set_config('request.jwt.claims','{"sub":"aaaaaaaa-0000-0000-0000-000000000002"}',false);
UPDATE organizations SET exchange_rate_bs = 1.00 RETURNING name, exchange_rate_bs;
RESET ROLE;

\echo '--- H4: integridad cross-tenant: orden del tenant A con cliente del tenant B y item de B ---'
INSERT INTO orders_appointments(id,organization_id,customer_id,reference_code,applied_exchange_rate_bs)
VALUES ('e0000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000000','d0000000-0000-0000-0000-0000000000b1','COT-1',6.96) RETURNING 'insert aceptado' AS resultado;
INSERT INTO order_items(order_id,catalog_item_id,quantity,unit_price_bs,subtotal_bs)
VALUES ('e0000000-0000-0000-0000-000000000001','c0000000-0000-0000-0000-0000000000b1',2,100,999) RETURNING 'item de B aceptado, subtotal 999 != 2*100' AS resultado;

\echo '--- H5: REJECTED_DUPLICATE es inalcanzable: el UNIQUE impide registrar el intento duplicado ---'
INSERT INTO qr_payments(organization_id,order_id,image_storage_path,image_sha256_hash) VALUES ('a0000000-0000-0000-0000-000000000000','e0000000-0000-0000-0000-000000000001','p1','abc');
\set ON_ERROR_STOP off
INSERT INTO qr_payments(organization_id,order_id,image_storage_path,image_sha256_hash,verification_status) VALUES ('a0000000-0000-0000-0000-000000000000','e0000000-0000-0000-0000-000000000001','p2','abc','REJECTED_DUPLICATE');

\echo '--- H6: el estado FLAGGED_MANUAL / PRE_VERIFIED del diagrama no existe en el ENUM ---'
UPDATE qr_payments SET verification_status='FLAGGED_MANUAL';

\echo '--- H7: búsqueda: item SIN embedding sale primero (NULLs FIRST en ORDER BY DESC) ---'
SELECT sku_or_code, similarity_score FROM search_catalog_for_tenant('a0000000-0000-0000-0000-000000000000','pastilla freno hilux',array_fill(0.1::real,ARRAY[1536])::vector,5);

\echo '--- H8: comodín LIKE: la consulta "%" da score 1.0 a todo el catálogo ---'
SELECT sku_or_code, similarity_score FROM search_catalog_for_tenant('a0000000-0000-0000-0000-000000000000','%',array_fill(0.1::real,ARRAY[1536])::vector,5);

\echo '--- H9: borrar una orden borra en cascada la evidencia de pago (auditoría) ---'
SELECT count(*) AS pagos_antes FROM qr_payments;
DELETE FROM orders_appointments WHERE id='e0000000-0000-0000-0000-000000000001';
SELECT count(*) AS pagos_despues FROM qr_payments;

\echo '--- H10: doble reserva: dos citas solapadas en el mismo horario son aceptadas ---'
INSERT INTO orders_appointments(organization_id,customer_id,reference_code,applied_exchange_rate_bs,status,scheduled_start_at,scheduled_end_at) VALUES
 ('b0000000-0000-0000-0000-000000000000','d0000000-0000-0000-0000-0000000000b1','CITA-1',6.96,'CONFIRMED','2026-10-20 09:00-04','2026-10-20 09:30-04'),
 ('b0000000-0000-0000-0000-000000000000','d0000000-0000-0000-0000-0000000000b1','CITA-2',6.96,'CONFIRMED','2026-10-20 09:15-04','2026-10-20 09:45-04') RETURNING reference_code, 'aceptada' AS r;

\echo '--- H11: updated_at nunca se actualiza (sin trigger) ---'
UPDATE organizations SET name=name||'' WHERE id='a0000000-0000-0000-0000-000000000000' RETURNING created_at = updated_at AS updated_at_sin_cambio;

\echo '--- H12: tipo de cambio por defecto 6.96 vs TCO BCB 15/10/2026 = 11.68 ---'
SELECT column_default FROM information_schema.columns WHERE table_name='organizations' AND column_name='exchange_rate_bs';

\echo '--- H13: función SECURITY DEFINER sin search_path fijo ---'
SELECT proname, prosecdef, proconfig FROM pg_proc WHERE proname='get_user_organization_id';
