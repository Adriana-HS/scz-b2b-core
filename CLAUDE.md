# CLAUDE.md

Las instrucciones del proyecto para cualquier asistente están en `AGENTS.md` (fuente única):

@AGENTS.md

Las skills del proyecto viven en `.agents/skills/` (formato estándar SKILL.md, fuente única). Claude Code las descubre por los puentes de `.claude/skills/`, que solo copian el frontmatter y remiten a la fuente; `scripts/ci/check_repo.py` verifica que coincidan. Cuando una tarea coincida con su descripción, lee el `SKILL.md` de `.agents/skills/` antes de empezar.
