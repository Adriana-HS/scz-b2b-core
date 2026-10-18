# Pruebas de carga y de caos

Demuestran dos requisitos del webhook de WhatsApp (#21, `SYSTEM_DESIGN.md` §2 y §13.1):

* **Q-PERF-1:** p99 del ack (recepción → `200 OK`) ≤ 250 ms con **50 mensajes nuevos por segundo** sostenidos.
* **Q-REL-1:** ningún mensaje aceptado con 200 se pierde, ni siquiera si Postgres se reinicia o el proceso api muere en medio de la carga.

El código está en `backend/loadtest/`, y la CI lo corre en el workflow **Carga y caos** (`.github/workflows/resiliencia.yml`): cada noche, a mano y en los PRs que tocan el camino del webhook.

## Escenarios

| Modo | Qué pasa | Pasa si |
| :--- | :--- | :--- |
| `load` | 50 rps durante 3 min | p99 del primer intento ≤ 250 ms, errores ≤ 0,1 %, k6 sostiene la tasa (≤ 1 % de iteraciones sin lanzar) y se cumplen los invariantes de la BD |
| `chaos-db` | Postgres se reinicia (`docker restart`) a un tercio de la corrida | Ningún mensaje abandonado, la falla **se notó** (algún primer intento falló) y se cumplen los invariantes de la BD |
| `chaos-api` | El proceso api muere con SIGKILL a un tercio de la corrida y vuelve 3 s después | Lo mismo que `chaos-db` |

**Invariantes de la BD, en todos los modos.** Los cuenta `run.py` al terminar, por el prefijo `wamid.load.<corrida>.`:
* cada mensaje aceptado con 200 está en `public.messages`: **0 perdidos**;
* ninguna fila repetida, y nunca más filas que mensajes únicos enviados, aunque se reenvíen duplicados: **0 duplicados**;
* un evento en `private.inbound_events` por mensaje: el registro fue **atómico**;
* cada conversación con mensajes tiene su trabajo `PROCESS_CONVERSATION`: **nada quedó sin procesar**.

La regla de "la falla se notó" evita un falso verde. Si el reinicio no llegó a afectar ningún request, la corrida no demostró nada y sale en rojo.

## Cómo simula a Meta

`loadtest/k6/webhook.js` (k6 1.3.0, imagen oficial fijada por digest):
* **Mensajes:** cada iteración es un mensaje nuevo con el formato de Meta, firmado con HMAC-SHA256 con el App Secret sintético de la corrida. Hay 500 clientes sintéticos, así que llegan varios mensajes por conversación.
* **Reintentos:** si la respuesta no es 200, reintenta con backoff (0,5 s → 5 s) hasta 90 s, como Meta. Solo el **primer intento** cuenta para el p99.
* **Reenvíos:** el 5 % de los mensajes ya aceptados se reenvía: Meta entrega *al menos una vez*.

## Cómo correrlo en local

Requiere Docker, `supabase db start` (como para las pruebas de integración) y, desde `backend/`:

```bash
uv run python -m loadtest.run load --duration 1m --out ../load-out/load
```

```bash
uv run python -m loadtest.run chaos-db --rate 5 --duration 60s --out ../load-out/chaos-db
```

```bash
uv run python -m loadtest.run chaos-api --rate 5 --duration 60s --out ../load-out/chaos-api
```

* **Qué deja:** `run.py` crea un negocio y un número sintéticos por corrida, habilita el login de `app_backend` con una contraseña aleatoria (al final lo deshabilita), levanta la api con uvicorn y calienta el pool. Al terminar, deja en `--out` el reporte, `summary.json` de k6 y los logs de la api y de k6. Los datos quedan en la BD local; `supabase db reset` los borra.
* **Seguridad:** igual que las pruebas de integración, se niega a correr contra una BD que no sea local.
* **Windows y macOS:** la latencia local **no sirve para el SLO**. Con Docker Desktop, cada viaje a Postgres pasa por el reenvío de puertos de su VM. En la máquina de desarrollo medimos ~49 ms por `SELECT 1`. El webhook hace ~9 viajes por request (resolver el número, BEGIN, contexto, evento, cliente, conversación, mensaje, trabajo y COMMIT), así que tarda ~430 ms, y a 50 rps se encola. El reporte muestra esa latencia ("Ida y vuelta a la BD") para que se vea la causa. Los escenarios de caos sí sirven en local a tasa baja (`--rate 5`). El reloj de la VM de Docker Desktop puede saltar: algún "máximo hasta el 200" absurdo es un artefacto, como se ve al compararlo con `iteration_duration`.
* **Linux (CI):** k6 usa la red del host y Postgres responde en fracciones de milisegundo. Ahí se mide el SLO.

## Qué mide y qué no

* **Server time del webhook en la máquina de la CI**, con Postgres local, no producción. En producción, la latencia del webhook es ~9 × la latencia BD↔cómputo. Por eso #11 exige medir esa latencia (p50 < 5 ms) y por eso existe la métrica `webhook_ack_ms` con su alerta `WebhookAckP99High` (`docs/observability.md`).
* **No incluye el worker:** `PROCESS_CONVERSATION` todavía no tiene handler (#81). La prueba de caos del worker, que lo mata a mitad de un trabajo sin perder ni duplicar, corre en cada PR (`tests/integration/test_worker_db.py`).
* **Cuándo se rompe la cadena.** Un cambio que agregue viajes a la BD en el webhook, que haga el ack antes de persistir o que deje un mensaje sin su trabajo hace fallar este workflow. Se comprobó con mutaciones: responder 200 sin haber persistido → `chaos-db` detecta 8 mensajes perdidos; no encolar el trabajo → `load` detecta 100 conversaciones sin trabajo.
