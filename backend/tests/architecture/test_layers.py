"""La regla de dependencias de la arquitectura hexagonal también corre como prueba.

Los contratos viven en pyproject.toml ([tool.importlinter]); la CI además ejecuta `lint-imports`.
Corre en un subproceso: import-linter altera el estado global del intérprete (logging) y,
dentro del mismo proceso, afectaba a otras pruebas según el orden de ejecución.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_las_dependencias_entre_capas_respetan_la_arquitectura() -> None:
    # Los nombres de los contratos llevan tildes: UTF-8 en ambos lados (en Windows la consola no lo es).
    result = subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports; raise SystemExit(lint_imports())"],
        cwd=BACKEND,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout
