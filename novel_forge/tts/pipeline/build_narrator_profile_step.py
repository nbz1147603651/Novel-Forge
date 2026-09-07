"""Build Narrator Profile pipeline step.

Uses LLM to analyze the work's outline, genre, tone, and style profile
to generate a NarratorVoiceProfile for dynamic narrator voice adaptation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep, StepEventCallback
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.tts.schemas import NarratorVoiceProfile, TTSProvider

_log = get_logger("tts.pipeline.build_narrator_profile")


def build_narrator_voice_design_prompt(profile: NarratorVoiceProfile) -> str:
    """Create the provider-facing brief for one work-level narrator voice."""
    distance = {
        "close": "贴近角色，亲近但不抢戏",
        "medium": "适度叙述距离，清晰克制",
        "distant": "全知叙述视角，沉着留白",
    }.get(profile.narration_distance, profile.narration_distance or "适度叙述距离")
    expression = {
        "wide": "情感层次丰富，但避免戏剧化夸张",
        "moderate": "情感表达适度，保持叙述稳定",
        "restrained": "情感克制，以停顿和节奏传达张力",
    }.get(profile.emotional_range, profile.emotional_range or "情感表达适度")
    keywords = "、".join(profile.style_keywords[:6]) or "自然、清晰"
    parts = [
        "请为中文长篇有声书设计稳定、可复用的作品级旁白音色。",
        "用途仅限旁白，不模仿现实人物，不使用夸张卡通腔。",
        f"音色类型：{profile.voice_type or '自然中性旁白'}。",
        f"叙述距离：{distance}。",
        f"情感控制：{expression}。",
        f"基础语速：{profile.base_speed:.2f}；风格关键词：{keywords}。",
    ]
    if profile.notes.strip():
        parts.append(f"创作说明：{profile.notes.strip()}。")
    return "".join(parts)[:1600]


@dataclass
class BuildNarratorProfileInput:
    """Input for narrator profile building."""

    # ── 作品信息 ──────────────────────────────────────────────────────────────
    outline: dict[str, Any] = field(default_factory=dict)
    """全书大纲（或卷级大纲）"""
    genre: str = ""
    """体裁（言情/悬疑/历史/科幻等）"""
    tone: str = ""
    """基调（轻松/沉重/紧张/温馨等）"""
    style_profile: dict[str, Any] = field(default_factory=dict)
    """风格档案"""
    story_bible: dict[str, Any] = field(default_factory=dict)
    """世界观/主题/叙事视角等全书级声音上下文"""
    audio_aesthetic_hint: str = ""
    """来自 StorySpec 的声音美学要求"""
    sample_chapter: str = ""
    """样章文本（可选，用于分析叙述风格）"""
    # ── 可选覆盖 ──────────────────────────────────────────────────────────────
    narrator_voice_id: str = ""
    """指定的旁白音色 ID（优先级高于 LLM 推荐）"""
    provider: TTSProvider = TTSProvider.MOCK
    """TTS 平台"""


class BuildNarratorProfileStep(PipelineStep[BuildNarratorProfileInput, NarratorVoiceProfile]):
    """Build narrator voice profile from work metadata.

    Uses LLM to analyze:
    - Outline (themes, structure, emotional arc)
    - Genre (romance, suspense, historical, sci-fi, etc.)
    - Tone (light, heavy, tense, warm, etc.)
    - Style profile (literary style, narrative pace, POV style)

    And produces a NarratorVoiceProfile with:
    - Voice type description
    - Base speed and speed range
    - Emotional range (wide/moderate/restrained)
    - Narration distance (close/medium/distant)
    - Style keywords
    - Emotion-speed/volume modifiers for dynamic adaptation
    - Sample narration text for voice preview
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, on_step=on_step)

    @property
    def step_name(self) -> str:
        return "tts_build_narrator_profile"

    async def _execute(self, input_data: BuildNarratorProfileInput) -> NarratorVoiceProfile:
        """Build narrator voice profile."""
        _log.info(
            "Building narrator profile: genre=%s, tone=%s",
            input_data.genre or "unknown",
            input_data.tone or "unknown",
        )

        # Try LLM-based profile generation
        profile = await self._build_profile_llm(input_data)

        # Fallback to default profile
        if profile is None:
            _log.warning("LLM narrator profile generation failed, using default")
            self._on_step_event(
                "tts_narrator_rule_fallback",
                {"reason": "llm_generation_failed"},
            )
            profile = self._build_default_profile(input_data)

        # Override with user-specified voice_id if provided
        if input_data.narrator_voice_id:
            profile.voice_id = input_data.narrator_voice_id
        profile.provider = input_data.provider
        self._on_step_event(
            "tts_narrator_profile_ready",
            {
                "voice_type": profile.voice_type,
                "narration_distance": profile.narration_distance,
            },
        )

        _log.info(
            "Narrator profile built: voice_type=%s, base_speed=%.2f, "
            "emotional_range=%s, narration_distance=%s",
            profile.voice_type,
            profile.base_speed,
            profile.emotional_range,
            profile.narration_distance,
        )

        return profile

    async def _build_profile_llm(
        self, input_data: BuildNarratorProfileInput
    ) -> NarratorVoiceProfile | None:
        """LLM-based narrator profile generation (primary path)."""
        try:
            # Build stage_cards for prompt template
            stage_cards = {
                "outline": input_data.outline,
                "genre": input_data.genre,
                "tone": input_data.tone,
                "style_profile": input_data.style_profile,
                "story_bible": input_data.story_bible,
                "audio_aesthetic_hint": input_data.audio_aesthetic_hint,
                "sample_chapter": input_data.sample_chapter,
            }

            context = {"stage_cards": stage_cards}
            raw_data = await self._call_with_retry(
                TaskType.TTS_BUILD_NARRATOR_PROFILE,
                context,
                max_tokens=self._dynamic_max_tokens(
                    TaskType.TTS_BUILD_NARRATOR_PROFILE,
                    3200,
                    prompt_overhead=4800,
                    min_tokens=4096,
                    max_cap=8192,
                ),
                temperature=float(
                    getattr(self._settings, "tts_narrator_profile_temperature", 0.2)
                ),
                required_keys=(
                    "voice_type",
                    "base_speed",
                    "emotional_range",
                    "narration_distance",
                    "style_keywords",
                ),
                max_retries=2,
            )
            if not isinstance(raw_data, dict):
                raise TypeError("Narrator profile model returned non-object JSON")
            profile = self._parse_profile_from_json(raw_data)

            _log.info("LLM narrator profile generation succeeded")
            return profile

        except Exception as exc:
            _log.warning("LLM narrator profile generation failed: %s", exc)
            return None

    def _parse_profile_from_json(self, data: dict[str, Any]) -> NarratorVoiceProfile:
        """Parse NarratorVoiceProfile from LLM JSON output."""
        # Validate and clamp speed values
        base_speed = float(data.get("base_speed", 1.0))
        base_speed = max(0.5, min(2.0, base_speed))

        speed_range_low = float(data.get("speed_range_low", 0.8))
        speed_range_low = max(0.5, min(2.0, speed_range_low))

        speed_range_high = float(data.get("speed_range_high", 1.2))
        speed_range_high = max(0.5, min(2.0, speed_range_high))

        # Validate emotional_range
        emotional_range = data.get("emotional_range", "moderate")
        if emotional_range not in ("wide", "moderate", "restrained"):
            emotional_range = "moderate"

        # Validate narration_distance
        narration_distance = data.get("narration_distance", "medium")
        if narration_distance not in ("close", "medium", "distant"):
            narration_distance = "medium"

        # Parse style_keywords (limit to 5)
        style_keywords = data.get("style_keywords", [])
        if not isinstance(style_keywords, list):
            style_keywords = []
        style_keywords = [str(k) for k in style_keywords[:5]]

        # Parse genre_adaptation
        genre_adaptation = data.get("genre_adaptation", {})
        if not isinstance(genre_adaptation, dict):
            genre_adaptation = {}
        genre_adaptation = {
            str(k): max(0.0, min(1.0, float(v))) for k, v in genre_adaptation.items()
        }

        # Parse emotion modifiers
        emotion_speed_modifiers = data.get("emotion_speed_modifiers", {})
        if not isinstance(emotion_speed_modifiers, dict):
            emotion_speed_modifiers = {}
        emotion_speed_modifiers = {
            str(k): max(0.5, min(2.0, float(v))) for k, v in emotion_speed_modifiers.items()
        }

        emotion_volume_modifiers = data.get("emotion_volume_modifiers", {})
        if not isinstance(emotion_volume_modifiers, dict):
            emotion_volume_modifiers = {}
        emotion_volume_modifiers = {
            str(k): max(0.5, min(2.0, float(v))) for k, v in emotion_volume_modifiers.items()
        }

        return NarratorVoiceProfile(
            voice_id="",  # Will be set later or by user
            voice_type=str(data.get("voice_type", "")),
            base_speed=base_speed,
            speed_range_low=speed_range_low,
            speed_range_high=speed_range_high,
            emotional_range=emotional_range,
            narration_distance=narration_distance,
            style_keywords=style_keywords,
            genre_adaptation=genre_adaptation,
            emotion_speed_modifiers=emotion_speed_modifiers,
            emotion_volume_modifiers=emotion_volume_modifiers,
            sample_narration_text=str(data.get("sample_narration_text", "")),
            notes=str(data.get("notes", "")),
        )

    def _build_default_profile(self, input_data: BuildNarratorProfileInput) -> NarratorVoiceProfile:
        """Build default narrator profile based on genre hints."""
        genre = input_data.genre.lower()
        tone = input_data.tone.lower()

        tone_is_heavy = any(keyword in tone for keyword in ("沉重", "悲", "dark", "tragic", "压抑"))
        tone_is_light = any(
            keyword in tone for keyword in ("轻松", "温馨", "喜剧", "humor", "light")
        )

        # Genre-based defaults
        if any(k in genre for k in ["言情", "romance", "都市", "urban"]):
            return NarratorVoiceProfile(
                voice_type="温暖青年",
                base_speed=0.9 if tone_is_heavy else 1.0 if tone_is_light else 0.95,
                speed_range_low=0.8,
                speed_range_high=1.15,
                emotional_range="wide",
                narration_distance="close",
                style_keywords=["温暖", "细腻", "贴近角色"],
                emotion_speed_modifiers={
                    "sad": 0.85,
                    "happy": 1.1,
                    "tender": 0.9,
                    "angry": 1.1,
                },
            )
        elif any(k in genre for k in ["悬疑", "suspense", "惊悚", "thriller"]):
            return NarratorVoiceProfile(
                voice_type="磁性男声",
                base_speed=0.95 if tone_is_heavy else 1.0,
                speed_range_low=0.75,
                speed_range_high=1.3,
                emotional_range="moderate",
                narration_distance="medium",
                style_keywords=["低沉", "紧张", "节奏感"],
                emotion_speed_modifiers={
                    "fearful": 1.15,
                    "tense": 1.2,
                    "surprised": 1.1,
                },
            )
        elif any(k in genre for k in ["历史", "historical", "古风", "古代"]):
            return NarratorVoiceProfile(
                voice_type="沧桑老者",
                base_speed=0.85 if tone_is_heavy else 0.9,
                speed_range_low=0.75,
                speed_range_high=1.1,
                emotional_range="restrained",
                narration_distance="distant",
                style_keywords=["沉稳", "文学感", "厚重"],
                emotion_speed_modifiers={
                    "sad": 0.85,
                    "determined": 1.05,
                },
            )
        else:
            # Default neutral profile
            return NarratorVoiceProfile(
                voice_type="标准旁白",
                base_speed=1.0,
                speed_range_low=0.8,
                speed_range_high=1.2,
                emotional_range="moderate",
                narration_distance="medium",
                style_keywords=["中性", "清晰", "自然"],
            )
