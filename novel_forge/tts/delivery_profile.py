"""Resolve the user-facing delivery tier into explicit rendering policy.

The execution-plan preset selects providers and plugins.  The delivery tier is
orthogonal: it decides how expensive the final master is allowed to be and
which objective gates are required before a chapter can be delivered.  Keeping
the two concepts separate prevents a ``commercial`` request from silently
behaving like a normal production render when the route plan itself is not
``MASTER``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AudioDeliveryTier = Literal["audition", "standard", "commercial"]


@dataclass(frozen=True)
class AudioDeliveryProfile:
    """Final-render policy derived from ``tts_audio_quality_tier``."""

    tier: AudioDeliveryTier
    mastering_mode: Literal["single_pass", "two_pass_loudnorm"]
    include_cross_chapter_reference: bool
    require_master_quality: bool
    export_stems: bool


_PROFILES: dict[AudioDeliveryTier, AudioDeliveryProfile] = {
    "audition": AudioDeliveryProfile(
        tier="audition",
        mastering_mode="single_pass",
        include_cross_chapter_reference=False,
        require_master_quality=False,
        export_stems=False,
    ),
    "standard": AudioDeliveryProfile(
        tier="standard",
        mastering_mode="two_pass_loudnorm",
        include_cross_chapter_reference=True,
        require_master_quality=False,
        export_stems=False,
    ),
    "commercial": AudioDeliveryProfile(
        tier="commercial",
        mastering_mode="two_pass_loudnorm",
        include_cross_chapter_reference=True,
        require_master_quality=True,
        export_stems=True,
    ),
}


def resolve_audio_delivery_profile(value: str | None) -> AudioDeliveryProfile:
    """Return a validated delivery policy, defaulting invalid persisted values."""

    normalized = str(value or "standard").strip().lower()
    return _PROFILES.get(normalized, _PROFILES["standard"])
