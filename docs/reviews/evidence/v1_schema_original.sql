-- ============================================================================
-- MIGRACIÓN BASE: SCZ-B2B-CORE (PostgreSQL 16 + Supabase + pgvector)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 1. Tipos Enumerados del Dominio
CREATE TYPE business_vertical AS ENUM ('IMPORTADORA_REPUESTOS', 'CLINICA_CONSULTORIO', 'DISTRIBUIDORA_MAYORISTA');
CREATE TYPE transaction_status AS ENUM ('DRAFT', 'QUOTED', 'AWAITING_QR_PAYMENT', 'QR_UNDER_REVIEW', 'CONFIRMED', 'COMPLETED', 'CANCELLED');
CREATE TYPE qr_verification_status AS ENUM ('PENDING_OCR', 'PRE_VERIFIED_AI', 'APPROVED_HUMAN', 'REJECTED_DUPLICATE', 'REJECTED_AMOUNT_MISMATCH');

-- 2. Tabla de Organizaciones (Tenants)
CREATE TABLE public.organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    nit TEXT,
    vertical business_vertical NOT NULL,
    whatsapp_phone_number_id TEXT UNIQUE NOT NULL,
    exchange_rate_bs NUMERIC(10, 4) NOT NULL DEFAULT 6.9600 CHECK (exchange_rate_bs > 0),
    require_deposit_percent NUMERIC(5, 2) NOT NULL DEFAULT 0.00 CHECK (require_deposit_percent BETWEEN 0 AND 100),
    static_qr_image_url TEXT,
    bank_account_info JSONB NOT NULL DEFAULT '{}'::jsonb,
    business_rules_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Perfiles de Usuarios Administrativos (Vinculados a Supabase Auth)
CREATE TABLE public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'OWNER' CHECK (role IN ('OWNER', 'STAFF', 'AUDITOR')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Catálogo Unificado (Productos de Importadora o Tratamientos de Clínica)
CREATE TABLE public.catalog_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    sku_or_code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    base_price_usd NUMERIC(12, 2),
    fixed_price_bs NUMERIC(12, 2),
    stock_quantity INTEGER DEFAULT 0,
    duration_minutes INTEGER DEFAULT 30,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('spanish', coalesce(sku_or_code, '') || ' ' || coalesce(name, '') || ' ' || coalesce(description, ''))
    ) STORED,
    embedding VECTOR(1536),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, sku_or_code)
);

CREATE INDEX idx_catalog_org ON public.catalog_items(organization_id);
CREATE INDEX idx_catalog_fts ON public.catalog_items USING GIN(search_vector);
CREATE INDEX idx_catalog_embedding ON public.catalog_items USING hnsw (embedding vector_cosine_ops);

-- 5. Clientes / Pacientes de WhatsApp
CREATE TABLE public.customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    whatsapp_wa_id TEXT NOT NULL,
    full_name TEXT,
    nit_or_ci TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_interaction_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, whatsapp_wa_id)
);

-- 6. Cabecera de Cotizaciones, Pedidos o Citas Agendadas
CREATE TABLE public.orders_appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES public.customers(id) ON DELETE RESTRICT,
    reference_code TEXT NOT NULL,
    status transaction_status NOT NULL DEFAULT 'DRAFT',
    applied_exchange_rate_bs NUMERIC(10, 4) NOT NULL,
    total_amount_bs NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    required_prepayment_bs NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    scheduled_start_at TIMESTAMPTZ,
    scheduled_end_at TIMESTAMPTZ,
    quote_pdf_storage_path TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, reference_code)
);

-- 7. Detalle de Ítems por Cotización / Cita
CREATE TABLE public.order_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES public.orders_appointments(id) ON DELETE CASCADE,
    catalog_item_id UUID NOT NULL REFERENCES public.catalog_items(id) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price_bs NUMERIC(12, 2) NOT NULL,
    subtotal_bs NUMERIC(12, 2) NOT NULL
);

-- 8. Registro y Auditoría Antifraude de Pagos QR
CREATE TABLE public.qr_payments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES public.orders_appointments(id) ON DELETE CASCADE,
    image_storage_path TEXT NOT NULL,
    image_sha256_hash TEXT NOT NULL,
    bank_origin TEXT,
    transaction_number TEXT,
    extracted_amount_bs NUMERIC(12, 2),
    transaction_datetime TIMESTAMPTZ,
    ocr_raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    verification_status qr_verification_status NOT NULL DEFAULT 'PENDING_OCR',
    reviewed_by UUID REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, image_sha256_hash),
    UNIQUE (bank_origin, transaction_number)
);

-- 9. Control de Idempotencia de Mensajes de WhatsApp
CREATE TABLE public.processed_webhook_messages (
    whatsapp_message_id TEXT PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- POLÍTICAS DE CIBERSEGURIDAD: ROW LEVEL SECURITY (RLS)
-- ============================================================================

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.catalog_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders_appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.qr_payments ENABLE ROW LEVEL SECURITY;

-- Función auxiliar para obtener el organization_id del usuario autenticado en JWT
CREATE OR REPLACE FUNCTION public.get_user_organization_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT organization_id FROM public.profiles WHERE id = auth.uid() LIMIT 1;
$$;

CREATE POLICY "Aislamiento Tenant en Organizaciones" ON public.organizations
    FOR ALL USING (id = public.get_user_organization_id());

CREATE POLICY "Aislamiento Tenant en Catalogo" ON public.catalog_items
    FOR ALL USING (organization_id = public.get_user_organization_id());

CREATE POLICY "Aislamiento Tenant en Clientes" ON public.customers
    FOR ALL USING (organization_id = public.get_user_organization_id());

CREATE POLICY "Aislamiento Tenant en Ordenes y Citas" ON public.orders_appointments
    FOR ALL USING (organization_id = public.get_user_organization_id());

CREATE POLICY "Aislamiento Tenant en Pagos QR" ON public.qr_payments
    FOR ALL USING (organization_id = public.get_user_organization_id());

-- ============================================================================
-- FUNCIÓN RPC: BÚSQUEDA HÍBRIDA + CÁLCULO DETERMINISTA DE PRECIO EN BS
-- ============================================================================

CREATE OR REPLACE FUNCTION public.search_catalog_for_tenant(
    p_organization_id UUID,
    p_query_text TEXT,
    p_query_embedding VECTOR(1536),
    p_limit INTEGER DEFAULT 5
)
RETURNS TABLE (
    item_id UUID,
    sku_or_code TEXT,
    name TEXT,
    description TEXT,
    stock_quantity INTEGER,
    duration_minutes INTEGER,
    calculated_price_bs NUMERIC(12, 2),
    similarity_score DOUBLE PRECISION
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_exchange_rate NUMERIC(10, 4);
BEGIN
    SELECT exchange_rate_bs INTO v_exchange_rate
    FROM public.organizations
    WHERE id = p_organization_id;

    RETURN QUERY
    SELECT
        ci.id AS item_id,
        ci.sku_or_code,
        ci.name,
        ci.description,
        ci.stock_quantity,
        ci.duration_minutes,
         COALESCE(
            ci.fixed_price_bs,
            ROUND(ci.base_price_usd * v_exchange_rate, 2)
        ) AS calculated_price_bs,
        (
            CASE
                WHEN ci.sku_or_code ILIKE '%' || p_query_text || '%' THEN 1.0
                ELSE 1 - (ci.embedding <=> p_query_embedding)
            END
        )::DOUBLE PRECISION AS similarity_score
    FROM public.catalog_items ci
    WHERE ci.organization_id = p_organization_id
      AND ci.is_active = true
    ORDER BY similarity_score DESC
    LIMIT p_limit;
END;
$$;
