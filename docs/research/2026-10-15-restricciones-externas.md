# Registro de restricciones externas (verificado al 15-oct-2026)

Lo que el proyecto **no controla** y puede cambiar: regulación, tarifas, políticas de plataformas y proveedores. Cada fila indica la fuente, el impacto en el diseño, la decisión tomada y **cada cuánto revisarla**. Este registro se revisa al inicio de cada fase del roadmap. Si algo cambia, se actualiza la fila y, si corresponde, el ADR afectado.

> No es asesoramiento legal, tributario ni financiero. Los puntos marcados ⚖️ deben confirmarse con un abogado o contador boliviano antes de operar.

## Resumen de riesgos

| ID | Restricción | Nivel | Decisión | Revisar |
| :--- | :--- | :--- | :--- | :--- |
| EXT-01 | Recibir o retener fondos de terceros = actividad regulada por la ASFI ⚖️ | **Alto** | La plataforma **nunca custodia dinero**; los fondos van directo a la cuenta del negocio (ADR-0013) | Semestral |
| EXT-02 | OpenBCB exige trámite por cada negocio, VPN o IP fija y reacreditación semestral | Medio | Adaptador disponible, pero no es el camino del MVP; se prioriza la API del banco de cada negocio | Semestral |
| EXT-03 | Las APIs QR bancarias requieren convenio entre el negocio y su banco | Medio | `PaymentPort` con un adaptador por banco; arrancar con 1 banco | Por banco |
| EXT-04 | WhatsApp cobra mensajes de servicio desde el 1-oct-2026 (1.000 gratis por número/mes), según varios BSP | **Alto** (margen) | Medir mensajes por número; agrupar respuestas; incluirlo en el precio por plan | Trimestral (Meta cambia tarifas el día 1 de cada trimestre) |
| EXT-05 | Meta prohíbe chatbots de IA de propósito general (desde 15-ene-2026) | Medio | El bot es de atención del negocio y no responde temas ajenos al negocio | Semestral |
| EXT-06 | Para conectar números de clientes hay que ser **Tech Provider** de Meta | Medio | Verificar la empresa y pasar App Review en la fase 1 | Una vez + cambios de Meta |
| EXT-07 | Límite de mensajería por portafolio (los nuevos empiezan en 250 contactos fuera de ventana por 24 h) | Bajo | Las plantillas proactivas son pocas; monitorear el tier | Trimestral |
| EXT-08 | Ley 1733: IVA "por fuera"; nuevos formatos de factura en piloto (1-oct al 16-nov-2026) ⚖️ | **Alto** (exactitud) | Modelo de IVA con régimen vigente por fecha (ADR-0012) | Mensual hasta su vigencia |
| EXT-09 | El SIN asignará la modalidad de facturación en línea a quienes aún no la tienen (RND 102600000038) | Medio | Facturación vía proveedor homologado (ADR-0009) | Trimestral |
| EXT-10 | Tipo de cambio flotante desde jun-2026; el TCO se publica cada día hábil | Medio | Historial de TC + control de antigüedad (ADR-0007) | Continuo (job diario) |
| EXT-11 | Pagos al exterior con tarjeta boliviana: débito con tope de US$ 500; crédito según el banco | Medio | Pagar Meta, Supabase y el LLM con tarjeta de **crédito**; prever el costo en Bs | Semestral |
| EXT-12 | Bolivia sin ley general de datos personales; proyecto de ley de IA en curso ⚖️ | Medio | Privacidad por diseño + aviso de IA (SYSTEM_DESIGN §8.7) | Semestral |
| EXT-13 | GitHub Free: sin ramas protegidas en repos privados; CodeQL y secret scanning de pago en privados | Bajo | GitHub Pro (gratis con el Student Pack) o disciplina + gitleaks/bandit/semgrep | Anual |
| EXT-14 | Proveedor de LLM: precios, límites de tasa y retiro de modelos cambian | Medio | Puerto `LLMPort`, modelo fijado por versión, evals antes de cambiar, costo medido | Mensual |
| EXT-15 | Supabase: el plan gratuito pausa proyectos; PITR es un add-on | Bajo | Plan Pro en producción (ADR-0010) | Anual |

---

## 1. Pagos QR

### 1.1 Marco regulatorio ⚖️

* **Pasarelas de pago:** el BCB las define como canales de pago (R.D. 069/2021, profundizada por R.D. 079/2022, vigente desde el 21-sep-2022). **Requieren autorización de la ASFI** antes de operar. — [BCB CP63/2022](https://www.bcb.gob.bo/webdocs/11_comunicados/cp_pasarelas_pago.pdf)
* **Fintech:** el D.S. 5384 (7-may-2025) crea las Empresas de Tecnología Financiera (categoría "pagos", entre otras). Deben constituirse como entidades financieras con licencia de la ASFI. El detalle lo fija el reglamento de la ASFI. — [D.S. 5384](https://www.asfi.gob.bo/sites/default/files/2025-08/Decreto%20Supremo%20N%C2%B0%205384%20de%20fecha%207%20de%20mayo%20de%202025.pdf), [análisis](https://www.arielagramont.com/blog/preguntas-y-respuestas-sobre-el-decreto-supremo-n-5384-de-empresas-de-tecnologia-financiera-etf-fintech)

**Consecuencia para el diseño (ADR-0013):** SCZ-B2B-Core es **software**, no un intermediario financiero. No abre cuentas, no recibe fondos de los clientes finales ni liquida a los negocios. Los QR se generan **con las credenciales y a nombre de la cuenta de cada negocio**, y el sistema solo lee la confirmación. Que un modelo "solo software" quede fuera del alcance regulado debe confirmarlo un abogado antes del piloto.

### 1.2 Opciones de integración

| Opción | Quién recibe el dinero | Confirmación automática | Requisitos | Costo | Uso en el diseño |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **API del banco del negocio** (p. ej., BNB: QR dinámico, sandbox, webhook) | Cuenta del negocio | Sí (webhook registrado en el portal del banco) | Convenio del negocio con su banco; credenciales (Account ID, Authorization ID, User Key en el caso del BNB) | Según el banco | **Camino A preferido.** Un adaptador por banco; empezar con 1 |
| **OpenBCB** (BCB, oct-2025) | Cuenta transitoria en el BCB → MLD → cuenta destino del negocio | La guía dice que "notifica a la EEPP"; el mecanismo (callback o consulta) no está documentado públicamente | Por **cada** negocio: nota a la Gerencia General del BCB, declaración jurada, formulario de conexión con **VPN sitio a sitio o IP pública fija**, pruebas con GEF/GSIS, **reacreditación semestral**; suspensión por inactividad de más de 2 meses | Gratuito (cuenta transitoria exenta) | Adaptador opcional para negocios que tramiten su habilitación |
| **Integradores de terceros** (servicios que exponen una API QR sobre bancos) | Varía: algunos depositan **en la cuenta del negocio**, otros **cobran y liquidan el neto** | Sí (webhook firmado en algunos) | Contrato con el integrador | Ej.: integración única Bs 490–3.200 + mensualidad Bs 199–850 + comisión 0–2,5 % | Solo si el dinero va a la cuenta del negocio **o** el integrador tiene autorización ASFI (verificar antes de contratar) |
| **QR estático + comprobante** | Cuenta del negocio | No (requiere revisión humana) | Ninguno | Bs 0 | **Camino B (respaldo)**, ADR-0008 |

Fuentes: [Guía técnica OpenBCB v1.0](https://www.bcb.gob.bo/webdocs/open_bcb/GUIA%20OPEN%20BCB%20V.1.0.pdf) · [Lanzamiento OpenBCB (CP44/2025)](https://www.bcb.gob.bo/webdocs/files_noticias/CP-44%20LANZAMIENTO%20OPENBCB%20_OK.pdf) · [Conector BNB (Iterasoft)](https://www.iterasoft.io/knowledge/article/306) · [Bemorex, jun-2026](https://bemorex.com/blog/pagos-digitales-qr-bolivia-integrar-negocio) · [CUCU QR](https://cucu.bo/qr) · [Xmart QR](https://xmart.cloud/pricing/qr)

**Acciones (P-1):**
1. Elegir el banco del primer negocio piloto y pedir, a través de su ejecutivo, el convenio de API QR, la documentación y el sandbox.
2. Escribir a la Gerencia de Entidades Financieras del BCB (openbcb@bcb.gob.bo, según la guía) para obtener la especificación Swagger y confirmar el mecanismo de notificación.
3. Consulta legal sobre el modelo "solo software" (EXT-01).

## 2. WhatsApp Business Platform

### 2.1 Tarifas

* **Modelo:** cobro por mensaje de plantilla entregado, según categoría (marketing, utilidad, autenticación) y país del destinatario. Gratis: mensajes entrantes, y mensajes dentro de las 72 h posteriores a un clic en un anuncio *Click-to-WhatsApp*. — [Meta, Pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
* **Cambio del 1-oct-2026 (según BSP):** los **mensajes de servicio** (respuestas de texto libre dentro de la ventana de 24 h) pasan a cobrarse con **1.000 gratis por número de teléfono y mes**, sin acumulación, a la misma tarifa que utilidad y autenticación del país. Las plantillas de utilidad dentro de la ventana también pasan a cobrarse. Además, había que registrar **un medio de pago antes del 30-sep-2026**; si no, se corta la entrega de mensajes de servicio. — [360dialog](https://360dialog.com/blog/whatsapp-service-message-charging-october-2026/), [ChatMaxima](https://chatmaxima.com/blog/whatsapp-service-message-pricing-october-2026/), [Telnyx](https://telnyx.com/resources/whatsapp-business-api-cost), [Zendesk](https://support.zendesk.com/hc/en-us/articles/11113277351322-Announcing-upcoming-changes-to-WhatsApp-Business-messaging-pricing)
* ⚠️ **Conflicto de fuentes:** la versión de la página oficial de Meta que se obtuvo para esta revisión no menciona el cobro de mensajes de servicio. **Confirmar en WhatsApp Manager → Facturación** y con la primera factura de octubre.
* **Bolivia** no tiene línea propia y cae en **"Resto de Latinoamérica"**: marketing ≈ US$ 0,0740 y utilidad/autenticación/servicio ≈ US$ 0,0113 por mensaje (tarifa de lista vigente desde 1-oct-2026, según [Zernio](https://zernio.com/whatsapp-api-pricing), que cita a Meta). Confirmar con el CSV oficial de tarifas en USD.

**Impacto en costos (peor caso, si el cobro de servicio se confirma):**

`costo_servicio_mes ≈ max(0, respuestas_mes − 1.000) × 0,0113 USD` por número.

| Perfil del negocio | Respuestas del bot/mes | Costo de servicio/mes |
| :--- | ---: | ---: |
| Pequeño (≈ 30 conversaciones/día × 3 respuestas) | 2.700 | ≈ US$ 19 |
| Mediano (≈ 80 × 4) | 9.600 | ≈ US$ 97 |
| Grande (≈ 200 × 4) | 24.000 | ≈ US$ 260 |

**Decisiones:** (a) **una sola respuesta por turno** (el debounce ya agrupa ráfagas, y el agente no envía varios globos), (b) mensajes interactivos (botones o listas) en lugar de varios textos, (c) métrica `wa_billable_messages{number}` y tope por plan, (d) el precio por plan del SaaS incluye un cupo de respuestas.

### 2.2 Política y onboarding

* **IA:** desde el 15-ene-2026 los términos prohíben a los proveedores de IA cuya tecnología sea la funcionalidad **principal** (chatbots de propósito general). Los bots de atención al cliente de un negocio siguen permitidos. — [TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform)
  → El agente se limita al catálogo, la agenda, los pagos y la atención del negocio, y rechaza conversaciones ajenas (guardrail de alcance en el prompt de sistema y en las evals).
* **Tech Provider:** para que cada negocio conecte su número con *Embedded Signup* hay que crear una app de Meta con el caso de uso WhatsApp, **verificar la empresa**, pasar **App Review** (con videos de demostración) y obtener acceso avanzado a `whatsapp_business_messaging` y `whatsapp_business_management`. Cada cliente debe registrar una **tarjeta de crédito** para la facturación. — [Meta, Become a Tech Provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers)
* **Límites de mensajería:** desde el 7-oct-2025 el límite es por **portafolio** de negocio: los nuevos empiezan en 250 y escalan a 2.000, 10.000, 100.000 o ilimitado. Solo afecta a los mensajes iniciados por el negocio fuera de la ventana. — [Meta, messaging limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/upcoming-messaging-limits-changes/)
* **Coexistencia:** permite usar el **mismo número** en la app WhatsApp Business y en la Cloud API, con historial sincronizado (*echoes*). Útil para que el dueño responda desde su celular sin perder el contexto del bot. Limitaciones: hay que abrir la app al menos cada 13 días, no admite la verificación clásica ni la insignia, y los dispositivos acompañantes no generan *echoes*. — [360dialog, Coexistence](https://docs.360dialog.com/partner/onboarding/whatsapp-coexistence)
  → Se agrega como opción de onboarding: el webhook `smb_message_echoes` cambia la conversación a modo `HUMAN`.

## 3. Impuestos y facturación ⚖️

* **Ley 1733 (27-may-2026), "IVA transparente":** el IVA (13 %) se calcula sobre el precio neto y se muestra separado en la factura. Antes estaba incluido en el precio (carga efectiva ≈ 14,94 % sobre el neto). Rige según su reglamentación: la RND 102600000034 (1-sep-2026) inicia la adecuación técnica, con **pruebas piloto y autorización de sistemas del 1-oct al 16-nov-2026**, y mientras tanto sigue vigente la RND 102100000011. — [Ley 1733 (SIN)](https://www.impuestos.gob.bo/wp-content/uploads/2026/05/L17332051NCPP.pdf), [Bolivia Impuestos, IVA por fuera](https://boliviaimpuestos.com/iva-por-fuera-ley-1733/), [RND 102600000034](https://boliviaimpuestos.com/rnd-102600000034-ajustes-facturacion-iva-transparente/)
  → **ADR-0012:** la tabla `tax_regimes` tiene un régimen vigente por fecha, y cada orden congela el suyo con subtotal, IVA y total separados. Probado en `04_tax_regimes.test.sql`.
* **RND 102600000038 (30-sep-2026):** deroga los cronogramas por grupos; el SIN asignará periódicamente una modalidad en línea a los contribuyentes de IVA que aún no la tengan. — [LookupTax](https://lookuptax.com/tax-changes/bolivia/rnd-102600000038-online-invoicing-deadlines-abrogated)
* **RND 102600000028:** plazo hasta el **30-oct-2026** para vincular productos y servicios con los códigos de actividad del RNC. → El catálogo debe guardar el **código de producto SIN** por ítem (`catalog_items.attributes.sin_product_code` hasta que se modele la facturación). — [LookupTax](https://lookuptax.com/tax-changes/bolivia/einvoicing-homologation-extension-2026)
* **Proveedores de facturación (RND 102100000011):** deben estar autorizados por el SIN (autorización por 3 años, pruebas piloto por cliente) y el contribuyente sigue siendo responsable de sus impuestos. Las facturas en línea se emiten el mismo día; en contingencia, el CUFD vale hasta 72 h. — [RND 102100000011](https://www.impuestos.gob.bo/wp-content/uploads/2025/10/RND11.pdf)
  → Confirma ADR-0009: usar un proveedor ya autorizado, no homologar uno propio.

## 4. Tipo de cambio y pagos al exterior

* **Régimen flexible desde el 26-jun-2026** (R.M. 245/2026 y R.D. BCB 88/2026): el BCB calcula el TCO cada día hábil como promedio ponderado de las compras de dólares de las entidades a sus clientes, y rige al día siguiente. El precio de venta de referencia de las entidades puede estar como máximo 10 centavos por encima del TCO. TCO del 15-oct-2026: **11,68 Bs/USD**. — [Ferrere](https://www.ferrere.com/es/novedades/bolivia-adopta-un-regimen-cambiario-flexible-principales-cambios-e-implicancias-para-las-empresas/), [Dentons](https://www.dentons.com/en/insights/alerts/2026/july/1/bolivia-adopts-a-flexible-exchange-rate-regime), [BCB](https://www.bcb.gob.bo/librerias/indicadores/otras/ultimo.php)
  → El job diario registra el TCO como `BCB_OFICIAL`. La diferencia de cambio entre la cotización y el cobro queda trazada por `applied_fx_rate` en cada orden.
* **Tarjetas en el exterior (desde abr-2026):** débito con tope de US$ 500 para compras en el exterior, en línea y servicios digitales; crédito según la capacidad de pago y el banco. — [Red Uno](https://www.reduno.com.bo/economia/cuanto-puedes-gastar-estos-son-los-nuevos-limites-tras-la-liberacion-de-tarjetas-202646124921), [Infobae](https://www.infobae.com/america/america-latina/2026/04/07/bolivia-habilita-las-compras-con-tarjetas-bancarias-en-el-exterior-luego-de-tres-anos-de-restricciones/)
  → Supabase, Meta, el LLM y GitHub cobran en USD: usar una tarjeta de crédito corporativa y registrar el costo en Bs con el TC del día.

## 5. Plataformas de desarrollo

* **GitHub (repo privado, plan Free):** las ramas protegidas en repos privados requieren GitHub Pro, Team o Enterprise. — [GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches). El escaneo de código con CodeQL y la protección de secretos en repos privados son productos de pago (GitHub Code Security / Secret Protection). Issues y Projects están incluidos.
  → Usar el **GitHub Student Developer Pack** (incluye GitHub Pro) si alguien del equipo es estudiante; si no, gitleaks + bandit + semgrep en CI y la regla de equipo "nunca push directo a `main`".
* **Proveedor de LLM:** las tarifas, los límites de tasa y las fechas de retiro de modelos cambian.
  → `LLMPort` con modelo fijado por identificador exacto, evals obligatorias antes de cambiar de modelo, costo medido en `private.llm_calls` y alerta por tenant.
* **Supabase:** el plan gratuito pausa los proyectos tras 7 días de inactividad. Pro cuesta US$ 25/mes; PITR es un add-on de ≈ US$ 100/mes. — [Makerkit](https://makerkit.dev/blog/saas/supabase-pricing)

## 6. Datos personales e IA ⚖️

* Bolivia no tiene una ley general de protección de datos personales (sep-2026); existe la acción constitucional de protección de privacidad y hay anteproyectos en curso. — [Opinión](https://www.opinion.com.bo/tendencias/quien-queda-tus-datos-bolivia-todavia-tiene-ley-que-obligue-responder/20260916111314996213.html)
* El PL 178/2024-2025 sobre IA (aprobado por el Senado el 22-oct-2025, en Diputados) prevé aviso de interacción automatizada, explicación de decisiones y revisión humana. — [Bemorex](https://bemorex.com/blog/ley-inteligencia-artificial-bolivia-empresas/)
