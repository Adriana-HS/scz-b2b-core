# ADR-0011: Acceso transaccional a PostgreSQL desde el backend

* **Estado:** Aceptado
* **Fecha:** 2026-10-15
* **Relacionado:** SYSTEM_DESIGN.md §4.3, §9.3 · ADR-0002

## Contexto
La v1 nombra `supabase_repo.py` (cliente `supabase-py`, que habla con PostgREST). PostgREST ejecuta **una petición por transacción**: no permite `BEGIN … SET LOCAL … varias sentencias … COMMIT` desde el cliente. El modelo de ADR-0002 (tenant por transacción) y la atomicidad de webhook, cotización y pago necesitan transacciones reales.

## Decisión
* El backend se conecta directo a Postgres como `app_backend` (asyncpg, opcionalmente con SQLAlchemy 2 Core) a través de **Supavisor en modo transacción**, sin *prepared statements* del lado del cliente.
* Un `UnitOfWork(org_id, actor)` abre la transacción y ejecuta `SET LOCAL app.current_org_id` y `SET LOCAL app.actor`. Ningún repositorio funciona fuera de un `UnitOfWork`.
* La lógica que debe ser atómica y no depender del backend (precio, stock, agenda, FSM, pagos) vive en **RPCs y triggers** probados con pgTAP. Python orquesta.
* El dashboard sigue usando `supabase-js` con el JWT del usuario (PostgREST + RLS).

## Consecuencias
* (+) Atomicidad y aislamiento verificables.
* (−) Hay dos estilos de acceso (directo en el backend, PostgREST en el dashboard). Ambos pasan por RLS.

## Verificación
Prueba de integración: un repositorio usado sin `UnitOfWork` lanza una excepción; una prueba con `SET LOCAL` en modo transacción del *pooler* confirma que el contexto no se filtra entre conexiones.
