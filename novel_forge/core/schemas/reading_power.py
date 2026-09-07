"""Reader Pull (追读力) schemas — quantifies "why the reader clicks next chapter".

Defines hook types, micro-payoff tracking, and chapter-level reading power scores.
These schemas are consumed by the ReadingPowerEvalStep and surfaced in reports.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.review import RepairTicket, RepairVerificationResult, ReviewFinding
from novel_forge.core.utils.type_coerce import stringify_text_value

if TYPE_CHECKING:
    from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection


class HookType(str, Enum):
    """Chapter-ending hook classification."""

    CRISIS = "crisis"  # 危机钩：危险逼近，读者担心
    MYSTERY = "mystery"  # 悬念钩：信息缺口，读者好奇
    EMOTION = "emotion"  # 情绪钩：强情绪触发（愤怒/心疼/心动）
    CHOICE = "choice"  # 选择钩：两难抉择，读者想知道选择
    DESIRE = "desire"  # 渴望钩：好事将至，读者期待
    NONE = "none"  # 无明显钩子


class HookStrength(str, Enum):
    """Hook intensity level."""

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


class MicroPayoffType(str, Enum):
    """Types of micro-payoffs that satisfy reader expectations within a chapter."""

    INFORMATION = "information"  # 信息兑现：揭示新信息/线索/真相
    RELATIONSHIP = "relationship"  # 关系兑现：关系推进/确认/变化
    ABILITY = "ability"  # 能力兑现：能力提升/新技能展示
    RESOURCE = "resource"  # 资源兑现：获得物品/资源/财富
    RECOGNITION = "recognition"  # 认可兑现：获得认可/面子/地位
    EMOTION = "emotion"  # 情绪兑现：情绪释放/共鸣
    CLUE = "clue"  # 线索兑现：伏笔回收/推进


class MicroPayoff(VersionedSchema):
    """A single micro-payoff instance detected in a chapter."""

    payoff_type: MicroPayoffType
    description: str = Field(default="", description="具体兑现内容简述")
    strength: str = Field(default="medium", description="兑现力度：strong/medium/weak")

    @field_validator("description", "strength", mode="before")
    @classmethod
    def _coerce_micropayoff_text_fields(cls, v: Any) -> str:
        """Flatten any nested dict/list from LLM output into a simple string."""
        return stringify_text_value(v)


class ReadingPowerReport(VersionedSchema):
    """Chapter-level reading power assessment."""

    chapter: int = Field(ge=1)
    review_mode: str = Field(
        default="full_review",
        description="full_review / targeted_recheck / regression_scan.",
    )
    source_text_hash: str = Field(default="")
    review_findings: list[ReviewFinding] = Field(default_factory=list)
    repair_tickets: list[RepairTicket] = Field(default_factory=list)
    repair_readiness: dict[str, Any] = Field(default_factory=dict)
    verification_results: list[RepairVerificationResult] = Field(default_factory=list)

    # ── Hook (章尾钩子) ──────────────────────────────────────────────────
    hook_type: str = Field(
        default="none",
        description="章尾钩子类型：crisis/mystery/emotion/choice/desire/none",
    )
    hook_strength: str = Field(
        default="weak",
        description="章尾钩子强度：strong/medium/weak",
    )
    hook_description: str = Field(
        default="",
        description="钩子的一句话描述",
    )
    prev_hook_fulfilled: bool = Field(
        default=True,
        description="上章钩子承诺是否在本章得到回应",
    )

    # ── Micro-payoffs (章内微兑现) ───────────────────────────────────────
    micro_payoffs: list[MicroPayoff] = Field(
        default_factory=list,
        description="本章检测到的微兑现列表",
    )

    # ── Chapter pacing metadata ──────────────────────────────────────────
    is_transition: bool = Field(
        default=False,
        description="是否为过渡章/铺垫章",
    )
    next_chapter_reason: str = Field(
        default="",
        description="读者点击下一章的核心驱动力（一句话）",
    )

    # ── Aggregate score ──────────────────────────────────────────────────
    overall_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="追读力综合评分（0-10），由规则计算",
    )
    score_breakdown: dict[str, Any] = Field(
        default_factory=dict,
        description="追读力综合评分拆解：分项归一化分、权重、贡献、加成与扣分。",
    )

    # ── Suggestions ──────────────────────────────────────────────────────
    suggestions: list[str] = Field(
        default_factory=list,
        description="追读力提升建议",
    )
    # ── Evaluation status ───────────────────────────────────────────────
    evaluation_status: str = Field(
        default="ok",
        description="评估状态：ok/fallback。fallback 表示未完成真实 LLM 诊断",
    )
    is_fallback: bool = Field(
        default=False,
        description="是否为兜底报告。兜底报告仅用于 UI 占位，不应参与趋势/门禁决策",
    )
    fallback_reason: str = Field(
        default="",
        description="兜底原因摘要",
    )

    # ── Outline match results (大纲对比结果) ──────────────────────────────
    outline_hook_match: dict[str, Any] | None = Field(
        default=None,
        description="大纲钩子匹配结果：{matched, match_type, reason}",
    )
    outline_payoff_coverage: dict[str, Any] | None = Field(
        default=None,
        description="大纲微兑现覆盖结果：{covered_count, total_expected, coverage_ratio, missing, unexpected}",
    )
    resolved_suspense_ids: list[str] = Field(
        default_factory=list,
        description="本章确认兑现的跨章悬念ID列表，由评估器根据悬念时间表判断",
    )
    unresolved_suspense_ids: list[str] = Field(
        default_factory=list,
        description="本章仍未兑现但被检查到的跨章悬念ID列表",
    )

    # ── Phase 1: 新增评估维度（信息释放节奏、主线推进、张力匹配）──────────
    information_pacing: str = Field(
        default="balanced",
        description="信息释放节奏：rushed（过快）/balanced（适中）/slow（不足）/stagnant（几乎无）",
    )
    information_pacing_score: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="信息释放节奏得分（0-2）",
    )
    main_plot_depth: str = Field(
        default="moderate",
        description="主线推进深度：deep（实质推进）/moderate（阶段进展）/surface（触及未推进）/stalled（停滞）",
    )
    main_plot_advancement_notes: str = Field(
        default="",
        description="主线推进质量笔记",
    )
    tension_match: str = Field(
        default="matched",
        description="张力匹配：matched（匹配）/elevated（高于预期）/depressed（低于预期）",
    )
    tension_match_score: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="张力匹配得分（0-2）",
    )
    revelation_count: int = Field(
        default=0,
        ge=0,
        description="本章重大揭示计数",
    )
    revelation_over_budget: bool = Field(
        default=False,
        description="是否超出 StoryBible.max_key_revelations_per_chapter",
    )
    genre_adapted_weights: dict[str, float] = Field(
        default_factory=dict,
        description="题材自适应权重（供前端展示和调试）",
    )
    consecutive_main_plot_stall: int = Field(
        default=0,
        ge=0,
        description="连续主线停滞章数",
    )
    character_drive: str = Field(
        default="moderate",
        description="角色驱动力：strong（主动抉择）/ moderate（回应但有立场）/ weak（被动跟随）",
    )
    character_drive_notes: str = Field(
        default="",
        description="角色驱动力质量简述（可选）",
    )

    @field_validator("hook_description", "next_chapter_reason", "main_plot_advancement_notes", mode="before")
    @classmethod
    def _coerce_report_text_fields(cls, v: Any) -> str:
        """Flatten any nested dict/list from LLM output into a simple string."""
        return stringify_text_value(v)

    def compute_score(
        self,
        *,
        min_payoffs: int = 1,
        hook_score_config: dict[str, Any] | None = None,
        element_selection: "BlueprintElementSelection | None" = None,
        genre_weights: dict[str, float] | None = None,
        preferred_payoff_types: list[str] | None = None,
    ) -> None:
        """Rule-based score computation with multi-dimensional weights.

        Phase 3: 修复满分通胀 — 乘法校准 + 提高满分门槛。

        核心改动：
        - 钩子归一化乘数 2.5→2.0，单维度不再轻易满分
        - 微兑现 cap 3→5，多兑现章节有区分度
        - 奖励/惩罚改为乘法校准（×1.03~1.05），避免加法叠加冲破天花板
        - 满分 10 分需要加权基础分 8.0+ 且多项对齐，区分"好"与"极好"

        Scoring rubric (configurable via hook_score_config):
        - Hook strength: configurable (default strong=4, medium=2.5, weak=1, none=0)
        - Micro-payoff count: each payoff adds weighted score (capped at payoff_cap)
        - Information pacing: 0-2 points
        - Main plot depth: 0-10 points
        - Tension match: 0-2 points
        - Bonuses: multiplicative (×1.03~1.05) for hook fulfillment, outline alignment
        - Penalties: multiplicative (÷1.03~1.05) for unfulfilled hooks, mismatches

        Args:
            min_payoffs: Minimum expected micro-payoffs for suggestions.
            hook_score_config: Optional dict with keys:
                hook_score_strong, hook_score_medium, hook_score_weak,
                payoff_cap, transition_penalty.
            element_selection: Optional BlueprintElementSelection to extract
                hook_score_config from quality_hook_score_config element.
            genre_weights: Optional dict with multi-dimensional weights.
            preferred_payoff_types: Optional list of preferred payoff types.
        """
        if hook_score_config is None and element_selection is not None:
            hook_score_config = getattr(element_selection, "hook_score_config", None)

        if hook_score_config:
            hook_scores = {
                "strong": float(hook_score_config.get("hook_score_strong", 4.0)),
                "medium": float(hook_score_config.get("hook_score_medium", 2.5)),
                "weak": float(hook_score_config.get("hook_score_weak", 1.0)),
            }
            payoff_cap = int(hook_score_config.get("payoff_cap", 5))
            transition_penalty = float(hook_score_config.get("transition_penalty", 1.0))
        else:
            hook_scores = {"strong": 4.0, "medium": 2.5, "weak": 1.0}
            payoff_cap = 5
            transition_penalty = 1.0

        # Get genre-adapted weights (or use defaults)
        if genre_weights:
            weights = genre_weights
        else:
            weights = {
                "hook_strength": 0.25,
                "payoff_density": 0.20,
                "information_pacing": 0.20,
                "main_plot_depth": 0.15,
                "tension_match": 0.20,
            }

        # ── 1. Hook strength score (0-10 normalized) ──
        # Phase 3: multiplier 2.5→2.0 so strong hook = 8.0, not 10.0
        hook_type = str(self.hook_type or "none").lower()
        hook_strength = str(self.hook_strength or "weak").lower()
        raw_hook_score = 0.0 if hook_type == "none" else hook_scores.get(hook_strength, 0.0)
        hook_score_normalized = min(10.0, raw_hook_score * 2.0)

        # ── 2. Micro-payoff score with type weights (0-10 normalized) ──
        payoff_type_weights = {
            "information": 1.0,
            "relationship": 1.0,
            "ability": 1.0,
            "resource": 0.8,
            "recognition": 0.8,
            "emotion": 1.0,
            "clue": 1.2,
        }
        preferred = set(preferred_payoff_types or [])

        payoff_strength_scores = {"strong": 1.2, "medium": 1.0, "weak": 0.6}
        payoff_score = 0.0
        for payoff in self.micro_payoffs:
            payoff_strength_val = str(getattr(payoff, "strength", "medium") or "medium").lower()
            base_score = payoff_strength_scores.get(payoff_strength_val, 1.0)

            type_key = payoff.payoff_type.value if hasattr(payoff.payoff_type, "value") else str(payoff.payoff_type)
            type_weight = payoff_type_weights.get(type_key, 1.0)
            if type_key in preferred:
                type_weight *= 1.3
            else:
                type_weight *= 0.7

            payoff_score += base_score * type_weight

        payoff_score_normalized = min(10.0, payoff_score * (10.0 / float(payoff_cap)))

        # ── 3. Information pacing score (0-2 → 0-10) ──
        pacing_score_normalized = self.information_pacing_score * 5.0

        # ── 4. Main plot depth score (0-10) ──
        main_plot_depth_scores = {
            "deep": 10.0,
            "moderate": 6.0,
            "surface": 3.0,
            "stalled": 0.0,
        }
        main_plot_score = main_plot_depth_scores.get(self.main_plot_depth, 5.0)
        if self.consecutive_main_plot_stall >= 3:
            main_plot_score -= min(3.0, self.consecutive_main_plot_stall * 1.0)

        # ── 5. Tension match score (0-2 → 0-10) ──
        tension_score_normalized = self.tension_match_score * 5.0

        # ── Weighted sum ──
        component_values = {
            "hook_strength": hook_score_normalized,
            "payoff_density": payoff_score_normalized,
            "information_pacing": pacing_score_normalized,
            "main_plot_depth": main_plot_score,
            "tension_match": tension_score_normalized,
        }
        component_weights = {
            "hook_strength": weights.get("hook_strength", 0.25),
            "payoff_density": weights.get("payoff_density", 0.20),
            "information_pacing": weights.get("information_pacing", 0.20),
            "main_plot_depth": weights.get("main_plot_depth", 0.15),
            "tension_match": weights.get("tension_match", 0.20),
        }
        component_contributions = {
            key: component_values[key] * component_weights[key] for key in component_values
        }
        score = sum(component_contributions.values())
        base_score = score
        adjustments: list[dict[str, Any]] = []

        # ── Multiplicative calibration (Phase 3) ──
        # Bonuses and penalties are now multiplicative ratios instead of additive.
        # This prevents score inflation when multiple bonuses stack.
        if self.prev_hook_fulfilled:
            score *= 1.03  # +3% for fulfilling previous hook promise
            adjustments.append(
                {
                    "kind": "multiplier",
                    "reason": "prev_hook_fulfilled",
                    "label": "上章钩子已回应",
                    "factor": 1.03,
                }
            )
        else:
            score /= 1.03  # -3% for broken hook promise
            adjustments.append(
                {
                    "kind": "multiplier",
                    "reason": "prev_hook_unfulfilled",
                    "label": "上章钩子未回应",
                    "factor": round(1 / 1.03, 4),
                }
            )

        if self.outline_hook_match:
            match_type = self.outline_hook_match.get("match_type", "different")
            if match_type == "exact":
                score *= 1.05  # +5% for exact outline hook match
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_hook_exact",
                        "label": "大纲钩子完全匹配",
                        "factor": 1.05,
                    }
                )
            elif match_type == "partial":
                score *= 1.02  # +2% for partial match
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_hook_partial",
                        "label": "大纲钩子部分匹配",
                        "factor": 1.02,
                    }
                )
            elif match_type == "different":
                score /= 1.05  # -5% for mismatched hook
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_hook_different",
                        "label": "大纲钩子未匹配",
                        "factor": round(1 / 1.05, 4),
                    }
                )

        if self.outline_payoff_coverage:
            coverage_ratio = self.outline_payoff_coverage.get("coverage_ratio", 0.0)
            if coverage_ratio >= 0.8:
                score *= 1.04  # +4% for high payoff coverage
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_payoff_high_coverage",
                        "label": "大纲微兑现覆盖充分",
                        "factor": 1.04,
                    }
                )
            elif coverage_ratio >= 0.5:
                score *= 1.01  # +1% for moderate coverage
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_payoff_moderate_coverage",
                        "label": "大纲微兑现部分覆盖",
                        "factor": 1.01,
                    }
                )
            elif coverage_ratio < 0.3:
                score /= 1.04  # -4% for poor coverage
                adjustments.append(
                    {
                        "kind": "multiplier",
                        "reason": "outline_payoff_low_coverage",
                        "label": "大纲微兑现覆盖不足",
                        "factor": round(1 / 1.04, 4),
                    }
                )

        # ── Additive penalties (structural issues, not calibration) ──
        if self.is_transition and hook_type == "none":
            score -= transition_penalty
            adjustments.append(
                {
                    "kind": "penalty",
                    "reason": "transition_without_hook",
                    "label": "过渡章缺少章尾钩子",
                    "points": -round(transition_penalty, 3),
                }
            )

        payoff_deficit = max(0, int(min_payoffs) - len(self.micro_payoffs))
        if payoff_deficit:
            penalty = min(1.5, payoff_deficit * 0.5)
            score -= penalty
            adjustments.append(
                {
                    "kind": "penalty",
                    "reason": "payoff_deficit",
                    "label": f"微兑现不足（{len(self.micro_payoffs)}/{int(min_payoffs)}）",
                    "points": -round(penalty, 3),
                    "deficit": payoff_deficit,
                }
            )

        if self.revelation_over_budget:
            score -= 0.5
            adjustments.append(
                {
                    "kind": "penalty",
                    "reason": "revelation_over_budget",
                    "label": "重大揭示超预算",
                    "points": -0.5,
                }
            )

        self.overall_score = round(max(0.0, min(10.0, score)), 1)
        self.score_breakdown = {
            "schema_version": 1,
            "weights": {key: round(value, 4) for key, value in component_weights.items()},
            "components": {
                "hook_strength": {
                    "label": hook_strength,
                    "hook_type": hook_type,
                    "raw_score": round(raw_hook_score, 3),
                    "normalized_score": round(hook_score_normalized, 3),
                    "weight": round(component_weights["hook_strength"], 4),
                    "weighted_score": round(component_contributions["hook_strength"], 3),
                },
                "payoff_density": {
                    "label": f"{len(self.micro_payoffs)}/{payoff_cap}",
                    "payoff_count": len(self.micro_payoffs),
                    "payoff_cap": payoff_cap,
                    "raw_score": round(payoff_score, 3),
                    "normalized_score": round(payoff_score_normalized, 3),
                    "weight": round(component_weights["payoff_density"], 4),
                    "weighted_score": round(component_contributions["payoff_density"], 3),
                },
                "information_pacing": {
                    "label": self.information_pacing,
                    "raw_score": round(self.information_pacing_score, 3),
                    "max_score": 2.0,
                    "normalized_score": round(pacing_score_normalized, 3),
                    "weight": round(component_weights["information_pacing"], 4),
                    "weighted_score": round(component_contributions["information_pacing"], 3),
                },
                "main_plot_depth": {
                    "label": self.main_plot_depth,
                    "normalized_score": round(main_plot_score, 3),
                    "weight": round(component_weights["main_plot_depth"], 4),
                    "weighted_score": round(component_contributions["main_plot_depth"], 3),
                },
                "tension_match": {
                    "label": self.tension_match,
                    "raw_score": round(self.tension_match_score, 3),
                    "max_score": 2.0,
                    "normalized_score": round(tension_score_normalized, 3),
                    "weight": round(component_weights["tension_match"], 4),
                    "weighted_score": round(component_contributions["tension_match"], 3),
                },
            },
            "base_score": round(base_score, 3),
            "adjustments": adjustments,
            "final_score": self.overall_score,
        }

        suggestions: list[str] = []
        if hook_type == "none":
            suggestions.append("章尾缺少钩子，建议添加悬念/危机/情绪型钩子")
        elif hook_strength == "weak":
            suggestions.append("章尾钩子力度偏弱，考虑提升为中/强级")
        if len(self.micro_payoffs) < min_payoffs:
            suggestions.append(
                f"微兑现不足（{len(self.micro_payoffs)}/{min_payoffs}），"
                f"建议补充信息/关系/能力类兑现"
            )
        if not self.prev_hook_fulfilled:
            suggestions.append("上章钩子承诺未在本章回应，可能导致读者失望")

        if self.outline_hook_match:
            match_type = self.outline_hook_match.get("match_type", "different")
            if match_type == "different":
                reason = self.outline_hook_match.get("reason", "")
                suggestions.append(
                    f"大纲钩子未达成（{reason}），建议在下一章调整钩子设置以匹配大纲规划"
                )

        if self.outline_payoff_coverage:
            coverage_ratio = self.outline_payoff_coverage.get("coverage_ratio", 0.0)
            missing = self.outline_payoff_coverage.get("missing", [])
            if coverage_ratio < 0.5 and missing:
                missing_types = ", ".join(missing[:3])
                suggestions.append(
                    f"大纲微兑现覆盖率偏低（{coverage_ratio:.0%}），缺失类型：{missing_types}"
                )

        self.suggestions = suggestions


class ReadingPowerHint(VersionedSchema):
    """Lightweight reading power hints for Draft/Edit stages.

    A minimal subset of ReadingPowerReport, designed to pass contextual
    reading power information to downstream stages without the overhead
    of full scoring and suggestions.
    """

    prev_hook_type: str | None = Field(
        default=None,
        description="上一章钩子类型",
    )
    prev_hook_description: str | None = Field(
        default=None,
        description="上一章钩子描述",
    )
    suggested_hook_type: str | None = Field(
        default=None,
        description="建议的钩子类型",
    )
    inherited_suspense: list[str] | None = Field(
        default=None,
        description="继承的悬念列表",
    )
    payoff_deficit_warning: str | None = Field(
        default=None,
        description="微兑现不足预警",
    )


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
ReadingPowerReport.model_rebuild()
