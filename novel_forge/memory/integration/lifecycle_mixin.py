"""Lifecycle methods for MemoryContext (shutdown, factory, warmup)."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from novel_forge.core.config import Settings
from novel_forge.memory.embedding_profiles import get_embedding_config_from_profiles
from novel_forge.memory.integration_canon import (
    extract_chapter_outline_from_canon as _canon_extract_chapter_outline,
)
from novel_forge.memory.integration_canon import (
    load_forbidden_seeds as _canon_load_forbidden_seeds,
)
from novel_forge.memory.summary_cache_helpers import (
    compute_summary_source_hash as _compute_summary_source_hash_impl,
)
from novel_forge.memory.summary_cache_helpers import (
    normalize_summary_cache_entry as _normalize_summary_cache_entry_impl,
)
from novel_forge.memory.summary_cache_helpers import (
    normalize_summary_stats as _normalize_summary_stats_impl,
)
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.prompts.builder import PromptBuilder


_log = get_logger("memory.context")


class LifecycleMixin:
    """Mixin providing lifecycle management methods."""

    async def shutdown(self) -> None:
        """Best-effort cleanup for background tasks and memory resources.

        Shutdown is also the last chance to persist in-memory summary, motif,
        and vector state before an LRU eviction or runtime reload detaches this
        context.
        """
        try:
            await self.flush_pending_tasks(timeout_s=2.0)
        except Exception as exc:
            _log.debug("memory_shutdown_flush_pending_failed | error=%s", exc)

        if self._pending_tasks:
            loop = asyncio.get_running_loop()
            local_tasks: list[asyncio.Task[Any]] = []
            for task in list(self._pending_tasks):
                try:
                    if task.get_loop() is not loop:
                        continue
                except Exception:
                    continue
                task.cancel()
                local_tasks.append(task)
            if local_tasks:
                await asyncio.gather(*local_tasks, return_exceptions=True)
            self._pending_tasks.clear()

        try:
            self.save_to_disk()
        except Exception as exc:
            _log.debug("memory_shutdown_save_failed | error=%s", exc)

        expression_memory = self._expression_memory
        self._expression_memory = None
        if expression_memory is not None:
            try:
                save = getattr(expression_memory, "save", None)
                if callable(save):
                    save()
            except Exception as exc:
                _log.debug("expression_memory_shutdown_save_failed | error=%s", exc)

        narrative_evidence = self._narrative_evidence_service
        self._narrative_evidence_service = None
        if narrative_evidence is not None:
            try:
                flush = getattr(narrative_evidence, "flush", None)
                if callable(flush):
                    flush()
            except Exception as exc:
                _log.debug("narrative_evidence_shutdown_flush_failed | error=%s", exc)

        story_kernel_store = self._story_kernel_store
        self._story_kernel_store = None
        self._entity_knowledge_service = None
        self._relationship_query_service = None
        self._foreshadow_reminder = None
        if story_kernel_store is not None:
            try:
                close = getattr(story_kernel_store, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except Exception as exc:
                _log.debug("memory_projection_store_shutdown_failed | error=%s", exc)

        episodic = self._episodic_memory
        self._episodic_memory = None
        if episodic is not None:
            close_hook = getattr(episodic, "shutdown", None)
            if not callable(close_hook):
                close_hook = getattr(episodic, "aclose", None)
            if callable(close_hook):
                result = close_hook()
                if inspect.isawaitable(result):
                    await result

    async def aclose(self) -> None:
        """Alias for ``shutdown``."""
        await self.shutdown()

    @classmethod
    def create_from_settings(
        cls,
        router: "ModelRouter",
        builder: "PromptBuilder",
        settings: Settings,
        project_id: str = "",
        storage: "FileSystemStorage | None" = None,
    ) -> Self:
        """Create a MemoryContext from settings.

        Args:
            router: Model router
            builder: Prompt builder
            settings: Application settings
            project_id: Project identifier for persistence
            storage: Storage backend for persistence (optional)

        Returns:
            Initialized MemoryContext
        """
        from novel_forge.memory.compression import AdaptiveCompressionService
        from novel_forge.memory.critic import CriticAgent
        from novel_forge.memory.episodic import EpisodicMemory
        from novel_forge.memory.motif import MotifTracker
        from novel_forge.memory.style_rule_tracker import StyleRuleTracker
        from novel_forge.memory.summary import MultiGranularitySummaryService, SummaryConfig

        vector_store_path: Path | None = None
        if storage is not None and project_id:
            vector_store_path = storage.root / project_id / "memory" / "zvec_vectors"
        expression_vector_store_path: Path | None = None
        expression_state_path: Path | None = None
        if storage is not None and project_id:
            expression_vector_store_path = (
                storage.root / project_id / "memory" / "zvec_expression_vectors"
            )
            expression_state_path = (
                storage.root / project_id / "memory" / "expression_channel_memory.json"
            )

        vector_store_backend = (
            str(getattr(settings, "memory_vector_store_backend", "zvec") or "zvec")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if getattr(settings, "memory_use_mock_embeddings", False):
            vector_store_backend = "in_memory"

        vector_store_kwargs: dict[str, Any] = {
            "vector_store_backend": vector_store_backend,
            "vector_store_path": vector_store_path,
            "zvec_index_type": getattr(settings, "memory_zvec_index_type", "hnsw"),
            "zvec_memory_limit_mb": int(
                getattr(settings, "memory_zvec_memory_limit_mb", 512) or 512
            ),
        }

        episodic_memory: EpisodicMemory | None = None
        if settings.memory_episodic_enabled:
            if vector_store_backend == "zvec" and vector_store_path is None:
                raise RuntimeError(
                    "Zvec memory backend requires a storage-backed project path. "
                    "Pass storage and project_id when creating MemoryContext, or use "
                    "NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND=in_memory only with mock embeddings/tests."
                )
            if getattr(settings, "memory_use_mock_embeddings", False):
                _log.info("using_mock_embeddings | forced_by_settings=true")
                episodic_memory = EpisodicMemory(
                    use_mock_embeddings=True,
                    **vector_store_kwargs,
                )
                embedding_mode = "mock"
            else:
                embedding_profile_id = getattr(settings, "memory_embedding_profile_id", None)
                embedding_config = get_embedding_config_from_profiles(embedding_profile_id)

                embedding_mode = "none"
                if embedding_config:
                    _log.info(
                        "using_embedding_from_profile | model=%s | profile_id=%s",
                        embedding_config.get("model", "unknown"),
                        embedding_profile_id or "auto",
                    )
                    episodic_memory = EpisodicMemory(
                        embedding_config=embedding_config,
                        use_mock_embeddings=False,
                        **vector_store_kwargs,
                    )
                    embedding_mode = "profile"
                else:
                    ollama_config = {
                        "provider": "ollama",
                        "model": getattr(settings, "ollama_embedding_model", "nomic-embed-text"),
                        "base_url": getattr(
                            settings, "ollama_base_url", "http://localhost:11434/v1"
                        ),
                    }
                    _log.warning(
                        "using_ollama_embedding_fallback | No embedding profile found in model_profiles. "
                        "Falling back to local Ollama. Semantic search quality may be degraded "
                        "if Ollama is not running. | model=%s | base_url=%s",
                        ollama_config["model"],
                        ollama_config["base_url"],
                    )
                    episodic_memory = EpisodicMemory(
                        embedding_config=ollama_config,
                        use_mock_embeddings=False,
                        **vector_store_kwargs,
                    )
                    embedding_mode = "ollama_fallback"
        else:
            embedding_mode = "none"

        summary_service = (
            MultiGranularitySummaryService(
                router=router,
                builder=builder,
                config=SummaryConfig(
                    chapter_target_words=int(
                        getattr(settings, "memory_chapter_summary_target_words", 200) or 200
                    ),
                    volume_target_words=int(
                        getattr(settings, "memory_volume_summary_target_words", 1000) or 1000
                    ),
                    input_token_budget=int(
                        getattr(settings, "memory_summary_input_token_budget", 24000) or 24000
                    ),
                    context_recent_chapters=int(
                        getattr(settings, "memory_summary_recent_chapters", 5) or 5
                    ),
                ),
            )
            if settings.memory_multi_granularity_summary_enabled
            else None
        )

        compression_service = (
            AdaptiveCompressionService(
                router=router,
                builder=builder,
                episodic_memory=episodic_memory,
            )
            if settings.memory_adaptive_compression_enabled
            else None
        )

        motif_tracker = (
            MotifTracker(
                router=router,
                builder=builder,
                episodic_memory=episodic_memory,
            )
            if settings.memory_motif_tracking_enabled
            else None
        )

        style_rule_tracker = (
            StyleRuleTracker(
                lookback_chapters=int(
                    getattr(settings, "style_rule_repetition_lookback_chapters", 5) or 5
                ),
                repetition_gap_chapters=int(
                    getattr(settings, "style_rule_repetition_recent_gap_chapters", 2) or 2
                ),
            )
            if getattr(settings, "memory_style_rule_tracking_enabled", True)
            else None
        )

        critic_agent = (
            CriticAgent(
                router=router,
                builder=builder,
                episodic_memory=episodic_memory,
                motif_tracker=motif_tracker,
                style_rule_tracker=style_rule_tracker,
                check_timeout_s=float(
                    getattr(settings, "memory_critic_agent_timeout_s", 45.0) or 45.0
                ),
                timeout_extend_attempts=int(
                    getattr(settings, "memory_critic_agent_timeout_extend_attempts", 1) or 0
                ),
                timeout_extend_multiplier=float(
                    getattr(settings, "memory_critic_agent_timeout_extend_multiplier", 1.5) or 1.0
                ),
                cache_enabled=bool(getattr(settings, "memory_critic_agent_cache_enabled", True)),
                cache_max_entries=int(
                    getattr(settings, "memory_critic_agent_cache_max_entries", 24) or 24
                ),
                motif_repetition_lookback_chapters=int(
                    getattr(settings, "motif_repetition_lookback_chapters", 5) or 5
                ),
                motif_repetition_recent_gap_chapters=int(
                    getattr(settings, "motif_repetition_recent_gap_chapters", 2) or 2
                ),
                style_rule_lookback_chapters=int(
                    getattr(settings, "style_rule_repetition_lookback_chapters", 5) or 5
                ),
                style_rule_repetition_gap_chapters=int(
                    getattr(settings, "style_rule_repetition_recent_gap_chapters", 2) or 2
                ),
            )
            if settings.memory_critic_agent_enabled
            else None
        )

        expression_memory = None
        if (
            getattr(settings, "expression_channel_detection_enabled", True)
            and getattr(settings, "expression_channel_zvec_memory_enabled", True)
            and settings.memory_semantic_search_enabled
            and episodic_memory is not None
            and expression_state_path is not None
        ):
            try:
                from novel_forge.memory.expression_channel import ExpressionChannelMemory

                expression_memory = ExpressionChannelMemory(
                    project_id=project_id,
                    state_path=expression_state_path,
                    vector_store_path=expression_vector_store_path,
                    embedding_source=episodic_memory,
                    vector_store_backend=vector_store_backend,
                    zvec_index_type=getattr(settings, "memory_zvec_index_type", "hnsw"),
                    zvec_memory_limit_mb=int(
                        getattr(settings, "memory_zvec_memory_limit_mb", 512) or 512
                    ),
                )
            except Exception as exc:
                _log.warning(
                    "expression_memory_init_failed | project=%s | error=%s", project_id, exc
                )

        narrative_evidence_service = None
        if episodic_memory is not None and storage is not None and project_id:
            try:
                from novel_forge.memory.narrative_evidence import (
                    NarrativeEvidenceIndex,
                    NarrativeEvidenceService,
                )

                evidence_index = NarrativeEvidenceIndex(
                    root=storage.root / project_id / "narrative_state" / "evidence_index",
                    embedding_provider=episodic_memory,
                    backend=vector_store_backend,
                    index_type=getattr(settings, "memory_zvec_index_type", "hnsw"),
                    memory_limit_mb=int(
                        getattr(settings, "memory_zvec_memory_limit_mb", 512) or 512
                    ),
                )
                narrative_evidence_service = NarrativeEvidenceService(evidence_index)
            except Exception as exc:
                _log.warning(
                    "narrative_evidence_service_init_failed | project=%s | error=%s",
                    project_id,
                    exc,
                )

        story_kernel_store = None
        entity_knowledge_service = None
        relationship_query_service = None
        foreshadow_reminder = None
        if storage is not None and project_id:
            try:
                from novel_forge.memory.entity_knowledge import EntityKnowledgeService
                from novel_forge.memory.foreshadow_reminder import ForeshadowReminder
                from novel_forge.memory.relationship_query import RelationshipQueryService
                from novel_forge.persistence.models import ProjectLayout
                from novel_forge.story_kernel.store import StoryKernelStore

                layout = ProjectLayout(storage.ensure_project_dir(project_id))
                configured_db_path = str(getattr(settings, "story_kernel_db_path", "") or "")
                story_kernel_db_path = (
                    Path(configured_db_path).expanduser()
                    if configured_db_path.strip()
                    else layout.story_kernel_db_path
                )
                if story_kernel_db_path.exists():
                    story_kernel_store = StoryKernelStore(
                        story_kernel_db_path,
                        wal_mode=bool(getattr(settings, "story_kernel_wal_mode", True)),
                    )
                    entity_knowledge_service = EntityKnowledgeService(
                        project_id=project_id,
                        store=story_kernel_store,
                    )
                    relationship_query_service = RelationshipQueryService(
                        project_id=project_id,
                        store=story_kernel_store,
                    )
                    foreshadow_reminder = ForeshadowReminder(
                        project_id=project_id,
                        store=story_kernel_store,
                    )
                else:
                    _log.debug(
                        "memory_projection_services_skipped | project=%s | missing_story_kernel=%s",
                        project_id,
                        story_kernel_db_path,
                    )
            except Exception as exc:
                _log.warning(
                    "memory_projection_services_init_failed | project=%s | error=%s",
                    project_id,
                    exc,
                )

        ctx = cls(
            settings=settings,
            _project_id=project_id,
            _storage=storage,
            _router=router,
            _builder=builder,
            _episodic_memory=episodic_memory,
            _summary_service=summary_service,
            _compression_service=compression_service,
            _motif_tracker=motif_tracker,
            _style_rule_tracker=style_rule_tracker,
            _critic_agent=critic_agent,
            _expression_memory=expression_memory,
            _story_kernel_store=story_kernel_store,
            _entity_knowledge_service=entity_knowledge_service,
            _relationship_query_service=relationship_query_service,
            _foreshadow_reminder=foreshadow_reminder,
            _narrative_evidence_service=narrative_evidence_service,
            _embedding_mode=embedding_mode,
        )

        # Wire storage on services that need it for persistence
        if storage is not None and project_id:
            if summary_service is not None and getattr(summary_service, "_storage", None) is None:
                summary_service.set_storage(storage, project_id)
            ctx.load_from_disk()

        _log.info(
            "MemoryContext initialized | project=%s | episodic=%s | motifs=%s | summaries=%s",
            project_id,
            bool(episodic_memory),
            bool(motif_tracker),
            bool(summary_service),
        )

        return ctx

    def get_cached_summary(self, chapter: int) -> str | None:
        """Get cached chapter summary if available.

        Delegates to the summary service as the single source of truth,
        falling back to the legacy ``_summary_cache`` for backward compatibility.
        """
        # Primary: delegate to summary service
        if self._summary_service is not None:
            text = self._summary_service.get_summary("chapter", chapter)
            if text:
                return str(text)

        # Fallback: legacy _summary_cache
        raw_entry = self._summary_cache.get(chapter)
        entry = (
            raw_entry
            if isinstance(raw_entry, dict)
            else self._normalize_summary_cache_entry(raw_entry)
        )
        if raw_entry is not None and entry is not None and not isinstance(raw_entry, dict):
            self._summary_cache[chapter] = entry
            self._summary_stats["legacy_migrated"] = (
                self._summary_stats.get("legacy_migrated", 0) + 1
            )
        if not entry:
            return None
        text = str(entry.get("text", "") or "")
        return text or None

    def _load_forbidden_seeds(self) -> dict[str, list[str]] | None:
        """Load forbidden element seeds from project config for motif warmup."""
        return _canon_load_forbidden_seeds(self)

    async def warm_start_from_init_artifacts(self) -> dict[str, Any]:
        """Prime first-chapter memory from artifacts produced by ``init_long``.

        This is intentionally best-effort and avoids LLM calls.  The goal is to
        make the already-generated outline/motif seed material visible before
        chapter 1 planning starts, instead of waiting for the first chapter's
        post-finalize memory update.
        """
        stats: dict[str, Any] = {
            "outline_episodic_loaded": False,
            "motif_warmup": False,
            "saved": False,
            "errors": [],
        }

        if self._episodic_memory is not None:
            try:
                before = len(getattr(self._episodic_memory, "_outline_index", {}) or {})
                self._load_outline_episodic_from_init()
                after = len(getattr(self._episodic_memory, "_outline_index", {}) or {})
                stats["outline_episodic_loaded"] = after > 0 or after != before
                stats["outline_episodic_count"] = after
            except Exception as exc:
                stats["errors"].append(f"outline_episodic: {exc}")
                _log.debug("init_memory_outline_warm_start_failed | error=%s", exc)

        if self._motif_tracker is not None and not getattr(
            self._motif_tracker, "_is_warmup_complete", False
        ):
            try:
                story_themes: list[str] | None = None
                if self._storage is not None and self._project_id:
                    try:
                        from novel_forge.core.schemas.bible import StoryBible
                        from novel_forge.persistence.models import ProjectLayout

                        layout = ProjectLayout(self._storage.ensure_project_dir(self._project_id))
                        if self._storage.exists(layout.bible_path):
                            story_bible_data = self._storage.load_json(layout.bible_path)
                            if story_bible_data:
                                story_bible = StoryBible.model_validate(story_bible_data)
                                story_themes = list(getattr(story_bible, "themes", []) or [])
                    except Exception as exc:
                        _log.debug(
                            "init_memory_story_bible_load_failed | project=%s | error=%s",
                            self._project_id,
                            exc,
                        )

                warmup = getattr(self._motif_tracker, "warmup", None)
                if callable(warmup):
                    await warmup(
                        project_seeds=self._load_forbidden_seeds(),
                        bible_data=None,
                        genre_template=None,
                        story_themes=story_themes,
                    )
                    stats["motif_warmup"] = True
                    stats["motif_count"] = len(getattr(self._motif_tracker, "_motifs", {}) or {})
            except Exception as exc:
                stats["errors"].append(f"motif_warmup: {exc}")
                _log.warning("init_memory_motif_warm_start_failed | error=%s", exc)

        if stats["outline_episodic_loaded"] or stats["motif_warmup"]:
            try:
                stats["saved"] = self.save_to_disk()
            except Exception as exc:
                stats["errors"].append(f"save: {exc}")
                _log.debug("init_memory_warm_start_save_failed | error=%s", exc)

        return stats

    def _extract_chapter_outline_from_canon(
        self,
        chapter_number: int,
    ) -> dict[str, Any] | None:
        """Extract chapter outline data from canon state for motif extraction context."""
        return _canon_extract_chapter_outline(self, chapter_number)

    @staticmethod
    def _compute_summary_source_hash(text: str) -> str:
        """Compute deterministic hash for chapter text."""
        return _compute_summary_source_hash_impl(text)

    @staticmethod
    def _normalize_summary_cache_entry(raw: Any) -> dict[str, Any] | None:
        """Normalize summary cache entry from legacy/new formats."""
        return _normalize_summary_cache_entry_impl(raw)

    @staticmethod
    def _normalize_summary_stats(raw: Any) -> dict[str, int]:
        """Normalize persisted summary stats structure."""
        return _normalize_summary_stats_impl(raw)

    def get_cached_motifs(self, chapter: int) -> list[Any] | None:
        """Get cached motif data if available."""
        return self._motif_cache.get(chapter)

    def get_chapter_exit_state(self, chapter: int) -> dict[str, Any] | None:
        """Get chapter exit state from canon state for memory-enhanced audit.

        This method retrieves the exit state of a specific chapter from the
        canon state, providing critical context for continuity and causal validation.

        Args:
            chapter: Chapter number to get exit state for

        Returns:
            Dictionary with chapter exit state info, or None if not available
        """
        if not self._storage or not self._project_id:
            return None

        try:
            canon_path = (
                self._storage.project_path(self._project_id) / "canon" / "canon_current.json"
            )
            if not self._storage.exists(canon_path):
                return None

            canon_state = self._storage.load_json(canon_path)
            chapter_exit_states = canon_state.get("chapter_exit_states", {})

            if str(chapter) in chapter_exit_states:
                exit_state = chapter_exit_states[str(chapter)]
                if isinstance(exit_state, dict):
                    return {
                        "chapter_number": exit_state.get("chapter_number", chapter),
                        "time_marker": exit_state.get("time_marker", ""),
                        "location": exit_state.get("location", ""),
                        "pov": exit_state.get("pov", ""),
                        "active_goals": exit_state.get("active_goals", []),
                        "open_questions": exit_state.get("open_questions", []),
                        "must_carry_forward": exit_state.get("must_carry_forward", []),
                        "character_end_states": exit_state.get("character_end_states", {}),
                    }
                elif hasattr(exit_state, "model_dump"):
                    dumped = exit_state.model_dump(mode="json")
                    return dumped if isinstance(dumped, dict) else None

            return None

        except Exception as exc:
            _log.warning("Failed to get chapter exit state for chapter %d: %s", chapter, exc)
            return None
