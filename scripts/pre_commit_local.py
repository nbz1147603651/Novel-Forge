"""Run pre-commit local hooks without hard-coded virtualenv paths.

The repository's pre-commit local hooks used to invoke ``.venv/bin/...``
directly, which fails or silently skips on a fresh checkout without a
virtualenv.  This wrapper resolves a usable interpreter (project ``.venv``
first, then the system ``python3``), verifies the hook's runtime dependency is
actually importable, and fails loudly with a clear message when it is not --
never silently skipping a gate.

Hooks:
- ``mypy``            -> scripts/check_mypy_baseline.py (same command as CI)
- ``verify-templates``-> scripts/verify_templates.py
- ``lint-imports``    -> lint-imports console script
- ``drift-check``     -> scripts/check_agents_md_drift.py
- ``contract-consumers`` -> scripts/check_contract_consumers.py --workspace
  (boundary changes in the working tree trigger the consumer validation set)

Set ``PRE_COMMIT_LOCAL_NO_VENV=1`` to force the system interpreter (used by
verification of the no-virtualenv path).

Run ``python scripts/pre_commit_local.py <hook>``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# hook name -> (required import or executable, argv relative to repo root).
_HOOKS: dict[str, tuple[str, list[str]]] = {
    "mypy": ("mypy", ["scripts/check_mypy_baseline.py"]),
    "verify-templates": ("jinja2", ["scripts/verify_templates.py"]),
    "lint-imports": ("exec:lint-imports", ["lint-imports"]),
    "drift-check": ("tomllib", ["scripts/check_agents_md_drift.py"]),
    "contract-consumers": ("argparse", ["scripts/check_contract_consumers.py", "--workspace"]),
}


def resolve_python(force_system: bool = False) -> str | None:
    """Return a usable interpreter: project venv first, then the system python."""

    venv_python = _ROOT / ".venv" / "bin" / "python"
    if not force_system and venv_python.exists():
        return str(venv_python)
    for name in ("python3", "python"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    return None


def dependency_available(python: str, requirement: str) -> bool:
    """Return True when the hook's runtime dependency resolves on this interpreter."""

    if requirement.startswith("exec:"):
        return shutil.which(requirement.removeprefix("exec:")) is not None
    return importlib.util.find_spec(requirement) is not None or _import_check(python, requirement)


def _import_check(python: str, module: str) -> bool:
    """Fallback: probe the resolved interpreter for the module."""

    result = subprocess.run(
        [python, "-c", f"import {module}"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    """Parse the hook name, resolve the interpreter, and run the hook."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hook", choices=sorted(_HOOKS), help="local hook to run")
    args = parser.parse_args()

    force_system = os.environ.get("PRE_COMMIT_LOCAL_NO_VENV") == "1"
    python = resolve_python(force_system=force_system)
    if python is None:
        print(
            "ERROR: no python interpreter found; create the project virtualenv "
            "(python -m venv .venv) or make python3 available on PATH",
            file=sys.stderr,
        )
        return 1

    requirement, argv = _HOOKS[args.hook]
    if not dependency_available(python, requirement):
        name = requirement.removeprefix("exec:")
        print(
            f"ERROR: pre-commit hook '{args.hook}' needs '{name}' which is not "
            f"available on {python}; install dev dependencies "
            "(pip install -e '.[dev]') or create the project virtualenv",
            file=sys.stderr,
        )
        return 1

    return subprocess.run([python, *argv], cwd=_ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
