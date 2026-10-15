---
name: scz-implementar-issue
description: Implementa un issue del backlog de SCZ-B2B-Core siguiendo el flujo, las reglas no negociables y la definición de terminado del proyecto. Úsala cuando te pidan trabajar en un issue, una historia o una tarea del roadmap de este repositorio.
---

# Implementar un issue de SCZ-B2B-Core

## 1. Entender (antes de escribir código)
1. Lee el issue completo: requisitos (RF-xx), ADRs y criterios de aceptación.
2. Abre `docs/requirements/SRS.md` y localiza cada RF citado.
3. Lee las secciones de `SYSTEM_DESIGN.md` y los ADRs referenciados. Si el issue contradice un ADR, **detente** y propón un ADR (skill `scz-nuevo-adr`).
4. Revisa `AGENTS.md` → "Reglas no negociables".
5. Escribe un plan de 3 a 8 pasos con los archivos que vas a tocar y compártelo antes de implementar si el cambio es grande.

## 2. Preparar
- Rama: `feat/<n>-<slug>` (o `fix/`, `docs/`, `chore/`).
- Si toca el esquema → usa la skill `scz-nueva-migracion`.

## 3. Pruebas primero
- BD: prueba pgTAP en `supabase/tests/database/NN_<tema>.test.sql` (incluye `\ir _fixtures.psql`, `BEGIN … ROLLBACK`).
- Python: pytest en `backend/tests/{unit,contract,integration,security}`; Hypothesis para dinero y FSM.
- IA: casos en `backend/evals/` si cambia el comportamiento del agente.

## 4. Implementar
- Dominio puro en `backend/app/domain` (sin I/O). Casos de uso en `application/`. Adaptadores en `infrastructure/`. Composición solo en `main.py` / `worker.py`.
- Puertos pequeños (ISP). Inyección por constructor (DIP). Nada de *service locator*.
- Errores de negocio con códigos estables (HINT de RPC → excepción de dominio).

## 5. Verificar (no declarar "listo" sin esto)
- Ejecuta las pruebas y pega el resumen real de la salida.
- `ruff`, `mypy --strict` e `import-linter` sin errores.
- Para cambios de seguridad, dinero o datos personales: aplica la skill `scz-revision-seguridad`.

## 6. Cerrar
- Actualiza la documentación afectada (diseño, ADR, SRS si cambia un RF) y `CHANGELOG.md`.
- PR con la plantilla, `Closes #<n>` y la lista de criterios de aceptación marcada.
