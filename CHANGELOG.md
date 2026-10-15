# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Agregado (v2.1)
- ADR-0012 (IVA por régimen vigente, Ley 1733) y ADR-0013 (la plataforma no custodia fondos).
- Modelo de IVA en la BD: `tax_regimes`, `orders.subtotal_bs/iva_bs/tax_regime_id`, `private.tax_breakdown`; 11 pruebas pgTAP nuevas (74 en total).
- Registro de restricciones externas (`docs/research/`), SRS con RF-xx, principios SOLID, patrones y UML (`docs/architecture/`).
- Gestión del proyecto: plantillas de issues, backlog de 42 issues en 8 fases y script `scripts/github/bootstrap.py`.
- `AGENTS.md`, `CLAUDE.md` y 5 skills del proyecto en `.agents/skills/`; guía `docs/HERRAMIENTAS_IA.md`.

### Agregado
- `SYSTEM_DESIGN.md` v2.0: requisitos medibles, arquitectura C4, FSM, agente IA, seguridad STRIDE, SLOs, costos.
- 11 ADRs en `docs/adr/`.
- Revisión de la v1 con evidencia reproducible (`docs/reviews/`).
- Esquema de base de datos en 7 migraciones (`supabase/migrations/`): multi-tenancy con RLS también para el backend, FK compuestas, historial de tipo de cambio, cotización atómica con reserva de stock, agenda sin doble reserva, evidencias y libro de pagos, auditoría, cola durable y outbox.
- 63 pruebas pgTAP (`supabase/tests/database/`).
- CI de base de datos, plantilla de PR, guías de seguridad, contribución y pruebas.

### Cambiado
- La sección "Guía de ejecución en Claude Pro" de la v1 pasa a `docs/ROADMAP.md` como plan por fases.
