"""Regression test: ensure setHtml() calls stay within the allowed budget.

After the incremental-renderer migration, production code must route every
rich-text update through ``IncrementalDocumentRenderer.update_content()``.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NOVEL_FORGE_DIR = _REPO_ROOT / "novel_forge"

_ALLOWED_FILES: set[str] = set()

# Maximum number of setHtml() calls allowed in production code (excluding tests).
_MAX_SET_HTML = 0


def _find_setHtml_calls() -> list[tuple[str, int, str]]:
    """Return list of (filepath, lineno, line_text) for .setHtml( calls."""
    results: list[tuple[str, int, str]] = []
    pattern = re.compile(r"\.setHtml\(")
    for py_file in sorted(_NOVEL_FORGE_DIR.rglob("*.py")):
        # Skip test files
        if "test_" in py_file.name:
            continue
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno_0, line in enumerate(lines):
            if pattern.search(line):
                results.append((str(py_file), lineno_0 + 1, line.strip()))
    return results


class TestNoSetHtmlRegression:
    """Guard against accidental reintroduction of setHtml() calls."""

    def test_setHtml_count_within_budget(self) -> None:
        """Total setHtml() calls in novel_forge/ must be <= _MAX_SET_HTML."""
        calls = _find_setHtml_calls()
        assert len(calls) <= _MAX_SET_HTML, (
            f"Found {len(calls)} setHtml() calls in production code "
            f"(limit is {_MAX_SET_HTML}). Each call must use "
            f"IncrementalDocumentRenderer.update_content() instead.\n"
            + "\n".join(f"  {f}:{n}: {t}" for f, n, t in calls)
        )

    def test_setHtml_only_in_allowed_files(self) -> None:
        """Every remaining setHtml() must be in an allowed file."""
        calls = _find_setHtml_calls()
        disallowed = [
            (f, n, t)
            for f, n, t in calls
            if Path(f).name not in _ALLOWED_FILES
        ]
        assert not disallowed, (
            f"Found {len(disallowed)} setHtml() call(s) outside allowed files "
            f"{_ALLOWED_FILES}. Use IncrementalDocumentRenderer.update_content() "
            f"instead, or add the file to _ALLOWED_FILES with justification.\n"
            + "\n".join(f"  {f}:{n}: {t}" for f, n, t in disallowed)
        )

    def test_incremental_renderer_imports_smoke(self) -> None:
        """Verify all migrated modules can be imported without error."""
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if not isinstance(app, QApplication):
            app = QApplication([])
        app.setQuitOnLastWindowClosed(False)

        # These imports must succeed — they all use IncrementalDocumentRenderer
        from novel_forge.desktop.components import memory_components  # noqa: F401
        from novel_forge.desktop.pages import (
            outline_editor,  # noqa: F401
            workflow_presets,  # noqa: F401
        )
        from novel_forge.desktop.pages.document_renderer.incremental import (  # noqa: F401
            IncrementalDocumentRenderer,
        )
