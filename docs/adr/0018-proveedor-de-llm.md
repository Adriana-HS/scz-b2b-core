# ADR-0018: Anthropic como único proveedor de IA del agente

* **Estado:** Aceptado (AHS, 17-oct-2026), con el modelo inicial Claude Haiku 5.5 confirmado por AHS el mismo día.
* **Fecha:** 2026-10-17
* **Decisores:** AHS
* **Relacionado:** SYSTEM_DESIGN.md §7 (agente), §8.7 (privacidad), §11 (costos) · Q-ACC-5, Q-COST-1 · #22 (puertos de IA, PR #77), #24 (herramientas), #25 (guardrails y cuotas), #28 (evals *golden*) · ADR-0003 (embeddings)

> **Conflicto de interés declarado:** este documento lo preparó un asistente de IA de Anthropic (Claude). Por eso los datos salen de las páginas oficiales de cada proveedor, con enlace y fecha de consulta, y la propuesta original fue medir con evaluaciones propias antes de elegir. AHS decidió sin esa comparación, para no sumar costo ni cuentas (ver **Decisión**).

## Contexto

* **Por qué hay que decidir.** El agente (#81, con las herramientas de #24 y los guardrails de #25) necesita un modelo de chat que use herramientas (buscar en el catálogo, cotizar, pasar a una persona) en español de Bolivia. El PR #77 trae los puertos de IA, que no dependen del proveedor, y **un** adaptador, el de Anthropic. Esa elección salió de una etiqueta en un diagrama de `SYSTEM_DESIGN.md` (`ai/anthropic_adapter.py`), no de un acuerdo. Es una decisión de presupuesto, de privacidad (un tercero procesa los mensajes de los clientes) y de proveedor.
* **Cuánto cuesta cambiar después.** Poco en código: un adaptador nuevo es un archivo más sus pruebas, y la suite de contrato de #22 verifica que se comporte igual. Los decoradores (reintento, circuito, costo y trazas) no cambian. Cambiar de proveedor en producción también implica una cuenta nueva, una clave, un medio de pago y actualizar el registro de proveedores (§8.7).

### Precios oficiales (consultados el 17-oct-2026, USD por millón de tokens)

| Proveedor · modelo | Entrada | Escritura en caché | Lectura de caché | Salida | Fuente |
| :--- | ---: | ---: | ---: | ---: | :--- |
| Anthropic · Claude Haiku 5.5 (prompts ≤ 100k) | 0,10 | 0,125 | 0,01 | 0,50 | [Anthropic][a-precios] |
| Anthropic · Claude Sonnet 5.5 | 2 | 2,50 | 0,10 | 10 | [Anthropic][a-precios] |
| Anthropic · Claude Opus 5.5 | 4 | 5 | 0,20 | 20 | [Anthropic][a-precios] |
| OpenAI · gpt-6-luna | 0,10 | 0,125 | 0,01 | 0,50 | [OpenAI][o-precios] |
| OpenAI · gpt-5.4-mini | 0,75 | — | 0,075 | 4,50 | [OpenAI][o-precios] |
| OpenAI · gpt-6.1-sol | 2 | 2,50 | 0,10 | 10 | [OpenAI][o-precios] |
| Google · Gemini 3.1 Flash-Lite | 0,25 | — | 0,025 (+ almacenamiento) | 1,50 | [Google][g-precios] |
| Google · Gemini 3.8 Flash (hasta 31-dic-2026) | 0,75 | — | 0,075 (+ almacenamiento) | 3,75 | [Google][g-precios] |
| Google · Gemini 3.8 Flash (**desde 1-ene-2027**) | 1,50 | — | 0,15 (+ almacenamiento) | 7,50 | [Google][g-precios] |
| Google · Gemini 3.1 Pro **Preview** | 2 | — | 0,20 (+ almacenamiento) | 12 | [Google][g-precios] |

* **Lectura de las páginas.** Las extrajo un modelo pequeño, que puede equivocarse con algún número. Antes de aceptar este ADR hay que confirmarlas a mano; cambian seguido. Un "—" en escritura de caché significa que la página no publica ese precio: en el cálculo se cobra como entrada, el supuesto más caro.
* **Precios por región.** En Anthropic y OpenAI, fijar el procesamiento a una región (EE. UU.) cuesta un 10 % más. Ninguno de los tres ofrece residencia en Latinoamérica.

### Costo por conversación

**Conversación tipo:** una cotización en una importadora.
* **Turnos:** 6 mensajes del cliente, así que 6 turnos del agente.
* **Llamadas:** 1,5 por turno (el tope del diseño es 2 rondas, §7), es decir **9 llamadas**.
* **Tokens:**
  * prompt de sistema con la configuración del negocio y las herramientas: ~2.500 tokens, cacheados. Se escriben una vez y se leen en las otras 8 llamadas;
  * resumen, últimos mensajes y resultados de herramientas: ~1.500 tokens por llamada, sin caché;
  * respuesta: ~200 tokens.
* **Negocio mediano:** ~9.600 respuestas al mes (§11), unas **1.600 conversaciones**.

| Modelo | USD por conversación | USD al mes, negocio mediano |
| :--- | ---: | ---: |
| Claude Haiku 5.5 | 0,0028 | ≈ 4,40 |
| gpt-6-luna | 0,0028 | ≈ 4,40 |
| Gemini 3.1 Flash-Lite | 0,0072 | ≈ 11,50 |
| Gemini 3.8 Flash | 0,020 (0,040 desde ene-2027) | ≈ 32 (≈ 65) |
| gpt-5.4-mini | 0,022 | ≈ 35 |
| Claude Sonnet 5.5 · gpt-6.1-sol | 0,053 | ≈ 85 |
| Gemini 3.1 Pro Preview | 0,058 | ≈ 92 |
| Claude Opus 5.5 | 0,107 | ≈ 170 |

**Escala.** WhatsApp, si cobra los mensajes de servicio (≈ US$ 0,0113 cada uno, P-3 de §11), cuesta ≈ US$ 0,068 por conversación de 6 respuestas. Con un modelo chico, la IA es del orden del 5 % de lo que cuesta WhatsApp. Con uno mediano, se le acerca.

**Supuestos.** Una conversación más larga, con más herramientas o con un catálogo grande en el contexto cuesta más. El costo real se mide en `private.llm_calls` (#22) y lo acotan las cuotas de #25.

### Privacidad, disponibilidad y pago

| | Anthropic | OpenAI | Google (Gemini API) |
| :--- | :--- | :--- | :--- |
| ¿Disponible en Bolivia? | Sí ([lista oficial][a-paises]) | Sí ([lista oficial][o-paises]) | Sí ([lista oficial][g-paises]) |
| ¿Entrena con los datos de la API? | No, según sus términos comerciales (ver [privacidad][a-retencion]) | No por defecto ([OpenAI][o-datos]) | No **en el nivel pago**. El nivel gratuito **sí** los usa: no sirve para datos de clientes ([Google][g-precios], [términos][g-terminos]) |
| Retención de mensajes | Se borran a los 30 días; hasta 2 años si hay una violación de la política de uso ([Anthropic][a-retencion]) | Hasta 30 días, para control de abuso ([OpenAI][o-datos]) | "Por un período limitado", sin plazo publicado ([términos][g-terminos]) |
| Retención cero (ZDR) | Por acuerdo con la empresa | Con aprobación previa | No figura en los términos consultados |
| Dónde se procesa | Global por defecto; EE. UU. con +10 % ([residencia][a-residencia]) | EE. UU., Europa y EAU; no hay región en Latinoamérica ([OpenAI][o-datos]) | Cualquier país donde Google tenga instalaciones ([términos][g-terminos]) |
| Pago | USD con tarjeta | USD con tarjeta | USD por una cuenta de facturación de Google Cloud |

* **Pago desde Bolivia.** Las tres cobran en USD. Con una tarjeta boliviana, el débito tiene un tope de US$ 500 para el exterior, así que hay que usar **crédito** (EXT-11). Si cada una acepta la tarjeta de un banco boliviano no se sabe hasta crear la cuenta y cargarla.
* **Calidad en español.** No hay un dato público confiable para español de Bolivia con herramientas. Las tres familias manejan español, pero lo que importa aquí es lo que se mide: Q-ACC-5 ≥ 90 % y **0 violaciones de grounding**. Eso solo se sabe con nuestras evaluaciones (#28).

## Decisión

**Decidido por AHS el 17-oct-2026:**

1. **Proveedor: Anthropic**, como único proveedor de chat del agente. Su adaptador ya está en `main` (PR #77). **No** se escriben adaptadores de OpenAI ni de Google y **no** se hace la evaluación comparativa entre proveedores: costaba 2 cuentas más y trabajo extra.
2. **Modelo inicial: Claude Haiku 5.5** (`claude-haiku-5-5`), confirmado por AHS: el más barato, ≈ US$ 0,003 por conversación, ≈ US$ 4,40 al mes por negocio mediano. Subir a Claude Sonnet 5.5 (≈ US$ 85 al mes) es cambiar `LLM_MODEL` y los precios; no toca código.
3. **Calidad.** Las evaluaciones *golden* del roadmap (#28) siguen siendo el criterio de salida de la fase 2 (Q-ACC-5 ≥ 90 %, 0 violaciones de grounding), solo con modelos de Anthropic. Su costo es de centavos: 30 conversaciones con Haiku ≈ US$ 0,09; con Sonnet ≈ US$ 1,60. Si Haiku no llega, se decide con AHS si se sube a Sonnet (decisión de AHS: "si las pruebas muestran que no alcanza, lo hablamos").
4. **Cuenta.** Una sola: la consola de Anthropic, con tarjeta de **crédito** en USD (EXT-11). La clave va en el gestor de secretos del despliegue.

## Alternativas consideradas

| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Anthropic sin evaluar (lo que hacía el PR #77) | Ya está el adaptador | Decisión de presupuesto y privacidad tomada sin acuerdo; conflicto de interés del asistente |
| El más barato sin evaluar (Haiku 5.5 o gpt-6-luna) | ≈ US$ 4/mes por negocio | Un modelo chico puede fallar más al cotizar, y un error de precio es grave (Q-ACC) |
| El mediano de una marca "por las dudas" | Calidad alta probable | ≈ 20 veces el costo del chico, sin dato que lo justifique |
| Evaluar una lista corta de 3 proveedores (la propuesta original) | Decisión medible; deja el respaldo listo | 2 adaptadores más (OpenAI y Google) y 3 cuentas. **Descartado por AHS (17-oct-2026)** por el costo y las cuentas extra |
| **Anthropic, con un solo modelo y evaluaciones propias (#28)** | Una cuenta; adaptador ya hecho; costo de evaluación de centavos | Sin comparación con otros proveedores; dependencia de un proveedor (mitigada: los puertos permiten agregar otro adaptador) |
| Modelo abierto en servidor propio | Datos bajo control propio | GPU always-on de cientos de USD al mes; operación compleja; fuera del alcance del MVP |
| Modelo abierto en una computadora local (sin GPU) | Sin costo de API | Sin GPU, leer el contexto de un turno toma de decenas de segundos a minutos (el turno debe durar menos de 20 s, §7); solo entran modelos chicos, que fallan más con herramientas; sin respaldo ante cortes de luz o internet; mensajes de clientes en un equipo doméstico. **Descartado por AHS (17-oct-2026)** |
| DeepSeek (API oficial) | Barato: ~US$ 6–11 al mes por negocio mediano ([precios][d-precios]); tarifa baja en horario comercial de Bolivia | Su política dice que procesa y guarda los datos en China ([política][d-privacidad]) y no aclara el uso de los datos de la API para entrenar. **Descartado por AHS (17-oct-2026)** |
| Modelo chino abierto (DeepSeek, Qwen) alojado fuera de China | Precio bajo sin mandar datos a China | Precios y condiciones de los anfitriones sin verificar en fuentes oficiales. **No se evalúa por ahora (AHS, 17-oct-2026)**; se puede reabrir con un ADR nuevo |

## Consecuencias

* **(+)** Una sola cuenta y un solo adaptador. Sin trabajo extra: el adaptador ya está en `main`.
* **(+)** Si algún día conviene otro proveedor, se agrega un adaptador. La suite de contrato y los decoradores de #22 no cambian.
* **(−)** No hay comparación con otros proveedores: no sabremos si otro cotizaba mejor o más barato.
* **(−)** Dependencia de un proveedor. Si Anthropic cae, el circuit breaker degrada a plantilla y pasa la conversación a una persona (§10.3); no hay un segundo proveedor de respaldo.
* **(−)** Los precios cambian seguido. Revisar este ADR cada semestre, igual que EXT-04.

## Verificación

* `LLM_MODEL=claude-haiku-5-5`, el identificador exacto (#22).
* Reporte de las evaluaciones de #28 con exactitud, violaciones de grounding, latencia y costo real, enlazado desde aquí.
* La alerta de costo por tenant (#25) vigila el gasto real contra el tope de cada plan.

[a-precios]: https://platform.claude.com/docs/en/about-claude/pricing
[a-residencia]: https://platform.claude.com/docs/en/manage-claude/data-residency
[a-retencion]: https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data
[a-paises]: https://www.anthropic.com/supported-countries
[o-precios]: https://developers.openai.com/api/docs/pricing
[o-datos]: https://developers.openai.com/api/docs/guides/your-data
[o-paises]: https://developers.openai.com/api/docs/supported-countries
[g-precios]: https://ai.google.dev/gemini-api/docs/pricing
[g-terminos]: https://ai.google.dev/gemini-api/terms
[g-paises]: https://ai.google.dev/gemini-api/docs/available-regions
[d-precios]: https://api-docs.deepseek.com/quick_start/pricing
[d-privacidad]: https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html
