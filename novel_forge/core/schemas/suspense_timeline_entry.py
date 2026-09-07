"""悬念时间表条目 — 追踪单个悬念从设置到兑现的全生命周期。

Defines the SuspenseTimelineEntry schema for tracking individual suspense items
across chapters, including setup timing, resolution windows, execution status,
and urgency levels.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.reading_power import HookType


class SuspenseTimelineEntry(VersionedSchema):
    """悬念时间表中的单个条目。

    Tracks a single suspense item from its setup chapter through its planned
    and actual resolution, including window position tracking and plot affiliation.
    """

    # ── 基本标识 ──────────────────────────────────────────────────────────
    suspense_id: str = Field(
        description="悬念唯一标识符",
    )
    suspense_type: HookType = Field(
        description="悬念类型，对应钩子分类",
    )
    suspense_description: str = Field(
        default="",
        description="悬念的一句话描述",
    )

    # ── 设置时机 ──────────────────────────────────────────────────────────
    setup_chapter: int = Field(
        ge=1,
        description="悬念设置的章节号",
    )
    setup_strength: str = Field(
        default="medium",
        description="悬念设置强度：strong/medium/weak",
    )
    setup_phase: str = Field(
        default="middle",
        description="悬念在章中的设置位置：opening/middle/closing",
    )
    setup_tension_expected: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description="设置时预期的张力水平（0-10）",
    )

    # ── 兑现时机 ──────────────────────────────────────────────────────────
    planned_resolution_chapter: int | None = Field(
        default=None,
        ge=1,
        description="计划兑现的章节号",
    )
    resolution_window_start: int | None = Field(
        default=None,
        ge=1,
        description="兑现窗口起始章节",
    )
    resolution_window_end: int | None = Field(
        default=None,
        ge=1,
        description="兑现窗口结束章节",
    )

    # ── 执行状态 ──────────────────────────────────────────────────────────
    setup_executed: bool = Field(
        default=True,
        description="悬念是否已成功设置",
    )
    resolution_executed: bool = Field(
        default=False,
        description="悬念是否已兑现",
    )
    resolution_timing_deviation: int = Field(
        default=0,
        description="兑现时机偏差（正=延迟，负=提前，0=准时）",
    )

    # ── 窗口追踪 ──────────────────────────────────────────────────────────
    current_window_position: int = Field(
        default=0,
        description="当前窗口中的相对位置（0=窗口左边界）",
    )
    urgency_level: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description="紧迫级别：low/medium/high/critical",
    )

    # ── 主线/支线关联 ─────────────────────────────────────────────────────
    related_main_plot_point: str | None = Field(
        default=None,
        description="关联的主线情节点ID",
    )
    related_subplot_id: str | None = Field(
        default=None,
        description="关联的支线ID",
    )
    strand_affinity: dict[str, float] = Field(
        default_factory=dict,
        description="与各情节线（strand）的关联度 {strand_type: affinity_score}",
    )

    # ── 要素关联 ──────────────────────────────────────────────────────────
    related_element_ids: list[str] = Field(
        default_factory=list,
        description="关联的叙事要素ID列表",
    )

    def update_window_position(
        self,
        current_chapter: int,
        suspense_delay_threshold: int,
    ) -> None:
        """更新当前窗口位置和紧迫级别。

        Args:
            current_chapter: 当前章节号。
            suspense_delay_threshold: 悬念延迟阈值，用于计算紧迫级别。
        """
        if self.resolution_window_start is None:
            self.current_window_position = 0
            self.urgency_level = "low"
            return

        self.current_window_position = current_chapter - self.resolution_window_start

        if self.resolution_executed:
            self.urgency_level = "low"
            return

        chapters_remaining = (
            self.resolution_window_end or self.planned_resolution_chapter or current_chapter
        ) - current_chapter

        if chapters_remaining <= 0:
            self.urgency_level = "critical"
        elif chapters_remaining <= suspense_delay_threshold:
            self.urgency_level = "high"
        elif chapters_remaining <= suspense_delay_threshold * 2:
            self.urgency_level = "medium"
        else:
            self.urgency_level = "low"


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
SuspenseTimelineEntry.model_rebuild()
