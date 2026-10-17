-- ============================================================================
-- 17 · Consentimiento con evidencia y plantillas por categoría (#13, RF-08, EXT-16)
--
--   a. consent_events: historial append-only de cada opt-in y baja, con origen y el mensaje
--      que lo respalda. customers.optin_*_at / optout_at siguen siendo el estado actual.
--   b. private.record_consent: actualiza el estado y registra el evento en la MISMA
--      transacción (no pueden desincronizarse). La baja prevalece; un opt-in explícito la
--      levanta. SECURITY INVOKER: corre con la RLS de quien llama.
--   c. private.template_consent_ok: la regla en un solo lugar, para el trigger al encolar
--      y para el worker al enviar. UTILITY exige opt-in de utilidad; MARKETING, el de
--      marketing; sin categoría o con otra, no (falla cerrado).
-- Pruebas: supabase/tests/database/14_consent.test.sql
-- ============================================================================

-- a. Evidencia --------------------------------------------------------------------------
CREATE TABLE public.consent_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    customer_id      uuid NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('OPTIN_UTILITY', 'OPTIN_MARKETING', 'OPTOUT')),
    source           text NOT NULL CHECK (source IN ('WHATSAPP_KEYWORD', 'AGENT', 'DASHBOARD', 'IMPORT')),
    evidence         text CHECK (evidence IS NULL OR length(evidence) <= 256),  -- p. ej. el wa_message_id
    created_at       timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, customer_id)
        REFERENCES public.customers(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX consent_events_org_customer_idx ON public.consent_events (organization_id, customer_id, created_at);

ALTER TABLE public.consent_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_backend ON public.consent_events TO app_backend
    USING (organization_id = (SELECT private.backend_org_id()))
    WITH CHECK (organization_id = (SELECT private.backend_org_id()));
CREATE POLICY tenant_member_select ON public.consent_events FOR SELECT TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids()));

REVOKE ALL ON public.consent_events FROM anon, authenticated, app_backend;
GRANT SELECT ON public.consent_events TO authenticated;
GRANT SELECT, INSERT ON public.consent_events TO app_backend;

CREATE TRIGGER trg_consent_events_append_only BEFORE UPDATE OR DELETE ON public.consent_events
    FOR EACH ROW EXECUTE FUNCTION private.tg_append_only();

-- b. Registrar un consentimiento ----------------------------------------------------------
CREATE OR REPLACE FUNCTION private.record_consent(
    p_customer_id uuid, p_kind text, p_source text, p_evidence text DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql VOLATILE
SET search_path = ''
AS $$
BEGIN
    UPDATE public.customers c SET
        optin_utility_at   = CASE WHEN p_kind = 'OPTIN_UTILITY'   THEN now() ELSE c.optin_utility_at END,
        optin_marketing_at = CASE WHEN p_kind = 'OPTIN_MARKETING' THEN now() ELSE c.optin_marketing_at END,
        -- La baja prevalece; solo un opt-in explícito posterior la levanta
        optout_at          = CASE WHEN p_kind = 'OPTOUT' THEN now() ELSE NULL END
    WHERE c.id = p_customer_id AND c.organization_id = private.backend_org_id();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Cliente inexistente en este negocio' USING ERRCODE = 'P0001', HINT = 'NOT_FOUND';
    END IF;
    INSERT INTO public.consent_events (organization_id, customer_id, kind, source, evidence)
    VALUES (private.backend_org_id(), p_customer_id, p_kind, p_source, p_evidence);
END
$$;
REVOKE ALL ON FUNCTION private.record_consent(uuid, text, text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION private.record_consent(uuid, text, text, text) TO app_backend;

-- c. La regla de las plantillas, en un solo lugar --------------------------------------------
CREATE OR REPLACE FUNCTION private.template_consent_ok(
    p_optin_utility_at timestamptz, p_optin_marketing_at timestamptz, p_optout_at timestamptz, p_category text)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = ''
AS $$
    SELECT p_optout_at IS NULL AND CASE p_category
        WHEN 'UTILITY'   THEN p_optin_utility_at IS NOT NULL
        WHEN 'MARKETING' THEN p_optin_marketing_at IS NOT NULL
        ELSE false
    END
$$;
GRANT EXECUTE ON FUNCTION private.template_consent_ok(timestamptz, timestamptz, timestamptz, text) TO app_backend;

CREATE OR REPLACE FUNCTION private.tg_outbound_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_customer public.customers%ROWTYPE;
BEGIN
    SELECT c.* INTO v_customer
    FROM public.conversations v
    JOIN public.customers c ON c.organization_id = v.organization_id AND c.id = v.customer_id
    WHERE v.organization_id = NEW.organization_id AND v.id = NEW.conversation_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- conversación de otro tenant o inexistente: la RLS y la FK la rechazan
    END IF;

    IF NEW.kind = 'TEMPLATE' THEN
        IF NOT coalesce(private.template_consent_ok(v_customer.optin_utility_at, v_customer.optin_marketing_at,
                                                    v_customer.optout_at, NEW.payload ->> 'category'), false) THEN
            RAISE EXCEPTION 'Plantilla % sin consentimiento vigente del cliente para su categoría (%)',
                NEW.payload ->> 'name', coalesce(NEW.payload ->> 'category', 'sin categoría')
                USING ERRCODE = 'P0001', HINT = 'CONSENT_REQUIRED';
        END IF;
    ELSIF v_customer.last_inbound_at IS NULL OR v_customer.last_inbound_at < now() - interval '24 hours' THEN
        RAISE EXCEPTION 'Fuera de la ventana de 24 h de WhatsApp: solo se pueden enviar plantillas'
            USING ERRCODE = 'P0001', HINT = 'WINDOW_CLOSED';
    END IF;
    RETURN NEW;
END
$$;
