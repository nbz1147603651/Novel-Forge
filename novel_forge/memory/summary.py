"""Multi-Granularity Summary Service.

Provides hierarchical summarization at scene, chapter, volume, and arc levels,
enabling context injection at appropriate granularity for different tasks.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.token_utils import estimate_chinese_tokens
from novel_forge.core.utils.text_validation import count_chapter_words

if TYPE_CHECKING:
    from novel_forge.core.schemas.canon import CreativeReport
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.story_kernel.schemas import StoryKernel
from novel_forge.core.schemas.outline import StoryOutline, VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder

_log = get_logger("memory.summary")


@dataclass
class SummaryAtGranularity:
    """Summary at a specific granularity level."""

    granularity: Literal["scene", "chapter", "volume", "arc"]
    chapter_start: int
    chapter_end: int
    text: str
    word_count: int = 0
    key_events: list[str] = field(default_factory=list)
    character_arcs: list[str] = field(default_factory=list)
    thematic_elements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.word_count == 0:
            self.word_count = count_chapter_words(self.text)


@dataclass
class SummaryConfig:
    """Configuration for summary generation."""

    # Target word counts for each granularity
    scene_target_words: int = 50
    chapter_target_words: int = 200
    volume_target_words: int = 800
    arc_target_words: int = 2000
    input_token_budget: int = 24000
    context_recent_chapters: int = 5

    # Content priorities
    prioritize_plot: bool = True
    prioritize_character: bool = True
    prioritize_world_building: bool = False
    prioritize_theme: bool = False

    # Compression settings
    max_compression_ratio: float = 0.3  # Max 30% of original
    preserve_dialogue_snippets: bool = False
    preserve_key_quotes: bool = True


class MultiGranularitySummaryService:
    """Generates and manages summaries at multiple granularity levels.

    Granularity levels:
    - scene: Individual scene summaries (50-100 words)
    - chapter: Chapter-level summaries (200-400 words)
    - volume: Volume-level summaries (800-1500 words)
    - arc: Story arc summaries (2000-5000 words)
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        config: SummaryConfig | None = None,
    ) -> None:
        self._router = router
        self._builder = builder
        self._config = config or SummaryConfig()

        # Cache for generated summaries
        self._scene_summaries: dict[tuple[int, int], str] = {}  # (chapter, scene_idx) -> text
        self._chapter_summaries: dict[int, str] = {}
        self._volume_summaries: dict[int, str] = {}
        self._arc_summaries: dict[str, str] = {}  # arc_name -> text

        # Persistence interface
        self._storage: "FileSystemStorage | None" = None
        self._project_id: str = ""

        _log.info(
            "MultiGranularitySummaryService initialized | config=%s",
            {
                "chapter_target": self._config.chapter_target_words,
                "volume_target": self._config.volume_target_words,
                "arc_target": self._config.arc_target_words,
            },
        )

    async def generate_scene_summary(
        self,
        chapter_number: int,
        scene_text: str,
        scene_index: int = 0,
        scene_title: str = "",
    ) -> SummaryAtGranularity:
        """Generate a scene-level summary (≈50 words).

        Args:
            chapter_number: Chapter the scene belongs to.
            scene_text: Full text of the scene.
            scene_index: Zero-based scene index within the chapter.
            scene_title: Optional scene title / label.

        Returns:
            SummaryAtGranularity with granularity="scene".
        """
        start_time = time.monotonic()
        target_words = self._config.scene_target_words

        _log.info(
            "scene_summary_start | chapter=%d | scene=%d | text_length=%d | target_words=%d",
            chapter_number,
            scene_index,
            len(scene_text),
            target_words,
        )

        try:
            if len(scene_text) <= target_words * 3:
                # Already compact enough: preserve the complete scene.
                summary_text = scene_text
                _log.debug(
                    "scene_summary_direct | chapter=%d | scene=%d | text_short",
                    chapter_number,
                    scene_index,
                )
            else:
                summary_text = await self._llm_generate_summary(
                    text=scene_text,
                    target_words=target_words,
                    task_type=TaskType.SUMMARIZE_SCENE,
                    context={
                        "chapter_number": str(chapter_number),
                        "scene_index": str(scene_index),
                        "scene_title": scene_title,
                    },
                )

            quality = self._verify_summary_quality(
                original_length=len(scene_text),
                summary_text=summary_text,
                level="scene",
            )

            compression_ratio = len(summary_text) / len(scene_text) if scene_text else 0
            elapsed_ms = (time.monotonic() - start_time) * 1000

            result = SummaryAtGranularity(
                granularity="scene",
                chapter_start=chapter_number,
                chapter_end=chapter_number,
                text=summary_text,
                metadata={
                    "scene_index": scene_index,
                    "scene_title": scene_title,
                    "original_length": len(scene_text),
                    "compression_ratio": compression_ratio,
                    "quality_score": quality["score"],
                    "quality_warnings": quality["warnings"],
                },
            )

            # Cache the summary
            self._scene_summaries[(chapter_number, scene_index)] = summary_text

            _log.info(
                "scene_summary_done | chapter=%d | scene=%d | summary_length=%d | "
                "compression_ratio=%.2f | quality_score=%.2f | elapsed_ms=%.2f",
                chapter_number,
                scene_index,
                len(summary_text),
                compression_ratio,
                quality["score"],
                elapsed_ms,
            )

            return result

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "scene_summary_failed | chapter=%d | scene=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                scene_index,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    async def generate_chapter_summary(
        self,
        chapter_number: int,
        chapter_text: str,
        outline: Any | None = None,
        creative_report: CreativeReport | None = None,
    ) -> SummaryAtGranularity:
        """Generate a chapter-level summary.

        Args:
            chapter_number: Chapter number
            chapter_text: Full chapter text
            outline: Chapter outline (optional, for alignment)
            creative_report: Creative report (optional)

        Returns:
            SummaryAtGranularity object
        """
        start_time = time.monotonic()
        target_words = self._config.chapter_target_words

        _log.info(
            "chapter_summary_start | chapter=%d | text_length=%d | target_words=%d",
            chapter_number,
            len(chapter_text),
            target_words,
        )

        try:
            # If chapter is short enough, use it directly
            if len(chapter_text) <= target_words * 3:
                summary_text = chapter_text
                summary_payload: dict[str, Any] = {"summary": summary_text}
                _log.debug(
                    "chapter_summary_direct | chapter=%d | text_short_using_direct",
                    chapter_number,
                )
            else:
                # Use LLM to generate summary
                summary_payload = await self._llm_generate_summary_payload(
                    text=chapter_text,
                    target_words=target_words,
                    task_type=TaskType.SUMMARIZE_CHAPTER,
                )
                summary_text = str(summary_payload["summary"])

            # Extract key events from creative report if available
            key_events: list[str] = []
            key_events.extend(_text_list(summary_payload.get("key_events", [])))
            if creative_report:
                if creative_report.key_moments:
                    key_events.extend(creative_report.key_moments)
                if creative_report.plot_deviations:
                    for dev in creative_report.plot_deviations:
                        key_events.append(f"偏离：{dev.actual_plot}")
            key_events = _dedupe_texts(key_events)
            character_state_changes = _text_list(
                summary_payload.get("character_state_changes", [])
            )
            unresolved_questions = _text_list(
                summary_payload.get("unresolved_questions", [])
            )
            new_facts = _text_list(summary_payload.get("new_facts", []))

            compression_ratio = len(summary_text) / len(chapter_text) if chapter_text else 0
            elapsed_ms = (time.monotonic() - start_time) * 1000

            quality = self._verify_summary_quality(
                original_length=len(chapter_text),
                summary_text=summary_text,
                level="chapter",
            )

            result = SummaryAtGranularity(
                granularity="chapter",
                chapter_start=chapter_number,
                chapter_end=chapter_number,
                text=summary_text,
                key_events=key_events,
                character_arcs=character_state_changes,
                metadata={
                    "original_length": len(chapter_text),
                    "compression_ratio": compression_ratio,
                    "quality_score": quality["score"],
                    "quality_warnings": quality["warnings"],
                    "unresolved_questions": unresolved_questions,
                    "new_facts": new_facts,
                    "summary_strategy": summary_payload.get("_summary_strategy", "direct"),
                    "summary_chunk_count": int(summary_payload.get("_chunk_count", 1) or 1),
                },
            )

            # Cache the summary
            self._chapter_summaries[chapter_number] = summary_text

            _log.info(
                "chapter_summary_done | chapter=%d | summary_length=%d | compression_ratio=%.2f | "
                "key_events=%d | quality_score=%.2f | elapsed_ms=%.2f",
                chapter_number,
                len(summary_text),
                compression_ratio,
                len(key_events),
                quality["score"],
                elapsed_ms,
            )

            return result

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "chapter_summary_failed | chapter=%d | error=%s | elapsed_ms=%.2f",
                chapter_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    async def generate_volume_summary(
        self,
        volume: VolumeOutline,
        chapter_summaries: dict[int, str],
        canon_state: StoryKernel | None = None,
        audit_report: VolumeAuditReport | None = None,
    ) -> SummaryAtGranularity:
        """Generate a volume-level summary from chapter summaries.

        Args:
            volume: Volume outline
            chapter_summaries: Dict of chapter_number -> summary
            canon_state: Current canon state
            audit_report: Volume audit report (optional)

        Returns:
            SummaryAtGranularity object
        """
        start_time = time.monotonic()
        target_words = self._config.volume_target_words

        _log.info(
            "volume_summary_start | volume=%d | title=%s | chapters=%d-%d | target_words=%d",
            volume.volume_number,
            volume.title,
            volume.start_chapter,
            volume.end_chapter,
            target_words,
        )

        try:
            # Collect chapter summaries for this volume
            volume_chapter_summaries: list[str] = []
            for ch in range(volume.start_chapter, volume.end_chapter + 1):
                if ch in chapter_summaries:
                    volume_chapter_summaries.append(f"第{ch}章：{chapter_summaries[ch]}")

            missing_chapters = [
                ch
                for ch in range(volume.start_chapter, volume.end_chapter + 1)
                if ch not in chapter_summaries
            ]
            if missing_chapters:
                raise ValueError(
                    "卷级摘要缺少章节摘要，拒绝生成不完整的全局记忆："
                    + ",".join(str(ch) for ch in missing_chapters)
                )

            combined_text = "\n".join(volume_chapter_summaries)

            # Generate volume summary
            summary_payload = await self._llm_generate_summary_payload(
                text=combined_text,
                target_words=target_words,
                task_type=TaskType.SUMMARIZE_VOLUME,
                context={
                    "volume_title": volume.title,
                    "volume_arc_goal": getattr(volume, "arc_goal", ""),
                },
            )
            summary_text = str(summary_payload["summary"])

            # Extract character arcs from canon state
            character_arcs: list[str] = []
            if canon_state is not None:
                for name, char_state in canon_state.get_all_characters().items():
                    if char_state.notes:
                        character_arcs.append(f"{name}: {char_state.notes}")

            # Extract thematic elements from audit report
            thematic_elements: list[str] = []
            if audit_report:
                if audit_report.volume_summary:
                    thematic_elements.append(audit_report.volume_summary)
                if audit_report.carry_over_items:
                    thematic_elements.extend(audit_report.carry_over_items)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            result = SummaryAtGranularity(
                granularity="volume",
                chapter_start=volume.start_chapter,
                chapter_end=volume.end_chapter,
                text=summary_text,
                key_events=[
                    chapter_summaries.get(ch, "")[:100]
                    for ch in range(volume.start_chapter, volume.end_chapter + 1)
                    if ch in chapter_summaries
                ],
                character_arcs=character_arcs,
                thematic_elements=thematic_elements,
                metadata={
                    "volume_number": volume.volume_number,
                    "chapter_count": volume.end_chapter - volume.start_chapter + 1,
                    "summary_strategy": summary_payload.get("_summary_strategy", "single_pass"),
                    "summary_chunk_count": int(summary_payload.get("_chunk_count", 1) or 1),
                },
            )

            # Cache the summary
            self._volume_summaries[volume.volume_number] = summary_text

            _log.info(
                "volume_summary_done | volume=%d | summary_length=%d | character_arcs=%d | elapsed_ms=%.2f",
                volume.volume_number,
                len(summary_text),
                len(character_arcs),
                elapsed_ms,
            )

            return result

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "volume_summary_failed | volume=%d | error=%s | elapsed_ms=%.2f",
                volume.volume_number,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    async def generate_arc_summary(
        self,
        arc_name: str,
        volume_summaries: list[str],
        outline: StoryOutline,
    ) -> SummaryAtGranularity:
        """Generate a story arc summary from volume summaries.

        Args:
            arc_name: Name of the story arc
            volume_summaries: List of volume summaries in this arc
            outline: Story outline

        Returns:
            SummaryAtGranularity object
        """
        start_time = time.monotonic()
        target_words = self._config.arc_target_words

        _log.info(
            "arc_summary_start | arc=%s | volumes=%d | target_words=%d",
            arc_name,
            len(volume_summaries),
            target_words,
        )

        try:
            combined_text = "\n\n".join(volume_summaries)

            summary_text = await self._llm_generate_summary(
                text=combined_text,
                target_words=target_words,
                task_type=TaskType.SUMMARIZE_ARC,
                context={
                    "arc_name": arc_name,
                },
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000

            result = SummaryAtGranularity(
                granularity="arc",
                chapter_start=1,  # Will be updated based on volumes
                chapter_end=max(v.end_chapter for v in outline.volumes) if outline.volumes else 0,
                text=summary_text,
                thematic_elements=[arc_name],
                metadata={"arc_name": arc_name},
            )

            # Cache the summary
            self._arc_summaries[arc_name] = summary_text

            _log.info(
                "arc_summary_done | arc=%s | summary_length=%d | elapsed_ms=%.2f",
                arc_name,
                len(summary_text),
                elapsed_ms,
            )

            return result

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "arc_summary_failed | arc=%s | error=%s | elapsed_ms=%.2f",
                arc_name,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    def get_summary(
        self,
        granularity: Literal["scene", "chapter", "volume", "arc"],
        key: int,
        arc_name: str | None = None,
        scene_index: int = 0,
    ) -> str | None:
        """Retrieve a cached summary by its key.

        Args:
            granularity: Summary granularity level
            key: Chapter number for chapter/scene, **volume number** for volume summaries
            arc_name: Arc name (for arc-level summaries)
            scene_index: Scene index (for scene-level summaries)

        Returns:
            Summary text or None if not found
        """
        if granularity == "scene":
            return self._scene_summaries.get((key, scene_index))
        elif granularity == "chapter":
            return self._chapter_summaries.get(key)
        elif granularity == "volume":
            # key is volume_number (NOT chapter_number)
            return self._volume_summaries.get(key)
        elif granularity == "arc":
            if arc_name:
                return self._arc_summaries.get(arc_name)
        return None

    def cache_summary(
        self,
        granularity: Literal["scene", "chapter", "volume", "arc"],
        chapter_number: int,
        text: str,
        arc_name: str | None = None,
        scene_index: int = 0,
    ) -> None:
        """Cache a generated summary.

        Args:
            granularity: Summary granularity level
            chapter_number: Chapter number (or volume number for volume summaries)
            text: Summary text
            arc_name: Arc name (for arc-level summaries)
            scene_index: Scene index (for scene-level summaries)
        """
        if granularity == "scene":
            self._scene_summaries[(chapter_number, scene_index)] = text
        elif granularity == "chapter":
            self._chapter_summaries[chapter_number] = text
        elif granularity == "volume":
            self._volume_summaries[chapter_number] = text
        elif granularity == "arc" and arc_name:
            self._arc_summaries[arc_name] = text

    def get_chapter_summaries(
        self,
        start_chapter: int | None = None,
        end_chapter: int | None = None,
    ) -> dict[str, str]:
        """Return cached chapter summaries filtered by inclusive chapter range."""
        summaries: dict[str, str] = {}
        for chapter, text in sorted(self._chapter_summaries.items()):
            if start_chapter is not None and chapter < start_chapter:
                continue
            if end_chapter is not None and chapter > end_chapter:
                continue
            summaries[str(chapter)] = text
        return summaries

    def get_volume_summaries(self) -> dict[str, str]:
        """Return all cached volume summaries."""
        return {
            str(volume_key): text for volume_key, text in sorted(self._volume_summaries.items())
        }

    def get_arc_summaries(self) -> dict[str, str]:
        """Return all cached arc summaries."""
        return {str(arc_name): text for arc_name, text in sorted(self._arc_summaries.items())}

    def get_summary_for_context(
        self,
        current_chapter: int,
        granularity: Literal["scene", "chapter", "volume", "arc"] = "chapter",
        lookback_volumes: int = 1,
        current_volume_number: int | None = None,
    ) -> str:
        """Get summary appropriate for current context.

        This is the main method called during chapter generation to inject
        historical context at the appropriate granularity.

        Args:
            current_chapter: Current chapter being written
            granularity: Desired granularity level
            lookback_volumes: Number of volumes to include (for volume/arc granularity)
            current_volume_number: Current volume number (if known, improves accuracy)

        Returns:
            Formatted summary text for prompt injection
        """
        if granularity == "chapter":
            # Get recent chapter summaries
            summaries: list[str] = []
            recent_window = max(1, int(self._config.context_recent_chapters or 5))
            for ch in range(max(1, current_chapter - recent_window), current_chapter):
                if ch in self._chapter_summaries:
                    summaries.append(f"第{ch}章：{self._chapter_summaries[ch]}")
            return "\n".join(summaries) if summaries else ""

        elif granularity == "volume":
            # Use current_volume_number if provided to find the right summary
            if current_volume_number is not None:
                # Try current volume first, then lookback volumes
                for vol_offset in range(lookback_volumes + 1):
                    vol_num = current_volume_number - vol_offset
                    if vol_num in self._volume_summaries:
                        return self._volume_summaries[vol_num]
            # Fallback: return most recent volume summary
            if self._volume_summaries:
                latest_volume_num = max(self._volume_summaries.keys())
                return self._volume_summaries[latest_volume_num]

        elif granularity == "arc":
            # Return most recent arc summary
            if self._arc_summaries:
                return list(self._arc_summaries.values())[-1]

        return ""

    async def _llm_generate_summary(
        self,
        text: str,
        target_words: int,
        task_type: TaskType,
        context: dict[str, str] | None = None,
    ) -> str:
        """Use LLM to generate a summary without silently truncating source text."""

        payload = await self._llm_generate_summary_payload(
            text=text,
            target_words=target_words,
            task_type=task_type,
            context=context,
        )
        return str(payload["summary"])

    async def _llm_generate_summary_payload(
        self,
        *,
        text: str,
        target_words: int,
        task_type: TaskType,
        context: dict[str, str] | None = None,
        _depth: int = 0,
    ) -> dict[str, Any]:
        """Generate a structured summary via hierarchical LLM map-reduce.

        Every source character belongs to exactly one evidence chunk.  Local
        code only partitions and merges model-authored fields; it never decides
        which narrative facts are important.
        """

        source = str(text or "").strip()
        if not source:
            raise ValueError(f"{task_type.value} summary source is empty")
        budget = max(512, int(self._config.input_token_budget or 24000))
        if estimate_chinese_tokens(source) <= budget:
            result = await self._llm_generate_summary_once(
                text=source,
                target_words=target_words,
                task_type=task_type,
                context=context,
            )
            result["_summary_strategy"] = "single_pass" if _depth == 0 else "hierarchical_merge"
            result["_chunk_count"] = 1
            return result

        if _depth >= 4:
            raise RuntimeError(
                f"{task_type.value} hierarchical summary did not fit after {_depth} passes"
            )
        chunks = _split_summary_source(source, token_budget=budget)
        if len(chunks) <= 1:
            raise RuntimeError(f"{task_type.value} summary source could not be partitioned")

        chunk_payloads: list[dict[str, Any]] = []
        chunk_target = max(120, min(target_words, max(200, target_words // 2)))
        for index, chunk in enumerate(chunks, start=1):
            chunk_context = dict(context or {})
            chunk_context.update(
                {
                    "summary_pass": "evidence_chunk",
                    "source_scope": f"分块 {index}/{len(chunks)}",
                }
            )
            chunk_payloads.append(
                await self._llm_generate_summary_once(
                    text=chunk,
                    target_words=chunk_target,
                    task_type=task_type,
                    context=chunk_context,
                )
            )

        merged_source = "\n".join(
            f"【证据分块 {index}/{len(chunk_payloads)}】{payload['summary']}"
            for index, payload in enumerate(chunk_payloads, start=1)
        )
        merge_context = dict(context or {})
        merge_context.update(
            {
                "summary_pass": "evidence_merge",
                "source_scope": f"合并 {len(chunk_payloads)} 个完整覆盖分块",
            }
        )
        merged = await self._llm_generate_summary_payload(
            text=merged_source,
            target_words=target_words,
            task_type=task_type,
            context=merge_context,
            _depth=_depth + 1,
        )
        for key in (
            "key_events",
            "character_state_changes",
            "unresolved_questions",
            "new_facts",
        ):
            merged[key] = _dedupe_texts(
                [
                    *_text_list(merged.get(key, [])),
                    *[
                        item
                        for payload in chunk_payloads
                        for item in _text_list(payload.get(key, []))
                    ],
                ]
            )
        merged["_summary_strategy"] = "hierarchical_map_reduce"
        merged["_chunk_count"] = sum(
            int(payload.get("_chunk_count", 1) or 1) for payload in chunk_payloads
        )
        return merged

    async def _llm_generate_summary_once(
        self,
        *,
        text: str,
        target_words: int,
        task_type: TaskType,
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one structured summary call over a source that already fits."""

        prompt_context: dict[str, Any] = {
            "text": text,
            "chapter_text": text,
            "scene_text": text,
            "target_words": target_words,
        }
        if context:
            prompt_context.update(context)

        _log.debug(
            "llm_summary_start | task_type=%s | input_length=%d | target_words=%d",
            task_type.value,
            len(text),
            target_words,
        )

        response = await self._router.route(
            self._builder.build(
                task_type,
                prompt_context,
                max_tokens=calculate_route_aware_max_tokens(
                    self._router,
                    task_type,
                    max(300, target_words * 2),
                    prompt_overhead=1800,
                    min_tokens=512,
                ),
                temperature=0.3,
            )
        )
        data = json.loads(response.content)
        if not isinstance(data, dict):
            raise ValueError(f"{task_type.value} summary response must be a JSON object")
        summary = str(data.get("summary") or "").strip()
        if not summary:
            raise ValueError(f"{task_type.value} summary response is missing non-empty summary")
        data["summary"] = summary

        _log.debug(
            "llm_summary_done | task_type=%s | summary_length=%d",
            task_type.value,
            len(summary),
        )
        return data

    @staticmethod
    def _verify_summary_quality(
        original_length: int,
        summary_text: str,
        level: str = "chapter",
    ) -> dict[str, Any]:
        """Heuristic quality check for generated summaries.

        This is a lightweight, LLM-free check. It catches degenerate outputs
        (empty summaries, over-truncated content) and emits structured warnings.
        Rules are intentionally lenient to avoid suppressing valid creative text.

        Args:
            original_length: Character count of the source text.
            summary_text: The generated summary string.
            level: Granularity level ("scene" | "chapter" | "volume" | "arc").

        Returns:
            dict with keys ``score`` (float, 0.0-1.0) and ``warnings`` (list[str]).
        """
        warnings: list[str] = []
        score = 1.0

        if not summary_text or not summary_text.strip():
            return {"score": 0.0, "warnings": ["摘要为空"]}

        summary_len = len(summary_text)

        # Minimum useful length thresholds per level
        min_lengths: dict[str, int] = {
            "scene": 20,
            "chapter": 60,
            "volume": 200,
            "arc": 500,
        }
        min_len = min_lengths.get(level, 60)
        if summary_len < min_len:
            score -= 0.4
            warnings.append(f"摘要过短（{summary_len} 字符 < 最低 {min_len}，可能丢失关键信息）")

        # Over-aggressive compression: < 1% of original is suspicious for long sources
        if original_length > 500 and summary_len < original_length * 0.01:
            score -= 0.3
            warnings.append(f"压缩率过高（摘要仅占原文 {summary_len / original_length:.1%}）")

        # Summary shouldn't be longer than the original
        if summary_len > original_length:
            score -= 0.1
            warnings.append("摘要长度超过原文，可能存在幻觉内容")

        score = max(0.0, min(1.0, score))

        if warnings:
            _log.warning(
                "summary_quality_warnings | level=%s | score=%.2f | warnings=%s",
                level,
                score,
                warnings,
            )

        return {"score": score, "warnings": warnings}

    def evict_volume_summaries(
        self,
        volume_number: int,
        keep_recent_volumes: int = 2,
        *,
        max_chapter_to_keep: int | None = None,
    ) -> int:
        """Evict chapter summaries for volumes older than the specified volume.

        Keeps summaries for the current volume and the N most recent volumes.
        Returns the number of evicted chapter summaries.

        Because this service has no outline access, the caller must supply
        ``max_chapter_to_keep`` to define the eviction boundary.  When omitted
        the method is a no-op and returns 0.

        Args:
            volume_number: Current volume number (used for logging).
            keep_recent_volumes: Number of recent volumes to keep (default 2).
            max_chapter_to_keep: Evict all chapter summaries where
                ``chapter_number < max_chapter_to_keep``.

        Returns:
            Number of evicted chapter summaries.
        """
        if max_chapter_to_keep is None:
            _log.debug(
                "evict_volume_summaries | volume=%d | skipped (no max_chapter_to_keep)",
                volume_number,
            )
            return 0

        evicted = 0
        chapters_to_remove = [ch for ch in self._chapter_summaries if ch < max_chapter_to_keep]
        for ch in chapters_to_remove:
            del self._chapter_summaries[ch]
            evicted += 1

        _log.info(
            "evict_volume_summaries | volume=%d | keep_recent=%d | "
            "threshold=%d | evicted=%d | remaining=%d",
            volume_number,
            keep_recent_volumes,
            max_chapter_to_keep,
            evicted,
            len(self._chapter_summaries),
        )

        return evicted

    def clear_cache(self) -> None:
        """Clear all cached summaries."""
        scene_count = len(self._scene_summaries)
        chapter_count = len(self._chapter_summaries)
        volume_count = len(self._volume_summaries)
        arc_count = len(self._arc_summaries)

        self._scene_summaries.clear()
        self._chapter_summaries.clear()
        self._volume_summaries.clear()
        self._arc_summaries.clear()

        _log.info(
            "summary_cache_cleared | scenes=%d | chapters=%d | volumes=%d | arcs=%d",
            scene_count,
            chapter_count,
            volume_count,
            arc_count,
        )

    def set_storage(self, storage: "FileSystemStorage", project_id: str) -> None:
        self._storage = storage
        self._project_id = project_id

    async def save_summaries(self) -> bool:
        if self._storage is None:
            return False
        data = {
            "scene_summaries": {f"{k[0]}_{k[1]}": v for k, v in self._scene_summaries.items()},
            "chapter_summaries": {str(k): v for k, v in self._chapter_summaries.items()},
            "volume_summaries": {str(k): v for k, v in self._volume_summaries.items()},
            "arc_summaries": {k: v for k, v in self._arc_summaries.items()},
        }
        path = self._storage.root / self._project_id / "memory" / "summaries.json"
        self._storage.save_json(path, data)
        return True

    async def load_summaries(self) -> bool:
        if self._storage is None:
            return False
        path = self._storage.root / self._project_id / "memory" / "summaries.json"
        if not self._storage.exists(path):
            return False
        data = self._storage.load_json(path)
        for key, val in data.get("scene_summaries", {}).items():
            ch, scene = key.split("_", 1)
            self._scene_summaries[(int(ch), int(scene))] = val
        for key, val in data.get("chapter_summaries", {}).items():
            self._chapter_summaries[int(key)] = val
        for key, val in data.get("volume_summaries", {}).items():
            self._volume_summaries[int(key)] = val
        for key, val in data.get("arc_summaries", {}).items():
            self._arc_summaries[key] = val
        return True


def _split_summary_source(text: str, *, token_budget: int) -> list[str]:
    """Partition source losslessly on paragraph/line boundaries."""

    budget = max(256, int(token_budget))
    units = [unit.strip() for unit in text.splitlines() if unit.strip()]
    if not units:
        units = [text]
    expanded: list[str] = []
    max_chars = max(128, int(budget / 1.5))
    for unit in units:
        if estimate_chinese_tokens(unit) <= budget:
            expanded.append(unit)
            continue
        expanded.extend(
            unit[start : start + max_chars]
            for start in range(0, len(unit), max_chars)
        )

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for unit in expanded:
        unit_tokens = estimate_chinese_tokens(unit)
        if current and current_tokens + unit_tokens > budget:
            chunks.append("\n".join(current))
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        chunks.append("\n".join(current))
    return chunks


def _text_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return _dedupe_texts(str(item or "").strip() for item in values)


def _dedupe_texts(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
