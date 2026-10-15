# ADR-0001: Monolito modular hexagonal con dos procesos (API y Worker)

* **Estado:** Aceptado (refina ADR-001 de la v1)
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §4.2–4.3, ADR-0005, ADR-0010

## Contexto
El equipo es pequeño y el volumen inicial es bajo (≈ 1,5 msg/s en ráfaga, §9.2). Los microservicios multiplican el despliegue, la observabilidad y los modos de falla sin aportar beneficio a esta escala. Pero el webhook debe responder en ≤ 250 ms y el trabajo de IA dura segundos, así que **no pueden compartir el mismo ciclo de vida de petición**.

## Decisión
Una sola base de código Python (FastAPI) organizada como arquitectura hexagonal (dominio → aplicación → puertos/adaptadores), desplegada como **dos procesos** de la misma imagen:
* `api`: webhook de Meta, callbacks bancarios, endpoints del dashboard. No hace trabajo pesado.
* `worker`: agente de IA, OCR, PDFs, envíos y jobs programados. Consume la cola durable (ADR-0005).

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Microservicios desde el día 1 | Escalado independiente | Costo operativo y de red; sobre-ingeniería para el volumen actual |
| Un solo proceso con `BackgroundTasks` (v1) | Simplicidad | Pérdida de trabajo al reiniciar; CPU limitada después de la respuesta en plataformas *serverless*; sin reintentos |
| Funciones *serverless* por evento | Escala a cero | *Cold starts* incompatibles con 250 ms; límites de duración |

## Consecuencias
* (+) Un solo repositorio, un solo modelo de dominio, despliegue simple.
* (+) El worker escala horizontalmente sin tocar la API.
* (−) Hay que mantener los límites de módulos con disciplina: `import-linter` en CI impide que `domain/` importe infraestructura.
* Extraer un servicio (por ejemplo, OCR) en el futuro implica implementar su puerto como cliente remoto, sin cambiar los casos de uso.

## Verificación
`import-linter` en CI; métrica `webhook_ack_ms` p99 ≤ 250 ms; prueba de caos (matar el worker no afecta al webhook).
