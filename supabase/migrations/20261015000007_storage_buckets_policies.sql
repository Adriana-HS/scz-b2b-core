-- ============================================================================
-- SCZ-B2B-CORE · Migración 0007 · Buckets privados y políticas de Storage
-- Convención de rutas: <organization_id>/<yyyy>/<mm>/<uuid>.<ext>
-- El primer segmento de la ruta ES el tenant: la política lo compara con las
-- membresías del usuario. Ningún bucket es público.
-- ============================================================================

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types) VALUES
    ('qr-receipts', 'qr-receipts', false, 5242880,  ARRAY['image/jpeg', 'image/png', 'image/webp']),
    ('quotes-pdf',  'quotes-pdf',  false, 10485760, ARRAY['application/pdf']),
    ('invoices',    'invoices',    false, 10485760, ARRAY['application/pdf', 'application/xml', 'text/xml']),
    ('chat-media',  'chat-media',  false, 16777216, ARRAY['image/jpeg', 'image/png', 'image/webp',
                                                          'audio/ogg', 'audio/mpeg', 'application/pdf'])
ON CONFLICT (id) DO UPDATE
    SET public = false,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

-- Lectura desde el dashboard: solo archivos del propio tenant (URLs firmadas de corta vida)
CREATE POLICY "tenant members read own files" ON storage.objects
    FOR SELECT TO authenticated
    USING (
        bucket_id IN ('qr-receipts', 'quotes-pdf', 'invoices', 'chat-media')
        AND (storage.foldername(name))[1] IN (SELECT id::text FROM private.user_org_ids() AS id)
    );

-- Sin políticas de INSERT/UPDATE/DELETE para usuarios: los archivos los escribe
-- únicamente el StorageAdapter del backend (ver SYSTEM_DESIGN.md §8.4).
