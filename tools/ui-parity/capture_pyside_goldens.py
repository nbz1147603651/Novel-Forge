#!/usr/bin/env python3
"""Capture deterministic full-window PySide6 goldens for UI parity work.

The tool is intentionally test-only: it binds a fixed workspace snapshot to
the existing desktop window, freezes motion, and writes screenshots plus a
small manifest.  It never calls a model or writes project artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QDialog,
    QScrollArea,
    QTabWidget,
    QWidget,
)

# Direct execution starts with ``tools/ui-parity`` as ``sys.path[0]``.  Add the
# repository root explicitly so the test-layer fixture is stable from CI, the
# project root, or an arbitrary shell working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.desktop.ui_parity_fixtures import (  # noqa: E402
    build_visual_chapter_checkpoint_workspace_snapshot,
    build_visual_chapter_running_workspace_snapshot,
    build_visual_chapter_workspace_snapshot,
    build_visual_workspace_snapshot,
    deterministic_desktop_visual_runtime,
    visual_character_bible_payload,
)

DEFAULT_PAGES = ("dashboard", "projects", "workflow", "settings", "chapter_studio", "voice_studio")
DEFAULT_VIEWPORTS = ((1440, 900),)
SETTINGS_CONNECTION_STATES = ("idle", "checking", "success", "failure")
SETTINGS_SECTION_STATES = ("creative-temperature", "model-routing")
WORKFLOW_STATES = ("running",)
PROJECTS_STATES = ("reader", "character-relationships", "character-graph")
CHAPTER_STUDIO_STATES = ("prepared", "running", "checkpoint", "checkpoint-dialog")
VOICE_STUDIO_STATES = ("configured",)
VOICE_STUDIO_TABS = ("team", "script", "room", "post", "settings")
VOICE_STUDIO_TAB_INDEX = {tab: index for index, tab in enumerate(VOICE_STUDIO_TABS)}
PAGE_PARITY_FIXTURE = json.loads(
    (PROJECT_ROOT / "tools/ui-parity/fixtures/reader-workflow.json").read_text(encoding="utf-8")
)


def _capture_window(window: Any, destination: Path) -> tuple[int, int]:
    """Render the Qt content frame and return its actual pixel dimensions.

    ``QWidget.resize()`` receives a native-window size on macOS, while
    ``QWidget.render()`` emits its client/content frame.  Keeping both the
    requested viewport and this measured frame in the manifest prevents a
    title-bar or status-bar delta from being misdiagnosed as a React layout
    regression.
    """

    pixmap = QPixmap(window.size())
    pixmap.fill(window.palette().color(window.backgroundRole()))
    window.render(pixmap)
    # A free-floating checkpoint dialog remains parented to Chapter Studio but
    # Qt renders it in a separate native surface. ``window.render`` therefore
    # omits it even though a user sees it over the workspace. Composite only
    # visible descendant dialogs back into the deterministic client-frame
    # capture; this preserves the source geometry without asking macOS for
    # Screen Recording permission or changing production dialog ownership.
    descendant_dialogs = tuple(
        dialog
        for dialog in window.findChildren(QDialog)
        if dialog.isVisible() and isinstance(dialog, QWidget)
    )
    # Qt detaches a ``free_floating`` dialog from QObject child traversal even
    # though it has a visual parent. Capture setup registers those explicit
    # overlays on the root frame; ordinary pages leave the sequence empty.
    registered_dialogs = tuple(
        dialog
        for dialog in getattr(window, "_ui_parity_overlays", ())
        if isinstance(dialog, QWidget) and dialog.isVisible()
    )
    visible_dialogs: tuple[QWidget, ...] = ()
    for dialog in (*descendant_dialogs, *registered_dialogs):
        if all(existing is not dialog for existing in visible_dialogs):
            visible_dialogs += (dialog,)
    if visible_dialogs:
        painter = QPainter(pixmap)
        try:
            for dialog in visible_dialogs:
                position = dialog.mapTo(window, QPoint(0, 0))
                dialog.render(painter, position)
        finally:
            painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save screenshot: {destination}")
    return pixmap.width(), pixmap.height()


def _load_window(theme_id: str, width: int, height: int, fixture_root: Path) -> Any:
    from novel_forge.desktop.constants import DENSITY_RESIZE_DEBOUNCE_MS
    from novel_forge.desktop.theme.runtime import apply_desktop_theme
    from novel_forge.desktop.window import NovelForgeDesktopWindow

    with (
        patch.object(NovelForgeDesktopWindow, "refresh_workspace", autospec=True),
        # Voice Studio emits a project-selection signal while the fixture is
        # bound.  In the live application that signal opens Finder/Explorer;
        # a golden capture must never open external applications.
        patch.object(NovelForgeDesktopWindow, "_open_project_folder", autospec=True),
    ):
        window = NovelForgeDesktopWindow()
    # ``_apply_workspace_refresh`` normally starts a background reader for
    # Chapter Studio.  The fixture intentionally only models visible workspace
    # metadata, not on-disk chapter artifacts; skipping that reader keeps a
    # golden capture free of asynchronous errors and timing-dependent pixels.
    # The production method remains untouched.
    window._refresh_chapter_studio_context = lambda: None
    window.resize(width, height)
    window.show()

    # Let the desktop's cold-start refresh and density debounce complete
    # before binding the immutable fixture.  Binding first let the initial
    # background refresh race back in with an empty local workspace, producing
    # a source image whose rail/header disagreed with its own fixed snapshot.
    QTest.qWait(DENSITY_RESIZE_DEBOUNCE_MS + 40)

    snapshot = build_visual_workspace_snapshot(fixture_root)
    snapshot_hash, changed_sections, payload = window._compute_snapshot_hash_incremental(snapshot)
    window._refresh_in_progress = True
    window._refresh_force_current = True
    window._apply_workspace_refresh(
        snapshot,
        window._workspace,
        payload,
        dict(window._section_hash_cache),
        changed_sections,
        snapshot_hash,
    )
    # ``NovelForgeDesktopWindow`` records its preferred theme during cold
    # construction.  The deterministic QApplication may still carry an older
    # stylesheet with that same id, and ``apply_desktop_theme`` intentionally
    # fast-paths equal ids. Clear only the capture-local marker so the source
    # QSS and themed logo are reapplied before pixels are recorded.
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setProperty("_novel_forge_desktop_theme", None)
    apply_desktop_theme(theme_id, root=window)
    # The shell's two large gradients are cached custom-painted widgets rather
    # than QSS surfaces.  Production rebuilds them at next launch from the
    # configured theme; a multi-theme capture reuses one live window, so bind
    # these source-owned stops explicitly before recording each image.
    from novel_forge.desktop.theme.palettes import desktop_theme_token_overrides
    from novel_forge.desktop.tokens.colors import COLORS

    overrides = desktop_theme_token_overrides(theme_id)

    def token(name: str) -> str:
        return overrides.get(name, COLORS[name][0])

    window._workspace_root._gradient_stops = [
        (0.0, token("bg.workspace")),
        (0.5, token("bg.workspace.mid")),
        (1.0, token("bg.workspace.end")),
    ]
    window._workspace_root.invalidate_cache()
    window._side_rail._gradient_stops = [
        (0.0, token("bg.sidebar.start")),
        (0.45, token("bg.sidebar.mid")),
        (1.0, token("bg.sidebar.end")),
    ]
    window._side_rail.invalidate_cache()
    QTest.qWait(20)
    return window


def _apply_settings_connection_state(page: Any, state: str) -> None:
    """Freeze source status cards without starting a provider request."""

    ensure_grid_built = getattr(page, "ensure_status_grid_built", None)
    if callable(ensure_grid_built):
        ensure_grid_built()
        QTest.qWait(20)
    cards = tuple(getattr(page, "_status_cards", {}).values())
    if not cards:
        raise RuntimeError("Settings status cards were not built before connection-state capture")
    for card in cards:
        if state == "idle":
            card.set_pending("待检测", flash=False)
        elif state == "checking":
            card.set_pending("检测中…", flash=False)
        elif state == "success":
            card.set_background_result(True, "连通 38ms")
            card.set_capabilities(False, True)
        elif state == "failure":
            card.set_background_result(False, "本地演练失败")
            card.set_capabilities(False, False)
        else:
            raise ValueError(f"Unknown settings connection state: {state}")


def _apply_settings_section_state(page: Any, state: str) -> None:
    """Expand one source settings section and bring it into the viewport."""

    titles = {
        "creative-temperature": "创作火候 — 浮动与适用范围",
        "model-routing": "流程路由 — 为每个步骤选择模型与能力",
    }
    target_title = titles.get(state)
    if target_title is None:
        raise ValueError(f"Unknown settings section state: {state}")
    build_all = getattr(page, "ensure_all_deferred_sections_built", None)
    if callable(build_all):
        build_all()
        QTest.qWait(40)
    sections = tuple(getattr(page, "_deferred_sections", ()))
    section = next((candidate for candidate in sections if getattr(candidate, "_title_text", "") == target_title), None)
    if section is None:
        raise RuntimeError(f"Settings section {target_title!r} was not built before capture")
    # Keep the capture independent of the user's persisted QSettings.  The
    # public setter writes that persistence, while these local visual changes
    # deliberately do not.  Collapsing unrelated sections makes the target
    # viewport deterministic even if a prior desktop session left routing open.
    for candidate in sections:
        expanded = candidate is section
        candidate._toggle.setChecked(expanded)
        candidate._sync_toggle_visual_state(expanded)
        candidate._set_body_expanded(expanded, animated=False)
    QTest.qWait(20)
    if state == "model-routing":
        route_tabs = page.findChild(QTabWidget, "routingGroupTabs")
        if route_tabs is None:
            raise RuntimeError("Settings routing tabs were not built before capture")
        # The React parity fixture always opens the first canonical routing
        # group. Explicitly pin the source instead of inheriting a persisted
        # active tab from an operator's previous PySide session.
        route_tabs.setCurrentIndex(0)
        QTest.qWait(20)
    content = getattr(page, "widget", lambda: None)()
    scrollbar = getattr(page, "verticalScrollBar", lambda: None)()
    if content is not None and scrollbar is not None:
        section_top = section.mapTo(content, QPoint(0, 0)).y()
        scrollbar.setValue(max(0, section_top - 12))
    QTest.qWait(40)


def _apply_workflow_state(page: Any, state: str) -> None:
    """Bind a fixed visible job without starting a pipeline or provider call."""

    if state != "running":
        raise ValueError(f"Unknown workflow state: {state}")

    from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
    from novel_forge.desktop.pages.workflow.jobs import JobCard

    workflow = PAGE_PARITY_FIXTURE["workflow"]
    job = DesktopJobRecord(
        job_id=workflow["jobId"],
        kind="init_long",
        label=workflow["title"],
        project_id="test-long",
        status=DesktopJobState.RUNNING,
        created_at="2026-07-14T12:02:08+00:00",
        updated_at="2026-07-14T12:34:56+00:00",
        current_step=workflow["currentStep"],
        cumulative_tokens=workflow["cumulativeTokens"],
        cumulative_cost_usd=workflow["cumulativeCostUsd"],
    )
    # JobCard normally computes elapsed time from the real clock.  The source
    # golden must retain a stable text contract, so freeze that one display
    # helper while the fixed record is bound.
    with patch.object(JobCard, "_elapsed_text", return_value="31:48"):
        page.bind_jobs([job])


def _apply_projects_state(page: Any, state: str, fixture_root: Path) -> None:
    """Open one named long-project reader state without changing project data.

    ``ProjectsPage`` restores its last reader tab from the desktop UI session.
    A golden cannot inherit that local history: the reader fixture always
    captures the source's first foundation document group.
    """

    if state not in PROJECTS_STATES:
        raise ValueError(f"Unknown projects state: {state}")
    load_project = getattr(page, "load_project", None)
    current_project_id = getattr(page, "current_project_id", None)
    if not callable(load_project) or not callable(current_project_id):
        raise RuntimeError("Projects page does not expose the reader fixture contract")
    project_dir = fixture_root / "test-long"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "spec.json").write_text(
        json.dumps(PAGE_PARITY_FIXTURE["project"]["spec"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if state in {"character-relationships", "character-graph"}:
        # The regular workspace fixture intentionally contains only the
        # project index.  Add this source-owned character artifact solely in
        # the temporary visual fixture so the Projects reader renders its
        # real CharacterBibleEditor and graph instead of an absent-file hint.
        (project_dir / "character_bible.json").write_text(
            json.dumps(visual_character_bible_payload(), ensure_ascii=False),
            encoding="utf-8",
        )

    load_project("test-long")
    for _ in range(32):
        # The page builds nested document tabs during its own event-loop turns.
        # Waiting for the actual outer-tab widget is stable and avoids using a
        # time-only delay as the visual baseline contract.
        tabs = getattr(page, "_outer_tabs", None)
        if current_project_id() == "test-long" and tabs is not None:
            for index in range(tabs.count()):
                if tabs.tabText(index) != "基础设定":
                    continue
                tabs.setCurrentIndex(index)
                foundation_tabs = tabs.widget(index)
                if foundation_tabs is not None and hasattr(foundation_tabs, "setCurrentIndex"):
                    if state == "reader":
                        foundation_tabs.setCurrentIndex(0)
                        document_host = foundation_tabs.widget(0)
                        scroll_areas = (
                            [document_host]
                            if isinstance(document_host, QAbstractScrollArea)
                            else []
                        )
                        if document_host is not None:
                            scroll_areas.extend(
                                document_host.findChildren(QAbstractScrollArea)
                            )
                        for scroll_area in scroll_areas:
                            scroll_area.verticalScrollBar().setValue(0)
                            scroll_area.horizontalScrollBar().setValue(0)
                        QTest.qWait(20)
                        return
                    for inner_index in range(foundation_tabs.count()):
                        if foundation_tabs.tabText(inner_index) != "角色与实体":
                            continue
                        foundation_tabs.setCurrentIndex(inner_index)
                        editor = foundation_tabs.widget(inner_index)
                        switch_view = getattr(editor, "_switch_view", None)
                        if not callable(switch_view):
                            raise RuntimeError(
                                "Character reader did not expose its graph-view fixture contract"
                            )
                        switch_view("relationships" if state == "character-relationships" else "graph")
                        QTest.qWait(40)
                        return
                    raise RuntimeError("Character reader did not expose its character tab")
        QTest.qWait(16)
    raise RuntimeError("Projects reader did not finish building before capture")


def _apply_voice_studio_state(
    page: Any,
    state: str,
    fixture_root: Path,
    *,
    tab: str = "team",
) -> None:
    """Bind a deterministic configured cast without starting audio workers.

    The normal full-window fixture intentionally starts Voice Studio without a
    selected project.  That is useful for its empty/loading state, but it is
    not the source counterpart of the React client's configured cast.  Keep
    this projection in the visual test layer: it supplies only the existing
    page's in-memory project, cast and provider views, starts no TTS worker and
    writes no project artifact.
    """

    if state != "configured":
        raise ValueError(f"Unknown voice studio state: {state}")

    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.tts.schemas import (
        NarratorVoiceProfile,
        TTSProvider,
        VoiceCastEntry,
        VoiceCloneStatus,
        VoiceTeamContract,
    )

    layout = ProjectLayout(fixture_root / "test-long")
    layout.ensure_dirs()
    page.set_project("test-long", layout)
    # ``set_project`` schedules production artifact readers. This fixture owns
    # all displayed data below, so cancel only those deferred reads rather than
    # letting absent temporary files overwrite the frozen projection.
    page._project_load_timer.stop()
    page._chapter_combo_load_timer.stop()
    page._bible_characters = [
        {
            "character_id": "lin-zhu",
            "name": "林逐",
            "role": "protagonist",
            "age": "28",
            "personality": "克制、敏锐，对时间戳异常保持职业性的怀疑。",
        },
        {
            "character_id": "zhou-yan",
            "name": "周砚",
            "role": "supporting",
            "age": "34",
            "personality": "调查官，语速平稳，习惯把疑问压到句尾。",
        },
        {
            "character_id": "system-announcer",
            "name": "系统播报",
            "role": "minor",
            "personality": "城市记忆库的中性提示音，尚待分配。",
        },
    ]
    page._narrator_profile = NarratorVoiceProfile(
        voice_id="minimax-yue",
        provider=TTSProvider.MINIMAX,
        voice_source="designed",
        voice_type="沉静女声",
        voice_design_prompt="克制、近距离，保留雨声与磁带等感官锚点的停顿。",
        style_keywords=["克制", "近距离", "冷峻"],
    )
    page._voice_team = VoiceTeamContract(
        narrator_voice_id="minimax-yue",
        narrator_provider=TTSProvider.MINIMAX,
        default_provider=TTSProvider.MINIMAX,
        entries=[
            VoiceCastEntry(
                character_id="lin-zhu",
                character_name="林逐",
                voice_id="minimax-linzhu",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
                quality_score=0.93,
                voice_source="designed",
                voice_design_prompt="主视角记忆回收师，压低声线，句尾保持克制。",
            ),
            VoiceCastEntry(
                character_id="zhou-yan",
                character_name="周砚",
                voice_id="minimax-zhouyan",
                provider=TTSProvider.MINIMAX,
                clone_status=VoiceCloneStatus.READY,
                quality_score=0.88,
                voice_source="system",
            ),
        ],
    )
    page._system_voices = [
        {"voice_id": "minimax-yue", "name": "沉静女声 · Yue"},
        {"voice_id": "minimax-linzhu", "name": "克制男声 · Lin"},
        {"voice_id": "minimax-zhouyan", "name": "冷静男声 · Zhou"},
    ]
    page._update_character_list()
    page._update_avatars()
    page._character_list.setCurrentRow(0)
    page._on_provider_status(
        {
            "provider": "minimax",
            "capabilities": {
                "synthesis": True,
                "voice_clone": True,
                "voice_design": True,
                "system_voice_catalog": True,
                "local_reference_audio": True,
                "synthesis_features": ["emotion", "paralinguistic", "instruction_control"],
            },
        }
    )
    page._status_badge.setText("就绪")
    page._status_badge.set_tone("default")
    page._refresh_workflow_controls()
    # The source stores the last selected tab in QSettings. A visual fixture
    # must never inherit that unrelated host preference: pin a named tab only
    # after the configured projection has populated its team and provider
    # controls. This is the source equivalent of the React `voice_tab` URL
    # fixture, and it starts no worker or persistence operation.
    tab_index = VOICE_STUDIO_TAB_INDEX.get(tab)
    tab_bar = getattr(page, "_custom_tab_bar", None)
    if tab_index is None or tab_bar is None or tab_index >= tab_bar.count():
        raise RuntimeError(f"Voice Studio did not expose configured tab {tab!r}")
    tab_bar.setCurrentIndex(tab_index)
    QTest.qWait(40)
    if tab_bar.currentIndex() != tab_index:
        raise RuntimeError(f"Voice Studio did not select configured tab {tab!r}")
    if tab != "settings":
        return

    # The Voice Studio settings pane is intentionally stateful in production:
    # its collapsibles and scroll position are restored from QSettings.  A
    # visual source fixture must instead model the fresh-page defaults.  Do
    # this locally, without calling ``set_expanded`` (which would write a
    # persistent user preference), so a developer's prior session cannot turn
    # a named screenshot into a different viewport.
    from novel_forge.desktop.components.containers import CollapsibleSection

    settings_scroll = page.findChild(QScrollArea, "voiceStudioSettingsScroll")
    settings_content = settings_scroll.widget() if settings_scroll is not None else None
    if settings_content is None:
        raise RuntimeError("Voice Studio did not build its settings scroll area")
    platform_section = getattr(page, "_platform_settings_section", None)
    expected_open_titles = {
        "本机音频模型中心",
        "质量目标与模型计划",
        getattr(platform_section, "_title_text", ""),
    }
    sections = tuple(settings_content.findChildren(CollapsibleSection))
    if not sections:
        raise RuntimeError("Voice Studio settings did not expose collapsible sections")
    for section in sections:
        expanded = getattr(section, "_title_text", "") in expected_open_titles
        section._toggle.setChecked(expanded)
        section._sync_toggle_visual_state(expanded)
        section._set_body_expanded(expanded, animated=False)
    settings_scroll.verticalScrollBar().setValue(0)
    QTest.qWait(40)


def _apply_chapter_studio_state(page: Any, state: str) -> None:
    """Bind one populated, read-only chapter context for source comparison."""

    bind_studio = getattr(page, "bind_studio", None)
    if not callable(bind_studio):
        raise RuntimeError("Chapter Studio page does not expose the source-fixture contract")
    ensure_ready = getattr(page, "ensure_all_deferred_sections_built", None)
    if callable(ensure_ready):
        ensure_ready()
        QTest.qWait(20)

    if state == "prepared":
        snapshot = build_visual_chapter_workspace_snapshot()
    elif state == "running":
        snapshot = build_visual_chapter_running_workspace_snapshot()
    elif state in {"checkpoint", "checkpoint-dialog"}:
        snapshot = build_visual_chapter_checkpoint_workspace_snapshot()
    else:
        raise ValueError(f"Unknown chapter studio state: {state}")

    # ``bind_studio`` correctly rejects a stale snapshot whose project/chapter
    # no longer matches the selector.  During a test capture the normal
    # desktop context reader is deliberately disabled, so establish that
    # selector contract locally before binding the fixture.  This is only a
    # visible-state setup; no chapter request or pipeline work is started.
    project_combo = getattr(page, "_project_combo", None)
    chapter_spin = getattr(page, "_chapter_spin", None)
    if project_combo is None or chapter_spin is None:
        raise RuntimeError("Chapter Studio page does not expose its selector fixture contract")
    project_combo.blockSignals(True)
    chapter_spin.blockSignals(True)
    try:
        project_index = project_combo.findData(snapshot.project_id)
        if project_index < 0:
            project_index = project_combo.findText(snapshot.project_id)
        if project_index < 0:
            # The shell's initial storage scan may finish before this
            # test-local project is visible. Add only the deterministic
            # fixture row; production project discovery remains untouched.
            project_combo.addItem(snapshot.project_id, snapshot.project_id)
            project_index = project_combo.findData(snapshot.project_id)
        project_combo.setCurrentIndex(project_index)
        project_combo.setEditText(snapshot.project_id)
        chapter_spin.setMaximum(max(snapshot.total_chapters, snapshot.chapter_number))
        chapter_spin.setValue(snapshot.chapter_number)
    finally:
        chapter_spin.blockSignals(False)
        project_combo.blockSignals(False)
    request_timer = getattr(page, "_context_request_timer", None)
    if request_timer is not None:
        request_timer.stop()
    # A source capture owns the in-memory snapshot below.  Letting the page
    # schedule a real reader would race it back to the deliberately minimal
    # temporary project directory and turn a named prepared/running state into
    # the unrelated empty selector frame.
    page._request_context = lambda: None
    page.current_project_id = lambda: snapshot.project_id
    page.current_chapter_number = lambda: snapshot.chapter_number
    bind_studio(snapshot)
    if getattr(page, "_studio", None) is not snapshot:
        raise RuntimeError(
            "Chapter Studio rejected the deterministic fixture snapshot "
            f"(selected={page.current_project_id()!r}, "
            f"chapter={page.current_chapter_number()!r}, "
            f"expected={snapshot.project_id!r}/{snapshot.chapter_number})"
        )
    # The page schedules its job/inspector refresh to the next Qt turn.  The
    # fixture never starts that reader, but waiting through the local render
    # turn ensures the actual populated rail and context panels are present.
    QTest.qWait(40)
    if state == "checkpoint-dialog":
        checkpoint = snapshot.pending_checkpoint
        show_dialog = getattr(page, "_show_checkpoint_dialog", None)
        if checkpoint is None or not callable(show_dialog):
            raise RuntimeError("Chapter Studio did not expose the checkpoint dialog fixture contract")
        # The static checkpoint panel and its companion free-floating decision
        # dialog are distinct PySide6 states.  Open the latter only when it is
        # named explicitly, matching the user's "定位 AI 建议浮窗" action
        # rather than making every checkpoint screenshot modal.
        from novel_forge.desktop.widgets import build_checkpoint_summary_text

        show_dialog(checkpoint, build_checkpoint_summary_text(checkpoint))
        dialog = getattr(page, "_checkpoint_dialog", None)
        if dialog is None or not dialog.isVisible():
            raise RuntimeError("Chapter Studio did not show the deterministic checkpoint dialog")
        page.window()._ui_parity_overlays = (dialog,)
        QTest.qWait(40)
        return
    if state != "running":
        return

    from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState

    bind_jobs = getattr(page, "bind_jobs", None)
    if not callable(bind_jobs):
        raise RuntimeError("Chapter Studio page does not expose the running-job fixture contract")
    bind_jobs(
        [
            DesktopJobRecord(
                job_id="fixture-chapter-005-prepare",
                kind="prepare_chapter",
                label="第 5 章 · 档案室",
                project_id="test-long",
                status=DesktopJobState.RUNNING,
                created_at="2026-07-14T12:02:08+00:00",
                updated_at="2026-07-14T12:34:56+00:00",
                current_step="draft",
                events=[
                    DesktopJobEvent(
                        at="2026-07-14T12:34:56+00:00",
                        step="draft",
                        payload={"chapter_number": 5},
                    )
                ],
                cumulative_tokens=184_200,
                cumulative_cost_usd=0.327,
            )
        ]
    )
    QTest.qWait(40)


def _wait_for_page_ready(page: Any, page_id: str) -> None:
    """Wait for a deferred PySide6 surface before freezing a golden.

    Workflow deliberately stages its form, task-flow, and observation panels
    across short event-loop turns.  Capturing after a fixed delay can preserve
    the temporary ``正在准备机杼`` placeholder instead of the user-visible
    page. Settings likewise starts as a cold ``加载中…`` placeholder while
    it defers the hero and status-card grid. Other pages simply return
    immediately unless they expose the same explicit readiness contract.
    """

    is_ready = getattr(page, "is_ui_ready", None)
    if page_id not in {"workflow", "settings"} or not callable(is_ready):
        return
    for _ in range(32):
        if is_ready():
            return
        QTest.qWait(16)
    raise RuntimeError(f"{page_id} UI did not finish deferred construction before capture")


def capture_pages(
    *,
    output_dir: Path,
    fixture_root: Path,
    theme_id: str,
    width: int,
    height: int,
    pages: tuple[str, ...],
    settings_connection_state: str | None = None,
    settings_section_state: str | None = None,
    workflow_state: str | None = None,
    projects_state: str | None = None,
    chapter_studio_state: str | None = None,
    voice_studio_state: str | None = None,
    voice_studio_tab: str = "team",
) -> list[dict[str, object]]:
    """Capture named pages and return manifest entries for their files."""

    from PySide6.QtGui import QDesktopServices

    from novel_forge.desktop.theme.palettes import DESKTOP_THEMES

    if theme_id not in DESKTOP_THEMES:
        raise ValueError(f"Unknown desktop theme: {theme_id}")

    records: list[dict[str, object]] = []
    # Chapter Studio's project selector is populated from the storage root
    # before its deterministic in-memory snapshot is bound. Seed the same
    # minimal long-form fixture used by the React parity client so the source
    # page can select ``test-long`` without reaching a developer's real data.
    fixture_project_dir = fixture_root / "test-long"
    fixture_project_dir.mkdir(parents=True, exist_ok=True)
    fixture_spec_path = fixture_project_dir / "spec.json"
    if not fixture_spec_path.exists():
        fixture_spec_path.write_text(
            json.dumps(PAGE_PARITY_FIXTURE["project"]["spec"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    with deterministic_desktop_visual_runtime(), patch.object(
        QDesktopServices, "openUrl", return_value=True
    ):
        window = _load_window(theme_id, width, height, fixture_root)
        try:
            for page_id in pages:
                window._ui_parity_overlays = ()
                window.switch_page(page_id)
                window._bind_workspace_for_page(page_id, force=True)
                QTest.qWait(160)
                # UI-session restore may retain a page's old scroll offset.
                # A source golden must describe a named viewport/state rather
                # than an incidental position from a previous local run.
                page = window._pages.get(page_id)
                _wait_for_page_ready(page, page_id)
                if page_id == "settings":
                    # Settings receives the snapshot before its deferred hero
                    # exists on a cold switch. Rebind only after the visible
                    # cards are ready, otherwise the source fixture captures
                    # empty runtime cards that a user never sees after the
                    # next normal workspace refresh.
                    window._bind_workspace_for_page(page_id, force=True)
                    QTest.qWait(20)
                if page is not None and hasattr(page, "verticalScrollBar"):
                    page.verticalScrollBar().setValue(0)
                    QTest.qWait(20)
                state = "loaded"
                if page_id == "settings" and settings_connection_state is not None:
                    _apply_settings_connection_state(page, settings_connection_state)
                    QTest.qWait(20)
                    state = f"connection-{settings_connection_state}"
                if page_id == "settings" and settings_section_state is not None:
                    _apply_settings_section_state(page, settings_section_state)
                    state = f"section-{settings_section_state}"
                if page_id == "workflow" and workflow_state is not None:
                    _apply_workflow_state(page, workflow_state)
                    QTest.qWait(20)
                    state = f"workflow-{workflow_state}"
                if page_id == "projects" and projects_state is not None:
                    _apply_projects_state(page, projects_state, fixture_root)
                    QTest.qWait(60)
                    project_scroll_areas = (
                        [page] if isinstance(page, QAbstractScrollArea) else []
                    )
                    project_scroll_areas.extend(page.findChildren(QAbstractScrollArea))
                    for scroll_area in project_scroll_areas:
                        scroll_area.verticalScrollBar().setValue(0)
                        scroll_area.horizontalScrollBar().setValue(0)
                    QTest.qWait(20)
                    state = f"projects-{projects_state}"
                if page_id == "chapter_studio" and chapter_studio_state is not None:
                    _apply_chapter_studio_state(page, chapter_studio_state)
                    state = f"chapter-{chapter_studio_state}"
                if page_id == "voice_studio" and voice_studio_state is not None:
                    _apply_voice_studio_state(
                        page,
                        voice_studio_state,
                        fixture_root,
                        tab=voice_studio_tab,
                    )
                    QTest.qWait(20)
                    state = f"voice-{voice_studio_state}-{voice_studio_tab}"
                file_name = f"{page_id}--{theme_id}--{state}--{width}x{height}.png"
                destination = output_dir / file_name
                frame_width, frame_height = _capture_window(window, destination)
                records.append(
                    {
                        "page": page_id,
                        "state": state,
                        "theme": theme_id,
                        "viewport": {"width": width, "height": height},
                        "frame": {"width": frame_width, "height": frame_height},
                        "file": file_name,
                        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    }
                )
        finally:
            window._pre_close_cleanup()
            window.close()
            window.deleteLater()
    return records


def _parse_viewport(value: str) -> tuple[int, int]:
    """Parse a capture viewport in the documented ``WIDTHxHEIGHT`` form."""

    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Viewports use WIDTHxHEIGHT, for example 1440x900") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Viewport width and height must be positive")
    return width, height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/ui-parity/goldens/pyside"))
    parser.add_argument("--fixture-root", type=Path, default=Path("/tmp/nimo_ui_parity_fixture"))
    parser.add_argument(
        "--theme",
        action="append",
        dest="themes",
        help="Theme id to capture. Repeat to produce a theme matrix (default: narrative_ember).",
    )
    parser.add_argument(
        "--viewport",
        action="append",
        type=_parse_viewport,
        dest="viewports",
        help="Viewport as WIDTHxHEIGHT. Repeat to produce a size matrix (default: 1440x900).",
    )
    # Kept for backward compatibility with the first capture script release.
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument(
        "--pages",
        default=",".join(DEFAULT_PAGES),
        help="Comma-separated registry page ids (default: every primary page).",
    )
    parser.add_argument(
        "--settings-connection-state",
        action="append",
        choices=SETTINGS_CONNECTION_STATES,
        dest="settings_connection_states",
        help="Capture a fixed settings status-card state. Repeat only with --pages settings.",
    )
    parser.add_argument(
        "--settings-section-state",
        choices=SETTINGS_SECTION_STATES,
        help="Capture a fixed expanded settings section. Requires --pages settings.",
    )
    parser.add_argument(
        "--workflow-state",
        choices=WORKFLOW_STATES,
        help="Capture a fixed workflow task state. Requires --pages workflow.",
    )
    parser.add_argument(
        "--projects-state",
        choices=PROJECTS_STATES,
        help="Capture a fixed project-reader state. Requires --pages projects.",
    )
    parser.add_argument(
        "--chapter-studio-state",
        action="append",
        choices=CHAPTER_STUDIO_STATES,
        dest="chapter_studio_states",
        help="Capture a populated Chapter Studio state. Requires --pages chapter_studio.",
    )
    parser.add_argument(
        "--voice-studio-state",
        choices=VOICE_STUDIO_STATES,
        help="Capture a configured Voice Studio state. Requires --pages voice_studio.",
    )
    parser.add_argument(
        "--voice-studio-tab",
        choices=VOICE_STUDIO_TABS,
        default="team",
        help="Pin the configured Voice Studio tab instead of inheriting QSettings (default: team).",
    )
    args = parser.parse_args()
    requested_pages = tuple(page.strip() for page in args.pages.split(",") if page.strip())
    unknown_pages = sorted(set(requested_pages) - set(DEFAULT_PAGES))
    if unknown_pages:
        parser.error(f"Unsupported primary page ids: {', '.join(unknown_pages)}")
    if args.settings_connection_states is not None and requested_pages != ("settings",):
        parser.error("--settings-connection-state requires --pages settings")
    if args.settings_section_state is not None and requested_pages != ("settings",):
        parser.error("--settings-section-state requires --pages settings")
    if args.workflow_state is not None and requested_pages != ("workflow",):
        parser.error("--workflow-state requires --pages workflow")
    if args.projects_state is not None and requested_pages != ("projects",):
        parser.error("--projects-state requires --pages projects")
    if args.chapter_studio_states is not None and requested_pages != ("chapter_studio",):
        parser.error("--chapter-studio-state requires --pages chapter_studio")
    if args.voice_studio_state is not None and requested_pages != ("voice_studio",):
        parser.error("--voice-studio-state requires --pages voice_studio")

    if (args.width is None) != (args.height is None):
        parser.error("--width and --height must be provided together")
    if args.viewports is not None and args.width is not None:
        parser.error("Use either --viewport or --width/--height, not both")
    themes = tuple(args.themes or ("narrative_ember",))
    if args.viewports is not None:
        viewports = tuple(args.viewports)
    elif args.width is not None:
        viewports = ((args.width, args.height),)
    else:
        viewports = DEFAULT_VIEWPORTS
    connection_states = tuple(args.settings_connection_states or (None,))
    projects_states = (args.projects_state,)
    chapter_studio_states = tuple(args.chapter_studio_states or (None,))
    records: list[dict[str, object]] = []
    for theme_id in themes:
        for width, height in viewports:
            for connection_state in connection_states:
                for projects_state in projects_states:
                    for chapter_studio_state in chapter_studio_states:
                        records.extend(
                            capture_pages(
                                output_dir=args.output_dir,
                                fixture_root=args.fixture_root,
                                theme_id=theme_id,
                                width=width,
                                height=height,
                                pages=requested_pages,
                                settings_connection_state=connection_state,
                                settings_section_state=args.settings_section_state,
                                workflow_state=args.workflow_state,
                                projects_state=projects_state,
                                chapter_studio_state=chapter_studio_state,
                                voice_studio_state=args.voice_studio_state,
                                voice_studio_tab=args.voice_studio_tab,
                            )
                        )
    manifest = {
        "source": "PySide6 NovelForgeDesktopWindow",
        "fixture": [
            "tests.desktop.ui_parity_fixtures.build_visual_workspace_snapshot",
            "tests.desktop.ui_parity_fixtures.build_visual_chapter_*_workspace_snapshot",
        ],
        "themes": themes,
        "viewports": [{"width": width, "height": height} for width, height in viewports],
        "settings_connection_states": args.settings_connection_states or [],
        "settings_section_state": args.settings_section_state,
        "workflow_state": args.workflow_state,
        "projects_state": args.projects_state,
        "chapter_studio_states": args.chapter_studio_states or [],
        "voice_studio_state": args.voice_studio_state,
        "voice_studio_tab": args.voice_studio_tab,
        "records": records,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
