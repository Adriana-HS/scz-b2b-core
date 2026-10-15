# Pruebas

## Base de datos (pgTAP)

```bash
supabase db start      # solo Postgres + migraciones
supabase test db
```

En Windows, `.\scripts\test-db.ps1` corre lo mismo que la CI (requiere Docker Desktop y Supabase CLI).

### Qué corre la CI (`.github/workflows/ci.yml`)

| Job | Qué verifica | Bloquea el PR |
| :--- | :--- | :---: |
| `database` | Migraciones sobre la imagen oficial de Supabase y las 79 pruebas pgTAP | Sí |
| `database` → lint | `supabase db lint` (plpgsql_check) sobre las funciones | No, hasta revisar el primer reporte; después se quita `continue-on-error` |
| `repo` | `scripts/ci/check_repo.py`: backlog coherente, enlaces de Markdown, numeración de ADRs y nombres de migraciones | Sí |
| `secrets` | gitleaks sobre todo el historial | Sí |

La versión de Supabase CLI está fijada en `SUPABASE_CLI_VERSION`. Se actualiza en un PR propio, para que un cambio de la CLI no se mezcle con un cambio del esquema.

| Archivo | Cubre | Pruebas |
| :--- | :--- | ---: |
| `01_tenant_isolation.test.sql` | RLS por rol (anon, dashboard por rol, backend con y sin tenant), FK compuestas, Storage, `search_path`, RLS al 100 % | 21 |
| `02_pricing_fsm_payments.test.sql` | Totales exactos, TC congelado, inyección de precio, stock, FSM por actor, doble candado de evidencias, aprobación con MFA, auditoría, append-only | 24 |
| `03_booking_search_queue.test.sql` | Disponibilidad y zona horaria, EXCLUDE anti doble reserva, vencimientos, búsqueda híbrida, TC vencido, debounce y serialización de la cola | 18 |
| `04_tax_regimes.test.sql` | IVA incluido y por fuera (Ley 1733), régimen congelado por orden, seña sobre el total, régimen global de solo lectura | 11 |
| `05_schema_lint.test.sql` | Guardas de regresión: FK con índice, RLS en todas las tablas, `search_path` en SECURITY DEFINER, vistas con `security_invoker`, `anon` sin privilegios | 5 |
| **Total** | | **79** |

`_fixtures.psql` crea dos tenants sintéticos y helpers (`tests.as_user`, `tests.as_backend`, `tests.as_anon`). Cada archivo corre dentro de `BEGIN … ROLLBACK`.

### Verificación local sin Docker

El esquema también se verificó en PostgreSQL 16 + pgvector 0.8 + pgTAP con el *shim* `scripts/local_supabase_shim.sql`, que emula roles y `auth.uid()` de Supabase. **No reemplaza** a `supabase test db`: es solo para entornos sin Docker.

```bash
createdb scz_test
psql -d scz_test -f scripts/local_supabase_shim.sql
for f in supabase/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -d scz_test -f "$f"; done
for f in supabase/tests/database/0*.test.sql; do psql -d scz_test -f "$f"; done
```

## Backend y frontend

Ver la estrategia en `SYSTEM_DESIGN.md` §13.1 (unitarias, propiedades, contrato, integración, seguridad, carga, caos, evals).
