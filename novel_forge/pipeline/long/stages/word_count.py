"""Pre-archive word-count gate and restructuring transaction."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.utils.text_validation import (
    WordCountAssessment,
    assess_word_count,
    count_chapter_words,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.steps.polish_step import PolishInput, PolishStep

_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_'-]{2,}|\d{2,}")
_ANCHOR_STOPWORDS = frozenset(
    {
        "本章",
        "场景",
        "必须",
        "需要",
        "通过",
        "推进",
        "体现",
        "完成",
        "不要",
        "不得",
        "目标",
        "状态",
        "情绪",
        "关系",
        "人物",
        "章节",
    }
)


@dataclass(frozen=True)
class AnchorCoverage:
    """Lightweight local coverage check for restructuring safety."""

    total: int
    covered: int
    covered_indices: tuple[int, ...]

    @property
    def ratio(self) -> float:
        if self.total <= 0:
            return 1.0
        return self.covered / self.total


@dataclass(frozen=True)
class WordCountRestructureResult:
    """Result of one pre-archive word-count transaction."""

    text: str
    changed: bool
    accepted: bool
    mode: str
    reason: str
    before: WordCountAssessment
    after: WordCountAssessment
    anchors: tuple[str, ...]
    coverage_before: AnchorCoverage
    coverage_after: AnchorCoverage

    def event_payload(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "accepted": self.accepted,
            "mode": self.mode,
            "reason": self.reason,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "anchor_count": len(self.anchors),
            "anchor_coverage_before": {
                "covered": self.coverage_before.covered,
                "total": self.coverage_before.total,
                "ratio": round(self.coverage_before.ratio, 3),
            },
            "anchor_coverage_after": {
                "covered": self.coverage_after.covered,
                "total": self.coverage_after.total,
                "ratio": round(self.coverage_after.ratio, 3),
            },
        }


def _clean_anchor(value: Any, *, max_len: int = 160) -> str:
    # Structured carry-forward items project their text under ``text``.
    text_attr = getattr(value, "text", None)
    if text_attr is not None and not isinstance(value, (str, bytes)):
        value = text_attr
    elif isinstance(value, dict) and ("text" in value or "item" in value):
        value = value.get("text") or value.get("item")
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


def _append_anchor(anchors: list[str], value: Any) -> None:
    text = _clean_anchor(value)
    if text and text not in anchors:
        anchors.append(text)


def _append_many(anchors: list[str], values: Any) -> None:
    if not values:
        return
    if isinstance(values, (str, bytes)):
        _append_anchor(anchors, values)
        return
    for item in values:
        _append_anchor(anchors, item)


def build_word_count_contract_anchors(
    *,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
) -> tuple[str, ...]:
    """Extract non-deletable contract anchors from plan/bridge/context.

    This stays schema-driven: no chapter-specific phrases are hardcoded here.
    """
    anchors: list[str] = []
    outline = getattr(bundle, "chapter_outline", None)

    _append_anchor(anchors, getattr(outline, "goal", ""))
    _append_many(anchors, getattr(outline, "main_plot_points", []))
    _append_many(anchors, getattr(outline, "subplot_points", []))

    _append_anchor(anchors, getattr(bridge, "action_handoff", ""))
    _append_anchor(anchors, getattr(bridge, "bridge_summary", ""))
    _append_anchor(anchors, getattr(bridge, "emotional_carryover", ""))
    _append_many(anchors, getattr(bridge, "pending_questions", []))
    _append_many(anchors, getattr(bridge, "sensory_anchors", []))
    causal_link = getattr(bridge, "causal_link", None)
    if causal_link is not None:
        _append_anchor(anchors, getattr(causal_link, "previous_event", ""))
        _append_anchor(anchors, getattr(causal_link, "causal_mechanism", ""))
        _append_anchor(anchors, getattr(causal_link, "unresolved_question", ""))
        _append_many(anchors, getattr(causal_link, "open_threads", []))

    _append_anchor(anchors, getattr(plan, "opening_contract", ""))
    _append_anchor(anchors, getattr(plan, "closing_contract", ""))
    _append_many(anchors, getattr(plan, "required_state_transitions", []))
    _append_many(anchors, getattr(plan, "key_revelations", []))
    _append_many(anchors, getattr(plan, "relationship_evolution", []))
    _append_many(anchors, getattr(plan, "intentional_callbacks", []))
    for scene in getattr(plan, "scene_intents", []) or []:
        _append_anchor(anchors, getattr(scene, "summary", ""))
        _append_anchor(anchors, getattr(scene, "purpose", ""))
        _append_anchor(anchors, getattr(scene, "conflict", ""))
        _append_anchor(anchors, getattr(scene, "required_outcome", ""))
        _append_anchor(anchors, getattr(scene, "exit_target_state", ""))
        _append_anchor(anchors, getattr(scene, "relationship_dynamics", ""))
        _append_many(anchors, getattr(scene, "required_characters", []))

    _append_many(anchors, getattr(packet, "must_carry_forward", []))
    _append_many(anchors, getattr(packet, "guard_constraints", []))
    return tuple(anchors[:80])


def _anchor_terms(anchor: str) -> tuple[str, ...]:
    terms: list[str] = []
    for raw in _TERM_RE.findall(anchor):
        term = raw.strip()
        if len(term) < 2 or term in _ANCHOR_STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", term):
            for i in range(0, len(term) - 1):
                chunk = term[i : i + 2]
                if chunk not in _ANCHOR_STOPWORDS and chunk not in terms:
                    terms.append(chunk)
            continue
        if term not in terms:
            terms.append(term)
    return tuple(terms[:12])


def measure_anchor_coverage(text: str, anchors: tuple[str, ...]) -> AnchorCoverage:
    """Return a conservative local estimate of preserved contract anchors."""
    if not anchors:
        return AnchorCoverage(total=0, covered=0, covered_indices=())

    covered_indices: list[int] = []
    for idx, anchor in enumerate(anchors):
        terms = _anchor_terms(anchor)
        if not terms:
            continue
        required = 1 if len(terms) <= 2 else max(2, math.ceil(len(terms) * 0.25))
        hits = sum(1 for term in terms if term in text)
        if hits >= required:
            covered_indices.append(idx)
    return AnchorCoverage(
        total=len(anchors),
        covered=len(covered_indices),
        covered_indices=tuple(covered_indices),
    )


def _mode_for_assessment(assessment: WordCountAssessment) -> str:
    if assessment.actual > assessment.target:
        return "light_compress" if assessment.action == "light_adjust" else "structural_compress"
    return "light_expand" if assessment.action == "light_adjust" else "structural_expand"


def _candidate_acceptance_reason(
    *,
    before: WordCountAssessment,
    after: WordCountAssessment,
    coverage_before: AnchorCoverage,
    coverage_after: AnchorCoverage,
) -> tuple[bool, str]:
    before_distance = abs(before.actual - before.target)
    after_distance = abs(after.actual - after.target)
    if after_distance >= before_distance:
        return False, "word_count_not_improved"
    if after.band in {"hard_reject", "structural"}:
        return False, "candidate_still_outside_buffer"

    lost = coverage_before.covered - coverage_after.covered
    allowed_loss = max(1, int(round(max(coverage_before.covered, 1) * 0.10)))
    if coverage_before.covered >= 3 and lost > allowed_loss:
        return False, "anchor_coverage_regressed"

    if after.within_acceptable:
        return True, "candidate_within_acceptable"
    if after.band == "buffer" and before.band in {"buffer", "structural", "hard_reject"}:
        return True, "candidate_improved_to_buffer"
    return False, "candidate_not_acceptable"


def _word_count_polish_guidance(
    *,
    before: WordCountAssessment,
    mode: str,
    direction: str,
    anchors: tuple[str, ...],
) -> str:
    anchors_text = "\n".join(f"- {anchor}" for anchor in anchors[:30])
    return (
        f"- 当前字数 {before.actual}，目标 {before.target}；本轮需要{direction}。\n"
        f"- 理想区间：{before.ideal_min}-{before.ideal_max} 字；"
        f"最低可接受区间：{before.acceptable_min}-{before.acceptable_max} 字；"
        f"buffer 上限/下限：{before.buffer_min}-{before.buffer_max} 字。\n"
        f"- 处理模式：{mode}。请从多个方面控制字数：合并重复心理描写、压缩低信息密度环境铺陈、"
        "删除重复解释、收紧同义反复对白、保留必要动作但减少复述。\n"
        "- 禁止改变事件顺序、人物动机、关系终态、章尾钩子和跨章承接。\n"
        "- 下列剧情锚点不可删除，只能改写得更凝练：\n"
        f"{anchors_text}"
    ).strip()


def word_count_archive_gate_enabled(settings: Any) -> bool:
    """Return whether the target-word-count archive gate is enabled."""
    return bool(getattr(settings, "long_word_count_archive_gate_enabled", True))


def word_count_archive_gate_max_rejections(settings: Any) -> int:
    """Return the per-chapter rejection cap for the archive word-count gate."""
    try:
        value = int(getattr(settings, "long_word_count_archive_gate_max_rejections", 2) or 0)
    except (TypeError, ValueError):
        value = 2
    return max(0, value)


def word_count_rejection_limit_reached(settings: Any, rejection_count: int) -> bool:
    """Return True when repeated target-word-count rejection should stop blocking."""
    limit = word_count_archive_gate_max_rejections(settings)
    if limit <= 0:
        return True
    return int(rejection_count or 0) >= limit


def word_count_gate_state_path(layout: Any, chapter_number: int) -> Path:
    """Return the small persistent state file used for repeated rejection counts."""
    return (
        Path(getattr(layout, "states_dir", Path(getattr(layout, "root", ".")) / "states"))
        / "word_count_archive_gate"
        / f"chapter_{int(chapter_number):03d}.json"
    )


def load_word_count_rejection_count(storage: Any, layout: Any, chapter_number: int) -> int:
    """Load the persisted rejection count for one chapter."""
    path = word_count_gate_state_path(layout, chapter_number)
    try:
        if hasattr(storage, "exists") and not storage.exists(path):
            return 0
        payload = storage.load_json(path)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        return max(0, int(payload.get("rejection_count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def record_word_count_rejection(
    storage: Any,
    layout: Any,
    chapter_number: int,
    result: WordCountRestructureResult,
) -> int:
    """Persist and return the updated repeated rejection count for one chapter."""
    count = load_word_count_rejection_count(storage, layout, chapter_number) + 1
    payload = {
        "chapter": int(chapter_number),
        "rejection_count": count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": result.reason,
        "mode": result.mode,
        "before": result.before.as_dict(),
        "after": result.after.as_dict(),
    }
    try:
        storage.save_json(word_count_gate_state_path(layout, chapter_number), payload)
    except Exception:
        pass
    return count


def clear_word_count_rejections(storage: Any, layout: Any, chapter_number: int) -> None:
    """Reset the repeated rejection counter after a chapter passes or is archived."""
    path = word_count_gate_state_path(layout, chapter_number)
    try:
        if hasattr(storage, "exists") and not storage.exists(path):
            return
        storage.save_json(
            path,
            {
                "chapter": int(chapter_number),
                "rejection_count": 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reason": "cleared",
            },
        )
    except Exception:
        pass


async def run_word_count_restructure(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
) -> WordCountRestructureResult:
    """Run at most one pre-archive restructure pass when length drifts."""
    target = int(getattr(getattr(bundle, "chapter_outline", None), "expected_word_count", 0) or 0)
    before = assess_word_count(current_text, target)
    anchors = build_word_count_contract_anchors(
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
    )
    coverage_before = measure_anchor_coverage(current_text, anchors)

    if before.action == "accept":
        return WordCountRestructureResult(
            text=current_text,
            changed=False,
            accepted=True,
            mode="none",
            reason="within_acceptable_range",
            before=before,
            after=before,
            anchors=anchors,
            coverage_before=coverage_before,
            coverage_after=coverage_before,
        )

    mode = _mode_for_assessment(before)
    direction = "压缩" if before.actual > before.target else "扩写"
    kernel_composer = await load_story_kernel_composer(runner, bundle)
    polish_kernel_context = (
        kernel_composer.compose_polish_input(chapter_number)
        if kernel_composer is not None
        else {}
    )
    polish_step = PolishStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    polish_result = await polish_step.run(
        PolishInput(
            chapter_number=chapter_number,
            chapter_title=getattr(getattr(bundle, "chapter_outline", None), "title", ""),
            chapter_text=current_text,
            tone=getattr(getattr(bundle, "story_bible", None), "tone", ""),
            genre=getattr(getattr(bundle, "story_bible", None), "genre", ""),
            pov_character=getattr(getattr(bundle, "chapter_outline", None), "pov_character", ""),
            repair_hints="字数精修：只调整表达密度，不改变剧情结构。",
            continuity_notes="",
            style_profile=getattr(bundle, "style_profile", None),
            target_word_count=target,
            word_count_guidance=_word_count_polish_guidance(
                before=before,
                mode=mode,
                direction=direction,
                anchors=anchors,
            ),
            kernel_context=polish_kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        )
    )
    candidate = (polish_result.polished_text or "").strip()
    if not candidate:
        return WordCountRestructureResult(
            text=current_text,
            changed=False,
            accepted=False,
            mode=mode,
            reason="empty_candidate",
            before=before,
            after=before,
            anchors=anchors,
            coverage_before=coverage_before,
            coverage_after=coverage_before,
        )

    after = assess_word_count(candidate, target)
    coverage_after = measure_anchor_coverage(candidate, anchors)
    accepted, reason = _candidate_acceptance_reason(
        before=before,
        after=after,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
    )
    return WordCountRestructureResult(
        text=candidate if accepted else current_text,
        changed=accepted and candidate != current_text,
        accepted=accepted,
        mode=mode,
        reason=reason,
        before=before,
        after=after if accepted else before,
        anchors=anchors,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
    )


__all__ = [
    "AnchorCoverage",
    "WordCountRestructureResult",
    "build_word_count_contract_anchors",
    "clear_word_count_rejections",
    "count_chapter_words",
    "load_word_count_rejection_count",
    "measure_anchor_coverage",
    "record_word_count_rejection",
    "run_word_count_restructure",
    "word_count_archive_gate_enabled",
    "word_count_archive_gate_max_rejections",
    "word_count_gate_state_path",
    "word_count_rejection_limit_reached",
]
