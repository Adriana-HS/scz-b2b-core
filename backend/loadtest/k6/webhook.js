// Carga y caos del webhook de WhatsApp (#21, Q-PERF-1, Q-REL-1).
//
// Lo lanza loadtest/run.py, que prepara un negocio sintético, levanta la api y verifica la BD
// al final. No correr contra staging ni producción: los mensajes son sintéticos pero reales
// para la BD que los recibe.
//
// Cada iteración es UN mensaje nuevo, firmado con HMAC-SHA256 como lo firma Meta:
//   * si la respuesta no es 200, se reintenta con backoff, como hace Meta, hasta RETRY_DEADLINE_S;
//   * con probabilidad DUP_RATIO, el mismo mensaje ya aceptado se reenvía (Meta entrega
//     al menos una vez): no debe crear filas nuevas.
// La latencia del SLO se mide solo en el PRIMER intento (first_attempt_ack_ms).

import crypto from 'k6/crypto';
import exec from 'k6/execution';
import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import { sleep } from 'k6';

const TARGET = __ENV.TARGET || 'http://127.0.0.1:8765';
const SECRET = __ENV.WEBHOOK_SECRET;
const PHONE_NUMBER_ID = __ENV.PHONE_NUMBER_ID;
const RUN_ID = __ENV.RUN_ID;
const RATE = Number(__ENV.RATE || 50);
const DURATION = __ENV.DURATION || '3m';
const SENDERS = Number(__ENV.SENDERS || 500);
const DUP_RATIO = Number(__ENV.DUP_RATIO || 0.05);
const RETRY_DEADLINE_MS = Number(__ENV.RETRY_DEADLINE_S || 90) * 1000;
const SUMMARY_PATH = __ENV.SUMMARY_PATH || '/out/summary.json';

if (!SECRET || !PHONE_NUMBER_ID || !RUN_ID) {
  throw new Error('faltan WEBHOOK_SECRET, PHONE_NUMBER_ID o RUN_ID (los fija loadtest/run.py)');
}

const URL = `${TARGET}/v1/webhooks/whatsapp`;
const QUERIES = [
  'pastilla de freno hilux 2015',
  'amortiguador trasero corolla',
  'filtro de aceite para nissan frontier',
  '¿tienen kit de embrague para l200?',
  'precio de bujías ngk',
];

const messagesSent = new Counter('messages_sent');
const messagesAcked = new Counter('messages_acked');
const messagesGivenUp = new Counter('messages_given_up');
const duplicatesSent = new Counter('duplicates_sent');
const firstAttemptFailed = new Rate('first_attempt_failed');
const firstAttemptAckMs = new Trend('first_attempt_ack_ms', true);
const timeToAckMs = new Trend('time_to_ack_ms', true);

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  scenarios: {
    webhook: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.max(RATE, 20),
      // Con caos, cada mensaje puede quedar reintentando: hacen falta VUs de sobra para que la
      // tasa de mensajes NUEVOS no baje mientras dura la falla.
      maxVUs: Math.max(RATE * 12, 200),
      gracefulStop: `${Math.ceil(RETRY_DEADLINE_MS / 1000) + 15}s`,
    },
  },
};

function payload(wamid, waId) {
  const text = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  return JSON.stringify({
    object: 'whatsapp_business_account',
    entry: [{
      id: 'WABA-LOAD',
      changes: [{
        field: 'messages',
        value: {
          messaging_product: 'whatsapp',
          metadata: { display_phone_number: '59100000000', phone_number_id: PHONE_NUMBER_ID },
          contacts: [{ profile: { name: 'Cliente de carga' }, wa_id: waId }],
          messages: [{
            from: waId,
            id: wamid,
            timestamp: String(Math.floor(Date.now() / 1000)),
            type: 'text',
            text: { body: text },
          }],
        },
      }],
    }],
  });
}

function post(body, signature, attempt) {
  return http.post(URL, body, {
    headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': signature },
    tags: { attempt },
    timeout: '15s',
  });
}

export default function () {
  const n = exec.scenario.iterationInTest;
  const wamid = `wamid.load.${RUN_ID}.${n}`;
  // wa_id sintético: 591 + 8 dígitos (cumple el CHECK de customers)
  const waId = `5917${String(n % SENDERS).padStart(7, '0')}`;
  const body = payload(wamid, waId);
  const signature = `sha256=${crypto.hmac('sha256', SECRET, body, 'hex')}`;

  messagesSent.add(1);
  const started = Date.now();
  let delay = 0.5;
  for (let attempt = 0; ; attempt++) {
    const res = post(body, signature, attempt === 0 ? 'first' : 'retry');
    if (attempt === 0) {
      firstAttemptFailed.add(res.status !== 200);
      if (res.status === 200) firstAttemptAckMs.add(res.timings.duration);
    }
    if (res.status === 200) {
      messagesAcked.add(1);
      timeToAckMs.add(Date.now() - started);
      break;
    }
    if (Date.now() - started > RETRY_DEADLINE_MS) {
      messagesGivenUp.add(1);
      return;
    }
    sleep(delay);
    delay = Math.min(delay * 2, 5);
  }

  if (Math.random() < DUP_RATIO) {
    duplicatesSent.add(1);
    post(body, signature, 'duplicate');
  }
}

export function handleSummary(data) {
  return {
    [SUMMARY_PATH]: JSON.stringify(data),
    stdout: `k6: resumen escrito en ${SUMMARY_PATH}\n`,
  };
}
