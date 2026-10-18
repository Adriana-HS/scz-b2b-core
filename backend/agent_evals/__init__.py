"""Evals golden del agente (#28, Q-ACC-5, Q-ACC-6, SYSTEM_DESIGN §7.5).

Conversaciones sintéticas por rubro (evals/conversations/*.yaml) con lo que el bot debe y no debe
hacer. El runner las corre con el orquestador real contra el catálogo de los seeds; el puntaje
dice qué casos pasan y el reporte los compara con la corrida anterior.

* En la CI (offline): se validan la suite, el puntaje y el runner con un modelo guionado.
* A mano (SCZ_RUN_EVALS=1): con el modelo configurado; por decisión de AHS, Gemini gratis y
  solo con datos inventados (ADR-0019). Ver docs/TESTING.md.
"""
