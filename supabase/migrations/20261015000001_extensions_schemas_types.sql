-- ============================================================================
-- SCZ-B2B-CORE · Migración 0001 · Extensiones, esquemas, roles y tipos
-- Referencia: SYSTEM_DESIGN.md §5 (Modelo de datos) y ADR-0002 / ADR-0006
-- ============================================================================

-- Extensiones en un esquema dedicado (recomendación Supabase: no en public)
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector     WITH SCHEMA extensions;  -- pgvector >= 0.8
CREATE EXTENSION IF NOT EXISTS pg_trgm    WITH SCHEMA extensions;  -- similitud de SKU / errores de tipeo
CREATE EXTENSION IF NOT EXISTS unaccent   WITH SCHEMA extensions;  -- "pastilla" = "pastílla"
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;  -- EXCLUDE anti doble-reserva

-- Esquema privado: tablas internas y funciones auxiliares.
-- NO se expone por la API REST de Supabase (PostgREST solo expone `public`).
CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC;

-- Rol de aplicación del backend (FastAPI + worker).
-- NO tiene BYPASSRLS: todo acceso queda sujeto a RLS por tenant (ADR-0002).
-- El LOGIN y la contraseña se configuran fuera de las migraciones (secreto):
--   ALTER ROLE app_backend WITH LOGIN PASSWORD '<desde el gestor de secretos>';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_backend') THEN
        CREATE ROLE app_backend NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public, private, extensions TO app_backend;
GRANT USAGE ON SCHEMA private TO authenticated;  -- solo para invocar helpers de RLS

-- Endurecimiento: el rol anónimo no accede a nada del dominio por defecto.
ALTER DEFAULT PRIVILEGES IN SCHEMA public  REVOKE ALL ON TABLES    FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public  REVOKE ALL ON FUNCTIONS FROM anon, PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA private REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA private REVOKE ALL ON FUNCTIONS FROM anon, authenticated, PUBLIC;

-- ----------------------------------------------------------------------------
-- Tipos de dominio (un solo vocabulario para BD, backend y diagramas)
-- ----------------------------------------------------------------------------
CREATE TYPE public.business_vertical AS ENUM (
    'IMPORTADORA_REPUESTOS', 'CLINICA_CONSULTORIO', 'DISTRIBUIDORA_MAYORISTA'
);

CREATE TYPE public.member_role AS ENUM ('OWNER', 'ADMIN', 'STAFF', 'AUDITOR');

CREATE TYPE public.item_type AS ENUM ('PRODUCT', 'SERVICE');

CREATE TYPE public.price_currency AS ENUM ('BOB', 'USD');

CREATE TYPE public.fx_source AS ENUM ('BCB_OFICIAL', 'REPOSICION_MANUAL', 'PROVEEDOR');

-- Régimen de IVA (ADR-0012): hoy IVA incluido en el precio (Ley 843); la Ley 1733
-- (27-may-2026) introduce el IVA "por fuera" desde la fecha que fije su reglamento.
CREATE TYPE public.tax_mode AS ENUM ('IVA_INCLUIDO', 'IVA_ADICIONAL');

CREATE TYPE public.order_kind AS ENUM ('SALE', 'APPOINTMENT');

-- Máquina de estados única; las transiciones válidas por `order_kind`
-- y por actor viven en private.fsm_transitions (ADR-0004).
CREATE TYPE public.order_status AS ENUM (
    'DRAFT',             -- borrador interno
    'QUOTED',            -- cotización emitida (SALE), con stock reservado y vigencia
    'HELD',              -- horario retenido temporalmente (APPOINTMENT)
    'AWAITING_PAYMENT',  -- esperando pago/seña
    'PAYMENT_REVIEW',    -- hay evidencia de pago en revisión
    'CONFIRMED',         -- pago requerido cubierto por un registro en `payments`
    'COMPLETED',         -- entregado / atendido
    'NO_SHOW',           -- paciente no asistió
    'EXPIRED',           -- venció la cotización o la retención de horario
    'CANCELLED',
    'REFUNDED'
);

CREATE TYPE public.payment_channel AS ENUM (
    'QR_DYNAMIC_BANK',   -- QR generado por API bancaria/OpenBCB con confirmación del banco
    'QR_STATIC_RECEIPT', -- QR estático + captura del comprobante (requiere revisión humana)
    'CASH', 'TRANSFER', 'OTHER'
);

CREATE TYPE public.evidence_status AS ENUM (
    'RECEIVED',            -- imagen recibida y hasheada
    'OCR_DONE',            -- extracción estructurada completada
    'AUTO_CHECK_PASSED',   -- reglas deterministas OK -> espera aprobación humana
    'NEEDS_REVIEW',        -- alguna regla falló o confianza baja -> revisión prioritaria
    'APPROVED',            -- aprobado (genera fila en `payments`)
    'REJECTED_DUPLICATE',  -- misma imagen o misma transacción bancaria ya usada
    'REJECTED_MISMATCH',   -- monto / beneficiario / fecha no coinciden
    'REJECTED_FRAUD',
    'REJECTED_OTHER'
);

CREATE TYPE public.payment_confirmation AS ENUM ('BANK_API', 'HUMAN');

CREATE TYPE public.conversation_mode AS ENUM ('BOT', 'HUMAN', 'CLOSED');

CREATE TYPE public.invoice_status AS ENUM ('PENDING', 'ISSUED', 'CONTINGENCY', 'FAILED', 'VOIDED');

-- ----------------------------------------------------------------------------
-- Funciones utilitarias inmutables
-- ----------------------------------------------------------------------------
-- unaccent() no es IMMUTABLE; este envoltorio permite usarlo en columnas generadas.
CREATE OR REPLACE FUNCTION private.f_unaccent(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
SET search_path = ''
AS $$
    SELECT extensions.unaccent('extensions.unaccent'::regdictionary, p_text)
$$;

-- Normaliza códigos (SKU, nro. de transacción): mayúsculas, solo [A-Z0-9], sin ceros a la izquierda
CREATE OR REPLACE FUNCTION private.f_normalize_code(p_text text)
RETURNS text
LANGUAGE sql
IMMUTABLE PARALLEL SAFE STRICT
SET search_path = ''
AS $$
    SELECT nullif(ltrim(upper(regexp_replace(p_text, '[^A-Za-z0-9]', '', 'g')), '0'), '')
$$;

GRANT EXECUTE ON FUNCTION private.f_unaccent(text), private.f_normalize_code(text)
    TO app_backend, authenticated;
