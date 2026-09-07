"""Finalize methods for MemoryContext."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Awaitable

from novel_forge.memory.integration_expression import (
    index_expression_observations_for_finalize as _expression_index_for_finalize,
)
from novel_forge.memory.integration_expression import (
    load_expression_profiles as _expression_load_profiles,
)
from novel_forge.memory.integration_expression import (
    refresh_expression_profiles_from_existing_project as _expression_refresh_profiles,
)
from novel_forge.memory.integration_finalize import (
    index_episodic_for_finalize as _finalize_index_episodic,
)
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    pass


_log = get_logger("memory.context")


class FinalizeMixin:
    """Mixin providing chapter/volume finalize methods."""

    async def finalize_chapter_memory(
        self,
        chapter_number: int,
        text: str,
        creative_report_text: str = "",
        *,
        chapter_result: Any | None = None,
        chapter_plan: Any | None = None,
    ) -> dict[str, Any]:
        """Index chapter and run all post-chapter memory tasks concurrently.

        Unlike the ``index_chapter`` + ``flush_pending_tasks`` pattern, this
        method:

        - Runs episodic indexing, motif extraction, and summary generation
          concurrently via ``asyncio.gather``.
        - Guarantees **all** tasks complete before returning (no timeout).
        - Persists results atomically after all tasks finish.

        Returns:
            Stats dict with keys: chapter, tasks_run, tasks_ok, tasks_failed,
            motifs_extracted, summary_generated, saved.
        """
        stats: dict[str, Any] = {
            "chapter": chapter_number,
            "tasks_run": [],
            "tasks_ok": [],
            "tasks_failed": [],
            "motifs_extracted": 0,
            "summary_generated": False,
            "expression_observations": 0,
            "saved": False,
        }

        if not text.strip():
            return stats

        # ── Warmup: pre-populate motifs from seeds for early chapters ──
        warmup_limit = int(getattr(self.settings, "memory_motif_warmup_chapters", 3) or 3)
        if (
            self._motif_tracker is not None
            and chapter_number <= warmup_limit
            and not getattr(self._motif_tracker, "_is_warmup_complete", False)
        ):
            try:
                story_bible = None
                if self._storage is not None and self._project_id:
                    try:
                        from novel_forge.core.schemas.bible import StoryBible
                        from novel_forge.persistence.models import ProjectLayout

                        layout = ProjectLayout(self._storage.ensure_project_dir(self._project_id))
                        if self._storage.exists(layout.bible_path):
                            story_bible_data = self._storage.load_json(layout.bible_path)
                            if story_bible_data:
                                story_bible = StoryBible.model_validate(story_bible_data)
                    except Exception as exc:
                        _log.debug(
                            "story_bible_load_failed | project=%s | error=%s",
                            self._project_id,
                            exc,
                        )

                warmup = getattr(self._motif_tracker, "warmup", None)
                if callable(warmup):
                    project_seeds = self._load_forbidden_seeds()
                    await warmup(
                        project_seeds=project_seeds,
                        bible_data=None,
                        genre_template=None,
                        story_themes=story_bible.themes if story_bible else None,
                    )
                    _log.info(
                        "motif_warmup_triggered | chapter=%d | warmup_limit=%d",
                        chapter_number,
                        warmup_limit,
                    )
            except Exception as exc:
                _log.warning("motif_warmup_failed | chapter=%d | error=%s", chapter_number, exc)

        # ── Warmup: load style-rule fingerprints from style_profile ──
        if self._style_rule_tracker is not None and not getattr(
            self._style_rule_tracker, "is_warmup_complete", False
        ):
            try:
                if self._storage is not None and self._project_id:
                    from novel_forge.persistence.models import ProjectLayout

                    layout = ProjectLayout(self._storage.ensure_project_dir(self._project_id))
                    if self._storage.exists(layout.style_profile_path):
                        style_data = self._storage.load_json(layout.style_profile_path)
                        if style_data:
                            self._style_rule_tracker.load_from_style_profile(style_data)
                            _log.info(
                                "style_rule_tracker_loaded | project=%s | rules=%d",
                                self._project_id,
                                len(getattr(self._style_rule_tracker, "rules", {}) or {}),
                            )
            except Exception as exc:
                _log.warning(
                    "style_rule_tracker_load_failed | project=%s | error=%s",
                    self._project_id,
                    exc,
                )

        # ── Hash-based skip logic (same as index_chapter) ──
        summary_source_hash = self._compute_summary_source_hash(text)
        previous_hash = str(self._chapter_content_hash.get(chapter_number, "") or "")
        content_changed = not previous_hash or previous_hash != summary_source_hash

        need_motifs = self._motif_tracker is not None and self.should_extract_motifs(
            chapter_number, len(text)
        )
        need_summary = self._summary_service is not None and self.should_generate_summary(
            chapter_number, text
        )

        if not content_changed and not need_motifs:
            _log.debug(
                "finalize_chapter_memory skipped | chapter=%d (hash unchanged, motifs ok)",
                chapter_number,
            )
            return stats

        if content_changed and chapter_number <= self._last_indexed_chapter:
            if previous_hash and previous_hash != summary_source_hash:
                self._refresh_single_chapter_memory(chapter_number)
            # Re-check after refresh
            need_motifs = self._motif_tracker is not None and self.should_extract_motifs(
                chapter_number, len(text)
            )

        self._chapter_content_hash[chapter_number] = summary_source_hash

        self._emit_progress(
            "indexing_started",
            {
                "chapter": chapter_number,
                "text_length": len(text),
                "concurrent": True,
            },
        )

        # ── Build concurrent tasks ──
        tasks: list[Awaitable[Any]] = []
        task_names: list[str] = []

        # Episodic indexing
        if self._episodic_memory is not None:
            tasks.append(
                self._index_episodic_for_finalize(
                    chapter_number=chapter_number,
                    text=text,
                    creative_report_text=creative_report_text,
                    chapter_result=chapter_result,
                    chapter_plan=chapter_plan,
                )
            )
            task_names.append("episodic")

        expression_profiles = self._load_expression_profiles()
        if (
            content_changed
            and self._expression_memory is not None
            and expression_profiles
            and self._router is not None
            and self._builder is not None
        ):
            tasks.append(
                self._index_expression_observations_for_finalize(
                    chapter_number=chapter_number,
                    text=text,
                    profiles=expression_profiles,
                    chapter_plan=chapter_plan,
                )
            )
            task_names.append("expression")

        # Motif extraction
        if need_motifs:
            chapter_outline = self._extract_chapter_outline_from_canon(chapter_number)
            tasks.append(self._extract_motifs_async(chapter_number, text, chapter_outline))
            task_names.append("motifs")

        # Summary generation
        if need_summary:
            tasks.append(
                self._generate_summary_async(
                    chapter=chapter_number,
                    text=text,
                    report=creative_report_text,
                    source_hash=summary_source_hash,
                )
            )
            task_names.append("summary")

        stats["tasks_run"] = list(task_names)

        if not tasks:
            self._last_indexed_chapter = max(self._last_indexed_chapter, chapter_number)
            self._emit_progress("indexing_complete", {"chapter": chapter_number, "success": True})
            return stats

        self._emit_progress(
            "concurrent_tasks_started",
            {
                "chapter": chapter_number,
                "tasks": list(task_names),
            },
        )

        concurrent = getattr(self.settings, "memory_concurrent_indexing", True)

        _log.info(
            "finalize_chapter_memory | chapter=%d | mode=%s | tasks: %s",
            chapter_number,
            "concurrent" if concurrent else "sequential",
            ", ".join(task_names),
        )

        # ── Run tasks ──
        results: list[Any]
        if concurrent:
            results = list(await asyncio.gather(*tasks, return_exceptions=True))
        else:
            results = []
            for t in tasks:
                try:
                    results.append(await t)
                except BaseException as exc:
                    results.append(exc)

        # ── Collect results ──
        for name, result in zip(task_names, results, strict=True):
            if isinstance(result, BaseException):
                stats["tasks_failed"].append(name)
                _log.error(
                    "finalize_chapter_memory task '%s' failed | chapter=%d | error=%s",
                    name,
                    chapter_number,
                    result,
                )
            else:
                stats["tasks_ok"].append(name)

        if "motifs" in task_names:
            motif_idx = task_names.index("motifs")
            if not isinstance(results[motif_idx], BaseException):
                stats["motifs_extracted"] = len(self._motif_cache.get(chapter_number, []))

        if "summary" in task_names:
            summary_idx = task_names.index("summary")
            stats["summary_generated"] = not isinstance(results[summary_idx], BaseException)

        if "expression" in task_names:
            expression_idx = task_names.index("expression")
            expression_result = results[expression_idx]
            if isinstance(expression_result, dict):
                stats["expression_observations"] = int(
                    expression_result.get("observations", 0) or 0
                )

        # Only advance the high-water mark when at least one task succeeded;
        # otherwise a fully-failed finalize would falsely suppress future retries.
        if stats["tasks_ok"]:
            self._last_indexed_chapter = max(self._last_indexed_chapter, chapter_number)

        # ── Single atomic save after all tasks complete ──
        saved = self.save_to_disk()
        stats["saved"] = saved

        # ── Prune episodic index if it has grown beyond threshold ──
        if self._episodic_memory is not None:
            try:
                self._episodic_memory.prune_episodic_by_recency()
            except Exception as exc:
                _log.warning("episodic_prune_failed | chapter=%d | error=%s", chapter_number, exc)

        self._emit_progress(
            "concurrent_tasks_done",
            {
                "chapter": chapter_number,
                "tasks_ok": stats["tasks_ok"],
                "tasks_failed": stats["tasks_failed"],
                "motifs_extracted": stats["motifs_extracted"],
                "summary_generated": stats["summary_generated"],
                "expression_observations": stats["expression_observations"],
            },
        )
        self._emit_progress(
            "indexing_complete",
            {
                "chapter": chapter_number,
                "success": not stats["tasks_failed"] or bool(stats["tasks_ok"]),
            },
        )

        _log.info(
            "finalize_chapter_memory done | chapter=%d | ok=%s | failed=%s | saved=%s",
            chapter_number,
            stats["tasks_ok"],
            stats["tasks_failed"],
            saved,
        )

        return stats

    async def _index_episodic_for_finalize(
        self,
        *,
        chapter_number: int,
        text: str,
        creative_report_text: str,
        chapter_result: Any | None,
        chapter_plan: Any | None,
    ) -> Any:
        return await _finalize_index_episodic(
            self,
            chapter_number=chapter_number,
            text=text,
            creative_report_text=creative_report_text,
            chapter_result=chapter_result,
            chapter_plan=chapter_plan,
        )

    async def _index_expression_observations_for_finalize(
        self,
        *,
        chapter_number: int,
        text: str,
        profiles: list[dict[str, Any]],
        chapter_plan: Any | None,
    ) -> dict[str, Any]:
        return await _expression_index_for_finalize(
            self,
            chapter_number=chapter_number,
            text=text,
            profiles=profiles,
            chapter_plan=chapter_plan,
        )

    def _load_expression_profiles(self) -> list[dict[str, Any]]:
        return _expression_load_profiles(self)

    async def _refresh_expression_profiles_from_existing_project(
        self, layout: Any
    ) -> list[dict[str, Any]]:
        return await _expression_refresh_profiles(self, layout)

    @staticmethod
    def _field(value: Any, key: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    def _load_chapter_plan_for_memory(self, chapter_number: int) -> Any | None:
        if self._storage is None or not self._project_id:
            return None

        try:
            from novel_forge.core.schemas.continuity import ChapterPlan
            from novel_forge.persistence.models import ProjectLayout

            layout = ProjectLayout(self._storage.ensure_project_dir(self._project_id))
            plan_path = layout.chapter_plan_path(chapter_number)
            if not self._storage.exists(plan_path):
                return None
            raw = self._storage.load_json(plan_path)
            try:
                return ChapterPlan.model_validate(raw)
            except Exception:
                return self._to_attr_namespace(raw)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "chapter_plan_load_for_memory_failed | project=%s | chapter=%d | error=%s",
                self._project_id,
                chapter_number,
                exc,
            )
            return None

    @classmethod
    def _to_attr_namespace(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(
                **{str(key): cls._to_attr_namespace(item) for key, item in value.items()}
            )
        if isinstance(value, list):
            return [cls._to_attr_namespace(item) for item in value]
        return value

    # ── Volume-scoped memory operations ──────────────────────────────

    async def finalize_volume_memory(
        self,
        volume_number: int,
        start_chapter: int,
        end_chapter: int,
        audit_report: Any = None,
    ) -> dict[str, Any]:
        """Coordinate all memory services at volume boundary.

        This is called after volume-end audit and canon compaction.
        It generates volume-level summary, prunes episodic/critique data,
        and persists the volume-scoped memory state.

        Args:
            volume_number: The volume number that just ended
            start_chapter: First chapter of the volume
            end_chapter: Last chapter of the volume
            audit_report: Optional VolumeAuditReport from the audit step

        Returns:
            Dictionary with operation statistics
        """
        stats: dict[str, Any] = {
            "volume": volume_number,
            "chapter_range": f"{start_chapter}-{end_chapter}",
            "volume_summary": False,
            "critique_pruning": {},
            "memory_pruning": {},
            "cache_purging": {},
            "errors": [],
        }
        keep_recent_volumes = 2
        eviction_threshold = self._volume_eviction_threshold(
            volume_number=volume_number,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            keep_recent_volumes=keep_recent_volumes,
        )
        if eviction_threshold is not None:
            stats["eviction_threshold"] = eviction_threshold

        # 1. Generate volume-level summary
        if self._summary_service is not None:
            try:
                # Import VolumeOutline if available
                from novel_forge.core.schemas.outline import VolumeOutline

                volume_outline = next(
                    (
                        volume
                        for volume in list(getattr(self._outline, "volumes", []) or [])
                        if int(getattr(volume, "volume_number", 0) or 0) == volume_number
                    ),
                    None,
                )
                if volume_outline is None:
                    volume_outline = VolumeOutline(
                        volume_number=volume_number,
                        title=str(
                            getattr(audit_report, "volume_title", "") or f"卷{volume_number}"
                        ),
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        arc_goal="",
                        milestone_targets=[],
                        main_conflicts=[],
                        climax_hint="",
                        resolution_hint="",
                        notes="",
                    )

                # Collect chapter summaries for this volume
                chapter_summaries = {}
                for ch in range(start_chapter, end_chapter + 1):
                    summary = self._summary_service.get_summary("chapter", ch)
                    if summary:
                        chapter_summaries[ch] = summary

                if chapter_summaries:
                    canon_state = None
                    if self._story_kernel_store is not None and self._project_id:
                        try:
                            canon_state = await self._story_kernel_store.load_kernel(
                                self._project_id
                            )
                        except Exception as exc:
                            _log.warning(
                                "volume_summary_canon_load_failed | volume=%d | error=%s",
                                volume_number,
                                exc,
                            )
                    volume_summary = await self._summary_service.generate_volume_summary(
                        volume=volume_outline,
                        chapter_summaries=chapter_summaries,
                        canon_state=canon_state,
                        audit_report=audit_report,
                    )
                    stats["volume_summary"] = True
                    stats["volume_summary_strategy"] = volume_summary.metadata.get(
                        "summary_strategy", "single_pass"
                    )
                    stats["volume_summary_chunk_count"] = int(
                        volume_summary.metadata.get("summary_chunk_count", 1) or 1
                    )
                    _log.info(
                        "volume_summary_generated | volume=%d | chapters=%d",
                        volume_number,
                        len(chapter_summaries),
                    )
            except Exception as exc:
                stats["errors"].append(f"volume_summary: {exc}")
                _log.warning("卷级摘要生成失败: %s", exc)

        # 1.5. Index volume audit report for similarity search
        if self._episodic_memory is not None and audit_report is not None:
            try:
                await self._episodic_memory.index_volume_audit(audit_report)
                stats["volume_audit_indexed"] = True
            except Exception as exc:
                stats["errors"].append(f"volume_audit_index: {exc}")

        # 2. Prune critique index via EpisodicMemory
        if self._episodic_memory is not None:
            try:
                critique_stats = self._episodic_memory.on_volume_end(
                    volume_number=volume_number,
                    end_chapter=end_chapter,
                )
                stats["critique_pruning"] = critique_stats
            except Exception as exc:
                stats["errors"].append(f"critique_pruning: {exc}")
                _log.warning("CritiqueIndex 修剪失败: %s", exc)

        # 2.5. Prune old episodic entries when a concrete volume boundary is known.
        if self._episodic_memory is not None:
            try:
                memory_stats = self._episodic_memory.prune_by_volume(
                    volume_number=volume_number,
                    keep_recent_volumes=keep_recent_volumes,
                    max_chapter_to_keep=eviction_threshold,
                    prune_critiques=False,
                )
                stats["memory_pruning"] = memory_stats
            except Exception as exc:
                stats["errors"].append(f"memory_pruning: {exc}")
                _log.warning("卷级情景记忆修剪失败: %s", exc)

        # 3. Purge caches for completed volume
        try:
            cache_stats = self.purge_volume_caches(volume_number)
            stats["cache_purging"] = cache_stats
        except Exception as exc:
            stats["errors"].append(f"cache_purging: {exc}")
            _log.warning("卷缓存清理失败: %s", exc)

        # 3.5. Evict old volume summaries from summary service
        if self._summary_service is not None:
            try:
                evicted = self._summary_service.evict_volume_summaries(
                    volume_number=volume_number,
                    keep_recent_volumes=keep_recent_volumes,
                    max_chapter_to_keep=eviction_threshold,
                )
                stats["summaries_evicted"] = evicted
            except Exception as exc:
                stats["errors"].append(f"evict_summaries: {exc}")

        # 3.6. Persist summary service caches
        if self._summary_service is not None:
            try:
                await self._summary_service.save_summaries()
                stats["summaries_saved"] = True
            except Exception as exc:
                stats["errors"].append(f"save_summaries: {exc}")

        # 4. Persist volume memory state
        try:
            self.save_to_disk()
            stats["saved"] = True
        except Exception as exc:
            stats["errors"].append(f"save: {exc}")
            _log.warning("卷末记忆持久化失败: %s", exc)

        _log.info(
            "finalize_volume_memory done | volume=%d | summary=%s | "
            "critique_remaining=%d | errors=%d",
            volume_number,
            stats["volume_summary"],
            stats.get("critique_pruning", {}).get("remaining", 0),
            len(stats["errors"]),
        )

        return stats

    def purge_volume_caches(
        self,
        volume_number: int,
        *,
        volume_chapter_range: tuple[int, int] | None = None,
    ) -> dict[str, int]:
        """Purge cache data for a completed volume.

        Args:
            volume_number: The volume to purge caches for
            volume_chapter_range: Optional (start, end) chapter range.
                If not provided, only summary/motif caches are purged.

        Returns:
            Statistics: cleared summaries, cleared motifs
        """
        stats = {
            "cleared_summaries": 0,
            "cleared_motifs": 0,
        }

        # Clear summary cache entries for chapters in this volume
        if volume_chapter_range is not None:
            start_ch, end_ch = volume_chapter_range
            chapters_to_remove = [ch for ch in self._summary_cache if start_ch <= ch <= end_ch]
            for ch in chapters_to_remove:
                del self._summary_cache[ch]
                stats["cleared_summaries"] += 1

            chapters_to_remove = [ch for ch in self._motif_cache if start_ch <= ch <= end_ch]
            for ch in chapters_to_remove:
                del self._motif_cache[ch]
                stats["cleared_motifs"] += 1

        _log.debug(
            "purge_volume_caches | volume=%d | cleared_summaries=%d | cleared_motifs=%d",
            volume_number,
            stats["cleared_summaries"],
            stats["cleared_motifs"],
        )

        return stats

    def on_volume_boundary(
        self,
        volume_number: int,
    ) -> None:
        """Update internal state when entering a new volume.

        This is a lightweight hook that can be called when the pipeline
        starts working on a new volume.

        Args:
            volume_number: The new active volume number
        """
        if self._episodic_memory is not None:
            self._episodic_memory._active_volume = volume_number

        _log.debug("on_volume_boundary | volume=%d", volume_number)

