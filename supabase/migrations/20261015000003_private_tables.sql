-- ============================================================================
-- SCZ-B2B-CORE · Migración 0003 · Tablas internas (esquema `private`, no expuesto)
-- Cola durable, idempotencia de webhooks, outbox, costos de IA, auditoría y FSM.
-- ============================================================================

-- 1. Idempotencia de webhooks: TODO evento entrante se persiste antes del 200 OK
CREATE TABLE private.inbound_events (
    wa_message_id    text PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    phone_number_id  text NOT NULL,
    event_type       text NOT NULL,             -- message | status | template_update ...
    payload          jsonb NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_inbound_received ON private.inbound_events(received_at);   -- purga por retención

-- 2. Cola durable de trabajos (FOR UPDATE SKIP LOCKED) — ADR-0005
CREATE TABLE private.jobs (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid REFERENCES public.organizations(id) ON DELETE RESTRICT,
    kind             text NOT NULL,        -- PROCESS_CONVERSATION | VERIFY_EVIDENCE | SEND_OUTBOUND | EMBED_ITEM | EXPIRE_ORDERS ...
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    conversation_key text,                 -- serializa trabajos de una misma conversación
    status           text NOT NULL DEFAULT 'QUEUED'
                     CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'DEAD')),
    attempts         integer NOT NULL DEFAULT 0,
    max_attempts     integer NOT NULL DEFAULT 8,
    run_after        timestamptz NOT NULL DEFAULT now(),
    locked_at        timestamptz,
    locked_by        text,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_jobs_ready ON private.jobs(run_after, id) WHERE status = 'QUEUED';
CREATE INDEX idx_jobs_running_key ON private.jobs(conversation_key) WHERE status = 'RUNNING';
-- Debounce: como máximo UN trabajo en cola por conversación; los mensajes en ráfaga se agrupan
CREATE UNIQUE INDEX uq_jobs_queued_conversation ON private.jobs(conversation_key)
    WHERE status = 'QUEUED' AND kind = 'PROCESS_CONVERSATION';

-- 3. Outbox de mensajes salientes (reintentos idempotentes hacia Meta)
CREATE TABLE private.outbound_messages (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    conversation_id  uuid NOT NULL,
    idempotency_key  text NOT NULL UNIQUE,
    kind             text NOT NULL CHECK (kind IN ('TEXT', 'DOCUMENT', 'IMAGE', 'TEMPLATE', 'INTERACTIVE')),
    payload          jsonb NOT NULL,
    status           text NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED')),
    wa_message_id    text UNIQUE,
    attempts         integer NOT NULL DEFAULT 0,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    sent_at          timestamptz,
    FOREIGN KEY (organization_id, conversation_id)
        REFERENCES public.conversations(organization_id, id) ON DELETE RESTRICT
);

-- 4. Telemetría y costo de IA por tenant (cuotas anti "denial of wallet")
CREATE TABLE private.llm_calls (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    conversation_id  uuid,
    purpose          text NOT NULL CHECK (purpose IN ('CHAT', 'OCR', 'EMBEDDING', 'SUMMARY', 'STT')),
    provider         text NOT NULL,
    model            text NOT NULL,
    input_tokens     integer NOT NULL DEFAULT 0,
    output_tokens    integer NOT NULL DEFAULT 0,
    cached_tokens    integer NOT NULL DEFAULT 0,
    cost_usd         numeric(12,6) NOT NULL DEFAULT 0,
    latency_ms       integer,
    tool_calls       integer NOT NULL DEFAULT 0,
    outcome          text,
    trace_id         text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_calls_org_day ON private.llm_calls(organization_id, created_at);

-- 5. Bitácora de auditoría append-only
CREATE TABLE private.audit_log (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid,
    table_name       text NOT NULL,
    row_id           text NOT NULL,
    action           text NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    actor_kind       text NOT NULL,      -- HUMAN | BOT | SYSTEM
    actor_id         uuid,
    db_role          text NOT NULL DEFAULT current_user,
    old_data         jsonb,
    new_data         jsonb,
    at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_org_at ON private.audit_log(organization_id, at DESC);
CREATE INDEX idx_audit_row ON private.audit_log(table_name, row_id);

-- 6. Transiciones válidas de la máquina de estados (fuente única de verdad)
CREATE TABLE private.fsm_transitions (
    kind            public.order_kind   NOT NULL,
    from_status     public.order_status NOT NULL,
    to_status       public.order_status NOT NULL,
    allowed_actors  text[] NOT NULL,     -- subconjunto de {BOT, SYSTEM, HUMAN}
    PRIMARY KEY (kind, from_status, to_status)
);

INSERT INTO private.fsm_transitions (kind, from_status, to_status, allowed_actors) VALUES
    -- Venta / cotización
    ('SALE', 'DRAFT',            'QUOTED',           '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'QUOTED',           'AWAITING_PAYMENT', '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'QUOTED',           'CONFIRMED',        '{SYSTEM,HUMAN}'),     -- sin seña requerida
    ('SALE', 'AWAITING_PAYMENT', 'PAYMENT_REVIEW',   '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'AWAITING_PAYMENT', 'CONFIRMED',        '{SYSTEM,HUMAN}'),     -- confirmación bancaria directa
    ('SALE', 'PAYMENT_REVIEW',   'CONFIRMED',        '{SYSTEM,HUMAN}'),
    ('SALE', 'PAYMENT_REVIEW',   'AWAITING_PAYMENT', '{SYSTEM,HUMAN}'),     -- evidencia rechazada
    ('SALE', 'CONFIRMED',        'COMPLETED',        '{SYSTEM,HUMAN}'),
    ('SALE', 'CONFIRMED',        'REFUNDED',         '{HUMAN}'),
    ('SALE', 'DRAFT',            'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'QUOTED',           'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'AWAITING_PAYMENT', 'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('SALE', 'PAYMENT_REVIEW',   'CANCELLED',        '{HUMAN}'),
    ('SALE', 'CONFIRMED',        'CANCELLED',        '{HUMAN}'),
    ('SALE', 'QUOTED',           'EXPIRED',          '{SYSTEM}'),
    ('SALE', 'AWAITING_PAYMENT', 'EXPIRED',          '{SYSTEM}'),
    -- Cita
    ('APPOINTMENT', 'DRAFT',            'HELD',             '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'HELD',             'AWAITING_PAYMENT', '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'HELD',             'CONFIRMED',        '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'AWAITING_PAYMENT', 'PAYMENT_REVIEW',   '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'AWAITING_PAYMENT', 'CONFIRMED',        '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'PAYMENT_REVIEW',   'CONFIRMED',        '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'PAYMENT_REVIEW',   'AWAITING_PAYMENT', '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'CONFIRMED',        'COMPLETED',        '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'CONFIRMED',        'NO_SHOW',          '{SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'CONFIRMED',        'REFUNDED',         '{HUMAN}'),
    ('APPOINTMENT', 'DRAFT',            'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'HELD',             'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'AWAITING_PAYMENT', 'CANCELLED',        '{BOT,SYSTEM,HUMAN}'),
    ('APPOINTMENT', 'PAYMENT_REVIEW',   'CANCELLED',        '{HUMAN}'),
    ('APPOINTMENT', 'CONFIRMED',        'CANCELLED',        '{HUMAN}'),
    ('APPOINTMENT', 'HELD',             'EXPIRED',          '{SYSTEM}'),
    ('APPOINTMENT', 'AWAITING_PAYMENT', 'EXPIRED',          '{SYSTEM}');

-- 7. Correlativos por tenant para códigos de referencia legibles (COT-2026-000123)
CREATE TABLE private.org_counters (
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    scope            text NOT NULL,
    year             integer NOT NULL,
    last_value       bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (organization_id, scope, year)
);
