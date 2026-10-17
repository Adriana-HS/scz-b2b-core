-- ============================================================================
-- 12 · Cola durable para el worker (#17, ADR-0005)
--   * fencing: solo quien tiene el lease cierra el trabajo
--   * el worker reclama solo los tipos que sabe procesar
--   * a lo sumo un trabajo por conversación en cada reclamo
--   * finish_job y el reaper informan cuándo un trabajo pasa a DEAD (alerta)
-- Tipos T_* propios de la prueba: no se mezclan con los EMBED_ITEM de las fixtures.
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(20);

INSERT INTO private.jobs (id, organization_id, kind, conversation_key, max_attempts) OVERRIDING SYSTEM VALUE VALUES
    (9001, 'a0000000-0000-4000-8000-000000000000', 'T_TURNO',   'conv-1', 8),
    (9002, 'a0000000-0000-4000-8000-000000000000', 'T_OTRO',    'conv-1', 8),  -- misma conversación
    (9003, 'a0000000-0000-4000-8000-000000000000', 'T_TURNO',   'conv-2', 2),
    (9004, 'a0000000-0000-4000-8000-000000000000', 'T_SIN_HANDLER', NULL, 8);

-- --- Reclamo ----------------------------------------------------------------------
SELECT is((SELECT count(*)::int FROM private.claim_jobs('w-a', 10, ARRAY[]::text[])), 0,
          'sin tipos registrados el worker no reclama nada (un worker sin handler no manda mensajes a DEAD)');
CREATE TEMP TABLE c1 AS SELECT * FROM private.claim_jobs('w-a', 10, ARRAY['T_TURNO', 'T_OTRO']);
SELECT is((SELECT count(*)::int FROM c1), 2, 'reclama dos trabajos: uno por cada conversación');
SELECT is((SELECT count(*)::int FROM c1 WHERE conversation_key = 'conv-1'), 1,
          'a lo sumo un trabajo por conversación en un mismo reclamo (orden por conversación)');
SELECT ok(NOT EXISTS (SELECT 1 FROM c1 WHERE kind = 'T_SIN_HANDLER'), 'no toma tipos que no pidió');
SELECT ok((SELECT bool_and(status = 'RUNNING' AND locked_by = 'w-a' AND attempts = 1 AND locked_at IS NOT NULL) FROM c1),
          'el reclamo deja el lease: RUNNING, locked_by, locked_at y attempts + 1');
SELECT is((SELECT count(*)::int FROM private.claim_jobs('w-b', 10, ARRAY['T_TURNO', 'T_OTRO'])), 0,
          'otro worker no toma la conversación que ya está en curso');

-- --- Cierre con fencing -------------------------------------------------------------
SELECT is(private.finish_job(9001, 'w-b', true), NULL::text,
          'un worker que no tiene el lease no puede cerrar el trabajo');
SELECT is((SELECT status FROM private.jobs WHERE id = 9001), 'RUNNING', '…y el trabajo sigue en curso');
SELECT is(private.finish_job(9001, 'w-a', true), 'DONE', 'quien tiene el lease lo cierra como DONE');
SELECT is(private.finish_job(9003, 'w-a', false, repeat('x', 900)), 'QUEUED', 'un fallo con intentos restantes vuelve a la cola');
SELECT ok((SELECT run_after > now() AND locked_by IS NULL AND length(last_error) = 500 FROM private.jobs WHERE id = 9003),
          'con backoff, sin lease y con el error truncado a 500 caracteres');

-- Segundo intento de 9003 (max_attempts = 2): agota los intentos
UPDATE private.jobs SET run_after = now() WHERE id = 9003;
SELECT is((SELECT count(*)::int FROM private.claim_jobs('w-a', 10, ARRAY['T_TURNO'])), 1, 'se reintenta');
SELECT is(private.finish_job(9003, 'w-a', false, 'TimeoutError'), 'DEAD',
          'con los intentos agotados pasa a DEAD y lo informa (alerta)');

-- Error permanente: DEAD al primer intento, sin reintentos inútiles
INSERT INTO private.jobs (id, kind) OVERRIDING SYSTEM VALUE VALUES (9005, 'T_PERMANENTE');
CREATE TEMP TABLE c2 AS SELECT * FROM private.claim_jobs('w-a', 10, ARRAY['T_PERMANENTE']);
SELECT is(private.finish_job(9005, 'w-a', false, 'PermanentJobError', false), 'DEAD',
          'p_retry = false lo manda a DEAD aunque queden intentos');

-- --- Reaper: un worker que murió deja trabajos RUNNING ------------------------------
INSERT INTO private.jobs (id, kind, status, attempts, max_attempts, locked_by, locked_at) OVERRIDING SYSTEM VALUE VALUES
    (9006, 'T_REAPER', 'RUNNING', 1, 8, 'w-muerto', now() - interval '10 minutes'),
    (9007, 'T_REAPER', 'RUNNING', 8, 8, 'w-muerto', now() - interval '10 minutes'),
    (9008, 'T_REAPER', 'RUNNING', 1, 8, 'w-vivo',   now());
SELECT results_eq($$ SELECT requeued, dead FROM private.requeue_stale_jobs(interval '5 minutes') $$,
                  $$ VALUES (1, 1) $$,
                  'el reaper devuelve cuántos reintenta y cuántos pasan a DEAD; no toca el lease vigente');
SELECT is((SELECT status FROM private.jobs WHERE id = 9008), 'RUNNING', 'un lease vigente no se toca');

-- El worker "muerto" vuelve tarde: su cierre se ignora porque otro ya tiene el trabajo
SELECT is((SELECT count(*)::int FROM private.claim_jobs('w-nuevo', 10, ARRAY['T_REAPER'])), 1,
          'otro worker reclama el trabajo liberado');
SELECT is(private.finish_job(9006, 'w-muerto', true), NULL::text,
          'el cierre tardío del worker que perdió el lease se ignora (sin doble procesamiento)');

-- --- Firmas viejas eliminadas y privilegios --------------------------------------------
SELECT ok(to_regprocedure('private.finish_job(bigint, boolean, text)') IS NULL
          AND to_regprocedure('private.claim_jobs(text, integer)') IS NULL,
          'no quedan las firmas sin fencing ni filtro por tipo');
SELECT ok(has_function_privilege('app_backend', 'private.claim_jobs(text, integer, text[])', 'EXECUTE')
          AND has_function_privilege('app_backend', 'private.finish_job(bigint, text, boolean, text, boolean)', 'EXECUTE')
          AND NOT has_function_privilege('authenticated', 'private.finish_job(bigint, text, boolean, text, boolean)', 'EXECUTE')
          AND NOT has_function_privilege('anon', 'private.claim_jobs(text, integer, text[])', 'EXECUTE')
          AND NOT has_function_privilege('authenticated', 'private.requeue_stale_jobs(interval)', 'EXECUTE'),
          'solo app_backend opera la cola');

SELECT * FROM finish();
ROLLBACK;
