"""StoryBible and CharacterBible for Long Mode world-building."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novel_forge.core.domain.character_identity import (
    clean_character_name,
    normalize_character_role,
    stable_character_id,
)
from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.schemas.world_rules import WorldRuleBook
from novel_forge.core.utils.type_coerce import stringify_text_value


class CharacterKnowledgeBoundary(VersionedSchema):
    """Initial knowledge boundary for a character at story start."""

    known_facts: list[str] = Field(
        default_factory=list,
        description="角色在故事开始时已知的事实。",
    )
    suspected: list[str] = Field(
        default_factory=list,
        description="角色怀疑但不确定的事。",
    )
    misbeliefs: list[str] = Field(
        default_factory=list,
        description="角色错误相信的事。",
    )
    secrets_kept: list[str] = Field(
        default_factory=list,
        description="角色主动隐瞒的信息。",
    )
    sensory_access_rules: list[str] = Field(
        default_factory=list,
        description="角色可感知的信息通道限制，如'无法感知密室内的动作'、'听不到隔壁对话'。",
    )


class CharacterVisualIdentity(BaseModel):
    """Reusable screen identity authored with the character, not during adaptation."""

    model_config = ConfigDict(extra="forbid")

    facial_anchors: list[str] = Field(
        default_factory=list,
        description="3-5 个稳定面部锚点，如脸型、眉眼、发型与识别性痕迹。",
    )
    silhouette: str = Field(default="", description="远景仍可辨认的体态、身形与轮廓。")
    body_language: str = Field(default="", description="稳定姿态、动作习惯与空间占位方式。")
    costume_palette: list[str] = Field(
        default_factory=list,
        description="核心服装廓形、材质与主辅色；允许随剧情演化但需可追踪。",
    )
    signature_props: list[str] = Field(
        default_factory=list,
        description="与角色身份或行动相关的标志性道具。",
    )
    continuity_rules: list[str] = Field(
        default_factory=list,
        description="跨章节、配音宣传物与影视镜头都必须保持的身份规则。",
    )
    forbidden_drift: list[str] = Field(
        default_factory=list,
        description="禁止无剧情依据改变的年龄感、五官、体态、发色或服装特征。",
    )


class StoryLocationProfile(BaseModel):
    """Shared story-space asset for prose, sound design and screen production."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(default="", description="稳定场景 id。")
    name: str = Field(description="场景规范名。")
    dramatic_function: str = Field(default="", description="该空间承载的冲突或情绪功能。")
    geography: str = Field(default="", description="地理位置、外部连接与可达性。")
    era: str = Field(default="", description="场景呈现对应的时代或时间层。")
    spatial_layout: str = Field(default="", description="门窗、动线、层高和关键区域的空间布局。")
    materials: list[str] = Field(default_factory=list, description="稳定材质与表面质感。")
    practical_lights: list[str] = Field(
        default_factory=list, description="场景内有动机的实景光源。"
    )
    weather_states: list[str] = Field(default_factory=list, description="可用天气及其叙事影响。")
    recurring_props: list[str] = Field(
        default_factory=list, description="位置固定或反复出现的关键道具。"
    )
    ambient_sound: list[str] = Field(
        default_factory=list, description="可供配音与混音继承的环境声层。"
    )
    continuity_rules: list[str] = Field(
        default_factory=list,
        description="跨章节和跨镜头保持动线、光向、道具与声场一致的规则。",
    )
    shot_language_seed: str = Field(
        default="",
        description=(
            "可选镜头语言种子：该场景偏好的景别、运镜、光线与焦段（如「低机位、手持、"
            "35mm、实景侧光」）。影视镜头规划优先消费种子，缺省时回落通用镜头模式。"
        ),
    )
    color_mood: str = Field(
        default="",
        description="场景色彩基调与情绪（如「冷青灰、压抑」），供视觉资产卡与色彩脚本继承。",
    )
    key_light: str = Field(
        default="",
        description="场景主光方向与质感（如「窗侧逆光、硬边」），供镜头布光与资产卡锁定。",
    )


class CharacterProfile(VersionedSchema):
    """Profile for a single character."""

    _correct_fields: ClassVar[set[str]] = {
        "character_id",
        "name",
        "role",
        "age",
        "gender",
        "status",
        "time_layer",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "relationships",
        "voice",
        "visual_identity",
        "knowledge_boundaries",
        "shot_language_seed",
        "notes",
        # ── TTS extension ──
        "tts_voice_hints",
    }

    _field_aliases: ClassVar[dict[str, str]] = {
        "relationships_2": "relationships",
        "relationships2": "relationships",
        "relationships_3": "relationships",
        "relationship": "relationships",
        "relations": "relationships",
        "id": "character_id",
        "entity_id": "character_id",
        "socialStatus": "social_status",
        "arc_goal": "arc",
        "arcGoal": "arc",
    }

    character_id: str = Field(default="", description="Stable deterministic character id.")
    name: str = Field(description="Character's full name.")
    role: str = Field(
        default="supporting",
        description="Role: 'protagonist', 'antagonist', 'deuteragonist', 'supporting', 'minor'.",
    )
    age: str = Field(default="", description="Age or age range.")
    gender: str = Field(
        default="", description="Character gender, e.g. '男', '女', or empty if ambiguous."
    )
    status: str = Field(
        default="active",
        description="Character lifecycle: 'active' (in story), 'dormant' (temporarily off-stage), 'retired' (arc complete, excluded from prompt injection).",
    )
    time_layer: str = Field(
        default="default",
        description="Narrative time layer: 'modern', 'past', 'cross_temporal', 'memory_only', or 'default'.",
    )
    social_status: str = Field(
        default="", description="Stable identity, rank, occupation or title."
    )
    abilities: str = Field(default="", description="Stable skills, powers, resources or expertise.")
    appearance: str = Field(default="", description="Physical description.")
    personality: str = Field(default="", description="Key personality traits.")
    backstory: str = Field(default="", description="Relevant backstory.")
    arc: str = Field(
        default="",
        description="Character arc summary (beginning → end transformation).",
    )

    @property
    def arc_goal(self) -> str:
        """Backward-compatible alias for older prompt/context code."""
        return self.arc

    relationships: dict[str, str] = Field(
        default_factory=dict,
        description="Map of other_character_name → relationship description.",
    )
    voice: str = Field(
        default="",
        description="角色声纹特征：句式节奏、解释倾向、情绪句法、标志性说话动作。",
    )
    visual_identity: CharacterVisualIdentity = Field(
        default_factory=CharacterVisualIdentity,
        description="上游统一角色形象定位，供小说描述、配音宣传资产和影视角色资产共同继承。",
    )
    shot_language_seed: str = Field(
        default="",
        description=(
            "可选角色镜头语言种子：该角色偏好的景别、运镜与光线（如「平视、固定、"
            "85mm 浅景深」）。场次镜头规划在场景种子之外叠加角色种子。"
        ),
    )
    knowledge_boundaries: CharacterKnowledgeBoundary = Field(
        default_factory=CharacterKnowledgeBoundary,
        description="角色初始知识边界——该角色在故事开始时已知、怀疑、误解和隐瞒的信息。",
    )
    notes: str = Field(default="")
    tts_voice_hints: dict[str, Any] | None = Field(
        default=None,
        description=(
            "TTS 声音提示：音域、语速、音色质感等结构化数据。"
            "对应 TTSVoiceHints schema，以 dict 存储以避免循环导入。"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _preserve_unknown_fields_in_notes(cls, data: Any) -> Any:
        """Fold LLM-added profile fields into notes before strict validation."""
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        allowed_keys = set(cls.model_fields)
        aliases = set(cls._field_aliases)
        case_insensitive_fields = set(cls._correct_fields)

        unknown: dict[str, Any] = {}
        for key in list(normalized.keys()):
            if key in allowed_keys or key in aliases or key.lower() in case_insensitive_fields:
                continue
            unknown[key] = normalized.pop(key)

        if not unknown:
            return cls._ensure_character_id(normalized)

        extra_notes = [
            f"{key}: {cls._stringify_extra_value(value)}"
            for key, value in unknown.items()
            if value not in (None, "", [], {})
        ]
        if not extra_notes:
            return cls._ensure_character_id(normalized)

        existing_notes = str(normalized.get("notes") or "").strip()
        merged_extra = "；".join(extra_notes)
        normalized["notes"] = (
            f"{existing_notes}\n额外设定：{merged_extra}"
            if existing_notes
            else f"额外设定：{merged_extra}"
        )
        return cls._ensure_character_id(normalized)

    @staticmethod
    def _ensure_character_id(data: dict[str, Any]) -> dict[str, Any]:
        if not str(data.get("character_id") or "").strip():
            name = clean_character_name(data.get("name"))
            if name:
                data["character_id"] = stable_character_id(name)
        return data

    @staticmethod
    def _stringify_extra_value(value: Any) -> str:
        """Render arbitrary extra profile values compactly for notes."""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _stringify_profile_text_value(value: Any) -> str:
        """Flatten model-emitted structured snippets into one profile text field.

        Delegates to the shared ``stringify_text_value`` utility in
        ``novel_forge.core.utils.type_coerce``.
        """
        return stringify_text_value(value)

    @field_validator(
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "voice",
        "notes",
        mode="before",
    )
    @classmethod
    def _coerce_profile_text_fields(cls, value: Any) -> str:
        """Keep CharacterProfile text fields scalar even when an LLM nests details."""
        return cls._stringify_profile_text_value(value)

    @field_validator("age", mode="before")
    @classmethod
    def _coerce_age_to_str(cls, v: int | str) -> str:
        """Convert integer age to string for compatibility with LLM outputs."""
        if isinstance(v, int):
            return str(v)
        return v if v else ""

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value: Any) -> str:
        """Normalize role aliases emitted by LLMs into canonical buckets."""
        return normalize_character_role(value)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        """Normalize lifecycle labels while keeping the schema tolerant."""
        text = str(value or "").strip().lower()
        aliases = {
            "active": "active",
            "活跃": "active",
            "在场": "active",
            "主线活跃": "active",
            "dormant": "dormant",
            "潜伏": "dormant",
            "暂离": "dormant",
            "回忆": "dormant",
            "retired": "retired",
            "退场": "retired",
            "完结": "retired",
            "deceased": "retired",
            "dead": "retired",
            "died": "retired",
            "已故": "retired",
            "故去": "retired",
            "死亡": "retired",
            "离世": "retired",
            "逝世": "retired",
        }
        return aliases.get(text, text or "active")

    @field_validator("time_layer", mode="before")
    @classmethod
    def _normalize_time_layer(cls, value: Any) -> str:
        """Normalize model-provided narrative time-layer labels."""
        text = str(value or "").strip().lower()
        aliases = {
            "present": "modern",
            "current": "modern",
            "modern": "modern",
            "现代": "modern",
            "今生": "modern",
            "past": "past",
            "历史": "past",
            "民国": "past",
            "前世": "past",
            "dual": "cross_temporal",
            "both": "cross_temporal",
            "cross_temporal": "cross_temporal",
            "双时间线": "cross_temporal",
            "跨时空": "cross_temporal",
            "memory": "memory_only",
            "memory_only": "memory_only",
            "回忆线": "memory_only",
            "记忆线": "memory_only",
        }
        return aliases.get(text, text or "default")


class CharacterBible(VersionedSchema):
    """Collection of all character profiles."""

    characters: list[CharacterProfile] = Field(min_length=1, description="At least one character.")


class StoryBible(VersionedSchema):
    """World-building reference document."""

    _correct_fields: ClassVar[set[str]] = {
        "title",
        "premise",
        "era",
        "notes",
        "time_convention",
        "geography",
        "locations",
        "culture",
        "magic_or_tech",
        "rules",
        "world_rule_book",
        "tone",
        "themes",
        "banned_intent_rules",
        "social_hierarchy",
        "address_rules",
        "self_reference_rules",
        "etiquette_rules",
        "institution_terms",
        "material_culture",
        "anachronism_blacklist",
        "dialogue_register_rules",
        "location_transition_window_sentences",
        "bridge_echo_window_chars",
        "max_key_revelations_per_chapter",
        "min_unresolved_threads_to_keep",
        # ── TTS extension ──
        "audio_aesthetic",
    }
    _field_aliases: ClassVar[dict[str, str]] = {
        **VersionedSchema._field_aliases,
        "world_rules": "rules",
        "worldRules": "rules",
        "worldRuleBook": "world_rule_book",
        "world_rulebook": "world_rule_book",
        "theme": "themes",
        "theme_list": "themes",
        "additional_notes": "notes",
        "additionalNotes": "notes",
        "key_locations": "locations",
        "location_profiles": "locations",
    }

    title: str = Field(default="", description="Story working title.")
    premise: str = Field(description="Core premise in 2–3 sentences.")
    era: str = Field(default="", description="Time period / era.")
    geography: str = Field(default="", description="Key locations.")
    locations: list[StoryLocationProfile] = Field(
        default_factory=list,
        description="结构化核心场景资产；作为小说空间连续性、环境声和影视美术的共同上游。",
    )
    culture: str = Field(default="", description="Cultural norms, society.")
    magic_or_tech: str = Field(
        default="",
        description="Magic system / technology level rules.",
    )
    rules: list[str] = Field(
        default_factory=list,
        description="Compatibility display summary of hard world rules; not the source of truth.",
    )
    world_rule_book: WorldRuleBook = Field(
        default_factory=WorldRuleBook,
        description=(
            "Versioned, executable source of truth for immutable world rules. "
            "New long-form projects must initialize this field before chapters can run."
        ),
    )
    tone: str = Field(default="", description="Overall tone / style guide.")
    themes: list[str] = Field(default_factory=list, description="Thematic threads.")
    notes: str = Field(default="", description="Recovered extra world-building notes.")
    time_convention: str = Field(
        default="",
        description="时间表达约定，如'古代中国：时辰/刻'、'现代：小时/分钟'、'奇幻：沙漏/月相'。为空时按时代背景自然选择。",
    )
    banned_intent_rules: list[str] = Field(
        default_factory=list,
        description="项目级禁止意向规则。每条描述一个应避免的表达模式或写作习惯，如：'禁止使用瞳孔微缩等眼部反应模板'、'私下对话中角色自称用我而非官职'",
    )
    social_hierarchy: str = Field(
        default="",
        description="时代/世界观中的阶层、官民、主仆、门第或组织等级结构，用于约束角色互动。",
    )
    address_rules: list[str] = Field(
        default_factory=list,
        description="称谓规则，如上下级、亲疏、官职、家族、师徒等不同场合的称呼约束。",
    )
    self_reference_rules: list[str] = Field(
        default_factory=list,
        description="角色自称规则，如正式场合/私下/对上/对下的自称变化。",
    )
    etiquette_rules: list[str] = Field(
        default_factory=list,
        description="礼制、行为边界、避讳与社交仪式规则。",
    )
    institution_terms: list[str] = Field(
        default_factory=list,
        description="本世界/时代内高频制度术语、官职、组织、机构、身份称号。",
    )
    material_culture: list[str] = Field(
        default_factory=list,
        description="时代质感相关器物、服饰、交通、饮食、建筑、书写工具等。",
    )
    anachronism_blacklist: list[str] = Field(
        default_factory=list,
        description="不符合时代/世界观的现代词、现代器物、现代观念或表达习惯。",
    )
    dialogue_register_rules: list[str] = Field(
        default_factory=list,
        description="对白语体规则，如古雅/白话比例、禁用现代口吻、正式与私下语气差异。",
    )

    @model_validator(mode="after")
    def _sync_legacy_rule_summary(self) -> "StoryBible":
        """Keep the legacy display list readable without treating it as authority."""
        if self.world_rule_book.rules and not self.rules:
            self.rules = [rule.content for rule in self.world_rule_book.hard_rules]
        return self

    location_transition_window_sentences: int = Field(
        default=3,
        ge=1,
        le=10,
        description="场景切换时开场地点变化，前 N 句必须出现位移动作链。",
    )
    bridge_echo_window_chars: int = Field(
        default=900,
        ge=100,
        le=2000,
        description="桥接落地：开场前 N 字须回应 bridge.action_handoff。",
    )
    max_key_revelations_per_chapter: int = Field(
        default=2,
        ge=1,
        le=5,
        description="单章重大揭示上限，超出必须改为伏笔或误导线索。",
    )
    min_unresolved_threads_to_keep: int = Field(
        default=1,
        ge=0,
        le=3,
        description="本章至少保留 N 条未决线索进入下一章（0 则不强制）。",
    )
    audio_aesthetic: str = Field(
        default="",
        description=(
            "声音美学设定：基于世界观的旁白风格指导。"
            "由 init_story_bible 从 Spec.audio_aesthetic_hint 推导生成。"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _preserve_unknown_fields_in_notes(cls, data: Any) -> Any:
        """Absorb extra StoryBible fields instead of failing hard on harmless drift."""
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        # Apply aliases here as well so schema compatibility does not depend on
        # validator ordering across inheritance layers.
        for alias, correct in cls._field_aliases.items():
            if alias in normalized and correct not in normalized:
                normalized[correct] = normalized.pop(alias)
            elif alias in normalized:
                normalized.pop(alias)

        # Merge detail-style fields back into their canonical text fields.
        for key in list(normalized.keys()):
            target_key = cls._detail_target_key(key)
            if target_key is None:
                continue
            value = normalized.pop(key)
            if value in (None, "", [], {}):
                continue
            normalized[target_key] = cls._merge_story_text(normalized.get(target_key), value)

        allowed_keys = set(cls.model_fields)
        aliases = set(cls._field_aliases)
        case_insensitive_fields = set(cls._correct_fields)

        unknown: dict[str, Any] = {}
        for key in list(normalized.keys()):
            if key in allowed_keys or key in aliases or key.lower() in case_insensitive_fields:
                continue
            unknown[key] = normalized.pop(key)

        if not unknown:
            return normalized

        extra_notes = [
            f"{key}: {cls._stringify_extra_value(value)}"
            for key, value in unknown.items()
            if value not in (None, "", [], {})
        ]
        if not extra_notes:
            return normalized

        existing_notes = str(normalized.get("notes") or "").strip()
        merged_extra = "；".join(extra_notes)
        normalized["notes"] = (
            f"{existing_notes}\n额外设定：{merged_extra}"
            if existing_notes
            else f"额外设定：{merged_extra}"
        )
        return normalized

    @field_validator("themes", mode="before")
    @classmethod
    def _coerce_themes_to_list(cls, v: Any) -> list[str]:
        """Coerce string themes to list for compatibility with LLM outputs."""
        return cls._coerce_text_items(
            v,
            preferred_keys=("theme", "content", "text", "description", "summary", "value"),
        )

    @field_validator(
        "title",
        "premise",
        "era",
        "geography",
        "culture",
        "magic_or_tech",
        "tone",
        "notes",
        "time_convention",
        mode="before",
    )
    @classmethod
    def _coerce_story_text_fields(cls, v: Any) -> str:
        """Coerce text fields when split-task LLMs return note/rule objects."""
        return cls._coerce_text_field(
            v,
            preferred_keys=(
                "content",
                "text",
                "description",
                "summary",
                "value",
                "title",
                "name",
            ),
        )

    @field_validator("rules", mode="before")
    @classmethod
    def _coerce_rules_to_list(cls, v: Any) -> list[str]:
        """Coerce string or structured rule objects into rule text."""
        return cls._coerce_text_items(
            v,
            preferred_keys=(
                "content",
                "rule",
                "text",
                "description",
                "constraint",
                "summary",
                "value",
            ),
        )

    @field_validator("banned_intent_rules", mode="before")
    @classmethod
    def _limit_banned_intent_rules(cls, v: Any) -> list[str]:
        """Coerce and limit banned_intent_rules: max 20 items, each ≤ 80 chars."""
        return cls._coerce_text_items(
            v,
            preferred_keys=("content", "rule", "intent", "text", "description", "value"),
            limit=20,
            max_chars=80,
        )

    @field_validator(
        "address_rules",
        "self_reference_rules",
        "etiquette_rules",
        "institution_terms",
        "material_culture",
        "anachronism_blacklist",
        "dialogue_register_rules",
        mode="before",
    )
    @classmethod
    def _coerce_world_context_rules(cls, v: Any) -> list[str]:
        """Coerce world-context rule fields: max 20 items, each ≤ 120 chars."""
        return cls._coerce_text_items(
            v,
            preferred_keys=(
                "content",
                "rule",
                "term",
                "item",
                "text",
                "description",
                "value",
                "name",
            ),
            limit=20,
            max_chars=120,
        )

    @staticmethod
    def _coerce_text_items(
        value: Any,
        *,
        preferred_keys: tuple[str, ...] = (),
        limit: int | None = None,
        max_chars: int | None = None,
    ) -> list[str]:
        """Normalize LLM string-list fields, including occasional structured items."""
        if isinstance(value, str):
            values = [value] if value.strip() else []
        elif isinstance(value, list):
            values = value
        else:
            return []

        normalized: list[str] = []
        for item in values:
            text = StoryBible._coerce_text_item(item, preferred_keys=preferred_keys)
            if max_chars is not None:
                text = text[:max_chars]
            if text:
                normalized.append(text)
            if limit is not None and len(normalized) >= limit:
                break
        return normalized

    @staticmethod
    def _coerce_text_item(item: Any, *, preferred_keys: tuple[str, ...]) -> str:
        if item is None:
            return ""
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, list):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if isinstance(item, dict):
            for key in (
                *preferred_keys,
                "content",
                "rule",
                "text",
                "description",
                "summary",
                "value",
                "name",
                "label",
                "title",
            ):
                candidate = item.get(key)
                if candidate is None:
                    continue
                if isinstance(candidate, (list, dict)):
                    text = json.dumps(
                        candidate,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                else:
                    text = str(candidate)
                if text.strip():
                    return text.strip()
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return str(item or "").strip()

    @field_validator("social_hierarchy", mode="before")
    @classmethod
    def _limit_social_hierarchy(cls, v: Any) -> str:
        text = cls._coerce_text_field(
            v,
            preferred_keys=("content", "hierarchy", "description", "text", "summary", "value"),
        )
        return text[:500]

    @staticmethod
    def _coerce_text_field(value: Any, *, preferred_keys: tuple[str, ...]) -> str:
        if isinstance(value, list):
            return "；".join(
                item
                for item in StoryBible._coerce_text_items(value, preferred_keys=preferred_keys)
                if item
            )
        return StoryBible._coerce_text_item(value, preferred_keys=preferred_keys)

    @classmethod
    def _detail_target_key(cls, key: str) -> str | None:
        """Map detail-like extra fields back to a canonical StoryBible text field."""
        for suffix in ("_detail", "_details"):
            if not key.endswith(suffix):
                continue
            candidate = key[: -len(suffix)]
            if candidate in cls.model_fields and candidate != "notes":
                field_info = cls.model_fields[candidate]
                if field_info.annotation is str:
                    return candidate
        return None

    @staticmethod
    def _merge_story_text(current: Any, extra: Any) -> str:
        """Merge supplementary text into the canonical field without duplicating content."""
        current_text = StoryBible._stringify_extra_value(current)
        extra_text = StoryBible._stringify_extra_value(extra)
        if not extra_text:
            return current_text
        if not current_text:
            return extra_text
        if extra_text in current_text:
            return current_text
        return f"{current_text}\n补充设定：{extra_text}"

    @staticmethod
    def _stringify_extra_value(value: Any) -> str:
        """Render arbitrary extra StoryBible values compactly for notes/text merging."""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)
