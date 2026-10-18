# Evals golden del agente (#28)

Conversaciones **inventadas** por rubro (`importadora.yaml`, `clinica.yaml`) con lo que el bot debe y no debe hacer. Miden:
* **Q-ACC-5:** herramienta y argumentos correctos. Objetivo ≥ 95 % de casos que pasan.
* **Q-ACC-6:** respuestas con cifras que no vienen de una herramienta. Objetivo 0.

Un caso pasa solo si cumple **todo** lo esperado: las herramientas en orden y con sus argumentos, ninguna prohibida, el resultado del turno y lo que la respuesta dice o no dice. El formato está en `backend/agent_evals/suite.py`.

## En la CI (sin IA)

Se valida que la suite esté bien escrita (≥ 30 casos por rubro, todas las categorías, códigos que existen en los seeds), que el puntaje sea estricto y que el runner funcione contra la BD con un modelo guionado. No se llama a ninguna IA y no cuesta nada.

## A mano, con un modelo de verdad

Por decisión de AHS (18-oct-2026), con **Gemini gratis** y solo estos datos inventados (ADR-0019).

1. Base local con los seeds:
   ```bash
   supabase db reset
   ```
2. En `backend/.env` (nunca en el repositorio): `LLM_PROVIDER=gemini`, `LLM_API_KEY=<clave de AI Studio>`, `LLM_MODEL=gemini-3.8-flash` y los cuatro `LLM_PRICE_*`.
3. Correr:
   ```bash
   cd backend && SCZ_RUN_EVALS=1 uv run pytest tests/integration/test_agent_evals_live.py -s --no-cov
   ```
   Entre caso y caso hay una pausa de 6 s (`SCZ_EVALS_PAUSE_S`) por el límite de pedidos del plan gratuito. Una corrida completa tarda unos 7 minutos.
4. El reporte queda en `results/<rubro>-latest.md` (y `.json`), comparado con la corrida anterior: qué casos empezaron a fallar o a pasar. Se sube con el PR que cambia el prompt o el modelo.

Gemini no es el modelo de producción: lo que pase con Gemini no garantiza lo mismo con Claude Haiku 5.5 (ADR-0018).
