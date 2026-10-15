# ADR-0013: La plataforma no custodia fondos; cada negocio cobra en su propia cuenta

* **Estado:** Aceptado (requiere confirmación legal antes del piloto)
* **Fecha:** 2026-10-15
* **Relacionado:** ADR-0008 · `docs/research/2026-10-15-restricciones-externas.md` §1

## Contexto
En Bolivia las pasarelas de pago requieren autorización de la ASFI (R.D. BCB 069/2021 y 079/2022), y el D.S. 5384 (2025) exige licencia a las empresas fintech de "pagos". Si SCZ-B2B-Core recibiera el dinero de los clientes finales y luego lo liquidara a los negocios, o retuviera una comisión del flujo, entraría en actividad regulada, con requisitos de capital, licencia y supervisión que no corresponden a un SaaS en etapa inicial.

## Decisión
* **El dinero nunca pasa por cuentas de la plataforma.** El QR se genera con las credenciales del **negocio** y a nombre de **su** cuenta (API de su banco u OpenBCB tramitado por el propio negocio). La plataforma solo orquesta: genera la solicitud, muestra el QR y recibe y verifica la confirmación.
* Las credenciales bancarias de cada negocio se guardan como secretos por tenant (Vault), nunca en tablas.
* Se descartan integradores que **cobran y liquidan el neto**, salvo que demuestren autorización de la ASFI.
* El cobro del SaaS al negocio (suscripción fija) es independiente y va por un canal propio, sin descontarse de las ventas. **No se cobran comisiones porcentuales sobre ventas ni pagos** (ADR-0014).

## Consecuencias
* (+) Menor riesgo regulatorio; el negocio conserva la relación con su banco y su conciliación.
* (−) El onboarding de pagos es por negocio (convenio con el banco), lo que alarga la activación del camino A. Mientras tanto se usa el camino B (ADR-0008).
* ⚖️ Pendiente: opinión legal escrita sobre el modelo "solo software" (EXT-01).

## Verificación
Revisión de arquitectura: ningún adaptador de `PaymentPort` puede recibir fondos ni credenciales de una cuenta de la plataforma. Checklist legal firmado antes del piloto.
