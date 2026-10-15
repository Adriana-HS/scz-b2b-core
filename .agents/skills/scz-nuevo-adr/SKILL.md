---
name: scz-nuevo-adr
description: Redacta o actualiza un Architecture Decision Record (formato MADR) para SCZ-B2B-Core. Úsala cuando un cambio contradiga o extienda una decisión existente, se elija una tecnología o proveedor, o cambie una restricción externa.
---

# Nuevo ADR

1. Copia `docs/adr/0000-plantilla.md` a `docs/adr/NNNN-<titulo-en-kebab>.md` (siguiente número libre).
2. **Contexto:** hechos y restricciones con fuente y fecha (si es externo, agrégalo también a `docs/research/`).
3. **Decisión:** una o dos frases imperativas y verificables.
4. **Alternativas:** al menos 2, con pros y contras honestos.
5. **Consecuencias:** positivas, negativas y deuda asumida.
6. **Verificación:** la prueba, métrica o revisión que demuestra que se cumple.
7. Si reemplaza a otro ADR: marca el anterior como "Reemplazado por ADR-NNNN" (no lo borres).
8. Actualiza la tabla de ADRs de `SYSTEM_DESIGN.md` §3, el mapa de `docs/architecture/principios-y-patrones.md` si cambia un patrón, y `CHANGELOG.md`.
