# Gestión del proyecto

Cómo se organiza el trabajo para que cualquier persona (o asistente de IA) sepa **qué hacer, en qué orden, con qué criterio de terminado y dónde queda registrado**.

## 1. Herramientas

| Necesidad | Herramienta | Por qué |
| :--- | :--- | :--- |
| Código, revisión y CI | Repositorio **privado** en GitHub | Fuente única; PR obligatorios |
| Tareas | **GitHub Issues** (con plantillas) | Cada tarea enlaza requisitos (RF-xx), ADRs y PRs |
| Tablero y roadmap | **GitHub Projects** (gratis, también en repos privados) | Vistas de tablero (Kanban) y de roadmap por fase |
| Fases | **Milestones** = fases del `docs/ROADMAP.md` | Avance por fase con porcentaje |
| Decisiones | `docs/adr/` | Quedan versionadas con el código |
| Conocimiento externo | `docs/research/` | Fuentes y fecha de verificación |

**No dependemos de ninguna herramienta de IA para recordar el proyecto:** todo el contexto vive en el repo (este documento, `AGENTS.md`, `SYSTEM_DESIGN.md`, el SRS y los ADRs). Así, Claude, ChatGPT/Codex o una persona nueva pueden continuar sin pérdida.

## 2. Repositorio privado: configuración recomendada

1. **Settings → General → Danger Zone → Change visibility → Private.**
2. **Protección de `main`:** en repos privados del plan Free, GitHub **no aplica** las reglas de rama. Opciones:
   * **GitHub Student Developer Pack** (si alguien del equipo es estudiante; incluye GitHub Pro) → *Settings → Rules → Rulesets*: PR obligatorio, 1 aprobación, CI `database` en verde, sin *force push*.
   * Sin Pro: misma regla como acuerdo de equipo, más CI que corre en cada PR.
3. **Colaboradores:** *Settings → Collaborators* (rol *Write* para quien desarrolla, *Maintain* para la responsable).
4. **Secretos de Actions:** *Settings → Secrets and variables → Actions*. Nunca en el código.
5. **Dependabot:** *Settings → Code security → Dependabot alerts y security updates* (disponible en privados).
6. **Minutos de Actions:** el plan Free incluye una cuota mensual de minutos para repos privados. Revisar el consumo en *Settings → Billing*.

## 3. Estructura del backlog

```
Milestone (fase)  ──►  Issue (historia, spike, chore)  ──►  PR  ──►  Commit(s)
      │                     │                               │
      └ ROADMAP.md          └ RF-xx / ADR-xxxx              └ "Closes #12"
```

* **Etiquetas:** `type:*` (feature, bug, spike, chore, docs, test, adr), `area:*` (db, backend, ia, whatsapp, pagos, agenda, frontend, ops, security, compliance), `prio:*` (M, S, C), `blocked`.
* **Plantillas:** `.github/ISSUE_TEMPLATE/` (funcionalidad, defecto, spike, ADR).
* **Backlog inicial:** `scripts/github/backlog.json`, con 42 issues en 8 fases, cada uno con criterios de aceptación.

### 3.1 Cargar el backlog en GitHub (una sola vez)

Requisitos: [GitHub CLI](https://cli.github.com/) y Python 3.9+.

```powershell
gh auth login
gh auth refresh -s project            # solo si se quiere crear el GitHub Project
cd C:\AHS\scz-b2b-core
python scripts\github\bootstrap.py --repo Adriana-HS/scz-b2b-core --dry-run   # revisar
python scripts\github\bootstrap.py --repo Adriana-HS/scz-b2b-core --project "SCZ-B2B-Core"
```

El script es idempotente: si se ejecuta dos veces, no duplica nada.

### 3.2 Tablero (GitHub Projects)

| Columna | Entra cuando… | Sale cuando… |
| :--- | :--- | :--- |
| **Backlog** | El issue existe | Cumple la *Definition of Ready* |
| **Listo** | Tiene RF/ADR, criterios de aceptación y dependencias resueltas | Alguien lo toma |
| **En curso** | Hay rama `feat/<n>-…` asignada (máx. 2 por persona) | Se abre el PR |
| **En revisión** | PR abierto con CI en verde | Aprobado y fusionado |
| **Hecho** | Se cumple la DoD (`SYSTEM_DESIGN.md` §13.3) | — |

Campos útiles del Project: *Fase* (milestone), *Prioridad*, *Estimación* (S/M/L) y *Área*.

### 3.3 Definition of Ready

Un issue entra a **Listo** solo si tiene: requisitos (RF-xx) o ADR enlazados, criterios de aceptación verificables, dependencias identificadas y tamaño ≤ 3 días (si es más grande, se parte en sub-issues).

## 4. Ritmo de trabajo

| Ritual | Frecuencia | Qué se hace |
| :--- | :--- | :--- |
| Planificación | Semanal (30 min) | Pasar issues de Backlog a Listo según la fase; revisar bloqueos |
| Seguimiento | 2–3 veces por semana (10 min) | ¿Qué terminé, qué sigue, qué me bloquea? |
| Revisión de fase | Al cerrar cada milestone | Criterios de salida del ROADMAP; actualizar el registro de restricciones externas |
| Retrospectiva | Al cerrar cada fase | Qué mejorar del proceso |

## 5. Flujo de una tarea (persona o asistente de IA)

1. Tomar un issue de **Listo** y moverlo a **En curso**.
2. Crear la rama `feat/<número>-<slug>` (o `fix/`, `docs/`, `chore/`).
3. Leer los RF del SRS, las secciones del diseño y los ADRs citados.
4. Escribir primero las pruebas (pgTAP si toca la BD; pytest si toca Python).
5. Implementar en pasos pequeños; los commits siguen Conventional Commits.
6. Correr las pruebas localmente; actualizar la documentación afectada y el CHANGELOG.
7. Abrir el PR con la plantilla y `Closes #<número>`.
8. Revisión (humana; opcionalmente, una segunda opinión de otro asistente).
9. Fusionar (*squash*) y mover el issue a **Hecho**.

## 6. Trabajo con asistentes de IA

| Asistente | Cómo recibe el contexto |
| :--- | :--- |
| **ChatGPT / Codex** (incluido en ChatGPT Plus: web, CLI, extensión de IDE) | Lee `AGENTS.md` automáticamente y las skills de `.agents/skills/` |
| **Claude / Claude Code** | Lee `CLAUDE.md`, que remite a `AGENTS.md` |
| Cualquier chat | Pegar `AGENTS.md` + el issue + las secciones citadas |

Reglas: un issue por sesión de trabajo; el asistente **no** decide arquitectura nueva sin un ADR; todo cambio entra por PR con CI en verde; no pegar secretos ni datos reales de clientes en ningún chat. En el plan Plus el uso tiene límites por ventana de tiempo: reservarlo para implementar y revisar, no para releer documentos que ya están en el repo.

Herramientas y skills recomendadas: `docs/HERRAMIENTAS_IA.md`.
