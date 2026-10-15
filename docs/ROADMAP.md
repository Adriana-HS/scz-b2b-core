# Roadmap de implementación

Reemplaza la §7 de la v1 ("4 sesiones en 4 días"). Cada fase tiene **criterios de salida verificables**: una fase no se da por terminada solo porque el código existe.

| Fase | Objetivo | Entregables | Criterio de salida |
| :--- | :--- | :--- | :--- |
| 0 · Fundaciones | Repo, decisiones e investigaciones que bloquean | Diseño v2.1, ADRs, migraciones, pgTAP, CI de BD, licencia, spikes legales y de tarifas (P-1, P-3, P-8) | CI verde en GitHub con `supabase test db`; spikes cerrados |
| 1 · Núcleo backend | Webhook confiable | `UnitOfWork`, repositorios, webhook (HMAC, tenant, persistencia, cola), worker (claim/lease/DLQ), outbox, logs y trazas, **trámite de Tech Provider de Meta** | Q-PERF-1 en k6; prueba de caos sin pérdida; pruebas de seguridad del webhook |
| 2 · Agente MVP (importadora) | Cotizar por WhatsApp | Tools `search_catalog`, `create_quote`, `handoff_to_human`; guardrails; PDF; seeds sintéticos; evals *golden* (≥ 30 conversaciones) | Q-ACC-5 ≥ 90 % en la primera iteración; 0 violaciones de grounding |
| 3 · Pagos | Cobrar con seguridad | `PaymentPort` (QR estático + evidencia; QR dinámico si P-1 lo permite), reglas antifraude, bandeja de revisión | pgTAP 02 en verde; prueba con comprobantes manipulados; dataset OCR ≥ 100 |
| 4 · Agenda (clínica) | Agendar con seña | `check_availability`, `hold_appointment`, recordatorios por plantilla | pgTAP 03 en verde; evals del rubro clínica |
| 5 · Dashboard | Operación del negocio | Login con MFA, TC, catálogo + CSV, órdenes/agenda, pagos, conversaciones | Pruebas E2E (Playwright) de los 5 flujos; accesibilidad AA |
| 6 · Piloto | 1 importadora + 1 clínica en Santa Cruz | Onboarding, runbooks, alertas, simulacro de restauración | 2 semanas con SLOs cumplidos y costo por tenant medido |
| 7 · SIAT | Facturar | Adaptador del proveedor elegido (P-2) | Facturas de prueba emitidas en el ambiente del proveedor |

## Uso de IA asistente para escribir código

Sirve para acelerar cualquier fase, con estas reglas: el diseño y los ADRs son la especificación; cada archivo generado entra por PR con pruebas; nada se acepta "sin placeholders" sin que CI lo verifique.

El backlog de cada fase está cargado como issues en GitHub (ver `docs/GESTION_DEL_PROYECTO.md` y `scripts/github/backlog.json`).
