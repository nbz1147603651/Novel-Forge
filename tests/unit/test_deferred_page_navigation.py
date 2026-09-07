"""Regression tests for cold-page navigation and activation semantics."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.desktop.window.chapter_focus import ChapterFocusMixin
from novel_forge.desktop.window.navigation import NavigationMixin
from novel_forge.desktop.window.navigation_handlers import NavigationHandlersMixin


def test_focus_chapter_studio_uses_deep_link_without_loading_page() -> None:
    switched: list[str] = []
    chapter_targets: list[tuple[str, int]] = []
    owner = SimpleNamespace(
        _chapter_studio_project_id="",
        _clamp_chapter_studio_target=lambda project_id, chapter: (project_id.strip(), chapter),
        _set_chapter_number=lambda project_id, chapter: chapter_targets.append(
            (project_id, chapter)
        ),
        switch_page=switched.append,
    )

    ChapterFocusMixin._focus_chapter_studio(owner, " demo ", 7)

    assert chapter_targets == [("demo", 7)]
    assert switched == ["chapter_studio:demo:7"]
    assert owner._chapter_studio_project_id == "demo"


def test_project_reader_uses_deep_link_without_eager_page_access() -> None:
    switched: list[str] = []
    owner = SimpleNamespace(switch_page=switched.append)

    NavigationHandlersMixin._view_project_in_reader(owner, "demo", "blueprint")

    assert switched == ["projects:project:demo:blueprint"]


def test_project_reader_deep_link_loads_then_focuses() -> None:
    calls: list[tuple[str, str]] = []

    class _ProjectsPage:
        def load_project(self, project_id: str) -> None:
            calls.append(("load", project_id))

        def focus_relationship_tracking_tab(self) -> None:
            calls.append(("focus", "relationships"))

    owner = SimpleNamespace(_pages={"projects": _ProjectsPage()})

    NavigationMixin._apply_switch_focus_tab(
        owner,
        "projects",
        "project:demo:relationships",
    )

    assert calls == [("load", "demo"), ("focus", "relationships")]


def test_deferred_activation_does_not_force_synchronous_full_build() -> None:
    calls: list[str] = []

    class _Page:
        def schedule_deferred_build(self) -> None:
            calls.append("schedule")

        def ensure_all_deferred_sections_built(self) -> None:
            calls.append("ensure_all")

        def activate(self) -> None:
            calls.append("activate")

    owner = SimpleNamespace(
        _page_activate_generation=3,
        _active_page_id="settings",
        _DEFERRED_PAGE_ACTIVATE_MS=420,
        _safe_deferred=lambda _delay, callback: callback(),
    )

    NavigationMixin._schedule_page_activation(owner, "settings", _Page())

    assert calls == ["schedule", "activate"]
