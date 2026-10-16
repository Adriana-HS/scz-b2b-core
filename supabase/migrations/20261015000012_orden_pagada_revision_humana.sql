-- ============================================================================
-- 12 · Una orden con pagos registrados no se cancela ni vence sin una persona
--
-- Falla corregida (seguimiento de SEC-02, issue #57):
--   fsm_transitions permite a BOT y SYSTEM llevar a CANCELLED una orden DRAFT,
--   QUOTED, HELD o AWAITING_PAYMENT, y a SYSTEM llevarla a EXPIRED. Los pagos se
--   aceptan desde QUOTED/HELD, así que una orden con un pago parcial aprobado
--   (amount_paid_bs > 0) podía quedar cancelada por el agente o vencida por
--   expire_stale_orders: dinero en payments sin orden viva y sin que nadie
--   decidiera el reembolso. La plataforma no custodia fondos (ADR-0013) y en el
--   MVP el reembolso lo hace el negocio a mano (ADR-0014): esa decisión es humana.
--
-- Corrección (un trigger, sin reemplazar funciones de otras migraciones):
--   a. -> CANCELLED con amount_paid_bs > 0: solo una persona OWNER/ADMIN con aal2.
--      BOT y SYSTEM reciben 42501 con HINT HUMAN_REVIEW_REQUIRED (el backend deriva
--      la conversación a una persona); STAFF, ROLE_REQUIRED; sin MFA, MFA_REQUIRED.
--      Coincide con trg_orders_human_guard (SEC-02) para personas y lo cubre si ese
--      trigger no existe.
--   b. -> EXPIRED con amount_paid_bs > 0: el trigger devuelve NULL y la fila no
--      cambia. Vencer es automático (solo SYSTEM): la orden sigue viva, con su
--      reserva de stock o de horario, para que OWNER/ADMIN decida (cancelar con
--      reembolso manual o completar el pago). No se lanza error para que una orden
--      pagada no frene el vencimiento de las demás en el mismo UPDATE.
-- Pruebas: supabase/tests/database/09_paid_order_exit.test.sql
-- ============================================================================

-- Se dispara después de trg_orders_guard (FSM) y trg_orders_human_guard: PostgreSQL
-- ejecuta los BEFORE del mismo evento en orden alfabético de nombre.
CREATE OR REPLACE FUNCTION private.tg_orders_paid_exit_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status
       OR NEW.status NOT IN ('CANCELLED', 'EXPIRED')
       OR greatest(OLD.amount_paid_bs, NEW.amount_paid_bs) = 0 THEN
        RETURN NEW;
    END IF;

    -- b. Una orden con pagos no vence por tiempo: queda para revisión humana
    IF NEW.status = 'EXPIRED' THEN
        RETURN NULL;
    END IF;

    -- a. Cancelar una orden con pagos implica decidir el reembolso
    IF private.actor_kind() <> 'HUMAN' THEN
        RAISE EXCEPTION 'La orden % tiene % Bs pagados: cancelarla lo decide OWNER o ADMIN',
            OLD.reference_code, OLD.amount_paid_bs USING ERRCODE = '42501', HINT = 'HUMAN_REVIEW_REQUIRED';
    END IF;
    IF NEW.organization_id NOT IN (SELECT private.user_org_ids('{OWNER,ADMIN}')) THEN
        RAISE EXCEPTION 'Cancelar la orden % con pagos requiere OWNER o ADMIN', OLD.reference_code
            USING ERRCODE = '42501', HINT = 'ROLE_REQUIRED';
    END IF;
    IF coalesce(auth.jwt() ->> 'aal', '') <> 'aal2' THEN
        RAISE EXCEPTION 'Cancelar la orden % con pagos requiere MFA', OLD.reference_code
            USING ERRCODE = '42501', HINT = 'MFA_REQUIRED';
    END IF;
    RETURN NEW;
END
$$;

COMMENT ON FUNCTION private.tg_orders_paid_exit_guard() IS
    'Orden con amount_paid_bs > 0: CANCELLED solo OWNER/ADMIN con aal2; EXPIRED se omite (revisión humana). Issue #57.';

CREATE TRIGGER trg_orders_paid_exit_guard
    BEFORE UPDATE OF status ON public.orders
    FOR EACH ROW EXECUTE FUNCTION private.tg_orders_paid_exit_guard();
