# ADR-0003: Búsqueda híbrida en PostgreSQL con Reciprocal Rank Fusion

* **Estado:** Aceptado (corrige la implementación de ADR-003 de la v1)
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §7.2 · migraciones 0002 (índices) y 0006 (`search_catalog`) · pruebas `03_*` #10–#15

## Contexto
Los clientes buscan por código exacto escrito de cualquier forma (`oem 4451`, `OEM-4451`), por descripción con errores (`pastiya freno hilux`) y por intención semántica. La v1 declaraba una búsqueda híbrida, pero al ejecutarla se comprobó que:
* nunca usaba la columna `search_vector` (FTS);
* los ítems sin embedding salían **primero** (`ORDER BY … DESC` pone los NULL al inicio);
* el texto del usuario se usaba como patrón `ILIKE`, así que `%` coincidía con todo el catálogo;
* mezclaba puntajes no comparables (1,0 fijo contra la similitud coseno).

## Decisión
Una RPC `search_catalog` (SECURITY INVOKER; el tenant sale de `app.current_org_id`, no de un parámetro) que ejecuta **cuatro recuperadores** sobre el catálogo del tenant y los fusiona con **RRF (k = 60)**:
1. FTS `spanish` sobre `unaccent(sku, name, category, description)` con pesos A/A/B/C (`websearch_to_tsquery`, sin comodines).
2. SKU normalizado (`[A-Z0-9]`): coincidencia exacta (bonus que la ubica primero) o trigram ≥ 0,45.
3. Nombre difuso con `word_similarity` ≥ 0,35 (errores de tipeo).
4. Vector (coseno, HNSW parcial `WHERE embedding IS NOT NULL`) cuando hay embedding de la consulta.

El precio se calcula con la misma función que la cotización (ADR-0007). Los embeddings se regeneran por job cuando cambian el nombre, la descripción o la categoría.

**Modelo de embeddings:** se asume dimensión 1536 (por ejemplo, `text-embedding-3-small`). Se guarda `embedding_model` por fila para poder migrar. La elección final se hace evaluando 2 modelos sobre 200 consultas reales por rubro (pregunta abierta P-4).

## Alternativas consideradas
| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| BD vectorial externa | Escala vectorial | Dos fuentes de verdad, sincronización, costo (descartada también en la v1) |
| Solo vector | Simple | Falla con códigos exactos y números de parte |
| Suma ponderada de puntajes | Intuitiva | Escalas incomparables entre FTS, trigram y coseno; RRF no requiere calibración |

## Consecuencias
* (+) El SKU exacto siempre sale primero; los errores de tipeo se toleran; la búsqueda funciona aunque el embedding no exista todavía.
* (−) Cuatro subconsultas por búsqueda; con catálogos de PyME (< 50 k ítems por tenant) se mantiene en milisegundos con los índices definidos. Medir con `EXPLAIN ANALYZE` al cargar los seeds.

## Verificación
pgTAP `03_*` #10–#15 y un benchmark de relevancia (MRR@5) en `evals/search/` con consultas reales anonimizadas.
