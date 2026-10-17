# ADR-0018: Elegir el proveedor y el modelo de IA del agente midiendo, no por marca

* **Estado:** Propuesto. **Pendiente de la decisión de AHS**: no fusionar el PR #77 hasta decidir.
* **Fecha:** 2026-10-17
* **Decisores:** AHS
* **Relacionado:** SYSTEM_DESIGN.md §7 (agente), §8.7 (privacidad), §11 (costos) · Q-ACC-5, Q-COST-1 · #22 (puertos de IA, PR #77), #25 (cuotas), #26 (agente), #28 (evals *golden*) · ADR-0003 (embeddings)

> **Conflicto de interés declarado:** este documento lo preparó un asistente de IA de Anthropic (Claude). Por eso los datos salen de las páginas oficiales de cada proveedor, con enlace y fecha de consulta, y la recomendación es medir con evaluaciones propias, no elegir por marca.

## Contexto

* **Por qué hay que decidir.** El agente (#26) necesita un modelo de chat que use herramientas (buscar en el catálogo, cotizar, pasar a una persona) en español de Bolivia. El PR #77 trae los puertos de IA, que no dependen del proveedor, y **un** adaptador, el de Anthropic. Esa elección salió de una etiqueta en un diagrama de `SYSTEM_DESIGN.md` (`ai/anthropic_adapter.py`), no de un acuerdo. Es una decisión de presupuesto, de privacidad (un tercero procesa los mensajes de los clientes) y de proveedor.
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

## Decisión (propuesta, a confirmar por AHS)

1. **No elegir proveedor por marca.** Correr las evaluaciones *golden* de #28 (≥ 30 conversaciones del rubro importadora) contra una lista corta y elegir con datos: exactitud (Q-ACC-5), violaciones de grounding, errores al pedir herramientas, latencia p95 y costo real medido en `llm_calls`.
2. **Lista corta**, un modelo chico y uno mediano por proveedor:
   * chicos: Claude Haiku 5.5, gpt-6-luna y Gemini 3.1 Flash-Lite;
   * medianos, como respaldo si los chicos no llegan al 90 %: Claude Sonnet 5.5, gpt-6.1-sol y Gemini 3.8 Flash.
   * Se excluyen los modelos *preview* (pueden cambiar o retirarse) y los grandes (Opus, gpt-6-astra), que cuestan de 2 a 4 veces más sin una necesidad demostrada.
3. **Gana el más barato** que cumpla Q-ACC-5 ≥ 90 % y 0 violaciones de grounding. A igualdad, pesan la privacidad (plazo de retención) y que el pago con tarjeta boliviana funcione.
4. **PR #77.** Se puede fusionar antes de decidir, si AHS quiere: los puertos, los decoradores y la suite de contrato no dependen del proveedor. El adaptador de Anthropic queda como uno de los tres para las evaluaciones. Si AHS prefiere no tener ningún adaptador hasta decidir, se saca de ese PR.

## Alternativas consideradas

| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Anthropic sin evaluar (lo que hacía el PR #77) | Ya está el adaptador | Decisión de presupuesto y privacidad tomada sin acuerdo; conflicto de interés del asistente |
| El más barato sin evaluar (Haiku 5.5 o gpt-6-luna) | ≈ US$ 4/mes por negocio | Un modelo chico puede fallar más al cotizar, y un error de precio es grave (Q-ACC) |
| El mediano de una marca "por las dudas" | Calidad alta probable | ≈ 20 veces el costo del chico, sin dato que lo justifique |
| **Evaluar una lista corta (propuesta)** | Decisión medible; deja el respaldo listo | Hay que escribir 2 adaptadores más (OpenAI y Google) y abrir 3 cuentas |
| Modelo abierto en servidor propio | Datos bajo control propio | GPU always-on de cientos de USD al mes; operación compleja; fuera del alcance del MVP |

## Consecuencias

* **(+)** La elección queda medida y escrita. Cambiar después es agregar un adaptador.
* **(−) Trabajo:** dos adaptadores más (OpenAI y Google), cada uno con su doble de HTTP y la misma suite de contrato.
* **(−) Cuentas y costo:** abrir 3 cuentas (una por proveedor), cada una con su tarjeta. Las evaluaciones cuestan poco: 30 conversaciones × 6 modelos × ≤ US$ 0,06 ≈ **menos de US$ 15** en total.
* **(−) Precios inestables:** cambian seguido (Gemini 3.8 Flash duplica su precio el 1-ene-2027). Hay que revisar este ADR cada semestre, igual que EXT-04.

## Verificación

* Un reporte de las evaluaciones de #28 por modelo, con exactitud, violaciones, latencia y costo, enlazado desde este ADR cuando se acepte.
* Tras elegir, `LLM_MODEL` fija el identificador exacto (#22) y la alerta de costo por tenant (#26) vigila el gasto real.

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
