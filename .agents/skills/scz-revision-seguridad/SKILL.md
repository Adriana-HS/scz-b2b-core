---
name: scz-revision-seguridad
description: Revisa un PR, un diff o un diseño de SCZ-B2B-Core contra su modelo de amenazas (multi-tenant, pagos, WhatsApp, LLM, privacidad). Úsala antes de fusionar cambios que toquen dinero, autenticación, datos personales, webhooks, el agente de IA o el esquema.
---

# Revisión de seguridad de SCZ-B2B-Core

Referencia: `SYSTEM_DESIGN.md` §8 (STRIDE), ADR-0002, ADR-0008, ADR-0013 y `docs/research/2026-10-15-restricciones-externas.md`.

## Preguntas obligatorias (responde cada una con evidencia: archivo:línea o prueba)
### Tenancy
1. ¿Toda consulta nueva pasa por `UnitOfWork` con `app_backend` (sin service role)?
2. ¿Alguna función o endpoint acepta `organization_id` desde la entrada externa?
3. ¿Las tablas nuevas tienen RLS, políticas y FK compuestas? ¿Hay prueba de aislamiento?
### Dinero y estados
4. ¿Algún monto se calcula fuera de las funciones SQL oficiales?
5. ¿El LLM puede influir en precios, estados o pagos por algún camino?
6. ¿Se respeta la FSM (`fsm_transitions`) y el actor correcto (BOT/SYSTEM/HUMAN)?
### Pagos
7. ¿Se trata algún comprobante como confirmación? ¿Se envía "confirmado" antes de `payments`?
8. ¿Algún flujo hace que la plataforma reciba o retenga fondos (ADR-0013)?
9. ¿Los callbacks bancarios se autentican y se confirman con una consulta de estado?
### Entradas externas
10. Webhooks: ¿HMAC sobre el cuerpo crudo antes de parsear? ¿Límites de tamaño? ¿Idempotencia?
11. Media: ¿tipo por *magic bytes*, tamaño máximo, re-codificación sin EXIF, hash de los bytes originales?
12. Prompt injection: ¿el texto de OCR, catálogo o mensajes se trata como datos? ¿Herramientas con `extra=forbid`?
### Datos y secretos
13. ¿Hay PII en logs o trazas? ¿Secretos en el código, tests o fixtures?
14. ¿Storage usa rutas `<organization_id>/…` construidas desde el contexto, nunca desde la entrada?
15. ¿Las funciones `SECURITY DEFINER` fijan `search_path`?

## Salida
Tabla de hallazgos: ID, severidad (Crítica/Alta/Media/Baja), archivo:línea, escenario de explotación concreto, corrección propuesta y prueba que lo cubriría. Sin hallazgos → indícalo y lista lo que verificaste.
