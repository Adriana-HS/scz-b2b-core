# Skills y herramientas de IA para el proyecto (verificado al 15-oct-2026)

Las *skills* son carpetas con un `SKILL.md` (instrucciones + scripts opcionales) que un asistente carga cuando la tarea coincide. El formato es un estándar abierto ([agentskills.io](https://agentskills.io)) y lo usan **Claude** (Claude Code, apps) y **OpenAI Codex** (incluido en ChatGPT Plus).

| Dónde las busca | Claude Code | Codex (ChatGPT) |
| :--- | :--- | :--- |
| En el repo | `.claude/skills/` | `.agents/skills/` (desde la carpeta actual hasta la raíz) |
| Del usuario | `~/.claude/skills/` | `~/.agents/skills/` |
| Instrucciones del repo | `CLAUDE.md` | `AGENTS.md` |

Fuentes: [Codex — Build skills](https://learn.chatgpt.com/docs/build-skills) · [Codex — Pricing/planes](https://learn.chatgpt.com/docs/pricing)

## 1. Skills propias del proyecto (ya incluidas)

En `.agents/skills/` (las lee Codex; Claude Code las encuentra por `CLAUDE.md`):

| Skill | Cuándo se usa |
| :--- | :--- |
| `scz-implementar-issue` | Implementar cualquier issue del backlog con el flujo y la DoD del proyecto |
| `scz-nueva-migracion` | Cualquier cambio de esquema (checklist de RLS, FK compuestas, privilegios, pgTAP) |
| `scz-revision-seguridad` | Revisar un PR o un diseño contra el modelo de amenazas del proyecto |
| `scz-nuevo-adr` | Proponer o cambiar una decisión de arquitectura |
| `scz-nuevo-rubro` | Agregar un rubro (`VerticalPolicy`) sin tocar el núcleo |

## 2. Skills externas recomendadas

> ⚠️ **Cadena de suministro:** una skill puede ejecutar scripts. Antes de instalar, leer su `SKILL.md` y sus scripts, preferir repos de organizaciones conocidas y fijar un commit.

### Diseño, planificación y proceso

| Skill | Repo | Para qué aquí | Claude | Codex |
| :--- | :--- | :--- | :---: | :---: |
| `brainstorming`, `writing-plans`, `executing-plans` | [obra/superpowers](https://github.com/obra/superpowers) | Pasar de un issue a un plan por pasos antes de tocar código | ✓ | ✓ (también en [openai/plugins](https://github.com/openai/plugins) como `superpowers`) |
| `test-driven-development`, `systematic-debugging`, `verification-before-completion` | obra/superpowers | Disciplina TDD y no declarar "listo" sin correr las pruebas | ✓ | ✓ |
| `requesting-code-review`, `receiving-code-review` | obra/superpowers | Revisión de PR estructurada | ✓ | ✓ |
| `doc-coauthoring` | [anthropics/skills](https://github.com/anthropics/skills) | Redactar specs, ADRs y documentos de decisión | ✓ | — |
| `skill-creator` | anthropics/skills · `.system` de Codex | Crear o mejorar las skills del proyecto | ✓ | ✓ |
| `mcp-builder` | anthropics/skills | Si se expone el sistema como servidor MCP | ✓ | — |
| `webapp-testing` | anthropics/skills | Pruebas del dashboard con Playwright | ✓ | — (Codex: `playwright`) |
| `frontend-design` | anthropics/skills | Diseño visual del dashboard | ✓ | — |

### Base de datos (Supabase/Postgres)

| Skill | Repo | Para qué aquí | Claude | Codex |
| :--- | :--- | :--- | :---: | :---: |
| `supabase-postgres-best-practices` | [supabase/agent-skills](https://github.com/supabase/agent-skills) (oficial, MIT) | Reglas de índices, RLS, bloqueos, colas y pgvector. Cargarla en **toda** migración. De aquí salió la migración 08 (índices de FK) y la prueba `05_schema_lint` | ✓ | ✓ (también en openai/plugins → `supabase`) |
| `supabase` | supabase/agent-skills | Checklist de seguridad de Supabase (Auth, RLS, Storage, Data API) y cambios recientes de la plataforma | ✓ | ✓ |

### Seguridad

| Skill | Repo | Para qué aquí | Claude | Codex |
| :--- | :--- | :--- | :---: | :---: |
| `threat-model`, `security-scan`, `security-diff-scan`, `attack-path-analysis`, `propose-security-hardening` | [openai/plugins → codex-security](https://github.com/openai/plugins) | Modelo de amenazas del repo, escaneo completo y revisión de diffs | — | ✓ |
| `sharp-edges` | [trailofbits/skills](https://github.com/trailofbits/skills) | Detectar APIs y configuraciones peligrosas ("seguro por defecto") | ✓ | ✓ (vía compatibilidad de marketplace) |
| `differential-review`, `review-pr` | trailofbits/skills | Revisión de seguridad de cada PR, con radio de impacto | ✓ | ✓ |
| `semgrep`, `codeql`, `sarif-parsing` | trailofbits/skills | Análisis estático. En repos privados, CodeQL de GitHub es de pago; Semgrep local es la alternativa | ✓ | ✓ |
| `supply-chain-risk-auditor` | trailofbits/skills | Auditar dependencias de Python y npm | ✓ | ✓ |
| `agentic-actions-auditor` | trailofbits/skills | Revisar workflows de GitHub Actions que usen agentes de IA | ✓ | ✓ |

### Calidad y pruebas

| Skill | Repo | Para qué aquí |
| :--- | :--- | :--- |
| `property-based-testing` | trailofbits/skills | Pruebas con Hypothesis para el dinero (redondeo, IVA, TC) y la FSM |
| `mutation-testing` | trailofbits/skills | Medir si las pruebas detectan errores reales |
| `modern-python` | trailofbits/skills | Configuración con uv + ruff + verificador de tipos |
| `second-opinion` | trailofbits/skills | Pedir a un segundo asistente una revisión independiente |

### Integraciones (plugins de Codex en openai/plugins)

`supabase`, `github`, `sentry`, `vercel`, `product-design`. Revisar sus permisos antes de conectarlos.

## 3. Instalación rápida

**Claude Code**

```text
/plugin marketplace add supabase/agent-skills
/plugin marketplace add trailofbits/skills
/plugin marketplace add anthropics/skills
```

Nombres verificados el 15-oct-2026 contra los repos: supabase/agent-skills `c9be0e9`, trailofbits/skills `1f68e03`, obra/superpowers `bb92a77`, openai/plugins (codex-security, supabase, superpowers). Antes de instalar, revisar que el nombre siga existiendo; estos repos cambian seguido.

**Codex (CLI o IDE)**

```text
$skill-installer <nombre>            # skills curadas
# o copiar la carpeta de la skill a .agents/skills/ (del repo) o ~/.agents/skills/
```

## 4. Referencias de buenas prácticas (no son skills, pero conviene tenerlas a mano)

OWASP ASVS · OWASP Top 10 para aplicaciones LLM · Modelo C4 (c4model.com) · arc42 (arc42.org) · Azure Architecture Center (patrones de nube) · *Patterns of Enterprise Application Architecture* (Fowler) · *Enterprise Integration Patterns* (Hohpe y Woolf).
