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
| `database` | Migraciones sobre la imagen oficial de Supabase y las 109 pruebas pgTAP | Sí |
| `database` → lint | `supabase db lint --fail-on error` (plpgsql_check) sobre las funciones | Sí (el primer reporte, del 16-oct-2026, salió sin errores) |
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
| `06_payment_mfa.test.sql` | SEC-01: MFA incondicional para aprobar pagos, editar la organización, cambiar membresías y cargar TC; el interruptor `require_mfa_for_payments` ya no existe | 11 |
| `07_stock_lifecycle.test.sql` | Reservas de stock (ADR-0015): una orden en revisión con `valid_until` vencido sigue bloqueando; cancelar libera; cancelar o reembolsar una confirmada devuelve el stock; seña 0 compromete; `STOCK_CONFLICT` sin efectos parciales | 19 |
| **Total** | | **109** |

`_fixtures.psql` crea dos tenants sintéticos y helpers (`tests.as_user`, `tests.as_backend`, `tests.as_anon`). Cada archivo corre dentro de `BEGIN … ROLLBACK`.

### Verificación local sin Docker

La CI corre sobre la imagen oficial de Supabase con PostgreSQL 17 (`supabase/config.toml`, `major_version = 17`). Antes de tener CI, el esquema se verificó en PostgreSQL 16 + pgvector 0.8 + pgTAP con el *shim* `scripts/local_supabase_shim.sql`, que emula roles y `auth.uid()` de Supabase. **No reemplaza** a `supabase test db`: es solo para entornos sin Docker.

```bash
createdb scz_test
psql -d scz_test -f scripts/local_supabase_shim.sql
for f in supabase/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -d scz_test -f "$f"; done
for f in supabase/tests/database/0*.test.sql; do psql -d scz_test -f "$f"; done
```

## Backend y frontend

Ver la estrategia en `SYSTEM_DESIGN.md` §13.1 (unitarias, propiedades, contrato, integración, seguridad, carga, caos, evals).
