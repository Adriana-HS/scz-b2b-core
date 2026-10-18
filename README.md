# scz-b2b-core

**Motor transaccional multi-tenant de atención, cotización, agendamiento y conciliación de pagos QR por WhatsApp**, para PyMEs de Santa Cruz de la Sierra (importadoras de repuestos, ferreterías industriales, clínicas y consultorios).

> Estado: **diseño v2.2 + esquema de base de datos probado (284 pruebas pgTAP)**. El backend y el dashboard se implementan según `docs/ROADMAP.md`; las tareas viven en GitHub Issues (ver `docs/GESTION_DEL_PROYECTO.md`).

## Qué resuelve

| Problema | Cómo lo resuelve |
| :--- | :--- |
| Cotizaciones con tipo de cambio desactualizado | TC con historial y control de antigüedad; precio calculado y congelado por la base de datos |
| Conciliación manual y fraude con comprobantes QR | El bot envía el QR **del propio negocio**; los comprobantes se clasifican con reglas antifraude y una persona aprueba con MFA. La plataforma nunca toca el dinero |
| Atención 24/7 por WhatsApp sin perder el control | Agente IA que usa herramientas deterministas; traspaso a humano; el bot no puede confirmar pagos ni cambiar precios |
| Doble carga fiscal | (Fase de escala) Integración con un proveedor SIAT autorizado que contrata el negocio |

## Arquitectura en una línea

Monolito modular hexagonal en **Python/FastAPI** (procesos `api` + `worker`) · **Supabase** (PostgreSQL + pgvector, Auth, Storage) con **RLS en el 100 % de las tablas, también para el backend** · cola durable en Postgres · dashboard **Next.js + Tailwind** · WhatsApp **Cloud API oficial**.

## Documentación

| Documento | Contenido |
| :--- | :--- |
| [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) | Diseño completo: requisitos medibles, arquitectura C4, datos, FSM, agente IA, seguridad, rendimiento, operación, costos |
| [`docs/requirements/SRS.md`](docs/requirements/SRS.md) | Requisitos funcionales RF-xx con criterios de aceptación y trazabilidad |
| [`docs/architecture/`](docs/architecture/) | Estilo arquitectónico, revisión SOLID, catálogo de patrones, diagramas UML |
| [`docs/adr/`](docs/adr/) | 17 decisiones de arquitectura (formato MADR) |
| [`docs/legal/`](docs/legal/) | Marco legal, modelo de negocio y checklist antes del piloto |
| [`docs/research/`](docs/research/) | Restricciones externas verificadas (pagos, WhatsApp, IVA, TC, plataformas) |
| [`docs/GESTION_DEL_PROYECTO.md`](docs/GESTION_DEL_PROYECTO.md) | Tareas, tablero, fases y flujo de trabajo |
| [`AGENTS.md`](AGENTS.md) · [`docs/HERRAMIENTAS_IA.md`](docs/HERRAMIENTAS_IA.md) | Instrucciones y skills para Codex/ChatGPT y Claude |
| [`docs/reviews/`](docs/reviews/) | Revisión de la v1 con evidencia reproducible |
| [`docs/TESTING.md`](docs/TESTING.md) | Cómo correr las pruebas |
| [`docs/ai-ports.md`](docs/ai-ports.md) | Puertos de IA, decoradores (reintento, circuit breaker, trazas, costo) y modelo fijado |
| [`docs/observability.md`](docs/observability.md) · [`docs/runbooks/`](docs/runbooks/) | Logs, trazas, métricas, Sentry, alertas SLO y runbooks |
| [`docs/load-testing.md`](docs/load-testing.md) | Pruebas de carga (k6) y de caos del webhook |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Fases de implementación y criterios de salida |
| [`SECURITY.md`](SECURITY.md) | Política de seguridad y reporte de vulnerabilidades |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Flujo de trabajo, convenciones y definición de terminado |

## Inicio rápido (base de datos)

Requisitos: [Supabase CLI](https://supabase.com/docs/guides/cli) y Docker.

```bash
supabase start        # levanta Postgres, Auth y Storage locales y aplica supabase/migrations
supabase test db      # corre las pruebas pgTAP de supabase/tests/database
```

## Estructura

```text
supabase/migrations/   esquema, RLS, triggers y RPCs (fuente única de verdad del modelo de datos)
supabase/tests/        pruebas pgTAP: aislamiento, dinero/FSM/pagos, agenda/búsqueda/cola, IVA, MFA, stock
docs/                  ADRs, revisiones, guías, runbooks
ops/alerts/            alertas de los SLO como código (reglas de Prometheus + pruebas con promtool)
backend/               Python 3.12 + FastAPI, arquitectura hexagonal (ver backend/README.md)
frontend/              (fase 5 del roadmap)
```

## Autoría

Arquitectura: Adriana Hernández Severich (AHS). Software propietario, todos los derechos reservados (ver `LICENSE`).
