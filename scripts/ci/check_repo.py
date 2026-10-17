#!/usr/bin/env python3
"""Chequeos de consistencia del repositorio (se ejecutan en CI y localmente).

Uso:  python scripts/ci/check_repo.py

Verifica, sin dependencias externas:
  1. backlog.json: JSON válido, títulos únicos, cada issue usa milestones y etiquetas
     declarados, y cada issue trae criterios de aceptación.
  2. Enlaces relativos en archivos Markdown: el archivo destino existe.
  3. ADRs: numeración única y consecutiva; cada ADR tiene un estado.
  4. Migraciones: nombre AAAAMMDDhhmmss_nombre.sql, orden estricto, sin duplicados.
  5. Skills: cada skill de .agents/skills/ tiene su puente en .claude/skills/ con el mismo
     frontmatter (Claude Code solo descubre skills ahí) y el puente apunta a la fuente.
  6. Workflows: toda action externa (`uses:`) se fija por el SHA completo del commit y lleva
     la versión en un comentario (`owner/repo@<40 hex> # vX.Y.Z`). Una etiqueta se puede mover.

Sale con código 1 si encuentra errores.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def check_backlog() -> None:
    path = ROOT / "scripts/github/backlog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        err(f"backlog.json ilegible: {exc}")
        return
    milestones = {m["title"] for m in data["milestones"]}
    labels = {lb["name"] for lb in data["labels"]}
    seen: set[str] = set()
    for i, issue in enumerate(data["issues"], 1):
        title = issue.get("title", f"<issue {i} sin título>")
        if title in seen:
            err(f"backlog: título duplicado: {title}")
        seen.add(title)
        if issue.get("milestone") not in milestones:
            err(f"backlog: '{title}' usa un milestone inexistente: {issue.get('milestone')}")
        for lb in issue.get("labels", []):
            if lb not in labels:
                err(f"backlog: '{title}' usa una etiqueta inexistente: {lb}")
        if "Criterios de aceptación" not in issue.get("body", ""):
            err(f"backlog: '{title}' no tiene criterios de aceptación")
    print(f"  backlog: {len(data['issues'])} issues, {len(milestones)} milestones, {len(labels)} etiquetas")


LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_DIRS = {".git", "node_modules", ".venv", "evidence"}


def check_markdown_links() -> None:
    files = [p for p in ROOT.rglob("*.md")
             if not SKIP_DIRS & set(p.relative_to(ROOT).parts)
             and p.relative_to(ROOT).parts[:2] != (".claude", "worktrees")]  # worktrees de otras sesiones
    checked = 0
    for md in files:
        text = md.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)  # ignorar bloques de código
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue  # URL externa, mailto:, computer:// o ancla interna
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            checked += 1
            if not (md.parent / rel).resolve().exists():
                err(f"enlace roto en {md.relative_to(ROOT)}: {target}")
    print(f"  markdown: {len(files)} archivos, {checked} enlaces relativos")


def check_adrs() -> None:
    adr_dir = ROOT / "docs/adr"
    numbers: list[int] = []
    for p in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        n = int(p.name[:4])
        if n == 0:
            continue  # plantilla
        numbers.append(n)
        head = p.read_text(encoding="utf-8")[:1500].lower()
        if "estado" not in head and "status" not in head:
            err(f"ADR sin estado: {p.name}")
    if len(numbers) != len(set(numbers)):
        err(f"ADRs con número repetido: {numbers}")
    if numbers and numbers != list(range(1, max(numbers) + 1)):
        err(f"ADRs con huecos en la numeración: {numbers}")
    print(f"  ADRs: {len(numbers)}")


MIGRATION = re.compile(r"^(\d{14})_[a-z0-9_]+\.sql$")


def check_migrations() -> None:
    files = sorted(p.name for p in (ROOT / "supabase/migrations").glob("*.sql"))
    stamps: list[str] = []
    for name in files:
        m = MIGRATION.match(name)
        if not m:
            err(f"migración con nombre inválido: {name} (esperado AAAAMMDDhhmmss_nombre.sql)")
            continue
        stamps.append(m.group(1))
    if len(stamps) != len(set(stamps)):
        err("migraciones con la misma marca de tiempo")
    print(f"  migraciones: {len(files)}")


FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def check_skills() -> None:
    sources = {p.parent.name: p for p in (ROOT / ".agents/skills").glob("*/SKILL.md")}
    bridges = {p.parent.name: p for p in (ROOT / ".claude/skills").glob("*/SKILL.md")}
    for name in sorted(set(bridges) - set(sources)):
        err(f"skills: .claude/skills/{name} no tiene fuente en .agents/skills/")
    for name, src in sorted(sources.items()):
        bridge = bridges.get(name)
        if bridge is None:
            err(f"skills: falta el puente .claude/skills/{name}/SKILL.md (Claude Code no verá la skill)")
            continue
        src_fm = FRONTMATTER.match(src.read_text(encoding="utf-8"))
        text = bridge.read_text(encoding="utf-8")
        bridge_fm = FRONTMATTER.match(text)
        if not src_fm or not bridge_fm or src_fm.group(1) != bridge_fm.group(1):
            err(f"skills: el frontmatter de .claude/skills/{name} no coincide con la fuente")
        if f".agents/skills/{name}/SKILL.md" not in text:
            err(f"skills: .claude/skills/{name} no apunta a .agents/skills/{name}/SKILL.md")
    print(f"  skills: {len(sources)}")


USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"#\s*v\d+(\.\d+){0,2}\b")


def check_workflows() -> None:
    files = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
    count = 0
    for wf in files:
        for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            m = USES.match(line)
            if not m:
                continue
            ref, rest = m.group(1).strip("'\""), m.group(2)
            if ref.startswith("./"):
                continue  # action local del repo
            count += 1
            where = f"{wf.relative_to(ROOT)}:{n}"
            if not PINNED.match(ref):
                err(f"workflow: {where} usa `{ref}` sin fijar por SHA de 40 caracteres")
            elif not VERSION_COMMENT.search(rest):
                err(f"workflow: {where} fija `{ref}` sin la versión en un comentario (# vX.Y.Z)")
    print(f"  workflows: {len(files)} archivos, {count} actions fijadas por SHA")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    print("Chequeos del repositorio")
    check_backlog()
    check_markdown_links()
    check_adrs()
    check_migrations()
    check_skills()
    check_workflows()
    if errors:
        print(f"\n✗ {len(errors)} problema(s):")
        for e in errors:
            print("  -", e)
        return 1
    print("\n✓ todo en orden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
