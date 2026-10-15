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

## Qué se sube y qué no

| ✅ Se sube (repo privado) | ❌ Nunca se sube |
| :--- | :--- |
| Código, migraciones, pruebas, seeds **sintéticos** | `.env`, tokens de Meta, claves de Supabase, credenciales bancarias o del SIAT, contraseñas |
| Diseño, ADRs, SRS, investigación con **fuentes públicas** | Contratos firmados, acuerdo de socios, opiniones legales confidenciales, documentos de identidad |
| Plantillas de contratos **sin datos** de clientes | Datos reales de clientes o pacientes: conversaciones, comprobantes, teléfonos, NIT/CI |
| Configuración de CI, plantillas de issues, skills del proyecto | Exportaciones o *dumps* de bases de datos de staging o producción |
| `.env.example` con nombres de variables vacíos | Archivos grandes (> 500 KB), binarios generados, `node_modules`, `.venv` |

Los documentos confidenciales viven en el almacenamiento seguro del equipo (por ejemplo, una carpeta compartida con acceso restringido y un gestor de contraseñas para las credenciales). Si un secreto llega a subirse por error, **rótalo de inmediato**: borrarlo del historial no alcanza, porque ya puede haber sido copiado.

## Higiene de commits

* **Identidad:** configura `git config user.name` y `user.email`. Para no exponer tu correo personal, usa el correo *noreply* de GitHub (*Settings → Emails → Keep my email addresses private*).
* **Mensajes:** Conventional Commits en español, en imperativo y con un alcance (`feat(pagos): agrega regla de beneficiario`). Un commit = un cambio lógico; nada de "varios cambios".
* **Nunca** directo a `main`: rama + PR (el hook `no-commit-to-branch` lo impide localmente).
* **Hooks:** `pip install pre-commit && pre-commit install` activa la detección de secretos (gitleaks), claves privadas, archivos grandes y formato.
* **Asistentes de IA:** si un asistente generó buena parte de un cambio, se puede indicar con un *trailer* `Co-authored-by:`. Es una decisión del equipo; lo importante es que una persona revisó y aprobó el PR.

## Definición de terminado

Ver `SYSTEM_DESIGN.md` §13.3.
