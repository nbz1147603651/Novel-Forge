"""Persistence and serialization methods for MemoryContext."""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from novel_forge.core.infra.async_runner import run_async_coro
from novel_forge.memory.integration_io import (
    MemoryIOContext,
)
from novel_forge.memory.integration_io import (
    load_from_disk as _io_load_from_disk,
)
from novel_forge.memory.integration_io import (
    save_to_disk as _io_save_to_disk,
)
from novel_forge.memory.integration_serializers import (
    serialize_episodic_index as _serializer_serialize_episodic,
)
from novel_forge.memory.integration_serializers import (
    serialize_motif_tracker as _serializer_serialize_motifs,
)
from novel_forge.memory.integration_utils import (
    plan_field as _plan_field,
)
from novel_forge.memory.integration_utils import (
    plan_list_field as _plan_list_field,
)
from novel_forge.memory.motif_repair_orchestration import MotifRepairContext
from novel_forge.memory.motif_repair_orchestration import (
    re_extract_motifs_from_archived_chapters as _re_extract_motifs_from_archived_chapters_impl,
)
from novel_forge.memory.motif_repair_orchestration import (
    repair_motif_history_from_cache as _repair_motif_history_from_cache_impl,
)
from novel_forge.memory.reindex_orchestration import (
    invalidate_chapter_memory as _invalidate_chapter_memory_impl,
)
from novel_forge.memory.reindex_orchestration import (
    rebuild_vector_collection as _rebuild_vector_collection_impl,
)
from novel_forge.memory.vector_shard_io import (
    load_vector_shard as _load_vector_shard_impl,
)
from novel_forge.memory.vector_shard_io import (
    save_vector_shard as _save_vector_shard_impl,
)
from novel_forge.memory.vector_shard_io import vector_shard_path as _vector_shard_path
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    pass


_log = get_logger("memory.context")


class PersistenceMixin:
    """Mixin providing save/load, serialization, and reset methods."""

    async def rebuild_vector_collection(self) -> dict[str, Any]:
        """Explicitly rebuild the episodic vector collection from JSON metadata."""
        return await _rebuild_vector_collection_impl(
            episodic_memory=self._episodic_memory,
            save_to_disk=self.save_to_disk,
            get_status_summary=self.get_status_summary,
        )

    async def index_expression_observations(
        self,
        chapter_number: int,
        final_text: str,
        profiles: list[dict[str, Any]] | None = None,
        scene_intents: list[dict[str, Any]] | None = None,
        pov_character: str = "",
    ) -> dict[str, Any]:
        """Extract, validate, and index expression-channel observations for one chapter."""

        if self._expression_memory is None or self._router is None or self._builder is None:
            return {"observations": 0, "vectors": 0, "saved": False, "skipped": "unavailable"}
        from novel_forge.core.review.review_contracts import source_text_hash
        from novel_forge.pipeline.steps.expression_observation_step import (
            ExpressionObservationInput,
            ExpressionObservationStep,
        )

        profile_items = profiles if profiles is not None else self._load_expression_profiles()
        if not profile_items:
            return {"observations": 0, "vectors": 0, "saved": False, "skipped": "no_profiles"}
        step = ExpressionObservationStep(
            self._router,
            self._builder,
            settings=self.settings,
        )
        result = await step.run(
            ExpressionObservationInput(
                chapter_number=chapter_number,
                chapter_text=final_text,
                expression_channel_profiles=profile_items,
                scene_intents=list(scene_intents or [])[:8],
                pov_character=pov_character,
            )
        )
        index_result = cast(
            dict[str, Any],
            await self._expression_memory.replace_chapter_observations(
                chapter_number=chapter_number,
                source_text_hash=source_text_hash(final_text),
                observations=list(result.observations),
            ),
        )
        index_result["skipped_reason"] = result.skipped_reason
        return index_result

    async def search_expression_channel_memory(
        self,
        *,
        current_chapter: int,
        profiles: list[dict[str, Any]],
        chapter_context: str = "",
        cooldown_chapters: int = 3,
    ) -> list[dict[str, Any]]:
        """Delegate expression-channel semantic retrieval to the expression memory layer."""

        if self._expression_memory is None:
            return []
        search = getattr(self._expression_memory, "search_expression_channel_memory", None)
        if not callable(search):
            return []
        matches = await search(
            current_chapter=current_chapter,
            profiles=profiles,
            chapter_context=chapter_context,
            cooldown_chapters=cooldown_chapters,
            top_k_per_profile=int(
                getattr(self.settings, "expression_channel_zvec_top_k_per_profile", 2) or 2
            ),
            max_hits=6,
        )
        return matches if isinstance(matches, list) else []

    async def rebuild_expression_channel_memory(
        self,
        *,
        from_chapter: int | None = None,
        to_chapter: int | None = None,
    ) -> dict[str, Any]:
        """Rebuild expression-channel observations from archived chapters."""

        if not self._storage or not self._project_id:
            raise RuntimeError("Expression memory rebuild requires project storage")
        if self._expression_memory is None:
            raise RuntimeError("Expression memory is not enabled for this project")
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(self._storage.ensure_project_dir(self._project_id))
        profiles = self._load_expression_profiles()
        refreshed_profiles = False
        if not profiles:
            profiles = await self._refresh_expression_profiles_from_existing_project(layout)
            refreshed_profiles = bool(profiles)
        if not profiles:
            return {
                "rebuilt_chapters": 0,
                "observations": 0,
                "vectors": 0,
                "refreshed_profiles": refreshed_profiles,
                "skipped": "no_profiles",
            }

        full_rebuild = from_chapter is None and to_chapter is None
        if full_rebuild:
            reset_all = getattr(self._expression_memory, "reset_all", None)
            if callable(reset_all):
                reset_all(reset_vectors=True)

        chapter_paths = sorted(layout.chapters_dir.glob("chapter_*.md"))
        total_observations = 0
        total_vectors = 0
        rebuilt = 0
        for path in chapter_paths:
            match = re.search(r"chapter_(\d+)\.md$", path.name)
            if not match:
                continue
            chapter_number = int(match.group(1))
            if from_chapter is not None and chapter_number < from_chapter:
                continue
            if to_chapter is not None and chapter_number > to_chapter:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                plan = self._load_chapter_plan_for_memory(chapter_number)
                result = await self.index_expression_observations(
                    chapter_number,
                    text,
                    profiles=profiles,
                    scene_intents=_plan_list_field(plan, "scene_intents"),
                    pov_character=str(_plan_field(plan, "pov_character", "") or ""),
                )
                rebuilt += 1
                total_observations += int(result.get("observations", 0) or 0)
                total_vectors += int(result.get("vectors", 0) or 0)
            except Exception as exc:
                _log.warning(
                    "expression_memory_rebuild_chapter_failed | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )
        return {
            "rebuilt_chapters": rebuilt,
            "observations": total_observations,
            "vectors": total_vectors,
            "refreshed_profiles": refreshed_profiles,
            "status": self.get_status_summary(),
        }

    def _build_io_context(self) -> MemoryIOContext:
        """Build a MemoryIOContext wiring all state dicts and callbacks.

        The free functions in ``integration_io`` (save_to_disk, load_from_disk)
        operate on a MemoryIOContext that holds the mutable state and
        callbacks. This method bundles the live state on this instance so
        the IO functions can read/write it via dataclass fields and
        callbacks, without taking a hard dependency on MemoryContext.

        Summary service reload is managed by MemoryContext after the core
        project-memory load succeeds. Pass ``None`` so the IO layer remains
        focused on filesystem state and does not also mutate async reload
        bookkeeping.
        """
        return MemoryIOContext(
            storage=self._storage,
            project_id=self._project_id,
            summary_cache=self._summary_cache,
            chapter_content_hash=self._chapter_content_hash,
            summary_stats=self._summary_stats,
            motif_cache=self._motif_cache,
            get_last_indexed_chapter=lambda: self._last_indexed_chapter,
            set_last_indexed_chapter=lambda v: setattr(self, "_last_indexed_chapter", v),
            summary_service=None,
            serialize_episodic_index=self._serialize_episodic_index,
            deserialize_episodic_index=self._deserialize_episodic_index,
            serialize_motif_tracker=self._serialize_motif_tracker,
            deserialize_motif_tracker=self._deserialize_motif_tracker,
            normalize_summary_cache_entry=self._normalize_summary_cache_entry,
            normalize_summary_stats=self._normalize_summary_stats,
            rebuild_motif_stats_from_cache=self._rebuild_motif_stats_from_cache,
            load_outline_episodic_from_init=self._load_outline_episodic_from_init,
            flush_episodic_vector_store=self._flush_episodic_vector_store,
            flush_expression_memory=self._flush_expression_memory,
        )

    def save_to_disk(self) -> bool:
        """Persist memory data to project directory.

        This ensures memory data survives application restarts and is properly
        scoped to the project.

        The actual implementation lives in ``integration_io.save_to_disk``;
        this method is a thin delegation that wires the live state via
        ``_build_io_context``.

        Returns:
            True if save was successful, False otherwise
        """
        if not self._storage or not self._project_id:
            _log.debug("Storage or project_id not set, skipping save")
            return False
        return _io_save_to_disk(self._build_io_context())

    def _flush_episodic_vector_store(self) -> None:
        """Flush the episodic vector backend when it supports durable writes."""
        if not self._episodic_memory:
            return
        flush = getattr(self._episodic_memory, "flush_vector_store", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:
                _log.debug("episodic_vector_store_flush_failed | error=%s", exc)

    def _flush_expression_memory(self) -> None:
        """Flush the expression-channel vector backend when available."""
        if not self._expression_memory:
            return
        flush = getattr(self._expression_memory, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:
                _log.debug("expression_vector_store_flush_failed | error=%s", exc)

    def load_from_disk(self) -> bool:
        """Load memory data from project directory.

        The actual implementation lives in ``integration_io.load_from_disk``;
        this method is a thin delegation that wires the live state via
        ``_build_io_context``. After delegation, it updates
        ``_summary_service_disk_load_attempted`` to signal whether
        ``ensure_async_disk_state_loaded`` still needs to run.

        Returns:
            True if load was successful, False otherwise
        """
        if not self._storage or not self._project_id:
            _log.debug("Storage or project_id not set, skipping load")
            return False

        result = _io_load_from_disk(self._build_io_context())

        if result:
            self._reload_summary_service_from_disk_after_load()

        return result

    def _reload_summary_service_from_disk_after_load(self) -> None:
        """Reload summary-service disk state after project-memory load.

        ``integration_io`` is deliberately kept to core JSON/motif/vector
        filesystem state.  Summary service reload is async-aware runtime state:
        in sync callers we can run it immediately; in async callers we defer
        to ``ensure_async_disk_state_loaded``.
        """
        if self._summary_service is None:
            return

        load_summaries = getattr(self._summary_service, "load_summaries", None)
        if not callable(load_summaries):
            self._summary_service_disk_load_attempted = True
            return

        try:
            asyncio.get_running_loop()
            self._summary_service_disk_load_attempted = False
            _log.debug(
                "load_from_disk called from async context; skipping summary reload | project=%s",
                self._project_id,
            )
            return
        except RuntimeError:
            pass

        try:
            result = load_summaries()
            if inspect.isawaitable(result):
                run_async_coro(result)
            self._summary_service_disk_load_attempted = True
        except Exception as exc:
            _log.warning("Failed to reload summaries from disk: %s", exc)

    async def ensure_async_disk_state_loaded(self) -> dict[str, bool]:
        """Load async-only disk state after construction from an async runtime.

        ``load_from_disk`` is intentionally synchronous for legacy callers.  When it
        runs inside API/Desktop event loops it cannot call ``SummaryService``'s
        async loader, so RuntimeServices invokes this method once before exposing
        the context.
        """
        stats = {
            "summary_attempted": False,
            "summary_loaded": False,
            "narrative_evidence_bootstrapped": False,
        }
        if self._summary_service is not None and not self._summary_service_disk_load_attempted:
            load_summaries = getattr(self._summary_service, "load_summaries", None)
            if not callable(load_summaries):
                self._summary_service_disk_load_attempted = True
            else:
                stats["summary_attempted"] = True
                try:
                    result = load_summaries()
                    if inspect.isawaitable(result):
                        result = await result
                    self._summary_service_disk_load_attempted = True
                    stats["summary_loaded"] = bool(result)
                except Exception as exc:
                    _log.warning("Failed to async-reload summaries from disk: %s", exc)

        if not self._narrative_evidence_bootstrap_attempted:
            self._narrative_evidence_bootstrap_attempted = True
            service = self._narrative_evidence_service
            has_cards = getattr(service, "has_cards", None)
            has_kind = getattr(service, "has_kind", None)
            sync = getattr(service, "sync_narrative_state", None)
            if (
                callable(has_cards)
                and callable(sync)
                and self._storage is not None
                and self._project_id
            ):
                try:
                    from novel_forge.narrative_state.store import NarrativeStateStore

                    project_root = self._storage.root / self._project_id
                    state_store = NarrativeStateStore(project_root)
                    registry = state_store.load_entity_registry()
                    entries = state_store.load_ledger_entries()
                    needs_bootstrap = not await has_cards()
                    # Transitional indexes created before state-ledger syncing
                    # may contain only entity cards; backfill those once too.
                    if entries and callable(has_kind) and not await has_kind("accepted_state"):
                        needs_bootstrap = True
                    if needs_bootstrap and (registry.entities or entries):
                        await sync(
                            entity_registry=registry,
                            ledger_entries=entries,
                            replace=True,
                        )
                        stats["narrative_evidence_bootstrapped"] = True
                except Exception as exc:
                    _log.warning(
                        "narrative_evidence_bootstrap_failed | project=%s | error=%s",
                        self._project_id,
                        exc,
                    )
        return stats

    def _load_outline_episodic_from_init(self) -> None:
        """Load outline episodic data saved during init_long phase.

        This loads the outline plot points, relationships, themes, and unresolved
        questions that were indexed during the init_long phase (outline generation).
        This enables semantic search to include outline-level information.
        """
        if not self._episodic_memory or not self._storage or not self._project_id:
            return

        try:
            if self._storage:
                outline_path = (
                    self._storage.root / self._project_id / "memory" / "outline_episodic.json"
                )
            else:
                outline_path = Path(self._project_id) / "memory" / "outline_episodic.json"
            if not self._storage.exists(outline_path):
                _log.debug(
                    "No outline episodic data found (init_long may not have been run) | project=%s",
                    self._project_id,
                )
                return

            outline_data = self._storage.load_json(outline_path)
            if outline_data:
                self._episodic_memory.deserialize_outline_data(outline_data)
                restored = self._restore_inline_outline_vectors()
                if restored:
                    _log.info(
                        "outline_vectors_restored_from_init | project=%s | vectors=%d",
                        self._project_id,
                        restored,
                    )
                _log.info(
                    "Loaded outline episodic data from init_long | project=%s | outlines=%d",
                    self._project_id,
                    len(outline_data.get("outline_index", {})),
                )
        except Exception as exc:
            _log.warning(
                "Failed to load outline episodic data from init_long: %s",
                exc,
            )

    def _restore_inline_outline_vectors(self) -> int:
        """Upsert outline vectors that are still present in init outline data."""
        if not self._episodic_memory:
            return 0
        outline_index = getattr(self._episodic_memory, "_outline_index", {}) or {}
        ensure = getattr(self._episodic_memory, "_ensure_vector_store_for_vector", None)
        if not callable(ensure):
            return 0
        restored = 0
        for sig, entry in outline_index.items():
            embedding = list(getattr(entry, "embedding", []) or [])
            if not embedding:
                continue
            vector_store = ensure(embedding, source="init_outline_inline_vectors")
            vector_store.add(
                str(sig),
                embedding,
                {
                    "content": " ".join(
                        part
                        for part in (
                            str(getattr(entry, "plot_point", "") or ""),
                            " ".join(list(getattr(entry, "characters", []) or [])),
                            " ".join(list(getattr(entry, "themes", []) or [])),
                            str(getattr(entry, "pov_character", "") or ""),
                            str(getattr(entry, "chapter_goal", "") or ""),
                        )
                        if part
                    ),
                    "chapter": int(getattr(entry, "chapter_number", 0) or 0),
                    "event_types": (
                        [getattr(entry, "event_type", "")]
                        if getattr(entry, "event_type", "")
                        else []
                    ),
                    "characters": list(getattr(entry, "characters", []) or []),
                    "is_outline": True,
                },
            )
            restored += 1
        if restored:
            self._flush_episodic_vector_store()
        return restored

    def _get_memory_path(self) -> Path:
        """Get the path to the memory data file."""
        if self._storage:
            return self._storage.root / self._project_id / "memory" / "project_memory.json"
        return Path(self._project_id) / "memory" / "project_memory.json"

    def _get_memory_journal_path(self) -> Path:
        """Pending save journal used for crash recovery."""
        return self._get_memory_path().with_name("project_memory.pending.json")

    def _serialize_episodic_index(self) -> dict[str, Any]:
        """Serialize episodic memory index for persistence.

        Embeddings are excluded from this output; the Zvec collection is the
        durable vector source of truth. JSON keeps memory metadata and indexes.
        """
        return _serializer_serialize_episodic(self)

    # ── Vector shard I/O ──────────────────────────────────────────

    def _get_vector_shard_path(self) -> Path:
        """Path to the separate binary vector file."""
        return _vector_shard_path(self._get_memory_path())

    def _save_vector_shard(self) -> bool:
        """Legacy-only writer for the pre-zvec compact JSON vector shard.

        Normal save/load no longer calls this method. zvec collections are the
        durable vector source of truth; JSON stores metadata and rebuild inputs.
        """
        return _save_vector_shard_impl(
            storage=self._storage,
            shard_path=self._get_vector_shard_path(),
            episodic_memory=self._episodic_memory,
            logger=_log,
        )

    def _load_vector_shard(self) -> dict[str, list[float]]:
        """Legacy-only loader for the pre-zvec compact JSON vector shard.

        Returns:
            Mapping of signature → embedding vector.  Empty dict if shard
            does not exist or cannot be loaded. Normal project load opens zvec
            directly instead of restoring inline embeddings from this file.
        """
        return _load_vector_shard_impl(
            storage=self._storage,
            shard_path=self._get_vector_shard_path(),
            logger=_log,
        )

    def _deserialize_episodic_index(self, data: dict[str, Any]) -> None:
        """Deserialize episodic memory index from persistence.

        JSON restores memory metadata and indexes. The Zvec collection remains
        the durable vector source of truth and is opened from persisted metadata
        when dimension information is available.
        """
        if not self._episodic_memory or not data:
            return

        try:
            from novel_forge.memory.episodic import EpisodicIndex

            raw_vector_meta = data.get("vector_store", {}) if isinstance(data, dict) else {}
            vector_meta = raw_vector_meta if isinstance(raw_vector_meta, dict) else {}
            index = getattr(self._episodic_memory, "_index", None)
            chapter_events = getattr(self._episodic_memory, "_chapter_events", None)

            if index is not None and "index" in data:
                for sig, entry_data in data["index"].items():
                    episodic_entry = EpisodicIndex(
                        chapter_number=entry_data["chapter_number"],
                        event_summary=entry_data["event_summary"],
                        scene_index=entry_data.get("scene_index", 0),
                        timestamp_in_story=entry_data.get("timestamp_in_story", ""),
                        characters=entry_data.get("characters", []),
                        locations=entry_data.get("locations", []),
                        event_types=entry_data.get("event_types", []),
                        embedding=[],
                        metadata=entry_data.get("metadata", {}),
                    )
                    index[sig] = episodic_entry

            if chapter_events is not None and "chapter_events" in data:
                chapter_events.clear()
                for k, v in data["chapter_events"].items():
                    chapter_events[int(k)] = v

            if "outline_data" in data and data["outline_data"]:
                self._episodic_memory.deserialize_outline_data(data["outline_data"])

            # Deserialize critique index and chapter critiques
            if "critique_index" in data:
                from novel_forge.memory.episodic import CritiqueIndex

                critique_index = getattr(self._episodic_memory, "_critique_index", {})
                chapter_critiques = getattr(self._episodic_memory, "_chapter_critiques", {})

                for sig, entry_data in data["critique_index"].items():
                    critique_entry = CritiqueIndex(
                        chapter_number=entry_data["chapter_number"],
                        issue_type=entry_data["issue_type"],
                        severity=entry_data["severity"],
                        summary=entry_data["summary"],
                        evidence=entry_data.get("evidence", ""),
                        suggested_fix=entry_data.get("suggested_fix", ""),
                        affected_chapters=entry_data.get("affected_chapters", []),
                        scene_index=entry_data.get("scene_index", -1),
                        embedding=[],
                        repair_attempts=entry_data.get("repair_attempts", []),
                        failure_pattern=entry_data.get("failure_pattern"),
                        lesson_learned=entry_data.get("lesson_learned"),
                        metadata=entry_data.get("metadata", {}),
                    )
                    critique_index[sig] = critique_entry

                # Restore chapter -> critiques mapping
                if "chapter_critiques" in data:
                    chapter_critiques.clear()
                    for k, v in data["chapter_critiques"].items():
                        chapter_critiques[int(k)] = v

                _log.info(
                    "critique_index_loaded | entries=%d | chapters=%d",
                    len(critique_index),
                    len(chapter_critiques),
                )

            try:
                dimension = int(vector_meta.get("dimension", 0) or 0)
            except Exception:
                dimension = 0
            if dimension > 0:
                ensure = getattr(self._episodic_memory, "_ensure_vector_store_for_dimension", None)
                if callable(ensure):
                    ensure(dimension, source="load_zvec_collection")
                    hydrate = getattr(self._episodic_memory, "hydrate_vector_store_metadata", None)
                    hydrated = hydrate() if callable(hydrate) else 0
                    _log.info(
                        "episodic_vector_store_opened | project=%s | backend=%s | "
                        "dimension=%d | hydrated_metadata=%d",
                        self._project_id,
                        getattr(self._episodic_memory, "vector_store_backend", ""),
                        dimension,
                        hydrated,
                    )

        except Exception as exc:
            _log.warning("Failed to deserialize episodic index: %s", exc)

    def _serialize_motif_tracker(self) -> dict[str, Any]:
        """Serialize motif tracker data for persistence."""
        return _serializer_serialize_motifs(self)

    def _deserialize_motif_tracker(self, data: dict[str, Any]) -> None:
        """Deserialize motif tracker data from persistence."""
        if not self._motif_tracker or not data:
            return

        try:
            from novel_forge.memory.motif import Motif

            motifs = getattr(self._motif_tracker, "_motifs", None)

            _valid_cats = {"意象", "动作", "感官", "颜色", "声音", "主题", "符号"}
            if motifs is not None and "motifs" in data:
                motifs.clear()
                for motif_id, motif_data in data["motifs"].items():
                    raw_cat = motif_data.get("category", "意象")
                    motif = Motif(
                        motif_id=motif_id,
                        name=motif_data.get("name", motif_id),
                        category=raw_cat if raw_cat in _valid_cats else "意象",
                        description=motif_data.get("description", ""),
                        first_appearance_chapter=motif_data.get("first_appearance_chapter", 0),
                        last_appearance_chapter=motif_data.get("last_appearance_chapter", 0),
                        occurrence_count=motif_data.get("occurrence_count", 0),
                        associated_characters=motif_data.get("associated_characters", []),
                        thematic_meaning=motif_data.get("thematic_meaning", ""),
                        is_intentional=motif_data.get("is_intentional", True),
                        retired=motif_data.get("retired", False),
                        metadata=motif_data.get("metadata", {})
                        if isinstance(motif_data.get("metadata", {}), dict)
                        else {},
                    )
                    motifs[motif_id] = motif

            # Restore chapter-to-motif mapping from persistence
            chapter_motifs = getattr(self._motif_tracker, "_chapter_motifs", None)
            if chapter_motifs is not None and "chapter_motifs" in data:
                chapter_motifs.clear()
                for ch_str, motif_ids in data["chapter_motifs"].items():
                    try:
                        chapter_num = int(ch_str)
                        if isinstance(motif_ids, list):
                            chapter_motifs[chapter_num] = set(motif_ids)
                    except (ValueError, TypeError):
                        _log.warning("Invalid chapter_motifs key: %s", ch_str)

        except Exception as exc:
            _log.warning("Failed to deserialize motif tracker: %s", exc)

    def _rebuild_motif_stats_from_cache(self) -> dict[str, Any]:
        """Rebuild motif tracker occurrence stats from motif_cache.

        The cache may contain richer per-chapter motif occurrences than the
        persisted tracker (for example after async extraction races or legacy
        saves).  This method merges missing stats from cache into the tracker
        without regressing existing values.
        """
        stats: dict[str, Any] = {
            "updated": False,
            "saved": False,
            "motifs_touched": 0,
            "occurrences_seen": 0,
            "created_motifs": 0,
        }
        if not self._motif_tracker or not self._motif_cache:
            return stats

        motifs = getattr(self._motif_tracker, "_motifs", None)
        if motifs is None:
            return stats

        chapter_motifs = getattr(self._motif_tracker, "_chapter_motifs", None)
        recent_usage = getattr(self._motif_tracker, "_recent_usage", None)
        MotifClass: Any | None
        try:
            from novel_forge.memory.motif import Motif as _MotifClass

            MotifClass = _MotifClass
        except Exception:
            MotifClass = None

        updated = 0
        chapter_usage_map: dict[str, set[int]] = {}
        occurrence_counts: dict[str, int] = {}
        _valid_cats = {"意象", "动作", "感官", "颜色", "声音", "主题", "符号"}

        for _ch, occurrences in self._motif_cache.items():
            if not isinstance(occurrences, list):
                continue
            for occ in occurrences:
                mid = ""
                ch_raw: Any = _ch
                motif_name = ""
                raw_cat = "意象"
                associated_chars: list[str] = []

                if isinstance(occ, dict):
                    mid = str(occ.get("motif_id", "") or "").strip()
                    ch_raw = occ.get("chapter_number", _ch)
                    motif_name = str(
                        occ.get("name", "") or occ.get("motif_name", "") or mid
                    ).strip()
                    raw_cat = str(occ.get("category", "意象") or "意象")
                    chars_raw = occ.get("associated_characters", occ.get("characters", []))
                    if isinstance(chars_raw, list):
                        associated_chars = [
                            str(item).strip() for item in chars_raw if str(item).strip()
                        ]
                else:
                    mid = str(getattr(occ, "motif_id", "") or "").strip()
                    ch_raw = getattr(occ, "chapter_number", _ch)
                    motif_name = str(
                        getattr(occ, "motif_name", "") or getattr(occ, "name", "") or mid
                    ).strip()
                    raw_cat = str(getattr(occ, "category", "意象") or "意象")
                    chars_raw = getattr(occ, "associated_characters", []) or getattr(
                        occ, "characters", []
                    )
                    if isinstance(chars_raw, list):
                        associated_chars = [
                            str(item).strip() for item in chars_raw if str(item).strip()
                        ]

                try:
                    ch_num = int(ch_raw or 0)
                except Exception:
                    ch_num = 0
                if not mid or ch_num <= 0:
                    continue
                if mid not in motifs and MotifClass is not None:
                    category = raw_cat if raw_cat in _valid_cats else "意象"
                    motifs[mid] = MotifClass(
                        motif_id=mid,
                        name=motif_name or mid,
                        category=category,  # type: ignore[arg-type]
                        description="",
                    )
                    stats["created_motifs"] = int(stats["created_motifs"]) + 1
                    updated += 1
                if mid not in motifs:
                    continue
                chapter_usage_map.setdefault(mid, set()).add(ch_num)
                occurrence_counts[mid] = occurrence_counts.get(mid, 0) + 1

                motif = motifs[mid]
                prev_count = int(getattr(motif, "occurrence_count", 0) or 0)
                motif.occurrence_count = max(prev_count, occurrence_counts[mid])
                if motif.occurrence_count != prev_count:
                    updated += 1

                first_ch = int(getattr(motif, "first_appearance_chapter", 0) or 0)
                if first_ch == 0 or ch_num < first_ch:
                    motif.first_appearance_chapter = ch_num
                    updated += 1

                last_ch = int(getattr(motif, "last_appearance_chapter", 0) or 0)
                if ch_num > last_ch:
                    motif.last_appearance_chapter = ch_num
                    updated += 1

                if associated_chars:
                    existing_chars = list(getattr(motif, "associated_characters", []) or [])
                    merged_chars = list(existing_chars)
                    for name in associated_chars:
                        if name not in merged_chars:
                            merged_chars.append(name)
                    if merged_chars != existing_chars:
                        motif.associated_characters = merged_chars
                        updated += 1

                stats["occurrences_seen"] = int(stats["occurrences_seen"]) + 1

        if isinstance(chapter_motifs, dict):
            for motif_id, chapters in chapter_usage_map.items():
                for chapter in chapters:
                    chapter_motifs.setdefault(chapter, set()).add(motif_id)

        if isinstance(recent_usage, dict):
            for motif_id, chapters in chapter_usage_map.items():
                existing = recent_usage.get(motif_id, [])
                merged = sorted({int(ch) for ch in existing if int(ch) > 0} | chapters)
                recent_usage[motif_id] = merged

        if stats["occurrences_seen"] > 0:
            stats["motifs_touched"] = len(chapter_usage_map)
            _log.info(
                "Rebuilt motif stats from cache | motifs_touched=%d | occurrences_seen=%d",
                stats["motifs_touched"],
                stats["occurrences_seen"],
            )
            if updated > 0:
                # Persist immediately so corrected stats are not lost again.
                stats["saved"] = bool(self.save_to_disk())
                stats["updated"] = True

        return stats

    def _build_motif_repair_context(self) -> MotifRepairContext:
        """Build live dependency context for motif repair orchestration."""
        return MotifRepairContext(
            settings=self.settings,
            storage=self._storage,
            project_id=self._project_id,
            motif_tracker=self._motif_tracker,
            motif_cache=self._motif_cache,
            ensure_lock=self._ensure_lock,
            rebuild_motif_stats_from_cache=self._rebuild_motif_stats_from_cache,
            save_to_disk=self.save_to_disk,
            logger=_log,
        )

    async def re_extract_motifs_from_archived_chapters(
        self,
        *,
        start_chapter: int = 1,
        end_chapter: int | None = None,
        on_progress: Callable[[int, int, str, int | None, str], None] | None = None,
    ) -> dict[str, Any]:
        """Layer 2: Re-extract motifs from archived chapter files when cache is empty.

        Reads ``chapters/chapter_NNN.md`` for each chapter in the range,
        runs ``MotifTracker.extract_from_chapter()``, and caches the result.
        Chapters that already have motif cache data are skipped.

        Args:
            start_chapter: First chapter to process (inclusive).
            end_chapter: Last chapter to process (inclusive). None = auto-detect
                from the highest numbered chapter file on disk.
            on_progress: Optional callback ``(processed, total, status, chapter)``
                called after each chapter is resolved.

        Returns:
            Stats dict with ``chapters_processed``, ``chapters_skipped``,
            ``motifs_extracted``, ``errors``.
        """
        return await _re_extract_motifs_from_archived_chapters_impl(
            self._build_motif_repair_context(),
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            on_progress=on_progress,
        )

    async def repair_motif_history_from_cache(
        self,
        *,
        force_re_extract: bool = False,
        start_chapter: int = 1,
        end_chapter: int | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Two-layer motif history repair.

        Layer 1: Rebuild stats from existing _motif_cache (always runs).
        Layer 2: If force_re_extract=True, re-extract from archived chapter
                 text for chapters with empty cache.
        """
        return await _repair_motif_history_from_cache_impl(
            self._build_motif_repair_context(),
            force_re_extract=force_re_extract,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            on_progress=on_progress,
        )

    def reset(self) -> None:
        """Reset all caches and state."""
        self._summary_cache.clear()
        self._chapter_content_hash.clear()
        self._summary_stats = self._normalize_summary_stats({})
        self._motif_cache.clear()
        self._last_indexed_chapter = 0
        # 清除母题追踪器中的所有数据
        if self._motif_tracker is not None:
            self._motif_tracker._motifs.clear()
            self._motif_tracker._occurrence_log.clear()
            self._motif_tracker._chapter_motifs.clear()
            self._motif_tracker._recent_usage.clear()
            self._motif_tracker._extraction_cache.clear()
        # 清除 episodic 记忆
        if self._episodic_memory is not None:
            all_chapters = list(self._episodic_memory._chapter_events.keys())
            for ch in all_chapters:
                self._episodic_memory.delete_chapter_memory(ch)
        _log.info("MemoryContext reset | project=%s", self._project_id)

    def invalidate_chapter_memory(self, from_chapter: int) -> dict[str, Any]:
        """Invalidate memory data for all chapters from from_chapter onwards.

        This is called when downstream chapters are invalidated after an upstream rewrite.
        It removes episodic memory, motif data, and cached summaries for all affected
        chapters to ensure memory consistency with the regenerated content.

        Args:
            from_chapter: Start chapter number (inclusive) to invalidate memory for

        Returns:
            Dictionary with counts of removed memory entries
        """
        return _invalidate_chapter_memory_impl(
            from_chapter=from_chapter,
            episodic_memory=self._episodic_memory,
            motif_tracker=self._motif_tracker,
            summary_cache=self._summary_cache,
            chapter_content_hash=self._chapter_content_hash,
            motif_cache=self._motif_cache,
            expression_memory=self._expression_memory,
            get_last_indexed_chapter=lambda: self._last_indexed_chapter,
            set_last_indexed_chapter=lambda value: setattr(
                self,
                "_last_indexed_chapter",
                value,
            ),
            logger=_log,
        )

    def invalidate_single_chapter_memory(self, chapter_number: int) -> dict[str, Any]:
        """Invalidate memory data for a single chapter.

        This is called when a specific chapter is regenerated to remove stale memory.

        Args:
            chapter_number: Chapter number to invalidate memory for

        Returns:
            Dictionary with counts of removed memory entries
        """
        return self.invalidate_chapter_memory(chapter_number)
