"""Metric definitions for draft evaluation."""

from __future__ import annotations

from enum import Enum


class MetricDimension(str, Enum):
    """Standard evaluation dimensions (v2: 6 dimensions)."""

    CONSISTENCY = "consistency"    # 设定一致性（战力/地点/时间线）
    CONTINUITY = "continuity"      # 连贯性（场景转换/情节线承接）
    CHARACTER = "character"        # 人物一致性（OOC 检测）
    STYLE = "style"                # 风格匹配度
    ENGAGEMENT = "engagement"      # 吸引力/爽点密度
    PACING = "pacing"              # 节奏感（张弛有度）

    # Legacy alias kept for backward compatibility
    COHERENCE = "coherence"


DEFAULT_PASS_THRESHOLD: float = 6.0
EARLY_STOP_THRESHOLD: float = 8.0
