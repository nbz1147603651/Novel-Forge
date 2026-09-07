"""Declarative expression adapter — bridges rich directorial intent to provider parameters.

All provider-specific mapping rules, compensation parameters, and bridging
formulas are loaded from JSON expression profiles.  This module contains only
generic execution logic; no emotion names or numeric constants are hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from novel_forge.obs.logger import get_logger
from novel_forge.tts.platform.expression_profiles import load_profile_dict

_log = get_logger("tts.platform.expression_adapter")


# ─── Pydantic Models (JSON schema) ────────────────────────────────────────────


class EmotionCompensationRule(BaseModel):
    """Compensation applied when a story emotion is projected to a native one."""

    projected_to: str = ""
    speed_delta: float = 0.0
    vol_delta: float = 0.0
    inject_tags: list[str] = Field(default_factory=list)
    note: str = ""


class ToneHintAction(BaseModel):
    """Action to take when a tone_hint pattern matches."""

    inject_prefix_tag: str = ""
    speed_delta: float = 0.0
    vol_delta: float = 0.0


class ToneHintRule(BaseModel):
    """A pattern-matching rule for tone_hint consumption."""

    patterns: list[str] = Field(default_factory=list)
    action: ToneHintAction = Field(default_factory=ToneHintAction)


class BridgeAction(BaseModel):
    """Parameter deltas for a vocal direction dimension threshold."""

    speed_delta: float = 0.0
    vol_delta: float = 0.0
    inject_tag: str = ""


class DimensionRule(BaseModel):
    """Bridging rule for one vocal direction dimension."""

    high_threshold: float | None = None
    high_action: BridgeAction = Field(default_factory=BridgeAction)
    low_threshold: float | None = None
    low_action: BridgeAction = Field(default_factory=BridgeAction)


class BridgeGuards(BaseModel):
    """Conditions under which vocal direction bridging is skipped."""

    skip_when_short_utterance: bool = True
    skip_when_extreme_short: bool = True
    skip_when_voice_identity_lock: bool = True


class BridgeClamp(BaseModel):
    """Maximum absolute deltas the bridge can produce."""

    max_speed_delta: float = 0.05
    max_vol_delta: float = 0.05


class VocalDirectionBridge(BaseModel):
    """Configuration for bridging 6D vocal direction to provider params."""

    enabled: bool = False
    guards: BridgeGuards = Field(default_factory=BridgeGuards)
    clamp: BridgeClamp = Field(default_factory=BridgeClamp)
    dimension_rules: dict[str, DimensionRule] = Field(default_factory=dict)


class ExpressionScaleConfig(BaseModel):
    """Formula parameters for emotion intensity → expression scale."""

    extreme_short: float = 0.0
    short_base: float = 0.10
    short_intensity_factor: float = 0.20
    normal_base: float = 0.15
    normal_intensity_factor: float = 0.35


class SubEmotionModulation(BaseModel):
    """Energy/tension deltas keyed by sub-emotion name."""

    energy_deltas: dict[str, float] = Field(default_factory=dict)
    tension_deltas: dict[str, float] = Field(default_factory=dict)


class ExpressionProfile(BaseModel):
    """Complete expression adaptation profile for one provider."""

    provider_id: str = "default"
    profile_version: str = "1.0"
    emotion_compensation: dict[str, EmotionCompensationRule] = Field(default_factory=dict)
    tone_hint_rules: list[ToneHintRule] = Field(default_factory=list)
    vocal_direction_bridge: VocalDirectionBridge = Field(default_factory=VocalDirectionBridge)
    expression_scale: ExpressionScaleConfig = Field(default_factory=ExpressionScaleConfig)
    sub_emotion_modulation: SubEmotionModulation = Field(default_factory=SubEmotionModulation)


# ─── Result dataclasses ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CompensationResult:
    """Result of emotion compensation for a degraded emotion."""

    native_emotion: str
    speed_delta: float = 0.0
    vol_delta: float = 0.0
    inject_tags: list[str] = field(default_factory=list)
    compensated: bool = False


@dataclass(frozen=True)
class ToneHintResult:
    """Result of tone_hint pattern matching."""

    matched: bool = False
    inject_prefix_tag: str = ""
    speed_delta: float = 0.0
    vol_delta: float = 0.0


@dataclass(frozen=True)
class BridgeResult:
    """Result of vocal direction bridging."""

    speed_delta: float = 0.0
    vol_delta: float = 0.0
    inject_tags: list[str] = field(default_factory=list)
    applied: bool = False


# ─── ExpressionAdapter ────────────────────────────────────────────────────────


class ExpressionAdapter:
    """Pure-function adapter that resolves expression parameters from a profile.

    Instantiate once per provider at step initialization; call per-segment
    methods for each dubbing segment.
    """

    def __init__(self, profile: ExpressionProfile) -> None:
        self._profile = profile

    @property
    def profile(self) -> ExpressionProfile:
        return self._profile

    @property
    def provider_id(self) -> str:
        return self._profile.provider_id

    def compensate_emotion(
        self,
        emotion: str,
        *,
        intensity: float = 0.5,
        is_short: bool = False,
        is_extreme_short: bool = False,
    ) -> CompensationResult:
        """P0: Resolve emotion compensation for a degraded emotion.

        Returns the native emotion to send to the provider plus any
        speed/vol deltas and interjection tags to inject.
        """
        normalized = emotion.strip().lower()
        rule = self._profile.emotion_compensation.get(normalized)
        if rule is None:
            # No compensation rule — emotion is either native or unmapped
            return CompensationResult(native_emotion=normalized, compensated=False)

        # Scale compensation by intensity (full at 1.0, half at 0.5)
        scale = 0.5 + 0.5 * intensity
        if is_extreme_short:
            scale = 0.0
        elif is_short:
            scale *= 0.5

        return CompensationResult(
            native_emotion=rule.projected_to or normalized,
            speed_delta=rule.speed_delta * scale,
            vol_delta=rule.vol_delta * scale,
            inject_tags=list(rule.inject_tags) if not is_extreme_short else [],
            compensated=True,
        )

    def resolve_tone_hint(self, tone_hint: str) -> ToneHintResult:
        """P1: Match tone_hint against configured patterns.

        Returns the first matching rule's action.
        """
        if not tone_hint or not tone_hint.strip():
            return ToneHintResult(matched=False)

        hint = tone_hint.strip()
        for rule in self._profile.tone_hint_rules:
            for pattern in rule.patterns:
                if pattern in hint:
                    return ToneHintResult(
                        matched=True,
                        inject_prefix_tag=rule.action.inject_prefix_tag,
                        speed_delta=rule.action.speed_delta,
                        vol_delta=rule.action.vol_delta,
                    )
        return ToneHintResult(matched=False)

    def bridge_vocal_direction(
        self,
        direction: dict[str, Any],
        *,
        is_short: bool = False,
        is_extreme_short: bool = False,
        identity_lock: bool = False,
    ) -> BridgeResult:
        """P2: Bridge 6D vocal direction to speed/vol deltas + inject tags.

        Respects guards and clamp from the profile configuration.
        """
        bridge = self._profile.vocal_direction_bridge
        if not bridge.enabled or not direction:
            return BridgeResult(applied=False)

        # Check guards
        guards = bridge.guards
        if guards.skip_when_extreme_short and is_extreme_short:
            return BridgeResult(applied=False)
        if guards.skip_when_short_utterance and is_short:
            return BridgeResult(applied=False)
        if guards.skip_when_voice_identity_lock and identity_lock:
            return BridgeResult(applied=False)

        total_speed = 0.0
        total_vol = 0.0
        tags: list[str] = []

        for dimension, rule in bridge.dimension_rules.items():
            value = direction.get(dimension)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            if rule.high_threshold is not None and value >= rule.high_threshold:
                total_speed += rule.high_action.speed_delta
                total_vol += rule.high_action.vol_delta
                if rule.high_action.inject_tag:
                    tags.append(rule.high_action.inject_tag)
            elif rule.low_threshold is not None and value <= rule.low_threshold:
                total_speed += rule.low_action.speed_delta
                total_vol += rule.low_action.vol_delta
                if rule.low_action.inject_tag:
                    tags.append(rule.low_action.inject_tag)

        # Clamp
        clamp = bridge.clamp
        total_speed = max(-clamp.max_speed_delta, min(clamp.max_speed_delta, total_speed))
        total_vol = max(-clamp.max_vol_delta, min(clamp.max_vol_delta, total_vol))

        applied = abs(total_speed) > 1e-6 or abs(total_vol) > 1e-6 or bool(tags)
        return BridgeResult(
            speed_delta=total_speed,
            vol_delta=total_vol,
            inject_tags=tags,
            applied=applied,
        )

    def resolve_expression_scale(
        self,
        intensity: float,
        *,
        is_short: bool = False,
        is_extreme_short: bool = False,
    ) -> float:
        """Resolve the expression scale factor from intensity and utterance class.

        Replaces the hardcoded ``0.15 + 0.35 * intensity`` formula.
        """
        cfg = self._profile.expression_scale
        if is_extreme_short:
            return cfg.extreme_short
        if is_short:
            return cfg.short_base + cfg.short_intensity_factor * intensity
        return cfg.normal_base + cfg.normal_intensity_factor * intensity

    def has_compensation_rule(self, emotion: str) -> bool:
        """Return whether the profile has a compensation rule for this emotion."""
        return emotion.strip().lower() in self._profile.emotion_compensation

    def resolve_sub_emotion_delta(self, sub_emotion: str) -> tuple[float, float]:
        """Resolve energy and tension deltas for a sub-emotion.

        Returns (energy_delta, tension_delta).
        """
        key = sub_emotion.strip().lower()
        mod = self._profile.sub_emotion_modulation
        energy = mod.energy_deltas.get(key, 0.0)
        tension = mod.tension_deltas.get(key, 0.0)
        return energy, tension


# ─── Factory ──────────────────────────────────────────────────────────────────


def load_expression_adapter(provider_id: str) -> ExpressionAdapter:
    """Load an ExpressionAdapter for the given provider.

    Falls back to the default profile (no compensation, bridge disabled)
    if the provider-specific profile is missing or invalid.
    """
    raw = load_profile_dict(provider_id)
    try:
        profile = ExpressionProfile.model_validate(raw)
    except Exception as exc:
        _log.warning(
            "Invalid expression profile for %s (%s); using default", provider_id, exc
        )
        profile = ExpressionProfile()
    return ExpressionAdapter(profile)


__all__ = [
    "BridgeClamp",
    "BridgeGuards",
    "BridgeResult",
    "CompensationResult",
    "DimensionRule",
    "EmotionCompensationRule",
    "ExpressionAdapter",
    "ExpressionProfile",
    "ExpressionScaleConfig",
    "SubEmotionModulation",
    "ToneHintAction",
    "ToneHintResult",
    "ToneHintRule",
    "VocalDirectionBridge",
    "load_expression_adapter",
]
