# ADR-0010: Despliegue co-ubicado con la base de datos y worker always-on

* **Estado:** Propuesto
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §9, §11 · ADR-0001, ADR-0005

## Contexto
* Cada consulta entre cómputo y BD en continentes distintos suma ≈ 100–150 ms; un turno del agente hace ≈ 20 consultas.
* En Cloud Run con facturación por petición, la CPU se limita fuera de una petición (rompe el trabajo en segundo plano), y el *cold start* de Python compromete el ack ≤ 250 ms.
* El plan gratuito de Supabase pausa los proyectos inactivos: no sirve para producción.

## Decisión
* Supabase en plan pago, en la región de **São Paulo** (la más cercana a Bolivia entre las disponibles; confirmar al crear el proyecto).
* `api` y `worker` en la **misma región**, con instancia mínima ≥ 1 para ambos. El `worker` corre con CPU siempre asignada (por ejemplo, Cloud Run con facturación por instancia, o un contenedor siempre encendido en Railway/Fly/Render).
* Dashboard en Vercel. *Staging* en un proyecto Supabase separado.

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| Cloud Run con escala a cero | Costo mínimo | *Cold start* y CPU limitada; incompatible con los SLO |
| Región en EE. UU. | Más opciones y precios | Latencia acumulada por turno |

## Consecuencias
* (+) Cumple Q-PERF-1/2 con margen.
* (−) Costo fijo mínimo (≈ US$ 35–60/mes en total, §11).

## Verificación
Medición de latencia BD↔cómputo (p50 < 5 ms) en el despliegue de *staging*; `webhook_ack_ms` p99.
