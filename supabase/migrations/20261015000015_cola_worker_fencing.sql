-- ============================================================================
-- 15 · Cola durable lista para el worker (#17, ADR-0005)
--
-- Huecos de las funciones de la migración 06, cerrados antes de poner un worker:
--   1. finish_job no comprobaba quién tenía el lease. Un worker que se colgó (su lease
--      venció y el reaper reasignó el trabajo) podía cerrar el trabajo de otro: doble
--      procesamiento o un DONE falso. Ahora exige p_worker = locked_by (fencing).
--   2. claim_jobs reclamaba cualquier tipo. Un worker sin handler para PROCESS_CONVERSATION
--      lo fallaría 8 veces y lo mandaría a DEAD: mensajes de clientes perdidos. Ahora
--      recibe los tipos que sabe procesar (p_kinds; NULL = todos, por compatibilidad).
--   3. Dos trabajos QUEUED de la misma conversación (de distinto tipo) podían salir en el
--      mismo reclamo y correr en paralelo. Ahora sale a lo sumo uno por conversation_key.
--   4. finish_job y requeue_stale_jobs no informaban los pasos a DEAD. Ahora devuelven
--      el estado nuevo y los conteos, para que el worker emita la alerta (§10.2).
--   5. finish_job admite p_retry = false: un error permanente va directo a DEAD.
-- El error se guarda truncado a 500 caracteres; el worker solo envía el tipo de error,
-- nunca el mensaje (puede traer datos del cliente).
-- Pruebas: supabase/tests/database/12_worker_queue.test.sql
-- ============================================================================

-- Firmas viejas: se eliminan para que nadie pueda cerrar trabajos sin fencing.
-- (pg_cron, si está habilitado, llama a requeue_stale_jobs() sin argumentos: sigue funcionando.)
DROP FUNCTION IF EXISTS private.claim_jobs(text, integer);
DROP FUNCTION IF EXISTS private.finish_job(bigint, boolean, text);
DROP FUNCTION IF EXISTS private.requeue_stale_jobs(interval);

CREATE FUNCTION private.claim_jobs(p_worker text, p_batch integer DEFAULT 10, p_kinds text[] DEFAULT NULL)
RETURNS SETOF private.jobs
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    WITH candidates AS (
        SELECT j.id, j.conversation_key, j.run_after
        FROM private.jobs j
        WHERE j.status = 'QUEUED' AND j.run_after <= now()
          AND (p_kinds IS NULL OR j.kind = ANY (p_kinds))
          AND (j.conversation_key IS NULL OR NOT EXISTS (
                SELECT 1 FROM private.jobs r
                WHERE r.status = 'RUNNING' AND r.conversation_key = j.conversation_key))
        ORDER BY j.run_after, j.id
        LIMIT least(greatest(coalesce(p_batch, 10), 1), 100)
        FOR UPDATE SKIP LOCKED
    ),
    -- Uno por conversación: los demás quedan QUEUED para el próximo reclamo
    picked AS (
        SELECT DISTINCT ON (coalesce(c.conversation_key, c.id::text)) c.id
        FROM candidates c
        ORDER BY coalesce(c.conversation_key, c.id::text), c.run_after, c.id
    )
    UPDATE private.jobs j
       SET status = 'RUNNING', locked_at = now(), locked_by = p_worker, attempts = j.attempts + 1
      FROM picked WHERE j.id = picked.id
    RETURNING j.*
$$;

-- Devuelve el estado nuevo (DONE, QUEUED o DEAD), o NULL si p_worker no tiene el lease.
CREATE FUNCTION private.finish_job(
    p_job_id bigint, p_worker text, p_ok boolean, p_error text DEFAULT NULL, p_retry boolean DEFAULT true)
RETURNS text
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    UPDATE private.jobs j SET
        status     = CASE WHEN p_ok THEN 'DONE'
                          WHEN NOT p_retry OR j.attempts >= j.max_attempts THEN 'DEAD'
                          ELSE 'QUEUED' END,
        -- backoff exponencial con tope de 10 min (solo si vuelve a la cola)
        run_after  = CASE WHEN NOT p_ok AND p_retry AND j.attempts < j.max_attempts
                          THEN now() + least(interval '10 minutes', make_interval(secs => power(2, j.attempts)))
                          ELSE j.run_after END,
        last_error = CASE WHEN p_ok THEN NULL ELSE left(p_error, 500) END,
        locked_at  = NULL,
        locked_by  = NULL
    WHERE j.id = p_job_id AND j.status = 'RUNNING' AND j.locked_by = p_worker
    RETURNING j.status
$$;

-- Reaper: un worker que murió deja trabajos RUNNING con el lease vencido.
CREATE FUNCTION private.requeue_stale_jobs(p_timeout interval DEFAULT interval '5 minutes')
RETURNS TABLE (requeued integer, dead integer)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $$
    WITH s AS (
        UPDATE private.jobs
           SET status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'QUEUED' END,
               locked_at = NULL, locked_by = NULL, last_error = 'lease expired'
         WHERE status = 'RUNNING' AND locked_at < now() - p_timeout
        RETURNING status)
    SELECT count(*) FILTER (WHERE status = 'QUEUED')::integer,
           count(*) FILTER (WHERE status = 'DEAD')::integer
    FROM s
$$;

REVOKE ALL ON FUNCTION private.claim_jobs(text, integer, text[]),
                       private.finish_job(bigint, text, boolean, text, boolean),
                       private.requeue_stale_jobs(interval)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION private.claim_jobs(text, integer, text[]),
                          private.finish_job(bigint, text, boolean, text, boolean),
                          private.requeue_stale_jobs(interval)
    TO app_backend;
