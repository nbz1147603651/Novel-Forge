"""Memory integration helpers for ChapterRunner.

Provides a unified MemoryContext that bundles all memory services
for easy integration into the chapter generation pipeline.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Coroutine

from novel_forge.core.config import Settings
from novel_forge.memory.embedding_profiles import get_embedding_config_from_profiles
from novel_forge.memory.integration.finalize_mixin import FinalizeMixin
from novel_forge.memory.integration.lifecycle_mixin import LifecycleMixin
from novel_forge.memory.integration.persistence_mixin import PersistenceMixin
from novel_forge.memory.integration.retrieval_mixin import RetrievalMixin
from novel_forge.memory.integration_config import MemoryIntegrationConfig
from novel_forge.memory.integration_utils import (
    plan_field as _plan_field,
)
from novel_forge.memory.integration_utils import (
    plan_list_field as _plan_list_field,
)
from novel_forge.memory.retrieval import (
    bm25_scores as _bm25_scores,
)
from novel_forge.memory.retrieval import (
    tokenize_bm25_text as _tokenize_bm25_text,
)
from novel_forge.memory.vector_serialization import (
    pack_embedding as _pack_embedding,
)
from novel_forge.memory.vector_serialization import (
    unpack_embedding as _unpack_embedding,
)
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.prompts.builder import PromptBuilder

_log = get_logger("memory.context")
__all__ = (
    "MemoryContext",
    "MemoryIntegrationConfig",
    "get_embedding_config_from_profiles",
    "_bm25_scores",
    "_tokenize_bm25_text",
    "_pack_embedding",
    "_plan_field",
    "_plan_list_field",
    "_unpack_embedding",
)


@dataclass
class MemoryContext(FinalizeMixin, LifecycleMixin, RetrievalMixin, PersistenceMixin):
    """Unified context bundling all memory services.

    This is the main integration point for memory services in ChapterRunner.

    Memory data is persisted to the project directory, ensuring that memory
    survives application restarts and is properly scoped to each project.
    """

    settings: Settings = field(default_factory=Settings)
    _project_id: str = field(default="", repr=False)
    _storage: "FileSystemStorage | None" = field(default=None, repr=False)

    _router: "ModelRouter | None" = field(default=None, repr=False)
    _builder: "PromptBuilder | None" = field(default=None, repr=False)

    _summary_cache: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _chapter_content_hash: dict[int, str] = field(default_factory=dict, repr=False)
    _summary_stats: dict[str, int] = field(
        default_factory=lambda: {
            "generated": 0,
            "regenerated": 0,
            "hash_skips": 0,
            "legacy_migrated": 0,
            "invalidated_short": 0,
            "reindexed": 0,
        },
        repr=False,
    )
    _motif_cache: dict[int, list[Any]] = field(default_factory=dict, repr=False)
    _last_indexed_chapter: int = 0
    _lock: asyncio.Lock = field(default=None, repr=False)  # type: ignore[assignment]
    _lock_init_guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _episodic_memory: Any = field(default=None, repr=False)
    _summary_service: Any = field(default=None, repr=False)
    _compression_service: Any = field(default=None, repr=False)
    _motif_tracker: Any = field(default=None, repr=False)
    _style_rule_tracker: Any = field(default=None, repr=False)
    _critic_agent: Any = field(default=None, repr=False)
    _expression_memory: Any = field(default=None, repr=False)
    _story_kernel_store: Any = field(default=None, repr=False)
    _entity_knowledge_service: Any = field(default=None, repr=False)
    _relationship_query_service: Any = field(default=None, repr=False)
    _foreshadow_reminder: Any = field(default=None, repr=False)
    _narrative_evidence_service: Any = field(default=None, repr=False)

    _progress_callback: Any = field(default=None, repr=False)
    _pending_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    _embedding_mode: str = field(
        default="none", repr=False
    )  # "mock", "profile", "ollama_fallback", or "none"

    _outline: Any = field(default=None, repr=False)  # StoryOutline | None
    _outline_loaded: bool = field(default=False, repr=False)  # guard for lazy loading
    _summary_service_disk_load_attempted: bool = field(default=False, repr=False)
    _narrative_evidence_bootstrap_attempted: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        # _lock is intentionally None by default so dataclass creation
        # works outside of an event loop (Python 3.9 compat).
        pass

    def _ensure_lock(self) -> asyncio.Lock:
        if self._lock is None:
            with self._lock_init_guard:
                if self._lock is None:
                    self._lock = asyncio.Lock()
        return self._lock

    @property
    def episodic_memory(self) -> Any:
        return self._episodic_memory

    @property
    def expression_memory(self) -> Any:
        return self._expression_memory

    @property
    def entity_knowledge_service(self) -> Any:
        return self._entity_knowledge_service

    @property
    def relationship_query_service(self) -> Any:
        return self._relationship_query_service

    @property
    def foreshadow_reminder(self) -> Any:
        return self._foreshadow_reminder

    @property
    def narrative_evidence_service(self) -> Any:
        return self._narrative_evidence_service

    @property
    def summary_service(self) -> Any:
        return self._summary_service

    @property
    def compression_service(self) -> Any:
        return self._compression_service

    @property
    def motif_tracker(self) -> Any:
        return self._motif_tracker

    @property
    def style_rule_tracker(self) -> Any:
        return self._style_rule_tracker

    @property
    def critic_agent(self) -> Any:
        return self._critic_agent

    def set_outline(self, outline: Any) -> None:
        """Bind StoryOutline for volume-aware context injection."""
        self._outline = outline
        self._outline_loaded = True

    def _load_outline(self) -> Any:
        """Lazy-load outline from disk if not already bound."""
        if self._outline_loaded:
            return self._outline
        if not self._storage or not self._project_id:
            self._outline_loaded = True
            return None
        try:
            outline_path = self._storage.project_path(self._project_id) / "outline.json"
            if not self._storage.exists(outline_path):
                self._outline_loaded = True
                return None
            from novel_forge.core.schemas.outline import StoryOutline

            raw = self._storage.load_json(outline_path)
            self._outline = StoryOutline.model_validate(raw)
        except Exception as exc:
            _log.warning("Failed to lazy-load outline: %s", exc)
            self._outline = None
        self._outline_loaded = True
        return self._outline

    @property
    def project_id(self) -> str:
        return self._project_id

    def _find_volume_for_chapter(
        self,
        chapter_number: int,
    ) -> "tuple[Any, Any] | tuple[None, None]":
        """Return (current_volume, previous_volume) for the given chapter.

        Uses lazy-loaded outline.  Returns (None, None) if no outline or volume_mode is off.
        """
        outline = self._load_outline()
        if outline is None:
            return None, None
        volume_mode = getattr(outline, "volume_mode", False)
        if not volume_mode:
            return None, None
        volumes = getattr(outline, "volumes", [])
        if not volumes:
            return None, None

        current_vol = None
        prev_vol = None
        for vol in volumes:
            start = getattr(vol, "start_chapter", 0)
            end = getattr(vol, "end_chapter", 0)
            if start <= chapter_number <= end:
                current_vol = vol
                break
            if end < chapter_number:
                prev_vol = vol

        return current_vol, prev_vol

    def _volume_eviction_threshold(
        self,
        *,
        volume_number: int,
        start_chapter: int,
        end_chapter: int,
        keep_recent_volumes: int,
    ) -> int | None:
        """Return the first chapter that should remain after volume-boundary eviction."""
        keep_recent_volumes = max(1, int(keep_recent_volumes or 1))
        volume_number = int(volume_number or 0)
        start_chapter = int(start_chapter or 0)
        end_chapter = int(end_chapter or 0)
        if volume_number <= keep_recent_volumes or start_chapter <= 1:
            return None

        first_kept_volume = volume_number - keep_recent_volumes + 1
        outline = self._load_outline()
        if outline is not None and getattr(outline, "volume_mode", False):
            volumes = sorted(
                list(getattr(outline, "volumes", []) or []),
                key=lambda vol: int(getattr(vol, "volume_number", 0) or 0),
            )
            for vol in volumes:
                if int(getattr(vol, "volume_number", 0) or 0) == first_kept_volume:
                    threshold = int(getattr(vol, "start_chapter", 0) or 0)
                    return threshold if threshold > 1 else None

        span = max(1, end_chapter - start_chapter + 1)
        threshold = start_chapter - (keep_recent_volumes - 1) * span
        return threshold if threshold > 1 else None

    def set_progress_callback(self, callback: Any) -> None:
        """Set a progress callback for memory indexing operations.

        The callback should accept two arguments: (stage: str, data: dict)
        Valid stages: 'indexing_started', 'episodic_done', 'motifs_extracted',
                     'motifs_completed', 'summary_scheduled',
                     'summary_generated', 'indexing_complete'

        Args:
            callback: Callable that accepts (stage, data) arguments
        """
        self._progress_callback = callback

    def _emit_progress(self, stage: str, data: dict[str, Any]) -> None:
        """Emit progress event if callback is set."""
        if self._progress_callback is not None:
            try:
                self._progress_callback(stage, data)
            except Exception as exc:
                _log.warning("Progress callback failed: %s", exc)

    def _schedule_background_task(self, coro: Coroutine[Any, Any, Any], *, tag: str) -> bool:
        """Create and track a background task for memory async work."""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return False

        self._pending_tasks.add(task)

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            self._pending_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                _log.debug("Memory background task cancelled | tag=%s", tag)
            except Exception as exc:
                _log.warning("Memory background task failed | tag=%s | error=%s", tag, exc)

        task.add_done_callback(_on_done)
        return True

    async def flush_pending_tasks(self, timeout_s: float = 0.0) -> dict[str, int]:
        """Wait briefly for pending memory tasks to complete.

        Returns:
            dict with keys ``completed`` and ``pending``.
        """
        if not self._pending_tasks:
            return {"completed": 0, "pending": 0}

        if timeout_s <= 0:
            await asyncio.sleep(0)
            return {"completed": 0, "pending": len(self._pending_tasks)}

        tasks = list(self._pending_tasks)
        done, pending = await asyncio.wait(tasks, timeout=max(0.05, float(timeout_s)))
        return {"completed": len(done), "pending": len(pending)}
