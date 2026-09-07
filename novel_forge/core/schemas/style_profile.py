"""ProjectStyleProfile — 项目专属风格规范（合并 GenreProfile 和 _styles 框架）。

统一风格配置系统，包含：
- 写作技法规范（modules/rules）— 原 StyleProfile
- 钩子/爽点/节奏参数 — 原 GenreProfile
- 对话占比/节奏模式/情绪风格 — 原 _styles 框架

存储位置：data/{project_id}/style_profile.json
触发时机：初始化流程中，由 LLM 根据 spec/story_bible/character_bible 动态生成
"""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import Field, StringConstraints, field_validator

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.reading_power_window_config import ReadingPowerWindowConfig
from novel_forge.core.utils.type_coerce import stringify_text_value

# ── 大类风格参数映射 ───────────────────────────────────────────────────

DIALOGUE_RATIO_MAP: dict[str, tuple[int, int]] = {
    "high": (50, 70),
    "medium": (30, 45),
    "low": (20, 35),
}

StyleRule = Annotated[str, StringConstraints(max_length=50)]
BannedPhrase = Annotated[str, StringConstraints(max_length=50)]

# ── 写作技法模块 ───────────────────────────────────────────────────────


class StyleModule(VersionedSchema):
    """风格规范中的单个技法模块。"""

    name: str = Field(
        description="模块名称，4-8 字。",
        max_length=12,
    )
    rules: list[StyleRule] = Field(
        default_factory=list,
        description="可执行规则，每条 ≤ 40 字。",
        max_length=5,
    )
    positive_example: str = Field(
        default="",
        description="正例（从实际章节中提取），≤ 50 字。",
        max_length=60,
    )
    negative_example: str = Field(
        default="",
        description="反例（实际出现的问题写法），≤ 50 字。",
        max_length=60,
    )

    @field_validator("rules")
    @classmethod
    def stringify_rules(cls, v: list[str]) -> list[str]:
        return [str(rule) for rule in v]

    @field_validator("positive_example", "negative_example", mode="before")
    @classmethod
    def stringify_example(cls, v: object) -> str:
        return stringify_text_value(v)


# ── 钩子配置 ───────────────────────────────────────────────────────────


class HookConfig(VersionedSchema):
    """钩子（Hook）配置 — 控制章尾钩子的类型偏好和强度。"""

    preferred_types: list[str] = Field(
        default_factory=lambda: ["crisis", "mystery"],
        description="偏好钩子类型：crisis/mystery/emotion/choice/desire",
    )
    strength_baseline: str = Field(
        default="medium",
        description="默认钩子强度要求：strong/medium/weak",
    )
    chapter_end_required: bool = Field(
        default=True,
        description="是否要求每章末尾必须有钩子",
    )


# ── 爽点配置 ───────────────────────────────────────────────────────────


class CoolPointConfig(VersionedSchema):
    """爽点（Cool Point）配置 — 控制爽点模式和密度。"""

    preferred_patterns: list[str] = Field(
        default_factory=list,
        description="偏好爽点模式，如 '装逼打脸'、'越级反杀'、'扮猪吃虎'",
    )
    density_per_chapter: str = Field(
        default="medium",
        description="每章爽点密度：high/medium/low",
    )


# ── 微兑现配置 ─────────────────────────────────────────────────────────


class MicroPayoffConfig(VersionedSchema):
    """微兑现（Micro Payoff）配置 — 控制兑现类型和最低数量。"""

    preferred_types: list[str] = Field(
        default_factory=lambda: ["information", "clue"],
        description=(
            "偏好微兑现类型：information/relationship/ability/resource/recognition/emotion/clue"
        ),
    )
    min_per_chapter: int = Field(
        default=1,
        ge=0,
        description="每章最低微兑现数量",
    )


# ── 情节线配置 ─────────────────────────────────────────────────────────


class StrandConfig(VersionedSchema):
    """情节线（Strand）配置 — 三线节奏阈值。"""

    quest_max_consecutive: int = Field(default=5, ge=1, description="Quest 线最大连续章数")
    fire_max_absent: int = Field(default=10, ge=1, description="Fire 线最大断档章数")
    constellation_max_absent: int = Field(default=15, ge=1, description="世界观线最大断档章数")
    stagnation_threshold: int = Field(
        default=3,
        ge=1,
        description="停滞预警阈值（连续 N 章无实质推进）",
    )


# ── 节奏配置 ───────────────────────────────────────────────────────────


class PacingConfig(VersionedSchema):
    """节奏（Pacing）配置 — 控制章节节奏的整体参数。"""

    min_tension_chapters: int = Field(
        default=2,
        ge=0,
        description="连续低张力章节上限（超过则预警）",
    )
    climax_spacing_chapters: int = Field(
        default=8,
        ge=1,
        description="高潮间最大间隔章数",
    )


# ── 钩子评分配置 ───────────────────────────────────────────────────────


class HookScoreConfig(VersionedSchema):
    """钩子评分权重配置 — 控制追读力评分算法的参数。"""

    hook_score_strong: float = Field(default=4.0, ge=0.0, description="强钩子评分权重")
    hook_score_medium: float = Field(default=2.5, ge=0.0, description="中等钩子评分权重")
    hook_score_weak: float = Field(default=1.0, ge=0.0, description="弱钩子评分权重")
    payoff_cap: int = Field(default=3, ge=0, le=10, description="微兑现计分上限")
    transition_penalty: float = Field(default=1.0, ge=0.0, description="过渡章无钩子惩罚")


# ── 关键词配置 ─────────────────────────────────────────────────────────


class StrandKeywordsConfig(VersionedSchema):
    """情节线关键词配置 — 控制章节内容类型推断的关键词集合。"""

    quest_keywords: list[str] = Field(
        default_factory=list,
        description="Quest线关键词（战斗/任务/探索类）",
    )
    fire_keywords: list[str] = Field(
        default_factory=list,
        description="Fire线关键词（感情/关系类）",
    )
    constellation_keywords: list[str] = Field(
        default_factory=list,
        description="Constellation线关键词（势力/世界观类）",
    )


class TimeKeywordsConfig(VersionedSchema):
    """时间验证关键词配置 — 控制时间跨度检测的关键词集合。"""

    large_gap_keywords: list[str] = Field(
        default_factory=list,
        description="大跨度时间关键词（如三天后、数日后）",
    )
    skip_keywords: list[str] = Field(
        default_factory=list,
        description="短间隔关键词（如次日、翌日）",
    )


# ── 大类风格参数（原 _styles 框架）────────────────────────────────────


class GlobalStyleConfig(VersionedSchema):
    """大类风格参数 — 原 _styles 框架的核心约束。"""

    dialogue_ratio: str = Field(
        default="medium",
        description="对话占比：high(50-70%)/medium(30-45%)/low(20-35%)",
    )
    pace_mode: str = Field(
        default="moderate",
        description="节奏模式：fast/moderate/slow",
    )
    emotional_style: str = Field(
        default="balanced",
        description="情绪风格：direct(直给)/balanced(平衡)/subtle(含蓄)",
    )
    environment_ratio: str = Field(
        default="medium",
        description="环境描写占比：high(25-35%)/medium(15-25%)/low(≤15%)",
    )
    info_density: str = Field(
        default="medium",
        description="信息密度：high(每500字)/medium(每800字)/low(每1000字)",
    )
    banned_phrases: list[BannedPhrase] = Field(
        default_factory=list,
        description="项目级禁用短语列表，由 ProfileStyleStep 从 StoryBible.banned_intent_rules 推导",
        max_length=30,
    )

    @field_validator("banned_phrases")
    @classmethod
    def stringify_banned_phrases(cls, v: list[str]) -> list[str]:
        return [str(phrase) for phrase in v]

    @field_validator("dialogue_ratio", "environment_ratio", "info_density", mode="before")
    @classmethod
    def normalize_level(cls, v: object) -> str:
        value = str(v or "").lower().strip()
        return value if value in {"high", "medium", "low"} else "medium"

    @field_validator("pace_mode", mode="before")
    @classmethod
    def normalize_pace_mode(cls, v: object) -> str:
        value = str(v or "").lower().strip()
        return value if value in {"fast", "moderate", "slow"} else "moderate"

    @field_validator("emotional_style", mode="before")
    @classmethod
    def normalize_emotional_style(cls, v: object) -> str:
        value = str(v or "").lower().strip()
        return value if value in {"direct", "balanced", "subtle"} else "balanced"


# ── 项目风格规范（合并版）──────────────────────────────────────────────


class ProjectStyleProfile(VersionedSchema):
    """项目专属风格规范 — 统一风格配置系统。

    包含：
    - modules: 写作技法规范（原 StyleProfile）
    - hook_config/strand_config/pacing_config: 题材参数（原 GenreProfile）
    - global_style: 大类风格约束（原 _styles 框架）
    """

    _correct_fields: ClassVar[set[str]] = {
        "modules",
        "source_elements",
        "summary",
        "hook_config",
        "cool_point_config",
        "micro_payoff_config",
        "strand_config",
        "pacing_config",
        "hook_score_config",
        "strand_keywords_config",
        "time_keywords_config",
        "global_style",
        "reading_power_window_config",
        "overrides",
    }

    # ── 写作技法规范 ─────────────────────────────────────────────────────
    modules: list[StyleModule] = Field(
        default_factory=list,
        description="最多 5 个风格规范模块。",
        max_length=5,
    )
    source_elements: list[str] = Field(
        default_factory=list,
        description="规范所基于的项目要素列表，如 ['story_bible', 'character_bible']。",
    )
    summary: str = Field(
        default="",
        description="一句话概括本作品的核心风格特征，≤ 60 字。",
        max_length=80,
    )

    # ── 钩子/爽点/节奏参数 ───────────────────────────────────────────────
    hook_config: HookConfig = Field(default_factory=HookConfig)
    cool_point_config: CoolPointConfig = Field(default_factory=CoolPointConfig)
    micro_payoff_config: MicroPayoffConfig = Field(default_factory=MicroPayoffConfig)
    strand_config: StrandConfig = Field(default_factory=StrandConfig)
    pacing_config: PacingConfig = Field(default_factory=PacingConfig)
    hook_score_config: HookScoreConfig = Field(default_factory=HookScoreConfig)
    strand_keywords_config: StrandKeywordsConfig = Field(default_factory=StrandKeywordsConfig)
    time_keywords_config: TimeKeywordsConfig = Field(default_factory=TimeKeywordsConfig)

    # ── 大类风格参数 ─────────────────────────────────────────────────────
    global_style: GlobalStyleConfig = Field(default_factory=GlobalStyleConfig)

    # ── 追读力移动窗口配置 ───────────────────────────────────────────────
    reading_power_window_config: ReadingPowerWindowConfig | None = Field(
        default_factory=ReadingPowerWindowConfig,
        description="追读力移动窗口系统的可配置参数",
    )

    # ── 用户覆盖 ─────────────────────────────────────────────────────────
    overrides: dict[str, object] = Field(
        default_factory=dict,
        description="用户自定义覆盖项（key=参数路径, value=覆盖值）",
    )

    @field_validator("modules")
    @classmethod
    def limit_modules(cls, v: list[StyleModule]) -> list[StyleModule]:
        if len(v) > 5:
            v = v[:5]
        return v


# ── 枚举值校验工具函数 ───────────────────────────────────────────────────


def validate_hook_type(value: str) -> str:
    """校验钩子类型枚举值，无效值回退到默认值。

    用于 ProfileStyleStep 解析逻辑，确保 LLM 输出的钩子类型值安全。

    Args:
        value: 待校验的钩子类型字符串（支持大小写不敏感）

    Returns:
        有效枚举值或默认值 "none"

    Valid values: crisis, mystery, emotion, choice, desire, none
    """
    valid_values = {"crisis", "mystery", "emotion", "choice", "desire", "none"}
    normalized = value.lower().strip()
    return normalized if normalized in valid_values else "none"


def validate_hook_strength(value: str) -> str:
    """校验钩子强度枚举值，无效值回退到默认值。

    用于 ProfileStyleStep 解析逻辑，确保 LLM 输出的钩子强度值安全。

    Args:
        value: 待校验的钩子强度字符串（支持大小写不敏感）

    Returns:
        有效枚举值或默认值 "medium"

    Valid values: strong, medium, weak
    """
    valid_values = {"strong", "medium", "weak"}
    normalized = value.lower().strip()
    return normalized if normalized in valid_values else "medium"


def validate_pacing_mode(value: str) -> str:
    """校验节奏模式枚举值，无效值回退到默认值。

    用于 ProfileStyleStep 解析逻辑，确保 LLM 输出的节奏模式值安全。

    Args:
        value: 待校验的节奏模式字符串（支持大小写不敏感）

    Returns:
        有效枚举值或默认值 "moderate"

    Valid values: fast, moderate, slow
    """
    valid_values = {"fast", "moderate", "slow"}
    normalized = value.lower().strip()
    return normalized if normalized in valid_values else "moderate"


# ---------------------------------------------------------------------------
# Resolve forward references for cross-module type annotations.
# ---------------------------------------------------------------------------
ProjectStyleProfile.model_rebuild()
