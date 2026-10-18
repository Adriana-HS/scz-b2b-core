# ADR-0020: Búsqueda con sinónimos por rubro, sin embeddings por ahora

* **Estado:** Aceptado (AHS, 18-oct-2026). Complementa el ADR-0003: el recuperador vectorial queda inactivo hasta que un ADR nuevo elija un modelo.
* **Fecha:** 2026-10-18
* **Decisores:** AHS
* **Relacionado:** ADR-0003 (búsqueda híbrida) · ADR-0018 (proveedor de IA) · ADR-0019 · #23 · migración 21 · pgTAP `19_search_synonyms` · `evals/search/`

## Contexto

* **Lo que falta.** La búsqueda (ADR-0003) ya encuentra por palabras en español, por SKU y con errores de tipeo. No entiende sinónimos: "zapatas" no encontraba "pastillas de freno" ni "calza" una restauración con resina. Los embeddings resuelven eso, pero hay que elegir quién los calcula.
* **Opciones comparadas** (precios oficiales del 18-oct-2026; uso de un negocio mediano, menos de 200 mil tokens al mes):
  * **A.** Modelo propio en el worker (paraphrase-multilingual-MiniLM, Apache-2.0, 220 MB).
    * Costo: US$ 0.
    * Datos de clientes: no salen.
    * Contras: más memoria en el worker y migración a 384 dimensiones.
  * **B.** Voyage `voyage-4-lite`.
    * Costo: ≈ US$ 0,004 al mes.
    * Contras: cuenta nueva con tarjeta, y por defecto guarda y entrena con los datos (hay que excluirse en su panel).
  * **C.** Gemini Embedding 2 de pago.
    * Costo: ≈ US$ 0,04 al mes.
    * Contras: requiere facturación de Google Cloud. El plan gratuito no sirve, porque usa los datos.
  * **D.** OpenAI `text-embedding-3-small`.
    * Costo: ≈ US$ 0,004 al mes.
    * Contras: cuenta nueva.
  * **E.** Sin embeddings, con sinónimos por rubro en Postgres.
    * Costo: US$ 0, sin cuentas y sin datos a terceros.
    * Contras: la lista se mantiene a mano.
* **Qué pesa.** El costo de A a D es de centavos. Lo que decide son las cuentas nuevas, adónde van las búsquedas de los clientes y el trabajo de operación (criterios de AHS: sin costos ni cuentas extra).

## Decisión

**Decidido por AHS el 18-oct-2026: opción E por el momento.**

1. **Sinónimos:** tabla `public.search_synonyms` con grupos de palabras por rubro ("zapata" = "pastilla" = "balata"). Son datos de la plataforma, como `tax_regimes`: sin `organization_id`, de solo lectura para la aplicación y cambiados por migración. Lista inicial de 76 términos en 25 grupos.
2. **Expansión:** `private.search_tsquery` expande cada palabra de la consulta a sus sinónimos, en singular o plural, y `search_catalog` la usa en su recuperador de texto. Lo demás del ADR-0003 no cambia.
3. **Trabajos de embeddings:** se dejan de encolar los `EMBED_ITEM`, porque nadie los procesa, y se descartan los que había. La columna `embedding` y el recuperador vectorial quedan en el esquema, sin uso.

## Alternativas consideradas

| Alternativa | A favor | En contra |
| :--- | :--- | :--- |
| **E. Sinónimos por rubro (elegida)** | Gratis; sin cuentas; nada sale del sistema; explicable ("zapata" está en la lista) | Lista manual; no entiende frases ni intenciones que no estén en la lista |
| A. Modelo propio en el worker | Gratis; nada sale del sistema; entiende significado | Más memoria y operación; calidad a medir |
| B. Voyage | Calidad alta; centavos | Cuenta nueva; entrena con los datos salvo exclusión |
| C. Gemini Embedding 2 de pago | La columna ya mide 1536 | Facturación de Google Cloud |
| D. OpenAI | La columna ya mide 1536 | Cuenta nueva |

## Consecuencias

* **(+)** Medido con `evals/search/` (consultas inventadas, sin datos reales), MRR@5:
  * importadora: de 0,86 sin sinónimos a 1,00 con sinónimos;
  * consultorio: de 0,71 a 0,92.
  * El objetivo del #23 es 0,8.
* **(+)** Sin costo, sin cuentas y sin enviar búsquedas de clientes a terceros.
* **(−)** Las consultas del eval las escribimos conociendo la lista de sinónimos: el número es optimista. Hace falta medir con consultas reales anonimizadas del piloto (pregunta P-4 del ADR-0003).
* **(−)** Lo que no está en la lista no se encuentra por significado. Ejemplo: "quisiera una limpieza para mi hijo" sigue fallando en el eval del consultorio.
* **(−)** La lista se mantiene a mano y es igual para todos los negocios del rubro. Que cada negocio agregue los suyos queda para el panel.
* **Cuándo revisar:** si el eval con consultas reales del piloto queda por debajo de 0,8, se reabre con la opción A primero (sin cuentas ni datos afuera) y un ADR nuevo.

## Verificación

* pgTAP `19_search_synonyms`: permisos, expansión, plural, aislamiento por rubro, consultas sin operadores y sin trabajos `EMBED_ITEM`.
* `backend/tests/integration/test_search_eval_db.py`: MRR@5 ≥ 0,8 por rubro y mejor que sin sinónimos.
