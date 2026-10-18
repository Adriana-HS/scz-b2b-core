# ADR-0019: Gemini (plan gratuito) solo para pruebas de desarrollo del agente

* **Estado:** Aceptado (AHS, 17-oct-2026). Amplía el ADR-0018 sin reemplazarlo: Anthropic sigue siendo el proveedor de producción.
* **Fecha:** 2026-10-17
* **Decisores:** AHS
* **Relacionado:** ADR-0018 · SYSTEM_DESIGN.md §7.1 y §8.7 · #22 (puertos de IA), #81 (orquestador), #83 (este adaptador), #28 (evals)

## Contexto

* **Qué pidió AHS.** "Metele para la API de Gemini [...] la puedo usar gratis para hacer las pruebas [...] con Claude tengo que pagar [...] a Claude déjalo por ahí." Es decir: probar el agente mientras se desarrolla sin gastar, y mantener Anthropic para producción.
* **La suscripción Gemini Pro no incluye la API.** Es un plan de la app de consumo. La API se usa con una clave de [Google AI Studio][g-studio], que tiene un nivel gratuito con límites de uso por minuto y por día ([límites][g-limites]).
* **Privacidad del nivel gratuito** ([términos][g-terminos], consultados el 17-oct-2026):
  * Google usa lo enviado y las respuestas "para proveer, mejorar y desarrollar" sus productos;
  * personas pueden leer, anotar y procesar las entradas y salidas;
  * los términos piden no mandar información sensible, confidencial ni personal al nivel gratuito.
  En el nivel pago, Google no usa los prompts para mejorar sus productos.
* **Requisito técnico.** Con los modelos Gemini 3, cada pedido de herramienta trae una firma de razonamiento (`thoughtSignature`) que hay que devolver en la ronda siguiente del mismo turno; si falta, la API responde 400 ([firmas][g-firmas]).

## Decisión

1. **Un segundo adaptador del puerto `ChatModel`:** `GeminiChatModel` (`backend/app/infrastructure/ai/gemini.py`), elegido con `LLM_PROVIDER=gemini`. El valor por defecto sigue siendo `anthropic`.
2. **Solo en desarrollo:** con `LLM_PROVIDER=gemini` el proceso **no arranca** si `ENVIRONMENT` es `staging` o `production`. Solo se usa con datos sintéticos (los seeds de #27), nunca con conversaciones de clientes reales ni con datos personales propios.
3. **El costo se sigue registrando:** `LLM_PRICE_*` lleva los precios del nivel **pago** del modelo elegido. El nivel gratuito no cobra, pero `private.llm_calls` muestra lo que costaría, para comparar con Anthropic.

## Alternativas consideradas

| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Probar solo con dobles (lo que había) | Sin cuenta ni datos a terceros | No mide cómo responde un modelo de verdad |
| Probar con Anthropic desde ya | El mismo proveedor que producción | Cuesta (centavos por conversación, pero AHS no quiere el gasto todavía) |
| **Gemini gratuito solo en desarrollo, con bloqueo en staging y producción** | Sin costo; prueba el agente con un modelo real; el bloqueo lo impone el código | Google usa y puede leer lo enviado; los resultados de calidad no valen para Claude; límites de uso bajos |
| Gemini como proveedor de producción | Ya hay adaptador | Contradice el ADR-0018; necesitaría el nivel pago y un ADR nuevo |

## Consecuencias

* **(+)** AHS puede probar el bot sin pagar. La suite de contrato de #22 corre contra los dos adaptadores: el orquestador no cambia.
* **(+)** El dominio aprende a llevar un dato opaco del proveedor (`ToolCall.signature`) sin saber qué es.
* **(−)** Calidad: lo que funcione con Gemini no prueba que funcione con Claude Haiku 5.5. Las evals que deciden la fase 2 (#28) se corren con el modelo de producción.
* **(−)** Un adaptador más que mantener; Google cambia su API seguido (modelos en *preview* que se apagan).
* **(−)** Los límites del nivel gratuito pueden cortar una prueba con 429. El adaptador lo trata como transitorio y respeta la espera que pide Google.

## Verificación

* `test_config.py`: `LLM_PROVIDER=gemini` falla en `staging` y `production`.
* `test_gemini_chat.py` y la suite de contrato con Gemini: formato, firmas devueltas, errores y la clave nunca en la URL ni en un error.
* Revisión: ningún archivo de despliegue define `LLM_PROVIDER=gemini`.

[g-studio]: https://aistudio.google.com/
[g-limites]: https://ai.google.dev/gemini-api/docs/rate-limits
[g-terminos]: https://ai.google.dev/gemini-api/terms
[g-firmas]: https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
