#!/usr/bin/env python3
"""Crea en GitHub las etiquetas, los milestones (fases) y los issues del backlog.

Uso (desde la raíz del repo, con GitHub CLI autenticado: `gh auth login`):

    python scripts/github/bootstrap.py --repo Adriana-HS/scz-b2b-core --dry-run
    python scripts/github/bootstrap.py --repo Adriana-HS/scz-b2b-core
    python scripts/github/bootstrap.py --repo Adriana-HS/scz-b2b-core --project "SCZ-B2B-Core"

Es idempotente: no duplica etiquetas, milestones ni issues que ya existan (compara por título).
Para --project, el token necesita el scope `project`: `gh auth refresh -s project`.
Solo usa la biblioteca estándar de Python 3.9+ y el ejecutable `gh`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BACKLOG = Path(__file__).with_name("backlog.json")


class Gh:
    def __init__(self, repo: str, dry_run: bool) -> None:
        self.repo = repo
        self.dry_run = dry_run

    def run(self, *args: str, mutate: bool = False) -> str:
        cmd = ["gh", *args]
        if mutate and self.dry_run:
            print("[dry-run]", " ".join(a if " " not in a else repr(a) for a in cmd[:8]), "…")
            return ""
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])} falló: {result.stderr.strip()}")
        return result.stdout

    def api_paginated(self, path: str) -> list[dict]:
        out = self.run("api", "--paginate", f"repos/{self.repo}/{path}?per_page=100&state=all")
        items: list[dict] = []
        # --paginate concatena arreglos JSON: "[...][...]"
        decoder = json.JSONDecoder()
        text, idx = out.strip(), 0
        while idx < len(text):
            chunk, end = decoder.raw_decode(text, idx)
            items.extend(chunk)
            idx = end
            while idx < len(text) and text[idx].isspace():
                idx += 1
        return items


def ensure_labels(gh: Gh, labels: list[dict]) -> None:
    for lb in labels:
        gh.run("label", "create", lb["name"], "--repo", gh.repo, "--color", lb["color"],
               "--description", lb["description"], "--force", mutate=True)
    print(f"✓ etiquetas: {len(labels)}")


def ensure_milestones(gh: Gh, milestones: list[dict]) -> None:
    existing = {m["title"] for m in gh.api_paginated("milestones")}
    created = 0
    for ms in milestones:
        if ms["title"] in existing:
            continue
        gh.run("api", f"repos/{gh.repo}/milestones", "-f", f"title={ms['title']}",
               "-f", f"description={ms['description']}", mutate=True)
        created += 1
    print(f"✓ milestones: {created} nuevos, {len(milestones) - created} existentes")


def ensure_issues(gh: Gh, issues: list[dict], project: str | None, owner: str) -> None:
    existing = {i["title"] for i in gh.api_paginated("issues") if "pull_request" not in i}
    created = 0
    for issue in issues:
        if issue["title"] in existing:
            continue
        args = ["issue", "create", "--repo", gh.repo, "--title", issue["title"],
                "--body", issue["body"], "--milestone", issue["milestone"]]
        for label in issue["labels"]:
            args += ["--label", label]
        if project:
            args += ["--project", project]
        url = gh.run(*args, mutate=True).strip()
        created += 1
        if url:
            print("  +", url)
    print(f"✓ issues: {created} nuevos, {len(issues) - created} existentes")


def ensure_project(gh: Gh, title: str, owner: str) -> None:
    out = gh.run("project", "list", "--owner", owner, "--format", "json")
    projects = json.loads(out or "{}").get("projects", []) if out else []
    if any(p.get("title") == title for p in projects):
        print(f"✓ proyecto '{title}' ya existe")
        return
    gh.run("project", "create", "--owner", owner, "--title", title, mutate=True)
    print(f"✓ proyecto '{title}' creado (agrega las vistas Tablero y Roadmap desde la web)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/nombre, p. ej. Adriana-HS/scz-b2b-core")
    ap.add_argument("--project", help="Título del GitHub Project (opcional)")
    ap.add_argument("--dry-run", action="store_true", help="Muestra lo que haría sin modificar GitHub")
    ns = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # consolas de Windows (cp1252) y caracteres ✓ ✗ ·
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    data = json.loads(BACKLOG.read_text(encoding="utf-8"))
    gh = Gh(ns.repo, ns.dry_run)
    owner = ns.repo.split("/")[0]
    try:
        gh.run("auth", "status")
        ensure_labels(gh, data["labels"])
        ensure_milestones(gh, data["milestones"])
        if ns.project:
            ensure_project(gh, ns.project, owner)
        ensure_issues(gh, data["issues"], ns.project, owner)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        print("  ¿Instalaste GitHub CLI (https://cli.github.com) y ejecutaste `gh auth login`?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
