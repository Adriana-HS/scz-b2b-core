# ADR-0008: Modelo de confianza de pagos — confirmación bancaria primero, comprobante como evidencia

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §4.5, §8.1 · migraciones 0002 (`payment_evidence`, `payments`), 0006 (`approve_payment_evidence`) · pruebas `02_*` #15–#24

## Contexto
La v1 extrae datos de la captura con un LLM y, si el monto coincide, marca `PRE_VERIFIED`, **genera un PDF de confirmación y lo envía al cliente**. Problemas:
* Una captura **no prueba un pago**: puede editarse o fabricarse por completo. El SHA-256 de los bytes se evade recortando un píxel o re-comprimiendo la imagen.
* No se verifica que el **beneficiario o la cuenta destino** sea el negocio (un pago real a otra persona pasaría), ni que la fecha sea posterior a la orden.
* La confirmación al cliente contradice el ADR-004 de la v1: libera antes de que exista una verificación real.
* `UNIQUE(bank_origin, transaction_number)` sin normalizar ("BNB" contra "Banco Nacional de Bolivia", ceros a la izquierda).

En oct-2025 el BCB lanzó **OpenBCB**, con generación gratuita de QR, consulta de estado de QR en tiempo real y notificación de pagos. Varios bancos ofrecen APIs equivalentes.

## Decisión
* **Camino A (preferido):** QR dinámico con monto y referencia de la orden, generado vía `PaymentPort` (OpenBCB o API del banco del tenant). Solo una confirmación bancaria verificada (callback autenticado + consulta de estado) crea `payments` con `confirmed_via = 'BANK_API'`. La confirmación al cliente llega después de eso.
* **Camino B (respaldo):** QR estático + comprobante → `payment_evidence`. Las reglas deterministas (monto, beneficiario y últimos 4 dígitos de la cuenta, ventana de fecha, pHash contra evidencias previas, confianza del OCR, unicidad normalizada global de banco + nro. de transacción) clasifican la evidencia en `AUTO_CHECK_PASSED` o `NEEDS_REVIEW`. **Siempre** se requiere aprobación humana (OWNER/ADMIN con MFA). Al cliente se le dice "recibido, en verificación".
* Todo intento queda registrado, incluidos los duplicados (UNIQUE parciales).

### Actualización (15-oct-2026)
La investigación de integración (ver `docs/research/…` §1) ajusta el camino A: el adaptador preferido para el MVP es la **API QR del banco de cada negocio** (por ejemplo, BNB: QR dinámico, sandbox y webhook, con convenio previo). **OpenBCB** queda como adaptador opcional, porque su habilitación es por negocio (nota formal, VPN o IP fija y reacreditación semestral). En todos los casos el dinero va a la cuenta del negocio (ADR-0013).

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Auto-aprobar si el OCR coincide (v1) | Rápido | Fraude trivial con comprobantes fabricados |
| Solo revisión humana sin reglas | Seguro | Lento; no prioriza; no detecta reuso entre tenants |
| Conciliación por extracto bancario diario | Sin API | Confirma con horas de retraso; útil como control complementario |

## Consecuencias
* (+) El dinero solo se da por recibido con una confirmación bancaria o una decisión humana auditada.
* (+) La unicidad global de la transacción detecta el reuso del mismo comprobante en otro negocio **sin** exponer datos de ese otro tenant.
* (−) El camino B mantiene trabajo humano (reducido y priorizado). El objetivo comercial es migrar cada tenant al camino A.
* Pregunta abierta P-1: requisitos de alta en OpenBCB y en los bancos.

## Verificación
pgTAP `02_*` #14–#24; métrica `needs_review_rate`; prueba de seguridad con comprobantes manipulados (recorte, re-compresión, monto editado).
