# ADR-0009: Facturación SIAT mediante puerto y proveedor homologado

* **Estado:** Aceptado (mantiene ADR-005 de la v1 y agrega el modelo de datos)
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §8.7 · migración 0002 (`invoices`)

## Contexto
Homologar un sistema propio ante el SIN (firma digital, servicios del SIAT, contingencias) toma meses y no es el núcleo del producto. La v1 lo resolvía bien con un `BillingPort`, pero no tenía dónde guardar el resultado (CUF, CUFD, número, estado, contingencia).

## Decisión
* `BillingPort` con adaptadores por proveedor homologado; se activa por tenant.
* Tabla `invoices` (una vigente por orden): `status` (`PENDING/ISSUED/CONTINGENCY/FAILED/VOIDED`), `cuf`, `cufd`, `invoice_number`, datos del comprador, PDF/XML en Storage y `provider_payload`.
* La emisión es un trabajo de la cola (reintento idempotente por `order_id`), disparado por la regla del tenant (al confirmar el pago o al completar la orden).
* El modo contingencia lo gestiona el proveedor; el sistema refleja su estado.

## Consecuencias
* (+) Cumplimiento sin homologación propia; se puede cambiar de proveedor.
* (−) Costo por tenant y dependencia del SLA del proveedor (pregunta abierta P-2).

## Verificación
Pruebas de contrato del adaptador con respuestas grabadas del proveedor; prueba de idempotencia (dos emisiones de la misma orden producen una factura).
