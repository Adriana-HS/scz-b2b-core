# Especificación de requisitos de software (SRS) — SCZ-B2B-Core

| Campo | Valor |
| :--- | :--- |
| Norma de referencia | ISO/IEC/IEEE 29148:2018 (estructura adaptada) |
| Versión | 1.1 · 15-oct-2026 (alcance de mínimo regulatorio, ADR-0014) |
| Requisitos de calidad | `SYSTEM_DESIGN.md` §2 (Q-SEC, Q-ACC, Q-PERF, Q-REL, Q-MAINT, Q-COST) |
| Prioridad | MoSCoW: **M** (must), **S** (should), **C** (could), **W** (won't, por ahora) |

## 1. Propósito y alcance

Definir de forma verificable **qué** debe hacer el sistema. El **cómo** está en `SYSTEM_DESIGN.md` y en los ADRs. Cada requisito tiene un identificador estable que se usa en issues, PRs, pruebas y diagramas.

## 2. Partes interesadas e intereses

| Parte interesada | Intereses principales (*concerns*) |
| :--- | :--- |
| Dueño de la PyME | Vender más, no perder dinero por TC o fraude, control sobre el bot, costo predecible |
| Personal (STAFF) | Bandeja clara de conversaciones y pagos, poco trabajo manual |
| Cliente / paciente | Respuesta rápida, precio correcto, confirmación confiable, privacidad |
| Auditor / contador | Trazabilidad, facturación correcta, registros inmutables |
| Equipo de desarrollo | Mantenibilidad, pruebas, documentación, despliegue simple |
| Reguladores (SIN, ASFI, Meta como plataforma) | Cumplimiento tributario, no custodiar fondos, políticas de WhatsApp |

## 3. Actores

Cliente/Paciente, Dueño/Admin (OWNER, ADMIN), Personal (STAFF), Auditor (AUDITOR), WhatsApp (Meta), Banco/OpenBCB, Proveedor SIAT, Sistema (jobs programados). Ver el diagrama de casos de uso en `docs/architecture/uml.md` §1.

## 4. Requisitos funcionales

### 4.1 Conversación y agente

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-01 | El sistema recibe mensajes de WhatsApp (texto, imagen, nota de voz, interactivos) de cada número conectado y los asocia al tenant y al cliente | M | Un webhook firmado crea `inbound_events`, `messages` y un job en una transacción; uno sin firma recibe 401; un duplicado no crea nada nuevo |
| RF-02 | El agente responde consultas de catálogo con precio en Bs, disponibilidad y la indicación de IVA, usando solo datos de las herramientas | M | 0 violaciones de *grounding* numérico en las evals; respuesta "precio en actualización" si el TC está vencido |
| RF-03 | El primer contacto informa que se conversa con un asistente automatizado y cómo pedir una persona | M | `customers.privacy_notice_at` queda registrado en el primer mensaje |
| RF-04 | El cliente puede pedir hablar con una persona en cualquier momento; el bot se pausa y el equipo recibe aviso | M | La tool `handoff_to_human` → `mode = HUMAN`; el bot no responde hasta que se libere |
| RF-05 | Las notas de voz se transcriben y se procesan como texto | S | Transcripción guardada en `messages.body`; p95 de la respuesta ≤ 10 s |
| RF-06 | Fuera de la ventana de 24 h solo se envían plantillas aprobadas | M | Ningún mensaje libre sale si `now − last_inbound_at > 24 h` |
| RF-07 | El agente rechaza conversaciones ajenas al negocio (política de Meta sobre IA) | M | Casos "fuera de alcance" en las evals con respuesta de redirección ≥ 95 % |
| RF-08 | Registrar opt-in y opt-out por cliente; los mensajes proactivos solo van a quien aceptó, y "BAJA" o equivalente lo excluye al instante | M | Ninguna plantilla sale a un cliente sin `optin_utility_at` (o `optin_marketing_at`) o con `optout_at` |
| RF-09 | El bot nunca pide CI, NIT, números de tarjeta ni de cuentas por el chat | M | Evals y revisión de prompts; no existe una tool que lo haga |

### 4.2 Catálogo, precios e impuestos

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-10 | El negocio gestiona su catálogo (alta, edición, baja lógica, importación CSV) con moneda de lista BOB o USD | M | Validación de forma (producto con stock, servicio con duración); el CSV reporta errores por fila; un precio en 0, un cambio de moneda o una variación mayor al umbral del negocio exigen OWNER/ADMIN con MFA (pgTAP 11, ADR-0016) |
| RF-11 | Búsqueda por código exacto, texto con errores y significado | M | pgTAP 03 #10–#13; MRR@5 ≥ 0,8 en `evals/search` |
| RF-12 | Registro del tipo de cambio con historial, fuente y autor; referencia diaria del BCB | M | Inserción append-only; alerta si el TC supera `fx_max_age_hours` |
| RF-13 | El IVA se calcula según el régimen vigente (incluido o adicional) y se congela en cada orden | M | pgTAP 04 (11 pruebas) |
| RF-14 | Cada ítem puede guardar su código de producto del SIN (fase de escala) | C | Campo validado; obligatorio si el tenant activa la facturación |

### 4.3 Cotizaciones y agenda

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-20 | Cotizar ítems con cantidades, reservando stock y con vigencia configurable; PDF enviado por WhatsApp | M | pgTAP 02 #1–#10; PDF con TC, IVA y vigencia; envío por `media_id` |
| RF-21 | Las cotizaciones y retenciones vencidas expiran solas y liberan stock u horario; cancelar también libera en el acto, y una orden en revisión de pago conserva su stock | M | pgTAP 03 #8–#9, 07 #1–#7, #12–#13 |
| RF-22 | Mostrar horarios libres por servicio, fecha y profesional, en la hora local del negocio | M | pgTAP 03 #1–#2, #7 |
| RF-23 | Retener un horario sin posibilidad de doble reserva | M | pgTAP 03 #5–#6 + prueba de concurrencia |
| RF-24 | Recordatorio de cita 24 h antes mediante plantilla de utilidad | S | Job programado; plantilla aprobada por Meta |
| RF-25 | El cliente puede consultar o cancelar su orden antes del pago | S | Tools `get_order_status` y `cancel_order` limitadas a órdenes del mismo cliente |

### 4.4 Pagos

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-30 | Enviar al cliente el **QR del propio negocio** con el monto y la referencia a pagar | M | ADR-0013/0014; ningún flujo usa cuentas de la plataforma |
| RF-31 | (Fase de escala) Confirmar automáticamente con el conector a la API del banco del negocio | C | El callback autenticado + consulta de estado crea `payments(BANK_API)` |
| RF-32 | Recibir comprobantes, aplicar reglas antifraude y priorizar la revisión | M | pgTAP 02 #15–#18; el cliente recibe "en verificación", nunca "confirmado" |
| RF-33 | Aprobar evidencias en el dashboard (OWNER/ADMIN, **siempre** con MFA); rechazarlas (OWNER/ADMIN/STAFF; rechazar no mueve dinero). Solo las RPC de revisión aprueban o rechazan: el backend no cambia esos estados ni los datos ya verificados. Reembolsar, cancelar órdenes pagadas o en revisión y confirmar sin seña: OWNER/ADMIN con MFA; STAFF solo cancela antes del pago | M | pgTAP 02 #19–#24, 06, 08 |
| RF-34 | Ninguna orden se confirma sin pago suficiente registrado | M | pgTAP 02 #12, #21 |

### 4.5 Dashboard, auditoría y facturación

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-40 | Inicio de sesión con MFA para OWNER/ADMIN; roles OWNER, ADMIN, STAFF, AUDITOR | M | pgTAP 01 |
| RF-41 | Bandeja de conversaciones con la opción de tomar y devolver al bot | M | Prueba E2E |
| RF-42 | Panel del día: ventas, citas, pagos pendientes, TC vigente, costo de IA y de mensajes | S | Prueba E2E |
| RF-43 | Bitácora de auditoría consultable por OWNER y AUDITOR | S | Toda acción financiera aparece con actor y antes/después |
| RF-44 | (Fase de escala) Emisión de factura vía un proveedor SIAT autorizado, contratado por el negocio | W (MVP) | Prueba de contrato del adaptador; idempotencia por orden |
| RF-45 | Exportación de datos del tenant (baja o pedido del titular de los datos) | S | Export en un formato abierto; registro en auditoría |

### 4.6 Plataforma

| ID | Requisito | Prior. | Criterio de aceptación |
| :--- | :--- | :--- | :--- |
| RF-50 | Alta de un tenant nuevo (organización, número de WhatsApp vía Embedded Signup, cuenta bancaria, horario) | M | Checklist de onboarding completado en menos de 30 min |
| RF-51 | Cuotas por plan (respuestas del bot, costo de IA) con alertas | S | Alerta al 80 %; degradación a plantilla al 100 % |
| RF-52 | Otros canales (Instagram, web chat) | W | — |
| RF-53 | El alta de tenants rechaza rubros prohibidos por la política de WhatsApp | M | Enum `business_vertical` + checklist de onboarding |
| RF-54 | Cobro de la suscripción fija al negocio fuera del flujo de pagos de sus clientes | M | Ningún cálculo de comisión sobre ventas o pagos en el código |

## 5. Restricciones

Ver `SYSTEM_DESIGN.md` §1.3 y `docs/research/2026-10-15-restricciones-externas.md` (EXT-01 … EXT-17).

## 6. Matriz de trazabilidad

| Requisito | Diseño | ADR | Verificación | Fase del roadmap |
| :--- | :--- | :--- | :--- | :--- |
| RF-01 | §4.4, §8.3, §10.1 | 0005, 0006, 0011 | Seguridad del webhook, caos | 1 |
| RF-02, RF-07 | §7.2–7.4 | 0003, 0007 | Evals, guardrails | 2 |
| RF-03, RF-04 | §7.1 | — | Unit + E2E | 2 |
| RF-05 | §4.4 | — | Integración | 2 |
| RF-06 | §10.1 | 0006 | Unit | 1 |
| RF-10 … RF-12 | §5.4, §8.1 | 0003, 0007, 0016 | pgTAP 02, 03, 11 | 2 / 5 |
| RF-13, RF-14 | §5.4 | 0012, 0009 | pgTAP 04 | 2 / 7 |
| RF-20, RF-21 | §5, §6 | 0004, 0007 | pgTAP 02, 03 | 2 |
| RF-22 … RF-25 | §6, §7.2 | 0004 | pgTAP 03 | 4 |
| RF-30 … RF-34 | §4.5 | 0008, 0013, 0014 | pgTAP 02 | 3 (RF-31: escala) |
| RF-08, RF-09 | §7, §10.1 | 0006, 0014 | pgTAP 13, 14; unit e integración del consentimiento; evals | 1 / 2 |
| RF-53, RF-54 | — | 0014 | Revisión | 6 |
| RF-40 … RF-45 | §8.2 | 0002 | pgTAP 01, E2E | 5 / 7 |
| RF-50, RF-51 | §7.4, §11 | 0006 | E2E, métricas | 6 |
