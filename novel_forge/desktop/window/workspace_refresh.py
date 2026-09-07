"""Mixin module: workspace_refresh methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    REFRESH_DEBOUNCE_MS,
)
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.workspace import DesktopWorkspaceService, DesktopWorkspaceSnapshot
from novel_forge.desktop.workspace_snapshot_hash import (
    build_snapshot_payload_static as _snapshot_build_payload_static,
)
from novel_forge.desktop.workspace_snapshot_hash import (
    compute_section_hashes as _snapshot_compute_section_hashes,
)
from novel_forge.desktop.workspace_snapshot_hash import (
    stable_snapshot_json as _snapshot_stable_json,
)
from novel_forge.desktop.workspace_snapshot_hash import (
    stable_snapshot_value as _snapshot_stable_value,
)
from novel_forge.persistence.filesystem import atomic_write_text

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = 1180
_WINDOW_DEFAULT_MIN_HEIGHT = 760
_WINDOW_DEFAULT_START_WIDTH = 1440
_WINDOW_DEFAULT_START_HEIGHT = 900
_WINDOW_SCREEN_WIDTH_RATIO = 0.92
_WINDOW_SCREEN_HEIGHT_RATIO = 0.90
_COMPACT_WIDTH_THRESHOLD = 1360
_COMPACT_HEIGHT_THRESHOLD = 820

if TYPE_CHECKING:
    pass


from novel_forge.desktop.window._runnables import (  # noqa: E402
    _WorkspaceRefreshRunnable,
)


def _desktop_thread_pools() -> Any:
    import novel_forge.desktop.window as window_facade

    return window_facade.desktop_thread_pools()


class WorkspaceRefreshMixin:
    """Mixin that contributes the **workspace_refresh** method group."""

    # Voice Studio creates several tabs, text browsers, audio controls and
    # provider settings in one pass.  Restoring it synchronously from the
    # first workspace refresh blocks the first frame for hundreds of ms on
    # slower machines.  Restore it only after the shell has had a chance to
    # paint; any user navigation in the meantime cancels the restore.
    _DEFERRED_SESSION_PAGE_RESTORE_MS = 750
    _HEAVY_SESSION_PAGES = frozenset({"voice_studio"})

    @staticmethod
    def _normalize_refresh_section_hint(section: str) -> str:
        normalized = str(section or "").strip()
        if normalized == "chapters":
            return "details"
        return normalized

    def _handle_job_section_changed(self, project_id: str, section: str) -> None:
        self._schedule_workspace_refresh(section, project_id=project_id, reason="job_section")

    def _schedule_workspace_refresh(
        self,
        section_hint: str,
        *,
        project_id: str = "",
        reason: str = "",
    ) -> None:
        """Schedule a fresh workspace snapshot for a changed section."""
        section = self._normalize_refresh_section_hint(section_hint)
        if not section:
            return
        if section == "jobs":
            self._schedule_bind_jobs()
            return
        self._pending_refresh_section_hints.add(section)
        _logger.debug(
            "Scheduling workspace refresh | section=%s | project=%s | reason=%s",
            section,
            project_id,
            reason,
        )
        if not self._workspace_refresh_schedule_timer.isActive():
            self._workspace_refresh_schedule_timer.start()

    def refresh_workspace(self, *, force: bool = True) -> None:
        # RuntimeServices may still be building on the ui_io pool (B2 async
        # init). Skip the refresh; the skeleton overlay stays visible and
        # _on_runtime_services_ready will trigger the first refresh once the
        # workspace is populated.
        if self._workspace is None:
            return
        # Skip refresh when the window is minimized to avoid pointless disk I/O.
        if self.isMinimized():
            if force:
                self._refresh_force_pending = True
            return
        # After system wake, suppress non-forced refreshes during the recovery
        # window to avoid hammering a slow/cold filesystem.  The wake handler
        # schedules a single forced refresh after the window expires.
        if not force and self._sleep_wake_monitor.in_recovery_window:
            return

        now = time.monotonic()

        # Throttle force refreshes arriving right after a cycle completed.
        _FORCE_REFRESH_THROTTLE_MS = 500
        if force and self._last_refresh_complete_time > 0:
            since_complete_ms = (now - self._last_refresh_complete_time) * 1000
            if since_complete_ms < _FORCE_REFRESH_THROTTLE_MS:
                self._refresh_force_pending = True
                delay_ms = max(1, int(_FORCE_REFRESH_THROTTLE_MS - since_complete_ms))
                self._safe_deferred(delay_ms, lambda: self.refresh_workspace(force=True))
                return

        # Debounce: skip if called too soon after the last refresh.
        elapsed_ms = (now - self._last_refresh_time) * 1000
        if not force and self._last_refresh_time > 0 and elapsed_ms < REFRESH_DEBOUNCE_MS:
            return

        # Prevent queuing multiple concurrent refreshes; the periodic timer
        # fires frequently enough that a slow I/O pass could overlap itself.
        if self._refresh_in_progress:
            if force:
                self._refresh_force_pending = True
            return
        self._refresh_in_progress = True
        self._refresh_force_current = force or self._refresh_force_pending
        self._refresh_force_pending = False
        self._refresh_section_hints_current = set(self._pending_refresh_section_hints)
        self._pending_refresh_section_hints.clear()
        self._last_refresh_time = now
        self._refresh_count += 1

        reload = self._workspace_needs_reload
        if reload:
            self._workspace_needs_reload = False
            self._refresh_force_current = True

        worker = _WorkspaceRefreshRunnable(
            self._workspace,
            reload,
            self._mock_enabled,
            prev_payload=self._last_snapshot_payload,
            prev_section_hash_cache=dict(self._section_hash_cache),
        )
        self._active_workspace_refresh_worker = worker
        worker.signals.refresh_ready.connect(self._on_workspace_refreshed)
        worker.signals.failed.connect(self._on_workspace_refresh_failed)
        _desktop_thread_pools().ui_io_pool.start(worker)

    def _on_workspace_refresh_failed(self, message: str) -> None:
        """Called on main thread when the background refresh raised an exception."""
        self._active_workspace_refresh_worker = None
        self._refresh_in_progress = False
        self._last_refresh_complete_time = time.monotonic()
        self._refresh_force_current = False
        self._refresh_section_hints_current.clear()
        self._hide_skeleton_overlay()
        _logger.warning("工作区刷新失败（后台）: %s", message)

    def _on_workspace_refreshed(
        self,
        snapshot: DesktopWorkspaceSnapshot,
        service: DesktopWorkspaceService,
        payload: dict[str, Any],
        section_hashes: dict[str, str],
        changed_sections: set[str],
        snapshot_hash: str,
    ) -> None:
        # Phase M6 guard: defer heavy widget ops during macOS fullscreen to
        # avoid driving AppKit out of Spaces. The check reuses the existing
        # _skip_page_motion_for_window_state() which handles macOS native
        # fullscreen, windowState, visibility, and frameGeometry.
        if sys.platform == "darwin" and self._skip_page_motion_for_window_state():
            self._safe_deferred(
                0,
                lambda: self._apply_workspace_refresh(
                    snapshot,
                    service,
                    payload,
                    section_hashes,
                    changed_sections,
                    snapshot_hash,
                ),
            )
            return
        self._apply_workspace_refresh(
            snapshot,
            service,
            payload,
            section_hashes,
            changed_sections,
            snapshot_hash,
        )

    def _build_snapshot_payload(self, snapshot: DesktopWorkspaceSnapshot) -> dict[str, Any]:
        cached = self._last_payload_values
        refs = self._last_payload_refs
        result: dict[str, Any] = {}

        sr = snapshot.storage_root
        result["storage_root"] = (
            cached["storage_root"]
            if refs.get("storage_root") is sr and "storage_root" in cached
            else str(sr)
        )

        dp = snapshot.default_provider
        result["default_provider"] = (
            cached["default_provider"]
            if refs.get("default_provider") is dp and "default_provider" in cached
            else dp
        )

        for section_name in ("overview", "metrics", "providers", "projects"):
            attr = getattr(snapshot, section_name)
            if refs.get(section_name) is attr and section_name in cached:
                result[section_name] = cached[section_name]
            else:
                result[section_name] = self._stable_snapshot_value(attr)

        fp = snapshot.featured_project
        if refs.get("featured_project") is fp and "featured_project" in cached:
            result["featured_project"] = cached["featured_project"]
        else:
            result["featured_project"] = self._stable_snapshot_value(fp)

        last_detail_ids = self._last_detail_ids
        new_detail_ids: dict[str, int] = {}
        details_payload: dict[str, Any] = {}
        cached_details = cached.get("details", {})
        for project_id, detail in sorted(snapshot.details.items()):
            detail_id = id(detail.index)
            new_detail_ids[project_id] = detail_id
            if last_detail_ids.get(project_id) == detail_id and project_id in cached_details:
                details_payload[project_id] = cached_details[project_id]
            else:
                details_payload[project_id] = self._stable_snapshot_value(detail.index)
        result["details"] = details_payload
        self._last_detail_ids = new_detail_ids

        self._last_payload_values = result
        self._last_payload_refs = {
            "storage_root": sr,
            "default_provider": dp,
            "overview": snapshot.overview,
            "metrics": snapshot.metrics,
            "providers": snapshot.providers,
            "projects": snapshot.projects,
            "featured_project": fp,
        }
        return result

    def _compute_snapshot_hash_incremental(
        self, snapshot: DesktopWorkspaceSnapshot
    ) -> tuple[str, set[str], dict[str, Any]]:
        """Compute incremental hash and return hash, changed sections, and payload."""
        payload = self._build_snapshot_payload(snapshot)
        _json = self._stable_snapshot_json

        if self._last_snapshot_payload is None:
            section_hashes: dict[str, str] = {}
            base_sections = (
                "storage_root",
                "default_provider",
                "overview",
                "metrics",
                "providers",
                "projects",
                "featured_project",
            )
            for section in base_sections:
                attr = getattr(snapshot, section, None)
                section_hashes[section] = hashlib.sha256(_json(attr).encode("utf-8")).hexdigest()
            for pid, detail in sorted(snapshot.details.items()):
                key = f"details/{pid}"
                section_hashes[key] = hashlib.sha256(
                    _json(detail.index).encode("utf-8")
                ).hexdigest()
            all_keys = list(base_sections) + [f"details/{pid}" for pid in sorted(snapshot.details)]
            hash_val = hashlib.sha256(
                "|".join(section_hashes[s] for s in all_keys).encode("utf-8")
            ).hexdigest()
            self._section_hash_cache = section_hashes
            changed_sections: set[str] = set(payload.keys())
            for pid in snapshot.details:
                changed_sections.add(f"details/{pid}")
            return hash_val, changed_sections, payload

        sections = [
            "storage_root",
            "default_provider",
            "overview",
            "metrics",
            "providers",
            "projects",
            "featured_project",
        ]
        prev_payload = self._last_snapshot_payload
        section_hashes = {}
        changed_sections = set()

        for section in sections:
            current_value = payload.get(section)
            prev_value = prev_payload.get(section)

            if current_value == prev_value and section in self._section_hash_cache:
                section_hashes[section] = self._section_hash_cache[section]
            else:
                attr = getattr(snapshot, section, None)
                if attr is not None and (
                    hasattr(attr, "model_dump_json") or hasattr(attr, "__dataclass_fields__")
                ):
                    section_json = _json(attr)
                else:
                    section_json = json.dumps(
                        current_value,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                section_hashes[section] = hashlib.sha256(section_json.encode("utf-8")).hexdigest()
                if current_value != prev_value:
                    changed_sections.add(section)

        prev_details = prev_payload.get("details", {})
        cur_details = payload.get("details", {})
        for pid, detail in sorted(snapshot.details.items()):
            key = f"details/{pid}"
            cur_idx_json = _json(detail.index)
            prev_idx_json = prev_details.get(pid)
            if (
                prev_idx_json is not None
                and cur_idx_json
                == json.dumps(prev_idx_json, ensure_ascii=False, sort_keys=True, default=str)
                and key in self._section_hash_cache
            ):
                section_hashes[key] = self._section_hash_cache[key]
            else:
                section_hashes[key] = hashlib.sha256(cur_idx_json.encode("utf-8")).hexdigest()
                if cur_details.get(pid) != prev_details.get(pid):
                    changed_sections.add(key)

        all_keys = sections + [f"details/{pid}" for pid in sorted(snapshot.details)]
        combined = "|".join(section_hashes[s] for s in all_keys)
        final_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()

        # Full replacement (not .update()) so stale keys from deleted projects
        # are pruned instead of accumulating indefinitely.
        self._section_hash_cache = section_hashes
        return final_hash, changed_sections, payload

    def _compute_snapshot_hash(self, snapshot: DesktopWorkspaceSnapshot) -> str:
        """Compute a stable hash of snapshot content for change detection."""
        payload = {
            "storage_root": str(snapshot.storage_root),
            "default_provider": snapshot.default_provider,
            "overview": self._stable_snapshot_value(snapshot.overview),
            "metrics": self._stable_snapshot_value(snapshot.metrics),
            "providers": self._stable_snapshot_value(snapshot.providers),
            "projects": self._stable_snapshot_value(snapshot.projects),
            "featured_project": self._stable_snapshot_value(snapshot.featured_project),
            "details": {
                project_id: self._stable_snapshot_value(detail)
                for project_id, detail in sorted(snapshot.details.items())
            },
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_snapshot_value(value: Any) -> Any:
        """Convert dataclass and Pydantic snapshot values into JSON-stable data."""
        return _snapshot_stable_value(value)

    @staticmethod
    def _stable_snapshot_json(value: Any) -> str:
        """Return a stable JSON string for hashing."""
        return _snapshot_stable_json(value)

    @staticmethod
    def _build_snapshot_payload_static(snapshot: DesktopWorkspaceSnapshot) -> dict[str, Any]:
        """Build payload dict from snapshot without accessing mutable window state."""
        return _snapshot_build_payload_static(snapshot)

    @staticmethod
    def _compute_section_hashes(
        snapshot: DesktopWorkspaceSnapshot,
        payload: dict[str, Any],
        prev_payload: dict[str, Any] | None = None,
        prev_section_hash_cache: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], set[str], str]:
        """Compute per-section hashes, changed-section set, and combined hash.

        Thread-safe pure function — no mutable instance state accessed.
        When *prev_payload* and *prev_section_hash_cache* are provided,
        unchanged sections reuse their cached hash (incremental mode).

        Returns:
            ``(section_hashes, changed_sections, final_hash)``
        """
        return _snapshot_compute_section_hashes(
            snapshot,
            payload,
            prev_payload,
            prev_section_hash_cache,
        )

    def _update_fs_watcher_paths(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        if self._fs_watcher is None:
            return
        storage_root = snapshot.storage_root
        current_files = set(self._fs_watcher.files())
        current_dirs = set(self._fs_watcher.directories())

        desired_files: set[str] = set()
        for project in snapshot.projects:
            project_dir = storage_root / project.project_id
            for name in self._FS_WATCHED_FILE_NAMES:
                candidate = project_dir / name
                if candidate.exists():
                    desired_files.add(str(candidate))
            kernel_file = project_dir / self._FS_WATCHED_KERNEL_FILE
            if kernel_file.exists():
                desired_files.add(str(kernel_file))

        was_blocked = self._fs_watcher.signalsBlocked()
        self._fs_watcher.blockSignals(True)
        try:
            for path in current_files - desired_files:
                self._fs_watcher.removePath(path)
            for path in current_dirs - set():
                self._fs_watcher.removePath(path)
            for path in desired_files - current_files:
                self._fs_watcher.addPath(path)
        finally:
            self._fs_watcher.blockSignals(was_blocked)

    def _sync_chapter_studio_target_with_snapshot(self, snapshot: DesktopWorkspaceSnapshot) -> None:
        """Drop or retarget the cached chapter-studio project after workspace changes."""
        target_project_id = self._chapter_studio_project_id.strip()
        long_projects = [project for project in snapshot.projects if project.mode == "long"]
        long_project_ids = {project.project_id for project in long_projects}
        if target_project_id and target_project_id in long_project_ids:
            self._chapter_studio_project_id = target_project_id
            clamped = self._clamp_chapter_studio_target(
                target_project_id,
                self._get_chapter_number(target_project_id),
            )
            self._chapter_studio_project_id = clamped[0]
            self._set_chapter_number(clamped[0], clamped[1])
            return

        first = long_projects[0] if long_projects else None
        if first is not None:
            clamped = self._clamp_chapter_studio_target(first.project_id, first.next_chapter or 1)
            self._chapter_studio_project_id = clamped[0]
            self._set_chapter_number(clamped[0], clamped[1])
        else:
            self._chapter_studio_project_id = ""

    def _current_page_id(self) -> str:
        """Return the page key of the currently visible page."""
        current_widget = self._stack.currentWidget()
        if self._page_placeholders.get(self._active_page_id) is current_widget:
            return self._active_page_id
        return next(
            (pid for pid, page in self._pages.items() if page is current_widget),
            "dashboard",
        )

    @staticmethod
    def _ui_session_path() -> Path:
        """Return path to the persisted UI session file."""
        return Path.home() / ".novel_forge" / "ui_session.json"

    def _schedule_ui_session_save(self) -> None:
        """Debounce session persistence during rapid page switches."""
        if self._ui_session_save_timer.isActive():
            self._ui_session_save_timer.stop()
        self._ui_session_save_timer.start()

    def _export_page_ui_states(self) -> dict[str, dict[str, Any]]:
        """Collect JSON-safe UI selections from pages that opt into sessions.

        The explicit export/restore protocol avoids serializing arbitrary Qt
        widgets and gives each page ownership over which choices are safe to
        replay after a restart.
        """
        states: dict[str, dict[str, Any]] = {}
        for page_id, page in self._pages.items():
            if page is None:
                continue
            exporter = getattr(page, "export_ui_state", None)
            if not callable(exporter):
                continue
            try:
                payload = exporter()
                if not isinstance(payload, dict):
                    continue
                # Validate one page at a time so an extension page cannot
                # prevent the shell and other pages from saving their state.
                json.dumps(payload, ensure_ascii=False)
                states[page_id] = payload
            except Exception as exc:
                _logger.debug("导出页面 UI 状态失败 (%s): %s", page_id, exc)
        return states

    def _restore_page_ui_state(self, page_id: str, page: Any) -> None:
        """Restore pending state once when a lazily-created page is ready."""
        if page_id in self._restored_page_ui_state_ids:
            return
        payload = self._pending_page_ui_states.get(page_id)
        if not isinstance(payload, dict):
            return
        restorer = getattr(page, "restore_ui_state", None)
        if not callable(restorer):
            return
        try:
            restorer(payload)
            self._restored_page_ui_state_ids.add(page_id)
        except Exception as exc:
            _logger.debug("恢复页面 UI 状态失败 (%s): %s", page_id, exc)

    def _save_ui_session(self) -> None:
        """Write current page and chapter-studio context to disk."""
        with ui_perf_span("switch_page.session_save"):
            try:
                state = {
                    "active_page": self._current_page_id(),
                    "side_rail_collapsed": bool(getattr(self, "_side_rail_collapsed", False)),
                    "chapter_studio_project_id": self._chapter_studio_project_id,
                    "chapter_studio_chapter_numbers": self._chapter_studio_chapter_numbers,
                    "chapter_studio_chapter_number": self._get_chapter_number(
                        self._chapter_studio_project_id
                    ),
                    "page_ui_states": self._export_page_ui_states(),
                }
                path = self._ui_session_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(path, json.dumps(state, ensure_ascii=False))
            except Exception as exc:
                _logger.debug("保存 UI 会话状态失败: %s", exc)

    def _load_ui_session(self, *, restore_active_page: bool = True) -> None:
        """Restore saved UI state without overriding newer navigation."""
        try:
            path = self._ui_session_path()
            if not path.exists():
                return
            state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            raw_page_states = state.get("page_ui_states")
            if isinstance(raw_page_states, dict):
                self._pending_page_ui_states = {
                    str(page_id): payload
                    for page_id, payload in raw_page_states.items()
                    if isinstance(payload, dict)
                }
                self._restored_page_ui_state_ids.clear()
                for page_id, page_instance in self._pages.items():
                    if page_instance is not None:
                        self._restore_page_ui_state(page_id, page_instance)
            if "side_rail_collapsed" in state:
                self._set_side_rail_collapsed(
                    bool(state.get("side_rail_collapsed")),
                    animate=False,
                )
            page = state.get("active_page", "")
            if restore_active_page and page and page in self._pages:
                # Restore chapter-studio context before switching so the page
                # binds correctly if it becomes the active widget.
                project_id = str(state.get("chapter_studio_project_id") or "")
                chapter_num = int(state.get("chapter_studio_chapter_number") or 1)
                # Restore per-project chapter numbers if available, else migrate from legacy format
                saved_chapter_numbers = state.get("chapter_studio_chapter_numbers")
                if isinstance(saved_chapter_numbers, dict):
                    self._chapter_studio_chapter_numbers = {
                        str(k): int(v) for k, v in saved_chapter_numbers.items() if k
                    }
                detail = self._snapshot.details.get(project_id) if self._snapshot else None
                if project_id and detail is not None and detail.mode == "long":
                    self._chapter_studio_project_id = project_id
                    if project_id not in self._chapter_studio_chapter_numbers:
                        self._chapter_studio_chapter_numbers[project_id] = max(1, chapter_num)
                if page in self._HEAVY_SESSION_PAGES:
                    restore_generation = self._page_activate_generation

                    def _restore_heavy_session_page() -> None:
                        if restore_generation != self._page_activate_generation:
                            return
                        self.switch_page(page)

                    self._safe_deferred(
                        self._DEFERRED_SESSION_PAGE_RESTORE_MS,
                        _restore_heavy_session_page,
                    )
                else:
                    self.switch_page(page)
        except Exception as exc:
            _logger.debug("恢复 UI 会话状态失败: %s", exc)
