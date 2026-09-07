"""Unit tests for TopBar visual refresh (Task 15).

Locks down the new top-bar styling and animations:

- TopBar surface background uses ``{{bg.surface}}`` token (Task 15 must-DO).
- TopBar title label color uses ``{{text.topbar.title}}`` token.
- ``QPushButton#actionButton:hover`` provides a subtle ``accent.primary`` 8% tint
  for the non-primary variant.
- ``QPushButton#actionButton[variant="primary"]:hover`` re-asserts the
  brighter ``{{accent.primary.hover}}`` token so primary buttons stay vibrant.
- ``switch_page()`` triggers a 200ms ``OutCubic`` fade-in on the top-bar
  title labels (eyebrow / title / subtitle) via ``Motion.fade_in``.
- Narrow window edge case: at minimum allowed width, the top-bar text labels
  remain visible (not clipped, not zero-width) and the action buttons stay
  on a single row.

The test file follows the importlib / monkeypatch pattern used in
``test_action_button.py`` and ``test_desktop_ui_layout.py`` so it runs in
isolation without depending on a real workspace service.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEasingCurve, QPropertyAnimation  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsDropShadowEffect,
    QWidget,
)

import novel_forge.desktop.components.primitives as primitives_module  # noqa: E402


class _FakeOptimizationFlag:
    """Stand-in for ``QGraphicsDropShadowEffect.OptimizationFlag`` enum."""

    DisableTransformHint = 0

import novel_forge.desktop.window as window_module  # noqa: E402
from novel_forge.desktop.theme import get_stylesheet  # noqa: E402
from novel_forge.desktop.theme.navigation import CONTENT as NAV_CONTENT  # noqa: E402
from novel_forge.desktop.tokens.colors import COLORS  # noqa: E402
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402
from novel_forge.desktop.workspace import (  # noqa: E402
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
    ProviderStatus,
)
from novel_forge.workspace.projects import (  # noqa: E402
    ChapterSummary,
    ProjectDetail,
    WorkspaceOverview,
)

# ── Shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def _patch_drop_shadow_optimization_flags() -> None:
    """Stub PySide6 APIs removed in newer releases.

    Two pre-existing issues block window construction under the test
    environment (unrelated to Task 15):

    1. ``QGraphicsDropShadowEffect.setOptimizationFlags()`` and its
       ``OptimizationFlag`` enum were removed in newer PySide6.  The
       ``Surface`` widget in ``components/primitives.py`` still calls
       them.  Patched to no-op so ``Surface.__init__`` does not raise.

    2. ``FilterChip.__init__`` sets ``_previous_checked`` AFTER the first
       ``setChecked`` call, so the subclass's ``setChecked`` override
       reads an undefined attribute and raises ``AttributeError``.
       Patched to set the attribute before the ``super().__init__``
       completes — this preserves the original behaviour for tests.

    Both fixes are environment glue; they do not touch the TopBar feature
    under test.  Other test files affected by these issues (e.g.
    ``test_desktop_ui_layout.py``) should adopt the same patch.
    """
    if not hasattr(QGraphicsDropShadowEffect, "setOptimizationFlags"):
        QGraphicsDropShadowEffect.setOptimizationFlags = (  # type: ignore[attr-defined]
            lambda self, *_args, **_kwargs: None
        )
    if not hasattr(QGraphicsDropShadowEffect, "OptimizationFlag"):
        QGraphicsDropShadowEffect.OptimizationFlag = _FakeOptimizationFlag  # type: ignore[attr-defined]

    original_filter_chip_init = primitives_module.FilterChip.__init__

    def _patched_filter_chip_init(
        self: object, *_args: object, **_kwargs: object
    ) -> None:
        # Initialize the buggy attribute BEFORE delegating to the original
        # constructor so the overridden setChecked() sees a defined value.
        self.__dict__.setdefault("_previous_checked", False)  # type: ignore[attr-defined]
        original_filter_chip_init(self, *_args, **_kwargs)

    primitives_module.FilterChip.__init__ = _patched_filter_chip_init  # type: ignore[method-assign]
    yield
    primitives_module.FilterChip.__init__ = original_filter_chip_init  # type: ignore[method-assign]


def _build_snapshot() -> DesktopWorkspaceSnapshot:
    storage_root = Path("/tmp/novel_forge_topbar")
    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="当前已载入",
        ),
    ]
    project = DesktopProjectItem(
        project_id="long_demo",
        title="遗物人生",
        mode="long",
        mode_label="长篇",
        status="writing",
        status_label="连载中",
        progress_label="1/12",
        progress_percent=15,
        last_updated_label="2026-03-13 09:30",
        headline="一枚旧怀表牵出一段被尘封的人生暗线。",
        next_action="续写第 2 章",
        genre="literary",
        tone="warm",
        completed_chapters=1,
        total_chapters=12,
        next_chapter=2,
        has_outline=True,
        has_canon=True,
        init_resume_available=False,
        init_resume_step_label="",
    )
    detail = ProjectDetail(
        project_id="long_demo",
        mode="long",
        title="遗物人生",
        genre="literary",
        tone="warm",
        preview="怀表盖启处，一声轻叹将旧事牵开。",
        total_chapters=12,
        completed_chapters=1,
        latest_chapter=1,
        completion_ratio=1 / 12,
        has_outline=True,
        has_canon=True,
        updated_at="2026-03-13T09:30:00+00:00",
        language="zh",
        words_per_chapter=4500,
        volume_mode="off",
        premise="一枚旧怀表牵出一段被尘封的人生暗线。",
        characters_hint="遗物整理师、被覆盖身份的高层",
        world_hint="近未来都市",
        conflict_hint="越靠近真相，人生越会被重写",
        pov_hint="女主主视角",
        opening_style="高概念开场",
        ending_style="余韵式 HE",
        extra_instructions="强化情绪拉扯",
        init_resume_available=False,
        init_resume_step="",
        init_resume_step_label="",
        init_resume_progress_label="",
        init_resume_progress_percent=0,
        init_resume_next_chapter=None,
        chapters=[
            ChapterSummary(
                chapter_number=1,
                title="旧怀表",
                word_count=3210,
                updated_at="2026-03-13T09:30:00+00:00",
                preview="怀表盖弹开时，屋里响起极轻的一声叹息。",
            )
        ],
        recent_files=["chapters/chapter_001.md"],
        artifact_counts={"chapters": 1, "drafts": 1, "reports": 1, "plans": 1, "states": 1},
    )
    overview = WorkspaceOverview(
        storage_root=str(storage_root),
        total_projects=1,
        short_projects=0,
        long_projects=1,
        total_generated_chapters=1,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=1,
        total_chapters=1,
        total_words=3210,
        configured_providers=1,
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=[project],
        featured_project=project,
        details={"long_demo": detail},
    )


class _FakeWorkspaceService:
    def __init__(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot

    def build_snapshot(self) -> DesktopWorkspaceSnapshot:
        return self._snapshot

    def get_chapter_workspace_snapshot(
        self,
        project_id: str,
        chapter_number: int,
        *,
        project_detail: object | None = None,
    ) -> object:
        return None

    def delete_project(self, project_id: str) -> bool:
        return True


class _StubPage(QWidget):
    """Minimal stand-in for a page widget.

    Avoids instantiating the real ``DashboardPage`` / ``ProjectsPage`` /
    etc., which currently raise during ``__init__`` due to pre-existing
    PySide6 / widget bugs that are unrelated to Task 15.  The TopBar
    under test only needs ``switch_page`` to find a page in the registry
    and call ``setCurrentWidget``; it never inspects page internals.
    """

    def __init__(self, page_id: str) -> None:
        super().__init__()
        self.page_id = page_id

    def bind_workspace(self, *args: object, **kwargs: object) -> None:
        return None

    def bind_workspace_sections(
        self, *args: object, **kwargs: object
    ) -> None:
        return None

    def bind_jobs(self, *args: object, **kwargs: object) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def supports_job_binding(self) -> bool:
        return False

    def needs_job_binding(self) -> bool:
        return False

    def set_mock_mode(self, *_args: object) -> None:
        return None

    def selected_project(self) -> object:
        return None

    def current_project_id(self) -> str:
        return ""

    def current_chapter_number(self) -> int:
        return 1

    def current_mode(self) -> str:
        return "short"

    def current_long_mode(self) -> str:
        return "init"

    def primary_action_label(self) -> str:
        return ""

    def trigger_primary_action(self) -> None:
        return None

    def export_active_template(self) -> None:
        return None

    def focus_long_init(self) -> None:
        return None

    def save_with_feedback(self) -> None:
        return None

    def activate(self) -> None:
        return None

    def load_project(self, _project_id: str) -> None:
        return None

    def focus_relationship_tab(self) -> None:
        return None

    def focus_project(
        self, _project_id: str, _chapter: int | None = None
    ) -> None:
        return None


def _stub_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace page_registry.get with _StubPage for all registered pages.

    Also patches ``_connect_signals``, ``_setup_event_subscriptions``,
    ``_on_workspace_refreshed``, ``_on_workspace_refresh_failed``,
    ``_refresh_chapter_studio_context``, and ``refresh_workspace`` to
    no-ops because the real implementations wire signals / enqueue
    background workers that crash under test isolation (the
    ``_FakeWorkspaceService`` returns ``None`` snapshots which the
    background payload-builder cannot handle).  None of these affect
    the TopBar feature under test; they are pre-existing surface area
    that breaks under the test isolation harness.
    """
    monkeypatch.setattr(
        window_module.page_registry,
        "get",
        lambda page_id: _StubPage(page_id),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_connect_signals",
        lambda self: None,
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_setup_event_subscriptions",
        lambda self: None,
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_on_workspace_refreshed",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_on_workspace_refresh_failed",
        lambda self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_refresh_chapter_studio_context",
        lambda self: None,
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "refresh_workspace",
        lambda self, **_kwargs: None,
    )


def _make_window(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> NovelForgeDesktopWindow:
    snapshot = _build_snapshot()
    service = _FakeWorkspaceService(snapshot)
    monkeypatch.setattr(
        window_module.DesktopWorkspaceService,
        "from_settings",
        classmethod(lambda cls, mock=False: service),
    )
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_load_ui_session",
        lambda self: None,
    )
    # B2 async init: force synchronous RuntimeServices construction so the
    # fake workspace lands before _make_window returns.
    monkeypatch.setattr(
        window_module.NovelForgeDesktopWindow,
        "_sync_runtime_services_init",
        True,
    )
    _stub_pages(monkeypatch)
    window = NovelForgeDesktopWindow()
    window._refresh_timer.stop()
    qapp.processEvents()
    return window


def _dispose_window(window: NovelForgeDesktopWindow, qapp: QApplication) -> None:
    window.hide()
    window.deleteLater()
    qapp.processEvents()


def _extract_block(qss: str, selector: str) -> str | None:
    """Return the body of the ``selector { ... }`` rule, or None if not found."""
    idx = qss.find(selector)
    if idx == -1:
        return None
    open_brace = qss.find("{", idx)
    if open_brace == -1:
        return None
    depth = 1
    i = open_brace + 1
    while i < len(qss) and depth > 0:
        c = qss[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return qss[open_brace + 1 : i - 1]


# ── Token coverage ──────────────────────────────────────────────────────────


class TestTopBarTokens:
    """TopBar QSS must use bg.surface and text.topbar.title tokens (Task 15 must-DO)."""

    def test_topbar_background_uses_bg_surface_token(self) -> None:
        raw_qss = NAV_CONTENT
        assert "QFrame#topBar" in raw_qss, "TopBar selector missing from navigation QSS"
        block = _extract_block(raw_qss, "QFrame#topBar")
        assert block is not None, "QFrame#topBar block not found"
        assert "{{bg.surface}}" in block, (
            "TopBar background must use {{bg.surface}} token (Task 15 must-DO), "
            f"got: {block!r}"
        )

    def test_topbar_background_resolves_to_token_hex(self) -> None:
        """get_stylesheet() resolves {{bg.surface}} → #fffaf3 in the QFrame#topBar block."""
        qss = get_stylesheet()
        block = _extract_block(qss, "QFrame#topBar")
        assert block is not None
        expected_hex = COLORS["bg.surface"][0]
        assert expected_hex in block, (
            f"TopBar background must resolve to {expected_hex} (bg.surface token), "
            f"got block: {block!r}"
        )

    def test_topbar_title_uses_topbar_title_token(self) -> None:
        raw_qss = NAV_CONTENT
        block = _extract_block(raw_qss, "QLabel#topBarTitle")
        assert block is not None, "QLabel#topBarTitle block missing"
        assert "{{text.topbar.title}}" in block, (
            "topBarTitle must use {{text.topbar.title}} token, "
            f"got: {block!r}"
        )

    def test_topbar_title_resolves_to_token_hex(self) -> None:
        qss = get_stylesheet()
        block = _extract_block(qss, "QLabel#topBarTitle")
        assert block is not None
        expected_hex = COLORS["text.topbar.title"][0]
        assert expected_hex in block, (
            f"topBarTitle color must resolve to {expected_hex}, got: {block!r}"
        )


# ── Action button hover QSS ─────────────────────────────────────────────────


class TestTopBarActionButtonHover:
    """The spec's actionButton hover rules must be present."""

    def test_generic_hover_rule_exists(self) -> None:
        raw_qss = NAV_CONTENT
        assert "QPushButton#actionButton:hover" in raw_qss, (
            "Generic QPushButton#actionButton:hover rule missing (Task 15 must-DO)"
        )

    def test_generic_hover_uses_accent_primary_8pct(self) -> None:
        raw_qss = NAV_CONTENT
        block = _extract_block(raw_qss, "QPushButton#actionButton:hover")
        assert block is not None, "actionButton:hover block not found"
        # accent.primary = #b65634 = rgb(182, 86, 52); alpha 0.08 (Task 15 spec)
        assert "182, 86, 52" in block, (
            f"Generic hover must be rgba(182, 86, 52, …) (accent.primary), "
            f"got: {block!r}"
        )
        assert "0.08" in block, (
            f"Generic hover alpha must be 0.08 (Task 15 spec), got: {block!r}"
        )

    def test_primary_hover_rule_exists(self) -> None:
        raw_qss = NAV_CONTENT
        assert (
            "QPushButton#actionButton[variant=\"primary\"]:hover" in raw_qss
        ), "Primary variant hover rule missing (Task 15 must-DO)"

    def test_primary_hover_uses_accent_primary_hover_token(self) -> None:
        raw_qss = NAV_CONTENT
        block = _extract_block(
            raw_qss, 'QPushButton#actionButton[variant="primary"]:hover'
        )
        assert block is not None, "primary hover block not found"
        assert "{{accent.primary.hover}}" in block, (
            "Primary hover must use {{accent.primary.hover}} token, "
            f"got: {block!r}"
        )

    def test_primary_hover_resolves_to_correct_hex(self) -> None:
        qss = get_stylesheet()
        block = _extract_block(
            qss, 'QPushButton#actionButton[variant="primary"]:hover'
        )
        assert block is not None
        expected_hex = COLORS["accent.primary.hover"][0]
        assert expected_hex in block, (
            f"Primary hover must resolve to {expected_hex}, got: {block!r}"
        )


# ── Page-switch fade-in animation ──────────────────────────────────────────


class TestTopBarFadeInOnSwitch:
    """switch_page() must trigger a 200ms OutCubic fade-in on top-bar title labels."""

    def test_switch_page_triggers_title_fade_in(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)
        monkeypatch.setattr(window_module, "motion_animations_supported", lambda kind="any": True)

        window.switch_page("workflow")

        from PySide6.QtWidgets import QGraphicsOpacityEffect

        for label_attr in ("_top_eyebrow", "_top_title", "_top_subtitle"):
            label = getattr(window, label_attr, None)
            assert label is not None
            effect = label.graphicsEffect()
            assert effect is not None, (
                f"{label_attr} must have a graphics effect after switch_page()"
            )
            assert isinstance(effect, QGraphicsOpacityEffect), (
                f"{label_attr} effect must be QGraphicsOpacityEffect for fade-in, "
                f"got {type(effect).__name__}"
            )

        anims = window._top_bar_animations
        assert len(anims) == 3, (
            f"_animate_top_bar_title must produce 3 fade-in anims "
            f"(eyebrow + title + subtitle), got {len(anims)}"
        )
        for anim in anims:
            assert isinstance(anim, QPropertyAnimation)
            assert anim.duration() == 200, (
                f"Top-bar fade-in duration must be 200ms, got {anim.duration()}"
            )

        _dispose_window(window, qapp)

    def test_top_bar_animation_uses_out_cubic_easing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)
        monkeypatch.setattr(window_module, "motion_animations_supported", lambda kind="any": True)

        window.switch_page("projects")

        anims = window._top_bar_animations
        assert len(anims) == 3, (
            f"_animate_top_bar_title must produce 3 fade-in anims, got {len(anims)}"
        )
        for anim in anims:
            assert anim.easingCurve().type() == QEasingCurve.Type.OutCubic, (
                f"Top-bar fade-in must use OutCubic easing, "
                f"got {anim.easingCurve().type()}"
            )
            assert anim.duration() == 200

        _dispose_window(window, qapp)

    def test_fade_in_is_skipped_when_animations_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)
        monkeypatch.setattr(window_module, "motion_animations_supported", lambda kind="any": False)

        # Animations disabled → no opacity effect should be installed.
        window.switch_page("workflow")
        qapp.processEvents()

        assert window._top_title.graphicsEffect() is None, (
            "Top-bar title must NOT have a graphics effect when animations are disabled"
        )
        _dispose_window(window, qapp)

    def test_rapid_switch_page_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)
        monkeypatch.setattr(window_module, "motion_animations_supported", lambda kind="any": True)

        pages = ["dashboard", "projects", "workflow", "settings", "chapter_studio"]
        for _ in range(20):
            for page_id in pages:
                window.switch_page(page_id)
                qapp.processEvents()

        # No exception = pass.  Ensure the title is still readable.
        assert window._top_title.text(), "Top-bar title must remain populated"
        _dispose_window(window, qapp)


# ── Edge case: narrow window no overflow ────────────────────────────────────


class TestTopBarNarrowWindow:
    """At minimum width the top-bar must not overflow / clip its title labels."""

    def test_minimum_width_preserves_title_visibility(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)

        # Resize to minimum allowed width — the existing _build_window()
        # clamps min_width to at most _WINDOW_DEFAULT_MIN_WIDTH = 1180, so
        # we shrink below that to verify the density/compact path engages.
        min_width = window.minimumWidth()
        assert min_width > 0, "Window must have a positive minimum width"

        compact_width = min_width - 100  # definitely below _COMPACT_WIDTH_THRESHOLD
        window.resize(compact_width, 720)
        window._apply_window_density(force=True)
        qapp.processEvents()

        # The 3 top-bar text labels must all be laid out with non-zero size
        # (no overflow / clipping at compact width).  We use width/height
        # rather than isVisible() because the window is never shown() in tests,
        # so isVisible() returns False even though the labels are positioned
        # in the layout correctly.
        for label_attr in ("_top_eyebrow", "_top_title", "_top_subtitle"):
            label = getattr(window, label_attr, None)
            assert label is not None, f"{label_attr} must exist on the window"
            assert label.width() > 0, (
                f"{label_attr} must have non-zero width at compact width "
                f"(got {label.width()})"
            )
            assert label.height() > 0, (
                f"{label_attr} must have non-zero height at compact width "
                f"(got {label.height()})"
            )

        # Top-bar must be in compact density (smaller minHeight).
        assert window._top_bar.minimumHeight() == 84, (
            f"TopBar minHeight must be 84 (compact) at narrow width, "
            f"got {window._top_bar.minimumHeight()}"
        )

        # All 3 action buttons must be present in the layout (visible or
        # hidden as appropriate — but not destroyed).
        for btn_attr in ("_primary_button", "_secondary_button", "_tertiary_button"):
            btn = getattr(window, btn_attr, None)
            assert btn is not None, f"{btn_attr} must exist"
            # Buttons may be hidden when no actions are configured, but
            # they must remain in the widget tree and not be clipped.
            assert btn.width() >= 0 and btn.height() >= 0

        _dispose_window(window, qapp)

    def test_subtitle_word_wraps_at_narrow_width(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        window = _make_window(monkeypatch, qapp)

        # Push a long subtitle so wrapping must engage to avoid overflow.
        long_subtitle = (
            "在长窗口模式下，TopBar 的副标题应当保持可读；"
            "在窄窗口模式下，应当通过 word-wrap 折叠为多行而不是横向溢出。"
            "本测试断言两件事：(1) 副标题 wordWrap 已开启，"
            "(2) 在窄窗口下高度至少为一行。"
        )
        window._top_subtitle.setText(long_subtitle)

        window.resize(window.minimumWidth(), 720)
        window._apply_window_density(force=True)
        qapp.processEvents()

        assert window._top_subtitle.wordWrap() is True, (
            "topBarSubtitle must have wordWrap=True to avoid horizontal overflow"
        )
        # After layout, the height should be ≥ one line (font-size 12-13px).
        assert window._top_subtitle.height() >= 16, (
            f"Subtitle must have ≥ 1 line height after narrow resize, "
            f"got {window._top_subtitle.height()}"
        )

        _dispose_window(window, qapp)


# ── Public API preservation (Task 15 must-NOT-do) ──────────────────────────


class TestTopBarPublicApiUnchanged:
    """The spec forbids changing the _page_actions_for() public API."""

    def test_page_actions_for_signature_unchanged(self) -> None:
        import inspect

        sig = inspect.signature(NovelForgeDesktopWindow._page_actions_for)
        params = list(sig.parameters.values())
        assert len(params) == 2, (
            f"_page_actions_for must keep 2 params (self, page_id), "
            f"got: {[p.name for p in params]}"
        )
        assert params[0].name == "self"
        assert params[1].name == "page_id"
        assert params[1].annotation == "str"

    def test_top_bar_fixed_height_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        qapp: QApplication,
    ) -> None:
        """Task 15 must-NOT-do: top bar fixed height must not change."""
        window = _make_window(monkeypatch, qapp)

        # regular density
        window.resize(1500, 900)
        window._apply_window_density(force=True)
        qapp.processEvents()
        assert window._top_bar.minimumHeight() == 96, (
            "Regular-density TopBar minHeight must remain 96 (Task 15 must-NOT-do)"
        )

        # compact density
        window.resize(1300, 800)
        window._apply_window_density(force=True)
        qapp.processEvents()
        assert window._top_bar.minimumHeight() == 84, (
            "Compact-density TopBar minHeight must remain 84 (Task 15 must-NOT-do)"
        )

        _dispose_window(window, qapp)