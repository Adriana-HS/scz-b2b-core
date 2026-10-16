# ADR-0014: Alcance de mínimo regulatorio para el MVP comercial

* **Estado:** Aceptado (confirmación legal pendiente: checklist de `docs/legal/marco-legal-y-modelo-de-negocio.md` §6)
* **Fecha:** 2026-10-15
* **Relacionado:** ADR-0006, ADR-0008, ADR-0009, ADR-0013 · `docs/legal/marco-legal-y-modelo-de-negocio.md`

## Contexto
El objetivo es un negocio rentable con un equipo pequeño. Tres funcionalidades del diseño v2.1 acercaban el producto a actividades reguladas o costosas: la confirmación automática de pagos con APIs bancarias u OpenBCB (ASFI y BCB), la emisión de facturas (SIN, autorización de proveedores) y el onboarding como *Tech Provider* de Meta (verificación de empresa y App Review). Además, la política de WhatsApp prohíbe pedir por el chat números de identificación personal o de cuentas financieras.

## Decisión
Para el MVP comercial:
1. **Pagos solo con el QR estático del propio negocio** + comprobante + reglas + aprobación humana (camino B de ADR-0008). Sin integraciones financieras ni OpenBCB. El conector a la API **del banco del negocio** queda para la fase de escala.
2. **Modelo de ingresos por suscripción fija.** Prohibido cobrar porcentajes sobre las ventas o los pagos de los negocios, o retener montos.
3. **Sin facturación SIAT** en el MVP: el negocio sigue con su sistema. Nunca seremos "proveedor de sistemas de facturación"; en la fase de escala, solo integración con un proveedor autorizado que el negocio contrate.
4. **No se piden CI, NIT ni números de cuenta por el chat.** La tool `update_billing_info` se elimina. Si hacen falta, se usa un formulario web con enlace temporal.
5. **Onboarding de WhatsApp en modelo directo** para el piloto (cada negocio es dueño de su WABA, su app y su token). *Tech Provider* al escalar.
6. **Primer rubro: importadoras y ferreterías.** Las clínicas vienen después del piloto, solo para agenda y sin datos clínicos.
7. **Consentimiento:** registro de opt-in y opt-out por cliente; los mensajes proactivos (plantillas) solo se envían a quienes aceptaron.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Mantener el camino A en el MVP | Confirmación automática | Convenios por banco, posible encuadre ASFI, más tiempo hasta vender |
| Ser agregador de pagos con licencia | Ingreso por transacción | Licencia ASFI, capital, supervisión: inviable para el equipo actual |
| Emitir facturas como proveedor autorizado | Producto más completo | Autorización del SIN, pruebas por cliente, responsabilidad tributaria |

## Consecuencias
* (+) El MVP no requiere licencias más allá de la empresa y el NIT; se puede vender antes.
* (+) El valor central (atención 24/7, cotización exacta, menos revisión manual de comprobantes) se mantiene.
* (−) La revisión de pagos sigue siendo humana, aunque priorizada y asistida. Es el argumento de venta para el plan superior con conector bancario.
* El modelo de datos no cambia: las tablas de pagos bancarios y de facturas ya existen y se activan en la fase de escala.
* **Privilegios (SEC-02, migración 11):** mientras no exista el conector bancario, `app_backend` **no tiene INSERT en `payments`**; el único camino al libro de pagos es `approve_payment_evidence` (OWNER/ADMIN con MFA). Un pago `BANK_API` exige además el actor `SYSTEM` explícito. La fase 7 vuelve a otorgar el INSERT en su propia migración, junto con el callback autenticado y la consulta de estado de vuelta (ADR-0008), y con pruebas pgTAP que lo cubran.

## Verificación
Revisión de arquitectura en cada PR que toque pagos o mensajería (skill `scz-revision-seguridad`); checklist legal firmado antes del piloto.
