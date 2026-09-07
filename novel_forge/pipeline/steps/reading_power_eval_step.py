"""ReadingPowerEvalStep — evaluates chapter reader-pull quality.

This step calls the LLM to assess:
1. Chapter-ending hook type and strength
2. Micro-payoffs within the chapter
3. Whether previous hook promises are fulfilled
4. Overall reading power score

The step is designed to run in the quality stage alongside existing checkers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.review.review_contracts import (
    compile_repair_tickets_from_findings,
    normalize_review_mode,
    reading_power_report_to_findings,
    source_text_hash,
)
from novel_forge.core.review.review_precision import prepare_findings_for_repair
from novel_forge.core.schemas.reading_power import (
    MicroPayoff,
    MicroPayoffType,
    ReadingPowerReport,
)
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep

_log = get_logger("pipeline.steps.reading_power_eval")

# Valid values for safe parsing
_VALID_HOOK_TYPES = {"crisis", "mystery", "emotion", "choice", "desire", "none"}
_HOOK_TYPE_ALIASES = {
    "suspense": "mystery",
    "revelation": "mystery",
    "悬念": "mystery",
    "揭示": "mystery",
    "危机": "crisis",
    "情绪": "emotion",
    "选择": "choice",
    "期待": "desire",
}
_VALID_STRENGTHS = {"strong", "medium", "weak"}
_VALID_PAYOFF_TYPES = {t.value for t in MicroPayoffType}


@dataclass
class ReadingPowerInput:
    chapter_number: int
    chapter_text: str
    chapter_type: str = ""
    previous_hook_description: str = ""
    genre: str = ""
    min_payoffs: int = 1
    expected_hook: dict[str, Any] | None = None
    expected_payoffs: list[dict[str, Any]] = field(default_factory=list)
    suspense_timeline_entries: list[dict[str, Any]] = field(default_factory=list)
    hook_score_config: dict[str, Any] | None = None
    excerpt_strategy: str = ""

    # LLM 只消费与追读力直接相关的规划字段。
    narrative_phase_context: dict[str, Any] | None = None  # 当前叙事阶段上下文
    suspense_schedule: list[dict[str, Any]] = field(default_factory=list)  # 悬念时间表
    genre_weights: dict[str, float] | None = None  # 本地评分权重，不转发给 LLM
    main_plot_continuity: dict[str, Any] | None = None  # 兼容旧调用，不转发给 LLM
    revelation_budget: int = 2  # 单章重大揭示上限（StoryBible）
    min_unresolved_threads: int = 1  # 最少保留未决线索
    preferred_payoff_types: list[str] = field(default_factory=list)  # 偏好微兑现类型
    pacing_dialogue_ratio: str = "medium"  # 节奏/对话占比
    main_plot_points: list[str] = field(default_factory=list)  # 本章主线推进点
    consecutive_main_plot_stall: int = 0  # 连续主线停滞章数
    strict_review: bool = False
    """When True, use lower temperature and inject an independent reviewer prompt.
    Used for post-repair re-evaluation to reduce false positives."""

    kernel_context: dict[str, Any] | None = None
    """StoryKernel field slices from ContextComposer. Fields are merged into
    the LLM context without overriding explicit fields."""
    chapter_source_slice: Any | None = None


class ReadingPowerEvalStep(PipelineStep[ReadingPowerInput, ReadingPowerReport]):
    """LLM-based reading power evaluation for a chapter."""

    @property
    def step_name(self) -> str:
        return "reading_power_eval"

    @staticmethod
    def _scope_expected_hook(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            "hook_type": str(value.get("hook_type", "") or ""),
            "hook_strength": str(value.get("hook_strength", "") or ""),
            "hook_description": str(value.get("hook_description", "") or ""),
        }

    @staticmethod
    def _scope_payoffs(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scoped: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            scoped.append(
                {
                    "payoff_type": str(item.get("payoff_type", item.get("type", "")) or ""),
                    "description": str(item.get("description", "") or ""),
                    "strength": str(item.get("strength", "") or ""),
                }
            )
        return scoped

    @staticmethod
    def _scope_suspense_entries(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scoped: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            scoped.append(
                {
                    "suspense_id": str(item.get("suspense_id", "") or ""),
                    "suspense_description": str(
                        item.get("suspense_description", item.get("description", "")) or ""
                    ),
                    "setup_chapter": item.get("setup_chapter", ""),
                    "planned_resolution_chapter": item.get("planned_resolution_chapter", ""),
                    "urgency_level": str(item.get("urgency_level", "") or ""),
                }
            )
        return scoped

    @staticmethod
    def _scope_phase_context(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            "phase_name": str(value.get("phase_name", value.get("name", "")) or ""),
            "description": str(value.get("description", "") or ""),
            "tension_level": str(value.get("tension_level", "") or ""),
        }

    @classmethod
    def _build_llm_context(cls, input_data: ReadingPowerInput) -> dict[str, Any]:
        context: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            "chapter_type": input_data.chapter_type or "unknown",
            "previous_hook_description": input_data.previous_hook_description or "无",
            "genre": input_data.genre or "other",
            "min_payoffs": input_data.min_payoffs,
            "revelation_budget": input_data.revelation_budget,
            "min_unresolved_threads": input_data.min_unresolved_threads,
            "consecutive_main_plot_stall": input_data.consecutive_main_plot_stall,
        }
        if input_data.excerpt_strategy:
            context["excerpt_strategy"] = input_data.excerpt_strategy
        expected_hook = cls._scope_expected_hook(input_data.expected_hook)
        if expected_hook is not None:
            context["expected_hook"] = expected_hook
        expected_payoffs = cls._scope_payoffs(input_data.expected_payoffs)
        if expected_payoffs:
            context["expected_payoffs"] = expected_payoffs
        suspense_timeline_entries = cls._scope_suspense_entries(
            input_data.suspense_timeline_entries
        )
        if suspense_timeline_entries:
            context["suspense_timeline_entries"] = suspense_timeline_entries
        if input_data.hook_score_config is not None:
            context["hook_score_config"] = input_data.hook_score_config
        phase_context = cls._scope_phase_context(input_data.narrative_phase_context)
        if phase_context is not None:
            context["narrative_phase_context"] = phase_context
        suspense_schedule = cls._scope_suspense_entries(input_data.suspense_schedule)
        if suspense_schedule:
            context["suspense_schedule"] = suspense_schedule
        if input_data.preferred_payoff_types:
            context["preferred_payoff_types"] = input_data.preferred_payoff_types
        if input_data.main_plot_points:
            context["main_plot_points"] = input_data.main_plot_points
        if input_data.strict_review:
            context["strict_review"] = True
        if input_data.chapter_source_slice is not None:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )

            context["stage_cards"] = {
                "source": project_stage_source_cards(
                    input_data.chapter_source_slice,
                    stage="review",
                )
            }

        # Merge StoryKernel field slices (explicit fields take precedence)
        if input_data.kernel_context is not None:
            for key, value in input_data.kernel_context.items():
                if key not in context and value is not None:
                    context[key] = value

        return context

    async def _execute(self, input_data: ReadingPowerInput) -> ReadingPowerReport:
        context = self._build_llm_context(input_data)

        # Strict review mode: lower temperature + independent reviewer prompt
        _rp_review_temp = getattr(self.settings, "temp_post_repair_review", 0.0)
        _rp_review_prompt = getattr(self.settings, "long_post_repair_review_independent_prompt", "")
        if input_data.strict_review:
            if _rp_review_prompt:
                context["independent_reviewer_note"] = _rp_review_prompt
        try:
            data = await self._call_with_retry(
                TaskType.EVALUATE_READING_POWER,
                context,
                max_tokens=self._dynamic_max_tokens(
                    TaskType.EVALUATE_READING_POWER,
                    max(1200, len(input_data.chapter_text) // 5),
                    prompt_overhead=2200,
                    min_tokens=2048,
                ),
                temperature=_rp_review_temp if input_data.strict_review else 0.3,
                required_keys=(
                    "hook_type",
                    "hook_strength",
                    "hook_description",
                    "prev_hook_fulfilled",
                    "micro_payoffs",
                    "is_transition",
                    "next_chapter_reason",
                    "information_pacing",
                    "main_plot_depth",
                    "tension_match",
                    "character_drive",
                ),
            )
        except Exception as exc:
            _log.warning(
                "reading_power_eval_failed | chapter=%d | error=%s",
                input_data.chapter_number,
                exc,
            )
            return self._default_report(input_data.chapter_number, reason="llm_error")

        return self._parse_report(data if isinstance(data, dict) else {}, input_data)

    def _parse_report(
        self, data: dict[str, Any], input_data: ReadingPowerInput
    ) -> ReadingPowerReport:
        """Parse LLM response into ReadingPowerReport."""
        hook_type = str(data.get("hook_type", "none")).lower().strip()
        hook_type = _HOOK_TYPE_ALIASES.get(hook_type, hook_type)
        if hook_type not in _VALID_HOOK_TYPES:
            hook_type = "none"

        hook_strength = str(data.get("hook_strength", "weak")).lower().strip()
        if hook_strength not in _VALID_STRENGTHS:
            hook_strength = "weak"

        raw_payoffs = data.get("micro_payoffs", [])
        micro_payoffs: list[MicroPayoff] = []
        if isinstance(raw_payoffs, list):
            for item in raw_payoffs:
                if not isinstance(item, dict):
                    continue
                ptype = str(item.get("type", item.get("payoff_type", ""))).lower()
                if ptype not in _VALID_PAYOFF_TYPES:
                    continue
                micro_payoffs.append(
                    MicroPayoff(
                        payoff_type=MicroPayoffType(ptype),
                        description=str(item.get("description", "")),
                        strength=str(item.get("strength", "medium")),
                    )
                )

        review_mode = normalize_review_mode("targeted_recheck" if input_data.strict_review else "full")
        text_hash = source_text_hash(input_data.chapter_text)
        report = ReadingPowerReport(
            chapter=input_data.chapter_number,
            review_mode=review_mode,
            source_text_hash=text_hash,
            hook_type=hook_type,
            hook_strength=hook_strength,
            hook_description=str(data.get("hook_description", "")),
            prev_hook_fulfilled=self._coerce_bool(data.get("prev_hook_fulfilled", True)),
            micro_payoffs=micro_payoffs,
            is_transition=self._coerce_bool(data.get("is_transition", False)),
            next_chapter_reason=str(data.get("next_chapter_reason", "")),
        )

        outline_hook_match = data.get("outline_hook_match")
        if outline_hook_match and isinstance(outline_hook_match, dict):
            report.outline_hook_match = {
                "matched": bool(outline_hook_match.get("matched", False)),
                "match_type": str(outline_hook_match.get("match_type", "different")),
                "reason": str(outline_hook_match.get("reason", "")),
            }

        outline_payoff_coverage = data.get("outline_payoff_coverage")
        if outline_payoff_coverage and isinstance(outline_payoff_coverage, dict):
            report.outline_payoff_coverage = {
                "covered_count": int(outline_payoff_coverage.get("covered_count", 0)),
                "total_expected": int(outline_payoff_coverage.get("total_expected", 0)),
                "coverage_ratio": float(outline_payoff_coverage.get("coverage_ratio", 0.0)),
                "missing": list(outline_payoff_coverage.get("missing", []) or []),
                "unexpected": list(outline_payoff_coverage.get("unexpected", []) or []),
            }

        report.resolved_suspense_ids = self._coerce_id_list(data.get("resolved_suspense_ids", []))
        report.unresolved_suspense_ids = self._coerce_id_list(
            data.get("unresolved_suspense_ids", [])
        )

        # Phase 1: 解析新增字段
        report.information_pacing = str(data.get("information_pacing", "balanced")).lower()
        if report.information_pacing not in ("rushed", "balanced", "slow", "stagnant"):
            report.information_pacing = "balanced"
        report.information_pacing_score = self._coerce_dimension_score(
            data.get("information_pacing_score"),
            default=self._default_information_pacing_score(report.information_pacing),
            positive_label=report.information_pacing in {"balanced", "rushed", "slow"},
        )
        report.information_pacing_score = max(0.0, min(2.0, report.information_pacing_score))

        report.main_plot_depth = str(data.get("main_plot_depth", "moderate")).lower()
        if report.main_plot_depth not in ("deep", "moderate", "surface", "stalled"):
            report.main_plot_depth = "moderate"
        report.main_plot_advancement_notes = str(data.get("main_plot_advancement_notes", ""))

        report.tension_match = str(data.get("tension_match", "matched")).lower()
        if report.tension_match not in ("matched", "elevated", "depressed"):
            report.tension_match = "matched"
        report.tension_match_score = self._coerce_dimension_score(
            data.get("tension_match_score"),
            default=self._default_tension_match_score(report.tension_match),
            positive_label=report.tension_match == "matched",
        )
        report.tension_match_score = max(0.0, min(2.0, report.tension_match_score))

        report.revelation_count = int(data.get("revelation_count", 0))
        report.revelation_over_budget = self._coerce_bool(data.get("revelation_over_budget", False))
        report.consecutive_main_plot_stall = int(data.get("consecutive_main_plot_stall", 0))
        report.character_drive = str(data.get("character_drive", "moderate")).lower()
        if report.character_drive not in ("strong", "moderate", "weak"):
            report.character_drive = "moderate"
        report.character_drive_notes = str(data.get("character_drive_notes", ""))
        # 传递题材自适应权重
        if input_data.genre_weights is not None:
            report.genre_adapted_weights = input_data.genre_weights

        report.compute_score(
            min_payoffs=input_data.min_payoffs,
            hook_score_config=input_data.hook_score_config,
            genre_weights=input_data.genre_weights,
            preferred_payoff_types=input_data.preferred_payoff_types,
        )
        findings = reading_power_report_to_findings(
            report,
            chapter_number=input_data.chapter_number,
            current_text_hash=text_hash,
            min_payoffs=input_data.min_payoffs,
            review_mode=review_mode,
            review_round=2 if input_data.strict_review else 1,
        )
        findings, readiness = prepare_findings_for_repair(
            findings,
            current_text=input_data.chapter_text,
            completed_chapters=[input_data.chapter_number],
        )
        report.review_findings = findings
        report.repair_readiness = readiness
        report.repair_tickets = compile_repair_tickets_from_findings(
            findings,
            require_auto_repair_eligible=True,
        )
        return report

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "y", "1", "是"}:
                return True
            if lowered in {"false", "no", "n", "0", "否", "无"}:
                return False
        return bool(value)

    @staticmethod
    def _coerce_dimension_score(
        value: Any,
        *,
        default: float,
        positive_label: bool = False,
    ) -> float:
        if isinstance(value, bool):
            return default
        try:
            score = float(value)
        except (TypeError, ValueError):
            return default
        if positive_label and score <= 0.0:
            return default
        if positive_label and default > 1.0 and 0.0 < score <= 1.0:
            # Some providers emit confidence-like 0-1 values despite the
            # contract's 0-2 scale. Preserve their nuance while preventing
            # balanced/matched labels from being scored as failing grades.
            return min(2.0, score * 2.0)
        return score

    @staticmethod
    def _default_information_pacing_score(label: str) -> float:
        return {
            "balanced": 2.0,
            "rushed": 1.0,
            "slow": 1.0,
            "stagnant": 0.0,
        }.get(label, 1.0)

    @staticmethod
    def _default_tension_match_score(label: str) -> float:
        return {
            "matched": 2.0,
            "elevated": 1.2,
            "depressed": 0.8,
        }.get(label, 1.0)

    @staticmethod
    def _coerce_id_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        ids: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ids.append(text)
        return ids

    @staticmethod
    def _default_report(chapter_number: int, *, reason: str = "unknown") -> ReadingPowerReport:
        """Fallback report when LLM call fails."""
        report = ReadingPowerReport(
            chapter=chapter_number,
            evaluation_status="fallback",
            is_fallback=True,
            fallback_reason=reason,
            overall_score=0.0,
            suggestions=[
                "追读力评估未完成：这是兜底报告，请重试评估或检查模型配置",
                "真实评估前仅建议人工快速检查：章尾是否有明确钩子，章内是否有信息/关系/能力类微兑现",
            ],
        )
        return report
