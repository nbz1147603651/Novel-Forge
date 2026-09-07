"""ExtractCanonDeltaStep — extracts a v2 ChapterOutcome from chapter text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import sanitize_story_text
from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor
from novel_forge.core.parsing.normalizers import (
    ChapterExitNormalizer,
    CharacterNormalizer,
    CreativeReportNormalizer,
    EventNormalizer,
    ForeshadowingNormalizer,
    PlotThreadNormalizer,
    RelationshipNormalizer,
    WorldFactNormalizer,
)
from novel_forge.core.parsing.text_utils import clean_str
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.quality.extraction_quality import (
    clean_placeholder_deltas,
    validate_creative_report_payload,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.split_artifact_runner import (
    SplitArtifactRunner,
    SplitJsonFragment,
)
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import StoryKernel


@dataclass
class ExtractInput:
    """Input for canon/state extraction."""

    chapter_number: int
    chapter_text: str
    known_characters: list[str]
    chapter_outline_summary: str = ""
    prior_violations: list[str] | None = None
    existing_thread_ids: list[str] | None = None
    # ── prior state baseline (compact snapshots for delta judgement) ──
    prior_relationships: list[dict[str, Any]] | None = None
    prior_character_snapshots: list[dict[str, Any]] | None = None
    prior_plot_threads: list[dict[str, Any]] | None = None
    authoritative_character_genders: dict[str, str] | None = None
    chapter_source_slice: Any | None = None


class ExtractCanonDeltaStep(PipelineStep[ExtractInput, ChapterOutcome]):
    """Chapter text → model → ChapterOutcome."""

    _EMBEDDED_TOP_LEVEL_KEYS = {
        "creative_report",
        "chapter_exit_state",
        "character_state_deltas",
        "relationship_deltas",
        "plot_thread_deltas",
        "structured_summary",
    }

    @property
    def step_name(self) -> str:
        return "extract_canon"

    @classmethod
    def _normalize_extracted_payload(
        cls,
        raw: dict[str, Any],
        chapter_number: int,
    ) -> dict[str, Any]:
        """Normalize the full extracted payload."""
        payload = dict(raw) if isinstance(raw, dict) else {}

        # Extract canon_delta fields
        canon_raw = payload.get("canon_delta")
        if not isinstance(canon_raw, dict):
            canon_raw = {
                key: payload.get(key)
                for key in (
                    "source_chapter",
                    "character_updates",
                    "new_events",
                    "foreshadowing_updates",
                    "new_world_facts",
                    "chapter_summary",
                )
                if key in payload
            }
        else:
            # Handle embedded keys — first lift from canon_delta siblings, then
            # from inside character_updates (both patterns seen in LLM output).
            for key in cls._EMBEDDED_TOP_LEVEL_KEYS:
                if key in canon_raw and key not in payload:
                    payload[key] = canon_raw.pop(key)
            raw_char_updates = canon_raw.get("character_updates")
            if isinstance(raw_char_updates, dict):
                for key in cls._EMBEDDED_TOP_LEVEL_KEYS:
                    if key in raw_char_updates and key not in payload:
                        payload[key] = raw_char_updates.get(key)
                canon_raw["character_updates"] = {
                    k: v
                    for k, v in raw_char_updates.items()
                    if k not in cls._EMBEDDED_TOP_LEVEL_KEYS
                }

        exit_raw = payload.get("chapter_exit_state")
        if isinstance(exit_raw, dict):
            for key in cls._EMBEDDED_TOP_LEVEL_KEYS:
                if key in exit_raw and key not in payload:
                    payload[key] = exit_raw.get(key)

        source_chapter = CharacterNormalizer.coerce_int(
            canon_raw.get("source_chapter"), chapter_number
        )
        character_updates = CharacterNormalizer.normalize_character_updates(
            canon_raw.get("character_updates", {})
        )

        # Get chapter summary
        chapter_summary = clean_str(canon_raw.get("chapter_summary"))
        if not chapter_summary:
            chapter_summary = sanitize_story_text(
                clean_str(payload.get("structured_summary"))
            ) or sanitize_story_text(clean_str(payload.get("chapter_summary")))

        # Normalize creative report
        creative_raw = payload.get("creative_report")
        if isinstance(creative_raw, dict):
            creative_raw = dict(creative_raw)
            if "relationship_deltas" not in creative_raw and "relationship_deltas" in payload:
                creative_raw["relationship_deltas"] = payload.get("relationship_deltas")
            if "plot_thread_updates" not in creative_raw:
                if "plot_thread_deltas" in payload:
                    creative_raw["plot_thread_updates"] = payload.get("plot_thread_deltas")
                elif "plot_thread_updates" in payload:
                    creative_raw["plot_thread_updates"] = payload.get("plot_thread_updates")
        else:
            creative_raw = {
                key: payload.get(key)
                for key in (
                    "new_characters",
                    "new_locations",
                    "new_key_items",
                    "plot_deviations",
                    "suggestions_for_next_chapter",
                    "creative_highlights",
                    "structured_summary",
                    "must_carry_forward",
                    "bridge_hints",
                    "character_state_deltas",
                    "relationship_deltas",
                    "plot_thread_updates",
                )
                if key in payload
            }
        creative_report = CreativeReportNormalizer.normalize_creative_report(
            creative_raw, source_chapter
        )

        # Prefer top-level payload; fall back to creative_report's already-normalised copy.
        relationship_deltas_raw = payload.get("relationship_deltas")
        if relationship_deltas_raw is not None:
            relationship_deltas = RelationshipNormalizer.normalize_relationship_deltas(
                relationship_deltas_raw,
                source_chapter=source_chapter,
            )
        else:
            relationship_deltas = creative_report.get("relationship_deltas", [])

        # Warn when LLM omitted trust/tension for any relationship delta.
        for rd in relationship_deltas:
            rel = rd.get("relationship", {})
            missing = [k for k in ("trust", "tension") if k not in rel]
            if missing:
                get_logger("extract_canon").warning(
                    "ch%d relationship %s missing %s — existing value preserved",
                    source_chapter,
                    rd.get("pair_id", "?"),
                    ", ".join(missing),
                )

        plot_thread_deltas = creative_report.get("plot_thread_updates", [])
        if not plot_thread_deltas:
            thread_payload = payload.get(
                "plot_thread_deltas", payload.get("plot_thread_updates", [])
            )
            plot_thread_deltas = PlotThreadNormalizer.normalize_plot_thread_deltas(
                thread_payload,
                source_chapter=source_chapter,
            )

        # Resolve authoritative top-level character_state_deltas
        character_state_deltas = CharacterNormalizer.normalize_state_deltas(
            payload.get("character_state_deltas", creative_raw.get("character_state_deltas", [])),
            source_chapter=source_chapter,
            fallback=character_updates,
        )

        # Back-fill authoritative values into creative_report so callers always
        # see consistent data without the model needing to duplicate output.
        creative_report["character_state_deltas"] = character_state_deltas
        creative_report["relationship_deltas"] = relationship_deltas
        creative_report["plot_thread_updates"] = plot_thread_deltas

        return {
            "source_chapter": source_chapter,
            "character_updates": character_updates,
            "new_events": EventNormalizer.normalize_events(
                canon_raw.get("new_events", []), source_chapter
            ),
            "foreshadowing_updates": ForeshadowingNormalizer.normalize_foreshadowing_updates(
                canon_raw.get("foreshadowing_updates", []), source_chapter
            ),
            "new_world_facts": WorldFactNormalizer.normalize_world_facts(
                canon_raw.get("new_world_facts", {})
            ),
            "chapter_summary": chapter_summary,
            "creative_report": creative_report,
            "chapter_exit_state": ChapterExitNormalizer.normalize_chapter_exit_state(
                payload.get("chapter_exit_state"),
                source_chapter=source_chapter,
                chapter_summary=chapter_summary,
                fallback_character_updates=character_updates,
                creative_report=creative_report,
            ),
            "character_state_deltas": character_state_deltas,
            "relationship_deltas": relationship_deltas,
            "plot_thread_deltas": plot_thread_deltas,
            "structured_summary": (
                sanitize_story_text(clean_str(payload.get("structured_summary")))
                or creative_report.get("structured_summary")
                or chapter_summary
            ),
        }

    # Fields the prompt asks LLM to output; ordered by template sequence.
    # Used to detect truncation: if late-order fields are missing, the
    # response was likely cut short by max_tokens.
    _REQUIRED_FIELDS = ("canon_delta", "creative_report", "chapter_exit_state")
    _LATE_FIELDS = ("relationship_deltas", "plot_thread_deltas", "structured_summary")

    def _estimate_max_tokens(
        self,
        chapter_length: int,
        known_characters_count: int,
    ) -> int:
        """Dynamically estimate output token budget.

        Heuristic accounts for:
        - Base overhead of 7-section JSON skeleton.
        - Per-character cost in character_state_deltas and chapter_exit_state.
        - Longer chapters → richer creative_report / canon_delta.
        """
        settings = self.settings
        base = int(getattr(settings, "extract_canon_output_base_tokens", 4096) or 4096)
        char_budget = int(getattr(settings, "extract_canon_output_tokens_per_character", 450) or 0)
        length_budget = int(
            getattr(settings, "extract_canon_output_tokens_per_2500_chars", 768) or 0
        )
        max_budget = int(getattr(settings, "extract_canon_output_max_tokens", 12288) or 12288)
        char_cost = known_characters_count * char_budget
        # Longer chapters produce more events, foreshadowing, plot threads
        length_cost = int(chapter_length / 2500 * length_budget)
        estimated_tokens = base + char_cost + length_cost
        target_output_chars = max(1200, chapter_length // 2, int(estimated_tokens / 2.2))
        return self._dynamic_max_tokens(
            TaskType.EXTRACT_CANON,
            target_output_chars,
            prompt_overhead=3500,
            safety_margin=0.85,
            min_tokens=base,
            max_cap=max_budget,
        )

    def _estimate_fragment_tokens(
        self,
        task_type: TaskType,
        chapter_length: int,
        known_characters_count: int,
    ) -> int:
        """Estimate a focused token budget for one canon extraction fragment."""
        max_budget = int(getattr(self.settings, "extract_canon_output_max_tokens", 12288) or 12288)
        per_character = int(
            getattr(self.settings, "extract_canon_output_tokens_per_character", 450) or 0
        )
        length_units = max(1, int(chapter_length / 2500) + 1)
        profiles_cost = max(0, known_characters_count) * max(80, per_character // 3)
        fragment_targets: dict[TaskType, tuple[int, int]] = {
            TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: (2200 + profiles_cost, 2048),
            TaskType.EXTRACT_CANON_DELTA: (2600 + profiles_cost, 2048),
            TaskType.EXTRACT_CREATIVE_REPORT: (1800, 1536),
            TaskType.EXTRACT_CHARACTER_STATE_DELTAS: (1800 + profiles_cost, 2048),
            TaskType.EXTRACT_RELATIONSHIP_DELTAS: (1500 + profiles_cost // 2, 1536),
            TaskType.EXTRACT_PLOT_THREAD_DELTAS: (1500 + length_units * 300, 1536),
        }
        target_chars, min_tokens = fragment_targets.get(task_type, (2000, 1536))
        return self._dynamic_max_tokens(
            task_type,
            target_chars,
            prompt_overhead=3000,
            safety_margin=0.85,
            min_tokens=min_tokens,
            max_cap=max(2048, min(max_budget, 8192)),
        )

    @staticmethod
    def _is_truncated(data: dict[str, Any], response: Any) -> bool:
        """Detect if the LLM response was truncated."""
        # Check finish_reason if available
        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason == "length":
            return True

        # Check completion_tokens vs max_tokens if available
        usage = getattr(response, "usage", None)
        if usage is not None:
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            max_tokens = getattr(response, "max_tokens", 0) or 0
            if max_tokens > 0 and completion_tokens >= max_tokens * 0.97:
                return True

        # Heuristic: late-order fields missing or empty
        missing_late = sum(
            1
            for key in ("relationship_deltas", "plot_thread_deltas", "structured_summary")
            if key not in data or data[key] is None
        )
        if missing_late >= 2:
            return True

        return False

    def _build_context(self, input_data: ExtractInput) -> dict[str, Any]:
        # Keep the split extraction prompt contract total.  A chapter-one
        # extraction legitimately has no prior snapshots or relationships; an
        # omitted key is not semantically different from an empty collection.
        # Supplying the empty values here prevents StrictUndefined from turning
        # that normal state into a split-DAG failure.
        ctx: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            "known_characters": input_data.known_characters,
            "chapter_outline_summary": input_data.chapter_outline_summary,
            "prior_violations": list(input_data.prior_violations or []),
            "existing_thread_ids": list(input_data.existing_thread_ids or []),
            "prior_relationships": list(input_data.prior_relationships or []),
            "prior_character_snapshots": list(input_data.prior_character_snapshots or []),
            "prior_plot_threads": list(input_data.prior_plot_threads or []),
            "authoritative_character_genders": dict(
                input_data.authoritative_character_genders or {}
            ),
            "max_character_state_deltas": int(
                getattr(self.settings, "extract_canon_max_character_state_deltas", 8) or 8
            ),
            "max_relationship_deltas": int(
                getattr(self.settings, "extract_canon_max_relationship_deltas", 8) or 8
            ),
            "max_plot_thread_deltas": int(
                getattr(self.settings, "extract_canon_max_plot_thread_deltas", 8) or 8
            ),
            "max_exit_state_characters": int(
                getattr(self.settings, "extract_canon_max_exit_state_characters", 6) or 6
            ),
        }
        if input_data.chapter_source_slice is not None:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )

            ctx["stage_cards"] = {
                "source": project_stage_source_cards(
                    input_data.chapter_source_slice,
                    stage="extract",
                )
            }
        return ctx

    async def _call_split_fragment(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        result = await self._call_with_retry(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=min(0.1, max(0.0, temperature)),
            required_keys=required_keys,
            max_retries=max_retries,
            thinking=False,
        )
        if not isinstance(result, dict):
            raise TypeError(f"{task_type.value} must return a JSON object, got {type(result).__name__}")
        return result

    async def _execute_split(
        self,
        input_data: ExtractInput,
        ctx: dict[str, Any],
    ) -> ChapterOutcome:
        """Run canon extraction as anchored fragments, then materialize ChapterOutcome."""
        chapter_length = len(input_data.chapter_text)
        known_count = len(input_data.known_characters)

        summary_runner = SplitArtifactRunner(
            call_json=self._call_split_fragment,
            max_parallel=1,
            artifact_name=f"extract_canon_ch{input_data.chapter_number}_summary",
        )
        summary_result = await summary_runner.run(
            [
                SplitJsonFragment(
                    name="summary_exit",
                    task_type=TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
                    context={
                        **ctx,
                        "shared_evidence_anchor": build_shared_evidence_anchor(
                            "extract_canon.chapter_source",
                            ctx,
                            source_keys=(
                                "chapter_number",
                                "chapter_text",
                                "known_characters",
                                "chapter_outline_summary",
                                "prior_violations",
                            ),
                            max_string_chars=900,
                        ),
                    },
                    required_keys=("chapter_exit_state", "structured_summary"),
                    max_tokens=self._estimate_fragment_tokens(
                        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
                        chapter_length,
                        known_count,
                    ),
                    temperature=self.settings.temp_extract_canon,
                )
            ]
        )
        summary_exit = summary_result.fragments["summary_exit"]
        anchored_ctx = {
            **ctx,
            "summary_exit": summary_exit,
            "shared_evidence_anchor": build_shared_evidence_anchor(
                "extract_canon.summary_exit",
                {**ctx, "summary_exit": summary_exit},
                source_keys=(
                    "chapter_number",
                    "known_characters",
                    "chapter_outline_summary",
                    "summary_exit",
                    "prior_character_snapshots",
                ),
                max_string_chars=900,
            ),
        }

        character_runner = SplitArtifactRunner(
            call_json=self._call_split_fragment,
            max_parallel=1,
            artifact_name=f"extract_canon_ch{input_data.chapter_number}_character_state",
        )
        character_state_payload = (
            await character_runner.run(
                [
                    SplitJsonFragment(
                        name="character_state_deltas",
                        task_type=TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
                        context=anchored_ctx,
                        required_keys=("character_state_deltas",),
                        max_tokens=self._estimate_fragment_tokens(
                            TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
                            chapter_length,
                            known_count,
                        ),
                        temperature=self.settings.temp_extract_canon,
                    )
                ]
            )
        ).fragments["character_state_deltas"]

        relationship_ctx = {
            **anchored_ctx,
            "character_state_deltas": character_state_payload.get(
                "character_state_deltas", []
            ),
            "shared_evidence_anchor": build_shared_evidence_anchor(
                "extract_canon.character_state",
                {
                    **anchored_ctx,
                    "character_state_deltas": character_state_payload.get(
                        "character_state_deltas", []
                    ),
                },
                source_keys=(
                    "chapter_number",
                    "summary_exit",
                    "known_characters",
                    "character_state_deltas",
                    "prior_relationships",
                ),
                max_string_chars=900,
            ),
        }
        relationship_runner = SplitArtifactRunner(
            call_json=self._call_split_fragment,
            max_parallel=1,
            artifact_name=f"extract_canon_ch{input_data.chapter_number}_relationship",
        )
        relationship_payload = (
            await relationship_runner.run(
                [
                    SplitJsonFragment(
                        name="relationship_deltas",
                        task_type=TaskType.EXTRACT_RELATIONSHIP_DELTAS,
                        context=relationship_ctx,
                        required_keys=("relationship_deltas",),
                        max_tokens=self._estimate_fragment_tokens(
                            TaskType.EXTRACT_RELATIONSHIP_DELTAS,
                            chapter_length,
                            known_count,
                        ),
                        temperature=self.settings.temp_extract_canon,
                    )
                ]
            )
        ).fragments["relationship_deltas"]

        downstream_ctx = {
            **relationship_ctx,
            "relationship_deltas": relationship_payload.get("relationship_deltas", []),
            "shared_evidence_anchor": build_shared_evidence_anchor(
                "extract_canon.relationships",
                {
                    **relationship_ctx,
                    "relationship_deltas": relationship_payload.get("relationship_deltas", []),
                },
                source_keys=(
                    "chapter_number",
                    "summary_exit",
                    "character_state_deltas",
                    "relationship_deltas",
                    "existing_thread_ids",
                    "prior_plot_threads",
                ),
                max_string_chars=900,
            ),
        }
        fragment_specs = [
            SplitJsonFragment(
                name="canon_delta",
                task_type=TaskType.EXTRACT_CANON_DELTA,
                context=downstream_ctx,
                required_keys=("canon_delta",),
                max_tokens=self._estimate_fragment_tokens(
                    TaskType.EXTRACT_CANON_DELTA,
                    chapter_length,
                    known_count,
                ),
                temperature=self.settings.temp_extract_canon,
            ),
            SplitJsonFragment(
                name="creative_report",
                task_type=TaskType.EXTRACT_CREATIVE_REPORT,
                context=downstream_ctx,
                required_keys=("creative_report",),
                max_tokens=self._estimate_fragment_tokens(
                    TaskType.EXTRACT_CREATIVE_REPORT,
                    chapter_length,
                    known_count,
                ),
                temperature=self.settings.temp_extract_canon,
            ),
            SplitJsonFragment(
                name="plot_thread_deltas",
                task_type=TaskType.EXTRACT_PLOT_THREAD_DELTAS,
                context=downstream_ctx,
                required_keys=("plot_thread_deltas",),
                max_tokens=self._estimate_fragment_tokens(
                    TaskType.EXTRACT_PLOT_THREAD_DELTAS,
                    chapter_length,
                    known_count,
                ),
                temperature=self.settings.temp_extract_canon,
            ),
        ]
        runner = SplitArtifactRunner(
            call_json=self._call_split_fragment,
            max_parallel=int(getattr(self.settings, "canon_extract_max_parallel", 4) or 4),
            artifact_name=f"extract_canon_ch{input_data.chapter_number}",
        )
        fragments = (await runner.run(fragment_specs)).fragments
        data: dict[str, Any] = {
            **summary_exit,
            **fragments.get("canon_delta", {}),
            **fragments.get("creative_report", {}),
            **character_state_payload,
            **relationship_payload,
            **fragments.get("plot_thread_deltas", {}),
        }
        normalized = self._normalize_extracted_payload(data, input_data.chapter_number)

        # ── Post-extraction quality: creative report + delta cleanup ──
        quality_report = validate_creative_report_payload(
            normalized, input_data.chapter_text, input_data.chapter_number,
        )
        if quality_report.retry_recommended:
            self._logger.info(
                "extraction_quality: retrying creative_report fragment | %s",
                quality_report.details,
            )
            try:
                retry_fragment = await self._call_with_retry(
                    TaskType.EXTRACT_CREATIVE_REPORT,
                    downstream_ctx,
                    max_tokens=self._estimate_fragment_tokens(
                        TaskType.EXTRACT_CREATIVE_REPORT,
                        len(input_data.chapter_text),
                        len(input_data.known_characters),
                    ),
                    temperature=self.settings.temp_extract_canon,
                    required_keys=("creative_report",),
                    max_retries=1,
                    thinking=False,
                )
                if not isinstance(retry_fragment, dict):
                    raise TypeError(
                        "extract_creative_report must return a JSON object, "
                        f"got {type(retry_fragment).__name__}"
                    )
                data.update(retry_fragment)
                normalized = self._normalize_extracted_payload(
                    data, input_data.chapter_number,
                )
                quality_report = validate_creative_report_payload(
                    normalized, input_data.chapter_text, input_data.chapter_number,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    "extraction_quality: creative_report retry failed | ch%d | %s",
                    input_data.chapter_number,
                    exc,
                )

        if (
            quality_report.placeholder_relationship_deltas
            or quality_report.placeholder_plot_thread_deltas
            or quality_report.placeholder_character_state_deltas
        ):
            normalized = clean_placeholder_deltas(normalized)

        self._on_step_event(
            "extract_creative_report_quality",
            {
                "chapter_number": input_data.chapter_number,
                "severity": quality_report.severity,
                "is_hollow": quality_report.is_hollow,
                "placeholder_rel": len(quality_report.placeholder_relationship_deltas),
                "placeholder_pt": len(quality_report.placeholder_plot_thread_deltas),
                "placeholder_cs": len(quality_report.placeholder_character_state_deltas),
                "details": quality_report.details,
            },
        )

        return ChapterOutcome.model_validate(normalized)

    async def _execute_legacy(
        self,
        input_data: ExtractInput,
        ctx: dict[str, Any],
    ) -> ChapterOutcome:
        chapter_length = len(input_data.chapter_text)
        max_tokens = self._estimate_max_tokens(chapter_length, len(input_data.known_characters))
        data = await self._call_with_retry(
            TaskType.EXTRACT_CANON,
            ctx,
            max_tokens=max_tokens,
            temperature=self.settings.temp_extract_canon,
            required_keys=self._REQUIRED_FIELDS,
            max_retries=2,
        )
        if not isinstance(data, dict):
            raise TypeError(f"extract_canon must return a JSON object, got {type(data).__name__}")
        normalized = self._normalize_extracted_payload(data, input_data.chapter_number)

        # ── Post-extraction quality: creative report + delta cleanup ──
        quality_report = validate_creative_report_payload(
            normalized, input_data.chapter_text, input_data.chapter_number,
        )
        if (
            quality_report.placeholder_relationship_deltas
            or quality_report.placeholder_plot_thread_deltas
            or quality_report.placeholder_character_state_deltas
        ):
            normalized = clean_placeholder_deltas(normalized)
        self._on_step_event(
            "extract_creative_report_quality",
            {
                "chapter_number": input_data.chapter_number,
                "severity": quality_report.severity,
                "is_hollow": quality_report.is_hollow,
                "placeholder_rel": len(quality_report.placeholder_relationship_deltas),
                "placeholder_pt": len(quality_report.placeholder_plot_thread_deltas),
                "placeholder_cs": len(quality_report.placeholder_character_state_deltas),
                "details": quality_report.details,
            },
        )

        return ChapterOutcome.model_validate(normalized)

    async def _execute(self, input_data: ExtractInput) -> ChapterOutcome:
        ctx = self._build_context(input_data)

        if bool(getattr(self.settings, "split_tasks_enabled", True)):
            # Do not silently replace the bounded fragment DAG with the legacy
            # monolithic extraction.  That would turn a local context/template
            # defect into one huge prompt-only JSON request and conceal the real
            # upstream failure.  Individual fragment calls retain their normal
            # retry policy; otherwise surface the precise fragment failure.
            return await self._execute_split(input_data, ctx)

        return await self._execute_legacy(input_data, ctx)

    def apply_outcome_to_kernel(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> StoryKernel:
        """Apply a ChapterOutcome to a StoryKernel, producing a new copy.

        This is the StoryKernel-native path: instead of merging into CanonState,
        the extracted deltas are applied directly to the unified field pool.
        """
        merger = StoryKernelMerger()
        return merger.merge_outcome(kernel, outcome)
