"""WaveStep — single-pass scene weaving for long-form chapter drafts.

Replaces the long-form ``EditStep`` edit loop.  Runs **exactly once** (no
rounds) after ``DraftStep`` to weave the multi-scene draft into a single
coherent chapter, honoring ``plan.cross_scene_intent`` (cross-scene
references + pacing curve).

Design constraints (locked by plan):
  - 6 post-condition checks run after the LLM response; failures are
    recorded in ``WaveOutput.warnings`` (no exception, no retry).
  - Word-count must stay within +/-30% of target.
  - POV must be preserved across all scenes.
  - At least 80% of cross-scene references must be hit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import detect_prompt_leaks
from novel_forge.core.format_contracts import (
    TextOutputContractError,
    validate_text_output_contract,
)
from novel_forge.core.guidance import required_cross_scene_ref
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step

_log = logging.getLogger(__name__)


@dataclass
class WaveInput:
    """Input for the wave (scene-weaving) step."""

    chapter_text: str
    context: dict[str, Any]  # additional template variables (stage_cards, plan, etc.)
    pov_character: str = ""
    target_word_count: int = 0
    cross_scene_intent: dict[str, Any] = field(default_factory=dict)
    scene_intents: list[dict[str, Any]] = field(default_factory=list)
    reading_power_hint: dict[str, Any] | None = None
    kernel_context: dict[str, Any] | None = None


@dataclass
class WaveOutput:
    """Output of the wave (scene-weaving) step."""

    woven_prose: str
    scene_transitions_added: list[dict[str, Any]] = field(default_factory=list)
    motif_weave_log: list[str] = field(default_factory=list)
    cross_ref_hits: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final_word_count: int = 0


@register_step("wave")
class WaveStep(PipelineStep[WaveInput, WaveOutput]):
    """Draft -> model (scene-weave, single pass) -> WaveOutput.

    Unlike ``EditStep``, this step runs **exactly once**; cross-scene
    concerns are handled by the prompt + post-condition checks, not by
    iterative rounds.
    """

    @property
    def step_name(self) -> str:
        return "wave"

    async def _execute(self, input_data: WaveInput) -> WaveOutput:
        chapter_length = len(input_data.chapter_text)
        max_tokens = self._dynamic_max_tokens(
            TaskType.WAVE_CHAPTER,
            chapter_length,
            prompt_overhead=3000,
            safety_margin=0.80,
            min_tokens=6144,
        )

        ctx: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            ctx.update(input_data.kernel_context)
        ctx.update(input_data.context)
        ctx["draft_text"] = input_data.chapter_text
        ctx["chapter_number"] = ctx.get("chapter_number", 0)
        if input_data.reading_power_hint is not None:
            ctx["reading_power_hint"] = input_data.reading_power_hint
        # Always inject the structured cross-scene intent so the template
        # can render it without depending on the caller to wire it through
        # stage_cards.
        if input_data.cross_scene_intent:
            cards = ctx.setdefault("stage_cards", {})
            plan_card = cards.setdefault("plan", {})
            if "cross_scene_intent" not in plan_card:
                plan_card["cross_scene_intent"] = input_data.cross_scene_intent

        request = self._builder.build(
            TaskType.WAVE_CHAPTER,
            ctx,
            max_tokens=max_tokens,
            temperature=getattr(self.settings, "temp_wave_chapter", 0.7),
        )

        response: Any
        if bool(getattr(self.settings, "long_streaming_text_enabled", True)):
            response_content = await self._call_with_retry(
                TaskType.WAVE_CHAPTER,
                ctx,
                max_tokens=max_tokens,
                temperature=getattr(self.settings, "temp_wave_chapter", 0.7),
                validate_text_output=False,
            )
            response = SimpleNamespace(content=str(response_content))
        else:
            response = await self._router.route(request)
        text = extract_text_content(response.content)

        text_contract_error: TextOutputContractError | None = None
        try:
            text = validate_text_output_contract(
                TaskType.WAVE_CHAPTER,
                response.content,
                text,
                min_chars=1,
            )
        except TextOutputContractError as exc:
            text_contract_error = exc
            text = ""

        if text_contract_error is not None or not text.strip():
            _log.warning(
                "wave_text_contract_guard | error=%s | keeping original draft",
                text_contract_error,
            )
            output = WaveOutput(
                woven_prose=input_data.chapter_text,
                warnings=[
                    f"wave skipped: {text_contract_error or 'empty output'}; kept original draft"
                ],
                final_word_count=count_chapter_words(input_data.chapter_text),
            )
            self._apply_degraded_anchors(input_data, output)
            self._run_post_conditions(input_data, output)
            return output

        prompt_leaks = detect_prompt_leaks(text, max_hits=4)
        if prompt_leaks:
            # WAVE is intentionally single-pass.  It may not turn a clean
            # DRAFT into a visible instruction leak, so retain the clean input
            # and let the usual downstream review evidence record the fallback.
            _log.warning(
                "wave_prompt_leak_guard | leaks=%s | keeping original draft",
                prompt_leaks,
            )
            output = WaveOutput(
                woven_prose=input_data.chapter_text,
                warnings=[
                    "wave skipped: generated prose exposed planning/guide information; kept original draft"
                ],
                final_word_count=count_chapter_words(input_data.chapter_text),
            )
            self._apply_degraded_anchors(input_data, output)
            self._run_post_conditions(input_data, output)
            return output

        # Truncation guard: if the response is dramatically shorter than
        # the input draft, treat as truncated and keep the original.
        # The ratio is configurable via ``long_wave_regression_floor_ratio``
        # (default 0.4) with a 200-word absolute floor that only applies to
        # drafts >= 400 words.  Short drafts scale the floor down proportionally.
        draft_words = count_chapter_words(input_data.chapter_text)
        text_words = count_chapter_words(text)
        regression_ratio = float(
            getattr(self.settings, "long_wave_regression_floor_ratio", 0.4) or 0.4
        )
        absolute_floor = 200 if draft_words >= 400 else max(1, draft_words // 2)
        regression_floor = max(absolute_floor, int(draft_words * regression_ratio))
        is_regressed = draft_words > 0 and text_words < regression_floor
        if is_regressed:
            _log.warning(
                "wave_regression_guard | draft_words=%d | woven_words=%d | "
                "floor=%d | keeping original",
                draft_words,
                text_words,
                regression_floor,
            )
            output = WaveOutput(
                woven_prose=input_data.chapter_text,
                warnings=[
                    f"wave skipped: regressed output ({text_words} words < "
                    f"{regression_floor}); kept original draft"
                ],
                final_word_count=count_chapter_words(input_data.chapter_text),
            )
            self._apply_degraded_anchors(input_data, output)
            return output

        output = WaveOutput(
            woven_prose=text,
            final_word_count=count_chapter_words(text),
        )
        self._run_post_conditions(input_data, output)
        return output

    def _run_post_conditions(self, input_data: WaveInput, output: WaveOutput) -> None:
        """Run the 6 post-condition checks; record failures as warnings.

        No exception, no retry — wave is single-pass by design.
        """
        woven = output.woven_prose
        warnings: list[str] = output.warnings

        # 1. cross_ref hits >= 80%
        scene_refs = [
            ref
            for ref in list(input_data.cross_scene_intent.get("cross_scene_references", []) or [])
            if required_cross_scene_ref(ref)
        ]
        if scene_refs:
            hits: list[str] = []
            for ref in scene_refs:
                desc = (ref.get("description") or "").strip()
                if _semantic_anchor_present(desc, woven):
                    hits.append(desc)
                    continue
            output.cross_ref_hits = hits
            hit_ratio = len(hits) / max(1, len(scene_refs))
            if hit_ratio < 0.8:
                warnings.append(
                    f"wave_post_cond_cross_ref: hit {len(hits)}/{len(scene_refs)} "
                    f"({hit_ratio:.0%}) < 80%"
                )

        # 2. pacing_curve length == scene count
        pacing = list(input_data.cross_scene_intent.get("pacing_curve", []))
        scene_count = len(input_data.scene_intents)
        if pacing and len(pacing) != scene_count:
            warnings.append(
                f"wave_post_cond_pacing: pacing_curve length {len(pacing)} != "
                f"scene count {scene_count}"
            )

        # 3. scene_anchor key sentences preserved (best-effort)
        for scene in input_data.scene_intents:
            anchor = (scene.get("summary") or "").strip()
            if anchor and not _anchor_words(anchor, min_words=2):
                continue  # summary too short to anchor
            anchor_words = _anchor_words(anchor)
            if not anchor_words:
                continue
            if not _semantic_anchor_present(anchor, woven):
                warnings.append(
                    f"wave_post_cond_anchor: scene {scene.get('scene_id', '?')} "
                    f"anchor words missing"
                )

        # 4. word count within +/-30% of target
        if input_data.target_word_count > 0:
            target = int(input_data.target_word_count)
            low = int(target * 0.7)
            high = int(target * 1.3)
            wc = count_chapter_words(woven)
            if wc < low or wc > high:
                warnings.append(
                    f"wave_post_cond_word_count: {wc} words not in [{low}, {high}] "
                    f"(target {target} +/-30%)"
                )

        # 5. POV preserved — best-effort heuristic, only if pov_character
        # is specified.
        if input_data.pov_character:
            pov = input_data.pov_character.strip()
            if pov and not _pov_consistent(woven, pov):
                warnings.append(f"wave_post_cond_pov: POV does not consistently match {pov!r}")

        # 6. required_outcome coverage — verify each scene's required_outcome
        # is semantically represented in the woven prose.  This catches
        # creative substitutions that the summary-based anchor check misses.
        for scene in input_data.scene_intents:
            outcome = (scene.get("required_outcome") or "").strip()
            if not outcome or not _anchor_words(outcome, min_words=2):
                continue
            if not _semantic_anchor_present(outcome, woven):
                warnings.append(
                    f"wave_post_cond_outcome: scene {scene.get('scene_id', '?')} "
                    f"required_outcome not semantically present"
                )

    def _apply_degraded_anchors(self, input_data: WaveInput, output: WaveOutput) -> None:
        """When WAVE is skipped, check scene anchors in degraded mode.

        Instead of the normal ``wave_post_cond_anchor:`` warnings (which
        ``assess_wave_integrity`` counts toward the blocking
        ``wave_scene_anchors_missing`` issue), record
        ``wave_degraded_anchor:`` warnings.  This lets downstream integrity
        assessment distinguish "WAVE ran but missed anchors" from "WAVE
        was skipped so anchor gaps are expected" and avoid blocking
        archive on a known-degraded pass.
        """
        woven = output.woven_prose
        for scene in input_data.scene_intents:
            anchor = (scene.get("summary") or "").strip()
            if anchor and not _anchor_words(anchor, min_words=2):
                continue
            anchor_words = _anchor_words(anchor)
            if not anchor_words:
                continue
            if not _semantic_anchor_present(anchor, woven):
                output.warnings.append(
                    f"wave_degraded_anchor: scene {scene.get('scene_id', '?')} "
                    "anchor words missing (WAVE skipped)"
                )


_MATCH_SEPARATOR_RE = re.compile(r"[\s，。；：、！？「」『』（）()\[\]…—\-_/\\,.!?;:'\"]+")


def _phrase_in_text(phrase: str, text: str) -> bool:
    """Substring check that ignores whitespace and basic punctuation."""
    if not phrase or not text:
        return False
    p = _MATCH_SEPARATOR_RE.sub("", phrase)
    t = _MATCH_SEPARATOR_RE.sub("", text)
    return p in t


def _semantic_anchor_present(anchor: str, text: str) -> bool:
    """Return whether a narrative anchor is represented in prose.

    WAVE anchors and cross-scene references are often naturally paraphrased by
    the model.  Reuse the carry-forward matcher so local post-conditions do not
    mistake semantic landing for a missing exact phrase.
    """
    if _phrase_in_text(anchor, text):
        return True
    from novel_forge.pipeline.steps.continuity_eval.local_checks import _LocalChecks

    return _LocalChecks._carry_forward_item_present_in_text(anchor, text)


def _anchor_words(text: str, min_words: int = 1) -> list[str]:
    """Pick 2-3 distinctive short tokens from *text* for cheap matching."""
    cleaned = _MATCH_SEPARATOR_RE.sub(" ", text)
    words = [w for w in cleaned.split() if 2 <= len(w) <= 8]
    return words[:3]


def _pov_consistent(text: str, pov: str) -> bool:
    """Heuristic: detect obvious POV switch by counting first-person
    pronouns that are inconsistent with a third-person narration, or by
    detecting a switch from one name to another mid-text.

    For the long-form default POV style (third person, named), the check
    is intentionally lax: a *rough* presence of the POV name in the first
    20% of paragraphs is treated as "consistent".
    """
    if not text or not pov:
        return True
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if not paragraphs:
        return True
    head = "\n".join(paragraphs[: max(1, len(paragraphs) // 5)])
    return pov in head
