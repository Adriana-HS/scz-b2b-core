# Revisión de ingeniería — SYSTEM_DESIGN v1.0

| Campo | Valor |
| :--- | :--- |
| Documento revisado | `SYSTEM_DESIGN.md` v1.0 (Especificación base, AHS) |
| Fecha | 15-oct-2026 |
| Resultado | **Aprobado con cambios mayores** → incorporados en `SYSTEM_DESIGN.md` v2.0 |
| Evidencia | `docs/reviews/evidence/` (SQL v1 original, script de sondeo, salida) · `supabase/tests/database/` (pgTAP v2) |

## 1. Resumen ejecutivo

La v1 tiene una **dirección arquitectónica correcta**: monolito modular hexagonal, el LLM nunca calcula dinero, FSM controlada por el backend, firma HMAC de webhooks, búsqueda y vectores dentro de PostgreSQL, buckets privados y facturación SIAT detrás de un puerto. Son las decisiones que tomaría un equipo con experiencia.

El problema está en que **varias garantías que el documento declara no se cumplen en la implementación propuesta**. Se ejecutó el DDL de la v1 en PostgreSQL 16 + pgvector 0.8 y se confirmaron 13 fallos con datos (sección 4). Los cuatro más graves son:

1. **El aislamiento por RLS no protege el camino del bot.** El backend usa *service role*, que ignora RLS, y una tabla interna quedaba legible con la clave pública.
2. **Un comprobante de pago se trata como prueba de pago.** Se envía una "confirmación" con el pre-chequeo de IA, sin validar beneficiario ni fecha; una captura fabricada pasaría.
3. **Mensajes aceptados pueden perderse.** `BackgroundTasks` + idempotencia marcada antes de procesar + CPU limitada en Cloud Run.
4. **Concurrencia e integridad.** Se aceptan citas solapadas, sobreventa de stock y órdenes que mezclan datos de dos tenants.

La v2.0 corrige los 30 hallazgos. El nuevo esquema (7 migraciones) pasa **63/63 pruebas pgTAP** y una prueba de concurrencia real (4 sesiones simultáneas → 1 reserva, 3 rechazos).

## 2. Método

1. Lectura completa y trazabilidad entre secciones (ADRs ↔ diagramas ↔ DDL ↔ tools).
2. **Ejecución del DDL v1** sobre PostgreSQL 16.15 + pgvector 0.8.0, con un *shim* mínimo de Supabase (roles `anon`/`authenticated`/`service_role`, `auth.uid()`, privilegios por defecto) y un script de sondeo que intenta cada fallo sospechado (`evidence/v1_probe.sql` → `evidence/v1_probe_output.txt`).
3. Verificación de supuestos externos a la fecha (TC del BCB, OpenBCB, normativa de datos e IA en Bolivia, precios de Supabase y WhatsApp, comportamiento de CPU en Cloud Run).
4. Rediseño, implementación del esquema v2 y pruebas automatizadas (pgTAP + concurrencia).

> Limitación: la verificación se hizo con un *shim* local de Supabase, no con la imagen oficial. Antes del primer despliegue hay que correr `supabase test db` (ver `docs/TESTING.md`); el CI lo hace en cada PR.

## 3. Lo que la v1 hizo bien (se conserva)

* ADR-001: monolito modular hexagonal, adecuado para el tamaño del equipo y la escala.
* Principio de determinismo financiero: el LLM no calcula montos y las tools no aceptan precios.
* ADR-004: el estado de pedidos y citas no lo decide el LLM.
* HMAC-SHA256 con `compare_digest` antes de parsear.
* Tabla de idempotencia por `whatsapp_message_id`.
* Doble candado de comprobantes (hash binario + nro. de transacción), como base.
* Buckets privados y URLs firmadas.
* `BillingPort` para el SIAT y la decisión de no homologar en el MVP.
* Uso de ADRs desde el día 1.

## 4. Hallazgos

Severidad: **Crítica** (pérdida de dinero, de datos o fuga entre tenants) · **Alta** (rompe una garantía declarada o un flujo principal) · **Media** (riesgo operativo o deuda que crecerá) · **Baja** (calidad documental).

"Evidencia" = ID del sondeo ejecutado sobre la v1 (`H#`) o análisis estático (`E`).

| ID | Sev. | Área | Hallazgo | Evid. | Corrección en v2.0 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| R-01 | Crítica | Seguridad | El backend usa *service role* (BYPASSRLS): la garantía "aislamiento por RLS" no aplica al camino de WhatsApp, que concentra la mayor parte del tráfico | E | ADR-0002: rol `app_backend` sin BYPASSRLS + `SET LOCAL app.current_org_id`; falla cerrado |
| R-02 | Crítica | Pagos | La captura se trata como prueba: se envía "PDF de confirmación" con `PRE_VERIFIED`; no valida beneficiario, cuenta destino ni fecha; SHA-256 evadible recortando la imagen | E | ADR-0008: QR dinámico con confirmación bancaria; la captura es evidencia + reglas + aprobación humana con MFA; pHash; unicidad normalizada global |
| R-03 | Crítica | Confiabilidad | `BackgroundTasks`: el trabajo se pierde al reiniciar, la CPU se limita tras la respuesta en Cloud Run y la idempotencia marca el mensaje como procesado **antes** de procesarlo (si falla, no se reintenta nunca) | E | ADR-0005: persistencia + job en la misma transacción antes del 200; cola durable con *lease*, reintentos y DLQ |
| R-04 | Crítica | Seguridad | `processed_webhook_messages` sin RLS: legible con la clave anónima | H1 | RLS en 25/25 tablas; tablas internas en el esquema `private` no expuesto; `anon` sin privilegios |
| R-05 | Alta | Seguridad | Las políticas `FOR ALL` no distinguen roles: un AUDITOR cambió el TC de la empresa a 1,00 | H3 | Políticas por rol; MFA para cuentas bancarias y aprobación de pagos |
| R-06 | Alta | Funcional | `profiles` y `order_items` con RLS activado y **sin políticas**: el dashboard no puede leerlas | H2 | Políticas explícitas para todas las tablas |
| R-07 | Alta | Integridad | FK simples: una orden de A aceptó un cliente y un ítem de B; `subtotal_bs = 999` para 2 × 100 aceptado | H4 | FK compuestas `(organization_id, id)`; `line_total_bs` generado; totales por trigger |
| R-08 | Alta | Integridad | Dos citas solapadas en el mismo horario aceptadas; verificación de stock sin reserva (carrera); no existe entidad de profesional/recurso aunque se menciona | H10 | `EXCLUDE USING gist`; `resources` + `business_hours`; `stock_reservations` con vencimiento y bloqueo de filas |
| R-09 | Alta | Consistencia | El diagrama usa `PRE_VERIFIED`, `FLAGGED_MANUAL` y `VERIFIED`, que no existen en el enum; `REJECTED_DUPLICATE` es inalcanzable porque el UNIQUE impide registrar el intento | H5, H6 | Un vocabulario único; UNIQUE **parciales** que permiten registrar duplicados rechazados |
| R-10 | Alta | Auditoría | `ON DELETE CASCADE`: borrar una orden borró la evidencia de pago; borrar una organización borra todo | H9 | `ON DELETE RESTRICT`; sin `DELETE` en runtime; `payments`/`exchange_rates`/`audit_log` append-only |
| R-11 | Alta | Dinero | TC por defecto 6,96 (el TCO del BCB al 15-oct-2026 es 11,68), sin historial, sin control de antigüedad ni vigencia de cotización: el problema 1 del documento queda sin resolver | H12 | ADR-0007: `exchange_rates` con historial, `fx_max_age_hours`, `valid_until`, TC congelado por orden |
| R-12 | Alta | Búsqueda | La "búsqueda híbrida" no usa el FTS; los ítems sin embedding salen primero; `%` coincide con todo; puntajes no comparables | H7, H8 | ADR-0003: FTS + SKU/trigram + difuso + vector con RRF |
| R-13 | Alta | Seguridad | Función SECURITY DEFINER sin `search_path` fijo; la RPC de búsqueda recibe `p_organization_id` como parámetro | H13 | `SET search_path = ''` en todas (verificado por prueba); el tenant sale del contexto, no de un parámetro |
| R-14 | Alta | Canal | Evolution API (no oficial) en el diagrama: riesgo de bloqueo, sin firma HMAC; contradice el control de seguridad 6.1 | E | ADR-0006: solo Cloud API oficial |
| R-15 | Alta | Canal | No contempla la ventana de 24 h ni las plantillas (notificaciones al dueño, recordatorios, cotización por vencer) ni el costo por mensaje | E | §10.1 y §11: plantillas por categoría, outbox, costos medidos |
| R-16 | Alta | IA | No hay memoria de conversación (faltan `conversations`/`messages`), ni traspaso a humano, ni transcripción de las notas de voz que el diagrama recibe | E | Tablas de conversación, modo `HUMAN`, tool `handoff_to_human`, adaptador STT |
| R-17 | Media | Dinero | Cálculo duplicado (`pricing_engine.py` + SQL) = dos fuentes de verdad; un ítem podía tener ambos precios NULL | E | Una función SQL; `price_currency` + `list_price NOT NULL` |
| R-18 | Media | Concurrencia | Mensajes en ráfaga del mismo cliente se procesan en paralelo: respuestas desordenadas o duplicadas | E | Un trabajo por conversación + debounce (pgTAP 03-#16…#18) |
| R-19 | Media | UX | Se envían por WhatsApp URLs firmadas de 15 min: caducan antes de que el cliente abra el PDF | E | Subir el documento a Meta y enviar por `media_id` |
| R-20 | Media | Operación | Sin auditoría; `updated_at` nunca se actualiza; faltan índices en FK | H11 | `audit_log` con actor; triggers de `updated_at`; índices en todas las FK |
| R-21 | Media | Requisitos | Objetivos no medibles ("85 % agnóstico", latencias sin percentil); < 6 s poco realista con 2 rondas de LLM | E | §2: 18 requisitos con métrica, objetivo y verificación; §9.1 presupuesto de latencia |
| R-22 | Media | Costos | "$us 0–15/mes" no es viable: el plan gratuito de Supabase pausa proyectos inactivos; se omiten LLM y WhatsApp | E | §11: modelo de costos fijo + variable, cuotas por tenant |
| R-23 | Media | Datos | `supabase-py` (PostgREST) no permite transacciones de varias sentencias, que la atomicidad requiere | E | ADR-0011: asyncpg vía Supavisor + `UnitOfWork`; lógica crítica en RPCs |
| R-24 | Media | Cumplimiento | Sin aviso de IA, sin política de retención, sin tratamiento de datos de salud | E | §8.7 privacidad por diseño; §5.5 retención |
| R-25 | Media | Operación | No hay observabilidad, RPO/RTO, CI/CD ni estrategia de pruebas | E | §10, §13; `.github/workflows/ci.yml`; pgTAP |
| R-26 | Media | IA | Prompt injection indirecta (glosa del OCR, descripciones) no tratada; `customer_name` libre en la tool; sin tope de costo por tenant | E | §7.4 guardrails; OCR sin tools; cuotas; grounding numérico |
| R-27 | Baja | Documento | Las referencias son búsquedas de Google, no fuentes; errata `date_ requested`; tono personal en §6–7 ("Dado tu perfil…", "Claude Pro") | E | Referencias directas; §7 de la v1 pasa a `docs/ROADMAP.md` |
| R-28 | Baja | Stack | Versiones sin política de fijación (Next.js 15, PostgreSQL 16) | E | Lockfiles; versión de Postgres según el proyecto Supabase |
| R-29 | Baja | Datos | Seeds con "repuestos reales": riesgo de marcas o datos de terceros | E | Seeds sintéticos |
| R-30 | Baja | Seguridad | Sin validación de media (tamaño, tipo real, EXIF) | E | §8.3 |

## 5. Comparación por área

| Área | v1.0 | v2.0 | Cómo se verifica en v2 |
| :--- | :--- | :--- | :--- |
| Aislamiento multi-tenant | Solo dashboard | Dashboard + backend + Storage + integridad referencial | pgTAP 01 (21 pruebas) |
| Determinismo financiero | Intención correcta, 2 implementaciones | Una función, montos congelados, TC con historial | pgTAP 02 #1–#14 |
| Antifraude de pagos | Hash + UNIQUE; confirma con IA | Confirmación bancaria o humana con MFA; reglas; duplicados registrados | pgTAP 02 #15–#24 |
| Concurrencia (stock/agenda) | Sin control | EXCLUDE + bloqueo de filas + reservas | pgTAP 03 + prueba de 4 sesiones |
| Entrega de mensajes | En memoria | Cola durable, orden por conversación, outbox | pgTAP 03 #16–#18 + caos en CI |
| Requisitos de calidad | Cualitativos | 18 medibles con verificación | §2 |
| Seguridad / privacidad | Controles puntuales | STRIDE, MFA, secretos, retención, aviso de IA | §8 |
| Documentación | 1 documento | Diseño + 11 ADRs + revisión con evidencia + guías | `docs/` |

## 6. Recomendaciones de siguiente paso

1. Correr `supabase test db` contra la imagen oficial (CI) y ajustar lo que difiera del *shim* local.
2. Resolver las preguntas abiertas P-1 (OpenBCB/bancos) y P-3 (tarifas de WhatsApp): definen el flujo de pago del MVP y el precio por tenant.
3. Implementar el backend en este orden: `UnitOfWork` + repositorios → webhook + cola → agente con 3 tools (`search_catalog`, `create_quote`, `handoff_to_human`) → evidencias de pago → agenda → dashboard.
4. Construir el dataset de evaluación (conversaciones *golden* y comprobantes) **antes** de ajustar prompts.
