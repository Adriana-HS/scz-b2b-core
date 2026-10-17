# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Corregido
- **Reservas de stock (Alta, H1):** una orden en `PAYMENT_REVIEW` con `valid_until` vencido dejaba de bloquear su stock; otro cliente podía reservarlo y, al aprobar el pago, `stock_on_hand` quedaba < 0 (la aprobación fallaba por `CHECK`) o había sobreventa. Ahora una reserva está activa mientras no se libere ni se comprometa; la termina el estado de la orden, no el reloj (ADR-0015).
- **Reservas de stock (Alta, H2):** cancelar no liberaba la reserva hasta su vencimiento, y cancelar o reembolsar una orden `CONFIRMED` nunca devolvía el stock. Confirmar con seña 0 tampoco comprometía stock. Migración 10: trigger `trg_orders_stock_lifecycle` que compromete, libera o devuelve el stock en cada cambio de estado; columna `stock_reservations.returned_at`. Si al confirmar no hay stock físico, el error es `HINT = STOCK_CONFLICT`, sin efectos parciales.
- Prueba `07_stock_lifecycle.test.sql` (19 casos; fallaba antes del fix). 109 pruebas pgTAP en total.

### Seguridad
- **Precios del catálogo (#50, ADR-0016):** STAFF, OWNER y ADMIN cambiaban precios sin MFA; con una sesión robada, un precio en 0 se congelaba en las cotizaciones nuevas. Migración 14, regla mixta: los cambios dentro del umbral siguen sin MFA; precio 0, cambio de moneda o variación mayor a `organizations.price_change_mfa_threshold_pct` (30 % por defecto) exigen OWNER/ADMIN con aal2; el backend no cambia precios; cada cambio aplicado encola `PRICE_CHANGE_ALERT` para el dueño. Prueba `11_catalog_price_mfa.test.sql` (20 casos). 196 pruebas pgTAP en total.
- **SEC-02 (Media, #53):** tres caminos de menor privilegio sin cerrar en la BD. (M1) Sin `app.actor`, `private.actor_kind()` devolvía `SYSTEM` y el backend podía registrar un pago `BANK_API`; además podía declararse `HUMAN`. (M2) STAFF sin MFA podía reembolsar, cancelar órdenes pagadas o en revisión y confirmar órdenes sin seña. (M3) `app_backend` podía marcar una evidencia `APPROVED` sin la RPC con MFA. Migración 11: actor por defecto `BOT`, `app_backend` sin INSERT en `payments` hasta la fase 7 (ADR-0014), guarda de actor en `payments`, trigger de roles en `orders` y FSM de evidencias en la BD (`private.evidence_transitions`) con columnas acotadas y datos congelados al pasar a revisión. Prueba `08_actor_roles.test.sql` (40 casos; fallaba antes del fix). 149 pruebas pgTAP en total.
- **Orden con pago parcial (Media, #57):** `BOT` y `SYSTEM` podían cancelar una orden con `amount_paid_bs > 0` y `expire_stale_orders` la pasaba a `EXPIRED`: quedaba dinero en `payments` sin orden viva y sin que una persona decidiera el reembolso. Migración 12 (`trg_orders_paid_exit_guard`): cancelarla exige OWNER/ADMIN con aal2 (HINT `HUMAN_REVIEW_REQUIRED` para el backend) y el vencimiento la omite, sin frenar el resto del job. Prueba `09_paid_order_exit.test.sql` (18 casos; fallaba antes del fix). 167 pruebas pgTAP en total.
- **Vencimiento de órdenes con pagos (seguimiento de #57):** `expire_stale_orders` seguía seleccionando cada minuto las órdenes vencidas con `amount_paid_bs > 0`; `trg_orders_paid_exit_guard` las omitía, pero el job bloqueaba esas filas en cada pasada sin efecto. Migración 13: `AND amount_paid_bs = 0` en el `WHERE` del job (mismo cuerpo que en la migración 10); el trigger queda como defensa en profundidad. Prueba `10_expire_skips_paid.test.sql` (9 casos; fallaba antes del fix). 176 pruebas pgTAP en total.
- **SEC-01 (Alta):** una sesión de OWNER/ADMIN sin MFA podía apagar `config.require_mfa_for_payments` y aprobar pagos sin segundo factor. La revisión del fix encontró dos caminos más: dar de alta o ascender un ADMIN, y cargar un tipo de cambio, ambos sin MFA. Migración 09: `aal2` incondicional en la aprobación, en la edición de la organización, en membresías y en el TC; la clave queda prohibida por `CHECK`. Prueba `06_payment_mfa.test.sql` (11 casos; fallaba antes del fix). 90 pruebas pgTAP en total.

### Cambiado (documentación y herramientas)
- `supabase db lint` bloquea el PR (`--fail-on error`; sin esa opción la CLI sale con 0 aunque haya errores). El primer reporte salió limpio. `scripts/test-db.ps1` también falla si el lint falla.
- Skills visibles en Claude Code: puentes en `.claude/skills/` con el mismo frontmatter que `.agents/skills/` (fuente única); `check_repo.py` verifica que coincidan.
- `SYSTEM_DESIGN.md`: 9 migraciones, EXT-01 … EXT-17, la CI corre sobre PostgreSQL 17, `request_payment` usa el QR estático del negocio en el MVP y los diagramas C4 marcan banco y SIAT como fase de escala (ADR-0014). SRS: EXT-01 … EXT-17.
- `supabase/seed/README.md`: los seeds siguen pendientes (issue #27).
- Regla de autoría en `AGENTS.md` y `CONTRIBUTING.md`: AHS es la única autora; ningún asistente de IA figura como coautor en commits ni PRs.

### Agregado (CI)
- CI con versiones fijas (Supabase CLI 2.120.0, gitleaks 8.21.2), cancelación de runs viejos, lint de funciones (informativo) y logs de Postgres si falla.
- Job `repo` con `scripts/ci/check_repo.py`: backlog, enlaces de Markdown, ADRs y nombres de migraciones.
- gitleaks como binario en lugar de `gitleaks-action` (esta pide licencia en repos de organizaciones).
- `scripts/test-db.ps1`: la misma verificación en Windows.
- Migración 08: índices para las 15 claves foráneas que no tenían (regla de supabase/agent-skills).
- Prueba `05_schema_lint.test.sql` (5 guardas de regresión); 79 pruebas pgTAP en total.
- EXT-17: desde el 30-oct-2026 Supabase no expone tablas nuevas a la Data API sin GRANT explícito (el esquema ya usa GRANT explícitos).
- `docs/HERRAMIENTAS_IA.md`: skills oficiales de Supabase y nombres corregidos de trailofbits/skills.
- `bootstrap.py` renombra issues y milestones con título anterior (campo `replaces`) en vez de duplicarlos.

### Cambiado (v2.2 — mínimo regulatorio)
- ADR-0014: MVP con QR estático del negocio y aprobación humana; sin OpenBCB, sin facturación SIAT y sin comisiones por transacción; sin pedir CI/NIT por el chat; WhatsApp en modelo directo para el piloto; importadoras primero.
- `docs/legal/marco-legal-y-modelo-de-negocio.md`: regulación aplicable, requisitos para constituir la empresa, modelo de ingresos y checklist legal.
- Columnas de consentimiento en `customers` (`optin_utility_at`, `optin_marketing_at`, `optout_at`).
- Backlog: 47 issues (nuevos: acuerdo de socios, constitución de la empresa, contratos y privacidad, onboarding directo, opt-in/opt-out).

### Agregado (v2.1)
- ADR-0012 (IVA por régimen vigente, Ley 1733) y ADR-0013 (la plataforma no custodia fondos).
- Modelo de IVA en la BD: `tax_regimes`, `orders.subtotal_bs/iva_bs/tax_regime_id`, `private.tax_breakdown`; 11 pruebas pgTAP nuevas (74 en total).
- Registro de restricciones externas (`docs/research/`), SRS con RF-xx, principios SOLID, patrones y UML (`docs/architecture/`).
- Gestión del proyecto: plantillas de issues, backlog de 47 issues en 8 fases y script `scripts/github/bootstrap.py`.
- `AGENTS.md`, `CLAUDE.md` y 5 skills del proyecto en `.agents/skills/`; guía `docs/HERRAMIENTAS_IA.md`.

### Agregado
- `SYSTEM_DESIGN.md` v2.0: requisitos medibles, arquitectura C4, FSM, agente IA, seguridad STRIDE, SLOs, costos.
- 11 ADRs en `docs/adr/`.
- Revisión de la v1 con evidencia reproducible (`docs/reviews/`).
- Esquema de base de datos en 7 migraciones (`supabase/migrations/`): multi-tenancy con RLS también para el backend, FK compuestas, historial de tipo de cambio, cotización atómica con reserva de stock, agenda sin doble reserva, evidencias y libro de pagos, auditoría, cola durable y outbox.
- 63 pruebas pgTAP (`supabase/tests/database/`), luego ampliadas a 74 con las de IVA.
- CI de base de datos, plantilla de PR, guías de seguridad, contribución y pruebas.

### Cambiado
- La sección "Guía de ejecución en Claude Pro" de la v1 pasa a `docs/ROADMAP.md` como plan por fases.
