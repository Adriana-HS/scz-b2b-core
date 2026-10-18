# Runbooks

Procedimientos operativos paso a paso (ver `SYSTEM_DESIGN.md` §10.4). Cada alerta de `ops/alerts/slo-rules.yaml` enlaza su runbook; una prueba verifica que exista (`backend/tests/unit/test_alert_rules.py`). Los que no tienen alerta se redactan en la fase 6 del roadmap.

| Runbook | Disparador | Estado |
| :--- | :--- | :--- |
| Rotación del token de Meta | Trimestral o ante sospecha de filtración | Pendiente |
| [Trabajos en DLQ (`DEAD`)](trabajos-dead.md) | Alertas `JobDead` y `JobsDeadPending`; log `ALERTA job_dead` del worker (Sentry) | Listo (#19) |
| [TC vencido](tc-vencido.md) | Alerta `FxRateStale` (`fx_stale_tenants > 0`) | Listo (#19) |
| [Latencia de respuesta o del ack](latencia.md) | Alertas `ReplyLatencyP95High` y `WebhookAckP99High` | Listo (#19) |
| [Cola atrasada](cola-atrasada.md) | Alertas `QueueBacklog` y `QueueWaitHigh` | Listo (#19) |
| [Fallas de firma del webhook](firma-webhook.md) | Alerta `WebhookSignatureFailuresHigh` | Listo (#19) |
| [Envío con resultado incierto](envio-incierto.md) | Alerta `OutboundOutcomeUnknown`; log `ALERTA outbound_outcome_unknown` (Sentry) | Listo (#19) |
| [Cuota diaria de IA](cuota-ia.md) | Alertas `LlmBudgetNearLimit` y `LlmBudgetExceeded`; log `ALERTA cuota_ia_agotada` | Listo (#25) |
| Revisión masiva de evidencias | Cola de `NEEDS_REVIEW` > umbral | Pendiente |
| Restauración de backup | Incidente o simulacro trimestral | Pendiente |
| Alta y baja de un tenant | Onboarding u offboarding | Pendiente |
| Incidente de seguridad | Sospecha de acceso indebido | Pendiente |
| Entrada en vigencia del IVA por fuera (Ley 1733) | Publicación de la norma reglamentaria | Pendiente |

Formato de cada runbook: síntoma → diagnóstico (consultas y paneles) → acción → verificación → comunicación → postmortem.
