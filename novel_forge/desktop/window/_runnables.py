"""QRunnable workers for background workspace refresh operations.

Extracted from ``window.py`` to reduce its size. These BaseJobWorker subclasses
perform file-system reads in QThreadPool worker threads to avoid blocking
the GUI event loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal

from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.desktop.workspace_snapshot_hash import (
    build_snapshot_payload_static as _snapshot_build_payload_static,
)
from novel_forge.desktop.workspace_snapshot_hash import (
    compute_section_hashes as _snapshot_compute_section_hashes,
)
from novel_forge.persistence.models import ProjectLayout

if TYPE_CHECKING:
    from novel_forge.desktop.workspace import DesktopWorkspaceService

_logger = __import__("logging").getLogger(__name__)


class _RefreshSignals(BaseJobWorkerSignals):
    """Signals carrier for _WorkspaceRefreshRunnable."""

    finished = Signal(object, object)
    refresh_ready = Signal(object, object, object, object, object, object)
    failed = Signal(str)


class _RuntimeServicesInitSignals(BaseJobWorkerSignals):
    """Signals carrier for _RuntimeServicesInitRunnable."""

    services_ready = Signal(object)
    failed = Signal(str)


class _RuntimeServicesInitRunnable(BaseJobWorker):
    """Build ``DesktopWorkspaceService.from_settings()`` off the GUI thread.

    Construction pulls in the full model router (all configured provider
    adapters), parses ``model_profiles.json``, builds the PromptBuilder /
    PromptRegistry, and captures the runtime config snapshot -- roughly 400ms
    of work that previously blocked the desktop startup sequence on the GUI
    thread. Running it on the ui_io pool lets the skeleton overlay render
    immediately and the window appear while the heavy work proceeds in the
    background.
    """

    pool = "aux"

    def __init__(self, *, mock: bool = False) -> None:
        super().__init__()
        self._mock = mock
        self.signals = _RuntimeServicesInitSignals()

    def run(self) -> None:  # noqa: D401 - override BaseJobWorker.run() for sync I/O
        """Run synchronously in pool thread - signals are delivered directly."""
        try:
            from novel_forge.desktop.workspace import DesktopWorkspaceService

            workspace = DesktopWorkspaceService.from_settings(mock=self._mock)
            try:
                self.signals.services_ready.emit(workspace)
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise
        except Exception as exc:
            _logger.warning("RuntimeServices 后台初始化失败: %s", exc, exc_info=True)
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise


class _ContextRefreshSignals(BaseJobWorkerSignals):
    """Signals carrier for _ChapterContextRefreshRunnable."""

    finished = Signal(object)
    failed = Signal(str, int)


class _ChapterContextRefreshRunnable(BaseJobWorker):
    """Read chapter workspace snapshot in a QThreadPool worker thread.

    ``get_chapter_workspace_snapshot`` performs ~12 JSON reads from disk which
    blocks the GUI event loop for 50-200 ms per call.  During auto-pilot this
    is called on every chapter transition and every context refresh, causing
    visible stutter and delayed decision scheduling.
    """

    pool = "aux"

    _CACHE_MAX_ENTRIES = 64
    _cache_lock = threading.RLock()
    _snapshot_cache: dict[
        tuple[str, int],
        tuple[tuple[tuple[str, int, int], ...], Any],
    ] = {}

    def __init__(
        self,
        workspace: "DesktopWorkspaceService",
        project_id: str,
        chapter_number: int,
        project_detail: Any | None,
    ) -> None:
        super().__init__()
        self._workspace = workspace
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._project_detail = project_detail
        self.signals = _ContextRefreshSignals()

    @staticmethod
    def _path_stamp(path: Path) -> tuple[str, int, int]:
        try:
            stat = path.stat()
            return (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), 0, -1)

    def _snapshot_fingerprint(
        self, storage_root_path: Any
    ) -> tuple[tuple[str, int, int], ...] | None:
        if storage_root_path is None:
            return None
        layout = ProjectLayout(Path(storage_root_path) / self._project_id)
        chapter_number = self._chapter_number
        paths = [
            layout.outline_path,
            layout.chapter_path(chapter_number),
            layout.chapter_review_draft_path(chapter_number),
            layout.chapter_path(chapter_number - 1) if chapter_number > 1 else None,
            layout.chapter_state_packet_path(chapter_number),
            layout.chapter_bridge_path(chapter_number),
            layout.chapter_plan_path(chapter_number),
            layout.chapter_checkpoint_path(chapter_number),
            layout.chapter_session_path(chapter_number),
            layout.chapter_review_progress_path(chapter_number),
            layout.chapter_exit_state_path(chapter_number - 1) if chapter_number > 1 else None,
            layout.alignment_report_path(chapter_number),
            layout.eval_report_path(chapter_number),
            layout.continuity_report_path(chapter_number),
            layout.chapter_causal_report_path(chapter_number),
            layout.reading_power_report_path(chapter_number),
            layout.guard_report_path(chapter_number),
            layout.chapter_memory_diagnostics_path(chapter_number),
            layout.creative_report_path(chapter_number - 1) if chapter_number > 1 else None,
            layout.canon_dir / "canon_current.json",
        ]
        return tuple(self._path_stamp(path) for path in paths if path is not None)

    @classmethod
    def _cached_snapshot(
        cls,
        cache_key: tuple[str, int],
        fingerprint: tuple[tuple[str, int, int], ...],
    ) -> Any | None:
        with cls._cache_lock:
            cached = cls._snapshot_cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
        return None

    @classmethod
    def _remember_snapshot(
        cls,
        cache_key: tuple[str, int],
        fingerprint: tuple[tuple[str, int, int], ...],
        snapshot: Any,
    ) -> None:
        with cls._cache_lock:
            cls._snapshot_cache[cache_key] = (fingerprint, snapshot)
            while len(cls._snapshot_cache) > cls._CACHE_MAX_ENTRIES:
                cls._snapshot_cache.pop(next(iter(cls._snapshot_cache)))

    def run(self) -> None:  # noqa: D401 — override BaseJobWorker.run() for sync I/O
        """Run synchronously in pool thread — signals are delivered directly."""
        try:
            cache_key = (self._project_id, self._chapter_number)
            storage_root = getattr(getattr(self._workspace, "runtime", None), "storage", None)
            storage_root_path = getattr(storage_root, "root", None)
            fingerprint = self._snapshot_fingerprint(storage_root_path)
            snapshot = (
                self._cached_snapshot(cache_key, fingerprint) if fingerprint is not None else None
            )
            if snapshot is None:
                try:
                    snapshot = self._workspace.get_chapter_workspace_snapshot(
                        self._project_id,
                        self._chapter_number,
                        project_detail=self._project_detail,
                    )
                except TypeError:
                    snapshot = self._workspace.get_chapter_workspace_snapshot(
                        self._project_id,
                        self._chapter_number,
                    )
                if fingerprint is not None:
                    self._remember_snapshot(cache_key, fingerprint, snapshot)
            try:
                self.signals.finished.emit(snapshot)
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise
        except Exception:
            _logger.exception(
                "章节上下文刷新失败: project=%s chapter=%s",
                self._project_id,
                self._chapter_number,
            )
            try:
                self.signals.failed.emit(self._project_id, self._chapter_number)
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise


class _WorkspaceRefreshRunnable(BaseJobWorker):
    """Run build_snapshot() + payload/hash in a QThreadPool worker thread.

    The *service* object must be safe to call from a non-GUI thread.  Because
    DesktopWorkspaceService only performs file-system reads it satisfies this
    requirement.  Results are delivered back to the main thread via Qt signals.

    Payload building and section hashing are computed **inside** the background
    thread (not on the main thread) to keep the GUI responsive.
    """

    pool = "aux"

    def __init__(
        self,
        service: "DesktopWorkspaceService",
        reload_from_settings: bool,
        mock_enabled: bool,
        prev_payload: dict[str, Any] | None = None,
        prev_section_hash_cache: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._reload = reload_from_settings
        self._mock = mock_enabled
        self._prev_payload = prev_payload
        self._prev_section_hash_cache = prev_section_hash_cache
        self.signals = _RefreshSignals()

    def run(self) -> None:  # noqa: D401 — override BaseJobWorker.run() for sync I/O
        """Run synchronously in pool thread — signals are delivered directly."""
        try:
            if self._reload:
                from novel_forge.desktop.workspace import DesktopWorkspaceService

                self._service = DesktopWorkspaceService.from_settings(mock=self._mock)
            snapshot = self._service.build_snapshot()
            payload = _snapshot_build_payload_static(snapshot)
            section_hashes, changed_sections, final_hash = _snapshot_compute_section_hashes(
                snapshot,
                payload,
                self._prev_payload,
                self._prev_section_hash_cache,
            )
            try:
                self.signals.refresh_ready.emit(
                    snapshot,
                    self._service,
                    payload,
                    section_hashes,
                    changed_sections,
                    final_hash,
                )
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise
        except Exception as exc:
            _logger.warning("工作区后台刷新失败: %s", exc, exc_info=True)
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError as emit_exc:
                if "Signal source has been deleted" in str(emit_exc):
                    return
                raise
