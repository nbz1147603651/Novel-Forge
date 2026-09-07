"""Deterministic sound style profiles and seed control for cross-chapter consistency.

A style profile anchors the sonic identity of a project so that SFX, soundscapes
and BGM generated across different chapters share a coherent spectral character.
The deterministic seed derivation guarantees that the same cue type produces
reproducible results regardless of which chapter triggers generation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest

_STYLE_PROFILE_FILENAME = "sound_style_profile.json"


class SoundStyleProfile(VersionedSchema):
    """Project-level sonic identity anchor for generated audio assets."""

    profile_id: str = Field(default="default", min_length=1)
    base_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    sfx_negative_prompt: str = Field(
        default="speech, narration, singing, watermark, clipping, digital artifacts",
        description="Negative prompt appended to all SFX generation requests.",
    )
    soundscape_negative_prompt: str = Field(
        default="speech, music, abrupt events, watermark, hard loop seam",
        description="Negative prompt appended to all soundscape generation requests.",
    )
    bgm_negative_prompt: str = Field(
        default="speech, narration, singing, lyrics, watermark, clipping, aggressive mastering",
        description="Negative prompt appended to all BGM generation requests.",
    )
    style_tags: list[str] = Field(
        default_factory=list,
        description="Global style descriptors injected into prompts, e.g. ['warm', 'analog', 'close-mic'].",
    )
    spectral_hint: str = Field(
        default="",
        description="Free-form spectral guidance, e.g. 'avoid sub-bass rumble below 60Hz'.",
    )

    def negative_prompt_for(self, kind: SoundGenerationKind) -> str:
        """Return the kind-specific negative prompt."""
        if kind == SoundGenerationKind.SFX:
            return self.sfx_negative_prompt
        if kind == SoundGenerationKind.SOUNDSCAPE:
            return self.soundscape_negative_prompt
        return self.bgm_negative_prompt

    def style_suffix(self) -> str:
        """Return a compact style tag string for prompt injection."""
        if not self.style_tags:
            return ""
        return f"Style: {', '.join(self.style_tags)}."


def derive_deterministic_seed(
    profile: SoundStyleProfile,
    request: SoundGenerationRequest,
) -> int:
    """Derive a reproducible seed from the project profile and cue identity.

    The seed is stable across chapters: the same kind + cue_label + style_tags
    always produces the same seed, ensuring sonic consistency for recurring
    sound types (e.g. a specific door creak always sounds similar).
    """
    identity_payload = json.dumps(
        {
            "kind": request.kind.value,
            "cue_label": request.cue_label,
            "style_tags": sorted(profile.style_tags),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
    # Use first 8 hex chars (32 bits) to stay within a reasonable seed range.
    offset = int(digest[:8], 16)
    return (profile.base_seed + offset) % (2**31)


def load_style_profile(layout: ProjectLayout) -> SoundStyleProfile:
    """Load the project sound style profile, returning defaults if absent."""
    profile_path = layout.tts_dir / _STYLE_PROFILE_FILENAME
    if not profile_path.is_file():
        return SoundStyleProfile()
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
        return SoundStyleProfile.model_validate(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return SoundStyleProfile()


def save_style_profile(layout: ProjectLayout, profile: SoundStyleProfile) -> Path:
    """Persist the sound style profile to the project TTS directory."""
    profile_path = layout.tts_dir / _STYLE_PROFILE_FILENAME
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        profile.model_dump_json(indent=2, exclude={"schema_version", "created_at"}),
        encoding="utf-8",
    )
    return profile_path
