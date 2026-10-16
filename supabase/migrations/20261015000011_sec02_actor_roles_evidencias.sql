-- ============================================================================
-- 11 · SEC-02: actor por defecto, transiciones humanas por rol y FSM de evidencias
--
-- Fallas corregidas (revisión del 15-oct-2026, severidad media, issue #53):
--   M1. private.actor_kind() devolvía SYSTEM cuando el backend no fijaba app.actor
--       (falla abierto). Con GRANT INSERT ON payments a app_backend, un UnitOfWork
--       que olvidara el actor podía registrar un pago BANK_API y confirmar la orden,
--       aunque el MVP no tiene integración bancaria (ADR-0014). Además el backend
--       podía declararse HUMAN con app.actor o con un JWT fijado por él mismo.
--   M2. STAFF (sin MFA) podía hacer todas las transiciones HUMAN: reembolsar,
--       cancelar órdenes CONFIRMED o PAYMENT_REVIEW y confirmar órdenes sin seña.
--       SYSTEM_DESIGN §8.2 solo le da catálogo, conversaciones, agenda y rechazos.
--   M3. app_backend tenía UPDATE sobre todas las columnas de payment_evidence:
--       podía marcar APPROVED y reviewed_by sin pasar por approve_payment_evidence.
--
-- Corrección:
--   a. actor_kind(): sin actor válido devuelve BOT (menor privilegio); el backend
--      solo puede ser BOT o SYSTEM; HUMAN solo con rol authenticated o JWT en una RPC.
--   b. app_backend sin INSERT en payments hasta la fase 7; un pago BANK_API exige
--      actor SYSTEM explícito y uno HUMAN, OWNER/ADMIN con aal2 a su nombre.
--   c. Trigger de roles en orders: STAFF solo cancela antes del pago; el resto de
--      transiciones humanas exige OWNER/ADMIN con aal2.
--   d. FSM de evidencias en la BD (private.evidence_transitions + trigger), revisión
--      solo desde las RPC humanas y columnas acotadas para app_backend.
-- Pruebas: supabase/tests/database/08_actor_roles.test.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- a. Actor: falla cerrado
-- ----------------------------------------------------------------------------
-- session_user cubre las funciones SECURITY DEFINER que invoca el backend: allí
-- current_user es el dueño de la función, pero la conexión sigue siendo app_backend.
CREATE OR REPLACE FUNCTION private.actor_kind()
RETURNS text
LANGUAGE sql STABLE
SET search_path = ''
AS $$
    SELECT CASE
        -- Dashboard: el usuario autenticado es una persona
        WHEN current_user = 'authenticated' THEN 'HUMAN'
        -- Backend: BOT o SYSTEM según el UnitOfWork. Un app.actor = 'HUMAN' o un JWT
        -- fijados por el propio backend no lo convierten en persona.
        WHEN current_user = 'app_backend' OR session_user = 'app_backend' THEN
            CASE WHEN current_setting('app.actor', true) = 'SYSTEM' THEN 'SYSTEM' ELSE 'BOT' END
        -- RPC SECURITY DEFINER invocada desde el dashboard: el JWT identifica a la persona
        WHEN private.jwt_uid() IS NOT NULL THEN 'HUMAN'
        WHEN current_setting('app.actor', true) = 'SYSTEM' THEN 'SYSTEM'
        -- Sin actor válido (UnitOfWork incompleto, mantenimiento): el de menor privilegio
        ELSE 'BOT'
    END
$$;

-- ----------------------------------------------------------------------------
-- b. Libro de pagos: sin escritura del backend en el MVP
-- ----------------------------------------------------------------------------
-- Fase 7 (OpenBCB / API bancaria, ADR-0014) volverá a otorgarlo en su propia
-- migración, junto con el callback autenticado y la consulta de estado de vuelta.
REVOKE INSERT ON public.payments FROM app_backend;

CREATE OR REPLACE FUNCTION private.tg_payments_actor_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_actor text := private.actor_kind();
BEGIN
    IF NEW.confirmed_via = 'BANK_API' AND v_actor <> 'SYSTEM' THEN
        RAISE EXCEPTION 'Un pago BANK_API solo lo registra el actor SYSTEM (actor %)', v_actor
            USING ERRCODE = '42501', HINT = 'ACTOR_REQUIRED';
    END IF;
    IF NEW.confirmed_via = 'HUMAN' AND (
           v_actor <> 'HUMAN'
        OR NEW.confirmed_by IS DISTINCT FROM private.jwt_uid()
        OR NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
        OR coalesce(auth.jwt() ->> 'aal', '') <> 'aal2') THEN
        RAISE EXCEPTION 'Un pago HUMAN lo registra OWNER/ADMIN con MFA y a su propio nombre'
            USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_payments_actor_guard
    BEFORE INSERT ON public.payments
    FOR EACH ROW EXECUTE FUNCTION private.tg_payments_actor_guard();

-- La política RESTRICTIVE payments_not_bot (migración 05) queda como tercera capa.

-- ----------------------------------------------------------------------------
-- c. Órdenes: quién de las personas puede hacer cada transición
-- ----------------------------------------------------------------------------
-- fsm_transitions dice QUÉ actor (BOT/SYSTEM/HUMAN); este trigger dice QUÉ persona.
-- STAFF: cancelar antes del pago (sin pagos registrados) y devolver a AWAITING_PAYMENT
-- una orden sin evidencias pendientes (el efecto de reject_payment_evidence).
-- Lo demás (reembolsar, cancelar pagadas o en revisión, confirmar, completar):
-- OWNER/ADMIN con aal2.
CREATE OR REPLACE FUNCTION private.tg_orders_human_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_staff_ok boolean;
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status OR private.actor_kind() <> 'HUMAN' THEN
        RETURN NEW;
    END IF;

    v_staff_ok :=
        (NEW.status = 'CANCELLED'
         AND OLD.status IN ('DRAFT', 'QUOTED', 'HELD', 'AWAITING_PAYMENT')
         AND OLD.amount_paid_bs = 0)
        OR (OLD.status = 'PAYMENT_REVIEW' AND NEW.status = 'AWAITING_PAYMENT'
            AND NOT EXISTS (SELECT 1 FROM public.payment_evidence e
                            WHERE e.organization_id = NEW.organization_id AND e.order_id = NEW.id
                              AND e.status IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW')));

    IF v_staff_ok AND NEW.organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')) THEN
        RETURN NEW;
    END IF;
    IF NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}')) THEN
        RAISE EXCEPTION 'La transición % -> % de % requiere OWNER o ADMIN',
            OLD.status, NEW.status, OLD.reference_code USING ERRCODE = '42501', HINT = 'ROLE_REQUIRED';
    END IF;
    IF coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' THEN
        RAISE EXCEPTION 'La transición % -> % de % requiere MFA',
            OLD.status, NEW.status, OLD.reference_code USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_orders_human_guard
    BEFORE UPDATE OF status ON public.orders
    FOR EACH ROW EXECUTE FUNCTION private.tg_orders_human_guard();

-- ----------------------------------------------------------------------------
-- d. Máquina de estados de evidencias (docs/architecture/uml.md §7.1)
-- ----------------------------------------------------------------------------
CREATE TABLE private.evidence_transitions (
    from_status     public.evidence_status NOT NULL,
    to_status       public.evidence_status NOT NULL,
    allowed_actors  text[] NOT NULL,     -- subconjunto de {BOT, SYSTEM, HUMAN}
    PRIMARY KEY (from_status, to_status)
);

-- Backend (agente y worker): RECEIVED -> OCR_DONE -> AUTO_CHECK_PASSED/NEEDS_REVIEW,
-- y REJECTED_DUPLICATE cuando la imagen o la transacción ya se usaron.
INSERT INTO private.evidence_transitions (from_status, to_status, allowed_actors) VALUES
    ('RECEIVED',          'OCR_DONE',           '{BOT,SYSTEM}'),
    ('RECEIVED',          'REJECTED_DUPLICATE', '{BOT,SYSTEM,HUMAN}'),
    ('OCR_DONE',          'AUTO_CHECK_PASSED',  '{BOT,SYSTEM}'),
    ('OCR_DONE',          'NEEDS_REVIEW',       '{BOT,SYSTEM}'),
    ('OCR_DONE',          'REJECTED_DUPLICATE', '{BOT,SYSTEM,HUMAN}'),
    ('AUTO_CHECK_PASSED', 'APPROVED',           '{HUMAN}'),
    ('NEEDS_REVIEW',      'APPROVED',           '{HUMAN}');
-- Personas: los rechazos que acepta reject_payment_evidence (desde cualquier estado no final)
INSERT INTO private.evidence_transitions (from_status, to_status, allowed_actors)
SELECT f::public.evidence_status, t::public.evidence_status, '{HUMAN}'
  FROM unnest(ARRAY['RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW']) f
 CROSS JOIN unnest(ARRAY['REJECTED_DUPLICATE', 'REJECTED_MISMATCH', 'REJECTED_FRAUD', 'REJECTED_OTHER']) t
ON CONFLICT (from_status, to_status) DO NOTHING;

ALTER TABLE private.evidence_transitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY evidence_fsm_read ON private.evidence_transitions FOR SELECT
    TO app_backend, authenticated USING (true);
GRANT SELECT ON private.evidence_transitions TO app_backend, authenticated;

CREATE OR REPLACE FUNCTION private.tg_payment_evidence_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_actor text := private.actor_kind();
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW', 'REJECTED_DUPLICATE') THEN
            RAISE EXCEPTION 'Evidencia: el estado inicial % lo decide una persona revisora', NEW.status
                USING ERRCODE = 'P0001', HINT = 'EVIDENCE_FSM';
        END IF;
        IF NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL OR NEW.review_note IS NOT NULL THEN
            RAISE EXCEPTION 'Evidencia: la revisión solo la registran las RPC de revisión'
                USING ERRCODE = '42501', HINT = 'REVIEW_RPC_ONLY';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IN ('APPROVED', 'REJECTED_DUPLICATE', 'REJECTED_MISMATCH', 'REJECTED_FRAUD', 'REJECTED_OTHER') THEN
        RAISE EXCEPTION 'Evidencia % en estado final %', OLD.id, OLD.status
            USING ERRCODE = 'P0001', HINT = 'EVIDENCE_FINAL';
    END IF;

    IF (NEW.organization_id, NEW.order_id, NEW.customer_id, NEW.channel)
           IS DISTINCT FROM (OLD.organization_id, OLD.order_id, OLD.customer_id, OLD.channel)
       OR (OLD.sha256 IS NOT NULL AND NEW.sha256 IS DISTINCT FROM OLD.sha256) THEN
        RAISE EXCEPTION 'Evidencia %: organización, orden, cliente, canal y sha256 son inmutables', OLD.id
            USING ERRCODE = 'P0001', HINT = 'EVIDENCE_IMMUTABLE';
    END IF;

    -- Desde AUTO_CHECK_PASSED/NEEDS_REVIEW la persona aprueba exactamente lo que se verificó
    IF OLD.status NOT IN ('RECEIVED', 'OCR_DONE') AND (
           NEW.storage_path, NEW.sha256, NEW.phash, NEW.bank_code, NEW.txn_number_normalized,
           NEW.amount_bs, NEW.txn_at, NEW.beneficiary_name, NEW.beneficiary_account_last4, NEW.glosa,
           NEW.ocr_model, NEW.ocr_confidence, NEW.ocr_raw, NEW.checks, NEW.duplicate_of)
       IS DISTINCT FROM (
           OLD.storage_path, OLD.sha256, OLD.phash, OLD.bank_code, OLD.txn_number_normalized,
           OLD.amount_bs, OLD.txn_at, OLD.beneficiary_name, OLD.beneficiary_account_last4, OLD.glosa,
           OLD.ocr_model, OLD.ocr_confidence, OLD.ocr_raw, OLD.checks, OLD.duplicate_of) THEN
        RAISE EXCEPTION 'Evidencia %: datos congelados en %', OLD.id, OLD.status
            USING ERRCODE = 'P0001', HINT = 'EVIDENCE_FROZEN';
    END IF;

    IF v_actor <> 'HUMAN' AND (NEW.reviewed_by, NEW.reviewed_at, NEW.review_note)
           IS DISTINCT FROM (OLD.reviewed_by, OLD.reviewed_at, OLD.review_note) THEN
        RAISE EXCEPTION 'Evidencia: la revisión solo la registran las RPC de revisión'
            USING ERRCODE = '42501', HINT = 'REVIEW_RPC_ONLY';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM private.evidence_transitions t
            WHERE t.from_status = OLD.status AND t.to_status = NEW.status
              AND v_actor = ANY (t.allowed_actors)
        ) THEN
            RAISE EXCEPTION 'Evidencia: transición % -> % no permitida para actor %',
                OLD.status, NEW.status, v_actor USING ERRCODE = 'P0001', HINT = 'EVIDENCE_FSM';
        END IF;

        -- Mismas reglas que las RPC: aprobar = OWNER/ADMIN con aal2; rechazar = OWNER/ADMIN/STAFF
        IF v_actor = 'HUMAN' THEN
            IF NEW.reviewed_by IS DISTINCT FROM private.jwt_uid() THEN
                RAISE EXCEPTION 'Evidencia: reviewed_by debe ser quien revisa'
                    USING ERRCODE = '42501', HINT = 'REVIEW_RPC_ONLY';
            END IF;
            IF NEW.status = 'APPROVED' AND (
                   NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
                OR coalesce(auth.jwt() ->> 'aal', '') <> 'aal2') THEN
                RAISE EXCEPTION 'Aprobar pagos requiere OWNER/ADMIN con MFA'
                    USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
            END IF;
            IF NEW.status <> 'APPROVED'
               AND NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN,STAFF}')) THEN
                RAISE EXCEPTION 'Rechazar evidencias requiere OWNER, ADMIN o STAFF'
                    USING ERRCODE = '42501', HINT = 'ROLE_REQUIRED';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER trg_payment_evidence_guard
    BEFORE INSERT OR UPDATE ON public.payment_evidence
    FOR EACH ROW EXECUTE FUNCTION private.tg_payment_evidence_guard();

-- Columnas que el backend puede actualizar: estado y resultados de OCR/reglas.
-- Nunca la revisión (reviewed_by, reviewed_at, review_note) ni la identidad de la fila.
REVOKE UPDATE ON public.payment_evidence FROM app_backend;
GRANT UPDATE (status, storage_path, sha256, phash, bank_code, txn_number_normalized, amount_bs, txn_at,
              beneficiary_name, beneficiary_account_last4, glosa, ocr_model, ocr_confidence, ocr_raw,
              checks, duplicate_of)
    ON public.payment_evidence TO app_backend;
