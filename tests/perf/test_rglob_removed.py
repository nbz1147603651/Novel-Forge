"""P0-1: Assert no ``rglob("*")`` remains in ``novel_forge/workspace/projects.py``.

The recursive ``root.rglob("*")`` walk in ``_iter_project_file_mtimes`` was the
primary source of UI jank on the Projects page — every project detail build
triggered a full recursive tree walk, and it was called *twice* per project
(once for ``_recent_project_files``, once for ``_latest_file_mtime``).

After the refactor, ``_iter_project_file_mtimes`` should no longer use
``rglob("*")``.  Instead, callers pass a pre-computed file-mtime list so the
tree is walked at most once per ``_build_project_detail`` /
``_build_degraded_detail`` call.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROJECTS_PY = Path(__file__).resolve().parents[2] / "novel_forge" / "workspace" / "projects.py"

# Matches ``.rglob("*")`` or ``.rglob('*')`` — the unbounded recursive walk.
_RGLOB_STAR_RE = re.compile(r"""\.rglob\(\s*["']\*["']\s*\)""")


def test_no_rglob_star_in_projects_py() -> None:
    """``projects.py`` must not contain any ``rglob("*")`` calls.

    ``_count_files`` uses ``rglob(pattern)`` with a *bounded* glob pattern
    (e.g. ``"*.md"``) — that is acceptable and NOT flagged by this test.
    Only the unbounded ``rglob("*")`` walk is prohibited.
    """
    assert _PROJECTS_PY.exists(), f"Source file not found: {_PROJECTS_PY}"
    source = _PROJECTS_PY.read_text(encoding="utf-8")
    matches = _RGLOB_STAR_RE.findall(source)
    assert not matches, (
        f"Found {len(matches)} rglob('*') call(s) in projects.py — "
        "the unbounded recursive walk must be removed (P0-1)."
    )


def test_iter_project_file_mtimes_accepts_precomputed() -> None:
    """``_iter_project_file_mtimes`` must accept an optional pre-computed list.

    After the refactor the function signature should be:
        _iter_project_file_mtimes(
            root: Path,
            *,
            precomputed: list[tuple[Path, float]] | None = None,
        ) -> list[tuple[Path, float]]

    This lets ``_build_project_detail`` scan once and share the result
    between ``_recent_project_files`` and ``_latest_file_mtime``.
    """
    source = _PROJECTS_PY.read_text(encoding="utf-8")
    # The function must accept a ``precomputed`` keyword parameter.
    assert "precomputed" in source, (
        "_iter_project_file_mtimes must accept a 'precomputed' keyword "
        "parameter to avoid duplicate tree walks."
    )
