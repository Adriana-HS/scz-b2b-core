-- ============================================================================
-- 03 · Agenda sin doble reserva, búsqueda híbrida, vencimientos y cola durable
-- ============================================================================
BEGIN;
\ir _fixtures.psql
SELECT plan(18);

-- --- Agenda (clínica B) ---------------------------------------------------------
SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'BOT');
CREATE TEMP TABLE d AS SELECT (current_date + 7) AS day;
CREATE TEMP TABLE slots AS
SELECT * FROM public.available_slots('cb000000-0000-4000-8000-000000000001', (SELECT day FROM d));
SELECT is((SELECT count(*)::int FROM slots), 20, 'available_slots devuelve hasta 20 bloques libres');
SELECT is((SELECT min(slot_start) FROM slots),
          ((SELECT day FROM d) + time '08:00') AT TIME ZONE 'America/La_Paz',
          'primer bloque 08:00 hora de Bolivia (zona horaria del tenant)');

CREATE TEMP TABLE h1 AS
SELECT * FROM public.hold_appointment('cc000000-0000-4000-8000-00000000000b', 'c0000000-0000-4000-8000-00000000000b',
    'cb000000-0000-4000-8000-000000000001', 'eb000000-0000-4000-8000-000000000001',
    ((SELECT day FROM d) + time '09:00') AT TIME ZONE 'America/La_Paz');
SELECT is((SELECT total_bs FROM h1), 350.00::numeric(12,2), 'cita retenida con precio del servicio');
SELECT is((SELECT deposit_required_bs FROM h1), 105.00::numeric(12,2), 'seña 30% de la clínica');
SELECT throws_like($$ SELECT * FROM public.hold_appointment('cc000000-0000-4000-8000-00000000000b', NULL,
    'cb000000-0000-4000-8000-000000000001', 'eb000000-0000-4000-8000-000000000001',
    ((SELECT day FROM d) + time '09:15') AT TIME ZONE 'America/La_Paz') $$,
    '%ya fue tomado%', 'EXCLUDE impide la doble reserva solapada (09:15 vs 09:00-09:30)');
SELECT throws_like($$ SELECT * FROM public.hold_appointment('cc000000-0000-4000-8000-00000000000b', NULL,
    'cb000000-0000-4000-8000-000000000001', 'eb000000-0000-4000-8000-000000000001',
    ((SELECT day FROM d) + time '17:45') AT TIME ZONE 'America/La_Paz') $$,
    '%horario de atención%', 'no se agenda fuera del horario (17:45 + 30 min > 18:00)');
SELECT ok(NOT EXISTS (SELECT 1 FROM public.available_slots('cb000000-0000-4000-8000-000000000001', (SELECT day FROM d))
                      WHERE slot_start = ((SELECT day FROM d) + time '09:00') AT TIME ZONE 'America/La_Paz'),
          'el bloque retenido desaparece de la disponibilidad');
SELECT tests.reset();

-- Vencimiento de la retención libera el horario
UPDATE public.orders SET valid_until = now() - interval '1 minute' WHERE id = (SELECT order_id FROM h1);
SELECT is(private.expire_stale_orders(), 1, 'job de vencimiento expira la retención');
SELECT tests.as_backend('b0000000-0000-4000-8000-000000000000', 'BOT');
SELECT lives_ok($$ SELECT * FROM public.hold_appointment('cc000000-0000-4000-8000-00000000000b', NULL,
    'cb000000-0000-4000-8000-000000000001', 'eb000000-0000-4000-8000-000000000001',
    ((SELECT day FROM d) + time '09:00') AT TIME ZONE 'America/La_Paz') $$,
    'el horario liberado vuelve a reservarse');
SELECT tests.reset();

-- --- Búsqueda híbrida (importadora A) ----------------------------------------------
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT is((SELECT sku FROM public.search_catalog('oem 4451') LIMIT 1), 'OEM-4451',
          'SKU escrito distinto ("oem 4451") encuentra el código exacto primero');
SELECT is((SELECT sku FROM public.search_catalog('pastiya de freno hilux') LIMIT 1), 'OEM-4451',
          'texto con errores de tipeo encuentra el repuesto (FTS + trigram)');
SELECT is((SELECT sku FROM public.search_catalog('filtro aceite', array_fill(-0.10::real, ARRAY[1536])::extensions.vector) LIMIT 1),
          'FIL-0001', 'un ítem SIN embedding no queda descartado ni sale primero por NULL');
SELECT is((SELECT count(*)::int FROM public.search_catalog('%')), 0,
          'el comodín "%" ya no coincide con todo el catálogo');
SELECT is((SELECT unit_price_bs FROM public.search_catalog('OEM-4451') LIMIT 1), 292.00::numeric(12,2),
          'precio en Bs calculado por la misma función que la cotización');
SELECT tests.reset();
-- El TC vigente de la fixture tiene 2 h; con tolerancia de 1 h pasa a estar vencido
UPDATE public.organizations SET fx_max_age_hours = 1 WHERE id = 'a0000000-0000-4000-8000-000000000000';
SELECT tests.as_backend('a0000000-0000-4000-8000-000000000000', 'BOT');
SELECT is((SELECT price_status FROM public.search_catalog('OEM-4451') LIMIT 1), 'FX_STALE',
          'con TC vencido el precio USD no se ofrece (price_status = FX_STALE)');
SELECT tests.reset();

-- --- Cola durable: debounce y serialización por conversación ------------------------
-- Se reclama solo PROCESS_CONVERSATION, como el worker real: la cola es de toda la plataforma
-- y puede tener otros trabajos (los seeds dejan EMBED_ITEM en cola, #27).
SELECT private.enqueue_conversation('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 0, 0);
SELECT private.enqueue_conversation('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 0, 0);
SELECT is((SELECT count(*)::int FROM private.jobs WHERE kind = 'PROCESS_CONVERSATION' AND status = 'QUEUED'), 1,
          'ráfaga de mensajes = un solo trabajo en cola (debounce)');
SELECT is((SELECT count(*)::int FROM private.claim_jobs('worker-1', 10, ARRAY['PROCESS_CONVERSATION']) WHERE kind = 'PROCESS_CONVERSATION'), 1,
          'worker-1 reclama la conversación');
SELECT private.enqueue_conversation('a0000000-0000-4000-8000-000000000000', 'c0000000-0000-4000-8000-00000000000a', 0, 0);
SELECT is((SELECT count(*)::int FROM private.claim_jobs('worker-2', 10, ARRAY['PROCESS_CONVERSATION']) WHERE kind = 'PROCESS_CONVERSATION'), 0,
          'worker-2 NO procesa la misma conversación en paralelo (orden garantizado)');

SELECT * FROM finish();
ROLLBACK;
