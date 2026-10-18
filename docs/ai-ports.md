# Puertos de IA

Cómo el backend habla con los modelos de IA sin depender de un proveedor (#22, `docs/architecture/principios-y-patrones.md` §2.1 y §3). El agente (#81, con las herramientas de #24 y los guardrails de #25) usa estos puertos; nunca un SDK.

## Puertos

| Puerto | Qué hace | Implementación hoy |
| :--- | :--- | :--- |
| `ChatModel.run_turn(system, history, tools, timeout_s)` | Un turno del modelo: texto y/o herramientas pedidas | `AnthropicChatModel` (Messages API, httpx) y `ScriptedChatModel` (fake) |
| `VisionExtractor.extract_receipt(image, timeout_s)` | Lee los campos de un comprobante QR. Es **evidencia**, nunca confirma un pago (ADR-0008) | Fake. El adaptador llega con el dataset OCR (#31, #33) |
| `Embedder.embed(texts)` | Un vector de 1536 dimensiones por texto (`catalog_items.embedding`, ADR-0003) | Fake. El modelo se elige midiendo 2 candidatos (pregunta P-4 del ADR-0003) en #23 |
| `SpeechToText.transcribe(audio, mime)` | Notas de voz a texto | Fake. El adaptador llega con #30 |

Todos lanzan errores del dominio con un **código estable**:
* `TransientAiError`: red, timeout, 429, 5xx, sobrecarga. Puede traer `retry_after_s`.
* `PermanentAiError`: request inválido, clave rechazada, modelo inexistente.
* `AiUnavailableError`: el circuito está abierto.

El mensaje del error nunca lleva la clave, el prompt ni la respuesta, porque ahí van mensajes de clientes.

La **suite de contrato** (`tests/contract/test_ai_ports_contract.py`) corre las mismas pruebas contra el fake y contra cada adaptador real (con un doble de HTTP). Si un fake se aparta del contrato, las pruebas que lo usan mienten. Cada adaptador nuevo se agrega a la lista de la suite.

## Decoradores y su orden

`resilient_chat_model(...)` (`app/application/ai.py`) arma el `ChatModel` que usa el agente, de afuera hacia adentro:

```
TracedChatModel → CostMeteredChatModel → CircuitBreakerChatModel → RetryingChatModel → adaptador
```

| Decorador | Qué hace | Por qué en ese lugar |
| :--- | :--- | :--- |
| `RetryingChatModel` | Reintenta solo los transitorios: backoff exponencial con *full jitter* (0,5 s → 4 s), respeta el `retry-after`, 3 intentos dentro de un **presupuesto de 20 s** | Adentro: para el circuito y para el costo, una llamada reintentada cuenta como una |
| `CircuitBreakerChatModel` | Con 5 fallas transitorias seguidas se abre: durante 30 s no llama al proveedor y lanza `AiUnavailableError` al instante. Después deja pasar **una** llamada de prueba. Los errores permanentes no lo abren (son un bug nuestro, no una caída) | Afuera del reintento: con el proveedor caído se corta sin esperar los backoffs, y el agente degrada a plantilla + modo HUMAN (§10.3). Hay un breaker por proveedor y por proceso |
| `CostMeteredChatModel` | Cada llamada lógica deja su fila en `private.llm_calls` (tenant, conversación, modelo, tokens, costo, latencia, resultado, `trace_id`) y suma a la métrica `llm_cost_usd`. Las fallidas quedan con costo 0 y su código. Si la BD no puede registrar, el turno sigue y queda un log de error | Se construye por turno, con el tenant y la conversación de ese turno |
| `TracedChatModel` | Span CLIENT `chat <modelo>` con las convenciones `gen_ai` de OpenTelemetry: proveedor, modelo, tokens y motivo de corte. **Sin contenido** y sin excepciones grabadas | Afuera de todo: mide la llamada completa, con reintentos |

## Modelo y costo

* **Modelo fijado.** `LLM_MODEL` es el identificador exacto (p. ej. `claude-sonnet-5-5`). Un alias como `-latest` se rechaza al arrancar, porque cambia de modelo sin aviso y con él cambian el costo y las respuestas.
* **Precios.** `LLM_PRICE_{INPUT,OUTPUT,CACHE_READ,CACHE_WRITE}_USD_PER_MTOK`, en USD por millón de tokens, se copian de la página de precios del proveedor. Con `LLM_MODEL` y sin los cuatro precios, el proceso **no arranca**: el costo se registraría en cero (Q-COST-1, falla cerrado).
* **Cálculo.** El costo se calcula con `Decimal` y se redondea a 6 decimales, como `private.llm_calls.cost_usd numeric(12,6)`. Es costo operativo, no dinero del cliente: por eso no pasa por las funciones de precios de la BD.
* **Caché del sistema.** El prompt de sistema se marca para caché (`cache_control: ephemeral`): en turnos con el mismo sistema, esa parte se cobra mucho menos (§11).
* **Integridad.** La **migración 19** agrega a `private.llm_calls` la FK compuesta con `conversations` (una llamada nunca apunta a la conversación de otro negocio) y `CHECK` de tokens, costo y latencia no negativos. Pruebas: `16_llm_calls.test.sql`.

## Qué falta y dónde llega

* **Cuotas por cliente y por tenant** ("denial of wallet", §8.3), con `private.llm_calls`: #25.
* **Alerta de costo por tenant** > 80 % del tope: #25, junto con el tope por plan.
* **Adaptadores de embeddings, voz y visión:** #23, #30 y #31.
