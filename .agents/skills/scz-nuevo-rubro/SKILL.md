---
name: scz-nuevo-rubro
description: Agrega un nuevo rubro de negocio (vertical) a SCZ-B2B-Core implementando VerticalPolicy sin modificar el dominio ni los casos de uso. Úsala cuando se quiera soportar un tipo de negocio nuevo (por ejemplo, distribuidora mayorista o veterinaria).
---

# Nuevo rubro (Strategy: VerticalPolicy)

1. Migración: agregar el valor al enum `public.business_vertical` (skill `scz-nueva-migracion`).
2. Crear `backend/app/verticals/<rubro>/` con:
   - `policy.py`: clase que implementa `VerticalPolicy` (prompt de sistema, herramientas habilitadas, reglas propias).
   - `config_schema.py`: modelo Pydantic versionado para `organizations.config` (`config_schema_version`).
   - `prompts/`: plantilla del prompt de sistema; incluye el alcance del negocio (política de Meta sobre IA) y el aviso de IA.
   - `templates.yaml`: plantillas de WhatsApp (utilidad) del rubro.
3. Registrar la política en el *composition root* (`ProviderFactory`).
4. **No** modificar `domain/` ni `application/`. Si hace falta, es señal de que falta un punto de extensión → ADR.
5. Seeds sintéticos del rubro en `supabase/seed/`.
6. Evals: ≥ 30 conversaciones *golden* en `backend/evals/conversations/<rubro>/`, incluidos casos fuera de alcance.
7. Documentar el rubro en `SYSTEM_DESIGN.md` §7.3 y en `CHANGELOG.md`.
