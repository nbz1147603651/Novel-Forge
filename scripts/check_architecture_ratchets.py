#!/usr/bin/env python3
"""Prevent reintroduction of retired architecture mechanisms."""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WATCHED_DIRECTORIES = (
    Path("novel_forge/model_runtime"),
    Path("novel_forge/pipeline/long/services/init"),
    Path("novel_forge/workspace/book_ops"),
)
WATCHED_FILES = (
    Path("novel_forge/pipeline/long/stages/chapter_repair_support.py"),
    Path("novel_forge/pipeline/long/stages/continuity_repair.py"),
    Path("novel_forge/pipeline/long/stages/causal_repair.py"),
    Path("novel_forge/pipeline/long/stages/quality_checks.py"),
    Path("novel_forge/pipeline/long/stages/quality_checks_common.py"),
    Path("novel_forge/pipeline/long/stages/quality_checks_lib.py"),
    Path("novel_forge/pipeline/long/stages/quality_checks_runner.py"),
    Path("novel_forge/pipeline/long/stages/finalize.py"),
    Path("novel_forge/pipeline/long/stages/finalize_common.py"),
    Path("novel_forge/pipeline/long/stages/finalize_persist.py"),
    Path("novel_forge/pipeline/long/stages/finalize_report.py"),
)

# These five imports predate the ratchet. They remain explicit debt rather than
# a reason to add more wildcard facades during conservative cleanup.
ALLOWED_STAR_IMPORTS = frozenset(
    {
        (
            Path("novel_forge/pipeline/long/stages/finalize_persist.py"),
            "novel_forge.pipeline.long.stages.finalize_common",
        ),
        (
            Path("novel_forge/pipeline/long/stages/finalize_report.py"),
            "novel_forge.pipeline.long.stages.finalize_common",
        ),
        (
            Path("novel_forge/pipeline/long/stages/quality_checks_lib.py"),
            "novel_forge.pipeline.long.stages.quality_checks_common",
        ),
        (
            Path("novel_forge/pipeline/long/stages/quality_checks_runner.py"),
            "novel_forge.pipeline.long.stages.quality_checks_common",
        ),
        (
            Path("novel_forge/pipeline/long/stages/quality_checks_runner.py"),
            "novel_forge.pipeline.long.stages.quality_checks_lib",
        ),
    }
)


def _watched_python_files() -> list[Path]:
    files = {ROOT / path for path in WATCHED_FILES}
    for directory in WATCHED_DIRECTORIES:
        files.update((ROOT / directory).rglob("*.py"))
    return sorted(path for path in files if path.exists())


def _is_globals_update(node: ast.Call) -> bool:
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "update"
        and isinstance(function.value, ast.Call)
        and isinstance(function.value.func, ast.Name)
        and function.value.func.id == "globals"
    )


def _is_module_dict_update(node: ast.Call) -> bool:
    function = node.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "update"
        and isinstance(function.value, ast.Attribute)
        and function.value.attr == "__dict__"
    )


def _is_module_class_target(node: ast.expr) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "__class__":
        return False
    owner = node.value
    return (
        isinstance(owner, ast.Subscript)
        and isinstance(owner.value, ast.Attribute)
        and isinstance(owner.value.value, ast.Name)
        and owner.value.value.id == "sys"
        and owner.value.attr == "modules"
    )


def scan_source_mechanisms() -> list[str]:
    violations: list[str] = []
    for path in _watched_python_files():
        relative = path.relative_to(ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                identity = (relative, node.module or "")
                if identity not in ALLOWED_STAR_IMPORTS:
                    violations.append(f"{relative}:{node.lineno}: new wildcard import")
            elif isinstance(node, ast.Call) and (
                _is_globals_update(node) or _is_module_dict_update(node)
            ):
                violations.append(f"{relative}:{node.lineno}: dynamic namespace update")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(_is_module_class_target(target) for target in targets):
                    violations.append(f"{relative}:{node.lineno}: dynamic module facade")
    return violations


def scan_import_linter_ignores() -> list[str]:
    baseline_path = ROOT / "architecture/import_linter_ignore_baseline.txt"
    baseline = {
        line.strip()
        for line in baseline_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    contracts = config.get("tool", {}).get("importlinter", {}).get("contracts", [])
    current = {
        str(item).strip()
        for contract in contracts
        for item in contract.get("ignore_imports", [])
        if str(item).strip()
    }
    return [f"new import-linter exception: {item}" for item in sorted(current - baseline)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="retained for CI command symmetry")
    parser.parse_args()
    violations = [*scan_source_mechanisms(), *scan_import_linter_ignores()]
    if violations:
        print("Architecture ratchet violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Architecture ratchets passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
