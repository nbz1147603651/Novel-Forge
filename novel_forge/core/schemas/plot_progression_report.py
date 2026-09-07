"""叙事推进报告 — 追踪主线、支线与情节线的活跃状态。

Defines schemas for tracking plot progression across chapters, including
strand activity, main plot coverage, subplot engagement, and composite
progression scoring.
"""

from __future__ import annotations

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema


class StrandActivityRecord(VersionedSchema):
    """单条情节线（strand）在窗口期内的活动记录。"""

    strand_type: str = Field(
        description="情节线类型（如 action/relationship/mystery/worldbuilding）",
    )
    planned_intensity: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description="计划强度（0-10）",
    )
    actual_intensity: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="实际强度（0-10）",
    )
    dormant_since: int = Field(
        default=0,
        ge=0,
        description="自多少章前进入休眠状态（0=当前活跃）",
    )


class MainPlotProgressionRecord(VersionedSchema):
    """主线推进记录。"""

    plot_point: str = Field(
        description="情节点ID或描述",
    )
    covered: bool = Field(
        default=False,
        description="该情节点是否在本章/窗口内被覆盖",
    )
    coverage_strength: str = Field(
        default="none",
        description="覆盖力度：strong/medium/weak/none",
    )
    related_suspense_ids: list[str] = Field(
        default_factory=list,
        description="关联的悬念ID列表",
    )


class SubplotProgressionRecord(VersionedSchema):
    """支线推进记录。"""

    subplot_id: str = Field(
        description="支线唯一标识符",
    )
    subplot_name: str = Field(
        default="",
        description="支线名称",
    )
    active_this_chapter: bool = Field(
        default=False,
        description="本章是否活跃",
    )
    dormant_chapters: int = Field(
        default=0,
        ge=0,
        description="连续休眠章数",
    )


class PlotProgressionReport(VersionedSchema):
    """叙事推进综合报告。

    Aggregates main plot, subplot, strand activity, narrative phase,
    and turning point progression data for the current window.
    """

    # ── 情节线活动 ────────────────────────────────────────────────────────
    strand_activities: list[StrandActivityRecord] = Field(
        default_factory=list,
        description="各情节线的活动记录",
    )

    # ── 主线推进 ──────────────────────────────────────────────────────────
    main_plot_progression: list[MainPlotProgressionRecord] = Field(
        default_factory=list,
        description="主线情节点推进记录",
    )

    # ── 支线推进 ──────────────────────────────────────────────────────────
    subplot_progression: list[SubplotProgressionRecord] = Field(
        default_factory=list,
        description="支线推进记录",
    )

    # ── 叙事阶段 ──────────────────────────────────────────────────────────
    current_narrative_phase: str = Field(
        default="development",
        description="当前叙事阶段：setup/rising_action/climax/falling_action/resolution",
    )
    phase_progress: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="当前阶段完成度（0.0-1.0）",
    )

    # ── 转折点 ────────────────────────────────────────────────────────────
    turning_points_covered: int = Field(
        default=0,
        ge=0,
        description="窗口内覆盖的转折点数量",
    )
    turning_points_expected: int = Field(
        default=0,
        ge=0,
        description="窗口内预期转折点数量",
    )

    # ── 休眠预警 ──────────────────────────────────────────────────────────
    dormant_strand_alerts: list[str] = Field(
        default_factory=list,
        description="休眠情节线预警列表",
    )

    def compute_progression_score(self) -> float:
        """计算叙事推进综合评分（0-10）。

        评分维度：
        - 主线覆盖度（40%）
        - 支线活跃度（25%）
        - 情节线平衡度（20%）
        - 转折点命中率（15%）

        Returns:
            推进综合评分，范围 0-10。
        """
        if not self.main_plot_progression and not self.subplot_progression:
            return 0.0

        main_plot_score = 0.0
        if self.main_plot_progression:
            covered = sum(1 for mp in self.main_plot_progression if mp.covered)
            total = len(self.main_plot_progression)
            main_plot_score = (covered / total) * 10.0 if total > 0 else 0.0

        subplot_score = 0.0
        if self.subplot_progression:
            active = sum(1 for sp in self.subplot_progression if sp.active_this_chapter)
            total = len(self.subplot_progression)
            subplot_score = (active / total) * 10.0 if total > 0 else 0.0

        strand_score = 0.0
        if self.strand_activities:
            intensities = [sa.actual_intensity for sa in self.strand_activities]
            avg_intensity = sum(intensities) / len(intensities)
            variance = sum((i - avg_intensity) ** 2 for i in intensities) / len(intensities)
            balance = max(0.0, 10.0 - variance)
            strand_score = balance

        turning_point_score = 0.0
        if self.turning_points_expected > 0:
            turning_point_score = (
                min(self.turning_points_covered, self.turning_points_expected)
                / self.turning_points_expected
            ) * 10.0

        return round(
            main_plot_score * 0.40
            + subplot_score * 0.25
            + strand_score * 0.20
            + turning_point_score * 0.15,
            1,
        )
