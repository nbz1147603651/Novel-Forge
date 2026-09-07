"""短剧（vertical short drama）domain schemas.

Commercial short-drama production has hard, verifiable standards; they are
encoded here as validators instead of prose guidance:

- 每集 3-5 场、全集 12 镜（±2 容差）、18-25 句对白；
- 付费卡点 = 5 种固定套路 + 位置规划；
- 爽点矩阵 + 四阶段情绪波形驱动节奏；
- 反派体系分层映射 character_bible。

Author: novel-forge
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PaywallStrategy(str, Enum):
    """The five canonical paywall routines of commercial short drama."""

    CLIFFHANGER_REVERSAL = "cliffhanger_reversal"  # 悬念反转卡点
    IDENTITY_REVEAL = "identity_reveal"  # 身份揭晓卡点
    EMOTIONAL_BETRAYAL = "emotional_betrayal"  # 情感背叛卡点
    CRISIS_UNRESOLVED = "crisis_unresolved"  # 危机未解卡点
    CONFLICT_THEN_SWEET = "conflict_then_sweet"  # 冲突后甜卡点


class PaywallBeat(BaseModel):
    """One planned paywall checkpoint inside an episode."""

    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    position: str = Field(default="end", pattern=r"^(start|middle|end)$")
    strategy: PaywallStrategy
    description: str = ""


class ThrillPoint(BaseModel):
    """One thrill point (爽点) in the series thrill matrix."""

    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    kind: str
    intensity: int = Field(ge=1, le=10)
    description: str = ""


class WaveformStage(BaseModel):
    """One stage of the four-stage emotional waveform."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    episode_start: int = Field(ge=1)
    episode_end: int = Field(ge=1)
    intensity_target: int = Field(default=5, ge=1, le=10)
    note: str = ""


class AntagonistLayer(BaseModel):
    """One tier of the antagonist system mapped onto character_bible."""

    model_config = ConfigDict(extra="forbid")

    name: str
    tier: str = Field(default="surface", pattern=r"^(surface|mid|boss)$")
    function: str = ""
    character_ref: str = ""


class DramaSeriesPlan(BaseModel):
    """Series-level plan: acts, paywall placement, thrill matrix, waveform."""

    model_config = ConfigDict(extra="forbid")

    title: str
    genre: str = ""
    logline: str = ""
    total_episodes: int = Field(ge=1, le=200)
    episode_duration_s: int = Field(default=120, ge=30, le=900)
    three_acts: list[str] = Field(default_factory=list)
    paywall_beats: list[PaywallBeat] = Field(default_factory=list)
    thrill_matrix: list[ThrillPoint] = Field(default_factory=list)
    waveform_stages: list[WaveformStage] = Field(default_factory=list)
    antagonist_system: list[AntagonistLayer] = Field(default_factory=list)


class DramaSceneOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    summary: str
    location: str = ""
    emotional_beat: str = ""


class EpisodeOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    title: str = ""
    summary: str = ""
    waveform_stage: str = ""
    paywall_marker: str = ""
    scenes: list[DramaSceneOutline] = Field(default_factory=list)


class DramaLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
    line: str
    action: str = ""


class DramaScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    heading: str = ""
    action: str = ""
    shot_count: int = Field(default=3, ge=1, le=6)
    lines: list[DramaLine] = Field(default_factory=list)


class EpisodeScreenplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    title: str = ""
    scenes: list[DramaScene] = Field(default_factory=list)

    @property
    def total_shots(self) -> int:
        return sum(scene.shot_count for scene in self.scenes)

    @property
    def dialogue_count(self) -> int:
        return sum(
            1 for scene in self.scenes for line in scene.lines if line.line.strip()
        )


# Hard commercial standards (deterministic, testable).
SCENE_COUNT_RANGE = (3, 5)
SHOT_TARGET = 12
SHOT_TOLERANCE = 2
DIALOGUE_RANGE = (18, 25)


def validate_episode_screenplay(screenplay: EpisodeScreenplay) -> tuple[bool, list[str]]:
    """Validate one episode against the hard short-drama standards.

    Returns ``(passed, issues)``; issues are stable, machine-readable codes.
    """
    issues: list[str] = []
    scene_min, scene_max = SCENE_COUNT_RANGE
    if not scene_min <= len(screenplay.scenes) <= scene_max:
        issues.append(f"scene_count_out_of_range:{len(screenplay.scenes)}")
    shots = screenplay.total_shots
    if abs(shots - SHOT_TARGET) > SHOT_TOLERANCE:
        issues.append(f"shot_count_off_target:{shots}")
    dialogue = screenplay.dialogue_count
    low, high = DIALOGUE_RANGE
    if not low <= dialogue <= high:
        issues.append(f"dialogue_count_out_of_range:{dialogue}")
    seen: set[int] = set()
    for scene in screenplay.scenes:
        if scene.scene_number in seen:
            issues.append(f"duplicate_scene_number:{scene.scene_number}")
        seen.add(scene.scene_number)
        if not scene.lines and scene.action.strip() == "":
            issues.append(f"empty_scene:{scene.scene_number}")
    return (not issues), issues


__all__ = [
    "DIALOGUE_RANGE",
    "SCENE_COUNT_RANGE",
    "SHOT_TARGET",
    "SHOT_TOLERANCE",
    "AntagonistLayer",
    "DramaLine",
    "DramaScene",
    "DramaSceneOutline",
    "DramaSeriesPlan",
    "EpisodeOutline",
    "EpisodeScreenplay",
    "PaywallBeat",
    "PaywallStrategy",
    "ThrillPoint",
    "WaveformStage",
    "validate_episode_screenplay",
]
