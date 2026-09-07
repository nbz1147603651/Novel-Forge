"""追读力移动窗口系统配置 — 用户可配置的窗口参数与评分权重。

Defines the configurable parameters for the Reading Power Moving Window system,
including window boundaries, suspense tracking, hook alternation, tension matching,
scoring weights, system toggles, and alert thresholds.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from novel_forge.core.schemas.base import VersionedSchema


class ReadingPowerWindowConfig(VersionedSchema):
    """追读力移动窗口系统的可配置参数。

    Controls the behavior of the sliding window that evaluates reading power
    across multiple chapters, including suspense tracking, hook alternation,
    tension matching, and composite scoring weights.
    """

    # ── 窗口边界配置 ──────────────────────────────────────────────────────
    window_size: int = Field(
        default=5,
        ge=3,
        le=10,
        description="窗口大小（覆盖章节数），最小3，最大10",
    )
    window_left_offset: int = Field(
        default=0,
        ge=-5,
        le=0,
        description="窗口左偏移量，负值表示向左扩展（继承更多历史章节）",
    )
    window_right_offset: int = Field(
        default=0,
        ge=0,
        le=5,
        description="窗口右偏移量，正值表示向右扩展（预览更多未来章节）",
    )

    # ── 悬念追踪配置 ──────────────────────────────────────────────────────
    suspense_delay_threshold: int = Field(
        default=3,
        ge=1,
        le=7,
        description="悬念延迟阈值（章数），超过此值未兑现则触发预警",
    )
    force_resolve_threshold: int = Field(
        default=5,
        ge=3,
        le=10,
        description="强制兑现阈值（章数），超过此值必须在下章兑现悬念",
    )

    # ── 钩子交替配置 ──────────────────────────────────────────────────────
    hook_alternation_threshold: int = Field(
        default=2,
        ge=1,
        le=4,
        description="钩子交替阈值，连续相同类型钩子超过此值触发交替建议",
    )
    max_consecutive_same_hook: int = Field(
        default=3,
        ge=2,
        le=5,
        description="最大连续同类型钩子数，超过此值视为钩子单调",
    )

    # ── 张力匹配配置 ──────────────────────────────────────────────────────
    tension_deviation_tolerance: float = Field(
        default=1.5,
        ge=0.5,
        le=3.0,
        description="张力偏差容忍度，实际张力与预期张力差值超过此值触发预警",
    )
    tension_recovery_factor: float = Field(
        default=0.3,
        ge=0.1,
        le=1.0,
        description="张力恢复因子，用于计算张力偏离后的恢复目标",
    )

    # ── 评分权重配置 ──────────────────────────────────────────────────────
    hook_strength_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "hook_strength": 0.25,
            "payoff_density": 0.20,
            "suspense_timing": 0.20,
            "hook_alternation": 0.15,
            "tension_match": 0.20,
        },
        description="评分权重配置，各权重之和应为1.0",
    )
    payoff_cap: int = Field(
        default=3,
        ge=1,
        le=5,
        description="微兑现计分上限，超过此数量的微兑现不再额外加分",
    )
    suspense_timing_weight: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="悬念时机权重，在综合评分中的占比",
    )

    # ── 系统开关 ──────────────────────────────────────────────────────────
    enabled: bool = Field(
        default=True,
        description="是否启用追读力移动窗口系统",
    )
    enable_force_resolve: bool = Field(
        default=True,
        description="是否启用强制兑现检查",
    )
    enable_hook_alternation_check: bool = Field(
        default=True,
        description="是否启用钩子交替检查",
    )
    enable_tension_recovery: bool = Field(
        default=True,
        description="是否启用张力恢复计算",
    )

    # ── 预警级别 ──────────────────────────────────────────────────────────
    critical_score_threshold: float = Field(
        default=3.0,
        ge=0.0,
        le=10.0,
        description="临界分数阈值，综合评分低于此值触发严重预警",
    )
    warning_score_threshold: float = Field(
        default=5.0,
        ge=0.0,
        le=10.0,
        description="警告分数阈值，综合评分低于此值触发一般预警",
    )

    @model_validator(mode="after")
    def validate_weights_sum(self) -> "ReadingPowerWindowConfig":
        """验证评分权重之和为1.0（允许±0.01浮点误差）。"""
        weights = self.hook_strength_weights
        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"评分权重之和必须为1.0，当前为{total:.4f}。权重详情: {weights}")
        return self
