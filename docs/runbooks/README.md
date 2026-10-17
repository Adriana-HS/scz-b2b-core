# Runbooks

Procedimientos operativos paso a paso (se redactan en la fase 6 del roadmap; ver `SYSTEM_DESIGN.md` §10.4).

| Runbook | Disparador | Estado |
| :--- | :--- | :--- |
| Rotación del token de Meta | Trimestral o ante sospecha de filtración | Pendiente |
| Trabajos en DLQ (`DEAD`) | Alerta `jobs_dead > 0` (log `ALERTA job_dead` del worker; consulta: `SELECT id, kind, attempts, last_error FROM private.jobs WHERE status = 'DEAD'`) | Pendiente |
| TC vencido | Alerta `fx_stale_tenants > 0` | Pendiente |
| Revisión masiva de evidencias | Cola de `NEEDS_REVIEW` > umbral | Pendiente |
| Restauración de backup | Incidente o simulacro trimestral | Pendiente |
| Alta y baja de un tenant | Onboarding u offboarding | Pendiente |
| Incidente de seguridad | Sospecha de acceso indebido | Pendiente |
| Entrada en vigencia del IVA por fuera (Ley 1733) | Publicación de la norma reglamentaria | Pendiente |

Formato de cada runbook: síntoma → diagnóstico (consultas y paneles) → acción → verificación → comunicación → postmortem.
