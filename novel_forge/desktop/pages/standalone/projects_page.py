"""卷帙 — Full-page document reader for viewing project documents at full size.

The reader occupies the entire main content area so rich widgets such as the
character graph and narrative blueprint timeline have room to render properly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QAbstractAnimation, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer
from novel_forge.desktop.components.skeleton import LoadingState
from novel_forge.desktop.document_presenter import (
    draft_display_name,
    read_artifact_text,
    smart_artifact_content,
)
from novel_forge.desktop.motion import Motion
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.pages.document_renderer_story_artifacts import render_outline
from novel_forge.desktop.pages.document_renderers import (
    render_chapter_prose,
    render_relationship_overview,
    smart_render_document,
)
from novel_forge.desktop.pages.standalone.character_bible_editor import CharacterBibleEditor
from novel_forge.desktop.pages.standalone.final_revision import FinalRevisionWidget
from novel_forge.desktop.pages.standalone.init_artifact_catalog import (
    INIT_COHERENCE_THEME_ARTIFACTS,
)
from novel_forge.desktop.pages.standalone.outline_editor import InteractiveOutlineWidget
from novel_forge.desktop.pages.standalone.renderer_html import connect_deep_links, parse_deep_link
from novel_forge.desktop.pages.standalone.token_analytics import TokenAnalyticsTab
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.widgets import ActionButton, clear_layout
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.projects import ProjectDetail

_logger = logging.getLogger(__name__)

# ── Artifact definitions ─────────────────────────────────────────

_SHORT_TABS: list[tuple[str, str]] = [
    ("正文", "chapters/short_story.md"),
    ("故事规格", "spec.json"),
    ("要素选择", "plans/blueprint_elements_selection.json"),
    ("节拍结构", "beats.json"),
    ("评估报告", "reports/eval_report.json"),
]

_LONG_GLOBAL_CATEGORIES: list[tuple[str, str]] = [
    ("故事规格", "spec.json"),
    ("世界观", "story_bible.json"),
    ("角色与实体", "character_bible.json"),
    ("要素与风格", "plans/blueprint_elements_selection.json"),
    ("叙事蓝图", "plans/narrative_blueprint.json"),
    ("章节设计矩阵", "plans/chapter_design_matrix.json"),
    ("章节大纲", "outline.json"),
]

_LONG_GLOBAL_GROUPS: list[tuple[str, tuple[tuple[str, str], ...]]] = [
    (
        "基础设定",
        (
            ("故事规格", "spec.json"),
            ("世界观", "story_bible.json"),
            ("角色与实体", "character_bible.json"),
            ("要素与风格", "plans/blueprint_elements_selection.json"),
        ),
    ),
    (
        "资料检索",
        (
            ("资料检索报告", "reports/init_web_research.json"),
            ("资料分析报告", "reports/init_research_dossier.json"),
            ("大纲资料校准", "reports/outline_research_grounding.json"),
        ),
    ),
]

_LONG_STANDALONE_TABS: list[tuple[str, str]] = [
    ("叙事蓝图", "plans/narrative_blueprint.json"),
    ("章节设计矩阵", "plans/chapter_design_matrix.json"),
    ("章节大纲", "outline.json"),
]

_OUTLINE_SESSION_REL_PATH = Path("states") / "outline_session.json"
_OUTLINE_BATCHES_REL_PATH = Path("states") / "outline_batches"

_BOOK_CONSISTENCY_REPORTS: list[tuple[str, str]] = [
    ("一致性审计", "reports/book_consistency_audit.json"),
    ("修复报告", "reports/book_consistency_repair_report.json"),
]

_CHAPTER_REPORT_TYPES: list[tuple[str, str]] = [
    ("质量评估", "reports/chapter_{ch}_eval.json"),
    ("创作总结", "reports/chapter_{ch}_creative.json"),
    ("对齐报告", "reports/chapter_{ch}_alignment.json"),
    ("连贯性", "reports/chapter_{ch}_continuity.json"),
    ("因果链", "reports/chapter_{ch}_causal.json"),
    ("追读力", "reports/chapter_{ch}_reading_power.json"),
    ("知识边界", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
    ("护栏", "reports/chapter_{ch}_guard.json"),
    ("质量门禁", "reports/chapter_{ch}_quality_gate.json"),
    ("控制", "reports/chapter_{ch}_stage_visibility.json"),
    ("表达", "reports/chapter_{ch}_expression_repetition.json"),
    ("拟人化", "reports/chapter_{ch}_humanize.json"),
    ("拟人化对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
    ("润色对比", "reports/revisions/chapter_{ch}_polish_chapter.json"),
]

_CHAPTER_REPORT_GROUPS: list[tuple[str, tuple[tuple[str, str], ...]]] = [
    (
        "质量",
        (
            ("质量评估", "reports/chapter_{ch}_eval.json"),
            ("质量门禁", "reports/chapter_{ch}_quality_gate.json"),
            ("护栏", "reports/chapter_{ch}_guard.json"),
            ("知识边界", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
        ),
    ),
    (
        "结构",
        (
            ("对齐报告", "reports/chapter_{ch}_alignment.json"),
            ("连贯性", "reports/chapter_{ch}_continuity.json"),
            ("因果链", "reports/chapter_{ch}_causal.json"),
            ("追读力", "reports/chapter_{ch}_reading_power.json"),
            ("控制", "reports/chapter_{ch}_stage_visibility.json"),
        ),
    ),
    (
        "表达",
        (
            ("表达", "reports/chapter_{ch}_expression_repetition.json"),
            ("拟人化", "reports/chapter_{ch}_humanize.json"),
            ("拟人化对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
            ("润色对比", "reports/revisions/chapter_{ch}_polish_chapter.json"),
        ),
    ),
    (
        "创作",
        (("创作总结", "reports/chapter_{ch}_creative.json"),),
    ),
]

_INIT_ADMISSION_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("初始化准入", "reports/init_readiness.json"),
    ("编辑契约准入", "reports/init_editorial_readiness.json"),
    ("初始化修复", "reports/init_artifact_repair.json"),
    ("创意精炼", "reports/init_creative_refinement.json"),
)

_INIT_COHERENCE_GOVERNANCE_ARTIFACTS: tuple[tuple[str, str], ...] = tuple(
    (label, rel_path)
    for label, rel_path in INIT_COHERENCE_THEME_ARTIFACTS
    if rel_path
    not in {
        "reports/init_readiness.json",
        "reports/init_artifact_repair.json",
    }
)

# ── Page-level stylesheet ────────────────────────────────────────


def _get_viewer_default_qss() -> str:
    """Return the viewer default QSS with theme-resolved colors."""
    from novel_forge.desktop.theme import resolve_qcolor

    _th = resolve_qcolor("text.tab.hover")
    _tm = resolve_qcolor("text.muted")
    _tp = resolve_qcolor("text.primary")
    _bi = resolve_qcolor("bg.input")
    _ap = resolve_qcolor("accent.primary")
    return (
        f"h1, h2, h3 {{ color: {_th.name()}; font-family: 'Songti SC', serif; }}\n"
        f"h3 {{ margin-top: 18px; margin-bottom: 6px; font-size: 14px; }}\n"
        f".kv-row {{ display: block; margin: 4px 0; }}\n"
        f".kv-label {{ display: inline-block; min-width: 100px; color: {_tm.name()}; font-weight: 500; }}\n"
        f".kv-value {{ color: {_tp.name()}; }}\n"
        f".hint-block {{ background: rgba({_bi.red()}, {_bi.green()}, {_bi.blue()}, 0.55); padding: 10px 14px; border-radius: 6px;\n"
        f"              border-left: 3px solid {_ap.name()}; margin: 8px 0; line-height: 1.7; color: {_th.name()}; }}\n"
        f".tag {{ display: inline-block; padding: 2px 8px; margin: 2px; background: rgba({_ap.red()}, {_ap.green()}, {_ap.blue()}, 0.10);\n"
        f"       color: {_ap.name()}; border-radius: 10px; font-size: 12px; }}\n"
        f".tag-row {{ margin: 6px 0; }}\n"
        f"details {{ background: rgba({_bi.red()}, {_bi.green()}, {_bi.blue()}, 0.45); padding: 8px 12px; border-radius: 6px; margin: 8px 0; }}\n"
        f"summary {{ cursor: pointer; color: {_th.name()}; font-weight: 500; }}\n"
        f".json-pre {{ background: rgba({_tp.red()}, {_tp.green()}, {_tp.blue()}, 0.04); padding: 8px; border-radius: 4px;\n"
        f"            font-family: Menlo, Consolas, monospace; font-size: 12px; }}\n"
        f".field-hint {{ color: {_tm.name()}; font-size: 11px; margin-top: 4px; }}\n"
    )


_LOG = logging.getLogger(__name__)


class ProjectsPage(QWidget):
    """Full-page project document reader occupying the main content area."""

    compose_requested = Signal(str, int)
    navigate_requested = Signal(str)
    context_changed = Signal()
    workspace_refresh_requested = Signal()
    ui_state_changed = Signal()

    # Files whose disk fingerprint changes should trigger a viewer rebuild.
    _KEY_FILES = (
        "spec.json",
        "story_bible.json",
        "character_bible.json",
        "outline.json",
    )
    _MTIME_SCAN_DIRS = (
        "plans",
        "reports",
        "states",
        "narrative_state",
    )
    _VIEWER_BUILD_DELAY_MS: int = 16

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("projectsPage")
        self._snapshot: DesktopWorkspaceSnapshot | None = None
        self._current_project_id: str | None = None
        self._outer_tabs: QTabWidget | None = None
        self._token_tab: TokenAnalyticsTab | None = None
        self._revision_widgets: list[FinalRevisionWidget] = []
        self._outline_widgets: list[InteractiveOutlineWidget] = []
        self._character_widgets: list[CharacterBibleEditor] = []
        self._revision_view_states: dict[tuple[str, int, str], dict[str, Any]] = {}
        # Generic lazy-tab registry: maps (id(tabs), index) -> (builder, label).
        # When a tab is first activated, its builder is invoked and the
        # placeholder swapped out.  This defers construction of heavy tabs
        # (character bible, outline, chapter reports, governance, etc.)
        # until the user actually opens them.
        self._lazy_tab_builders: dict[tuple[int, int], tuple[Any, str]] = {}
        # Outline widgets built lazily need post-insertion signal wiring
        # (their _wire_outline_widget call requires the tab widget + label).
        self._pending_outline_wire: list[tuple[Any, QTabWidget, str]] = []
        # Remember which chapter was selected so timer-driven rebuilds don't reset to ch1
        self._current_chapter_prose: int = 1
        self._current_chapter_report: int = 1
        # Fingerprint of the last built project state; used to skip no-op refreshes from
        # timer ticks without hiding real document/report updates from the reader.
        self._last_build_fingerprint: tuple[object, ...] = ()
        self._last_chapter_prose_fingerprint: tuple[object, ...] = ()
        # Last scanned fingerprints for key project files; used to trigger
        # viewer rebuilds when an external process (CLI/API) edits project
        # artifacts even if the workspace snapshot has not yet been refreshed.
        self._last_project_mtimes: dict[str, tuple[int, int]] = {}
        self._has_established_mtime_baseline: bool = False
        # Chapter-list wiring (Task 22): cached for ``_switch_chapter`` and the fade hook.
        self._active_chapter_list: QListWidget | None = None
        self._active_chapter_host: QVBoxLayout | None = None
        self._active_chapter_project_dir: Path | None = None
        self._active_chapter_layout: ProjectLayout | None = None
        self._active_chapter_outline: list[dict[str, Any]] = []
        self._pending_ui_state: dict[str, Any] | None = None
        # Active fade animations (Task 22): ``findChildren`` cannot reach
        # the parent-less ``QPropertyAnimation`` created by ``Motion.fade_in``.
        self._active_fades: list[QAbstractAnimation] = []
        self._is_shutting_down = False
        self._viewer_build_generation = 0
        self._pending_viewer_build: tuple[Any, ...] | None = None
        self._authoring_service: Any = None
        self._authoring_dialog: Any = None
        self._viewer_build_timer = QTimer(self)
        self._viewer_build_timer.setSingleShot(True)
        self._viewer_build_timer.timeout.connect(self._finish_pending_viewer_build)
        self._build_ui()
        self.context_changed.connect(self._refresh_authoring_button)

    def shutdown(self) -> None:
        """Stop timers, disconnect signals, and tear down sub-widgets on app exit.

        Thread-pool draining is handled globally by
        ``shutdown_desktop_thread_pools()`` in ``_pre_close_cleanup()``.
        """
        self._is_shutting_down = True
        self._viewer_build_timer.stop()
        self._pending_viewer_build = None
        if self._authoring_dialog is not None:
            self._authoring_dialog.shutdown()
            self._authoring_dialog.close()
        safe_disconnect(self.context_changed, self._refresh_authoring_button)
        safe_disconnect(self._project_combo.currentIndexChanged, self._on_combo_changed)

        if self._token_tab is not None:
            try:
                self._token_tab.shutdown()
            except (RuntimeError, TypeError, AttributeError):
                pass
        self._stop_active_fades()

    def export_ui_state(self) -> dict[str, Any]:
        """Serialize the reader position without serializing document widgets."""
        outer_tab = ""
        inner_tabs: dict[str, str] = {}
        if self._outer_tabs is not None and self._outer_tabs.currentIndex() >= 0:
            outer_tab = self._outer_tabs.tabText(self._outer_tabs.currentIndex())
            for index in range(self._outer_tabs.count()):
                child = self._outer_tabs.widget(index)
                if isinstance(child, QTabWidget) and child.currentIndex() >= 0:
                    inner_tabs[self._outer_tabs.tabText(index)] = child.tabText(child.currentIndex())
        return {
            "version": 1,
            "project_id": self._current_project_id or "",
            "outer_tab": outer_tab,
            "inner_tabs": inner_tabs,
            "chapter_prose": self._current_chapter_prose,
            "chapter_report": self._current_chapter_report,
        }

    def restore_ui_state(self, payload: object) -> None:
        """Stage reader state until a workspace snapshot supplies its documents."""
        if not isinstance(payload, dict):
            return
        project_id = str(payload.get("project_id") or "").strip()
        raw_inner_tabs = payload.get("inner_tabs")
        inner_tabs = (
            {str(key): str(value) for key, value in raw_inner_tabs.items()}
            if isinstance(raw_inner_tabs, dict)
            else {}
        )
        try:
            chapter_prose = max(1, int(payload.get("chapter_prose") or 1))
            chapter_report = max(1, int(payload.get("chapter_report") or 1))
        except (TypeError, ValueError):
            chapter_prose = chapter_report = 1
        self._current_project_id = project_id or None
        self._current_chapter_prose = chapter_prose
        self._current_chapter_report = chapter_report
        self._pending_ui_state = {
            "outer_tab": str(payload.get("outer_tab") or ""),
            "inner_tabs": inner_tabs,
        }
        if self._snapshot is not None and self._current_project_id:
            self._rebuild_viewer()

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        # ── Header ─────────────────────────────────────────────────
        header = QWidget()
        header.setObjectName("projectReaderHeader")
        header.setFixedHeight(42)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.setSpacing(10)

        self._project_combo = QComboBox()
        self._project_combo.setObjectName("projectSelector")
        self._project_combo.setProperty("readerHeader", "true")
        self._project_combo.setPlaceholderText("请选择项目…")
        self._project_combo.setFixedHeight(34)
        self._project_combo.currentIndexChanged.connect(self._on_combo_changed)
        hl.addWidget(self._project_combo)

        self._viewer_title = QLabel()
        self._viewer_title.setObjectName("viewerTitle")
        self._viewer_title.setProperty("readerHeader", "true")
        self._viewer_title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._viewer_title, 1)
        self._authoring_button = ActionButton("作者授权与确认")
        self._authoring_button.setVisible(False)
        self._authoring_button.clicked.connect(self._open_authoring_control)
        hl.addWidget(self._authoring_button)

        root.addWidget(header)

        sep = QFrame()
        sep.setObjectName("viewerSep")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Content host ───────────────────────────────────────────
        self._content_host = QWidget()
        self._content_layout = QVBoxLayout(self._content_host)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        root.addWidget(self._content_host, 1)

        self._show_empty_hint("请在案头选择一个项目，然后点击「阅卷」按钮来到此处。")

    # ── Data binding ─────────────────────────────────────────────

    def bind_authoring_service(self, service: Any) -> None:
        self._authoring_service = service
        self._refresh_authoring_button()

    def _refresh_authoring_button(self) -> None:
        from novel_forge.app_service.engine_views import engine_capabilities

        detail = (
            self._snapshot.details.get(self._current_project_id or "") if self._snapshot else None
        )
        configured = bool(
            self._snapshot
            and self._current_project_id
            and (
                self._snapshot.storage_root / self._current_project_id / "authoring_policy.json"
            ).is_file()
        )
        self._authoring_button.setVisible(
            bool(
                self._authoring_service
                and detail
                and detail.mode == "long"
                and (configured or engine_capabilities().features.get("authoring_coauthor", False))
            )
        )

    def _open_authoring_control(self) -> None:
        if not self._snapshot or not self._current_project_id or self._authoring_service is None:
            return
        from novel_forge.desktop.components.authoring_control import AuthoringControlDialog
        from novel_forge.persistence.filesystem import FileSystemStorage

        if self._authoring_dialog is not None and self._authoring_dialog.isVisible():
            self._authoring_dialog.raise_()
            return
        self._authoring_dialog = AuthoringControlDialog(
            FileSystemStorage(self._snapshot.storage_root),
            self._authoring_service,
            self._current_project_id,
            self._current_chapter_prose,
            self,
        )
        self._authoring_dialog.changed.connect(self.workspace_refresh_requested.emit)
        self._authoring_dialog.open()

    def bind_workspace(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        self._refresh_authoring_button()
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        for item in snapshot.projects:
            self._project_combo.addItem(item.title, item.project_id)
        if self._current_project_id:
            idx = self._project_combo.findData(self._current_project_id)
            if idx >= 0:
                self._project_combo.setCurrentIndex(idx)
            else:
                self._current_project_id = None
        self._project_combo.blockSignals(False)
        if self._current_project_id:
            self._rebuild_if_changed(
                force=self._active_project_files_changed(snapshot),
            )

    def bind_workspace_sections(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        sections: frozenset[str],
    ) -> None:
        """Incremental binding — only refresh the sections that changed.

        * ``projects`` — refresh the combo box (do NOT rebuild the viewer).
        * ``details`` — rebuild the viewer only when the *currently displayed*
          project has new detail data.  Changes to other projects are ignored.
        """
        if not sections:
            return

        detail_changed = False
        files_changed = False
        if "details" in sections and self._current_project_id:
            old_detail = (
                self._snapshot.details.get(self._current_project_id) if self._snapshot else None
            )
            new_detail = snapshot.details.get(self._current_project_id)
            if old_detail is not None and new_detail is not None:
                detail_changed = self._project_detail_fingerprint(
                    old_detail
                ) != self._project_detail_fingerprint(new_detail)
            elif new_detail is not None:
                detail_changed = True

            # Task 5: also rebuild if key project files were touched on disk
            # (e.g. external CLI workflow updated spec/story_bible/etc.) even
            # though the in-memory workspace snapshot may not yet have caught up.
            project_dir: Path | None = None
            storage_root = getattr(self._snapshot, "storage_root", None) if self._snapshot else None
            if storage_root is not None:
                project_dir = Path(storage_root) / self._current_project_id
            elif storage_root is None and snapshot is not None:
                # Fall back to the *new* snapshot's storage_root.
                project_dir = Path(snapshot.storage_root) / self._current_project_id
            if project_dir is not None:
                files_changed = self._project_mtimes_changed(project_dir)

        self._snapshot = snapshot

        self._refresh_authoring_button()

        if "projects" in sections:
            self._project_combo.blockSignals(True)
            self._project_combo.clear()
            for item in snapshot.projects:
                self._project_combo.addItem(item.title, item.project_id)
            if self._current_project_id:
                idx = self._project_combo.findData(self._current_project_id)
                if idx >= 0:
                    self._project_combo.setCurrentIndex(idx)
                else:
                    self._current_project_id = None
            self._project_combo.blockSignals(False)

        if detail_changed or files_changed:
            self._rebuild_if_changed(force=files_changed)

    def load_project(self, project_id: str) -> None:
        """Load a specific project into the reader (called by the main window)."""
        if (
            self._current_project_id
            and project_id != self._current_project_id
            and not self._confirm_embedded_editors_can_close()
        ):
            return
        if project_id == self._current_project_id and self.has_unsaved_changes():
            return
        self._current_project_id = project_id
        self._last_build_fingerprint = ()  # force full rebuild on explicit nav
        self._has_established_mtime_baseline = False
        if self._snapshot:
            idx = self._project_combo.findData(project_id)
            if idx >= 0:
                self._project_combo.blockSignals(True)
                self._project_combo.setCurrentIndex(idx)
                self._project_combo.blockSignals(False)
        self._rebuild_viewer()
        self.context_changed.emit()

    def current_project_id(self) -> str | None:
        """Return the project ID currently being viewed."""
        return self._current_project_id

    def focus_relationship_tab(self) -> None:
        """Switch the reader to the relationship tracking tab."""
        if self._outer_tabs is None:
            return
        for idx in range(self._outer_tabs.count()):
            if self._outer_tabs.tabText(idx) != "追踪":
                continue
            self._outer_tabs.setCurrentIndex(idx)
            tracking_tabs = self._outer_tabs.widget(idx)
            if isinstance(tracking_tabs, QTabWidget):
                for inner_idx in range(tracking_tabs.count()):
                    if tracking_tabs.tabText(inner_idx) == "关系追踪":
                        tracking_tabs.setCurrentIndex(inner_idx)
                        break
            break

    def focus_narrative_blueprint_tab(self) -> None:
        """Switch the reader to the 叙事蓝图 outer tab (Task 8 router)."""
        self._focus_outer_tab_by_text("叙事蓝图")

    def focus_relationship_tracking_tab(self) -> None:
        """Switch the reader to 追踪 outer + 关系追踪 inner (Task 8 router)."""
        self._focus_outer_tab_by_text("追踪")
        if self._outer_tabs is None:
            return
        for idx in range(self._outer_tabs.count()):
            if self._outer_tabs.tabText(idx) != "追踪":
                continue
            tracking_tabs = self._outer_tabs.widget(idx)
            if isinstance(tracking_tabs, QTabWidget):
                for j in range(tracking_tabs.count()):
                    if tracking_tabs.tabText(j) == "关系追踪":
                        tracking_tabs.setCurrentIndex(j)
                        return

    def focus_character_bible_tab(self) -> None:
        """Switch to 基础设定 outer + 角色与实体 inner (Task 8 router)."""
        self._focus_outer_tab_by_text("基础设定")
        if self._outer_tabs is None:
            return
        for idx in range(self._outer_tabs.count()):
            if self._outer_tabs.tabText(idx) != "基础设定":
                continue
            group_tabs = self._outer_tabs.widget(idx)
            if isinstance(group_tabs, QTabWidget):
                for j in range(group_tabs.count()):
                    if group_tabs.tabText(j) == "角色与实体":
                        group_tabs.setCurrentIndex(j)
                        return

    def focus_book_consistency_tab(self) -> None:
        """Switch to 治理 outer + 全书审修 inner (dashboard quick-nav router)."""
        self._focus_outer_tab_by_text("治理")
        if self._outer_tabs is None:
            return
        for idx in range(self._outer_tabs.count()):
            if self._outer_tabs.tabText(idx) != "治理":
                continue
            governance_tabs = self._outer_tabs.widget(idx)
            if isinstance(governance_tabs, QTabWidget):
                for j in range(governance_tabs.count()):
                    if governance_tabs.tabText(j) == "全书审修":
                        governance_tabs.setCurrentIndex(j)
                        return

    def _focus_outer_tab_by_text(self, text: str) -> None:
        if self._outer_tabs is None:
            return
        for idx in range(self._outer_tabs.count()):
            if self._outer_tabs.tabText(idx) == text:
                self._outer_tabs.setCurrentIndex(idx)
                return

    def has_unsaved_changes(self) -> bool:
        return (
            bool(
                self._token_tab is not None
                and hasattr(self._token_tab, "has_unsaved_changes")
                and self._token_tab.has_unsaved_changes()
            )
            or self._has_revision_unsaved_changes()
            or self._has_outline_unsaved_changes()
            or self._has_character_unsaved_changes()
        )

    def save_pending_changes(self) -> bool:
        character_saved = True
        for character_widget in list(self._character_widgets):
            try:
                character_saved = character_widget.save_pending_changes() and character_saved
            except (RuntimeError, TypeError):
                continue
        outline_saved = True
        for outline_widget in list(self._outline_widgets):
            try:
                outline_saved = outline_widget.save_pending_changes() and outline_saved
            except (RuntimeError, TypeError):
                continue
        revision_saved = True
        for revision_widget in list(self._revision_widgets):
            try:
                revision_saved = revision_widget.save_pending_changes() and revision_saved
            except (RuntimeError, TypeError):
                continue
        if self._token_tab is None:
            return character_saved and outline_saved and revision_saved
        if not hasattr(self._token_tab, "save_pending_changes"):
            return character_saved and outline_saved and revision_saved
        return (
            bool(self._token_tab.save_pending_changes())
            and character_saved
            and outline_saved
            and revision_saved
        )

    def unsaved_changes_description(self) -> str:
        parts: list[str] = []
        if self._has_revision_unsaved_changes():
            parts.append("- 卷帙页（终稿修订）有未保存修改")
        if self._has_outline_unsaved_changes():
            parts.append("- 卷帙页（全书大纲润色）有未保存修改")
        if self._has_character_unsaved_changes():
            parts.append("- 卷帙页（角色与关系）有未保存修改")
        if (
            self._token_tab is not None
            and hasattr(self._token_tab, "has_unsaved_changes")
            and self._token_tab.has_unsaved_changes()
        ):
            parts.append("- 卷帙页（Token 追踪）有未保存设置")
        return "\n".join(parts) or "- 卷帙页有未保存修改"

    def _has_revision_unsaved_changes(self) -> bool:
        for widget in list(self._revision_widgets):
            try:
                if widget.has_unsaved_changes():
                    return True
            except (RuntimeError, TypeError):
                continue
        return False

    def _has_outline_unsaved_changes(self) -> bool:
        for widget in list(self._outline_widgets):
            try:
                if widget.has_unsaved_changes():
                    return True
            except (RuntimeError, TypeError):
                continue
        return False

    def _has_character_unsaved_changes(self) -> bool:
        for widget in list(self._character_widgets):
            try:
                if widget.has_unsaved_changes():
                    return True
            except (RuntimeError, TypeError):
                continue
        return False

    def _has_character_active_edit_session(self) -> bool:
        for widget in list(self._character_widgets):
            try:
                if hasattr(widget, "has_active_edit_session") and widget.has_active_edit_session():
                    return True
            except (RuntimeError, TypeError):
                continue
        return False

    def _confirm_revision_widgets_can_close(self) -> bool:
        for widget in list(self._revision_widgets):
            try:
                if not widget.confirm_close():
                    return False
            except (RuntimeError, TypeError):
                continue
        return True

    def _confirm_outline_widgets_can_close(self) -> bool:
        for widget in list(self._outline_widgets):
            try:
                if not widget.confirm_close():
                    return False
            except (RuntimeError, TypeError):
                continue
        return True

    def _confirm_character_widgets_can_close(self) -> bool:
        for widget in list(self._character_widgets):
            try:
                if not widget.confirm_close():
                    return False
            except (RuntimeError, TypeError):
                continue
        return True

    def _confirm_embedded_editors_can_close(self) -> bool:
        return (
            self._confirm_revision_widgets_can_close()
            and self._confirm_outline_widgets_can_close()
            and self._confirm_character_widgets_can_close()
        )

    def _handle_deep_link(self, url: object) -> None:
        """Handle a deep-link anchor click from a rendered report."""
        url_str = url.toString() if hasattr(url, "toString") else str(url)
        info = parse_deep_link(url_str)
        if info is None:
            return
        if info.get("type") == "chapter":
            project_id = str(info["project_id"])
            chapter_number = int(info["chapter_number"])
            self.navigate_requested.emit(f"chapter_studio:{project_id}:{chapter_number}")

    def _wire_deep_links(self, widget: QWidget | None) -> None:
        """If *widget* is a QTextBrowser with deep links, connect its signal."""
        if (
            widget is not None
            and isinstance(widget, QTextBrowser)
            and widget.property("has_deep_links")
        ):
            connect_deep_links(widget, self._handle_deep_link)

    def _wire_outline_widget(
        self,
        widget: InteractiveOutlineWidget,
        tabs: QWidget,
        label: str,
    ) -> None:
        """Connect the outline widget's sync-contracts signal to the window.

        Forwards the ``sync_chapter_contracts_requested`` signal up the
        page tree until it reaches the main window, which dispatches it
        through ``_JOB_DISPATCH``.
        """
        # Enable the sync button once the user has interacted with the widget.
        # Real save-state tracking already lives in the widget; we surface
        # the button when the widget becomes visible so it is always
        # reachable from the polish panel.
        try:
            widget._set_sync_btn_enabled(True)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        # Bubble the signal up to the top-level window.
        parent = self.parent()
        target_dispatch = None
        while parent is not None:
            if hasattr(parent, "_dispatch_job"):
                target_dispatch = parent._dispatch_job  # noqa: SLF001
                break
            parent = parent.parent()
        if target_dispatch is None:
            return
        widget.sync_chapter_contracts_requested.connect(target_dispatch)
        widget.extend_outline_requested.connect(target_dispatch)

    def set_outline_sync_enabled(self, enabled: bool) -> None:
        """Enable/disable sync controls on embedded outline editors."""
        for widget in list(self._outline_widgets):
            try:
                widget._set_sync_btn_enabled(enabled)  # noqa: SLF001
            except (RuntimeError, TypeError, AttributeError):
                continue

    def notify_outline_sync_finished(
        self,
        *,
        project_id: str,
        status: str,
        result: dict[str, object] | None = None,
        error: str = "",
    ) -> None:
        """Forward sync job completion details to matching embedded outline editors."""
        if self._current_project_id and project_id and project_id != self._current_project_id:
            return
        for widget in list(self._outline_widgets):
            try:
                if project_id and widget._outline_path.parent.name != project_id:  # noqa: SLF001
                    continue
                widget._on_sync_job_finished(  # noqa: SLF001
                    status=status,
                    result=dict(result or {}),
                    error=error,
                )
            except (RuntimeError, TypeError, AttributeError):
                continue

    def notify_outline_extend_finished(
        self,
        *,
        project_id: str,
        status: str,
        result: dict[str, object] | None = None,
        error: str = "",
    ) -> None:
        """Forward extend-outline job completion details to matching outline editors."""
        if self._current_project_id and project_id and project_id != self._current_project_id:
            return
        for widget in list(self._outline_widgets):
            try:
                if project_id and widget._outline_path.parent.name != project_id:  # noqa: SLF001
                    continue
                widget._on_extend_job_finished(  # noqa: SLF001
                    status=status,
                    result=dict(result or {}),
                    error=error,
                )
            except (RuntimeError, TypeError, AttributeError):
                continue

    # ── Combo box change handler ─────────────────────────────────

    def _on_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        pid = self._project_combo.itemData(index)
        if pid and pid != self._current_project_id:
            if not self._confirm_embedded_editors_can_close():
                previous = self._project_combo.findData(self._current_project_id)
                if previous >= 0:
                    self._project_combo.blockSignals(True)
                    self._project_combo.setCurrentIndex(previous)
                    self._project_combo.blockSignals(False)
                return
            self._current_project_id = pid
            # Reset chapter memory and fingerprint when switching projects
            self._current_chapter_prose = 1
            self._current_chapter_report = 1
            self._last_build_fingerprint = ()
            self._has_established_mtime_baseline = False
            self._rebuild_viewer()
            self.context_changed.emit()

    # ── Viewer rebuild ───────────────────────────────────────────

    def _rebuild_if_changed(self, *, force: bool = False) -> None:
        """Rebuild viewer only when the active project's chapter data has changed.

        Called by ``bind_workspace`` (driven by the 30-second background timer and
        job-completion events).  Skips the rebuild when nothing relevant has changed
        so that the user's scroll position and tab selection are preserved.
        """
        if not self._snapshot or not self._current_project_id:
            self._rebuild_viewer()
            return
        detail = self._snapshot.details.get(self._current_project_id)
        if detail is None:
            self._rebuild_viewer()
            return
        if (
            self._has_revision_unsaved_changes()
            or self._has_outline_unsaved_changes()
            or self._has_character_unsaved_changes()
            or self._has_character_active_edit_session()
        ):
            return
        if force:
            self._rebuild_viewer()
            return
        new_fp = self._project_detail_fingerprint(detail)
        if new_fp == self._last_build_fingerprint:
            return  # Data unchanged — leave the view exactly as-is
        if self._is_chapter_prose_tab_active():
            prose_fp = self._active_chapter_prose_fingerprint()
            if prose_fp and prose_fp == self._last_chapter_prose_fingerprint:
                return
        self._rebuild_viewer()

    def _is_chapter_prose_tab_active(self) -> bool:
        tabs = self._outer_tabs
        if tabs is None or tabs.currentIndex() < 0:
            return False
        current_label = tabs.tabText(tabs.currentIndex())
        if current_label in {"章节正文", "正文"}:
            return True
        if current_label != "章节":
            return False
        inner = tabs.widget(tabs.currentIndex())
        return (
            isinstance(inner, QTabWidget)
            and inner.currentIndex() >= 0
            and inner.tabText(inner.currentIndex()) in {"正文", "章节正文"}
        )

    def _active_chapter_prose_fingerprint(self) -> tuple[object, ...]:
        if not self._snapshot or not self._current_project_id:
            return ()
        project_dir = self._snapshot.storage_root / self._current_project_id
        detail = self._snapshot.details.get(self._current_project_id)
        paths: list[Path] = []
        if detail is not None and detail.mode == "short":
            paths.append(project_dir / "chapters" / "short_story.md")
        else:
            layout = ProjectLayout(project_dir)
            paths.append(layout.chapter_path(self._current_chapter_prose))
            draft_dir = layout.chapter_draft_dir(self._current_chapter_prose)
            if draft_dir.is_dir():
                paths.extend(sorted(draft_dir.glob("v*.md")))

        file_fp: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            file_fp.append((str(path.relative_to(project_dir)), stat.st_mtime_ns, stat.st_size))
        return (
            self._current_project_id,
            self._current_chapter_prose,
            tuple(file_fp),
        )

    def _active_project_files_changed(
        self,
        snapshot: DesktopWorkspaceSnapshot | None = None,
    ) -> bool:
        """Return True when the active project's on-disk artifacts changed."""
        active_snapshot = snapshot or self._snapshot
        if active_snapshot is None or not self._current_project_id:
            return False
        return self._project_mtimes_changed(active_snapshot.storage_root / self._current_project_id)

    def _collect_project_file_fingerprints(
        self,
        project_dir: Path,
    ) -> dict[str, tuple[int, int]]:
        """Collect cheap file fingerprints for document artifacts shown in 卷帙."""
        fingerprints: dict[str, tuple[int, int]] = {}

        def _record(f: Path) -> None:
            try:
                stat = f.stat()
            except OSError:
                return
            try:
                key = str(f.relative_to(project_dir))
            except ValueError:
                key = str(f)
            fingerprints[key] = (stat.st_mtime_ns, stat.st_size)

        for rel in self._KEY_FILES:
            f = project_dir / rel
            _record(f)

        for rel_dir in self._MTIME_SCAN_DIRS:
            root = project_dir / rel_dir
            if not root.is_dir():
                continue
            for f in root.rglob("*.json"):
                _record(f)
            for f in root.rglob("*.jsonl"):
                _record(f)

        return fingerprints

    def _adopt_project_mtime_baseline(self, project_dir: Path) -> None:
        self._last_project_mtimes = self._collect_project_file_fingerprints(project_dir)
        self._has_established_mtime_baseline = True

    def _project_mtimes_changed(self, project_dir: Path) -> bool:
        """Return True if any visible project artifact changed since last scan."""
        new_mtimes = self._collect_project_file_fingerprints(project_dir)
        if not self._has_established_mtime_baseline:
            # First scan: adopt the current mtimes as baseline without
            # signalling a change.  This avoids rebuilding the viewer on
            # the initial bind just because we don't have a prior snapshot.
            self._last_project_mtimes = new_mtimes
            self._has_established_mtime_baseline = True
            return False
        changed = new_mtimes != self._last_project_mtimes
        self._last_project_mtimes = new_mtimes
        return changed

    @staticmethod
    def _project_detail_fingerprint(detail: ProjectDetail) -> tuple[object, ...]:
        """Return a cheap UI fingerprint for the active reader project."""
        chapter_fp = tuple(
            (
                chapter.chapter_number,
                chapter.title,
                chapter.word_count,
                chapter.overall_score,
                chapter.continuity_score,
                chapter.updated_at,
                chapter.preview,
            )
            for chapter in detail.chapters
        )
        return (
            detail.updated_at,
            detail.project_state,
            detail.project_state_updated_at,
            detail.completed_chapters,
            detail.latest_chapter,
            detail.has_outline,
            detail.has_canon,
            detail.outline_generated_count,
            detail.init_resume_available,
            detail.init_resume_step,
            detail.init_resume_progress_label,
            detail.init_resume_progress_percent,
            tuple(sorted(detail.artifact_counts.items())),
            tuple(detail.recent_files),
            chapter_fp,
        )

    def _rebuild_viewer(self) -> None:
        # Any pending staged build belongs to an older project/snapshot.
        self._viewer_build_generation += 1
        self._viewer_build_timer.stop()
        self._pending_viewer_build = None
        # Update build fingerprint so the next timer-tick can skip if nothing changed
        if self._snapshot and self._current_project_id:
            detail = self._snapshot.details.get(self._current_project_id)
            if detail is not None:
                self._last_build_fingerprint = self._project_detail_fingerprint(detail)

        # Preserve current tab selection across rebuilds (outer + inner)
        old_tab_state: dict[str, str | None] = {}
        old_tab_text: str | None = None
        for i in range(self._content_layout.count()):
            item = self._content_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QTabWidget):
                if widget.currentIndex() >= 0:
                    old_tab_text = widget.tabText(widget.currentIndex())
                    # Save inner tab states for each outer tab
                    for idx in range(widget.count()):
                        inner = widget.widget(idx)
                        if isinstance(inner, QTabWidget) and inner.currentIndex() >= 0:
                            old_tab_state[widget.tabText(idx)] = inner.tabText(inner.currentIndex())
                break

        self._capture_revision_view_states()
        self._stop_active_fades()
        clear_layout(self._content_layout)
        self._revision_widgets = []
        self._outline_widgets = []
        self._character_widgets = []
        self._token_tab = None
        self._outer_tabs = None
        self._lazy_tab_builders.clear()
        self._pending_outline_wire.clear()
        if not self._snapshot or not self._current_project_id:
            self._viewer_title.setText("")
            self._show_empty_hint("请在案头选择一个项目。")
            return

        if self._current_project_id not in self._snapshot.details:
            self._viewer_title.setText("")
            self._show_empty_hint("所选项目数据不可用。")
            return

        detail = self._snapshot.details[self._current_project_id]
        project_item = next(
            (p for p in self._snapshot.projects if p.project_id == self._current_project_id),
            None,
        )
        if project_item is None:
            self._show_empty_hint("所选项目不在库中。")
            return

        self._viewer_title.setText(f"\U0001f4d6  {project_item.title}")

        project_dir = self._snapshot.storage_root / self._current_project_id
        layout = ProjectLayout(project_dir)

        build = (
            self._viewer_build_generation,
            self._current_project_id,
            detail,
            project_dir,
            layout,
            old_tab_state,
            old_tab_text,
        )
        # A mounted page can yield one paint turn to the shared loading state.
        # Standalone instances (tests, export helpers) intentionally retain
        # synchronous construction as their public API has always promised an
        # immediately available tab tree.
        if self.isVisible() and self.parentWidget() is not None:
            # Let the loading state paint before the (Qt-main-thread) widget
            # construction phase.  File-heavy tabs are lazy below, so this
            # short staging turn keeps project switches responsive.
            self._show_loading_state()
            self._pending_viewer_build = build
            self._viewer_build_timer.start(self._VIEWER_BUILD_DELAY_MS)
            return
        self._build_viewer_content(*build)

    def _finish_pending_viewer_build(self) -> None:
        pending = self._pending_viewer_build
        self._pending_viewer_build = None
        if pending is None or self._is_shutting_down:
            return
        self._build_viewer_content(*pending)

    def _build_viewer_content(
        self,
        generation: int,
        project_id: str,
        detail: ProjectDetail,
        project_dir: Path,
        layout: ProjectLayout,
        old_tab_state: dict[str, str | None],
        old_tab_text: str | None,
    ) -> None:
        """Build the reader after its loading state has had a chance to paint."""
        if (
            self._is_shutting_down
            or generation != self._viewer_build_generation
            or project_id != self._current_project_id
        ):
            return
        clear_layout(self._content_layout)
        if detail.mode == "short":
            self._build_short_viewer(project_dir, detail)
        else:
            self._build_long_viewer(project_dir, detail, layout)

        # Restore previous tab selection (outer + inner).
        for i in range(self._content_layout.count()):
            item = self._content_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if not isinstance(widget, QTabWidget):
                continue
            # Restore nested selection before activating the outer tab.  If
            # index 0 is lazy, doing this in the opposite order needlessly
            # builds both the default child and the restored child.
            for idx in range(widget.count()):
                tab_label = widget.tabText(idx)
                if tab_label in old_tab_state:
                    inner = widget.widget(idx)
                    if isinstance(inner, QTabWidget):
                        for j in range(inner.count()):
                            if inner.tabText(j) == old_tab_state[tab_label]:
                                inner.setCurrentIndex(j)
                                break
            if old_tab_text:
                for idx in range(widget.count()):
                    if widget.tabText(idx) == old_tab_text:
                        widget.setCurrentIndex(idx)
                        break
            break
        self._apply_pending_ui_state()
        if self._outer_tabs is not None and self._outer_tabs.currentIndex() >= 0:
            # The first nested lazy tab is current before its signal wiring is
            # connected. Trigger the normal activation path once so the reader
            # shows the selected document rather than a permanent placeholder.
            self._on_outer_tab_changed(self._outer_tabs.currentIndex())
        self._last_chapter_prose_fingerprint = self._active_chapter_prose_fingerprint()
        self._adopt_project_mtime_baseline(project_dir)

    def _apply_pending_ui_state(self) -> None:
        """Apply a one-time restored tab position after the reader is rebuilt."""
        state = self._pending_ui_state
        if state is None or self._outer_tabs is None:
            return
        inner_tabs = state.get("inner_tabs")
        if isinstance(inner_tabs, dict):
            for index in range(self._outer_tabs.count()):
                outer_label = self._outer_tabs.tabText(index)
                inner_tab = self._outer_tabs.widget(index)
                inner_label = inner_tabs.get(outer_label)
                if not isinstance(inner_tab, QTabWidget) or not isinstance(inner_label, str):
                    continue
                for inner_index in range(inner_tab.count()):
                    if inner_tab.tabText(inner_index) == inner_label:
                        inner_tab.setCurrentIndex(inner_index)
                        break
        outer_tab = str(state.get("outer_tab") or "")
        if outer_tab:
            for index in range(self._outer_tabs.count()):
                if self._outer_tabs.tabText(index) == outer_tab:
                    self._outer_tabs.setCurrentIndex(index)
                    break
        self._pending_ui_state = None

    @staticmethod
    def _configure_reader_tabs(tabs: QTabWidget, object_name: str) -> QTabWidget:
        """Apply compact, scrollable behavior to document-reader tab groups."""
        tabs.setObjectName(object_name)
        tabs.setDocumentMode(True)
        tabs.setElideMode(Qt.TextElideMode.ElideRight)
        tab_bar = tabs.tabBar()
        tab_bar.setObjectName(f"{object_name}Bar")
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(True)
        tab_bar.setDrawBase(False)
        tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        return tabs

    @staticmethod
    def _safe_smart_render_document(path: Path) -> QWidget | None:
        """Render a project artifact without letting one bad renderer blank the reader."""
        try:
            return smart_render_document(path)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "project_document_render_failed | path=%s | error=%s",
                path,
                exc,
                exc_info=True,
            )
            return None

    @staticmethod
    def _render_document_html(path: Path) -> str | None:
        """Re-render rich document HTML after the active desktop theme changes."""
        rendered = ProjectsPage._safe_smart_render_document(path)
        if not isinstance(rendered, QTextBrowser):
            return None
        try:
            return rendered.toHtml()
        finally:
            rendered.deleteLater()

    # ── Tab switch animation (Task 22) ───────────────────────────

    _TAB_FADE_MS: int = 200

    def _ensure_fallback_outer_tabs(self) -> QTabWidget:
        """Return the outer tabs, creating a 2-tab fallback if missing.

        Used by ``_switch_tab`` and tests so the animation hook fires
        even when no project is loaded.  In production, the outer tabs
        are always created by ``_build_long_viewer`` before the user
        can click anything.  The fallback is parented to the page so
        ``findChildren`` walks reach its animation.
        """
        if self._outer_tabs is not None and self._outer_tabs.count() >= 2:
            return self._outer_tabs
        if self._outer_tabs is None:
            tabs = self._configure_reader_tabs(QTabWidget(), "projectDocumentTabs")
            placeholder_a = QWidget()
            placeholder_b = QWidget()
            tabs.addTab(placeholder_a, "占位 1")
            tabs.addTab(placeholder_b, "占位 2")
            tabs.setParent(self)
            self._outer_tabs = tabs
            self._outer_tab_placeholders: tuple[QWidget, QWidget] = (placeholder_a, placeholder_b)
            tabs.currentChanged.connect(self._on_outer_tab_changed)
        return self._outer_tabs

    def _switch_tab(self, index: int) -> None:
        """Switch the outer reader tabs to *index* and trigger a 200ms fade.

        Thin wrapper around ``setCurrentIndex`` — the fade is owned by
        ``_on_outer_tab_changed`` so user clicks and programmatic calls
        share a single code path.  No-op if the index is out of range.
        """
        tabs = self._ensure_fallback_outer_tabs()
        if not (0 <= index < tabs.count()):
            return
        tabs.setCurrentIndex(index)

    def _start_fade(self, widget: QWidget) -> None:
        """Run ``Motion.fade_in`` and remember the animation for testing.

        Wraps :func:`Motion.fade_in` so the returned ``QPropertyAnimation``
        is captured on ``self._active_fades``.  ``findChildren`` cannot
        reach the animation (its Qt parent is the graphics effect, not
        the widget tree), and ``widget.property('_motion_anim')``
        segfaults in PySide6 6.11 on macOS for some destroyed-widget
        states, so we track the animation on the page itself.
        """
        if not self._can_use_graphics_effect_fade(widget):
            return
        anim = Motion.fade_in(widget, duration=self._TAB_FADE_MS)
        if anim is not None:
            self._active_fades.append(anim)
            target = self._fade_target(anim)
            anim.finished.connect(
                lambda anim=anim, target=target: self._cleanup_finished_fade(anim, target)
            )

    @staticmethod
    def _fade_target(anim: QAbstractAnimation) -> QObject | None:
        target = getattr(anim, "targetObject", None)
        if not callable(target):
            return None
        try:
            value = target()
        except RuntimeError:
            return None
        return value if isinstance(value, QObject) else None

    @staticmethod
    def _restore_opacity_effect(target: QObject | None) -> None:
        if not isinstance(target, QGraphicsOpacityEffect):
            return
        try:
            target.setOpacity(1.0)
            parent = target.parent()
            if isinstance(parent, QWidget) and parent.graphicsEffect() is target:
                parent.setGraphicsEffect(cast(Any, None))
        except RuntimeError:
            return

    def _cleanup_finished_fade(
        self,
        anim: QAbstractAnimation,
        target: QObject | None,
    ) -> None:
        self._active_fades = [item for item in self._active_fades if item is not anim]
        self._restore_opacity_effect(target)

    def _stop_active_fades(self) -> None:
        for anim in list(self._active_fades):
            target = self._fade_target(anim)
            try:
                anim.stop()
            except RuntimeError:
                pass
            self._restore_opacity_effect(target)
        self._active_fades.clear()

    @staticmethod
    def _can_use_graphics_effect_fade(widget: QWidget) -> bool:
        """Return False for complex reader/editor trees that are unsafe to pixmap-fade."""
        if isinstance(
            widget,
            (
                QAbstractScrollArea,
                QTabWidget,
                QSplitter,
                FinalRevisionWidget,
                InteractiveOutlineWidget,
            ),
        ):
            return False
        return not (
            widget.findChild(QAbstractScrollArea) is not None
            or widget.findChild(QTabWidget) is not None
            or widget.findChild(QSplitter) is not None
            or widget.findChild(FinalRevisionWidget) is not None
            or widget.findChild(InteractiveOutlineWidget) is not None
        )

    def _on_outer_tab_changed(self, index: int) -> None:
        """Fade-in handler wired to outer-tab ``currentChanged``.

        D1-safe: opacity-only via the Motion library.  Fades only the
        deepest active widget so chrome (header, splitter handle) does
        not flicker.  Build-time tab population happens while the page
        is still hidden, so skip those synthetic currentChanged events.
        """
        self.ui_state_changed.emit()
        if self._outer_tabs is None or index < 0:
            return
        widget = self._outer_tabs.currentWidget()
        if widget is None:
            return
        if isinstance(widget, QTabWidget):
            # A nested widget may have a lazy tab at index 0.  QTabWidget makes
            # that tab current before _register_lazy_tab can connect its
            # currentChanged handler, so entering the outer tab is the only
            # activation event we can reliably observe.
            self._load_lazy_tab(widget, widget.currentIndex())
            inner = widget.currentWidget()
            if inner is not None:
                widget = inner
        if not self._can_start_reader_fade():
            return
        if isinstance(widget, QWidget):
            self._start_fade(widget)

    def _ensure_fallback_chapter_list(self) -> QListWidget:
        """Return the active chapter list, creating a 2-row stub if missing.

        Used by ``_switch_chapter`` and tests so the fade hook fires
        even when no project is loaded.  In production the chapter list
        is always created by ``_build_chapter_tab`` first.
        """
        if self._active_chapter_list is not None and self._active_chapter_list.count() >= 2:
            return self._active_chapter_list

        host_widget = QWidget()
        host_widget.setObjectName("chapterHostStub")
        host_widget.setParent(self)
        host_layout = QVBoxLayout(host_widget)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        stub_list = QListWidget()
        stub_list.setObjectName("chapterList")
        stub_list.setParent(self)
        for n in (1, 2):
            item = QListWidgetItem(f"第 {n} 章")
            item.setData(Qt.ItemDataRole.UserRole, n)
            stub_list.addItem(item)

        # Keep strong refs on self so the closures survive GC of
        # function-local names (the stub has no Qt parent).
        self._chapter_stub_host_widget: QWidget = host_widget
        self._chapter_stub_host_layout: QVBoxLayout = host_layout
        self._chapter_stub_list: QListWidget = stub_list

        def _on_stub_change(_row: int) -> None:
            if host_layout.count() > 0:
                self._stop_active_fades()
                clear_layout(host_layout)
            sel = stub_list.currentItem()
            num = sel.data(Qt.ItemDataRole.UserRole) if sel is not None else None
            if isinstance(num, int):
                lbl = QLabel(f"占位章节 {num} 内容")
                lbl.setObjectName("viewerHint")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                host_layout.addWidget(lbl, 1)
            self._fade_chapter_content(host_layout)

        stub_list.currentRowChanged.connect(_on_stub_change)
        stub_list.setCurrentRow(0)
        self._active_chapter_list = stub_list
        self._active_chapter_host = host_layout
        return stub_list

    def _switch_chapter(self, chapter_number: int) -> None:
        """Switch to *chapter_number* and trigger a 200ms content fade.

        Thin wrapper around the active ``QListWidget``'s row select —
        the existing ``currentRowChanged`` handler refreshes the content
        and the fade-in hook fires from there.  No-op if the chapter
        number is not in the list.
        """
        ch_list = self._ensure_fallback_chapter_list()
        target_row = -1
        for row in range(ch_list.count()):
            item = ch_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == chapter_number:
                target_row = row
                break
        if target_row < 0:
            return
        ch_list.setCurrentRow(target_row)

    # ── Short-story viewer ───────────────────────────────────────

    def _build_short_viewer(self, project_dir: Path, detail: ProjectDetail) -> None:
        tabs = self._configure_reader_tabs(QTabWidget(), "projectDocumentTabs")
        self._outer_tabs = tabs
        tabs.currentChanged.connect(self._on_outer_tab_changed)
        has_any = False

        for label, rel_path in _SHORT_TABS:
            path = project_dir / rel_path
            if not path.exists():
                continue
            if rel_path == "chapters/short_story.md":
                widget = self._make_final_revision_view(
                    project_dir=project_dir,
                    chapter_path=path,
                    chapter_number=0,
                    title=detail.title or "短篇正文",
                    text=read_artifact_text(path),
                )
                tabs.addTab(widget, label)
                has_any = True
                continue
            rendered_widget = self._safe_smart_render_document(path)
            if rendered_widget is not None:
                tabs.addTab(rendered_widget, label)
            else:
                is_prose = path.suffix == ".md"
                tabs.addTab(self._make_content_view(path, prose=is_prose), label)
            has_any = True

        drafts_dir = project_dir / "drafts"
        if drafts_dir.is_dir():
            draft_files = sorted(drafts_dir.glob("v*_*.md"))
            if draft_files:
                if len(draft_files) == 1:
                    tabs.addTab(
                        self._make_content_view(draft_files[0], prose=True),
                        draft_display_name(draft_files[0].stem),
                    )
                else:
                    sub = self._configure_reader_tabs(QTabWidget(), "projectNestedTabs")
                    for f in draft_files:
                        sub.addTab(
                            self._make_content_view(f, prose=True),
                            draft_display_name(f.stem),
                        )
                    tabs.addTab(sub, "草稿历程")
                has_any = True

        if has_any:
            self._content_layout.addWidget(tabs, 1)
        else:
            self._show_empty_hint("此卷尚无可查看的文档。\n流程完成后此处将展示作品内容。")

    # ── Long-story viewer ────────────────────────────────────────

    def _build_long_viewer(
        self,
        project_dir: Path,
        detail: ProjectDetail,
        layout: ProjectLayout,
    ) -> None:
        outline_chapters = self._load_outline_chapters(layout)
        completed_nums = {ch.chapter_number for ch in detail.chapters}

        tabs = self._configure_reader_tabs(QTabWidget(), "projectDocumentTabs")
        self._outer_tabs = tabs
        self._token_tab = None
        tabs.currentChanged.connect(self._on_outer_tab_changed)
        has_any = False

        for group_label, specs in _LONG_GLOBAL_GROUPS:
            group_tabs = self._configure_reader_tabs(QTabWidget(), "projectGroupedDocumentTabs")
            group_has_any = False
            for label, rel_path in specs:
                # Defer character bible (CharacterBibleEditor is heavy) to
                # first activation; keep the remaining lightweight documents
                # ready for the reader's first visible frame.
                is_heavy = rel_path in ("character_bible.json",)
                if is_heavy:
                    added = self._add_lazy_project_document_tab(
                        group_tabs, project_dir, label, rel_path
                    )
                else:
                    added = self._add_project_document_tab(
                        group_tabs, project_dir, label, rel_path
                    )
                group_has_any = added or group_has_any
            if group_has_any:
                tabs.addTab(group_tabs, group_label)
                has_any = True

        for label, rel_path in _LONG_STANDALONE_TABS:
            # InteractiveOutlineWidget (outline.json) is heavy - defer it.
            if rel_path == "outline.json":
                has_any = self._add_lazy_project_document_tab(
                    tabs, project_dir, label, rel_path
                ) or has_any
            else:
                has_any = self._add_project_document_tab(
                    tabs, project_dir, label, rel_path
                ) or has_any

        chapter_tabs = self._configure_reader_tabs(QTabWidget(), "projectChapterTabs")
        chapter_text_tab = self._build_chapter_tab(
            project_dir,
            detail,
            layout,
            outline_chapters,
            completed_nums,
            prose=True,
        )
        chapter_tabs.addTab(chapter_text_tab, "正文")
        # Defer the chapter-report tab: it renders ~14 JSON reports across
        # 4 nested group tabs for the selected chapter on first activation.
        self._register_lazy_tab(
            chapter_tabs,
            "报告",
            lambda pd=project_dir, d=detail, lyt=layout, oc=outline_chapters, cn=completed_nums: self._build_chapter_tab(
                pd, d, lyt, oc, cn, prose=False
            ),
            message="正在准备章节报告…",
        )
        tabs.addTab(chapter_tabs, "章节")
        has_any = True

        # Governance tabs: defer all three sub-tabs.
        governance_tabs = self._configure_reader_tabs(QTabWidget(), "projectGovernanceTabs")
        self._register_lazy_tab(
            governance_tabs,
            "全书审修",
            lambda pd=project_dir: self._build_book_consistency_tab(pd),
            message="正在准备全书审修报告…",
        )
        self._add_lazy_artifact_group_tab(
            governance_tabs,
            project_dir,
            "初始化准入",
            _INIT_ADMISSION_ARTIFACTS,
            "initAdmissionTabs",
        )
        self._add_lazy_artifact_group_tab(
            governance_tabs,
            project_dir,
            "一致性治理",
            _INIT_COHERENCE_GOVERNANCE_ARTIFACTS,
            "initCoherenceGovernanceTabs",
        )
        tabs.addTab(governance_tabs, "治理")
        has_any = True

        tracking_tabs = self._configure_reader_tabs(QTabWidget(), "projectTrackingTabs")
        self._register_lazy_tab(
            tracking_tabs,
            "关系追踪",
            lambda pd=project_dir: self._build_relationship_tab(pd),
            message="角色关系图谱待展开。",
        )
        self._register_lazy_tab(
            tracking_tabs,
            "Token 追踪",
            lambda pd=project_dir: self._build_token_tab(pd),
            message="Token 用量统计待展开。",
        )
        tabs.addTab(tracking_tabs, "追踪")

        if has_any:
            self._content_layout.addWidget(tabs, 1)
        else:
            self._show_empty_hint("此项目尚无可查看的文档。\n完成立项流程后此处将展示各类资料。")

    def _add_project_document_tab(
        self,
        tabs: QTabWidget,
        project_dir: Path,
        label: str,
        rel_path: str,
    ) -> bool:
        path = project_dir / rel_path
        if rel_path == "outline.json":
            if path.exists():
                try:
                    outline_data = json.loads(path.read_text(encoding="utf-8"))
                    outline_widget = InteractiveOutlineWidget(outline_data, path)
                    self._outline_widgets.append(outline_widget)
                    tabs.addTab(outline_widget, label)
                    self._wire_outline_widget(outline_widget, tabs, label)
                    return True
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
            preview_data = self._load_outline_generation_preview(project_dir)
            if preview_data is not None:
                preview_widget = render_outline(preview_data)
                tabs.addTab(preview_widget, f"{label}（生成中）")
                return True
            tabs.addTab(self._make_missing_document_hint(label), label)
            return True

        if rel_path == "character_bible.json":
            if path.exists():
                try:
                    editor = CharacterBibleEditor(project_dir)
                    editor.workspace_refresh_requested.connect(self.workspace_refresh_requested)
                    self._character_widgets.append(editor)
                    tabs.addTab(editor, label)
                    return True
                except (OSError, json.JSONDecodeError, ValueError, RuntimeError):
                    pass
            tabs.addTab(self._make_missing_document_hint(label), label)
            return True

        if not path.exists():
            tabs.addTab(self._make_missing_document_hint(label), label)
            return True

        widget = self._safe_smart_render_document(path)
        if widget is not None:
            self._wire_deep_links(widget)
            if isinstance(widget, QTextBrowser):
                wrapped = RichDocumentViewer(
                    widget,
                    file_path=path,
                    title=path.name,
                    raw_text=smart_artifact_content(path),
                    theme_html_factory=lambda document_path=path: ProjectsPage._render_document_html(
                        document_path
                    ),
                )
                tabs.addTab(wrapped, label)
            else:
                # 复杂 widget（带子组件如 CharacterGraphWidget）不包装
                tabs.addTab(widget, label)
        else:
            tabs.addTab(self._make_content_view(path), label)
        return True

    def _add_lazy_project_document_tab(
        self,
        tabs: QTabWidget,
        project_dir: Path,
        label: str,
        rel_path: str,
    ) -> bool:
        """Lazy variant of :meth:`_add_project_document_tab`.

        Registers a placeholder and defers the real widget construction
        to first tab activation.  Used for heavy document tabs
        (outline.json, character_bible.json).  Returns ``True`` when a
        placeholder was inserted.
        """
        path = project_dir / rel_path

        if rel_path == "outline.json":
            if not path.exists():
                preview_data = self._load_outline_generation_preview(project_dir)
                if preview_data is not None:
                    preview_widget = render_outline(preview_data)
                    tabs.addTab(preview_widget, f"{label}（生成中）")
                    return True
                tabs.addTab(self._make_missing_document_hint(label), label)
                return True

            def _build_outline(_pd: Path = project_dir, _label: str = label) -> QWidget:
                outline_data = json.loads((_pd / rel_path).read_text(encoding="utf-8"))
                outline_widget = InteractiveOutlineWidget(outline_data, _pd / rel_path)
                self._outline_widgets.append(outline_widget)
                # Wire after insertion - _wire_outline_widget needs the tab widget.
                # We'll wire in a post-build hook.
                self._pending_outline_wire.append((outline_widget, tabs, _label))
                return outline_widget

            self._register_lazy_tab(tabs, label, _build_outline)
            return True

        if rel_path == "character_bible.json":
            if not path.exists():
                tabs.addTab(self._make_missing_document_hint(label), label)
                return True

            def _build_character(_pd: Path = project_dir) -> QWidget:
                editor = CharacterBibleEditor(_pd)
                editor.workspace_refresh_requested.connect(self.workspace_refresh_requested)
                self._character_widgets.append(editor)
                return editor

            self._register_lazy_tab(tabs, label, _build_character)
            return True

        if not path.exists():
            tabs.addTab(self._make_missing_document_hint(label), label)
            return True

        def _build_document(_path: Path = path) -> QWidget:
            widget = self._safe_smart_render_document(_path)
            if widget is not None:
                self._wire_deep_links(widget)
                if isinstance(widget, QTextBrowser):
                    return RichDocumentViewer(
                        widget,
                        file_path=_path,
                        title=_path.name,
                        raw_text=smart_artifact_content(_path),
                        theme_html_factory=lambda document_path=_path: ProjectsPage._render_document_html(
                            document_path
                        ),
                    )
                return widget
            return self._make_content_view(_path)

        self._register_lazy_tab(tabs, label, _build_document)
        return True

    def _add_lazy_artifact_group_tab(
        self,
        tabs: QTabWidget,
        project_dir: Path,
        label: str,
        artifacts: tuple[tuple[str, str], ...],
        object_name: str,
    ) -> bool:
        """Lazy variant of :meth:`_add_existing_artifact_group_tab`.

        Checks if any artifact file exists; if so, registers a lazy tab
        that builds the full group on first activation.
        """
        has_any = any((project_dir / rel_path).exists() for _, rel_path in artifacts)
        if not has_any:
            return False

        def _build_group(
            _pd: Path = project_dir,
            _artifacts: tuple[tuple[str, str], ...] = artifacts,
            _object_name: str = object_name,
        ) -> QWidget:
            group_tabs = self._configure_reader_tabs(QTabWidget(), _object_name)
            for artifact_label, rel_path in _artifacts:
                path = _pd / rel_path
                if not path.exists():
                    continue
                widget = self._safe_smart_render_document(path)
                if widget is not None:
                    self._wire_deep_links(widget)
                    group_tabs.addTab(widget, artifact_label)
                else:
                    group_tabs.addTab(self._make_content_view(path), artifact_label)
            return group_tabs

        self._register_lazy_tab(tabs, label, _build_group)
        return True

    @staticmethod
    def _load_outline_generation_preview(project_dir: Path) -> dict[str, Any] | None:
        """Build a read-only preview from accepted outline batch checkpoints."""
        session_path = project_dir / _OUTLINE_SESSION_REL_PATH
        try:
            session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(session_payload, dict):
            return None

        accepted_batches = session_payload.get("accepted_batches")
        if not isinstance(accepted_batches, list) or not accepted_batches:
            return None

        chapters_by_number: dict[int, dict[str, Any]] = {}
        batch_dir = project_dir / _OUTLINE_BATCHES_REL_PATH
        for batch_record in accepted_batches:
            if not isinstance(batch_record, dict):
                continue
            checkpoint_name = str(batch_record.get("checkpoint_file") or "").strip()
            if not checkpoint_name or Path(checkpoint_name).name != checkpoint_name:
                continue
            checkpoint_path = batch_dir / checkpoint_name
            try:
                checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(checkpoint_payload, dict):
                continue
            chapters = checkpoint_payload.get("chapters")
            if not isinstance(chapters, list):
                continue
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    continue
                chapter_number = ProjectsPage._positive_int(chapter.get("chapter_number"))
                if chapter_number is None:
                    continue
                chapters_by_number[chapter_number] = dict(chapter)

        if not chapters_by_number:
            return None

        sorted_chapters = [chapters_by_number[number] for number in sorted(chapters_by_number)]
        total_chapters = ProjectsPage._positive_int(session_payload.get("total_chapters")) or max(
            chapters_by_number
        )
        recorded_done = ProjectsPage._positive_int(session_payload.get("chapters_done"))
        latest_chapter = ProjectsPage._positive_int(
            session_payload.get("latest_chapter_number")
        ) or max(chapters_by_number)
        loaded_count = len(sorted_chapters)
        done_count = recorded_done or loaded_count
        count_note = f"已验收 {done_count}/{total_chapters} 章"
        if done_count != loaded_count:
            count_note += f"，当前可读取 {loaded_count} 章"
        synopsis = (
            f"生成中预览：{count_note}，断点记录到第 {latest_chapter} 章。"
            "这是初始化过程中的只读临时大纲；最终组装、规范化与一致性修复后仍可能变化。"
        )

        return {
            "total_chapters": total_chapters,
            "synopsis": synopsis,
            "volume_mode": False,
            "volumes": [],
            "chapters": sorted_chapters,
        }

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _make_missing_document_hint(label: str) -> QLabel:
        placeholder = QLabel(f"暂无{label}数据。\n完成对应流程后将自动生成。")
        placeholder.setObjectName("viewerHint")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        return placeholder

    def _build_book_consistency_tab(self, project_dir: Path) -> QTabWidget:
        tabs = self._configure_reader_tabs(QTabWidget(), "bookConsistencyTabs")
        for label, rel_path in _BOOK_CONSISTENCY_REPORTS:
            path = project_dir / rel_path
            if not path.exists():
                placeholder = QLabel(f"暂无{label}数据。\n完成对应流程后将自动生成。")
                placeholder.setObjectName("viewerHint")
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                placeholder.setWordWrap(True)
                tabs.addTab(placeholder, label)
                continue
            widget = self._safe_smart_render_document(path)
            if widget is not None:
                self._wire_deep_links(widget)
                tabs.addTab(widget, label)
            else:
                tabs.addTab(self._make_content_view(path), label)
        return tabs

    def _add_existing_artifact_group_tab(
        self,
        tabs: QTabWidget,
        project_dir: Path,
        label: str,
        artifacts: tuple[tuple[str, str], ...],
        object_name: str,
    ) -> bool:
        group_tabs = self._configure_reader_tabs(QTabWidget(), object_name)
        has_any = False
        for artifact_label, rel_path in artifacts:
            path = project_dir / rel_path
            if not path.exists():
                continue
            widget = self._safe_smart_render_document(path)
            if widget is not None:
                self._wire_deep_links(widget)
                group_tabs.addTab(widget, artifact_label)
            else:
                group_tabs.addTab(self._make_content_view(path), artifact_label)
            has_any = True
        if not has_any:
            group_tabs.deleteLater()
            return False
        tabs.addTab(group_tabs, label)
        return True

    @staticmethod
    def _make_lazy_placeholder(message: str) -> QLabel:
        label = QLabel(message)
        label.setObjectName("viewerHint")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setProperty("_lazy_project_tab", True)
        return label

    # ── Generic lazy-tab mechanism ───────────────────────────────
    # Any tab (outer or nested) can be registered as lazy.  A placeholder
    # is inserted immediately; the real widget is built on first
    # ``currentChanged`` via the builder closure.  This defers the
    # construction of heavy widgets (CharacterBibleEditor,
    # InteractiveOutlineWidget, chapter-report groups, governance
    # artifacts, etc.) until the user actually opens the tab.

    def _register_lazy_tab(
        self,
        tabs: QTabWidget,
        label: str,
        builder: Any,
        message: str | None = None,
    ) -> int:
        """Insert a placeholder tab and register *builder* for lazy construction.

        Returns the tab index.  When the tab is first activated,
        ``_load_lazy_tab`` swaps the placeholder for ``builder()``.
        """
        hint = message or f"正在准备{label}…"
        index = tabs.addTab(self._make_lazy_placeholder(hint), label)
        self._lazy_tab_builders[(id(tabs), index)] = (builder, label)

        # Wire currentChanged once per tab widget.
        if not getattr(tabs, "_lazy_tab_wired", False):
            tabs.currentChanged.connect(lambda idx, t=tabs: self._load_lazy_tab(t, idx))
            tabs._lazy_tab_wired = True  # type: ignore[attr-defined]
        return index

    def _load_lazy_tab(self, tabs: QTabWidget, index: int) -> None:
        """Build and swap in the real widget for a lazy tab on first access."""
        key = (id(tabs), index)
        entry = self._lazy_tab_builders.pop(key, None)
        if entry is None:
            return
        builder, label = entry
        current = tabs.widget(index)
        if current is None or not current.property("_lazy_project_tab"):
            # Already built or not a placeholder - restore entry and bail.
            self._lazy_tab_builders[key] = entry
            return

        try:
            built_widget = builder()
        except Exception:
            # Re-queue so the next activation retries.
            self._lazy_tab_builders[key] = entry
            _logger.exception("Failed to build lazy tab: %s", label)
            return

        if built_widget is None:
            # Builder decided there's nothing to show; leave placeholder.
            self._lazy_tab_builders[key] = entry
            return

        signals_were_blocked = tabs.blockSignals(True)
        try:
            tabs.removeTab(index)
            tabs.insertTab(index, built_widget, label)
            tabs.setCurrentIndex(index)
        finally:
            tabs.blockSignals(signals_were_blocked)
        current.deleteLater()

        # Process any pending post-build wiring (e.g. outline widget signals).
        while self._pending_outline_wire:
            outline_widget, wire_tabs, wire_label = self._pending_outline_wire.pop(0)
            try:
                self._wire_outline_widget(outline_widget, wire_tabs, wire_label)
            except RuntimeError:
                pass

    def _attach_lazy_project_tabs(
        self,
        tabs: QTabWidget,
        project_dir: Path,
        relationship_index: int,
        token_index: int,
    ) -> None:
        """Backward-compat wrapper.

        Tracking tabs are now registered via :meth:`_register_lazy_tab`
        at creation time, so this method only needs to ensure the
        ``currentChanged`` signal is wired (already done by
        ``_register_lazy_tab``).  Kept for callers that build the
        placeholder externally.
        """
        def _load_if_needed(index: int) -> None:
            self._load_lazy_tab(tabs, index)

        if not getattr(tabs, "_lazy_tab_wired", False):
            tabs.currentChanged.connect(_load_if_needed)
            tabs._lazy_tab_wired = True  # type: ignore[attr-defined]

    def _load_lazy_project_tab(
        self,
        tabs: QTabWidget,
        index: int,
        project_dir: Path,
        kind: str,
    ) -> None:
        """Legacy entry point - delegates to the generic lazy-tab loader."""
        self._load_lazy_tab(tabs, index)

    def _build_token_tab(self, project_dir: Path) -> TokenAnalyticsTab:
        """Builder for the lazy Token 追踪 tab."""
        tab = TokenAnalyticsTab(project_dir)
        self._token_tab = tab
        return tab

    # ── Chapter browser ──────────────────────────────────────────

    @staticmethod
    def _load_outline_chapters(layout: ProjectLayout) -> list[dict[str, Any]]:
        path = layout.outline_path
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
            data: Any = json.loads(raw)
            chapters = data.get("chapters", []) if isinstance(data, dict) else []
            if not isinstance(chapters, list):
                return []
            return [dict(chapter) for chapter in chapters if isinstance(chapter, dict)]
        except (OSError, json.JSONDecodeError, ValueError):
            return []

    def _build_chapter_tab(
        self,
        project_dir: Path,
        detail: ProjectDetail,
        layout: ProjectLayout,
        outline_chapters: list[dict[str, Any]],
        completed_nums: set[int],
        *,
        prose: bool,
    ) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 4, 0, 0)
        page_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("docSplitter")
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left.setObjectName("chapterListPanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        search = QLineEdit()
        search.setObjectName("chapterSearch")
        search.setPlaceholderText("搜索章节…")
        search.setClearButtonEnabled(True)
        left_layout.addWidget(search)

        ch_list = QListWidget()
        ch_list.setObjectName("chapterList")
        ch_list.setUniformItemSizes(True)
        ch_list.setLayoutMode(QListView.LayoutMode.Batched)
        ch_list.setBatchSize(50)
        ch_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._populate_chapter_list(ch_list, outline_chapters, detail, completed_nums)
        left_layout.addWidget(ch_list, 1)

        splitter.addWidget(left)

        right = QWidget()
        host_layout = QVBoxLayout(right)
        host_layout.setContentsMargins(6, 0, 0, 0)
        host_layout.setSpacing(0)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 800])

        page_layout.addWidget(splitter, 1)

        self._active_chapter_list = ch_list
        self._active_chapter_host = host_layout
        self._active_chapter_project_dir = project_dir
        self._active_chapter_layout = layout
        self._active_chapter_outline = outline_chapters

        selected_num = self._current_chapter_prose if prose else self._current_chapter_report
        previous_selection: dict[str, int] = {"chapter": selected_num}

        def _on_chapter_row_changed() -> None:
            item = ch_list.currentItem()
            num = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if (
                prose
                and isinstance(num, int)
                and num != previous_selection["chapter"]
                and not self._confirm_revision_widgets_can_close()
            ):
                ch_list.blockSignals(True)
                try:
                    for row in range(ch_list.count()):
                        old_item = ch_list.item(row)
                        if (
                            old_item is not None
                            and old_item.data(Qt.ItemDataRole.UserRole)
                            == previous_selection["chapter"]
                        ):
                            ch_list.setCurrentRow(row)
                            break
                finally:
                    ch_list.blockSignals(False)
                return
            if item is not None:
                if isinstance(num, int):
                    if prose:
                        self._current_chapter_prose = num
                    else:
                        self._current_chapter_report = num
                    previous_selection["chapter"] = num
            self._refresh_chapter_content(
                ch_list,
                host_layout,
                project_dir,
                layout,
                outline_chapters,
                prose=prose,
            )

        ch_list.currentRowChanged.connect(_on_chapter_row_changed)
        search.textChanged.connect(lambda text: self._filter_chapter_list(ch_list, text))

        # Restore the previously selected chapter; fall back to row 0
        if ch_list.count() > 0:
            saved = self._current_chapter_prose if prose else self._current_chapter_report
            restore_row = 0
            for row in range(ch_list.count()):
                item = ch_list.item(row)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == saved:
                    restore_row = row
                    break
            ch_list.setCurrentRow(restore_row)
        else:
            self._refresh_chapter_content(
                ch_list,
                host_layout,
                project_dir,
                layout,
                outline_chapters,
                prose=prose,
            )

        return page

    @staticmethod
    def _populate_chapter_list(
        ch_list: QListWidget,
        outline_chapters: list[dict[str, Any]],
        detail: ProjectDetail,
        completed_nums: set[int],
    ) -> None:
        chapters = outline_chapters or [
            {"chapter_number": ch.chapter_number, "title": ch.title} for ch in detail.chapters
        ]
        for ch in chapters:
            num = ch.get("chapter_number", 0)
            title = ch.get("title", f"第{num}章")
            done = num in completed_nums
            marker = "" if done else "  \u25c7"
            item = QListWidgetItem(f"第 {num} 章 \u00b7 {title}{marker}")
            item.setData(Qt.ItemDataRole.UserRole, num)
            if not done:
                item.setForeground(resolve_qcolor("text.muted"))
            ch_list.addItem(item)

    @staticmethod
    def _filter_chapter_list(ch_list: QListWidget, text: str) -> None:
        text = text.strip().lower()
        for i in range(ch_list.count()):
            item = ch_list.item(i)
            item.setHidden(text not in item.text().lower() if text else False)

    def _refresh_chapter_content(
        self,
        ch_list: QListWidget,
        host_layout: QVBoxLayout,
        project_dir: Path,
        layout: ProjectLayout,
        outline_chapters: list[dict[str, Any]],
        *,
        prose: bool,
    ) -> None:
        self._stop_active_fades()
        clear_layout(host_layout)
        current = ch_list.currentItem()
        ch_num = current.data(Qt.ItemDataRole.UserRole) if current else None
        if ch_num is None:
            hint = QLabel("暂无章节数据。\n完成立项后此处将列出所有章节。")
            hint.setObjectName("viewerHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            host_layout.addWidget(hint, 1)
            self._fade_chapter_content(host_layout)
            return
        if prose:
            self._fill_chapter_text(ch_num, host_layout, layout, outline_chapters)
        else:
            self._fill_chapter_reports(ch_num, host_layout, project_dir)
        self._fade_chapter_content(host_layout)

    def _fade_chapter_content(self, host_layout: QVBoxLayout) -> None:
        """Fade-in the first widget of *host_layout* over 200ms (Task 22).

        D1-safe: opacity-only via the Motion library.  Only the topmost
        child is faded; nested children inherit the parent's effective
        opacity.  Silently no-ops when the host is empty.
        Build-time content population happens before the page is visible,
        so those synthetic refreshes do not start Qt animations.
        """
        if not self._can_start_reader_fade():
            return
        if host_layout.count() == 0:
            return
        item = host_layout.itemAt(0)
        if item is None:
            return
        widget = item.widget()
        if isinstance(widget, QWidget):
            self._start_fade(widget)

    def _can_start_reader_fade(self) -> bool:
        return bool(not self._is_shutting_down and self.isVisible())

    def _fill_chapter_text(
        self,
        ch_num: int,
        host_layout: QVBoxLayout,
        layout: ProjectLayout,
        outline_chapters: list[dict[str, Any]],
    ) -> None:
        final_path = layout.chapter_path(ch_num)
        draft_dir = layout.chapter_draft_dir(ch_num)

        ch_title = ""
        for ch in outline_chapters:
            if ch.get("chapter_number") == ch_num:
                ch_title = ch.get("title", "")
                break

        versions: list[tuple[str, Path]] = []
        final_empty = False
        if final_path.exists():
            final_text = (
                final_path.read_text(encoding="utf-8").strip()
                if final_path.stat().st_size > 0
                else ""
            )
            if len(final_text) > 100:
                versions.append(("终稿", final_path))
            else:
                final_empty = True
        if draft_dir.is_dir():
            for f in sorted(draft_dir.glob("v*.md")):
                versions.append((draft_display_name(f.stem), f))

        if not versions:
            label = QLabel(f"第 {ch_num} 章尚未落笔。\n续写该章后可在此阅读。")
            label.setObjectName("viewerHint")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            host_layout.addWidget(label, 1)
        elif len(versions) == 1:
            text = read_artifact_text(versions[0][1])
            if versions[0][0] == "终稿":
                host_layout.addWidget(
                    self._make_final_revision_view(
                        project_dir=layout.root,
                        chapter_path=versions[0][1],
                        chapter_number=ch_num,
                        title=ch_title,
                        text=text,
                    ),
                    1,
                )
            else:
                host_layout.addWidget(
                    render_chapter_prose(text, chapter_num=ch_num, title=ch_title),
                    1,
                )
        else:
            tabs = self._configure_reader_tabs(QTabWidget(), "chapterVersionTabs")
            best_tab = 0
            for tab_label, path in versions:
                text = read_artifact_text(path)
                if tab_label == "终稿":
                    chapter_widget: QWidget = self._make_final_revision_view(
                        project_dir=layout.root,
                        chapter_path=path,
                        chapter_number=ch_num,
                        title=ch_title,
                        text=text,
                    )
                else:
                    chapter_widget = render_chapter_prose(text, chapter_num=ch_num, title=ch_title)
                tabs.addTab(chapter_widget, tab_label)
            # If the final chapter was empty/short, default to the last
            # (most-edited) draft tab instead of showing a blank page.
            if final_empty and len(versions) > 1:
                best_tab = len(versions) - 1
            tabs.setCurrentIndex(best_tab)
            host_layout.addWidget(tabs, 1)

    def _make_final_revision_view(
        self,
        *,
        project_dir: Path,
        chapter_path: Path,
        chapter_number: int,
        title: str,
        text: str,
    ) -> FinalRevisionWidget:
        widget = FinalRevisionWidget(
            project_id=self._current_project_id or project_dir.name,
            project_dir=project_dir,
            chapter_path=chapter_path,
            chapter_number=chapter_number,
            title=title,
            initial_text=text,
        )
        widget.saved.connect(self.context_changed.emit)
        widget.workspace_refresh_requested.connect(self.workspace_refresh_requested)
        self._revision_widgets.append(widget)
        saved_state = self._revision_view_states.get(widget.view_state_key())
        if saved_state:
            widget.restore_view_state(saved_state)
        return widget

    def _capture_revision_view_states(self) -> None:
        for widget in list(self._revision_widgets):
            try:
                self._revision_view_states[widget.view_state_key()] = widget.view_state()
            except (RuntimeError, TypeError, AttributeError):
                continue

    def _fill_chapter_reports(
        self,
        ch_num: int,
        host_layout: QVBoxLayout,
        project_dir: Path,
    ) -> None:
        ch = f"{ch_num:03d}"
        tabs = self._configure_reader_tabs(QTabWidget(), "chapterReportGroupTabs")
        has_any = False
        for group_label, report_specs in _CHAPTER_REPORT_GROUPS:
            group_tabs = self._configure_reader_tabs(QTabWidget(), "chapterReportTabs")
            group_has_any = False
            for tab_label, template in report_specs:
                path = project_dir / template.replace("{ch}", ch)
                if not path.exists():
                    continue
                widget = self._safe_smart_render_document(path)
                if widget is not None:
                    self._wire_deep_links(widget)
                    group_tabs.addTab(widget, tab_label)
                else:
                    group_tabs.addTab(self._make_content_view(path), tab_label)
                group_has_any = True
            if group_has_any:
                tabs.addTab(group_tabs, group_label)
                has_any = True
            else:
                group_tabs.deleteLater()

        if has_any:
            host_layout.addWidget(tabs, 1)
        else:
            label = QLabel(f"第 {ch_num} 章暂无报告。\n章节流程完成后此处将展示质量评估等信息。")
            label.setObjectName("viewerHint")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            host_layout.addWidget(label, 1)

    # ── Relationship tracking tab ────────────────────────────────

    def _build_relationship_tab(self, project_dir: Path) -> QWidget:
        """Build the relationship tracking dashboard tab."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 6, 0, 0)
        page_layout.setSpacing(6)

        # ── Filter toolbar ─────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(8)

        filter_label = QLabel("角色筛选")
        filter_label.setObjectName("viewerFilterLabel")
        toolbar.addWidget(filter_label)

        filter_combo = QComboBox()
        filter_combo.setObjectName("projectSelector")  # reuse existing QSS
        filter_combo.setMinimumWidth(160)
        filter_combo.addItem("全部角色", "")
        toolbar.addWidget(filter_combo)

        refresh_btn = ActionButton("刷新", variant="secondary")
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        page_layout.addLayout(toolbar)

        # ── Content host ───────────────────────────────────────
        content_host = QVBoxLayout()
        content_host.setContentsMargins(0, 0, 0, 0)
        page_layout.addLayout(content_host, 1)

        def _has_init_relationship_matrix() -> bool:
            matrix_path = project_dir / "states" / "init_v2" / "character_relationship_matrix.json"
            try:
                payload = json.loads(matrix_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                return False
            matrix = payload.get("relationship_matrix") if isinstance(payload, dict) else None
            return isinstance(matrix, list) and bool(matrix)

        def _load_overview(filter_char: str = "") -> None:
            """Load relationship data and render into content_host."""
            # Clear previous content
            while content_host.count():
                item = content_host.takeAt(0)
                w = item.widget() if item is not None else None
                if w:
                    w.deleteLater()

            try:
                from novel_forge.story_kernel.relationship_tracker import (
                    build_relationship_overview_sync,
                )

                overview = build_relationship_overview_sync(project_dir)
            except Exception:
                hint = QLabel("关系数据加载失败。\n完成章节创作后此处将展示角色关系图谱。")
                hint.setObjectName("viewerHint")
                hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
                hint.setWordWrap(True)
                content_host.addWidget(hint, 1)
                return

            if overview.total_relationships == 0:
                if _has_init_relationship_matrix():
                    text = (
                        "暂未追踪到章节关系变化。\n"
                        "初始化角色关系矩阵已生成，可在「角色设定」中查看基础人物关系。"
                    )
                else:
                    text = "暂未追踪到角色关系。\n完成章节创作后，角色之间的关系变化将在此展示。"
                hint = QLabel(text)
                hint.setObjectName("viewerHint")
                hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
                hint.setWordWrap(True)
                content_host.addWidget(hint, 1)
                return

            # Populate character filter combo (only on first load / refresh)
            current_items = {filter_combo.itemData(i) for i in range(filter_combo.count())}
            all_chars: set[str] = set()
            for tl in overview.timelines:
                if tl.character_a:
                    all_chars.add(tl.character_a)
                if tl.character_b:
                    all_chars.add(tl.character_b)
            for name in sorted(all_chars):
                if name not in current_items:
                    filter_combo.addItem(name, name)

            browser = render_relationship_overview(
                overview,
                filter_character=filter_char,
            )
            content_host.addWidget(browser, 1)

        def _on_filter_changed(index: int) -> None:
            char = filter_combo.itemData(index) or ""
            _load_overview(char)

        filter_combo.currentIndexChanged.connect(_on_filter_changed)
        refresh_btn.clicked.connect(lambda: _load_overview(filter_combo.currentData() or ""))

        # Initial load
        _load_overview()

        return page

    # ── Shared helpers ───────────────────────────────────────────

    @staticmethod
    def _make_content_view(path: Path, *, prose: bool = False) -> QWidget:
        """Return a viewer for *path*.

        - JSON files → RichDocumentViewer wrapping a generic card view
        - prose 模式 → QTextEdit plain text
        - other → QTextEdit plain text
        """
        if prose:
            view = QTextEdit()
            view.setObjectName("docViewerProse")
            view.setReadOnly(True)
            view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            view.setPlainText(read_artifact_text(path))
            return view

        raw_text = smart_artifact_content(path)

        if path.suffix == ".json":
            body = QTextBrowser()
            body.setObjectName("docViewerContent")
            body.setOpenExternalLinks(False)
            body.document().setDefaultStyleSheet(_get_viewer_default_qss())
            update_browser_html(body, ProjectsPage._render_generic_json_card(path))
            return RichDocumentViewer(
                body,
                file_path=path,
                title=path.name,
                raw_text=raw_text,
                theme_html_factory=lambda document_path=path: ProjectsPage._render_generic_json_card(
                    document_path
                ),
            )

        view = QTextEdit()
        view.setObjectName("docViewerContent")
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        view.setPlainText(raw_text)
        return view

    @staticmethod
    def _render_generic_json_card(path: Path) -> str:
        """Render any JSON file as a generic grouped card view.

        Layout:
        - 元信息（id / schema_version / created_at / updated_at / version）
        - 顶层字符串 / 数值（kv-row）
        - 顶层数组（tag-row）
        - 顶层嵌套对象（details 折叠）
        - 原始 JSON（details 折叠）

        Top-level non-dict JSON falls back to a plain <pre> dump.
        """
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            from html import escape as _esc

            return f"<pre class='json-pre'>{_esc(raw)}</pre>"

        from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
        from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

        title = path.stem

        if not isinstance(data, dict):
            return (
                "<h2>JSON 数据</h2>"
                f"<pre class='json-pre'>{_esc(json.dumps(data, indent=2, ensure_ascii=False))}</pre>"
            )

        META_KEYS = {"id", "schema_version", "created_at", "updated_at", "version"}

        meta: list[tuple[str, Any]] = []
        scalars: list[tuple[str, Any]] = []
        lists: list[tuple[str, Any]] = []
        nested: list[tuple[str, Any]] = []

        for k, v in data.items():
            if k in META_KEYS:
                meta.append((k, v))
            elif isinstance(v, (str, int, float, bool)) or v is None:
                scalars.append((k, v))
            elif isinstance(v, list):
                lists.append((k, v))
            elif isinstance(v, dict):
                nested.append((k, v))

        parts: list[str] = [f"<h2>{_esc(title)}</h2>"]

        if meta:
            parts.append("<h3>元信息</h3>")
            for k, v in meta:
                parts.append(
                    f'<div class="kv-row"><span class="kv-label">{_esc(k)}</span>'
                    f'<span class="kv-value">{_esc(str(v))}</span></div>'
                )

        for k, v in scalars:
            if isinstance(v, str) and len(v) > 40:
                parts.append(f"<h3>{_esc(k)}</h3>")
                parts.append(f'<div class="hint-block">{_nl2br(v)}</div>')
            else:
                parts.append(
                    f'<div class="kv-row"><span class="kv-label">{_esc(k)}</span>'
                    f'<span class="kv-value">{_esc(str(v))}</span></div>'
                )

        for k, v in lists:
            parts.append(f"<h3>{_esc(k)}</h3>")
            if not v:
                parts.append('<div class="hint-block">（空列表）</div>')
                continue
            tags = "".join(f'<span class="tag">{_esc(str(item)[:80])}</span>' for item in v[:50])
            parts.append(f'<div class="tag-row">{tags}</div>')
            if len(v) > 50:
                parts.append(f'<div class="field-hint">…及其他 {len(v) - 50} 项</div>')

        for k, v in nested:
            if len(v) > 200:
                parts.append(
                    f"<details><summary><b>{_esc(k)}</b> "
                    f"（{len(v)} 个字段，已折叠）</summary>"
                    f'<pre class="json-pre">{_esc(json.dumps(v, indent=2, ensure_ascii=False))}</pre>'
                    "</details>"
                )
            else:
                parts.append(
                    f"<details open><summary><b>{_esc(k)}</b></summary>"
                    f'<pre class="json-pre">{_esc(json.dumps(v, indent=2, ensure_ascii=False))}</pre>'
                    "</details>"
                )

        parts.append(
            "<details><summary><b>原始 JSON</b></summary>"
            f'<pre class="json-pre">{_esc(json.dumps(data, indent=2, ensure_ascii=False))}</pre>'
            "</details>"
        )

        return "\n".join(parts)

    def _show_empty_hint(self, message: str) -> None:
        clear_layout(self._content_layout)
        label = QLabel(message)
        label.setObjectName("viewerHint")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self._content_layout.addWidget(label, 1)

    def _show_loading_state(self) -> None:
        """Display the shared, animated placeholder during a staged rebuild."""
        clear_layout(self._content_layout)
        loading = LoadingState(
            "正在整理卷帙…",
            "先准备目录与资料，随后一次性呈现当前卷帙。",
            card_count=2,
            parent=self._content_host,
        )
        self._content_layout.addWidget(loading, 1)

    def refresh_theme_colors(self) -> None:
        """Re-apply theme-dependent viewer stylesheets after a theme switch."""
        from novel_forge.desktop.components.rich_document_viewer import RichDocumentViewer

        viewer_qss = _get_viewer_default_qss()
        for viewer in self.findChildren(RichDocumentViewer):
            viewer.refresh_theme_colors(viewer_qss)

    def _emit_compose(self) -> None:
        if not self._snapshot or not self._current_project_id:
            return
        item = next(
            (p for p in self._snapshot.projects if p.project_id == self._current_project_id),
            None,
        )
        if item:
            self.compose_requested.emit(item.project_id, item.next_chapter or 1)
