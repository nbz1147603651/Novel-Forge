"""Test: _ui_session_path() resolves to a location outside storage_root.

This is a regression guard: the UI session file must never be stored inside
the project storage root, because that would cause the fs watcher to trigger
spurious refreshes every time the session is saved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


class TestSessionPathOutsideStorage:
    """UI session path must be outside any project's storage_root."""

    def test_session_path_is_under_home(self) -> None:
        """Session path must be under the user's home directory."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_path = NovelForgeDesktopWindow._ui_session_path()
        home = Path.home()

        # Session path must be under home directory
        assert session_path.is_absolute(), "Session path must be absolute"
        try:
            session_path.relative_to(home)
        except ValueError:
            pytest.fail(
                f"Session path {session_path} is not under home directory {home}"
            )

    def test_session_path_not_under_storage_root(self, tmp_path: Path) -> None:
        """Session path must not be under any plausible storage_root."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_path = NovelForgeDesktopWindow._ui_session_path()

        # Typical storage roots that projects might use
        candidate_roots = [
            tmp_path,
            tmp_path / "data",
            Path.cwd() / "data",
            Path.home() / "data",
        ]

        for root in candidate_roots:
            try:
                session_path.relative_to(root)
                pytest.fail(
                    f"Session path {session_path} is under storage root {root}. "
                    "This would cause fs watcher feedback loops."
                )
            except ValueError:
                pass  # Good — not under this root

    def test_session_path_in_novel_forge_config_dir(self) -> None:
        """Session path should be in ~/.novel_forge/ (the app config dir)."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        session_path = NovelForgeDesktopWindow._ui_session_path()
        expected_dir = Path.home() / ".novel_forge"

        assert session_path.parent == expected_dir, (
            f"Session path parent {session_path.parent} != expected {expected_dir}"
        )
        assert session_path.name == "ui_session.json"
