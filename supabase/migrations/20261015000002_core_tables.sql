-- ============================================================================
-- SCZ-B2B-CORE · Migración 0002 · Tablas de dominio multi-tenant
-- Principios (SYSTEM_DESIGN.md §5):
--   * Toda tabla de negocio lleva organization_id.
--   * Las FK entre entidades del mismo tenant son COMPUESTAS (organization_id, id):
--     el motor impide mezclar filas de tenants distintos aunque el backend falle.
--   * Datos financieros: ON DELETE RESTRICT (nunca cascada) + auditoría.
--   * Dinero en NUMERIC(12,2) Bs; tipo de cambio NUMERIC(10,4) Bs por USD.
-- ============================================================================

-- 1. Organizaciones (tenants) ------------------------------------------------
CREATE TABLE public.organizations (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  text NOT NULL CHECK (length(name) BETWEEN 2 AND 200),
    legal_name            text,
    nit                   text CHECK (nit IS NULL OR nit ~ '^[0-9]{5,15}$'),
    vertical              public.business_vertical NOT NULL,
    timezone              text NOT NULL DEFAULT 'America/La_Paz',
    deposit_percent       numeric(5,2) NOT NULL DEFAULT 0 CHECK (deposit_percent BETWEEN 0 AND 100),
    quote_validity_hours  integer NOT NULL DEFAULT 24 CHECK (quote_validity_hours BETWEEN 1 AND 720),
    hold_minutes          integer NOT NULL DEFAULT 15 CHECK (hold_minutes BETWEEN 5 AND 1440),
    fx_max_age_hours      integer NOT NULL DEFAULT 24 CHECK (fx_max_age_hours BETWEEN 1 AND 720),
    config                jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(config) = 'object'),
    config_schema_version integer NOT NULL DEFAULT 1,
    is_active             boolean NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz
);

-- 2. Membresías (un usuario puede pertenecer a varias organizaciones) ---------
CREATE TABLE public.profiles (
    id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    full_name  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.organization_members (
    organization_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    user_id         uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role            public.member_role NOT NULL DEFAULT 'STAFF',
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, user_id)
);
CREATE INDEX idx_members_user ON public.organization_members(user_id);

-- 3. Canales de WhatsApp (resolución de tenant por phone_number_id) ----------
CREATE TABLE public.whatsapp_channels (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    phone_number_id   text NOT NULL UNIQUE,      -- metadata.phone_number_id del webhook
    waba_id           text NOT NULL,
    display_phone     text NOT NULL,
    access_token_ref  text NOT NULL,             -- nombre del secreto en Vault, nunca el token
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_wa_channels_org ON public.whatsapp_channels(organization_id);

-- 4. Cuentas bancarias receptoras (para validar beneficiario del comprobante)
CREATE TABLE public.bank_accounts (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    bank_code               text NOT NULL,       -- catálogo normalizado (BNB, BCP, BUN, BME, ...)
    account_holder          text NOT NULL,
    account_last4           text NOT NULL CHECK (account_last4 ~ '^[0-9]{4}$'),
    static_qr_storage_path  text,                -- ruta en bucket privado, no URL pública
    is_default              boolean NOT NULL DEFAULT false,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id)
);
CREATE UNIQUE INDEX uq_bank_default ON public.bank_accounts(organization_id) WHERE is_default;

-- 5. Historial de tipo de cambio (append-only; nunca un valor fijo por defecto)
CREATE TABLE public.exchange_rates (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    rate_bs_per_usd  numeric(10,4) NOT NULL CHECK (rate_bs_per_usd > 0 AND rate_bs_per_usd < 1000),
    source           public.fx_source NOT NULL,
    effective_at     timestamptz NOT NULL DEFAULT now(),
    note             text,
    created_by       uuid REFERENCES auth.users(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id)
);
CREATE INDEX idx_fx_org_effective ON public.exchange_rates(organization_id, effective_at DESC);

-- 5b. Regímenes de IVA (tabla GLOBAL, no por tenant; la mantiene operación)
CREATE TABLE public.tax_regimes (
    id               smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode             public.tax_mode NOT NULL,
    iva_rate         numeric(5,2) NOT NULL CHECK (iva_rate > 0 AND iva_rate < 100),
    effective_from   timestamptz NOT NULL UNIQUE,
    legal_reference  text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.tax_regimes (mode, iva_rate, effective_from, legal_reference) VALUES
    ('IVA_INCLUIDO', 13.00, '2000-01-01 00:00:00-04',
     'Ley 843, IVA 13 % incluido en el precio (vigente hasta que rija el reglamento de la Ley 1733)');
-- Cuando el reglamento de la Ley 1733 fije la fecha, operación inserta:
--   ('IVA_ADICIONAL', 13.00, '<fecha de vigencia>', 'Ley 1733 + <norma reglamentaria>')

-- 6. Recursos agendables (profesional, sillón, box, sucursal) ----------------
CREATE TABLE public.resources (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    name             text NOT NULL,
    kind             text NOT NULL DEFAULT 'PROFESSIONAL',
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id)
);

CREATE TABLE public.business_hours (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL,
    resource_id      uuid NOT NULL,
    weekday          smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),   -- 0 = domingo
    opens_at         time NOT NULL,
    closes_at        time NOT NULL,
    CHECK (closes_at > opens_at),
    FOREIGN KEY (organization_id, resource_id)
        REFERENCES public.resources(organization_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_hours_resource ON public.business_hours(organization_id, resource_id, weekday);

-- 7. Catálogo unificado (productos o servicios) -------------------------------
CREATE TABLE public.catalog_items (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id          uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    item_type                public.item_type NOT NULL,
    sku                      text NOT NULL CHECK (length(sku) BETWEEN 1 AND 64),
    sku_normalized           text GENERATED ALWAYS AS (upper(regexp_replace(sku, '[^A-Za-z0-9]', '', 'g'))) STORED,
    name                     text NOT NULL CHECK (length(name) BETWEEN 2 AND 300),
    description              text,
    category                 text,
    price_currency           public.price_currency NOT NULL,
    list_price               numeric(12,2) NOT NULL CHECK (list_price >= 0),
    stock_on_hand            integer CHECK (stock_on_hand >= 0),
    duration_minutes         integer CHECK (duration_minutes BETWEEN 5 AND 600),
    deposit_percent_override numeric(5,2) CHECK (deposit_percent_override BETWEEN 0 AND 100),
    attributes               jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(attributes) = 'object'),
    search_text              tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish'::regconfig, private.f_unaccent(coalesce(sku, ''))), 'A') ||
        setweight(to_tsvector('spanish'::regconfig, private.f_unaccent(coalesce(name, ''))), 'A') ||
        setweight(to_tsvector('spanish'::regconfig, private.f_unaccent(coalesce(category, ''))), 'B') ||
        setweight(to_tsvector('spanish'::regconfig, private.f_unaccent(coalesce(description, ''))), 'C')
    ) STORED,
    embedding                extensions.vector(1536),     -- ver ADR-0003 (modelo de embeddings)
    embedding_model          text,
    embedding_updated_at     timestamptz,
    is_active                boolean NOT NULL DEFAULT true,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, sku),
    UNIQUE (organization_id, id),
    -- Coherencia por tipo: productos tienen stock, servicios tienen duración
    CONSTRAINT chk_item_shape CHECK (
        (item_type = 'PRODUCT' AND stock_on_hand IS NOT NULL AND duration_minutes IS NULL) OR
        (item_type = 'SERVICE' AND stock_on_hand IS NULL     AND duration_minutes IS NOT NULL)
    )
);
CREATE INDEX idx_catalog_org_active ON public.catalog_items(organization_id) WHERE is_active;
CREATE INDEX idx_catalog_fts  ON public.catalog_items USING gin (search_text);
CREATE INDEX idx_catalog_trgm ON public.catalog_items USING gin (sku_normalized extensions.gin_trgm_ops);
CREATE INDEX idx_catalog_name_trgm ON public.catalog_items USING gin (name extensions.gin_trgm_ops);
CREATE INDEX idx_catalog_embedding ON public.catalog_items
    USING hnsw (embedding extensions.vector_cosine_ops) WHERE embedding IS NOT NULL;

-- 8. Clientes / pacientes ----------------------------------------------------
CREATE TABLE public.customers (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    wa_id              text NOT NULL CHECK (wa_id ~ '^[0-9]{8,15}$'),
    display_name       text,
    full_name          text,
    billing_doc_type   text CHECK (billing_doc_type IN ('NIT', 'CI', 'CEX', 'PAS', 'OD')),
    billing_doc_number text,
    billing_name       text,
    privacy_notice_at  timestamptz,           -- aviso de privacidad / IA mostrado
    optin_utility_at   timestamptz,           -- aceptó recordatorios/avisos (plantillas de utilidad)
    optin_marketing_at timestamptz,           -- aceptó promociones (plantillas de marketing)
    optout_at          timestamptz,           -- pidió no recibir mensajes proactivos (prevalece)
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_inbound_at    timestamptz,           -- controla la ventana de 24 h de Meta
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    deleted_at         timestamptz,
    UNIQUE (organization_id, wa_id),
    UNIQUE (organization_id, id)
);

-- 9. Conversaciones y mensajes (memoria del agente + auditoría) --------------
CREATE TABLE public.conversations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL,
    customer_id       uuid NOT NULL,
    mode              public.conversation_mode NOT NULL DEFAULT 'BOT',
    assigned_to       uuid REFERENCES auth.users(id),
    bot_paused_until  timestamptz,
    summary           text,                   -- resumen rodante para acotar tokens
    last_inbound_at   timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, customer_id)
        REFERENCES public.customers(organization_id, id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_open_conversation ON public.conversations(organization_id, customer_id)
    WHERE mode <> 'CLOSED';

CREATE TABLE public.messages (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id    uuid NOT NULL,
    conversation_id    uuid NOT NULL,
    direction          text NOT NULL CHECK (direction IN ('IN', 'OUT')),
    sender             text NOT NULL CHECK (sender IN ('CUSTOMER', 'BOT', 'HUMAN', 'SYSTEM')),
    wa_message_id      text UNIQUE,
    msg_type           text NOT NULL,         -- text, image, audio, document, interactive, template
    body               text,
    media_storage_path text,
    processed_at       timestamptz,           -- cuándo el agente consumió este mensaje entrante
    created_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, conversation_id)
        REFERENCES public.conversations(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_messages_conv ON public.messages(organization_id, conversation_id, created_at);
CREATE INDEX idx_messages_pending ON public.messages(conversation_id)
    WHERE direction = 'IN' AND processed_at IS NULL;

-- 10. Órdenes: cotizaciones/pedidos (SALE) y citas (APPOINTMENT) -------------
CREATE TABLE public.orders (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    customer_id          uuid NOT NULL,
    conversation_id      uuid,
    kind                 public.order_kind NOT NULL,
    reference_code       text NOT NULL,
    status               public.order_status NOT NULL DEFAULT 'DRAFT',
    fx_rate_id           bigint,
    applied_fx_rate      numeric(10,4) CHECK (applied_fx_rate > 0),
    tax_regime_id        smallint REFERENCES public.tax_regimes(id),
    subtotal_bs          numeric(12,2) NOT NULL DEFAULT 0 CHECK (subtotal_bs >= 0),  -- Σ líneas
    iva_bs               numeric(12,2) NOT NULL DEFAULT 0 CHECK (iva_bs >= 0),
    total_bs             numeric(12,2) NOT NULL DEFAULT 0 CHECK (total_bs >= 0),     -- a pagar
    deposit_required_bs  numeric(12,2) NOT NULL DEFAULT 0 CHECK (deposit_required_bs >= 0),
    amount_paid_bs       numeric(12,2) NOT NULL DEFAULT 0 CHECK (amount_paid_bs >= 0),
    valid_until          timestamptz,
    resource_id          uuid,
    slot                 tstzrange,
    quote_pdf_path       text,
    notes                text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, reference_code),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, customer_id)
        REFERENCES public.customers(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, conversation_id)
        REFERENCES public.conversations(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, resource_id)
        REFERENCES public.resources(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, fx_rate_id)
        REFERENCES public.exchange_rates(organization_id, id) ON DELETE RESTRICT,
    CONSTRAINT chk_deposit_le_total CHECK (deposit_required_bs <= total_bs),
    CONSTRAINT chk_appointment_shape CHECK (
        (kind = 'APPOINTMENT' AND slot IS NOT NULL AND resource_id IS NOT NULL
             AND NOT isempty(slot) AND lower_inc(slot) AND NOT upper_inc(slot))
        OR (kind = 'SALE' AND slot IS NULL)
    ),
    -- Garantía a nivel de motor: un recurso no puede tener dos citas activas solapadas
    CONSTRAINT excl_no_double_booking EXCLUDE USING gist (
        organization_id WITH =, resource_id WITH =, slot WITH &&
    ) WHERE (kind = 'APPOINTMENT'
             AND status IN ('HELD', 'AWAITING_PAYMENT', 'PAYMENT_REVIEW', 'CONFIRMED'))
);
CREATE INDEX idx_orders_customer ON public.orders(organization_id, customer_id, created_at DESC);
CREATE INDEX idx_orders_status   ON public.orders(organization_id, status);
CREATE INDEX idx_orders_expiring ON public.orders(valid_until)
    WHERE status IN ('QUOTED', 'HELD', 'AWAITING_PAYMENT');

CREATE TABLE public.order_items (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid NOT NULL,
    order_id         uuid NOT NULL,
    catalog_item_id  uuid NOT NULL,
    sku_snapshot     text NOT NULL,
    name_snapshot    text NOT NULL,
    quantity         integer NOT NULL CHECK (quantity BETWEEN 1 AND 10000),
    unit_price_bs    numeric(12,2) NOT NULL CHECK (unit_price_bs >= 0),
    line_total_bs    numeric(12,2) GENERATED ALWAYS AS (round(quantity * unit_price_bs, 2)) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_id, catalog_item_id),
    FOREIGN KEY (organization_id, order_id)
        REFERENCES public.orders(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, catalog_item_id)
        REFERENCES public.catalog_items(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_order_items_order ON public.order_items(organization_id, order_id);

-- Reservas de stock con vencimiento (evitan sobreventa por concurrencia)
CREATE TABLE public.stock_reservations (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL,
    order_id         uuid NOT NULL,
    catalog_item_id  uuid NOT NULL,
    quantity         integer NOT NULL CHECK (quantity > 0),
    expires_at       timestamptz NOT NULL,
    released_at      timestamptz,
    committed_at     timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (released_at IS NULL OR committed_at IS NULL),
    FOREIGN KEY (organization_id, order_id)
        REFERENCES public.orders(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, catalog_item_id)
        REFERENCES public.catalog_items(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_reservations_active ON public.stock_reservations(organization_id, catalog_item_id)
    WHERE released_at IS NULL AND committed_at IS NULL;

-- 11. Evidencias de pago (TODO intento queda registrado, incluso duplicados) --
CREATE TABLE public.payment_evidence (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id           uuid NOT NULL,
    order_id                  uuid NOT NULL,
    customer_id               uuid NOT NULL,
    channel                   public.payment_channel NOT NULL,
    storage_path              text,
    sha256                    text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    phash                     bigint,          -- hash perceptual 64-bit (recortes / recompresión)
    bank_code                 text,
    txn_number_normalized     text,
    amount_bs                 numeric(12,2) CHECK (amount_bs > 0),
    txn_at                    timestamptz,
    beneficiary_name          text,
    beneficiary_account_last4 text,
    glosa                     text,
    ocr_model                 text,
    ocr_confidence            numeric(4,3) CHECK (ocr_confidence BETWEEN 0 AND 1),
    ocr_raw                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    checks                    jsonb NOT NULL DEFAULT '{}'::jsonb,   -- resultado de cada regla antifraude
    status                    public.evidence_status NOT NULL DEFAULT 'RECEIVED',
    duplicate_of              uuid,
    reviewed_by               uuid REFERENCES auth.users(id),
    reviewed_at               timestamptz,
    review_note               text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, order_id)
        REFERENCES public.orders(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, customer_id)
        REFERENCES public.customers(organization_id, id) ON DELETE RESTRICT
);
-- Candado 1: misma imagen exacta no puede sostener dos evidencias vigentes en el tenant
CREATE UNIQUE INDEX uq_evidence_sha256 ON public.payment_evidence(organization_id, sha256)
    WHERE sha256 IS NOT NULL AND status NOT IN ('REJECTED_DUPLICATE');
-- Candado 2: una transacción bancaria solo puede respaldar UN pago en TODA la plataforma
-- (cross-tenant a propósito: detecta reuso del mismo comprobante en otro negocio sin
--  exponer datos de ese otro tenant, porque RLS sigue ocultando la fila).
CREATE UNIQUE INDEX uq_evidence_bank_txn ON public.payment_evidence(bank_code, txn_number_normalized)
    WHERE txn_number_normalized IS NOT NULL
      AND status IN ('AUTO_CHECK_PASSED', 'NEEDS_REVIEW', 'APPROVED');
CREATE INDEX idx_evidence_order  ON public.payment_evidence(organization_id, order_id);
CREATE INDEX idx_evidence_review ON public.payment_evidence(organization_id, status, created_at)
    WHERE status IN ('AUTO_CHECK_PASSED', 'NEEDS_REVIEW');

-- 12. Pagos confirmados (libro mayor append-only) ------------------------------
CREATE TABLE public.payments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL,
    order_id            uuid NOT NULL,
    evidence_id         uuid UNIQUE,
    channel             public.payment_channel NOT NULL,
    amount_bs           numeric(12,2) NOT NULL CHECK (amount_bs > 0),
    bank_code           text,
    txn_number          text,
    confirmed_via       public.payment_confirmation NOT NULL,
    confirmed_by        uuid REFERENCES auth.users(id),
    confirmed_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (confirmed_via <> 'HUMAN' OR confirmed_by IS NOT NULL),
    FOREIGN KEY (organization_id, order_id)
        REFERENCES public.orders(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, evidence_id)
        REFERENCES public.payment_evidence(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX idx_payments_order ON public.payments(organization_id, order_id);

-- 13. Facturas SIAT (vía adaptador de proveedor homologado) ------------------
CREATE TABLE public.invoices (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL,
    order_id          uuid NOT NULL,
    provider          text NOT NULL,
    status            public.invoice_status NOT NULL DEFAULT 'PENDING',
    invoice_number    bigint,
    cuf               text,
    cufd              text,
    buyer_doc_type    text NOT NULL,
    buyer_doc_number  text NOT NULL,
    buyer_name        text NOT NULL,
    total_bs          numeric(12,2) NOT NULL CHECK (total_bs >= 0),
    issued_at         timestamptz,
    pdf_storage_path  text,
    xml_storage_path  text,
    provider_payload  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, order_id)
        REFERENCES public.orders(organization_id, id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_invoice_per_order ON public.invoices(organization_id, order_id)
    WHERE status <> 'VOIDED';
