"""Canonical character-performance policy shared by audition and final synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from novel_forge.tts.schemas import (
    EMOTION_MAPPINGS,
    EmotionTag,
    TTSProvider,
    VoiceCastEntry,
    VoicePerformanceDirection,
    VoicePerformanceOffsets,
    VoicePerformanceOverrides,
    VoicePerformanceProfile,
    VoiceTeamContract,
)

PERFORMANCE_POLICY_VERSION = "1.0"
SHORT_UTTERANCE_MAX_UNITS = 8
EXTREME_SHORT_UTTERANCE_MAX_UNITS = 4
"""≤ 4 可朗读字符的极短句剥离情绪；5–8 字符短句保留情绪但降低强度。"""
PerformanceDerivationSource = Literal["neutral", "structured_hints", "legacy", "manual"]
PerformanceDirectionName = Literal[
    "slow_down",
    "speed_up",
    "pause_more",
    "lower_volume",
    "raise_volume",
]


@dataclass(frozen=True)
class ResolvedCharacterPerformance:
    """Final portable parameters plus an audit trail explaining how they were resolved."""

    speed: float
    pitch: int
    volume: float
    speed_offset: float
    pitch_offset: int
    vol_offset: float
    speed_source: str
    pitch_source: str
    volume_source: str
    manual_fields: tuple[str, ...] = ()
    short_utterance_stabilized: bool = False
    provider_guard_applied: bool = False
    warnings: tuple[str, ...] = ()


def is_short_spoken_utterance(text: str) -> bool:
    """Return whether a line is too short for aggressive identity-shaping controls."""

    units = re.sub(r"[\s\W_]+", "", str(text), flags=re.UNICODE)
    return len(units) <= SHORT_UTTERANCE_MAX_UNITS


def is_extreme_short_utterance(text: str) -> bool:
    """Return whether a line is so short that emotion should be fully stripped.

    Extreme short (≤ 4 speakable chars) gets identity stabilization AND emotion
    removal.  Moderate short (5–8 chars) keeps identity stabilization but
    preserves a reduced emotion signal.
    """

    units = re.sub(r"[\s\W_]+", "", str(text), flags=re.UNICODE)
    return len(units) <= EXTREME_SHORT_UTTERANCE_MAX_UNITS


def derive_voice_performance_profile(
    character: dict[str, Any],
) -> VoicePerformanceProfile:
    """Derive baseline offsets and preserve situational prose as semantic directions.

    Baseline derivation priority:
    1. Structured ``tts_voice_hints`` (explicit author intent)
    2. Keyword-based inference from voice/speaking-habit descriptions
    3. Neutral zero baseline (fallback)

    Free-form personality descriptions are scanned for stable speech-pattern
    keywords ("语速快", "声音轻", "声调高") that indicate a character's
    *permanent* vocal identity, as opposed to situational acting notes which
    become conditional_directions.
    """

    hints = character.get("tts_voice_hints")
    reasons: list[str] = []
    warnings: list[str] = []
    baseline = VoicePerformanceOffsets()
    source: PerformanceDerivationSource = "neutral"
    if isinstance(hints, dict) and hints:
        preferred_speed = str(hints.get("preferred_speed") or "").strip().lower()
        preferred_pitch = str(hints.get("preferred_pitch") or "").strip().lower()
        speed_offset = {"slow": -0.05, "normal": 0.0, "fast": 0.05}.get(
            preferred_speed,
            0.0,
        )
        pitch_offset = {"low": -1, "medium": 0, "high": 1}.get(preferred_pitch, 0)
        baseline = VoicePerformanceOffsets(
            speed_offset=speed_offset,
            pitch_offset=pitch_offset,
            vol_offset=0.0,
        )
        source = "structured_hints"
        if preferred_speed:
            reasons.append(f"结构化角色提示：基础语速 {preferred_speed}")
        if preferred_pitch:
            reasons.append(f"结构化角色提示：基础音高 {preferred_pitch}")
    else:
        # ── Keyword-based baseline inference from speech patterns ──
        voice_text = " ".join(
            str(character.get(key) or "").strip()
            for key in ("voice", "voice_description", "speaking_habits", "clone_prompt")
            if str(character.get(key) or "").strip()
        )
        inferred = _infer_baseline_from_speech_patterns(voice_text)
        if inferred is not None:
            baseline = inferred
            source = "structured_hints"  # Treat as structured for downstream trust
            reasons.append("从角色说话习惯描述推导基线偏移")
        else:
            reasons.append("自由文本仅生成情境表演指令，不再改变角色全局数值")

    voice_text = " ".join(
        str(character.get(key) or "").strip()
        for key in ("voice", "voice_description")
        if str(character.get(key) or "").strip()
    )
    directions = _extract_conditional_directions(voice_text)
    if directions:
        warnings.append("检测到情境性语速/音量描述，已从全局基线分离")

    return VoicePerformanceProfile(
        policy_version=PERFORMANCE_POLICY_VERSION,
        automatic_baseline=baseline,
        conditional_directions=directions,
        derivation_source=source,
        derivation_reasons=reasons,
        warnings=warnings,
    )


def voice_performance_profile(entry: VoiceCastEntry) -> VoicePerformanceProfile:
    """Return the canonical profile, migrating or reconciling legacy flat fields."""

    profile = entry.performance_profile
    legacy = VoicePerformanceOffsets(
        speed_offset=float(entry.speed_offset),
        pitch_offset=int(entry.pitch_offset),
        vol_offset=float(entry.vol_offset),
    )
    if profile is None:
        return _legacy_profile(legacy, manually_set=entry.performance_offsets_manually_set)

    configured = profile.configured_offsets
    if configured == legacy:
        return profile

    # ``model_copy(update={legacy_field: ...})`` was a public pattern before the
    # profile existed. Reconcile it here so callers do not silently lose edits.
    if entry.performance_offsets_manually_set:
        return profile.model_copy(
            update={
                "manual_overrides": VoicePerformanceOverrides(
                    speed_offset=legacy.speed_offset,
                    pitch_offset=legacy.pitch_offset,
                    vol_offset=legacy.vol_offset,
                ),
                "derivation_source": "manual",
            }
        )
    return profile.model_copy(
        update={
            "automatic_baseline": legacy,
            "derivation_source": "legacy",
        }
    )


def update_voice_performance_profile(
    entry: VoiceCastEntry,
    profile: VoicePerformanceProfile,
) -> VoiceCastEntry:
    """Persist the canonical profile and legacy mirrors in one atomic model copy."""

    configured = profile.configured_offsets
    return entry.model_copy(
        update={
            "performance_profile": profile,
            "speed_offset": configured.speed_offset,
            "pitch_offset": configured.pitch_offset,
            "vol_offset": configured.vol_offset,
            "performance_offsets_manually_set": profile.has_manual_overrides,
        }
    )


def refresh_voice_performance_profile(
    entry: VoiceCastEntry,
    character: dict[str, Any],
) -> VoiceCastEntry:
    """Re-derive automatic policy data while preserving explicit per-field choices."""

    current = voice_performance_profile(entry)
    derived = derive_voice_performance_profile(character)
    refreshed = derived.model_copy(
        update={
            "manual_overrides": current.manual_overrides,
            "short_utterance_stability": current.short_utterance_stability,
        }
    )
    updated = update_voice_performance_profile(entry, refreshed)
    if refreshed != current:
        updated = updated.model_copy(
            update={
                "preview_audio_path": "",
                "preview_text": "",
                "preview_error": "",
                "preview_variants": {},
            }
        )
    return updated


def hydrate_voice_team_performance_profiles(
    team: VoiceTeamContract,
    characters: list[dict[str, Any]],
) -> VoiceTeamContract:
    """Upgrade an entire team at domain boundaries without recasting any voice IDs."""

    by_key: dict[str, dict[str, Any]] = {}
    for character in characters:
        character_id = str(character.get("character_id") or "").strip()
        name = str(character.get("name") or "").strip()
        if character_id:
            by_key[character_id] = character
        if name:
            by_key[name] = character
    entries: list[VoiceCastEntry] = []
    for entry in team.entries:
        matched_character = by_key.get(entry.character_id) or by_key.get(entry.character_name)
        entries.append(
            refresh_voice_performance_profile(entry, matched_character)
            if matched_character
            else entry
        )
    return team.model_copy(update={"entries": entries})


def with_manual_performance_overrides(
    entry: VoiceCastEntry,
    *,
    speed_offset: float | None | object = ...,  # ``...`` means unchanged.
    pitch_offset: int | None | object = ...,
    vol_offset: float | None | object = ...,
) -> VoiceCastEntry:
    """Update individual author overrides without unlocking unrelated fields."""

    profile = voice_performance_profile(entry)
    current = profile.manual_overrides
    overrides = VoicePerformanceOverrides(
        speed_offset=current.speed_offset if speed_offset is ... else speed_offset,
        pitch_offset=current.pitch_offset if pitch_offset is ... else pitch_offset,
        vol_offset=current.vol_offset if vol_offset is ... else vol_offset,
    )
    source = "manual" if overrides.active_fields else profile.derivation_source
    updated = profile.model_copy(
        update={
            "manual_overrides": overrides,
            "derivation_source": source,
        }
    )
    return update_voice_performance_profile(entry, updated)


def resolve_character_performance(
    entry: VoiceCastEntry,
    provider: TTSProvider,
    *,
    text: str,
    default_speed: float = 1.0,
    speed_override: float | None = None,
    pitch_override: int | None = None,
    vol_override: float | None = None,
    emotion: EmotionTag | str | None = None,
    scene_context: str = "",
    tone_hint: str = "",
) -> ResolvedCharacterPerformance:
    """Resolve every character numeric layer through one provider-aware policy.

    When ``scene_context`` or ``tone_hint`` is provided, the profile's
    ``conditional_directions`` are matched against them (P3 activation).
    Matched directions apply subtle speed/vol micro-adjustments so a
    character's situational acting notes ("紧急时语速加快") actually
    influence the synthesized output.
    """

    profile = voice_performance_profile(entry)
    baseline = profile.automatic_baseline
    manual = profile.manual_overrides
    speed_offset = baseline.speed_offset if manual.speed_offset is None else manual.speed_offset
    pitch_offset = baseline.pitch_offset if manual.pitch_offset is None else manual.pitch_offset
    vol_offset = baseline.vol_offset if manual.vol_offset is None else manual.vol_offset
    speed_source = "automatic" if manual.speed_offset is None else "manual"
    pitch_source = "automatic" if manual.pitch_offset is None else "manual"
    volume_source = "automatic" if manual.vol_offset is None else "manual"

    short_utterance = is_short_spoken_utterance(text)
    short_stabilized = bool(short_utterance and profile.short_utterance_stability)
    provider_guard_applied = False
    if short_stabilized:
        speed_offset = 0.0
        pitch_offset = 0
        speed_source = "short_stability"
        pitch_source = "short_stability"
    elif provider == TTSProvider.MINIMAX:
        if manual.speed_offset is None:
            # 有明确情绪时允许更宽的语速偏移，增强情绪节奏感
            emotion_tag_for_guard = _coerce_emotion(emotion)
            speed_limit = 0.18 if emotion_tag_for_guard != EmotionTag.NEUTRAL else 0.12
            guarded_speed = max(-speed_limit, min(speed_limit, speed_offset))
            provider_guard_applied = provider_guard_applied or guarded_speed != speed_offset
            speed_offset = guarded_speed
        if manual.pitch_offset is None:
            provider_guard_applied = provider_guard_applied or pitch_offset != 0
            pitch_offset = 0

    base_speed = default_speed if speed_override is None else speed_override
    segment_pitch = 0 if short_stabilized else (pitch_override or 0)
    base_volume = 1.0 if vol_override is None else vol_override
    speed = base_speed + speed_offset
    pitch = segment_pitch + pitch_offset
    volume = base_volume + vol_offset

    emotion_tag = _coerce_emotion(emotion)
    if (
        not short_stabilized
        and provider != TTSProvider.MINIMAX
        and emotion_tag != EmotionTag.NEUTRAL
    ):
        mapping = EMOTION_MAPPINGS.get(emotion_tag)
        if mapping is not None:
            speed += mapping.speed_offset * 0.45
            volume += mapping.vol_offset * 0.45

    # ── P3: Conditional directions activation ──
    # Match the character's situational acting notes against the current
    # segment's scene_context / tone_hint.  Each matched direction applies
    # a graduated micro-adjustment based on strength:
    #   subtle=±0.03, moderate=±0.08, strong=±0.15
    _direction_speed_delta = 0.0
    _direction_vol_delta = 0.0
    _matched_directions: list[str] = []
    if not short_stabilized and profile.conditional_directions:
        _context_pool = f"{scene_context} {tone_hint}".strip()
        if _context_pool:
            for direction in profile.conditional_directions:
                trigger = direction.trigger.strip()
                if not trigger or trigger not in _context_pool:
                    continue
                strength_scale = {
                    "subtle": 0.03,
                    "moderate": 0.08,
                    "strong": 0.15,
                }.get(direction.strength, 0.03)
                if direction.direction == "slow_down":
                    _direction_speed_delta -= strength_scale
                elif direction.direction == "speed_up":
                    _direction_speed_delta += strength_scale
                elif direction.direction == "lower_volume":
                    _direction_vol_delta -= strength_scale
                elif direction.direction == "raise_volume":
                    _direction_vol_delta += strength_scale
                # pause_more is handled at the assembly layer, not here.
                _matched_directions.append(direction.direction)
            speed += _direction_speed_delta
            volume += _direction_vol_delta

    speed = max(0.5, min(2.0, speed))
    pitch = max(-12, min(12, pitch))
    volume = max(0.0, min(2.0, volume))
    warnings: list[str] = []
    if speed < 0.9:
        warnings.append("有效语速低于 0.90×，长句可能拖腔")
    elif speed > 1.15:
        warnings.append("有效语速高于 1.15×，吐字可能不稳定")
    if abs(pitch) > 3:
        warnings.append("音调偏移超过 3 半音，可能改变角色声纹")
    if provider_guard_applied:
        warnings.append("已应用平台声纹稳定保护")
    if _matched_directions:
        warnings.append(f"情境指令激活: {', '.join(_matched_directions)}")

    return ResolvedCharacterPerformance(
        speed=speed,
        pitch=pitch,
        volume=volume,
        speed_offset=speed_offset,
        pitch_offset=pitch_offset,
        vol_offset=vol_offset,
        speed_source=speed_source,
        pitch_source=pitch_source,
        volume_source=volume_source,
        manual_fields=tuple(
            field for field in ("speed", "pitch", "volume") if field in manual.active_fields
        ),
        short_utterance_stabilized=short_stabilized,
        provider_guard_applied=provider_guard_applied,
        warnings=tuple(warnings),
    )


def build_voice_preview_samples(character: dict[str, Any]) -> dict[str, str]:
    """Return independent audition cases so sentence-length policy is observable."""

    name = str(character.get("name") or "这个角色").strip()
    identity = ""
    for key in ("voice_sample", "sample_dialogue", "signature_line", "sample_quote"):
        identity = str(character.get(key) or "").strip()
        if identity:
            break
    if not identity:
        identity = f"我是{name}。事情还没有结束，我们先把已经知道的线索重新理一遍。"
    return {
        "identity": identity[:500],
        "short": "知道了。",
        "emotion": "有些事情我一直没有说，不代表我已经忘记。",
        "urgent": "先离开这里，其他问题路上再说。",
    }


def _legacy_profile(
    offsets: VoicePerformanceOffsets,
    *,
    manually_set: bool,
) -> VoicePerformanceProfile:
    if manually_set:
        return VoicePerformanceProfile(
            policy_version=PERFORMANCE_POLICY_VERSION,
            manual_overrides=VoicePerformanceOverrides(
                speed_offset=offsets.speed_offset,
                pitch_offset=offsets.pitch_offset,
                vol_offset=offsets.vol_offset,
            ),
            derivation_source="manual",
            derivation_reasons=["由旧版人工表达参数迁移"],
        )
    return VoicePerformanceProfile(
        policy_version=PERFORMANCE_POLICY_VERSION,
        automatic_baseline=offsets,
        derivation_source="legacy",
        derivation_reasons=["由旧版自动表达参数迁移"],
    )


def _extract_conditional_directions(text: str) -> list[VoicePerformanceDirection]:
    if not text:
        return []
    results: list[VoicePerformanceDirection] = []
    seen: set[tuple[str, str]] = set()
    for raw_clause in re.split(r"[。；;\n]+", text):
        clause = raw_clause.strip(" ，,")
        if not clause or not re.search(r"(?:时|当|一旦|提到|遇到|紧急|情绪)", clause):
            continue
        trigger = _direction_trigger(clause)
        directions: list[PerformanceDirectionName] = []
        if re.search(r"语速.{0,6}(?:慢|放缓|降低)", clause):
            directions.append("slow_down")
        if re.search(r"语速.{0,6}(?:快|加快|升高|陡升)", clause):
            directions.append("speed_up")
        if "停顿" in clause or "沉默" in clause:
            directions.append("pause_more")
        if re.search(r"音量.{0,6}(?:低|降低|压低)", clause):
            directions.append("lower_volume")
        if re.search(r"音量.{0,6}(?:高|提高|升高)", clause):
            directions.append("raise_volume")
        # Determine strength from intensity markers in the clause
        strength = _infer_direction_strength(clause)
        for direction in directions:
            key = (trigger, direction)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                VoicePerformanceDirection(
                    trigger=trigger,
                    direction=direction,
                    strength=strength,
                    evidence=clause[:240],
                )
            )
    return results[:8]


def _infer_direction_strength(clause: str) -> str:
    """Infer conditional direction strength from intensity markers.

    Intensity is intentionally capped at ``moderate`` because
    ``VoicePerformanceDirection`` exposes a portable two-level contract:
    ``subtle`` or ``moderate``.  A former ``strong`` return value bypassed
    that contract and caused voice-team construction to fail validation.

    - moderate: "突然""直接""从不""绝不""彻底" + explicit speed/volume change
    - moderate: "明显""加快""放慢""提高""压低" without extreme markers
    - subtle: default for mild descriptions
    """
    strong_markers = ("突然", "直接", "从不", "绝不", "彻底", "猛然", "驤时")
    moderate_markers = ("明显", "加快", "放慢", "提高", "压低", "收紧", "收掉")
    if any(marker in clause for marker in strong_markers):
        return "moderate"
    if any(marker in clause for marker in moderate_markers):
        return "moderate"
    return "subtle"


def _infer_baseline_from_speech_patterns(text: str) -> VoicePerformanceOffsets | None:
    """Infer stable baseline offsets from a character's speech-pattern description.

    Scans for permanent vocal identity keywords (not situational) and maps
    them to small, safe numeric offsets.  Returns None when no confident
    inference can be made.

    Keywords mapped:
    - "语速快" / "短句为主" / "快节奏" → speed_offset +0.06
    - "语速慢" / "声音轻" / "慢悠悠" → speed_offset -0.06
    - "声音很轻" / "声音偏小" → vol_offset -0.10
    - "声调高" / "提高声调" / "吹牛" → pitch_offset +1
    - "声调低" / "低沉" / "沙哑" → pitch_offset -1
    """
    if not text:
        return None

    speed_offset = 0.0
    pitch_offset = 0
    vol_offset = 0.0
    matched = False

    # Speed patterns (permanent identity, not situational)
    if re.search(r"语速快|短句为主|快节奏|语速偏快|多说.*短句", text):
        speed_offset += 0.06
        matched = True
    if re.search(r"语速慢|慢悠悠|语速偏慢|声音轻.*语速慢|说话.*慢", text):
        speed_offset -= 0.06
        matched = True

    # Volume patterns
    if re.search(r"声音很轻|声音偏小|声音轻|小声|轻声细语", text):
        vol_offset -= 0.10
        matched = True

    # Pitch patterns
    if re.search(r"声调高|提高声调|吹牛|神神叨叨|尖锐", text):
        pitch_offset += 1
        matched = True
    if re.search(r"声调低|低沉|沙哑|沉闷", text):
        pitch_offset -= 1
        matched = True

    if not matched:
        return None

    # Clamp to safe ranges
    speed_offset = max(-0.10, min(0.10, speed_offset))
    pitch_offset = max(-2, min(2, pitch_offset))
    vol_offset = max(-0.15, min(0.15, vol_offset))

    return VoicePerformanceOffsets(
        speed_offset=speed_offset,
        pitch_offset=pitch_offset,
        vol_offset=vol_offset,
    )


def _direction_trigger(clause: str) -> str:
    match = re.search(r"(.{1,40}?)(?:时|时会|时则)", clause)
    if match:
        return match.group(1).strip(" ，,")
    return clause[:40]


def _coerce_emotion(value: EmotionTag | str | None) -> EmotionTag:
    if isinstance(value, EmotionTag):
        return value
    try:
        return EmotionTag(str(value or EmotionTag.NEUTRAL.value))
    except ValueError:
        return EmotionTag.NEUTRAL


__all__ = [
    "PERFORMANCE_POLICY_VERSION",
    "ResolvedCharacterPerformance",
    "build_voice_preview_samples",
    "derive_voice_performance_profile",
    "hydrate_voice_team_performance_profiles",
    "is_short_spoken_utterance",
    "refresh_voice_performance_profile",
    "resolve_character_performance",
    "update_voice_performance_profile",
    "voice_performance_profile",
    "with_manual_performance_overrides",
]
