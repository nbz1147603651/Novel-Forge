"""Artifact tab rendering helpers for the chapter studio page."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QLabel,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.skeleton import create_skeleton_card_with_lines
from novel_forge.desktop.document_presenter import read_artifact_text, smart_artifact_content
from novel_forge.desktop.pages.document_renderers import (
    render_generic_report,
    smart_render_document,
)
from novel_forge.desktop.workspace import DesktopWorkspaceSnapshot
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot


class ChapterStudioArtifactPresenter:
    """Owns artifact tab rendering and tab/scroll restoration for chapter studio."""

    def __init__(self, tabs: QTabWidget) -> None:
        self._tabs = tabs
        self._fingerprint: frozenset[tuple[str, str, int, int]] = frozenset()
        self._studio_key: tuple[str, int] | None = None
        self._skeleton_widget: QWidget | None = None

    def render(
        self,
        *,
        workspace: DesktopWorkspaceSnapshot | None,
        studio: ChapterWorkspaceSnapshot | None,
    ) -> None:
        old_tab_text, old_scroll_value = self._capture_current_view_state()
        new_fingerprint, studio_key = self._build_fingerprint(
            workspace=workspace,
            studio=studio,
        )

        if (
            new_fingerprint == self._fingerprint
            and studio_key == self._studio_key
            and self._tabs.count() > 0
        ):
            return

        self._fingerprint = new_fingerprint
        self._studio_key = studio_key

        self._tabs.clear()
        if studio is None or workspace is None:
            return

        for artifact in studio.artifacts:
            path = workspace.storage_root / studio.project_id / artifact.relative_path
            if not path.exists():
                continue

            if artifact.kind == "json":
                rich_widget = smart_render_document(path)
                if rich_widget is not None:
                    self._tabs.addTab(rich_widget, artifact.label)
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        widget = render_generic_report(data, title=artifact.label)
                        self._tabs.addTab(widget, artifact.label)
                        continue
                except (OSError, json.JSONDecodeError, ValueError):
                    pass

            if artifact.kind == "markdown":
                viewer = QTextEdit()
                viewer.setReadOnly(True)
                viewer.setObjectName("artifactContent")
                viewer.setMarkdown(read_artifact_text(path))
                self._tabs.addTab(viewer, artifact.label)
                continue

            viewer = QTextEdit()
            viewer.setReadOnly(True)
            viewer.setObjectName("artifactContent")
            content = (
                smart_artifact_content(path)
                if artifact.kind == "json"
                else read_artifact_text(path)
            )
            viewer.setPlainText(content)
            self._tabs.addTab(viewer, artifact.label)

        if self._tabs.count() == 0:
            if studio is not None and workspace is not None:
                self._render_loading_skeleton()
            else:
                self._render_empty_state()
            return

        if old_tab_text:
            for index in range(self._tabs.count()):
                if self._tabs.tabText(index) == old_tab_text:
                    self._tabs.setCurrentIndex(index)
                    break

        if old_scroll_value > 0:
            QTimer.singleShot(0, lambda value=old_scroll_value: self._restore_scroll(value))

    def _capture_current_view_state(self) -> tuple[str | None, int]:
        old_tab_text: str | None = None
        old_scroll_value = 0
        if self._tabs.currentIndex() >= 0:
            old_tab_text = self._tabs.tabText(self._tabs.currentIndex())
            current_widget = self._tabs.currentWidget()
            if isinstance(current_widget, QAbstractScrollArea):
                old_scroll_value = current_widget.verticalScrollBar().value()
        return old_tab_text, old_scroll_value

    @staticmethod
    def _build_fingerprint(
        *,
        workspace: DesktopWorkspaceSnapshot | None,
        studio: ChapterWorkspaceSnapshot | None,
    ) -> tuple[frozenset[tuple[str, str, int, int]], tuple[str, int] | None]:
        studio_key: tuple[str, int] | None = (
            (studio.project_id, studio.chapter_number) if studio is not None else None
        )
        if studio is None or workspace is None:
            return frozenset(), studio_key

        entries: list[tuple[str, str, int, int]] = []
        for artifact in studio.artifacts:
            path = workspace.storage_root / studio.project_id / artifact.relative_path
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                (
                    artifact.label,
                    artifact.relative_path,
                    stat.st_mtime_ns,
                    stat.st_size,
                )
            )
        fingerprint = frozenset(entries)
        return fingerprint, studio_key

    def _render_loading_skeleton(self) -> None:
        card = create_skeleton_card_with_lines(line_count=3)
        card.start_pulse()
        for child in card.findChildren(QWidget):
            if hasattr(child, "start_pulse"):
                child.start_pulse()
        self._skeleton_widget = card
        self._tabs.addTab(card, "（载入中…）")

    def _render_empty_state(self) -> None:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel("本章尚无产物，完成章节流程后将自动显示。")
        label.setObjectName("fieldHint")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        self._tabs.addTab(placeholder, "（暂无）")

    def _restore_scroll(self, value: int) -> None:
        current_widget = self._tabs.currentWidget()
        if isinstance(current_widget, QAbstractScrollArea):
            current_widget.verticalScrollBar().setValue(value)
