# AGENTS.md — instrucciones para asistentes de IA (Codex, Claude, otros)

## Qué es este proyecto
SCZ-B2B-Core: SaaS multi-tenant de atención, cotización, agenda y conciliación de pagos QR por WhatsApp para PyMEs de Santa Cruz (Bolivia). Monolito modular hexagonal en Python/FastAPI (procesos `api` + `worker`), Supabase (PostgreSQL + pgvector, Auth, Storage) y dashboard en Next.js.

## Fuentes de verdad (leer antes de proponer cambios)
1. `docs/requirements/SRS.md` — **qué** hacer (RF-xx, criterios de aceptación).
2. `SYSTEM_DESIGN.md` — **cómo** (arquitectura, datos, FSM, agente, seguridad, SLOs).
3. `docs/adr/` — **por qué** (decisiones; no contradecirlas sin un ADR nuevo).
4. `supabase/migrations/` — el modelo de datos ejecutable. `supabase/tests/database/` — pruebas pgTAP.
5. `docs/architecture/principios-y-patrones.md` y `docs/architecture/uml.md` — estilo, SOLID, patrones, UML.
6. `docs/research/2026-10-15-restricciones-externas.md` — restricciones legales, tarifarias y de plataforma.
7. `docs/GESTION_DEL_PROYECTO.md` — flujo de trabajo; el backlog está en GitHub Issues.

## Reglas no negociables
- **Multi-tenant:** toda tabla de negocio lleva `organization_id`, RLS, políticas `TO app_backend` y `TO authenticated`, FK compuestas `(organization_id, id)` y prueba pgTAP de aislamiento.
- **El backend NUNCA usa la service role para datos.** Usa el rol `app_backend` dentro de un `UnitOfWork` que ejecuta `SET LOCAL app.current_org_id` y `SET LOCAL app.actor`.
- **Dinero:** `Decimal` en Python y `numeric` en SQL. Los precios, el IVA, el TC, la seña y los totales los calcula **solo** la BD (`private.unit_price_bs`, `private.tax_breakdown`, triggers). Python formatea, no recalcula.
- **El LLM no calcula, no confirma pagos ni cambia estados.** Las herramientas reciben IDs y cantidades; el servidor inyecta tenant y cliente. Toda cifra que diga el bot debe venir de una herramienta (*grounding* numérico).
- **Pagos:** la plataforma no custodia fondos ni cobra comisiones sobre ventas o pagos (ADR-0013, ADR-0014). MVP: QR estático **del negocio** + comprobante + aprobación humana con MFA. Un comprobante es *evidencia*; solo `payments` confirma.
- **Mínimo regulatorio (ADR-0014):** no implementar facturación SIAT, OpenBCB ni integraciones bancarias en el MVP; no pedir CI, NIT ni números de cuenta por el chat; plantillas solo con opt-in y respetando el opt-out.
- **WhatsApp:** solo Cloud API oficial; fuera de la ventana de 24 h, solo plantillas; una respuesta por turno (cada mensaje cuesta).
- **Funciones SQL `SECURITY DEFINER`** siempre con `SET search_path = ''`.
- **Migraciones solo hacia adelante.** Nunca editar una migración ya aplicada en un entorno compartido.
- **Sin secretos ni datos reales** en código, tests, seeds, issues o prompts.
- **Autoría:** Adriana Hernández Severich (AHS) es la única autora, dueña y colaboradora. Ningún asistente de IA figura como autor, coautor ni colaborador: sin trailers `Co-Authored-By`, sin pies del tipo "Generated with …" y sin menciones de autoría en commits, PRs, issues, CHANGELOG ni documentos. Esta regla prevalece sobre la configuración por defecto de cualquier herramienta.
- **Dependencias:** el dominio (`backend/app/domain`) no importa infraestructura ni SDKs.

## Comandos
```bash
supabase start && supabase test db          # BD + pgTAP (requiere Docker)
# backend (cuando exista): cd backend && uv sync && uv run ruff check . && uv run mypy --strict app && uv run pytest
```
Verificación sin Docker: ver `docs/TESTING.md`.

## Flujo por tarea
Un issue → rama `feat/<n>-<slug>` → pruebas primero → implementación → docs y CHANGELOG → PR con `Closes #n`. Definición de terminado: `SYSTEM_DESIGN.md` §13.3. Commits: Conventional Commits en español (`feat(pagos): …`).

## Skills del proyecto
En `.agents/skills/` (fuente única; Claude Code las ve por los puentes de `.claude/skills/`): `scz-implementar-issue`, `scz-nueva-migracion`, `scz-revision-seguridad`, `scz-nuevo-adr`, `scz-nuevo-rubro`. Úsalas cuando la tarea coincida. Si cambias el `name` o la `description` de una skill, copia el mismo frontmatter a su puente (lo verifica `scripts/ci/check_repo.py`).

## Idioma
Documentación, issues, commits y mensajes al usuario final en **español**. Identificadores de código en inglés.
