# Cómo contribuir

## Flujo

1. Crea una rama desde `main`: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
2. Commits con [Conventional Commits](https://www.conventionalcommits.org/es/) (`feat(pagos): …`).
3. Abre un PR con la plantilla. CI debe estar en verde y se requiere 1 revisión.
4. *Squash merge* a `main`.

## Convenciones

* **Base de datos:** las migraciones solo avanzan (nunca se edita una ya aplicada en un entorno compartido). Nombre: `AAAAMMDDHHMMSS_descripcion.sql`. Todo cambio de esquema trae su prueba pgTAP.
* **Python:** 3.12+, `ruff` (lint + formato), `mypy --strict`, `pytest`. Dinero siempre con `Decimal`.
* **TypeScript:** `strict`, ESLint, Prettier.
* **Decisiones:** si un cambio altera una decisión de arquitectura, agrega o actualiza un ADR en `docs/adr/` (plantilla `0000-plantilla.md`).
* **Datos de prueba:** siempre sintéticos. Nunca datos reales de clientes o pacientes.

## Definición de terminado

Ver `SYSTEM_DESIGN.md` §13.3.
