# Seeds

Datos **sintéticos** por rubro para desarrollo y demos (#27). Los cargan solos `supabase db start` (al crear la base) y `supabase db reset`, porque `supabase/config.toml` apunta a `./seed/*.sql`. La prueba `supabase/tests/database/17_seeds.test.sql` verifica que estén y que sirvan.

| Archivo | Negocio | Qué trae |
| :--- | :--- | :--- |
| `seed_importadora.sql` | **Importadora de Ejemplo** (`5eed0000-…-00000000000a`) | 30 repuestos en 8 categorías (frenos, filtros, suspensión, motor, embrague, refrigeración, eléctrico, transmisión), en USD y en Bs, uno sin stock; número de WhatsApp `SEED-IMP-0001`; **TC inicial 11,68 Bs/USD** |
| `seed_clinica.sql` | **Consultorio de Ejemplo** (`5eed0000-…-00000000000b`) | 16 servicios odontológicos en Bs con duración; 2 profesionales con horario de lunes a viernes 08:00-18:00 y sábados 08:00-12:00; seña del 30 % y turnos cada 30 min; número `SEED-CLI-0001` |

## Reglas

* **Sin marcas.** La compatibilidad se describe por tipo de vehículo ("Camioneta 4x4 diésel 2.4"), nunca por fabricante. La prueba 17 rechaza una lista de marcas comunes.
* **Sin datos personales.** No hay clientes ni pacientes; los profesionales tienen nombres genéricos. Los números de WhatsApp son `+5910000xxxx` y los tokens, nombres de variables de entorno ficticias (`WA_TOKEN_SEED_*`).
* **Tipo de cambio explícito.** No existe valor por defecto. El seed carga 11,68 Bs/USD (TCO del 15-oct-2026) como `REPOSICION_MANUAL`, vigente 24 h (`fx_max_age_hours`). Para cotizar ítems en USD otro día, carga el TC del día o vuelve a correr `supabase db reset`.
* **Ids fijos** (`5eed0000-…`): no chocan con los de las pruebas pgTAP (`_fixtures.psql`) ni con los datos aleatorios de las pruebas de integración.

## Efectos que conviene saber

* **Cola de embeddings.** Cada ítem nuevo encola un trabajo `EMBED_ITEM`, así que los seeds dejan **46 trabajos `EMBED_ITEM` en cola**. Es lo esperado: los procesará el worker cuando exista el handler (#23). Mientras tanto, una prueba que reclame trabajos debe filtrar por tipo, como el worker real (`claim_jobs(..., p_kinds)`).
* **TC vencido en una BD vieja.** Si la base local lleva más de 24 h levantada, la importadora de ejemplo cuenta en `fx_stale_tenants` y en la alerta `FxRateStale`. La prueba 15 compara contra una línea de base para no depender de eso.
