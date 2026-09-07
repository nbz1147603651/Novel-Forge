"""Shared automation policy for every Voice Studio entry point."""

from __future__ import annotations

from enum import Enum

from novel_forge.core.config import Settings


class AudioAutomationMode(str, Enum):
    """How much authority the dubbing pipeline may exercise without review."""

    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"


def resolve_audio_automation_mode(
    value: AudioAutomationMode | str | None,
    *,
    settings: Settings,
) -> AudioAutomationMode:
    """Resolve a request override, falling back to the platform default."""

    raw = value.value if isinstance(value, AudioAutomationMode) else str(value or "").strip()
    if not raw:
        raw = str(getattr(settings, "tts_automation_mode", "") or "").strip()
    aliases = {
        "ai_assisted": AudioAutomationMode.ASSISTED.value,
        "suggest": AudioAutomationMode.ASSISTED.value,
        "ai_auto": AudioAutomationMode.AUTONOMOUS.value,
        "auto": AudioAutomationMode.AUTONOMOUS.value,
    }
    raw = aliases.get(raw, raw)
    try:
        return AudioAutomationMode(raw)
    except ValueError:
        return AudioAutomationMode.ASSISTED


def settings_for_audio_automation_mode(
    settings: Settings,
    mode: AudioAutomationMode | str | None = None,
) -> tuple[AudioAutomationMode, Settings]:
    """Return one effective settings snapshot derived from the selected mode.

    The three low-level sound-generation switches are deliberately not allowed
    to drift away from the user-facing mode. Existing approved library assets
    remain resolvable in every mode.
    """

    resolved = resolve_audio_automation_mode(mode, settings=settings)
    sound_policy = {
        AudioAutomationMode.MANUAL: {
            "sound_generation_enabled": False,
            "sound_generation_auto_generate": False,
            "sound_generation_auto_approve": False,
        },
        AudioAutomationMode.ASSISTED: {
            "sound_generation_enabled": True,
            "sound_generation_auto_generate": True,
            "sound_generation_auto_approve": False,
        },
        AudioAutomationMode.AUTONOMOUS: {
            "sound_generation_enabled": True,
            "sound_generation_auto_generate": True,
            "sound_generation_auto_approve": True,
        },
    }[resolved]
    return resolved, settings.model_copy(
        update={
            "tts_automation_mode": resolved.value,
            **sound_policy,
        }
    )


__all__ = [
    "AudioAutomationMode",
    "resolve_audio_automation_mode",
    "settings_for_audio_automation_mode",
]
