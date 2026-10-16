-- ============================================================================
-- 13 · expire_stale_orders no toma las órdenes con pagos registrados
--
-- Seguimiento de #57: trg_orders_paid_exit_guard (migración 12) omite el paso a
-- EXPIRED de una orden con amount_paid_bs > 0 devolviendo NULL. El job seguía
-- seleccionando esas órdenes cada minuto: el trigger se ejecutaba y la fila quedaba
-- bloqueada en cada pasada (contención con quien la esté revisando o pagando) sin
-- ningún efecto.
--
-- Corrección: mismo cuerpo que en la migración 10 más `AND amount_paid_bs = 0` en el
-- WHERE. El trigger se queda como defensa en profundidad para cualquier otro UPDATE
-- a EXPIRED. CREATE OR REPLACE conserva los privilegios de la migración 06.
-- Pruebas: supabase/tests/database/10_expire_skips_paid.test.sql (y la 09 sin cambios)
-- ============================================================================

CREATE OR REPLACE FUNCTION private.expire_stale_orders()
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_count integer;
BEGIN
    PERFORM set_config('app.actor', 'SYSTEM', true);
    -- PAYMENT_REVIEW no vence por tiempo: un humano decide (ADR-0008).
    -- Con pagos registrados tampoco: decide OWNER/ADMIN (#57, migración 12).
    UPDATE public.orders SET status = 'EXPIRED'
    WHERE status IN ('QUOTED', 'HELD', 'AWAITING_PAYMENT') AND valid_until < now()
      AND amount_paid_bs = 0;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$;
