# Marco legal y modelo de negocio (verificado al 15-oct-2026)

> **No es asesoramiento legal ni tributario.** Resume normas y fuentes públicas para preparar la reunión con un abogado y un contador en Bolivia. Las decisiones marcadas ⚖️ requieren su confirmación por escrito.

## 1. Idea central: vender software, no servicios financieros ni tributarios

SCZ-B2B-Core gana dinero **cobrando una suscripción mensual** a cada negocio por usar el software: atención por WhatsApp, cotización, agenda y revisión de pagos. Para que el negocio sea viable con un equipo pequeño, el producto se diseña para **no entrar en actividades reguladas** que exigen licencias, capital y supervisión:

| Actividad regulada | Quién la regula | Qué exige | Cómo la evitamos |
| :--- | :--- | :--- | :--- |
| **Pasarela de pagos / fintech de pagos** | ASFI (R.D. BCB 069/2021 y 079/2022; D.S. 5384 y Reglamento de Empresas de Tecnología Financiera, 2025) | Constituirse como entidad financiera, licencia de la ASFI, garantía de seriedad de 5.000 UFV (≈ Bs 14.000) con la solicitud, entorno controlado de pruebas e inspecciones. El plazo de adecuación venció el **31-dic-2025** | **Nunca tocamos el dinero.** Cada negocio cobra con **su propio QR**, en **su propia cuenta**. No cobramos comisión por transacción |
| **Proveedor de sistemas de facturación** | SIN (RND 102100000011) | Autorización del SIN por 3 años, pruebas piloto por cliente, domicilio y RNC en el régimen general, controles periódicos | **No emitimos facturas por el negocio.** En el MVP, el negocio factura con su sistema actual. Más adelante, una integración con un proveedor **ya autorizado** que el negocio contrata directamente |
| **Datos de salud** (clínicas) | Política de Meta; buenas prácticas (no hay ley general de datos) | Cuidado reforzado | Primer rubro: **importadoras y ferreterías**. Las clínicas vienen después y solo para agenda, sin datos clínicos |

## 2. Qué cambia en el producto (versión "mínimo regulatorio")

| Tema | Antes (v2.1) | Ahora (MVP comercial) | Por qué |
| :--- | :--- | :--- | :--- |
| Pagos | Camino A (API bancaria/OpenBCB) preferido; camino B de respaldo | **Camino B por defecto:** el bot envía **el QR estático del propio negocio**, el cliente paga en su app bancaria, manda el comprobante, las reglas lo clasifican y **una persona del negocio lo aprueba**. El camino A queda como **conector opcional** (fase posterior) solo con la API **del banco del negocio** y sus credenciales | Cero manejo de fondos y cero integración financiera en el MVP. El negocio ya usa QR hoy; solo le ahorramos la revisión manual |
| OpenBCB | Adaptador opcional | **Fuera del roadmap** | Trámite por negocio ante el BCB, VPN o IP fija y reacreditación semestral: mucho costo para una PyME |
| Comisiones | No definido | **Prohibido** cobrar un porcentaje de los pagos o retener montos | Que nuestro ingreso dependa del flujo de pagos acerca el modelo a "facilitador de pagos" ⚖️ |
| Facturación SIAT | Fase 7 con proveedor | **Fuera del MVP.** Integración futura con un proveedor autorizado, contratado por el negocio | No ser "proveedor de sistemas de facturación" |
| NIT / CI por chat | Tool `update_billing_info` | **Eliminada.** Si se necesitan datos de facturación, se piden con un **formulario web seguro** de enlace temporal | La política de WhatsApp prohíbe pedir números de identificación personal o de cuentas financieras por el chat |
| Onboarding de WhatsApp | Tech Provider desde la fase 1 | **Piloto en "modelo directo":** cada negocio es dueño de su portafolio de Meta, su WABA, su app y su token; nosotros configuramos como proveedores de software. **Tech Provider** solo al escalar (requiere nuestra empresa ya constituida y verificada) | Arrancar sin App Review y sin depender de plazos de Meta |
| Clínicas | Rubro 2 del MVP | Rubro 2 **después del piloto**, solo agenda | Datos de salud y restricciones de Meta |

## 3. Obligaciones que sí aplican (y cómo las cumple el diseño)

### 3.1 Ley 453 de derechos de usuarios y consumidores (aplica al negocio frente a su cliente final)

| Obligación | Artículo | Cumplimiento en el producto |
| :--- | :--- | :--- |
| Información veraz, completa y oportuna | Art. 13, 14.1 | El bot solo dice datos que vienen de las herramientas (*grounding* numérico) |
| **Precio total en moneda nacional, con impuestos** | Art. 14.4 | Cotizaciones siempre en **Bs**, con el IVA reflejado (ADR-0012). Los precios en USD se convierten antes de mostrarse |
| La oferta obliga: entregar en los términos ofertados | Art. 18.1 | Por eso el precio se **congela** con vigencia (`valid_until`) y el PDF deja constancia. Una cifra inventada por la IA tendría efecto legal: el diseño lo impide |
| Canales permanentes de consultas y reclamos | Art. 40.7–40.8 | Traspaso a humano siempre disponible; registro de la conversación |
| Proteger el secreto de la información del usuario | Art. 40.6 | RLS, cifrado en tránsito, mínimo privilegio, no compartir datos entre tenants |
| Contratos de adhesión claros, en castellano, sin cláusulas abusivas; modelos con aprobación previa de la autoridad | Art. 19, 21, 22 | ⚖️ Si el bot llega a "contratar" con consumidores, revisar con el abogado. El MVP solo cotiza y agenda |

### 3.2 Políticas de WhatsApp Business (contractuales; su incumplimiento puede cerrar el número)

* **Opt-in:** solo escribir a quien dio su número y aceptó recibir mensajes. Respetar inmediatamente los pedidos de baja. → Los mensajes proactivos (recordatorios) requieren un registro de consentimiento por cliente.
* **Escalamiento:** toda experiencia automatizada debe ofrecer una vía clara hacia un humano. → Tool `handoff_to_human` + texto en el aviso inicial.
* **Datos prohibidos en el chat:** números completos de tarjeta, de cuentas financieras o de identificación personal. → Sin pedir CI/NIT por el chat. El QR del negocio se envía como imagen, sin escribir el número de cuenta.
* **Salud:** no usar WhatsApp para información de salud donde la regulación lo impida, y no reenviar información de un cliente a otro.
* **Política de privacidad publicada:** obligatoria. → Plantilla en esta carpeta (pendiente de redacción final con el abogado).
* **IA de propósito general prohibida** (desde 15-ene-2026): el bot solo atiende temas del negocio.
* **Productos prohibidos:** armas, alcohol y tabaco, medicamentos y productos médicos, entre otros. → Filtro de alta de tenants: no aceptar rubros prohibidos.

### 3.3 Propiedad del software (Ley 1322 y D.S. 24582) — **urgente**

* El derecho de autor nace con la creación; el registro ante la Dirección Nacional de Derecho de Autor (SENAPI) **no es obligatorio**, pero da certeza y publicidad (D.S. 24582, art. 22–23).
* El software hecho por empleados o por encargo pertenece a quien lo encarga y asume el riesgo, **salvo contrato en contrario** (art. 12). Las cesiones de derechos patrimoniales deben constar por contrato (art. 11).
* **Acción:** antes de seguir programando entre dos o más personas, firmar un **acuerdo de socios / titularidad** que diga de quién es el código, cómo se reparten las ganancias, qué pasa si alguien se va y quién decide. Sin ese papel, "algo de nosotros" no está protegido.

### 3.4 Datos personales e IA

Sin ley general de datos (sep-2026) ni ley de IA promulgada. Igual se aplican: aviso de IA, finalidad limitada, retención definida, borrado a pedido y registro de proveedores (`SYSTEM_DESIGN.md` §8.7). Esto además es requisito de Meta.

## 4. Requisitos para operar la empresa

| Paso | Detalle | Costo y plazo estimados (2026) |
| :--- | :--- | :--- |
| 1. Elegir la forma jurídica ⚖️ | **Empresa unipersonal** (una persona titular; trámite en línea) o **SRL** (dos o más socios; mayor protección patrimonial) | Unipersonal ≈ Bs 930–2.430, 5–7 días hábiles · SRL ≈ Bs 3.447–7.647, 15–30 días hábiles |
| 2. Registro de comercio | SEPREC (control de homonimia + inscripción) | Incluido arriba |
| 3. NIT | SIN, con la matrícula de SEPREC | Gratuito, ≈ 1 día |
| 4. Régimen tributario ⚖️ | Base de cálculo: **régimen general** (IVA 13 %, IT 3 %, IUE 25 % sobre utilidades). Existe un proyecto de "Régimen Tributario Especial" (5 % bimestral para ventas < Bs 400.000/año, anunciado en feb-2026); confirmar con el contador si está vigente y si aplica a servicios de software | — |
| 5. Facturar nuestras suscripciones | Modalidad en línea; para bajo volumen, los sistemas que ofrece el SIN | — |
| 6. Licencia municipal | Plataforma del municipio | Gratuita; patente anual |
| 7. Si contratamos personal | Gestora (AFP) y CNS | — |
| 8. Verificación del negocio en Meta | Necesaria para ser Tech Provider (fase de escala) | — |
| 9. Contratos | Acuerdo de socios · Términos de servicio SaaS + acuerdo de tratamiento de datos con cada negocio · Política de privacidad · Plantilla de aviso para los clientes finales del negocio | Honorarios del abogado |

Fuente de costos: [Bolivia Empresas](https://boliviaempresas.com/cuanto-cuesta-crear-una-empresa-en-bolivia/) y [SEPREC, guía de trámites](https://www.seprec.gob.bo/wp-content/uploads/2025/12/Guia-DE-TRAMITES-ACTUALIZADO_v1.pdf). Confirmar las tarifas vigentes.

## 5. Modelo de ingresos propuesto

| Elemento | Propuesta | Nota |
| :--- | :--- | :--- |
| Cobro | **Suscripción mensual fija** por negocio, en Bs, facturada por nuestra empresa | Nunca un % de las ventas ni de los pagos del negocio |
| Planes | Por cupo de **respuestas del bot por mes** y por número de usuarios del panel | Cubre el costo variable de WhatsApp (≈ US$ 0,0113 por respuesta después de 1.000 gratis) y de la IA |
| Alta (*setup*) | Pago único por configuración: catálogo, número de WhatsApp, QR, horarios | Cubre el onboarding en modelo directo |
| Cómo nos pagan | Con **nuestro** QR, a **nuestra** cuenta, contra **nuestra** factura | Es una venta normal de servicios, no un servicio de pagos |
| Costos que se trasladan | Mensajes de WhatsApp: los paga cada negocio directamente a Meta con su tarjeta (modelo directo) **o** se incluyen en el plan (modelo Tech Provider) | Decidir por plan |

**Fórmula para fijar el precio mínimo del plan:**
`precio_mínimo = (costo_fijo_mensual / n.º_de_negocios) + costo_IA_por_negocio + costo_WhatsApp_por_negocio (si se incluye) + margen`
Con los datos reales de `private.llm_calls` y `wa_billable_messages` del piloto se fija el precio definitivo.

## 6. Checklist legal antes del piloto

- [ ] Acuerdo de socios / titularidad del software firmado
- [ ] Forma jurídica elegida y empresa inscrita (SEPREC + NIT)
- [ ] Régimen tributario confirmado por el contador
- [ ] Opinión del abogado: modelo "solo software, sin manejo de fondos" fuera del alcance de la ASFI (ADR-0013)
- [ ] Términos de servicio + acuerdo de tratamiento de datos para negocios
- [ ] Política de privacidad publicada (requisito de Meta)
- [ ] Aviso de IA y de privacidad para los clientes finales (texto aprobado por cada negocio)
- [ ] Filtro de rubros prohibidos por Meta en el alta de tenants
- [ ] (Opcional) Registro del software en SENAPI

## 7. Fuentes

* ASFI — [Reglamento de Empresas de Tecnología Financiera (resumen de Ferrere)](https://www.ferrere.com/es/novedades/asfi-aprueba-formalmente-el-reglamento-para-empresas-fintech/) · [Análisis de BDA Abogados](https://www.bda-lawfirm.com/articles/regulacion-empresas-de-tecnologia-financiera/) · [D.S. 5384](https://www.asfi.gob.bo/sites/default/files/2025-08/Decreto%20Supremo%20N%C2%B0%205384%20de%20fecha%207%20de%20mayo%20de%202025.pdf)
* BCB — [Pasarelas de pago requieren autorización de la ASFI (CP63/2022)](https://www.bcb.gob.bo/webdocs/11_comunicados/cp_pasarelas_pago.pdf) · [Guía OpenBCB](https://www.bcb.gob.bo/webdocs/open_bcb/GUIA%20OPEN%20BCB%20V.1.0.pdf)
* SIN — [RND 102100000011, Sistema de Facturación](https://www.impuestos.gob.bo/wp-content/uploads/2025/10/RND11.pdf) · [Régimen Tributario Especial (proyecto, feb-2026)](https://www.impuestos.gob.bo/index.php/nota_prensa/nuevo-regimen-tributario-especial-permitira-formalizar-a-pequenos-proveedores-con-pago-unico-del-5-bimensual/) · [Ley 1733](https://www.impuestos.gob.bo/wp-content/uploads/2026/05/L17332051NCPP.pdf)
* [Ley 453 de derechos de usuarios y consumidores](https://www.lexivox.org/norms/BO-L-N453.html)
* [Ley 1322 de derecho de autor](https://www.lexivox.org/norms/BO-L-1322.html) · [D.S. 24582, Reglamento del soporte lógico](https://www.lexivox.org/norms/BO-RE-DS24582.html)
* [WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/)
* SEPREC — [Guía de trámites](https://www.seprec.gob.bo/wp-content/uploads/2025/12/Guia-DE-TRAMITES-ACTUALIZADO_v1.pdf) · [Costos (Bolivia Empresas)](https://boliviaempresas.com/cuanto-cuesta-crear-una-empresa-en-bolivia/)
