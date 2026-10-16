-- ============================================================================
-- 05 · Reglas de esquema que deben cumplirse siempre (guardas de regresión)
-- Basadas en supabase/agent-skills (supabase-postgres-best-practices) y ADR-0002.
-- Si una migración nueva rompe una regla, este archivo falla y dice qué objeto es.
-- ============================================================================
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SET search_path = public, extensions;
SELECT plan(5);

-- 1. Toda FK tiene un índice cuyo prefijo son exactamente sus columnas
SELECT is_empty($$
    SELECT c.conrelid::regclass::text || '.' || c.conname
      FROM pg_constraint c
     WHERE c.contype = 'f'
       AND c.connamespace::regnamespace::text IN ('public', 'private')
       AND NOT EXISTS (
           SELECT 1 FROM pg_index i
            WHERE i.indrelid = c.conrelid
              AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] @> c.conkey
              AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] <@ c.conkey)
$$, 'toda clave foránea tiene un índice que la cubre');

-- 2. RLS activado en todas las tablas de public y private
SELECT is_empty($$
    SELECT c.oid::regclass::text FROM pg_class c
     WHERE c.relkind IN ('r', 'p')
       AND c.relnamespace::regnamespace::text IN ('public', 'private')
       AND NOT c.relrowsecurity
$$, 'RLS activado en todas las tablas de public y private');

-- 3. Toda función SECURITY DEFINER fija search_path
SELECT is_empty($$
    SELECT p.oid::regprocedure::text FROM pg_proc p
     WHERE p.prosecdef
       AND p.pronamespace::regnamespace::text IN ('public', 'private')
       AND NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) cfg WHERE cfg LIKE 'search_path=%')
$$, 'toda función SECURITY DEFINER fija search_path');

-- 4. Las vistas expuestas respetan la RLS del que consulta
SELECT is_empty($$
    SELECT c.oid::regclass::text FROM pg_class c
     WHERE c.relkind = 'v' AND c.relnamespace = 'public'::regnamespace
       AND NOT coalesce(c.reloptions @> '{security_invoker=true}', false)
$$, 'las vistas de public usan security_invoker');

-- 5. anon no tiene privilegios sobre ninguna tabla de public (Data API cerrada por defecto)
SELECT is_empty($$
    SELECT table_name::text FROM information_schema.role_table_grants
     WHERE grantee = 'anon' AND table_schema = 'public'
$$, 'anon no tiene privilegios sobre tablas de public');

SELECT * FROM finish();
ROLLBACK;
