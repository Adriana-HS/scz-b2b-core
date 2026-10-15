# ADR-0006: Solo la WhatsApp Business Platform (Cloud API) oficial

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §1.3, §8.3, §10.1, §11

## Contexto
El diagrama de la v1 incluía "WhatsApp Cloud API / Evolution API". Evolution API y librerías similares automatizan WhatsApp Web (no son la API oficial): contravienen los términos de Meta, exponen el número a bloqueo, no envían webhooks firmados con `X-Hub-Signature-256` (lo que invalida el control §6.1 de la v1) y no ofrecen plantillas ni garantías de entrega.

## Decisión
Usar exclusivamente la **Cloud API oficial** (número verificado, WABA del tenant, token de usuario del sistema). Diseñar para sus reglas:
* **ventana de 24 h:** fuera de ella, solo plantillas aprobadas;
* **plantillas** por categoría (utilidad para recordatorios y confirmaciones; nunca texto promocional en una plantilla de utilidad, porque Meta puede recategorizarla);
* **webhooks de estado** y de recategorización de plantillas;
* **costos por mensaje** medidos por tenant.

### Actualización (15-oct-2026, ver `docs/research/2026-10-15-restricciones-externas.md` §2)
* **Política de IA:** desde el 15-ene-2026 Meta prohíbe los chatbots de IA de propósito general. Nuestro agente es de atención del negocio y se limita a su catálogo, agenda y pagos; las evals incluyen casos de "fuera de alcance".
* **Tech Provider:** para conectar los números de los negocios con *Embedded Signup* se requiere verificar la empresa y pasar App Review (fase 1 del roadmap).
* **Costos:** varios BSP informan que desde el 1-oct-2026 los mensajes de servicio se cobran (1.000 gratis por número y mes; Bolivia ≈ US$ 0,0113 cada uno). Diseño: una sola respuesta por turno, mensajes interactivos y cupo por plan.
* **Coexistencia:** opción de onboarding para que el dueño use el mismo número desde la app; el webhook `smb_message_echoes` pasa la conversación a modo `HUMAN`.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Evolution API / Baileys | Gratis, sin aprobación | ToS, bloqueos, sin firma, sin soporte; inaceptable para pagos y datos de salud |
| BSP intermediario (proveedor de soluciones de Meta) | Soporte y onboarding | Costo adicional; se puede adoptar después sin cambiar el puerto `MessagingPort` |

## Consecuencias
* (+) Webhooks autenticados, plantillas y menor riesgo de bloqueo.
* (−) Onboarding de cada tenant (verificación del negocio y del número) y costo por mensaje de plantilla.

## Verificación
Prueba de seguridad: un webhook sin firma válida se rechaza. Revisión de que no existan dependencias no oficiales en `pyproject.toml`.
