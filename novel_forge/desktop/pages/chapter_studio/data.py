"""Data filtering and patching logic for ChapterStudioPage.

Contains helper functions and the ChapterStudioDataMixin for:
- Outline/episodic payload filtering
- Memory file patching
- Autopilot context building
- Chapter status queries and data utilities
"""

from __future__ import annotations

import json as _json
import time
from typing import Any

from PySide6.QtWidgets import QMessageBox

from novel_forge.desktop.constants import LABEL_CHAPTER_RE as _QUESTION_CHAPTER_PATTERN
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.widgets import MessageBoxAction, show_message_box, show_warning_message
from novel_forge.workspace.memory_patcher import MemoryPatcher

from .autorun import AutoPilotContext
from .contract import ChapterStudioMixinBase
from .dialogs import CleanChaptersDialog


def _safe_chapter_number(raw: Any) -> int | None:
    """Best-effort chapter number parser used by cleanup filters."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_kept_chapter(raw: Any, from_chapter: int) -> bool:
    chapter = _safe_chapter_number(raw)
    return chapter is not None and chapter < from_chapter


def _filter_unresolved_questions(
    questions: Any,
    *,
    from_chapter: int,
) -> list[str]:
    """Drop unresolved-question entries that explicitly target invalidated chapters."""
    if not isinstance(questions, list):
        return []
    kept: list[str] = []
    for item in questions:
        text = str(item or "").strip()
        if not text:
            continue
        match = _QUESTION_CHAPTER_PATTERN.search(text)
        if match is not None:
            try:
                chapter = int(match.group(1))
            except ValueError:
                chapter = 0
            if chapter >= from_chapter:
                continue
        kept.append(text)
    return kept


def _filter_outline_payload(
    outline_payload: Any,
    *,
    from_chapter: int,
) -> dict[str, Any]:
    """Filter outline episodic payload to retain chapters < from_chapter only."""
    if not isinstance(outline_payload, dict):
        return {}

    filtered = dict(outline_payload)

    outline_index_raw = outline_payload.get("outline_index", {})
    filtered_outline_index: dict[str, Any] = {}
    if isinstance(outline_index_raw, dict):
        for sig, entry in outline_index_raw.items():
            if not isinstance(entry, dict):
                continue
            chapter = _safe_chapter_number(entry.get("chapter_number"))
            if chapter is None or chapter >= from_chapter:
                continue
            filtered_outline_index[str(sig)] = entry
    filtered["outline_index"] = filtered_outline_index

    chapter_outlines_raw = outline_payload.get("chapter_outlines", {})
    filtered_chapter_outlines: dict[str, list[str]] = {}
    if isinstance(chapter_outlines_raw, dict):
        for chapter_key, signatures in chapter_outlines_raw.items():
            if not _is_kept_chapter(chapter_key, from_chapter):
                continue
            if not isinstance(signatures, list):
                continue
            kept_sigs = [str(sig) for sig in signatures if str(sig) in filtered_outline_index]
            if kept_sigs:
                filtered_chapter_outlines[str(chapter_key)] = kept_sigs
    filtered["chapter_outlines"] = filtered_chapter_outlines

    relationships_raw = outline_payload.get("relationships", {})
    filtered_relationships: dict[str, dict[str, Any]] = {}
    if isinstance(relationships_raw, dict):
        for pair_key, changes in relationships_raw.items():
            if not isinstance(changes, dict):
                continue
            kept_changes: dict[str, Any] = {}
            for change_key, change in changes.items():
                if not isinstance(change, dict):
                    continue
                if not _is_kept_chapter(change.get("chapter"), from_chapter):
                    continue
                kept_changes[str(change_key)] = change
            if kept_changes:
                filtered_relationships[str(pair_key)] = kept_changes
    filtered["relationships"] = filtered_relationships

    theme_tracker_raw = outline_payload.get("theme_tracker", {})
    filtered_theme_tracker: dict[str, list[int]] = {}
    if isinstance(theme_tracker_raw, dict):
        for theme, chapters in theme_tracker_raw.items():
            if not isinstance(chapters, list):
                continue
            kept_chapters = [
                int(chapter) for chapter in chapters if _is_kept_chapter(chapter, from_chapter)
            ]
            if kept_chapters:
                filtered_theme_tracker[str(theme)] = kept_chapters
    filtered["theme_tracker"] = filtered_theme_tracker

    filtered["unresolved_questions"] = _filter_unresolved_questions(
        outline_payload.get("unresolved_questions", []),
        from_chapter=from_chapter,
    )

    return filtered


def _filter_episodic_index_payload(
    episodic_payload: Any,
    *,
    from_chapter: int,
) -> dict[str, Any]:
    """Filter chapter-scoped episodic index payload to retain chapters < from_chapter."""
    if not isinstance(episodic_payload, dict):
        return {}

    filtered = dict(episodic_payload)

    index_raw = episodic_payload.get("index", {})
    filtered_index: dict[str, Any] = {}
    if isinstance(index_raw, dict):
        for signature, entry in index_raw.items():
            if not isinstance(entry, dict):
                continue
            if not _is_kept_chapter(entry.get("chapter_number"), from_chapter):
                continue
            filtered_index[str(signature)] = entry
    filtered["index"] = filtered_index

    chapter_events_raw = episodic_payload.get("chapter_events", {})
    filtered_chapter_events: dict[str, list[str]] = {}
    if isinstance(chapter_events_raw, dict):
        for chapter_key, signatures in chapter_events_raw.items():
            if not _is_kept_chapter(chapter_key, from_chapter):
                continue
            if not isinstance(signatures, list):
                continue
            kept_sigs = [str(sig) for sig in signatures if str(sig) in filtered_index]
            if kept_sigs:
                filtered_chapter_events[str(chapter_key)] = kept_sigs
    filtered["chapter_events"] = filtered_chapter_events

    outline_data_raw = episodic_payload.get("outline_data", {})
    if isinstance(outline_data_raw, dict):
        filtered["outline_data"] = _filter_outline_payload(
            outline_data_raw,
            from_chapter=from_chapter,
        )

    return filtered


class ChapterStudioDataMixin(ChapterStudioMixinBase):
    """Mixin providing data filtering, patching, and autopilot context building."""

    def _patch_memory_disk_file(self, project_id: str, from_chapter: int) -> None:
        """Directly remove motifs/episodic entries >= from_chapter from project_memory.json."""
        try:
            workspace = self._workspace
            if workspace is None:
                return
            storage_root = workspace.storage_root
            memory_file = storage_root / project_id / "memory" / "project_memory.json"
            if not memory_file.exists():
                return
            with open(memory_file, "r", encoding="utf-8") as f:
                data = _json.load(f)

            summary_cache = data.get("summary_cache", {})
            if isinstance(summary_cache, dict):
                data["summary_cache"] = {
                    ch: entry
                    for ch, entry in summary_cache.items()
                    if _is_kept_chapter(ch, from_chapter)
                }

            chapter_content_hash = data.get("chapter_content_hash", {})
            if isinstance(chapter_content_hash, dict):
                data["chapter_content_hash"] = {
                    ch: value
                    for ch, value in chapter_content_hash.items()
                    if _is_kept_chapter(ch, from_chapter)
                }

            filtered_motif_cache: dict[str, Any] = {}
            motif_cache = data.get("motif_cache", {})
            if isinstance(motif_cache, dict):
                filtered_motif_cache = {
                    ch: value
                    for ch, value in motif_cache.items()
                    if _is_kept_chapter(ch, from_chapter)
                }
                data["motif_cache"] = filtered_motif_cache

            existing_motifs: dict[str, dict[str, Any]] = {}
            existing_chapter_motifs: dict[str, list[str]] = {}
            motif_tracker = data.get("motif_tracker", {})
            if isinstance(motif_tracker, dict):
                motifs = motif_tracker.get("motifs", {})
                if isinstance(motifs, dict):
                    existing_motifs = {
                        str(mid): m for mid, m in motifs.items() if isinstance(m, dict)
                    }
                chapter_motifs = motif_tracker.get("chapter_motifs", {})
                if isinstance(chapter_motifs, dict):
                    for chapter_key, motif_ids in chapter_motifs.items():
                        if not _is_kept_chapter(chapter_key, from_chapter):
                            continue
                        if not isinstance(motif_ids, list):
                            continue
                        existing_chapter_motifs[str(chapter_key)] = [
                            str(mid).strip() for mid in motif_ids if str(mid).strip()
                        ]

            def _clean_character_list(raw: Any) -> list[str]:
                if not isinstance(raw, list):
                    return []
                return [str(item).strip() for item in raw if str(item).strip()]

            def _kept_existing_motif(motif: dict[str, Any]) -> bool:
                first = _safe_chapter_number(motif.get("first_appearance_chapter"))
                last = _safe_chapter_number(motif.get("last_appearance_chapter"))
                if first is None or last is None:
                    return False
                return first < from_chapter and last < from_chapter

            def _fresh_motif_entry(
                motif_id: str,
                existing: dict[str, Any],
                chapter_number: int,
            ) -> dict[str, Any]:
                return {
                    "name": str(existing.get("name", motif_id) or motif_id),
                    "category": str(existing.get("category", "unknown") or "unknown"),
                    "description": str(existing.get("description", "") or ""),
                    "thematic_meaning": str(existing.get("thematic_meaning", "") or ""),
                    "is_intentional": bool(existing.get("is_intentional", True)),
                    "retired": bool(existing.get("retired", False)),
                    "occurrence_count": 0,
                    "first_appearance_chapter": chapter_number,
                    "last_appearance_chapter": chapter_number,
                    "associated_characters": _clean_character_list(
                        existing.get("associated_characters", [])
                    ),
                }

            rebuilt_motifs: dict[str, dict[str, Any]] = {}
            rebuilt_chapter_motifs: dict[str, set[str]] = {}
            for chapter_key, raw_occurrences in filtered_motif_cache.items():
                chapter_number = _safe_chapter_number(chapter_key)
                if chapter_number is None or not isinstance(raw_occurrences, list):
                    continue
                for occurrence in raw_occurrences:
                    if not isinstance(occurrence, dict):
                        continue
                    motif_id = str(occurrence.get("motif_id", "") or "").strip()
                    if not motif_id:
                        continue
                    existing = existing_motifs.get(motif_id, {})
                    motif_entry = rebuilt_motifs.setdefault(
                        motif_id,
                        _fresh_motif_entry(motif_id, existing, chapter_number),
                    )
                    rebuilt_chapter_motifs.setdefault(str(chapter_number), set()).add(motif_id)
                    motif_entry["occurrence_count"] = int(motif_entry["occurrence_count"]) + 1
                    motif_entry["first_appearance_chapter"] = min(
                        int(motif_entry["first_appearance_chapter"]),
                        chapter_number,
                    )
                    motif_entry["last_appearance_chapter"] = max(
                        int(motif_entry["last_appearance_chapter"]),
                        chapter_number,
                    )
                    associated: list[str] = motif_entry["associated_characters"]
                    seen = {str(item) for item in associated if str(item).strip()}
                    raw_characters = occurrence.get("associated_characters", [])
                    if isinstance(raw_characters, list):
                        for character in raw_characters:
                            value = str(character or "").strip()
                            if value and value not in seen:
                                associated.append(value)
                                seen.add(value)

            for motif_id, motif in existing_motifs.items():
                if motif_id not in rebuilt_motifs and _kept_existing_motif(motif):
                    rebuilt_motifs[motif_id] = dict(motif)

            for chapter_key, motif_ids in existing_chapter_motifs.items():
                kept_ids = [mid for mid in motif_ids if mid in rebuilt_motifs]
                if kept_ids:
                    rebuilt_chapter_motifs.setdefault(chapter_key, set()).update(kept_ids)

            data["motif_tracker"] = {
                "motifs": rebuilt_motifs,
                "chapter_motifs": {
                    chapter_key: sorted(motif_ids)
                    for chapter_key, motif_ids in sorted(
                        rebuilt_chapter_motifs.items(),
                        key=lambda item: int(item[0]),
                    )
                    if motif_ids
                },
            }

            episodic_index = data.get("episodic_index", {})
            if isinstance(episodic_index, dict):
                data["episodic_index"] = _filter_episodic_index_payload(
                    episodic_index,
                    from_chapter=from_chapter,
                )

            if isinstance(data.get("motif_suggestions"), list):
                data["motif_suggestions"] = []
            if isinstance(data.get("repetition_warnings"), list):
                data["repetition_warnings"] = []

            if data.get("last_indexed_chapter", 0) >= from_chapter:
                data["last_indexed_chapter"] = max(0, from_chapter - 1)

            MemoryPatcher.patch_memory_file(memory_file, data)

            motif_state_file = memory_file.with_name("motif_state.json")
            if motif_state_file.exists():
                try:
                    with open(motif_state_file, "r", encoding="utf-8") as f:
                        motif_state = _json.load(f)
                    if isinstance(motif_state, dict):
                        # After the persistence split, motif_tracker/motif_cache
                        # live in motif_state.json. Patch those keys here too.
                        shard_tracker = motif_state.get("motif_tracker", {})
                        if not isinstance(shard_tracker, dict):
                            shard_tracker = {}
                        shard_cache = motif_state.get("motif_cache", {})
                        if not isinstance(shard_cache, dict):
                            shard_cache = {}
                        has_shard_payload = (
                            bool(shard_tracker.get("motifs"))
                            or bool(shard_tracker.get("chapter_motifs"))
                            or bool(shard_cache)
                        )

                        if has_shard_payload:
                            # Rebuild motif data from the shard, applying the
                            # same from_chapter filter as above.
                            shard_existing_motifs: dict[str, dict[str, Any]] = {}
                            if isinstance(shard_tracker.get("motifs"), dict):
                                for mid, m in shard_tracker["motifs"].items():
                                    if isinstance(m, dict):
                                        shard_existing_motifs[str(mid)] = m

                            shard_chapter_motifs: dict[str, list[str]] = {}
                            for ck, mids in shard_tracker.get("chapter_motifs", {}).items():
                                if not _is_kept_chapter(ck, from_chapter):
                                    continue
                                if isinstance(mids, (list, tuple)):
                                    shard_chapter_motifs[str(ck)] = [
                                        str(x).strip() for x in mids if str(x).strip()
                                    ]

                            shard_filtered_cache: dict[str, Any] = {}
                            for ck, occ_list in shard_cache.items():
                                if _is_kept_chapter(ck, from_chapter):
                                    shard_filtered_cache[ck] = occ_list

                            shard_rebuilt_motifs: dict[str, dict[str, Any]] = {}
                            shard_rebuilt_chapter_motifs: dict[str, set[str]] = {}
                            for ck, raw_occs in shard_filtered_cache.items():
                                ch_no = _safe_chapter_number(ck)
                                if ch_no is None or not isinstance(raw_occs, list):
                                    continue
                                for occ in raw_occs:
                                    if not isinstance(occ, dict):
                                        continue
                                    mid = str(occ.get("motif_id", "") or "").strip()
                                    if not mid:
                                        continue
                                    existing = shard_existing_motifs.get(mid, {})
                                    entry = shard_rebuilt_motifs.setdefault(
                                        mid, _fresh_motif_entry(mid, existing, ch_no)
                                    )
                                    shard_rebuilt_chapter_motifs.setdefault(str(ch_no), set()).add(
                                        mid
                                    )
                                    entry["occurrence_count"] = int(entry["occurrence_count"]) + 1
                                    entry["first_appearance_chapter"] = min(
                                        int(entry["first_appearance_chapter"]), ch_no
                                    )
                                    entry["last_appearance_chapter"] = max(
                                        int(entry["last_appearance_chapter"]), ch_no
                                    )
                                    assoc: list[str] = entry["associated_characters"]
                                    seen = {str(x) for x in assoc if str(x).strip()}
                                    raw_chars = occ.get("associated_characters", [])
                                    if isinstance(raw_chars, list):
                                        for ch in raw_chars:
                                            v = str(ch or "").strip()
                                            if v and v not in seen:
                                                assoc.append(v)
                                                seen.add(v)

                            for mid, m in shard_existing_motifs.items():
                                if mid not in shard_rebuilt_motifs and _kept_existing_motif(m):
                                    shard_rebuilt_motifs[mid] = dict(m)

                            for ck, mids in shard_chapter_motifs.items():
                                kept = [m for m in mids if m in shard_rebuilt_motifs]
                                if kept:
                                    shard_rebuilt_chapter_motifs.setdefault(ck, set()).update(kept)

                            motif_state["motif_tracker"] = {
                                "motifs": shard_rebuilt_motifs,
                                "chapter_motifs": {
                                    ck: sorted(mids)
                                    for ck, mids in sorted(
                                        shard_rebuilt_chapter_motifs.items(),
                                        key=lambda item: int(item[0]),
                                    )
                                    if mids
                                },
                            }
                            motif_state["motif_cache"] = shard_filtered_cache
                        else:
                            motif_state["motif_tracker"] = data["motif_tracker"]
                            motif_state["motif_cache"] = data["motif_cache"]
                        MemoryPatcher.patch_memory_file(motif_state_file, motif_state)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _patch_outline_episodic_file(self, project_id: str, from_chapter: int) -> None:
        """Remove chapter-scoped entries >= from_chapter from outline_episodic.json."""
        try:
            workspace = self._workspace
            if workspace is None:
                return
            storage_root = workspace.storage_root
            outline_file = storage_root / project_id / "memory" / "outline_episodic.json"
            if not outline_file.exists():
                return

            with open(outline_file, "r", encoding="utf-8") as f:
                data = _json.load(f)

            if not isinstance(data, dict):
                return

            filtered = _filter_outline_payload(
                data,
                from_chapter=from_chapter,
            )
            MemoryPatcher.patch_memory_file(outline_file, filtered)
        except Exception:  # noqa: BLE001
            pass

    def _build_autopilot_context(self) -> AutoPilotContext:
        """Build a frozen snapshot of state for autopilot decision functions."""
        from novel_forge.core.config import get_settings

        _ch_key = (
            (self._studio.project_id, self._studio.chapter_number) if self._studio else ("", 0)
        )
        _upstream_ready = self._is_upstream_chapter_ready()
        return AutoPilotContext(
            mode=self._mode,
            auto_started=self._auto_started,
            auto_pilot_pending=self._auto_pilot_pending,
            studio=self._studio,
            latest_job=self._latest_relevant_job(),
            last_submitted_checkpoint_id=self._last_submitted_checkpoint_id,
            current_chapter_done=self._is_current_chapter_done(),
            book_auto_skip_done=self._book_auto_skip_done,
            chapter_prepared_this_run=self._auto_chapter_prepared,
            auto_repair_pending=self._auto_repair_pending,
            repair_attempts=self._auto_repair_attempts.get(_ch_key, 0),
            max_repair_attempts=get_settings().max_auto_repair_attempts,
            upstream_chapter_ready=_upstream_ready,
            current_project_id=self.current_project_id(),
            user_navigated=getattr(self, "_user_nav_ctx_pending", False),
        )

    def _is_upstream_chapter_ready(self) -> bool:
        if self._studio is None or self._workspace is None:
            return True

        project_id = self._studio.project_id
        chapter_number = self._studio.chapter_number
        cache_key = (project_id, chapter_number)

        now = time.monotonic()
        cached = getattr(self, "_upstream_ready_cache", None)
        if cached:
            result, timestamp = cached.get(cache_key, (None, 0.0))
            if result is not None and now - timestamp < 2.0:
                return bool(result)

        ch = f"{chapter_number:03d}"
        project_dir = self._workspace.storage_root / project_id
        chapter_path = project_dir / "chapters" / f"chapter_{ch}.md"
        canon_path = project_dir / "canon" / "canon_current.json"
        result = chapter_path.exists() and canon_path.exists()

        if not hasattr(self, "_upstream_ready_cache"):
            self._upstream_ready_cache = {}
        self._upstream_ready_cache[cache_key] = (result, now)

        return result

    def _latest_relevant_job(self) -> DesktopJobRecord | None:
        """Return the most recent chapter-specific job.

        Book-level jobs (INIT_BOOK, BUILD_INDEX, etc.) are intentionally excluded
        because autopilot decisions should only consider chapter-scoped jobs.
        """
        return self._jobs[0] if self._jobs else None

    def _is_current_chapter_done(self) -> bool:
        if self._studio is None:
            return False
        current_ch = self._studio.chapter_number
        for ch in self._studio.chapters:
            if ch.chapter_number == current_ch:
                return ch.status == "done"
        return False

    def _current_chapter_stale_data(self) -> tuple[bool, int]:
        if self._studio is None:
            return False, 0
        current_ch = self._studio.chapter_number
        for ch in self._studio.chapters:
            if ch.chapter_number == current_ch:
                if ch.status == "stale" and ch.word_count > 0:
                    return True, ch.word_count
                return False, 0
        return False, 0

    def _feasibility_check_word_count(self) -> bool:
        if self._auto_pilot and self._auto_started:
            return True

        if self._studio is None or self._workspace is None:
            return True

        storage_root = self._workspace.storage_root
        outline_path = storage_root / self._studio.project_id / "outline.json"
        target_chars: int | None = None
        try:
            import json as _json

            data = _json.loads(outline_path.read_text(encoding="utf-8"))
            for ch in data.get("chapters", []):
                if ch.get("chapter_number") == self._studio.chapter_number:
                    raw = ch.get("expected_word_count")
                    target_chars = int(raw) if raw else None
                    break
        except Exception:
            return True

        if not target_chars or target_chars <= 0:
            return True

        model_id: str | None = None
        thinking = False
        if self._runtime is not None:
            from novel_forge.common.constants import TaskType

            override = self._runtime.router.task_route_overrides.get(TaskType.DRAFT_CHAPTER)
            if override and override.model_id:
                model_id = override.model_id
                thinking = override.thinking

        if model_id is None:
            return True

        from novel_forge.gateway.profiles import check_draft_feasibility

        level, message = check_draft_feasibility(model_id, target_chars, thinking=thinking)
        if level == "ok":
            return True

        if level == "error":
            icon = QMessageBox.Icon.Warning
            text = "⚠️ 目标字数很可能超出模型的有效输出上限"
        else:
            icon = QMessageBox.Icon.Information
            text = "ℹ️ 目标字数已接近模型的有效输出上限"
        return (
            show_message_box(
                self.window(),
                "字数可行性提示",
                text,
                informative_text=message,
                icon=icon,
                actions=(
                    MessageBoxAction(
                        "continue",
                        "继续生成",
                        QMessageBox.ButtonRole.AcceptRole,
                        "primary",
                        True,
                    ),
                    MessageBoxAction(
                        "cancel",
                        "取消",
                        QMessageBox.ButtonRole.RejectRole,
                        "secondary",
                    ),
                ),
                escape_key="cancel",
            )
            == "continue"
        )

    def _delete_completed_chapter_files(self) -> None:
        if self._studio is None or self._workspace is None:
            return

        project_id = self._studio.project_id
        project_dir = None
        for proj in self._workspace.projects:
            if proj.project_id == project_id:
                project_dir = self._workspace.storage_root / project_id
                break

        if project_dir is None or not project_dir.exists():
            return

        from novel_forge.persistence.filesystem import FileSystemStorage
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.persistence.project_staleness import regenerate_from_chapter

        layout = ProjectLayout(project_dir)
        storage = FileSystemStorage(project_dir)

        regenerate_from_chapter(storage, layout, from_chapter=1)

        self._invalidate_memory_context(project_id, from_chapter=1)

        self.clean_chapters_flow_requested.emit(project_id, 1)

        self._context_request_timer.stop()
        self._context_request_timer.start()
        self.workspace_refresh_requested.emit()

    def _invalidate_memory_context(self, project_id: str, from_chapter: int) -> None:
        try:
            if self._runtime is not None:
                memory_ctx = self._runtime.memory_contexts.get(project_id)
                if memory_ctx is not None:
                    memory_ctx.invalidate_chapter_memory(from_chapter)
                    memory_ctx.save_to_disk()
                    pending_tasks = getattr(memory_ctx, "_pending_tasks", None)
                    if isinstance(pending_tasks, set):
                        for task in list(pending_tasks):
                            cancel = getattr(task, "cancel", None)
                            if callable(cancel):
                                try:
                                    cancel()
                                except Exception:  # noqa: BLE001
                                    pass
                        pending_tasks.clear()
                    memory_ctx._storage = None
                self._runtime.memory_contexts.pop(project_id, None)
            if self._workspace is not None:
                self._patch_memory_disk_file(project_id, from_chapter)
                self._patch_outline_episodic_file(project_id, from_chapter)
            store = self._store
            if store is not None:
                store.clear_memory_status(project_id)
        except Exception:  # noqa: BLE001
            pass

    def _show_version_diff(self) -> None:
        layout = self._project_layout()
        if layout is None:
            return
        from novel_forge.core.utils.version_diff import diff_chapter_versions, list_draft_versions
        from novel_forge.desktop.pages.document_renderers import render_version_diff

        from .dialogs import VersionDiffDialog

        assert self._studio is not None
        ch_num = self._studio.chapter_number
        versions = list_draft_versions(layout.drafts_dir, ch_num)
        if len(versions) < 2:
            show_warning_message(
                self,
                "版本不足",
                f"第 {ch_num} 章的草稿版本不足 2 个，无法进行对比。\n"
                "完成至少一轮编辑后即可使用版本对比功能。",
            )
            return

        dlg = VersionDiffDialog(versions, ch_num, parent=self.window())
        dlg.exec()
        if not dlg.was_accepted():
            return

        ver_a, ver_b = dlg.get_versions()
        info_a = next(v for v in versions if v.version == ver_a)
        info_b = next(v for v in versions if v.version == ver_b)

        try:
            result = diff_chapter_versions(layout.drafts_dir, ch_num, ver_a, ver_b)
        except (ValueError, OSError):
            show_warning_message(self, "对比失败", "无法读取版本文件进行对比。")
            return

        diff_data = {
            "additions": result.total_additions,
            "deletions": result.total_deletions,
            "similarity_ratio": result.similarity_ratio,
            "unified_diff": result.unified_diff,
            "hunks": [
                {
                    "tag": "replace",
                    "a_text": "\n".join(line[1:] for line in h.lines if line.startswith("-")),
                    "b_text": "\n".join(line[1:] for line in h.lines if line.startswith("+")),
                }
                for h in result.hunks
            ],
        }
        widget = render_version_diff(diff_data, label_a=info_a.label, label_b=info_b.label)
        tab_label = f"📊 对比: {info_a.label} → {info_b.label}"
        self._artifact_tabs.addTab(widget, tab_label)
        self._artifact_tabs.setCurrentWidget(widget)

    def _on_clean_stale_chapters(self) -> None:
        from novel_forge.persistence.filesystem import FileSystemStorage
        from novel_forge.persistence.project_staleness import stale_chapter_cutoff

        layout = self._project_layout()
        if layout is None:
            return

        if any(j.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED} for j in self._jobs):
            show_warning_message(self.window(), "无法清理", "请等待当前任务完成后再清理失效章节。")
            return

        storage = FileSystemStorage(layout.root.parent)
        cutoff = stale_chapter_cutoff(storage, layout)
        if cutoff is None:
            cutoff = self.current_chapter_number()

        max_chapter = self._studio.total_chapters if self._studio else cutoff
        dlg = CleanChaptersDialog(cutoff, max_chapter, self.window())
        dlg.exec()
        if dlg.result_key() != CleanChaptersDialog.CONFIRM:
            return
        cutoff = dlg.selected_cutoff()

        from novel_forge.persistence.project_staleness import regenerate_from_chapter

        invalidated = regenerate_from_chapter(storage, layout, from_chapter=cutoff)
        if not invalidated:
            show_warning_message(self.window(), "无文件清理", "未找到需要清理的章节文件。")
            return

        project_id = self._studio.project_id if self._studio else None
        if project_id:
            self._invalidate_memory_context(project_id, from_chapter=cutoff)

        chapter_list = "、".join(f"第 {n} 章" for n in sorted(invalidated))
        window = self.window()
        if hasattr(window, "show_priority_status"):
            window.show_priority_status(
                f"已清理 {len(invalidated)} 个章节的失效文件：{chapter_list}",
                6_000,
                0,  # _STATUS_INFO,
            )
        if project_id:
            self.clean_chapters_flow_requested.emit(project_id, cutoff)
        self._clean_stale_btn.setVisible(False)
        self._context_request_timer.stop()
        self._context_request_timer.start()
        self.workspace_refresh_requested.emit()
