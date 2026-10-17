-- ============================================================================
-- 16 · Outbox: ventana de 24 h, consentimiento, estado SENDING y canal de la conversación
--      (#18, RF-06, RF-08, ADR-0006)
--
--   a. La conversación recuerda por qué número escribió el cliente (un negocio puede tener
--      varios). FK compuesta por tenant: no puede apuntar al número de otro negocio.
--   b. Estado SENDING: el worker lo confirma ANTES de llamar a Meta. La Cloud API no tiene
--      clave de idempotencia; si el worker muere durante la llamada, el reintento encuentra
--      SENDING y no reenvía (resultado incierto -> FAILED con alerta, no un mensaje duplicado).
--   c. Trigger al encolar (defensa en profundidad; el worker vuelve a mirar al enviar):
--        * TEXT, DOCUMENT, IMAGE, INTERACTIVE: solo dentro de la ventana de 24 h desde el
--          último mensaje del cliente (customers.last_inbound_at). HINT WINDOW_CLOSED.
--        * TEMPLATE: exige opt-in (de utilidad o de marketing) y que no haya baja. Falla
--          cerrado hasta el #13, que distingue la categoría. HINT CONSENT_REQUIRED.
--   d. El backend solo actualiza las columnas del envío: un mensaje encolado no cambia de
--      tipo, contenido ni conversación (lo que esquivaría el control de ventana).
-- Pruebas: supabase/tests/database/13_outbox.test.sql
-- ============================================================================

-- a. Canal de la conversación --------------------------------------------------------
ALTER TABLE public.whatsapp_channels
    ADD CONSTRAINT whatsapp_channels_org_id_key UNIQUE (organization_id, id);

ALTER TABLE public.conversations
    ADD COLUMN whatsapp_channel_id uuid,
    ADD CONSTRAINT conversations_whatsapp_channel_fkey
        FOREIGN KEY (organization_id, whatsapp_channel_id)
        REFERENCES public.whatsapp_channels (organization_id, id) ON DELETE RESTRICT;
CREATE INDEX conversations_org_channel_idx
    ON public.conversations (organization_id, whatsapp_channel_id) WHERE whatsapp_channel_id IS NOT NULL;

-- b. Estado SENDING -------------------------------------------------------------------
ALTER TABLE private.outbound_messages DROP CONSTRAINT outbound_messages_status_check;
ALTER TABLE private.outbound_messages ADD CONSTRAINT outbound_messages_status_check
    CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED'));

-- c. Ventana y consentimiento al encolar ------------------------------------------------
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
        IF v_customer.optout_at IS NOT NULL
           OR (v_customer.optin_utility_at IS NULL AND v_customer.optin_marketing_at IS NULL) THEN
            RAISE EXCEPTION 'Plantilla sin consentimiento vigente del cliente (opt-in y sin baja)'
                USING ERRCODE = 'P0001', HINT = 'CONSENT_REQUIRED';
        END IF;
    ELSIF v_customer.last_inbound_at IS NULL OR v_customer.last_inbound_at < now() - interval '24 hours' THEN
        RAISE EXCEPTION 'Fuera de la ventana de 24 h de WhatsApp: solo se pueden enviar plantillas'
            USING ERRCODE = 'P0001', HINT = 'WINDOW_CLOSED';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_outbound_guard
    BEFORE INSERT ON private.outbound_messages
    FOR EACH ROW EXECUTE FUNCTION private.tg_outbound_guard();

-- d. Columnas que el backend puede actualizar -----------------------------------------
REVOKE UPDATE ON private.outbound_messages FROM app_backend;
GRANT UPDATE (status, wa_message_id, attempts, last_error, sent_at) ON private.outbound_messages TO app_backend;
