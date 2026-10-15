# Roadmap de implementación

Reemplaza la §7 de la v1 ("4 sesiones en 4 días"). Cada fase tiene **criterios de salida verificables**: una fase no se da por terminada solo porque el código existe.

| Fase | Objetivo | Entregables | Criterio de salida |
| :--- | :--- | :--- | :--- |
| 0 · Fundaciones | Repo, empresa y decisiones | Diseño v2.2, ADRs, migraciones, pgTAP, CI de BD; **acuerdo de socios, empresa (SEPREC + NIT), contador y abogado** (checklist de `docs/legal/`) | CI verde con `supabase test db`; checklist legal en curso |
| 1 · Núcleo backend | Webhook confiable | `UnitOfWork`, repositorios, webhook (HMAC, tenant, persistencia, cola), worker (claim/lease/DLQ), outbox con opt-in/opt-out, logs y trazas; onboarding de WhatsApp en **modelo directo** | Q-PERF-1 en k6; prueba de caos sin pérdida; pruebas de seguridad del webhook |
| 2 · Agente MVP (importadora) | Cotizar por WhatsApp | Tools `search_catalog`, `create_quote`, `handoff_to_human`; guardrails; PDF; seeds sintéticos; evals *golden* (≥ 30 conversaciones) | Q-ACC-5 ≥ 90 % en la primera iteración; 0 violaciones de grounding |
| 3 · Pagos | Cobrar con seguridad (sin manejar fondos) | QR estático del negocio + evidencia, reglas antifraude, bandeja de revisión con MFA | pgTAP 02 en verde; prueba con comprobantes manipulados; dataset OCR ≥ 100 |
| 4 · Agenda | Agendar con seña (genérica; el rubro clínicas se habilita en la fase 7) | `check_availability`, `hold_appointment`, recordatorios por plantilla con opt-in | pgTAP 03 en verde; evals de agenda |
| 5 · Dashboard | Operación del negocio | Login con MFA, TC, catálogo + CSV, órdenes/agenda, pagos, conversaciones | Pruebas E2E (Playwright) de los 5 flujos; accesibilidad AA |
| 6 · Piloto | 2 importadoras o ferreterías en Santa Cruz (ADR-0014) | Onboarding, runbooks, alertas, simulacro de restauración | 2 semanas con SLOs cumplidos y costo por tenant medido |
| 7 · Escala | Crecer con más automatización | Tech Provider de Meta, conector a la API QR del banco del negocio, conector a un proveedor SIAT autorizado, rubro clínicas | Cada conector con pruebas de contrato y opinión legal vigente |

## Uso de IA asistente para escribir código

Sirve para acelerar cualquier fase, con estas reglas: el diseño y los ADRs son la especificación; cada archivo generado entra por PR con pruebas; nada se acepta "sin placeholders" sin que CI lo verifique.

El backlog de cada fase está cargado como issues en GitHub (ver `docs/GESTION_DEL_PROYECTO.md` y `scripts/github/backlog.json`).
