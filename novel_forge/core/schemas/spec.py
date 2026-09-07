"""StorySpec — the creative brief that drives everything."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from novel_forge.core.schemas.base import VersionedSchema

# ---------------------------------------------------------------------------
# BackstoryRevealSpec — added in M4 — see docs/ai_flavor_quality.md
# ---------------------------------------------------------------------------


class BackstoryRevealSpec(BaseModel):
    """Structured backstory reveal requirement.

    Replaces free-text ``extra_instructions`` lines like
    "前配音员沈鹿溪在经历一场被恶意剪辑的网络暴力后隐居..." —
    instead of trusting the LLM to remember a paragraph of creative
    guidance, projects declare structured constraints:

    - topic: what the backstory is about (e.g. "主角网络暴力前史")
    - required_first_appearance: chapter number by which the backstory
      must have been expanded to at least ``min_word_count`` chars
    - min_word_count: minimum expansion length (default 200)
    - reveal_mode: preferred expansion form
    - triggers: keywords whose presence in chapter content suggests
      the backstory could/should be expanded

    Consumed by ``QualityGate.check_backstory_reveals`` and the chapter
    orchestrator's pre-flight checks.
    """

    model_config = {"extra": "forbid"}

    topic: str = Field(
        min_length=1,
        description="Backstory topic identifier, e.g. '主角网络暴力前史'.",
    )
    required_first_appearance: int = Field(
        ge=1,
        description="Chapter number by which this backstory must have been expanded.",
    )
    min_word_count: int = Field(
        default=200,
        ge=0,
        description="Minimum expansion length in characters (default 200).",
    )
    reveal_mode: Literal[
        "flashback", "third_party_expose", "dialogue_snippet", "object_trigger"
    ] = Field(
        default="flashback",
        description="Preferred expansion form for this backstory.",
    )
    triggers: list[str] = Field(
        default_factory=list,
        description="Optional keywords whose presence signals a chance to expand.",
    )


class StorySpec(VersionedSchema):
    """User-provided specification for a story."""

    _correct_fields: ClassVar[set[str]] = {
        "title",
        "genre",
        "theme",
        "tone",
        "length_target",
        "language",
        "characters_hint",
        "world_hint",
        "conflict_hint",
        "pov_hint",
        "opening_style",
        "ending_style",
        "extra_instructions",
        "narrative_complexity",
        "writing_style",
        # ── M4 additions (see docs/ai_flavor_quality.md) ──
        "backstory_reveals",
        "character_silence",
        # ── TTS extension ──
        "audio_aesthetic_hint",
    }
    _writing_style_aliases: ClassVar[dict[str, str]] = {
        "default": "default",
        "通用": "default",
        "默认": "default",
        "普通": "default",
        "常规": "default",
        "webnovel": "webnovel",
        "web_novel": "webnovel",
        "web novel": "webnovel",
        "网文": "webnovel",
        "网络文学": "webnovel",
        "爽文": "webnovel",
        "literary": "literary",
        "文学": "literary",
        "文学风": "literary",
        "文学化": "literary",
        "文艺": "literary",
        "文艺向": "literary",
        "纯文学": "literary",
    }

    title: str = Field(default="", description="Working title (may be empty).")
    genre: str = Field(
        default="other",
        description="Primary genre, e.g. 'fantasy', 'scifi', '古代言情，宫斗'.",
    )
    theme: str = Field(description="Central theme / premise in one sentence.")
    tone: str = Field(
        default="neutral",
        description="Desired tone, e.g. 'dark', 'humorous', 'lyrical'.",
    )
    length_target: int = Field(
        default=3000,
        ge=500,
        le=1_000_000,
        description="Target word count.",
    )
    language: str = Field(
        default="zh",
        description="Output language code. Plain 'zh' means Simplified Chinese (zh-Hans).",
    )
    characters_hint: str = Field(
        default="",
        description="Free-text hint about main characters.",
    )
    world_hint: str = Field(
        default="",
        description="Free-text hint about setting / world.",
    )
    conflict_hint: str = Field(
        default="",
        description="核心冲突/矛盾提示，如'人与自然的对抗'、'内心的善恶挣扎'。",
    )
    pov_hint: str = Field(
        default="",
        description=(
            "叙事视角提示。必须明确叙事人称、视角结构和角色分配；"
            "如'第三人称限知，林晚单主视角，非对话正文禁用我/我们作为叙述主体'，"
            "或'第一人称，林晚自述'。"
        ),
    )
    opening_style: str = Field(
        default="",
        description="开篇方式提示，如'悬念开场'、'倒叙'、'环境描写切入'、'对话开场'。",
    )
    ending_style: str = Field(
        default="",
        description="结尾方式提示，如'开放式结局'、'首尾呼应'、'反转结局'、'余韵式收束'。",
    )
    extra_instructions: str = Field(
        default="",
        description="Any additional creative instructions.",
    )
    style_tags: list[str] = Field(
        default_factory=list,
        description=(
            "Deprecated: overlay 模块已被 style_profile 取代。保留字段仅为向后兼容旧 JSON 文件。"
        ),
    )
    writing_style: str = Field(
        default="default",
        description=(
            "Deprecated compatibility field. New flows derive writing style through "
            "ProjectStyleProfile/style_profile instead of this coarse mode."
        ),
    )
    narrative_complexity: Literal["simple", "standard", "complex", "epic"] = Field(
        default="standard",
        description="叙事复杂度级别，影响支线数量建议",
    )

    # ── M4 additions (see docs/ai_flavor_quality.md) ──
    backstory_reveals: list[BackstoryRevealSpec] = Field(
        default_factory=list,
        description=(
            "结构化的背景信息展开时间窗约束。系统会在 outline 生成阶段把这些约束"
            "传递到 chapter_plan.scene_intent，并在每个章节生成后通过 "
            "QualityGate.check_backstory_reveals() 校验是否满足。"
        ),
    )
    character_silence: bool = Field(
        default=False,
        description=(
            "主角是否被设定为'几乎不主动说话'。为 True 时，dialogue_ratio 的语义"
            "从'全章对话占比'改为'主角以外角色的对话占比'。用于解决沉默主角与"
            "高对话密度规格之间的结构性冲突。"
        ),
    )
    audio_aesthetic_hint: str = Field(
        default="",
        description=(
            "声音美学提示：期望的旁白风格、听觉氛围。"
            "如'沉稳低沉的男性旁白，带有沧桑感'。"
            "用于引导 StoryBible.audio_aesthetic 和 TTS 旁白画像生成。"
        ),
    )

    @field_validator("theme")
    @classmethod
    def theme_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("theme must not be blank")
        return v.strip()

    @field_validator("genre")
    @classmethod
    def genre_not_empty(cls, v: str) -> str:
        if not v.strip():
            return "other"
        return v.strip()

    @field_validator("writing_style", mode="before")
    @classmethod
    def normalize_writing_style(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return "default"
        return cls._writing_style_aliases.get(normalized, "default")
