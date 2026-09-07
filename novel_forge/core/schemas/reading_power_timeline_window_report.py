"""追读力时间线窗口完整报告 — 窗口三边界与综合评分。

Defines the full Reading Power Timeline Window report, including deviation alerts,
next-chapter constraints, and composite scoring across suspense chains, timeline
execution, and plot progression.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.plot_progression_report import PlotProgressionReport
from novel_forge.core.schemas.suspense_timeline_entry import SuspenseTimelineEntry


class TimelineDeviationAlert(VersionedSchema):
    """时间线偏离预警。"""

    alert_type: str = Field(
        description="预警类型：suspense_delay/hook_monotony/tension_mismatch/strand_dormancy",
    )
    severity: Literal["info", "warning", "critical"] = Field(
        description="严重程度：info/warning/critical",
    )
    planned_value: str | float | None = Field(
        default=None,
        description="计划值",
    )
    actual_value: str | float | None = Field(
        default=None,
        description="实际值",
    )
    deviation_summary: str = Field(
        default="",
        description="偏离摘要（一句话描述）",
    )
    correction_suggestion: str = Field(
        default="",
        description="修正建议",
    )


class NextChapterConstraints(VersionedSchema):
    """下一章创作约束建议。"""

    suspense_ids_to_resolve: list[str] = Field(
        default_factory=list,
        description="需要在下一章兑现的悬念ID列表",
    )
    expected_hook_type: str | None = Field(
        default=None,
        description="建议的章尾钩子类型",
    )
    tension_adjustment_target: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="张力调整目标值（0-10）",
    )
    subplots_to_activate: list[str] = Field(
        default_factory=list,
        description="建议激活的支线ID列表",
    )
    element_focus_recommendation: list[str] = Field(
        default_factory=list,
        description="建议重点关注的叙事要素ID",
    )
    strand_distribution_hint: dict[str, float] = Field(
        default_factory=dict,
        description="情节线分布建议 {strand_type: target_ratio}",
    )


class ReadingPowerTimelineWindowReport(VersionedSchema):
    """追读力时间线窗口完整报告。

    Integrates the three window boundaries:
    - Left: inherited suspense carry-over
    - Center: current chapter execution verification
    - Right: next chapter constraints

    Also aggregates plot progression, element progress summary, and strand balance.
    """

    # ── 窗口元数据 ────────────────────────────────────────────────────────
    window_start_chapter: int = Field(
        ge=1,
        description="窗口起始章节号",
    )
    window_center_chapter: int = Field(
        ge=1,
        description="窗口中心章节号（当前执行验证章节）",
    )
    window_end_chapter: int = Field(
        ge=1,
        description="窗口结束章节号",
    )

    # ── 左边界：继承悬念 ──────────────────────────────────────────────────
    inherited_suspenses: list[SuspenseTimelineEntry] = Field(
        default_factory=list,
        description="从窗口左侧继承的未兑现悬念列表",
    )

    # ── 中心：执行验证 ────────────────────────────────────────────────────
    chapter_execution_verified: bool = Field(
        default=False,
        description="中心章节执行是否已验证",
    )
    suspense_resolutions_in_window: list[SuspenseTimelineEntry] = Field(
        default_factory=list,
        description="窗口内已兑现的悬念列表",
    )
    suspense_misses_in_window: list[SuspenseTimelineEntry] = Field(
        default_factory=list,
        description="窗口内错过兑现窗口的悬念列表",
    )

    # ── 右边界：下一章约束 ────────────────────────────────────────────────
    next_chapter_constraints: NextChapterConstraints | None = Field(
        default=None,
        description="下一章创作约束建议",
    )

    # ── 偏离预警 ──────────────────────────────────────────────────────────
    deviation_alerts: list[TimelineDeviationAlert] = Field(
        default_factory=list,
        description="时间线偏离预警列表",
    )

    # ── 叙事推进 ──────────────────────────────────────────────────────────
    plot_progression: PlotProgressionReport | None = Field(
        default=None,
        description="叙事推进报告",
    )

    # ── 要素执行摘要 ──────────────────────────────────────────────────────
    element_progress_summary: dict[str, str] = Field(
        default_factory=dict,
        description="要素执行摘要 {element_id: hit/weak/miss}",
    )

    # ── 情节线平衡 ────────────────────────────────────────────────────────
    strand_balance_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="情节线平衡评分（0-10）",
    )

    # ── 综合评分 ──────────────────────────────────────────────────────────
    timeline_execution_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="时间线执行评分（0-10）",
    )
    suspense_chain_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="悬念链评分（0-10）",
    )
    composite_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="追读力窗口综合评分（0-10）",
    )

    def compute_timeline_execution_score(self) -> float:
        """计算时间线执行评分（0-10）。

        基于窗口内悬念兑现率、偏离预警数量、章节执行验证状态。

        Returns:
            时间线执行评分。
        """
        total_suspenses = len(self.suspense_resolutions_in_window) + len(
            self.suspense_misses_in_window
        )
        if total_suspenses == 0:
            base_score = 5.0
        else:
            resolution_rate = len(self.suspense_resolutions_in_window) / total_suspenses
            base_score = resolution_rate * 10.0

        alert_penalty = len(self.deviation_alerts) * 0.5
        verification_bonus = 1.0 if self.chapter_execution_verified else 0.0

        return round(max(0.0, min(10.0, base_score - alert_penalty + verification_bonus)), 1)

    def compute_suspense_chain_score(self) -> float:
        """计算悬念链评分（0-10）。

        基于继承悬念数量、兑现及时性、紧迫级别分布。

        Returns:
            悬念链评分。
        """
        if not self.inherited_suspenses:
            return 10.0

        total = len(self.inherited_suspenses)
        critical_count = sum(1 for s in self.inherited_suspenses if s.urgency_level == "critical")
        high_count = sum(1 for s in self.inherited_suspenses if s.urgency_level == "high")

        urgency_penalty = (critical_count * 2.0 + high_count * 1.0) / total * 5.0

        avg_deviation = (
            sum(abs(s.resolution_timing_deviation) for s in self.inherited_suspenses) / total
        )
        deviation_penalty = min(avg_deviation * 0.5, 3.0)

        return round(max(0.0, min(10.0, 10.0 - urgency_penalty - deviation_penalty)), 1)

    def compute_composite_score(
        self,
        timeline_weight: float = 0.35,
        suspense_weight: float = 0.30,
        progression_weight: float = 0.20,
        strand_weight: float = 0.15,
    ) -> float:
        """计算追读力窗口综合评分（0-10）。

        Args:
            timeline_weight: 时间线执行权重。
            suspense_weight: 悬念链权重。
            progression_weight: 叙事推进权重。
            strand_weight: 情节线平衡权重。

        Returns:
            综合评分。
        """
        self.timeline_execution_score = self.compute_timeline_execution_score()
        self.suspense_chain_score = self.compute_suspense_chain_score()

        progression_score = (
            self.plot_progression.compute_progression_score() if self.plot_progression else 5.0
        )

        score = (
            self.timeline_execution_score * timeline_weight
            + self.suspense_chain_score * suspense_weight
            + progression_score * progression_weight
            + self.strand_balance_score * strand_weight
        )

        self.composite_score = round(max(0.0, min(10.0, score)), 1)
        return self.composite_score


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
ReadingPowerTimelineWindowReport.model_rebuild()
