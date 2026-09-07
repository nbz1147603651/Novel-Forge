"""Bounded upstream context projections for dubbing-script stages.

Each script stage consumes a different view of the same upstream artifacts.
This module keeps those views explicit and compact so callers do not either
drop useful information or pass entire project documents to every prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from novel_forge.core.config import Settings
from novel_forge.tts.schemas import (
    ChapterTTSMetadata,
    DubbingSegment,
    DubbingStyleProfile,
    NarratorVoiceProfile,
)


class ScriptContextStage(str, Enum):
    """Prompt stages that need a bounded view of script upstream context."""

    PROFESSIONAL_REVIEW = "professional_review"
    SPOKEN_REWRITE = "spoken_rewrite"
    EMOTION_LABEL = "emotion_label"


@dataclass(frozen=True)
class ContextProjectionSpec:
    """Declarative field selection for one prompt stage."""

    style_fields: tuple[str, ...] = ()
    story_fields: tuple[str, ...] = ()
    narrator_fields: tuple[str, ...] = ()
    character_voice_fields: tuple[str, ...] = ()
    tts_metadata_fields: tuple[str, ...] = ()
    scene_intent_fields: tuple[str, ...] = ()
    location_acoustic_fields: tuple[str, ...] = ()


_PERFORMANCE_STYLE_FIELDS = (
    "summary",
    "speed_range",
    "emotional_tone",
    "literary_style",
    "narrative_pace",
    "dialogue_style",
)
_SPOKEN_CHARACTER_VOICE_FIELDS = (
    "character",
    "character_name",
    "sentence_profile",
    "explanation_bias",
    "emotion_syntax",
    "signature_moves",
    "taboo_patterns",
    "sample_lines",
)
_SCENE_INTENT_FIELDS = (
    "scene_id",
    "location",
    "time_marker",
    "emotional_beat",
    "purpose",
    "summary",
    "pov_character",
    "conflict",  # 核心阻力 → 情绪强度和张力判断
    "dialogue_subtext",  # 对白潜台词 → tone_hint 和语气改写
    "sensory_notes",  # 感官锚点 → 声景设计和氛围改写
    "relationship_dynamics",  # 关系动态 → 角色互动语气
)
_LOCATION_ACOUSTIC_FIELDS = (
    "name",
    "location_id",
    "ambient_sound",  # 初始化锁定的环境声层 → 声景与氛围改写种子
    "audio_aesthetic",
)


SCRIPT_CONTEXT_SPECS: Mapping[ScriptContextStage, ContextProjectionSpec] = {
    ScriptContextStage.PROFESSIONAL_REVIEW: ContextProjectionSpec(
        style_fields=_PERFORMANCE_STYLE_FIELDS,
        story_fields=("genre", "theme", "themes", "tone", "era"),
        narrator_fields=(
            "voice_type",
            "emotional_range",
            "narration_distance",
            "style_keywords",
            "notes",
        ),
        character_voice_fields=_SPOKEN_CHARACTER_VOICE_FIELDS,
        tts_metadata_fields=(
            "scene_emotion_map",
            "character_emotion_trajectories",
            "pacing_annotations",
            "expression_channel_constraints",
            "narration_tone_progression",
        ),
        scene_intent_fields=_SCENE_INTENT_FIELDS,
        location_acoustic_fields=_LOCATION_ACOUSTIC_FIELDS,
    ),
    ScriptContextStage.SPOKEN_REWRITE: ContextProjectionSpec(
        style_fields=_PERFORMANCE_STYLE_FIELDS,
        story_fields=("genre", "theme", "themes", "tone", "era"),
        narrator_fields=(
            "voice_type",
            "emotional_range",
            "narration_distance",
            "style_keywords",
        ),
        character_voice_fields=_SPOKEN_CHARACTER_VOICE_FIELDS,
        tts_metadata_fields=(
            "scene_emotion_map",
            "narration_tone_progression",
            "character_emotion_trajectories",  # 角色情绪轨迹 → 改写时保持情绪连贯
            "pacing_annotations",  # 节奏标注 → 改写时匹配目标节奏
        ),
        scene_intent_fields=_SCENE_INTENT_FIELDS,
        location_acoustic_fields=_LOCATION_ACOUSTIC_FIELDS,
    ),
    ScriptContextStage.EMOTION_LABEL: ContextProjectionSpec(
        style_fields=_PERFORMANCE_STYLE_FIELDS,
        story_fields=("genre", "theme", "themes", "tone", "era"),
        narrator_fields=(
            "voice_type",
            "emotional_range",
            "narration_distance",
            "style_keywords",
        ),
        character_voice_fields=_SPOKEN_CHARACTER_VOICE_FIELDS,
        tts_metadata_fields=(
            "scene_emotion_map",
            "character_emotion_trajectories",  # 角色情绪轨迹 → 情绪精标保持轨迹连贯
            "narration_tone_progression",  # 旁白基调进程 → 精标强度随弧线波动
            "expression_channel_constraints",  # 表达通道约束 → 标签须服从通道边界
            "pacing_annotations",  # 节奏标注 → 标签匹配目标节奏
        ),
        scene_intent_fields=_SCENE_INTENT_FIELDS,
    ),
}


def _setting_default(name: str) -> Any:
    field_info = Settings.model_fields[name]
    return field_info.default


@dataclass(frozen=True)
class ContextProjectionLimits:
    """Size limits shared by all script-stage context projections."""

    max_items: int
    max_text_chars: int

    @classmethod
    def from_settings(cls, settings: object) -> "ContextProjectionLimits":
        return cls(
            max_items=max(
                1,
                int(
                    getattr(
                        settings,
                        "tts_script_context_max_items",
                        _setting_default("tts_script_context_max_items"),
                    )
                ),
            ),
            max_text_chars=max(
                40,
                int(
                    getattr(
                        settings,
                        "tts_script_context_max_text_chars",
                        _setting_default("tts_script_context_max_text_chars"),
                    )
                ),
            ),
        )


@dataclass(frozen=True)
class ScriptStageContext:
    """Authoritative upstream inputs shared by script stages.

    The object stores references to the already-loaded artifacts.  ``project``
    creates a stage-specific JSON-ready view and applies size limits only to
    contextual material; authoritative segment text is never truncated here.
    """

    character_voices: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    style_profile: Mapping[str, Any] = field(default_factory=dict)
    story_context: Mapping[str, Any] = field(default_factory=dict)
    narrator_profile: NarratorVoiceProfile | None = None
    tts_metadata: ChapterTTSMetadata | None = None
    scene_intents: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    location_acoustics: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    reference_style_profile: DubbingStyleProfile | None = None
    reference_style_strength: float = 0.0
    chapter_number: int = 0

    def project(
        self,
        stage: ScriptContextStage,
        segments: Sequence[DubbingSegment],
        *,
        limits: ContextProjectionLimits,
    ) -> dict[str, Any]:
        """Return only the upstream context needed by ``stage``."""

        spec = SCRIPT_CONTEXT_SPECS[stage]
        focus_text = "\n".join(segment.text for segment in segments)
        focus_names = {
            str(value).strip()
            for segment in segments
            for value in (segment.character_id, segment.character_name)
            if str(value).strip()
        }
        scene_markers = {
            str(segment.scene_context or "").strip()
            for segment in segments
            if str(segment.scene_context or "").strip()
        }

        cards: dict[str, Any] = {}
        self._add_mapping(cards, "style_profile", self.style_profile, spec.style_fields, limits)
        self._add_mapping(cards, "story_context", self.story_context, spec.story_fields, limits)

        if self.narrator_profile is not None and spec.narrator_fields:
            cards["narrator_profile"] = _compact(
                _project_mapping(
                    self.narrator_profile.model_dump(mode="json"),
                    spec.narrator_fields,
                ),
                limits,
            )

        character_voices = self._relevant_character_voices(focus_text, focus_names)
        if character_voices and spec.character_voice_fields:
            cards["character_voices"] = _compact(
                [
                    _project_mapping(profile, spec.character_voice_fields)
                    for profile in character_voices
                ],
                limits,
            )

        if self.tts_metadata is not None and spec.tts_metadata_fields:
            metadata = _project_mapping(
                self.tts_metadata.model_dump(mode="json"),
                spec.tts_metadata_fields,
            )
            if metadata:
                cards["tts_metadata"] = _compact(metadata, limits)

        scene_intents = self._relevant_scene_intents(scene_markers)
        if scene_intents and spec.scene_intent_fields:
            cards["scene_intents"] = _compact(
                [_project_mapping(scene, spec.scene_intent_fields) for scene in scene_intents],
                limits,
            )

        location_acoustics = self._relevant_location_acoustics(scene_markers)
        if location_acoustics and spec.location_acoustic_fields:
            cards["location_acoustics"] = _compact(
                [
                    _project_mapping(acoustic, spec.location_acoustic_fields)
                    for acoustic in location_acoustics
                ],
                limits,
            )

        if (
            stage == ScriptContextStage.SPOKEN_REWRITE
            and self.reference_style_profile is not None
            and self.reference_style_strength > 0.0
        ):
            raw_profile = self.reference_style_profile.model_dump(mode="json")
            cards["reference_dubbing_style"] = {
                "strength": round(max(0.0, min(1.0, self.reference_style_strength)), 3),
                "confidence": self.reference_style_profile.confidence,
                **{
                    key: _compact(raw_profile.get(key, []), limits)
                    for key in (
                        "narration_traits",
                        "dialogue_traits",
                        "rhythm_rules",
                        "pause_rules",
                        "performance_direction_rules",
                        "sound_design_rules",
                        "forbidden_tendencies",
                    )
                },
            }

        # Always project narrative_position for position-aware guidance.
        cards["narrative_position"] = self._build_narrative_position(segments)
        return cards

    def preserved_terms(self) -> tuple[str, ...]:
        """Return source-anchored names and locations that rewrites must keep."""

        terms: list[str] = []
        for profile in self.character_voices:
            terms.extend(
                str(profile.get(key) or "").strip() for key in ("character", "character_name")
            )
        for scene in self.scene_intents:
            terms.append(str(scene.get("location") or "").strip())
        return tuple(dict.fromkeys(term for term in terms if term))

    @staticmethod
    def _add_mapping(
        cards: dict[str, Any],
        key: str,
        source: Mapping[str, Any],
        fields: tuple[str, ...],
        limits: ContextProjectionLimits,
    ) -> None:
        projected = _project_mapping(source, fields)
        if projected:
            cards[key] = _compact(projected, limits)

    def _relevant_character_voices(
        self,
        focus_text: str,
        focus_names: set[str],
    ) -> list[Mapping[str, Any]]:
        relevant: list[Mapping[str, Any]] = []
        unnamed: list[Mapping[str, Any]] = []
        for profile in self.character_voices:
            names = {
                str(profile.get(key) or "").strip()
                for key in ("character", "character_name", "character_id")
                if str(profile.get(key) or "").strip()
            }
            if not names:
                unnamed.append(profile)
            elif names & focus_names or any(name in focus_text for name in names):
                relevant.append(profile)
        return relevant or unnamed or list(self.character_voices)

    def _relevant_scene_intents(self, scene_markers: set[str]) -> list[Mapping[str, Any]]:
        if not scene_markers:
            return list(self.scene_intents)
        relevant = []
        for scene in self.scene_intents:
            values = (
                str(scene.get("scene_id") or "").strip(),
                str(scene.get("location") or "").strip(),
            )
            if any(
                value and any(value in marker or marker in value for marker in scene_markers)
                for value in values
            ):
                relevant.append(scene)
        return relevant or list(self.scene_intents)

    def _relevant_location_acoustics(
        self, scene_markers: set[str]
    ) -> list[Mapping[str, Any]]:
        if not self.location_acoustics:
            return []
        if not scene_markers:
            return list(self.location_acoustics)
        relevant = []
        for acoustic in self.location_acoustics:
            name = str(acoustic.get("name") or "").strip()
            if name and any(name in marker or marker in name for marker in scene_markers):
                relevant.append(acoustic)
        return relevant or list(self.location_acoustics)

    def _build_narrative_position(
        self, segments: Sequence[DubbingSegment]
    ) -> dict[str, Any]:
        """Build a compact narrative-position card for prompt injection."""
        if not segments:
            return {
                "chapter_number": self.chapter_number,
                "is_first_chapter": self.chapter_number == 1,
                "is_opening": True,
                "segment_position_ratio": 0.0,
            }
        # Use the first segment's position as representative for the batch.
        first_pos = segments[0].segment_index
        # Estimate total from the highest segment_index we can see.
        total = max(seg.segment_index for seg in segments) + 1
        ratio = first_pos / max(1, total)
        return {
            "chapter_number": self.chapter_number,
            "is_first_chapter": self.chapter_number == 1,
            "is_opening": first_pos < 3 or ratio < 0.1,
            "segment_position_ratio": round(ratio, 3),
        }


def _project_mapping(source: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {
        key: source[key]
        for key in fields
        if key in source and source[key] not in (None, "", [], {})
    }


def _compact(value: Any, limits: ContextProjectionLimits) -> Any:
    """Compact contextual values recursively while preserving their shape."""

    if isinstance(value, str):
        if len(value) <= limits.max_text_chars:
            return value
        return value[: limits.max_text_chars].rstrip() + "…"
    if isinstance(value, Mapping):
        return {
            str(key): _compact(item, limits)
            for key, item in list(value.items())[: limits.max_items]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_compact(item, limits) for item in list(value)[: limits.max_items]]
    return value


__all__ = [
    "ContextProjectionLimits",
    "ScriptContextStage",
    "ScriptStageContext",
]
