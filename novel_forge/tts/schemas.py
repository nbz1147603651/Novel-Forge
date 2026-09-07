"""TTS domain schemas — voice team, dubbing script, synthesis results.

All schemas follow the project's VersionedSchema convention for
forward-compatible persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from novel_forge.core.schemas.base import VersionedSchema

# ─── Enums ────────────────────────────────────────────────────────────────────


_REGISTERED_EXTERNAL_TTS_PROVIDERS: set[str] = set()


def register_external_tts_provider(provider_id: str) -> None:
    """Allow a trusted adapter registration to cross the schema boundary.

    Built-in providers remain explicit enum members.  External wire protocols
    must first be registered by the gateway factory; arbitrary or misspelled
    persisted provider ids therefore continue to fail validation.
    """

    _REGISTERED_EXTERNAL_TTS_PROVIDERS.add(provider_id.strip().lower())


def unregister_external_tts_provider(provider_id: str) -> None:
    """Remove a previously registered external provider id (mainly for tests)."""

    _REGISTERED_EXTERNAL_TTS_PROVIDERS.discard(provider_id.strip().lower())


class TTSProvider(str, Enum):
    """Supported TTS provider backends."""

    MINIMAX = "minimax"
    BAILIAN = "bailian"  # 阿里百炼
    DASHSCOPE = "dashscope"  # 阿里云 DashScope 兼容名称
    TENCENT = "tencent"  # 腾讯云
    VOLCENGINE_ARK = "volcengine_ark"  # 火山方舟·豆包语音
    MIMO = "mimo"  # 小米 MiMo TTS
    LOCAL = "local"  # CosyVoice / ChatTTS
    QWEN3 = "qwen3"  # Qwen3-TTS local sidecar
    COSYVOICE = "cosyvoice"  # FunAudioLLM 本地原生 FastAPI
    OPENVOICE = "openvoice"  # MyShell OpenVoice V2 本地运行时
    MOCK = "mock"

    @classmethod
    def _missing_(cls, value: object) -> TTSProvider | None:
        """Create a schema-safe pseudo member only for a registered adapter."""

        if not isinstance(value, str):
            return None
        provider_id = value.strip().lower()
        if provider_id not in _REGISTERED_EXTERNAL_TTS_PROVIDERS:
            return None
        member = str.__new__(cls, provider_id)
        member._name_ = f"EXTERNAL_{provider_id.upper().replace('-', '_')}"
        member._value_ = provider_id
        return member


class TTSFeature(str, Enum):
    """Fine-grained synthesis features negotiated independently of provider names."""

    SPEED = "speed"
    VOLUME = "volume"
    PITCH = "pitch"
    EMOTION = "emotion"
    PARALINGUISTIC = "paralinguistic"
    PRONUNCIATION = "pronunciation"
    LANGUAGE_BOOST = "language_boost"
    VOICE_EFFECTS = "voice_effects"
    SSML = "ssml"
    INSTRUCTION_CONTROL = "instruction_control"
    NATIVE_SUBTITLES = "native_subtitles"
    STREAMING_AUDIO = "streaming_audio"


class TTSProviderCapabilities(BaseModel):
    """Provider features surfaced consistently to API and desktop clients."""

    provider: TTSProvider = TTSProvider.MOCK
    synthesis: bool = True
    voice_clone: bool = False
    voice_design: bool = False
    system_voice_catalog: bool = False
    local_reference_audio: bool = False
    synthesis_features: set[TTSFeature] = Field(
        default_factory=set,
        description="当前适配器已真实消费的细粒度合成能力",
    )


class VoiceCloneStatus(str, Enum):
    """Status of a voice clone operation."""

    PENDING = "pending"
    CLONING = "cloning"
    READY = "ready"
    EXPIRED = "expired"
    FAILED = "failed"


class SegmentType(str, Enum):
    """Types of dubbing script segments."""

    NARRATION = "narration"  # 旁白
    DIALOGUE = "dialogue"  # 角色对白
    INNER_THOUGHT = "inner_thought"  # 内心独白
    BGM = "bgm"  # 背景音乐提示
    SFX = "sfx"  # 音效提示
    SILENCE = "silence"  # 静音/停顿


class EmotionTag(str, Enum):
    """Emotion tags for TTS expression control."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    FEARFUL = "fearful"
    SURPRISED = "surprised"
    DISGUSTED = "disgusted"
    TENDER = "tender"
    MOCKING = "mocking"
    WHISPER = "whisper"
    NOSTALGIC = "nostalgic"  # 怀旧/回忆
    ANXIOUS = "anxious"  # 焦虑/紧张
    CONTEMPT = "contempt"  # 轻蔑/不屑
    DETERMINED = "determined"  # 坚定/决绝
    PLAYFUL = "playful"  # 俏皮/戏谑


class SynthesisStatus(str, Enum):
    """Status of a synthesis segment."""

    PENDING = "pending"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TakeReviewStatus(str, Enum):
    """Lifecycle of a Voice Room audition take."""

    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ─── Voice Team Schemas ───────────────────────────────────────────────────────


class VoicePerformanceOffsets(BaseModel):
    """Provider-neutral numeric baseline before scene-level performance is applied."""

    speed_offset: float = Field(default=0.0, ge=-0.5, le=0.5)
    pitch_offset: int = Field(default=0, ge=-12, le=12)
    vol_offset: float = Field(default=0.0, ge=-0.5, le=0.5)


class VoicePerformanceOverrides(BaseModel):
    """Per-field author overrides; ``None`` keeps that field under automatic policy."""

    speed_offset: float | None = Field(default=None, ge=-0.5, le=0.5)
    pitch_offset: int | None = Field(default=None, ge=-12, le=12)
    vol_offset: float | None = Field(default=None, ge=-0.5, le=0.5)

    @property
    def active_fields(self) -> set[str]:
        return {
            field
            for field, value in (
                ("speed", self.speed_offset),
                ("pitch", self.pitch_offset),
                ("volume", self.vol_offset),
            )
            if value is not None
        }


class VoicePerformanceDirection(BaseModel):
    """Semantic, scene-conditional acting direction kept separate from voice identity."""

    trigger: str = Field(default="", description="触发表演变化的情境或话题")
    direction: Literal[
        "slow_down",
        "speed_up",
        "pause_more",
        "lower_volume",
        "raise_volume",
    ]
    strength: Literal["subtle", "moderate"] = "subtle"
    evidence: str = Field(default="", description="角色资料中的原始依据")

    @field_validator("strength", mode="before")
    @classmethod
    def _migrate_legacy_strength(cls, v: Any) -> str:
        """Map removed 'strong' level to 'moderate' for backward compat."""
        if v == "strong":
            return "moderate"
        return str(v)


class VoicePerformanceProfile(BaseModel):
    """Canonical character-performance policy, independent from the selected voice ID."""

    policy_version: str = "1.0"
    automatic_baseline: VoicePerformanceOffsets = Field(default_factory=VoicePerformanceOffsets)
    manual_overrides: VoicePerformanceOverrides = Field(default_factory=VoicePerformanceOverrides)
    conditional_directions: list[VoicePerformanceDirection] = Field(default_factory=list)
    short_utterance_stability: bool = Field(
        default=True,
        description="短句默认保持原始声纹和自然语速，避免拖腔与身份漂移",
    )
    derivation_source: Literal["neutral", "structured_hints", "legacy", "manual"] = "neutral"
    derivation_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def has_manual_overrides(self) -> bool:
        return bool(self.manual_overrides.active_fields)

    @property
    def configured_offsets(self) -> VoicePerformanceOffsets:
        overrides = self.manual_overrides
        baseline = self.automatic_baseline
        return VoicePerformanceOffsets(
            speed_offset=(
                baseline.speed_offset if overrides.speed_offset is None else overrides.speed_offset
            ),
            pitch_offset=(
                baseline.pitch_offset if overrides.pitch_offset is None else overrides.pitch_offset
            ),
            vol_offset=(
                baseline.vol_offset if overrides.vol_offset is None else overrides.vol_offset
            ),
        )


class VariantPreview(BaseModel):
    """单个台词变体（常规/短句/情绪/紧急）的试听缓存。"""

    audio_path: str = Field(default="", description="该变体的试听音频路径")
    text: str = Field(default="", description="该变体使用的试听台词文本")
    error: str = Field(default="", description="该变体试听准备失败时的错误信息")


class VoiceCastEntry(VersionedSchema):
    """A single character-to-voice mapping in the voice team.

    Fields are logically grouped into: Identity, Voice Config, Matching,
    Adjudication, Lifecycle, Performance, Preview, and Fallback.
    Serialization format is unchanged (flat JSON).

    Author: novel-forge
    """

    # ── Identity ────────────────────────────────────────────────────────────────
    character_id: str = Field(min_length=1, description="角色唯一标识")
    character_name: str = Field(min_length=1, description="角色显示名称")
    # 角色身份硬约束，在 build_voice_team 阶段从 character 画像写入并随 team 持久化，
    # 供下游脚本生成 / 说话人裁决 / 专业审校等 LLM 步骤读取，避免性别错配。
    # 可空以兼容旧 voice_team.json；为空时下游投影显示“未指定”。
    character_gender: str = Field(
        default="",
        description="角色性别硬约束（male/female/neutral），下游脚本生成与审校不可违反",
    )
    character_age: str = Field(default="", description="角色年龄或年龄段，用于声音身份校核")
    character_role: str = Field(default="", description="角色定位（protagonist/minor/...）")
    avatar_path: str = Field(default="", description="角色头像路径（相对或绝对）")
    # ── Voice Config ─────────────────────────────────────────────────────────────
    voice_id: str = Field(default="", description="TTS 平台返回的音色 ID")
    model_id: str = Field(
        default="",
        description="该音色绑定的 TTS 模型 ID；云端自定义音色必须与创建时模型一致",
    )
    provider: TTSProvider = Field(default=TTSProvider.MOCK, description="TTS 平台")
    clone_status: VoiceCloneStatus = Field(
        default=VoiceCloneStatus.PENDING,
        description="音色克隆状态",
    )
    # ── Matching ────────────────────────────────────────────────────────────────
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0, description="音色质量评分")
    match_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="角色画像与音色身份的匹配置信度；None 表示尚未评估",
    )
    match_reasons: list[str] = Field(
        default_factory=list,
        description="可向用户解释的匹配依据",
    )
    match_warnings: list[str] = Field(
        default_factory=list,
        description="画像缺失、回退或低置信度等需要人工试听确认的风险",
    )
    # ── Adjudication ────────────────────────────────────────────────────────────
    llm_adjudication_status: Literal[
        "not_reviewed",
        "approved",
        "recast",
        "needs_audition",
        "rejected",
    ] = Field(default="not_reviewed", description="LLM 受约束选角裁决状态")
    llm_adjudication_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="LLM 对当前选角裁决的置信度",
    )
    llm_adjudication_reason: str = Field(default="", description="LLM 选角裁决的简要理由")
    audition_candidate_voice_ids: list[str] = Field(
        default_factory=list,
        description="通过硬约束且建议用于对比试听的音色 ID 白名单",
    )
    # ── Lifecycle ───────────────────────────────────────────────────────────────
    expires_at: datetime | None = Field(
        default=None,
        description="音色过期时间（部分平台有 TTL）",
    )
    activation_deadline: datetime | None = Field(
        default=None,
        description="临时音色首次正式合成的激活截止时间；激活后清空",
    )
    reference_audio_path: str = Field(default="", description="参考音频路径")
    reference_transcript: str = Field(
        default="",
        description="授权参考音频的逐字转写；Qwen3-TTS Base 用它建立高保真克隆提示",
    )
    clone_prompt: str = Field(default="", description="音色克隆时的示例文本")
    voice_design_prompt: str = Field(
        default="",
        description="根据角色特征生成音色时使用的可审计提示词",
    )
    voice_library_match_key: str = Field(
        default="",
        description="全局音色库匹配时的角色特征指纹，用于检测后续角色画像变化",
    )
    voice_source: Literal["system", "designed", "cloned", "manual", "library"] = Field(
        default="system",
        description="音色来源：系统音色/特征设计/参考音频克隆/人工指定/全局音色库",
    )
    # Approval gate for designed/cloned voices. MiniMax voice_design is a black
    # box that may return a wrong-gender voice despite a correct prompt; a
    # "pending" entry holds a saved preview for audition but is blocked from
    # formal synthesis until the author confirms it. Defaults to "approved" so
    # existing voice_team.json payloads (and system/manual voices) are unaffected.
    approval_status: Literal["pending", "approved", "rejected"] = Field(
        default="approved",
        description="设计/克隆音色的试听确认状态；pending 表示已生成试听但未确认，禁止进入正式合成。",
    )
    # ── Fallback ────────────────────────────────────────────────────────────────
    fallback_voice_ids: dict[str, str] = Field(
        default_factory=dict,
        description="跨平台回退所需的 provider→voice_id 映射；禁止复用主平台音色 ID",
    )
    identity_locked: bool = Field(
        default=False,
        description="角色完成首次正式合成后锁定 provider + voice_id，防止静默换人",
    )
    identity_locked_at: datetime | None = Field(default=None)
    identity_lock_reason: str = Field(default="")
    # ── Performance ─────────────────────────────────────────────────────────────
    speed_offset: float = Field(default=0.0, ge=-0.5, le=0.5, description="语速偏移")
    pitch_offset: int = Field(default=0, ge=-12, le=12, description="音调偏移")
    vol_offset: float = Field(default=0.0, ge=-0.5, le=0.5, description="音量偏移")
    performance_offsets_manually_set: bool = Field(
        default=False,
        description="旧版兼容字段；新代码以 performance_profile.manual_overrides 为准。",
    )
    performance_profile: VoicePerformanceProfile | None = Field(
        default=None,
        description="角色表演策略的唯一领域模型；旧数据缺失时由兼容层迁移。",
    )
    # ── Preview ─────────────────────────────────────────────────────────────────
    preview_audio_path: str = Field(
        default="",
        description="最近一次与当前音色身份和表达参数一致的角色试听文件（兼容旧数据；新路径以 preview_variants 为准）",
    )
    preview_text: str = Field(
        default="",
        description="最近一次角色试听使用的稳定台词（兼容旧数据；新路径以 preview_variants 为准）",
    )
    preview_error: str = Field(default="", description="自动准备角色试听时的最近错误（兼容旧数据）")
    preview_variants: dict[str, VariantPreview] = Field(
        default_factory=dict,
        description="按台词变体（identity/short/emotion/urgent）分别缓存的试听数据",
    )
    notes: str = Field(default="", description="人工备注")

    @property
    def is_ready(self) -> bool:
        """Usable for formal synthesis only when cloned AND audition-approved.

        A ``pending`` approval_status (set after voice_design/clone produces a
        preview) blocks synthesis until the author confirms the voice actually
        matches the character (e.g. gender). This guards against providers
        returning a wrong-gender voice that the system cannot detect from
        metadata alone.
        """
        return self.clone_status == VoiceCloneStatus.READY and self.approval_status == "approved"

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        return bool(
            (self.expires_at is not None and now > self.expires_at)
            or (self.activation_deadline is not None and now > self.activation_deadline)
        )

    def get_variant_preview(self, variant: str = "identity") -> VariantPreview:
        """返回指定变体的试听数据；旧数据自动回退到单值字段（identity 变体）。"""
        if variant in self.preview_variants:
            return self.preview_variants[variant]
        # 旧数据兼容：单值字段视为 identity 变体
        if variant == "identity" and (self.preview_audio_path or self.preview_text):
            return VariantPreview(
                audio_path=self.preview_audio_path,
                text=self.preview_text,
                error=self.preview_error,
            )
        return VariantPreview()

    def with_variant_preview(
        self,
        variant: str,
        *,
        audio_path: str = "",
        text: str = "",
        error: str = "",
    ) -> "VoiceCastEntry":
        """返回写入指定变体试听数据的新 VoiceCastEntry（同时维护兼容字段）。"""
        updated_variants = dict(self.preview_variants)
        updated_variants[variant] = VariantPreview(audio_path=audio_path, text=text, error=error)
        update: dict[str, Any] = {"preview_variants": updated_variants}
        # identity 变体始终同步到单值兼容字段，供旧读取路径使用
        if variant == "identity":
            update["preview_audio_path"] = audio_path
            update["preview_text"] = text
            update["preview_error"] = error
        return self.model_copy(update=update)


class VoiceTeamContract(VersionedSchema):
    """Project-level voice team contract."""

    entries: list[VoiceCastEntry] = Field(default_factory=list)
    narrator_voice_id: str = Field(default="", description="旁白音色 ID")
    narrator_provider: TTSProvider = Field(default=TTSProvider.MOCK)
    default_tts_model: str = Field(default="speech-2.8-hd", description="默认 TTS 模型")
    default_provider: TTSProvider = Field(default=TTSProvider.MINIMAX)
    preview_audio_path: str = Field(
        default="",
        description="最近一次音色设计生成的试听音频；不参与正式合成缓存指纹",
    )
    voice_catalog_fingerprint: str = Field(
        default="",
        description="最近一次成功拉取的供应商音色目录指纹",
    )
    voice_catalog_synced_at: datetime | None = Field(
        default=None,
        description="最近一次将供应商目录投影到匹配索引的时间",
    )
    voice_catalog_sync_status: str = Field(
        default="unknown",
        description="供应商目录同步状态: live/unavailable/unknown",
    )
    # Author's whole-team confirmation for auto-dubbing.  When True, post-archive
    # TTS trusts the team without re-checking each entry; any entry mutation
    # (approve/clone/design/replace) or readiness regression resets it via the
    # validator below + ``_replace_voice_entry``.  This models the author's
    # mental model of "I approved this cast as a unit" instead of forcing the
    # machine to re-derive it from per-entry state every chapter.
    confirmed: bool = Field(
        default=False,
        description="作者是否已确认整支配音团队可用于自动配音。"
        "True 隐含所有 entries 已 ready+approved+未过期且 provider 一致。",
    )
    confirmed_at: datetime | None = Field(
        default=None,
        description="团队确认时间；任何 entry 变更或就绪度回退后自动置空。",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _invalidate_confirmation_when_team_unready(self) -> "VoiceTeamContract":
        """Auto-reset ``confirmed`` if any entry is no longer reusable.

        This is the safety backstop: ``_replace_voice_entry`` resets the flag
        explicitly on every mutation, but a voice can also expire on the clock
        (``activation_deadline`` / ``expires_at``) without any write.  The
        validator catches that drift on load so a stale confirmation can never
        unlock synthesis with an expired voice.  VersionedSchema is non-frozen,
        so direct assignment is safe here.
        """
        if not self.confirmed:
            return self
        provider_ok = bool(self.narrator_voice_id) and bool(self.entries)
        if provider_ok:
            for entry in self.entries:
                if (
                    entry.provider != self.default_provider
                    or not entry.is_ready
                    or entry.is_expired
                ):
                    provider_ok = False
                    break
        if not provider_ok:
            self.confirmed = False
            self.confirmed_at = None
        return self

    def get_entry(self, character_id: str) -> VoiceCastEntry | None:
        """Find a voice cast entry by character ID."""
        for entry in self.entries:
            if entry.character_id == character_id:
                return entry
        return None

    def get_ready_entries(self) -> list[VoiceCastEntry]:
        """Return all entries with ready voice IDs."""
        return [e for e in self.entries if e.is_ready and not e.is_expired]


# ─── Paralinguistic & Transition Schemas ─────────────────────────────────────


class ParalinguisticTag(VersionedSchema):
    """副语言标注：停顿、气息、笑声、哽咽等非文字声音标记。"""

    tag_type: str = Field(
        min_length=1,
        description="标记类型: pause | breath | laugh | sigh | stutter | emphasis | choke | hum",
    )
    position: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="句内位置 (0.0=句首, 1.0=句尾)",
    )
    duration_ms: int = Field(default=0, ge=0, description="持续时间（毫秒）")
    intensity: float = Field(default=1.0, ge=0.0, le=1.0, description="强度 (0.0=极轻, 1.0=强烈)")
    description: str = Field(default="", description="人类可读描述（如 '深吸一口气'）")


class SceneTransition(VersionedSchema):
    """场景转换标记：时间跳转、地点切换、视角切换等。"""

    transition_type: str = Field(
        min_length=1,
        description="转换类型: time_skip | location_change | pov_shift | tone_shift | flashback",
    )
    label: str = Field(default="", description="转换标签（如 '三天后'、'城楼上'）")
    gap_ms: int = Field(default=1000, ge=0, description="建议停顿时长（毫秒）")
    from_context: str = Field(default="", description="转换前场景简述")
    to_context: str = Field(default="", description="转换后场景简述")


class SpeedCurvePoint(VersionedSchema):
    """句内语速变化点。"""

    position: float = Field(ge=0.0, le=1.0, description="句内位置")
    factor: float = Field(default=1.0, ge=0.5, le=2.0, description="语速倍率")


class VoiceEffectControls(BaseModel):
    """平台可选声音效果器。

    MiniMax 可原生消费全部字段，但 ``pitch``、``intensity``、``timbre``
    会改写声线本体，只应由用户显式选择；AI 表达规划不得自动驱动它们。
    其他平台在明确映射前忽略，不会把“已写入脚本”误报为“已执行”。
    """

    pitch: int = Field(default=0, ge=-100, le=100, description="效果器音高")
    intensity: int = Field(default=0, ge=-100, le=100, description="声线力度/柔和度")
    timbre: int = Field(default=0, ge=-100, le=100, description="浑厚/清脆音色调整")
    sound_effects: Literal[
        "",
        "spacious_echo",
        "auditorium_echo",
        "lofi_telephone",
        "robotic",
    ] = Field(default="", description="空间/设备声效")


class ProviderTakeEvidence(BaseModel):
    """Normalized provider evidence consumed by the shared quality pipeline."""

    trace_id: str = ""
    provider_status: str = ""
    reported_duration_ms: int = Field(default=0, ge=0)
    sample_rate: int = Field(default=0, ge=0)
    audio_size_bytes: int = Field(default=0, ge=0)
    text_units: int = Field(default=0, ge=0)
    invisible_character_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    native_timing_requested: bool = False
    native_timing_granularity: str = ""
    provider_extension: dict[str, Any] = Field(
        default_factory=dict,
        description="仅供审计的平台命名空间；公共质量逻辑不读取。",
    )


class LanguageRun(BaseModel):
    """One language span inside a potentially mixed-language speech segment."""

    language: str = Field(default="auto", min_length=2, description="语言代码，如 zh/en/ja/yue")
    text: str = Field(min_length=1)
    start_char: int = Field(default=0, ge=0)
    end_char: int = Field(default=0, ge=0)
    pronunciation_hint: str = Field(default="", description="本语言片段的读音或转写提示")

    @model_validator(mode="after")
    def normalize_range(self) -> "LanguageRun":
        if self.end_char == 0:
            self.end_char = self.start_char + len(self.text)
        if self.end_char < self.start_char:
            raise ValueError("LanguageRun.end_char must be >= start_char")
        return self


class VocalTag(BaseModel):
    """Extensible, vendor-neutral performance tag with auditable provenance."""

    category: Literal[
        "emotion",
        "delivery",
        "prosody",
        "articulation",
        "paralinguistic",
        "spatial",
        "pronunciation",
        "language",
    ]
    key: str = Field(min_length=1)
    value: str = Field(default="")
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    start_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    end_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    source: Literal["llm", "manual", "migrated", "system"] = "llm"
    required: bool = Field(default=False, description="不支持时是否必须阻止正式成片")


class VocalDirection(BaseModel):
    """Provider-neutral voice direction consumed through capability mappings."""

    delivery_style: Literal[
        "natural",
        "intimate",
        "narrative",
        "conversational",
        "dramatic",
        "broadcast",
    ] = "natural"
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    articulation: float = Field(default=0.6, ge=0.0, le=1.0)
    breathiness: float = Field(default=0.2, ge=0.0, le=1.0)
    resonance: float = Field(default=0.0, ge=-1.0, le=1.0, description="负值偏暗，正值偏亮")
    tension: float = Field(default=0.3, ge=0.0, le=1.0)
    intimacy: float = Field(default=0.5, ge=0.0, le=1.0)
    intent: str = Field(default="", description="这一句希望听众感受到的行动意图")


# ─── Dubbing Script Schemas ───────────────────────────────────────────────────


class DubbingSegment(VersionedSchema):
    """A single segment in a dubbing script."""

    segment_index: int = Field(ge=0, description="片段序号")
    segment_type: SegmentType = Field(description="片段类型")
    character_id: str = Field(default="", description="角色 ID（对白时必填）")
    character_name: str = Field(default="", description="角色显示名称")
    text: str = Field(min_length=1, description="文本内容（可含 DML 标签）")
    spoken_text: str = Field(
        default="",
        description=(
            "实际送入 TTS 的受限口语改写；为空时朗读 text。"
            "text 始终保留正文原文供溯源；对齐与字幕使用实际朗读文本。"
        ),
    )
    emotion: EmotionTag = Field(default=EmotionTag.NEUTRAL, description="主情绪标签")
    sub_emotion: EmotionTag | None = Field(default=None, description="次情绪标签（情感渐变）")
    emotion_intensity: float = Field(
        default=0.5, ge=0.0, le=1.0, description="情绪强度 (0=平淡, 1=极致)"
    )
    tone_hint: str = Field(default="", description="语气提示（讽刺/恳求/命令/试探等）")
    speed_override: float | None = Field(default=None, ge=0.5, le=2.0, description="语速覆盖")
    vol_override: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="音量倍率覆盖；1.0 为原始音量",
    )
    pitch_override: int | None = Field(default=None, ge=-12, le=12, description="音调覆盖")
    dml_tags: list[str] = Field(default_factory=list, description="DML 标记列表")
    start_ms: int = Field(default=0, ge=0, description="起始时间（毫秒）")
    end_ms: int = Field(default=0, ge=0, description="结束时间（毫秒）")
    source_paragraph: int = Field(default=0, ge=0, description="源段落索引")
    # ── 增强字段 ──────────────────────────────────────────────────────────────
    stress_words: list[str] = Field(default_factory=list, description="重音词列表")
    paralinguistic_tags: list[ParalinguisticTag] = Field(
        default_factory=list, description="副语言标注（停顿/气息/笑声等）"
    )
    speed_curve: list[SpeedCurvePoint] = Field(
        default_factory=list,
        description=(
            "句内语速变化曲线。"
            "[DEPRECATED] LLM 产出不稳定且 synthesize_audio_step 零消费，"
            "当前在 script_review 阶段被清空。待实现消费逻辑后启用。"
        ),
    )
    scene_context: str = Field(default="", description="场景简述（供 LLM/配音员参考）")
    transition: SceneTransition | None = Field(default=None, description="场景转换标记")
    narrator_distance: str = Field(
        default="",
        description="旁白叙述距离: close | medium | distant（仅旁白段有效）",
    )
    pronunciation_overrides: list[str] = Field(
        default_factory=list,
        description="发音覆盖，例如 '燕少飞/(yan4)(shao3)(fei1)'",
    )
    language_boost: str = Field(
        default="auto",
        description="旧版 Provider 语种增强字段；新流程优先读取 language_code/language_runs。",
    )
    language_code: str = Field(
        default="auto",
        description="供应商无关的主要语言代码；混读时由 language_runs 细分。",
    )
    language_runs: list[LanguageRun] = Field(
        default_factory=list,
        description="句内多语言范围；为空表示整段使用 language_code。",
    )
    vocal_direction: VocalDirection = Field(
        default_factory=VocalDirection,
        description="声腔导演参数；适配器按能力原生消费或安全降级。",
    )
    vocal_tags: list[VocalTag] = Field(
        default_factory=list,
        description="可扩展声腔标签；不将供应商私有标签写入主流程。",
    )
    platform_extensions: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "按平台命名空间保存的原生高级字段，例如 qwen3.instruct 或 "
            "minimax.language_boost；主流程只透传给对应适配器。"
        ),
    )
    voice_effect: VoiceEffectControls = Field(
        default_factory=VoiceEffectControls,
        description="兼容旧脚本的效果器参数；新 UI 将其作为可映射的空间效果。",
    )
    segment_uid: str = Field(
        default="",
        description=(
            "内容寻址身份；推导自 text/character/emotion/overrides 等影响"
            "TTS 输出的字段，不含位置信息（segment_index/source_paragraph）。"
            "validator 每次校验都会按当前内容重算，保证永不陈旧；"
            "持久化值仅供诊断，向后兼容无此字段的旧脚本。"
        ),
    )

    @model_validator(mode="after")
    def _ensure_segment_uid(self) -> "DubbingSegment":
        """Recompute the content-addressed identity on every validation.

        Recomputing unconditionally (instead of only when empty) guarantees
        the uid can never go stale when a segment's fields are edited
        externally and the script is re-loaded.  ``model_copy(update=...)``
        skips validation entirely, so in-place editors must refresh via
        ``refresh_segment_uid`` — see script_review.py.
        """
        from novel_forge.tts.script_integrity import compute_segment_uid

        object.__setattr__(self, "segment_uid", compute_segment_uid(self))
        return self

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def synthesis_text(self) -> str:
        """Return the approved performance wording without losing source text.

        Returns empty string for segments whose text is purely non-speakable
        (scene breaks like ``---``, ``——``, ``***``).  This ensures the
        synthesis layer and UI never attempt to vocalize separator markers.
        """
        spoken = self.spoken_text.strip()
        if spoken:
            return spoken
        text = self.text.strip()
        if not text:
            return ""
        # Fast check: if text has any word/CJK character, it's speakable.
        import re as _re

        if _re.search(r"[\w\u3400-\u9fff]", text):
            return text
        # Pure punctuation / scene break → return empty to signal skip.
        return ""


class BGMTiming(VersionedSchema):
    """Background music timing information."""

    track_name: str = Field(default="", description="曲目名称或描述")
    mood: str = Field(default="", description="情绪氛围")
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    start_segment_index: int | None = Field(
        default=None,
        ge=0,
        description="首选的对白/旁白片段锚点；合成后换算为真实时间",
    )
    end_segment_index: int | None = Field(
        default=None,
        ge=0,
        description="结束片段锚点；为空时延续到章节末尾",
    )
    volume: float = Field(default=0.3, ge=0.0, le=1.0, description="BGM 音量")
    fade_in_ms: int = Field(default=1200, ge=0)
    fade_out_ms: int = Field(default=1800, ge=0)
    ducking_db: float = Field(default=8.0, ge=0.0, le=30.0, description="对白下压分贝")
    loop: bool = Field(default=True)
    narrative_role: Literal["underscore", "tension", "transition", "climax", "resolution"] = (
        "underscore"
    )
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    mood_tags: list[str] = Field(
        default_factory=list,
        description="语义情绪标签，用于跨章复用匹配（如：紧张、温馨、悬疑）",
    )
    narrative_arc_position: str = Field(
        default="underscore",
        description="叙事弧位置：opening/rising/climax/falling/resolution/transition",
    )
    reuse_hint: str = Field(
        default="",
        description="优先复用的资产 ID 或稳定标签",
    )


class BGMGap(BaseModel):
    """未覆盖的 BGM 需求，供用户在 Voice Studio 中决策。"""

    mood_tags: list[str] = Field(default_factory=list)
    narrative_role: str = Field(default="underscore")
    suggested_direction: str = Field(default="", description="建议的音乐方向描述")
    estimated_duration_ms: int = Field(default=60_000, ge=5_000, le=600_000)
    chapter_number: int = Field(default=0, ge=0)


class SFXCue(VersionedSchema):
    """Sound effect cue."""

    effect_name: str = Field(min_length=1, description="音效名称")
    description: str = Field(default="", description="音效描述")
    trigger_ms: int = Field(default=0, ge=0, description="触发时间点")
    trigger_segment_index: int | None = Field(
        default=None,
        ge=0,
        description="首选的剧情片段锚点；合成后换算为真实时间",
    )
    offset_ms: int = Field(default=0, ge=-30_000, le=30_000)
    duration_ms: int = Field(default=0, ge=0)
    volume: float = Field(default=0.5, ge=0.0, le=1.0)
    allow_dialogue_overlap: bool = Field(
        default=False,
        description="只有剧情必须与台词同时发生时才开启。",
    )
    maximum_timing_shift_ms: int = Field(default=1000, ge=0, le=5000)
    narrative_priority: int = Field(default=70, ge=0, le=100)


class SoundscapeCue(VersionedSchema):
    """A persistent environmental layer such as rain, wind, or a busy market."""

    name: str = Field(min_length=1, description="环境声名称")
    description: str = Field(default="", description="叙事与听觉意图")
    asset_hint: str = Field(default="", description="可选的声音资产 ID 或检索关键词")
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0, description="0 表示延续至章节末尾")
    start_segment_index: int | None = Field(default=None, ge=0)
    end_segment_index: int | None = Field(default=None, ge=0)
    volume: float = Field(default=0.18, ge=0.0, le=1.0)
    fade_in_ms: int = Field(default=600, ge=0)
    fade_out_ms: int = Field(default=800, ge=0)
    loop: bool = Field(default=True, description="素材不足时是否循环铺满区间")
    ducking_db: float = Field(default=5.0, ge=0.0, le=30.0, description="对白下压分贝")
    density: float = Field(default=0.5, ge=0.0, le=1.0)


class AudioCreativeMixPolicy(BaseModel):
    """Project-wide foreground hierarchy and cue-density limits."""

    max_bgm_volume: float = Field(default=0.24, ge=0.0, le=1.0)
    min_bgm_ducking_db: float = Field(default=9.0, ge=0.0, le=30.0)
    minimum_bgm_fade_ms: int = Field(default=1000, ge=0, le=10_000)
    max_soundscape_volume: float = Field(default=0.18, ge=0.0, le=1.0)
    min_soundscape_ducking_db: float = Field(default=6.0, ge=0.0, le=30.0)
    minimum_soundscape_fade_ms: int = Field(default=500, ge=0, le=10_000)
    max_sfx_volume: float = Field(default=0.65, ge=0.0, le=1.0)
    max_dialogue_overlap_sfx_volume: float = Field(default=0.45, ge=0.0, le=1.0)
    max_bgm_cues_per_scene: int = Field(default=1, ge=0, le=4)
    max_soundscapes_per_scene: int = Field(default=1, ge=0, le=3)
    max_sfx_cues_per_scene: int = Field(default=3, ge=0, le=12)
    minimum_dialogue_overlap_priority: int = Field(default=90, ge=0, le=100)


class AudioCreativeBible(VersionedSchema):
    """Stable sound identity shared by every chapter and every provider route."""

    project_id: str = Field(min_length=1)
    title: str = ""
    genre: str = ""
    overall_aesthetic: list[str] = Field(default_factory=list)
    music_identity: list[str] = Field(default_factory=list)
    soundscape_identity: list[str] = Field(default_factory=list)
    location_sound_signatures: dict[str, list[str]] = Field(default_factory=dict)
    motif_directions: dict[str, str] = Field(default_factory=dict)
    prohibited_patterns: list[str] = Field(default_factory=list)
    mix_policy: AudioCreativeMixPolicy = Field(default_factory=AudioCreativeMixPolicy)
    source_fingerprint: str = ""
    locked: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DubbingStyleProfile(VersionedSchema):
    """Abstract, non-copying performance style learned from a reference script.

    The source script itself is deliberately not persisted in this artifact.  A
    content hash is enough for cache invalidation while the extracted rules can
    safely guide later chapters without leaking reference wording into prompts.
    """

    profile_id: str = Field(min_length=1, description="稳定风格画像 ID")
    source_name: str = Field(default="", description="用户可识别的参考脚本名称")
    source_hash: str = Field(min_length=16, max_length=64, description="参考脚本内容指纹")
    language: str = Field(default="zh", description="参考脚本主要语言")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    analysis_mode: Literal["hybrid", "deterministic"] = "deterministic"
    narration_traits: list[str] = Field(default_factory=list)
    dialogue_traits: list[str] = Field(default_factory=list)
    rhythm_rules: list[str] = Field(default_factory=list)
    pause_rules: list[str] = Field(default_factory=list)
    performance_direction_rules: list[str] = Field(default_factory=list)
    sound_design_rules: list[str] = Field(default_factory=list)
    forbidden_tendencies: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SoundAsset(VersionedSchema):
    """A local sound asset available to the chapter mixer.

    Imported and generated files share this contract.  Generation provenance is
    deliberately persisted with the asset, rather than only with a chapter
    result, so an author can audit or replace a sound years after it was first
    used.
    """

    asset_id: str = Field(min_length=1, description="稳定资产 ID")
    kind: Literal["soundscape", "bgm", "sfx"]
    display_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1, description="相对于 tts/assets 的文件路径")
    tags: list[str] = Field(default_factory=list, description="检索标签")
    loopable: bool = Field(default=False)
    default_volume: float = Field(default=0.25, ge=0.0, le=1.0)
    license_note: str = Field(default="", description="授权来源或使用备注")
    commercial_use_status: Literal["cleared", "review_required", "restricted"] = Field(
        default="review_required",
        description=(
            "商业使用结论：cleared=已人工确认可商用，"
            "review_required=尚未确认，restricted=不得用于商业成片。"
        ),
    )
    commercial_use_reviewed_at: datetime | None = Field(
        default=None,
        description="最后一次人工确认商用权利结论的时间。",
    )
    source: Literal["imported", "generated", "manual"] = Field(
        default="imported",
        description="资产来源：作者导入、生成服务或人工登记。",
    )
    approval_status: Literal["approved", "pending", "rejected"] = Field(
        default="approved",
        description="待审核生成资产不会进入正式章节混音。",
    )
    generation_provider: str = Field(default="", description="生成 Provider 标识。")
    generation_model: str = Field(default="", description="生成模型标识与版本。")
    generation_request_hash: str = Field(
        default="",
        description="用于缓存、复现和追溯的生成请求指纹。",
    )
    generation_output_hash: str = Field(
        default="",
        description="生成音频字节的 SHA-256，用于完整性与执行审计。",
    )
    generation_route_plugin_id: str = Field(default="")
    generation_route_plugin_version: str = Field(default="")
    generation_route_endpoint: str = Field(default="")
    generation_prompt: str = Field(default="", description="实际提交的可审计生成提示词。")
    generated_at: datetime | None = Field(default=None, description="生成完成时间。")
    # Compliance/audit provenance. Defaults keep existing sound_library.json
    # payloads valid (extra="forbid" requires new fields to be optional).
    generation_repository_id: str = Field(
        default="",
        description="模型权重仓库标识（HF/org 等），用于 license 与权重版本追溯。",
    )
    aigc_watermark: bool = Field(
        default=False,
        description="该生成资产是否携带 AIGC 水印（合规传播追溯用）。",
    )
    scope: Literal["project", "application"] = Field(
        default="project",
        description="资产归属：项目专属或发布到应用级可复用资源库。",
    )


class SoundLibraryManifest(VersionedSchema):
    """Versioned catalogue of BGM, SFX, and ambience assets for one library scope."""

    assets: list[SoundAsset] = Field(default_factory=list)


class SoundCueResolution(VersionedSchema):
    """Auditable result of matching one generated cue to one local asset."""

    cue_kind: Literal["soundscape", "bgm", "sfx"]
    cue_index: int = Field(ge=0)
    cue_label: str = Field(default="")
    status: Literal["matched", "missing", "invalid"]
    asset_id: str = Field(default="")
    relative_path: str = Field(default="")
    asset_scope: Literal["project", "application"] = "project"
    reason: str = Field(default="")


class ChapterSoundResolutionReport(VersionedSchema):
    """Persisted cue-to-asset resolution report for one chapter mix."""

    chapter_number: int = Field(ge=1)
    resolutions: list[SoundCueResolution] = Field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(item.status == "matched" for item in self.resolutions)

    @property
    def unresolved_count(self) -> int:
        return sum(item.status != "matched" for item in self.resolutions)


class DubbingScript(VersionedSchema):
    """Complete dubbing script for a chapter."""

    chapter_number: int = Field(ge=1, description="章节号")
    segments: list[DubbingSegment] = Field(default_factory=list)
    bgm_suggestions: list[BGMTiming] = Field(default_factory=list)
    sfx_cues: list[SFXCue] = Field(default_factory=list)
    soundscapes: list[SoundscapeCue] = Field(default_factory=list)
    scene_transitions: list[SceneTransition] = Field(
        default_factory=list, description="章节内场景转换列表"
    )
    total_estimated_duration_ms: int = Field(default=0, ge=0, description="预估总时长")
    script_hash: str = Field(default="", description="脚本内容哈希（用于断点续传检测）")
    source_text_hash: str = Field(default="", description="源文本哈希")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def dialogue_segments(self) -> list[DubbingSegment]:
        return [s for s in self.segments if s.segment_type == SegmentType.DIALOGUE]

    @property
    def narration_segments(self) -> list[DubbingSegment]:
        return [s for s in self.segments if s.segment_type == SegmentType.NARRATION]


# ─── Synthesis Result Schemas ─────────────────────────────────────────────────


class SynthesisResult(VersionedSchema):
    """Result of synthesizing a single dubbing segment."""

    segment_index: int = Field(ge=0, description="对应脚本片段序号")
    audio_path: str = Field(default="", description="生成的音频文件路径")
    duration_ms: int = Field(default=0, ge=0, description="实际音频时长")
    status: SynthesisStatus = Field(default=SynthesisStatus.PENDING)
    provider: TTSProvider = Field(default=TTSProvider.MOCK)
    model_id: str = Field(default="", description="使用的模型 ID")
    voice_id: str = Field(default="", description="使用的音色 ID")
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    error_message: str = Field(default="", description="失败时的错误信息")
    failure_kind: str = Field(default="", description="稳定的供应商/内部故障分类")
    retryable: bool = Field(default=False, description="该失败是否适合原请求重试")
    retry_after_s: float = Field(default=0.0, ge=0.0, description="建议等待后重试的秒数")
    retry_count: int = Field(default=0, ge=0)
    request_hash: str = Field(
        default="",
        description="合成请求指纹，用于断点恢复时确认音频仍对应当前脚本/音色/参数",
    )
    output_hash: str = Field(
        default="",
        description="实际音频字节 SHA-256，用于复现、去重与供应商响应审计",
    )
    requested_voice_id: str = Field(
        default="",
        description="发送给平台的音色 ID；与 voice_id 分开保存以检测平台静默回退",
    )
    route_plugin_id: str = Field(default="")
    route_plugin_version: str = Field(default="")
    route_endpoint: str = Field(default="")
    fallback_reason: str = Field(default="")
    audio_data: bytes = Field(
        default=b"",
        exclude=True,
        repr=False,
        description="进程内音频载荷；仅供合成池交接，禁止写入 JSON 检查点",
    )
    used_fallback: bool = Field(
        default=False,
        description="是否由备用 TTS 适配器完成合成",
    )
    quality_passed: bool = Field(default=True, description="片段合成后客观质量门是否通过")
    quality_warnings: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="供应商 trace、音频参数和可审计原生响应摘要",
    )
    take_evidence: ProviderTakeEvidence = Field(default_factory=ProviderTakeEvidence)


class SegmentTakeVersion(VersionedSchema):
    """One isolated Voice Room audition/accepted take."""

    take_id: str = Field(min_length=1)
    chapter_number: int = Field(ge=1)
    segment_index: int = Field(ge=0)
    status: TakeReviewStatus = TakeReviewStatus.CANDIDATE
    segment: DubbingSegment
    segment_result: SynthesisResult
    source_script_hash: str = ""
    voice_team_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_at: datetime | None = None
    note: str = ""


class ChapterTakeManifest(VersionedSchema):
    """Durable Voice Room drafts and take history for one chapter."""

    chapter_number: int = Field(ge=1)
    source_script_hash: str = ""
    drafts: dict[str, DubbingSegment] = Field(default_factory=dict)
    takes: list[SegmentTakeVersion] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MixRenderEventResult(BaseModel):
    """Auditable proof that one planned mix event reached the rendered master."""

    event_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    bus: Literal["voice", "bgm", "soundscape", "sfx"]
    status: Literal["rendered", "failed"]
    planned_start_ms: int = Field(ge=0)
    planned_end_ms: int = Field(ge=0)
    actual_start_ms: int | None = Field(default=None, ge=0)
    actual_end_ms: int | None = Field(default=None, ge=0)
    gain_db: float = 0.0
    source_path: str = ""
    source_hash: str = ""
    reason: str = ""


class MixRenderReport(VersionedSchema):
    """Result of rendering a complete MixPlan into one mastered artifact."""

    chapter_number: int = Field(ge=1)
    renderer_plugin_id: str = ""
    status: Literal["completed", "failed"] = "failed"
    passed: bool = False
    mastering_succeeded: bool = False
    planned_event_count: int = Field(default=0, ge=0)
    rendered_event_count: int = Field(default=0, ge=0)
    failed_event_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    output_path: str = ""
    output_hash: str = ""
    events: list[MixRenderEventResult] = Field(default_factory=list)
    error: str = ""
    # Per-bus stem export paths (voice/bed/sfx). Empty when stem export is
    # disabled or the renderer didn't produce stems. Enables author review /
    # re-mix without re-running synthesis.
    stem_paths: dict[str, str] = Field(default_factory=dict)
    # Loudnorm analysis-pass measurements (input_i / input_lra / input_tp /
    # input_thresh / target_offset).  Persisted by the two-pass master so the
    # post-render quality gate can reuse them instead of decoding the master a
    # second time (P2-1).
    measured_loudness: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_render_evidence(self) -> "MixRenderReport":
        rendered = sum(item.status == "rendered" for item in self.events)
        failed = sum(item.status == "failed" for item in self.events)
        if self.planned_event_count != len(self.events):
            raise ValueError("planned_event_count must equal the number of render events")
        if self.rendered_event_count != rendered or self.failed_event_count != failed:
            raise ValueError("render event counts do not match event statuses")
        complete = (
            bool(self.events)
            and failed == 0
            and rendered == len(self.events)
            and self.mastering_succeeded
            and bool(self.output_path)
            and bool(self.output_hash)
        )
        if self.passed != complete:
            raise ValueError("passed must reflect complete event and mastering evidence")
        if self.status != ("completed" if complete else "failed"):
            raise ValueError("status must reflect render completion")
        return self


class ChapterAudioResult(VersionedSchema):
    """Complete audio result for a chapter."""

    chapter_number: int = Field(ge=1)
    script: DubbingScript = Field(description="配音脚本")
    segment_results: list[SynthesisResult] = Field(default_factory=list)
    assembled_audio_path: str = Field(default="", description="组装后的完整音频路径")
    subtitle_path: str = Field(default="", description="字幕文件路径")
    total_duration_ms: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    is_complete: bool = Field(default=False, description="是否所有段都成功合成")
    delivery_ready: bool = Field(
        default=False,
        description="人声、成片质量和剧情声音资产均满足最终交付条件",
    )
    delivery_blocking_reasons: list[str] = Field(
        default_factory=list,
        description="阻止最终交付的稳定机器可读原因",
    )
    mix_render_report: MixRenderReport | None = Field(
        default=None,
        description="正式多轨混音计划的逐事件实际渲染证据",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── Progress / Checkpoint Schemas ────────────────────────────────────────────


class ReusableTakeRecord(VersionedSchema):
    """A synthesis result keyed by content identity, reusable across script edits.

    Unlike ``SynthesisResult`` which is keyed by positional ``segment_index``,
    this record is keyed by the content-addressed ``segment_uid``.  When a
    script is regenerated and unchanged segments shift position, their audio
    can be reused from this index.
    """

    segment_uid: str = Field(min_length=1, description="片段内容身份")
    segment_index_at_creation: int = Field(
        ge=0, description="创建时的位置索引（仅诊断，不参与复用判定）"
    )
    audio_path: str = Field(min_length=1, description="音频文件路径")
    duration_ms: int = Field(ge=0)
    request_hash: str = Field(default="", description="完整请求指纹（含 voice/provider/参数）")
    voice_id: str = Field(default="")
    output_hash: str = Field(default="", description="音频字节 SHA-256")
    cost_usd: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TTSProgressState(VersionedSchema):
    """Checkpoint state for TTS resume-from-interruption.

    Mirrors the ReviewProgressState pattern used in chapter pipeline.
    """

    chapter_number: int = Field(ge=1)
    voice_team_done: bool = Field(default=False)
    script_done: bool = Field(default=False)
    synthesis_done: bool = Field(default=False)
    assembly_done: bool = Field(default=False)
    voice_team_hash: str = Field(default="", description="配音团队哈希")
    script_hash: str = Field(default="", description="脚本哈希")
    provider: TTSProvider = Field(default=TTSProvider.MOCK, description="上次合成使用的平台")
    completed_segments: list[int] = Field(
        default_factory=list,
        description="已完成合成的片段索引",
    )
    completed_segment_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="片段索引到合成请求指纹的映射，用于安全恢复",
    )
    failed_segments: list[int] = Field(
        default_factory=list,
        description="最近一次合成失败的片段索引，供续跑与诊断使用",
    )
    failed_segment_errors: dict[str, str] = Field(
        default_factory=dict,
        description="失败片段的错误摘要，键为片段索引",
    )
    segment_results: list[SynthesisResult] = Field(
        default_factory=list,
        description="最近一次完整合成结果快照；音频二进制不会序列化",
    )
    reusable_takes: dict[str, ReusableTakeRecord] = Field(
        default_factory=dict,
        description=(
            "按 segment_uid 索引的可复用音频成果；与 segment_index 解耦。"
            "脚本重新生成后，未改动的台词即使位置变化仍可通过此索引复用。"
        ),
    )
    last_error: str = Field(default="")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_checkpoint(cls, data: Any) -> Any:
        """Ensure ``reusable_takes`` exists in older checkpoint payloads."""
        if isinstance(data, dict) and "reusable_takes" not in data:
            data["reusable_takes"] = {}
        return data

    @property
    def is_complete(self) -> bool:
        return all(
            [
                self.voice_team_done,
                self.script_done,
                self.synthesis_done,
                self.assembly_done,
            ]
        )


# ─── TTS Gateway Types ────────────────────────────────────────────────────────


class TTSRequest(BaseModel):
    """Request to a TTS provider."""

    text: str = Field(min_length=1, description="待合成文本")
    voice_id: str = Field(default="", description="目标音色 ID")
    model_id: str = Field(default="", description="TTS 模型 ID")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="语速")
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="音量")
    pitch: int = Field(default=0, ge=-12, le=12, description="音调偏移")
    output_format: str = Field(default="mp3", description="输出格式: mp3/pcm/flac/wav")
    sample_rate: int = Field(default=32000, description="采样率")
    bitrate: int = Field(default=128000, description="MP3 比特率")
    channel: int = Field(default=1, ge=1, le=2, description="声道数")
    emotion: str = Field(default="neutral", description="主情绪标签")
    emotion_tags: list[str] = Field(default_factory=list, description="情绪/语气词标签")
    pronunciation_overrides: list[str] = Field(default_factory=list, description="发音字典")
    language_boost: str = Field(default="auto", description="语言/方言增强")
    voice_effect: VoiceEffectControls = Field(default_factory=VoiceEffectControls)
    provider: TTSProvider = Field(default=TTSProvider.MOCK)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TTSResponse(BaseModel):
    """Response from a TTS provider."""

    audio_data: bytes = Field(default=b"", description="音频二进制数据")
    audio_path: str = Field(default="", description="保存的音频文件路径")
    duration_ms: int = Field(default=0, ge=0)
    model_id: str = Field(default="")
    voice_id: str = Field(default="")
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    content_type: str = Field(default="audio/mpeg", description="MIME 类型")
    audio_format: str = Field(default="", description="实际返回音频格式；为空时沿用请求格式")
    metadata: dict[str, Any] = Field(default_factory=dict)
    take_evidence: ProviderTakeEvidence = Field(default_factory=ProviderTakeEvidence)


class VoiceCloneRequest(BaseModel):
    """Request to clone a voice from reference audio."""

    voice_id: str = Field(min_length=1, description="目标音色 ID（用户指定）")
    file_id: str = Field(min_length=1, description="参考音频文件 ID")
    model_id: str = Field(default="", description="创建音色时绑定的目标 TTS 模型")
    language: str = Field(default="zh", description="参考音频主语种提示")
    clone_prompt: str = Field(default="", description="示例文本/描述")
    reference_transcript: str = Field(
        default="",
        description="参考音频逐字转写；为空时部分本地模型只能降级为声纹向量克隆",
    )
    authorized: bool = Field(
        default=False,
        description="调用方已确认拥有参考音频说话人的明确授权",
    )
    provider: TTSProvider = Field(default=TTSProvider.MOCK)


class VoiceCloneResponse(BaseModel):
    """Response from a voice clone operation."""

    voice_id: str = Field(default="", description="克隆后的音色 ID")
    model_id: str = Field(default="", description="该音色绑定的目标 TTS 模型")
    provider: TTSProvider = Field(default=TTSProvider.MOCK)
    status: VoiceCloneStatus = Field(default=VoiceCloneStatus.PENDING)
    expires_at: datetime | None = None
    activation_deadline: datetime | None = None
    message: str = Field(default="")


class VoiceDesignRequest(BaseModel):
    """Request to design a new voice from text description."""

    description: str = Field(min_length=1, description="音色描述文本")
    model_id: str = Field(default="", description="设计音色时绑定的目标 TTS 模型")
    preview_text: str = Field(
        default="你好，这是根据角色特质生成的专属音色，请试听声音是否符合角色。",
        min_length=1,
        max_length=500,
        description="音色设计接口用于生成试听音频的文本",
    )
    language: str = Field(default="Auto", description="试听文本主语种；平台可选择自动识别")
    provider: TTSProvider = Field(default=TTSProvider.MOCK)


class VoiceDesignResponse(BaseModel):
    """Response from a voice design operation."""

    voice_id: str = Field(default="")
    model_id: str = Field(default="", description="该音色绑定的目标 TTS 模型")
    provider: TTSProvider = Field(default=TTSProvider.MOCK)
    preview_audio_data: bytes = Field(default=b"")
    preview_audio_format: str = Field(default="mp3", description="试听音频格式")
    status: VoiceCloneStatus = Field(default=VoiceCloneStatus.PENDING)
    expires_at: datetime | None = Field(
        default=None, description="设计音色过期时间（如平台有 TTL）"
    )
    activation_deadline: datetime | None = Field(
        default=None,
        description="设计音色首次正式合成的激活截止时间；激活后清空",
    )
    message: str = Field(default="")


# ─── Narrator Voice Profile ───────────────────────────────────────────────────


class NarratorVoiceProfile(VersionedSchema):
    """旁白声音画像：由 LLM 根据全书大纲/体裁/基调/风格档案分析生成。

    用于替代静态 tts_narrator_voice_id 配置，实现旁白声音与作品风格匹配。
    """

    voice_id: str = Field(default="", description="旁白音色 ID")
    model_id: str = Field(
        default="",
        description="旁白音色绑定的 TTS 模型 ID；自定义音色切换模型前必须重建",
    )
    provider: TTSProvider = Field(default=TTSProvider.MOCK, description="TTS 平台")
    voice_source: Literal["system", "designed", "manual"] = Field(
        default="system",
        description="旁白音色来源：系统匹配/作品特征设计/人工指定",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="旁白设计音色的过期时间（部分平台有 TTL）",
    )
    activation_deadline: datetime | None = Field(
        default=None,
        description="旁白设计音色首次正式合成的激活截止时间；激活后清空",
    )
    voice_design_prompt: str = Field(
        default="",
        description="用于生成旁白专属音色的可审计设计简报",
    )
    fallback_voice_ids: dict[str, str] = Field(
        default_factory=dict,
        description="旁白跨平台回退所需的 provider→voice_id 映射",
    )
    identity_locked: bool = Field(default=False)
    identity_locked_at: datetime | None = Field(default=None)
    voice_type: str = Field(
        default="",
        description="音色类型描述（如 '磁性男声'、'知性女声'、'沧桑老者'）",
    )
    base_speed: float = Field(default=1.0, ge=0.5, le=2.0, description="基础语速")
    pitch_offset: int = Field(default=0, ge=-12, le=12, description="旁白音调半音偏移")
    vol_offset: float = Field(default=0.0, ge=-1.0, le=1.0, description="旁白音量倍率偏移")
    speed_range_low: float = Field(default=0.8, ge=0.5, le=2.0, description="语速下限")
    speed_range_high: float = Field(default=1.2, ge=0.5, le=2.0, description="语速上限")
    emotional_range: str = Field(
        default="moderate",
        description="情感表达范围: wide | moderate | restrained",
    )
    narration_distance: str = Field(
        default="medium",
        description="叙述距离: close(贴近角色) | medium | distant(全知视角)",
    )
    style_keywords: list[str] = Field(
        default_factory=list,
        description="风格关键词（如 ['沉稳', '文学感', '略带忧伤']）",
    )
    genre_adaptation: dict[str, float] = Field(
        default_factory=dict,
        description="体裁适配度（如 {'romance': 0.8, 'suspense': 0.9}）",
    )
    emotion_speed_modifiers: dict[str, float] = Field(
        default_factory=dict,
        description="情感→语速修正系数（如 {'sad': 0.85, 'tense': 1.15}）",
    )
    emotion_volume_modifiers: dict[str, float] = Field(
        default_factory=dict,
        description="情感→音量修正系数",
    )
    sample_narration_text: str = Field(
        default="",
        description="旁白试读文本（用于预生成旁白音色预览）",
    )
    notes: str = Field(default="", description="人工备注或 LLM 分析说明")

    @property
    def is_expired(self) -> bool:
        """Return whether a provider-managed narrator voice has expired."""
        now = datetime.now(timezone.utc)
        return bool(
            (self.expires_at is not None and now > self.expires_at)
            or (self.activation_deadline is not None and now > self.activation_deadline)
        )


# ─── TTS Voice Hints (shared by Init / Editorial / Chapter) ─────────────────


class TTSVoiceHints(BaseModel):
    """结构化 TTS 声音提示，由 Init / EditorialContract 阶段生成。

    供 TTS 模块在配音团队构建和脚本生成时直接消费，无需重新从文本推断。
    所有字段均为可选——缺失时 TTS 模块回退到关键词推断逻辑。
    """

    preferred_pitch: str = Field(
        default="",
        description="推荐音高: low / medium / high",
    )
    preferred_speed: str = Field(
        default="",
        description="推荐语速: slow / normal / fast",
    )
    emotional_range_tags: list[EmotionTag] = Field(
        default_factory=list,
        description="该角色常见情绪范围标签",
    )
    pronunciation_notes: list[str] = Field(
        default_factory=list,
        description="发音注意事项（如方言、古语、特殊读音）",
    )
    voice_texture: str = Field(
        default="",
        description="音色质感描述: 沙哑 / 清亮 / 浑厚 / 磁性 等",
    )


# ─── Chapter TTS Metadata (produced by Finalize, consumed by TTS) ────────────


class SceneEmotionAnnotation(BaseModel):
    """场景级情绪标注——从 ChapterPlan.scene_intents 确定性映射而来。"""

    scene_id: str = Field(default="", description="场景 ID")
    dominant_emotion: EmotionTag = Field(
        default=EmotionTag.NEUTRAL,
        description="场景主情绪",
    )
    emotion_intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="情绪强度 (0=平淡, 1=极致)",
    )
    emotion_shift: str = Field(
        default="",
        description="场景内情绪转变描述（如 '从紧张到释然'）",
    )


class PacingAnnotation(BaseModel):
    """节奏标注——从章节计划 / 追读力报告确定性提取。"""

    text_range: str = Field(
        default="",
        description="文本范围标识（如段落范围 'para_3-7'）",
    )
    pacing: str = Field(
        default="normal",
        description="节奏: slow / normal / fast / accelerating / decelerating",
    )
    reason: str = Field(default="", description="节奏判定原因")


class ToneShift(BaseModel):
    """旁白基调转换点。"""

    position: str = Field(default="", description="位置标识（如 scene_id 或段落范围）")
    from_tone: str = Field(default="", description="转换前基调")
    to_tone: str = Field(default="", description="转换后基调")
    trigger: str = Field(default="", description="触发转换的原因")


class ChapterTTSMetadata(VersionedSchema):
    """章节级 TTS 元数据，由章节 Pipeline 在 Finalize 阶段确定性生成。

    纯确定性映射（不调用 LLM），零额外 API 成本。
    所有字段均为 Optional/空默认——缺失时 TTS 模块回退到文本推断。
    """

    chapter_number: int = Field(
        default=0,
        ge=0,
        description="元数据所属章节；0 表示缺少来源身份的旧版数据",
    )
    source_text_hash: str = Field(
        default="",
        description="生成元数据时所消费终稿正文的紧凑哈希",
    )
    scene_emotion_map: list[SceneEmotionAnnotation] = Field(
        default_factory=list,
        description="场景→情绪映射（从 ChapterPlan.scene_intents 提取）",
    )
    character_emotion_trajectories: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict,
        description="角色情绪轨迹（character_name → 情绪变化列表）",
    )
    pacing_annotations: list[PacingAnnotation] = Field(
        default_factory=list,
        description="节奏标注（从章节计划或追读力报告提取）",
    )
    expression_channel_constraints: dict[str, int] = Field(
        default_factory=dict,
        description="表达通道约束（从 EditorialContract.expression_channel_budget 映射）",
    )
    narration_tone_progression: list[ToneShift] = Field(
        default_factory=list,
        description="旁白基调演进（从 ChapterPlan.emotional_arc 映射）",
    )


# ─── Emotion → TTS Parameter Mapping ─────────────────────────────────────────


class EmotionTTSMapping(BaseModel):
    """Mapping from emotion to TTS parameter offsets."""

    emotion: EmotionTag
    speed_offset: float = 0.0
    vol_offset: float = 0.0
    pitch_offset: int = 0
    voice_tags: list[str] = Field(default_factory=list)


# Default emotion → TTS parameter mappings
EMOTION_MAPPINGS: dict[EmotionTag, EmotionTTSMapping] = {
    EmotionTag.NEUTRAL: EmotionTTSMapping(
        emotion=EmotionTag.NEUTRAL, speed_offset=0.0, vol_offset=0.0, pitch_offset=0
    ),
    EmotionTag.HAPPY: EmotionTTSMapping(
        emotion=EmotionTag.HAPPY,
        speed_offset=0.1,
        vol_offset=0.1,
        pitch_offset=1,
        voice_tags=["(chuckle)", "(laughs)"],
    ),
    EmotionTag.SAD: EmotionTTSMapping(
        emotion=EmotionTag.SAD,
        speed_offset=-0.15,
        vol_offset=-0.1,
        pitch_offset=-2,
        voice_tags=["(sighs)"],
    ),
    EmotionTag.ANGRY: EmotionTTSMapping(
        emotion=EmotionTag.ANGRY,
        speed_offset=0.15,
        vol_offset=0.2,
        pitch_offset=2,
        voice_tags=[],
    ),
    EmotionTag.FEARFUL: EmotionTTSMapping(
        emotion=EmotionTag.FEARFUL,
        speed_offset=0.2,
        vol_offset=-0.1,
        pitch_offset=1,
        voice_tags=["(gasps)", "(breath)"],
    ),
    EmotionTag.SURPRISED: EmotionTTSMapping(
        emotion=EmotionTag.SURPRISED,
        speed_offset=0.1,
        vol_offset=0.15,
        pitch_offset=3,
        voice_tags=["(gasps)", "(inhale)"],
    ),
    EmotionTag.DISGUSTED: EmotionTTSMapping(
        emotion=EmotionTag.DISGUSTED,
        speed_offset=-0.05,
        vol_offset=0.1,
        pitch_offset=-1,
        voice_tags=["(groans)", "(hissing)"],
    ),
    EmotionTag.TENDER: EmotionTTSMapping(
        emotion=EmotionTag.TENDER,
        speed_offset=-0.2,
        vol_offset=-0.15,
        pitch_offset=-1,
        voice_tags=["(humming)", "(sighs)"],
    ),
    EmotionTag.MOCKING: EmotionTTSMapping(
        emotion=EmotionTag.MOCKING,
        speed_offset=0.05,
        vol_offset=0.05,
        pitch_offset=1,
        voice_tags=["(chuckle)", "(snorts)"],
    ),
    EmotionTag.WHISPER: EmotionTTSMapping(
        emotion=EmotionTag.WHISPER,
        speed_offset=-0.3,
        vol_offset=-0.3,
        pitch_offset=0,
        voice_tags=["(breath)", "(exhale)"],
    ),
    EmotionTag.NOSTALGIC: EmotionTTSMapping(
        emotion=EmotionTag.NOSTALGIC,
        speed_offset=-0.1,
        vol_offset=-0.05,
        pitch_offset=-1,
        voice_tags=["(sighs)", "(humming)"],
    ),
    EmotionTag.ANXIOUS: EmotionTTSMapping(
        emotion=EmotionTag.ANXIOUS,
        speed_offset=0.15,
        vol_offset=0.05,
        pitch_offset=1,
        voice_tags=["(breath)", "(gasps)"],
    ),
    EmotionTag.CONTEMPT: EmotionTTSMapping(
        emotion=EmotionTag.CONTEMPT,
        speed_offset=-0.05,
        vol_offset=0.1,
        pitch_offset=0,
        voice_tags=["(snorts)"],
    ),
    EmotionTag.DETERMINED: EmotionTTSMapping(
        emotion=EmotionTag.DETERMINED,
        speed_offset=0.05,
        vol_offset=0.15,
        pitch_offset=1,
        voice_tags=[],
    ),
    EmotionTag.PLAYFUL: EmotionTTSMapping(
        emotion=EmotionTag.PLAYFUL,
        speed_offset=0.1,
        vol_offset=0.1,
        pitch_offset=2,
        voice_tags=["(chuckle)"],
    ),
}


__all__ = [
    "BGMTiming",
    "ChapterAudioResult",
    "ChapterSoundResolutionReport",
    "ChapterTakeManifest",
    "ChapterTTSMetadata",
    "DubbingScript",
    "DubbingSegment",
    "EMOTION_MAPPINGS",
    "EmotionTTSMapping",
    "EmotionTag",
    "NarratorVoiceProfile",
    "PacingAnnotation",
    "ParalinguisticTag",
    "ReusableTakeRecord",
    "SceneEmotionAnnotation",
    "SceneTransition",
    "SegmentType",
    "SegmentTakeVersion",
    "SFXCue",
    "SoundAsset",
    "SoundCueResolution",
    "SoundLibraryManifest",
    "SoundscapeCue",
    "SpeedCurvePoint",
    "SynthesisResult",
    "SynthesisStatus",
    "TakeReviewStatus",
    "ToneShift",
    "TTSProgressState",
    "TTSProvider",
    "TTSProviderCapabilities",
    "TTSFeature",
    "TTSRequest",
    "TTSResponse",
    "TTSVoiceHints",
    "VoiceCastEntry",
    "VoiceCloneRequest",
    "VoiceCloneResponse",
    "VoiceCloneStatus",
    "VoiceEffectControls",
    "VoicePerformanceDirection",
    "VoicePerformanceOffsets",
    "VoicePerformanceOverrides",
    "VoicePerformanceProfile",
    "VoiceDesignRequest",
    "VoiceDesignResponse",
    "VoiceTeamContract",
]
