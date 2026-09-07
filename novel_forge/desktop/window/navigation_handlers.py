"""Mixin module: navigation_handlers methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QUrl,
)
from PySide6.QtGui import QDesktopServices

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.widgets import (
    show_info_message,
    show_warning_message,
)

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD

if TYPE_CHECKING:
    pass


class NavigationHandlersMixin:
    """Mixin that contributes the **navigation_handlers** method group."""

    def _view_project_in_reader(self, project_id: str, focus_target: str = "") -> None:
        """Navigate to the 卷帙 page and load a specific project for reading."""
        target = f"projects:project:{project_id}"
        if focus_target:
            target = f"{target}:{focus_target}"
        # Project loading is applied by the post-switch sequencer after the
        # lightweight reader shell is visible and its workspace is bound.
        self.switch_page(target)

    def _on_open_blueprint(self, project_id: str) -> None:
        try:
            self._view_project_in_reader(project_id, "blueprint")
        except (KeyError, RuntimeError) as exc:
            _logger.debug("open_blueprint routing failed: %s", exc)

    def _on_open_graph(self, project_id: str) -> None:
        try:
            self._view_project_in_reader(project_id, "relationships")
        except (KeyError, RuntimeError) as exc:
            _logger.debug("open_graph routing failed: %s", exc)

    def _on_open_profile(self, project_id: str) -> None:
        try:
            self._view_project_in_reader(project_id, "characters")
        except (KeyError, RuntimeError) as exc:
            _logger.debug("open_profile routing failed: %s", exc)

    def _on_open_book_consistency(self, project_id: str) -> None:
        try:
            self._view_project_in_reader(project_id, "consistency")
        except (KeyError, RuntimeError) as exc:
            _logger.debug("open_book_consistency routing failed: %s", exc)

    def _open_storage_root(self) -> None:
        if self._snapshot is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._snapshot.storage_root)))

    def _import_profiles_config(self) -> None:
        """Import model_profiles.json from a user-selected file."""
        import shutil
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        from novel_forge.gateway.profiles import get_profiles_path

        src, _ = QFileDialog.getOpenFileName(
            self, "导入配置文件", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not src:
            return
        src_path = Path(src)
        try:
            with src_path.open(encoding="utf-8") as f:
                json.load(f)  # 验证是合法 JSON
        except Exception as exc:
            show_warning_message(self, "导入失败", f"所选文件不是有效的 JSON：{exc}")
            return
        dst = get_profiles_path().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst)
        show_info_message(self, "导入成功", f"配置文件已从\n{src_path}\n导入，重载设置后生效。")
        self._pages["settings"].reload_config()

    def _export_profiles_config(self) -> None:
        """Export model_profiles.json to a user-selected path."""

        from PySide6.QtWidgets import QFileDialog

        from novel_forge.gateway.profiles import get_profiles_path

        src = get_profiles_path().resolve()
        if not src.is_file():
            show_info_message(self, "提示", "配置文件尚未创建，请先保存设置后再导出。")
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "导出配置文件", "model_profiles.json", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not dst:
            return
        import shutil

        shutil.copy2(src, Path(dst))
        show_info_message(self, "导出成功", f"配置文件已导出到\n{dst}")

    def _open_project_folder(self, project_id: str) -> None:
        if not project_id.strip():
            show_warning_message(self, "提示", "尚无可展卷项目，请先在机杼页开卷。")
            return
        if self._snapshot is None:
            return
        path = self._snapshot.storage_root / project_id
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_voice_studio_project_selected(self, project_id: str) -> None:
        """Handle project selection from the voice studio top-bar dropdown."""
        if not project_id or self._snapshot is None:
            return
        from novel_forge.persistence.models import ProjectLayout

        page = self._pages.get("voice_studio")
        if page is None or not hasattr(page, "set_project"):
            return
        layout = ProjectLayout(self._snapshot.storage_root / project_id)
        page.set_project(project_id, layout)

    def _handle_delete_project(self, project_id: str) -> None:
        # RuntimeServices may still be building on the ui_io pool (B2 async
        # init). Without a workspace we cannot delete the project.
        if self._workspace is None:
            return
        success = self._workspace.delete_project(project_id)
        if success:
            # 清理 UI 缓存中残留的内存状态（母题、摘要等），避免重建同名项目时显示旧数据
            studio_page = self._pages.get("chapter_studio")
            if studio_page is not None:
                studio_page.clear_memory_status(project_id)
                if self._chapter_studio_project_id == project_id:
                    self._chapter_studio_project_id = ""
                self._chapter_studio_chapter_numbers.pop(project_id, None)
                self._autorun_chapter_numbers.pop(project_id, None)
                if hasattr(studio_page, "forget_project"):
                    studio_page.forget_project(project_id)
            self.show_priority_status(f"已删除项目：{project_id}", 4000, self._STATUS_INFO)
            self.refresh_workspace(force=True)
        else:
            show_warning_message(self, "删除失败", f"找不到项目目录：{project_id}")
