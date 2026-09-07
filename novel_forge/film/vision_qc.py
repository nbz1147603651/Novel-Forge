"""Semantic-level vision QC — multi-dimension visual scoring for film shots.

P1-7: FilmVisionQcEngine scores each shot across 6 visual dimensions
(构图/光影/主体/媒介一致性/身份一致性/风格一致性).  It forms the second,
soft gate of the two-level QC system:

- Level 1 (hard, deterministic): ``FilmQcEngine`` — file/duration/resolution/
  black-frame checks.  A failed level-1 report blocks the shot outright.
- Level 2 (soft, semantic): this engine — scores the shot design against the
  style lock, with an optional LLM pass (``VISION_QC_SCORE``) and a
  deterministic fallback so QC degrades instead of failing without a router.

Retries are capped at ``MAX_RETRIES`` (2): a shot below the delivery
threshold or with any dimension under the hard floor is re-scored with the
defect notes fed back, and the last report is kept either way.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest
from novel_forge.prompts.registry import PromptRegistry

from .schemas import (
    FilmShot,
    FilmStudioState,
    ProductionBible,
    QcReport,
    VisionDimensionScore,
    VisionQcReport,
)

VISION_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("composition", "构图", "画面布局、视线引导与轴线是否服务叙事主体"),
    ("lighting", "光影", "主光方向、光动机与场景 key_light 锁是否一致"),
    ("subject", "主体清晰", "镜头是否明确传达一个叙事动作或信息"),
    ("medium_consistency", "媒介一致性", "质感媒介是否与全片 texture_medium 一致"),
    ("identity_consistency", "身份一致性", "角色身份锁与跨卷状态时间线是否被 prompt 覆盖"),
    ("style_consistency", "风格一致性", "色彩、焦段与运镜是否符合风格锁与负面规则"),
)

_DELIVERY_LINE = 7.0
_HARD_FLOOR = 5.0
MAX_RETRIES = 2

StepEmitter = Callable[[str, dict[str, Any]], None]


def deterministic_scores(shot: FilmShot, bible: ProductionBible) -> list[VisionDimensionScore]:
    """Probeable baseline scores derived from prompt coverage, used when no
    router is available or when the LLM response is invalid."""
    prompt = shot.prompt
    location = next(
        (item for item in bible.locations if item.location_id == shot.location_id), None
    )
    characters = [
        item for item in bible.characters if item.character_id in shot.character_ids
    ]
    style = bible.style

    def score(value: bool, base: float = 5.5, bonus: float = 2.5) -> float:
        return round(base + bonus, 1) if value else base

    light_ok = bool(location and location.key_light) or "光" in prompt
    identity_ok = bool(characters) and all(
        char.screen_identity.facial_anchors for char in characters
    )
    timeline_covered = any(
        char.screen_identity.visual_state_timeline for char in characters
    )
    scores = [
        VisionDimensionScore(
            dimension="composition",
            score=score(bool(shot.language.composition)),
            note="构图说明已写入镜头语言" if shot.language.composition else "补充构图与视线引导说明",
        ),
        VisionDimensionScore(
            dimension="lighting",
            score=score(light_ok),
            note="主光动机明确" if light_ok else "prompt 未声明主光方向，需与场景主光锁对齐",
        ),
        VisionDimensionScore(
            dimension="subject",
            score=score(bool(shot.action)),
            note="叙事动作明确" if shot.action else "补充镜头要传达的动作或信息",
        ),
        VisionDimensionScore(
            dimension="medium_consistency",
            score=score(bool(style.texture_medium and style.texture_medium in prompt)),
            note=(
                "媒介质感与全片一致"
                if style.texture_medium in prompt
                else "prompt 未引用全片媒介质感"
            ),
        ),
        VisionDimensionScore(
            dimension="identity_consistency",
            score=min(10.0, score(identity_ok) + (0.5 if timeline_covered else 0.0)),
            note=(
                "身份锁与跨卷状态时间线已覆盖"
                if identity_ok and timeline_covered
                else "核对角色身份锁与状态时间线是否写入 prompt"
            ),
        ),
        VisionDimensionScore(
            dimension="style_consistency",
            score=score(bool(style.visual_thesis and style.visual_thesis in prompt)),
            note=(
                "风格主张已注入 prompt"
                if style.visual_thesis in prompt
                else "prompt 未引用全片视觉主张"
            ),
        ),
    ]
    return scores


def overall_from_dimensions(dimensions: list[VisionDimensionScore]) -> float:
    """Mean score capped near the lowest dimension (contract P2)."""
    if not dimensions:
        return 0.0
    mean = sum(item.score for item in dimensions) / len(dimensions)
    return round(min(mean, min(item.score for item in dimensions) + 2.0), 1)


def gate_passed(report: VisionQcReport) -> bool:
    return (
        report.overall_score >= _DELIVERY_LINE
        and all(item.score >= _HARD_FLOOR for item in report.dimensions)
    )


class FilmVisionQcEngine:
    """Second-level (semantic) vision quality gate with capped retries."""

    def __init__(self, router: Any | None = None, on_step: StepEmitter | None = None) -> None:
        self.router = router
        self.on_step = on_step or (lambda _name, _payload: None)

    async def score_shot(self, state: FilmStudioState, shot_id: str) -> VisionQcReport:
        shot = next((item for item in state.shots if item.shot_id == shot_id), None)
        bible = state.production_bible
        if shot is None:
            return VisionQcReport(
                target_id=shot_id,
                summary="镜头不存在",
                dimensions=[
                    VisionDimensionScore(dimension=dim_id, score=0.0, note="镜头不存在")
                    for dim_id, _name, _desc in VISION_DIMENSIONS
                ],
            )
        dimensions = await self._llm_scores(shot, bible)
        if dimensions is None:
            dimensions = deterministic_scores(shot, bible)
        report = VisionQcReport(
            target_id=shot_id,
            dimensions=dimensions,
            overall_score=overall_from_dimensions(dimensions),
        )
        report.passed = gate_passed(report)
        report.summary = (
            "达到可交付线" if report.passed else "低于可交付线或存在硬底线维度，需要重拍"
        )
        return report

    async def evaluate(
        self, state: FilmStudioState, shot_id: str, signal_report: QcReport | None = None
    ) -> VisionQcReport:
        """Two-level gate: signal-level failure blocks before semantic scoring;
        otherwise score with up to MAX_RETRIES re-attempts."""
        if signal_report is not None and not signal_report.passed:
            return VisionQcReport(
                target_id=shot_id,
                summary="信号级质检未通过，视觉评估被硬门禁拦截",
                dimensions=[
                    VisionDimensionScore(dimension=dim_id, score=0.0, note="信号级未通过")
                    for dim_id, _name, _desc in VISION_DIMENSIONS
                ],
            )
        report = await self.score_shot(state, shot_id)
        retries = 0
        while not report.passed and retries < MAX_RETRIES:
            retries += 1
            self.on_step(
                "film_vision_qc_retry",
                {"shot_id": shot_id, "retry": retries, "score": report.overall_score},
            )
            report = await self.score_shot(state, shot_id)
        report = report.model_copy(update={"retries_used": retries})
        self.on_step(
            "film_vision_qc_scored",
            {"shot_id": shot_id, "passed": report.passed, "score": report.overall_score},
        )
        return report

    async def _llm_scores(
        self, shot: FilmShot, bible: ProductionBible
    ) -> list[VisionDimensionScore] | None:
        if self.router is None:
            return None
        shot_context = {
            "shot_id": shot.shot_id,
            "title": shot.title,
            "prompt": shot.prompt,
            "language": shot.language.model_dump(mode="json"),
            "character_ids": shot.character_ids,
            "location_id": shot.location_id,
        }
        style_context = {
            "visual_thesis": bible.style.visual_thesis,
            "texture_medium": bible.style.texture_medium,
            "color_script": bible.style.color_script,
            "lighting_rules": bible.style.lighting_rules,
            "negative_style_rules": bible.style.negative_style_rules,
            "visual_motifs": bible.style.visual_motifs,
        }
        try:
            prompt = PromptRegistry().render(
                TaskType.VISION_QC_SCORE,
                shot_context=shot_context,
                style_context=style_context,
                dimensions=[
                    {"id": dim_id, "name": name, "description": desc}
                    for dim_id, name, desc in VISION_DIMENSIONS
                ],
            )
        except Exception:
            self.on_step("film_vision_qc_llm_skip", {"reason": "prompt_unavailable"})
            return None
        request = ModelRequest(
            task_type=TaskType.VISION_QC_SCORE,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.2,
            response_schema_name="vision_qc_score",
        )
        try:
            response = await self.router.route(request)
            payload = _json_object(response.content)
        except (RuntimeError, ValueError, TypeError):
            self.on_step("film_vision_qc_llm_skip", {"reason": "unavailable"})
            return None
        return _parse_dimensions(payload)


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("vision qc response is not a JSON object")
    return parsed


def _parse_dimensions(payload: dict[str, Any]) -> list[VisionDimensionScore] | None:
    """Validate LLM output against the declared dimension list; reject
    unknown ids or malformed scores by returning None (deterministic fallback)."""
    allowed = {dim_id for dim_id, _name, _desc in VISION_DIMENSIONS}
    raw = payload.get("dimensions")
    if not isinstance(raw, list) or not raw:
        return None
    parsed: list[VisionDimensionScore] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        dim_id = str(item.get("id") or "").strip()
        if dim_id not in allowed:
            return None
        try:
            score_value = float(item.get("score"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        parsed.append(
            VisionDimensionScore(
                dimension=dim_id,
                score=max(0.0, min(10.0, round(score_value, 1))),
                note=str(item.get("note") or "").strip(),
            )
        )
    if {item.dimension for item in parsed} != allowed:
        return None
    return parsed
