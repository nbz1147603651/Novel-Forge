"""Repository test-suite hygiene guards."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_xfail_call(node: ast.Call) -> bool:
    return _is_xfail_reference(node.func)


def _is_xfail_reference(node: ast.AST) -> bool:
    name = _call_name(node)
    return name in {
        "pytest.xfail",
        "pytest.mark.xfail",
        "mark.xfail",
        "xfail",
    }


def _iter_python_tests() -> list[Path]:
    return sorted(
        path
        for path in _TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_tests_do_not_use_xfail_as_long_term_debt() -> None:
    offenders: set[str] = set()

    for path in _iter_python_tests():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_xfail_call(node):
                rel_path = path.relative_to(_REPO_ROOT)
                offenders.add(f"{rel_path}:{node.lineno}")
            elif isinstance(node, (ast.Attribute, ast.Name)) and _is_xfail_reference(node):
                rel_path = path.relative_to(_REPO_ROOT)
                offenders.add(f"{rel_path}:{node.lineno}")

    assert not offenders, (
        "Do not commit xfail-based test debt. Fix or delete obsolete tests instead: "
        + ", ".join(sorted(offenders))
    )
