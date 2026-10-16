# Seeds

> **Estado: pendiente** (issue #27, fase 2). Esta carpeta todavía no tiene archivos `.sql`; `supabase/config.toml` ya apunta a `./seed/*.sql`, así que se cargarán solos cuando existan. Mientras tanto, las pruebas usan sus propios datos sintéticos (`supabase/tests/database/_fixtures.psql`).

Datos **sintéticos** por rubro, previstos como `seed_importadora.sql` y `seed_clinica.sql`. Se cargan con `supabase db reset`.
Reglas: nada de datos reales de clientes o pacientes; marcas y códigos de ejemplo genéricos; un TC inicial explícito en `exchange_rates` (no existe valor por defecto).
