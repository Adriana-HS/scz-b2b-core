-- ============================================================================
-- 09 · SEC-01: MFA obligatorio e incondicional para aprobar pagos
--
-- Falla corregida (revisión del 15-oct-2026, reproducida en BD):
--   1. OWNER/ADMIN con sesión aal1 podía actualizar organizations.config.
--   2. approve_payment_evidence solo exigía aal2 si config.require_mfa_for_payments
--      no era false. Juntas: una sesión robada sin segundo factor aprobaba pagos.
--
-- Corrección (defensa en profundidad, tres capas):
--   a. La función exige aal2 siempre; ya no lee ningún interruptor.
--   b. Editar la organización (seña, TC, configuración) exige aal2, igual que las
--      cuentas bancarias receptoras.
--   c. La clave require_mfa_for_payments queda prohibida en config, para que nadie
--      crea que el control se puede apagar.
-- Pruebas: supabase/tests/database/06_payment_mfa.test.sql
-- ============================================================================

-- c. Datos heredados y restricción ------------------------------------------------
UPDATE public.organizations
   SET config = config - 'require_mfa_for_payments'
 WHERE config ? 'require_mfa_for_payments';

ALTER TABLE public.organizations
    ADD CONSTRAINT organizations_config_no_mfa_override
    CHECK (NOT (config ? 'require_mfa_for_payments'));

-- b. Editar la organización exige MFA ---------------------------------------------
DROP POLICY IF EXISTS org_admin_update ON public.organizations;
CREATE POLICY org_admin_update ON public.organizations FOR UPDATE TO authenticated
    USING (id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
           AND (SELECT auth.jwt() ->> 'aal') = 'aal2')
    WITH CHECK (id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
                AND (SELECT auth.jwt() ->> 'aal') = 'aal2');

-- b2. Mismo tipo de falla por otros caminos (hallados al revisar este fix):
--   * Membresías: un OWNER aal1 podía dar de alta o ascender un ADMIN (por ejemplo,
--     una cuenta del atacante con su propio MFA) y aprobar pagos desde ahí.
--   * Tipo de cambio: la política no exigía aal2 aunque el diseño lo pide; un TC
--     falso cambia todos los precios en Bs.
DROP POLICY IF EXISTS members_owner_insert ON public.organization_members;
DROP POLICY IF EXISTS members_owner_update ON public.organization_members;
DROP POLICY IF EXISTS members_owner_delete ON public.organization_members;
CREATE POLICY members_owner_insert ON public.organization_members FOR INSERT TO authenticated
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER}'))
                AND (SELECT auth.jwt() ->> 'aal') = 'aal2');
CREATE POLICY members_owner_update ON public.organization_members FOR UPDATE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER}')) AND user_id <> (SELECT auth.uid())
           AND (SELECT auth.jwt() ->> 'aal') = 'aal2')
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER}'))
                AND (SELECT auth.jwt() ->> 'aal') = 'aal2');
CREATE POLICY members_owner_delete ON public.organization_members FOR DELETE TO authenticated
    USING (organization_id IN (SELECT private.user_org_ids('{OWNER}')) AND user_id <> (SELECT auth.uid())
           AND (SELECT auth.jwt() ->> 'aal') = 'aal2');

DROP POLICY IF EXISTS fx_admin_insert ON public.exchange_rates;
CREATE POLICY fx_admin_insert ON public.exchange_rates FOR INSERT TO authenticated
    WITH CHECK (organization_id IN (SELECT private.user_org_ids('{OWNER,ADMIN}'))
                AND created_by = (SELECT auth.uid())
                AND (SELECT auth.jwt() ->> 'aal') = 'aal2');

-- a. Aprobación de evidencias: aal2 siempre ----------------------------------------
CREATE OR REPLACE FUNCTION public.approve_payment_evidence(
    p_evidence_id uuid,
    p_amount_bs   numeric DEFAULT NULL,
    p_note        text DEFAULT NULL
)
RETURNS public.order_status
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_uid    uuid := auth.uid();
    v_ev     public.payment_evidence%ROWTYPE;
    v_amount numeric(12,2);
    v_status public.order_status;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'Requiere sesión' USING ERRCODE = '42501', HINT = 'AUTH_REQUIRED';
    END IF;
    -- Antes de leer nada: sin segundo factor no hay aprobación (sin excepciones por tenant)
    IF coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' THEN
        RAISE EXCEPTION 'Aprobar pagos requiere MFA' USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
    END IF;
    SELECT * INTO v_ev FROM public.payment_evidence WHERE id = p_evidence_id FOR UPDATE;
    IF NOT FOUND OR v_ev.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}')) THEN
        RAISE EXCEPTION 'Evidencia no encontrada' USING ERRCODE = '42501', HINT = 'NOT_FOUND';
    END IF;
    IF v_ev.status NOT IN ('AUTO_CHECK_PASSED', 'NEEDS_REVIEW') THEN
        RAISE EXCEPTION 'Evidencia en estado %', v_ev.status USING ERRCODE = 'P0001', HINT = 'INVALID_STATE';
    END IF;
    v_amount := coalesce(p_amount_bs, v_ev.amount_bs);
    IF v_amount IS NULL OR v_amount <= 0 THEN
        RAISE EXCEPTION 'Monto inválido' USING ERRCODE = 'P0001', HINT = 'INVALID_AMOUNT';
    END IF;

    UPDATE public.payment_evidence
       SET status = 'APPROVED', reviewed_by = v_uid, reviewed_at = now(), review_note = p_note
     WHERE id = v_ev.id;

    INSERT INTO public.payments (organization_id, order_id, evidence_id, channel, amount_bs,
                                 bank_code, txn_number, confirmed_via, confirmed_by)
    VALUES (v_ev.organization_id, v_ev.order_id, v_ev.id, v_ev.channel, v_amount,
            v_ev.bank_code, v_ev.txn_number_normalized, 'HUMAN', v_uid);

    -- Pago parcial sin otras evidencias pendientes: vuelve a esperar pago
    UPDATE public.orders o SET status = 'AWAITING_PAYMENT'
    WHERE o.id = v_ev.order_id AND o.organization_id = v_ev.organization_id
      AND o.status = 'PAYMENT_REVIEW'
      AND NOT EXISTS (SELECT 1 FROM public.payment_evidence e
                      WHERE e.order_id = o.id AND e.status IN ('RECEIVED', 'OCR_DONE', 'AUTO_CHECK_PASSED', 'NEEDS_REVIEW'));

    SELECT status INTO v_status FROM public.orders WHERE id = v_ev.order_id;
    RETURN v_status;
END
$$;
