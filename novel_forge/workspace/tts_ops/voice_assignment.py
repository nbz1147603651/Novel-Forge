"""Narrator voice assignment and matching logic.

Extracted from execution.py to isolate voice assignment concerns.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.factory import (
    TTSAdapterRegistry,
    resolve_tts_model,
)
from novel_forge.tts.pipeline.build_narrator_profile_step import (
    build_narrator_voice_design_prompt,
)
from novel_forge.tts.pipeline.build_voice_team_step import match_system_voice
from novel_forge.tts.schemas import (
    NarratorVoiceProfile,
    TTSProvider,
    VoiceCloneStatus,
    VoiceDesignRequest,
)

_log = get_logger("workspace.tts")


def _stable_hash(value: Any) -> str:
    """Create a stable short fingerprint for cache/checkpoint validation."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _emit_tts_progress(callback: Any, event: str, data: dict[str, Any]) -> None:
    """Deliver a non-critical UI progress event without breaking persistence."""
    from novel_forge.pipeline.steps.base import forward_step_event  # noqa: PLC0415

    forward_step_event(callback, _log, event, data)


def _narrator_profile_content_hash(profile: NarratorVoiceProfile | None) -> str:
    if profile is None:
        return ""
    return _stable_hash(
        profile.model_dump(
            mode="json",
            exclude={
                "created_at",
                "updated_at",
                "identity_locked",
                "identity_locked_at",
            },
        )
    )


def _narrator_voice_match_input(profile: NarratorVoiceProfile) -> dict[str, Any]:
    """Project a narrator profile into the shared catalog-matching input shape."""
    return {
        "name": "旁白",
        "role": "narrator",
        "personality": profile.emotional_range,
        "voice_description": " ".join(
            part
            for part in (
                profile.voice_type,
                " ".join(profile.style_keywords),
                profile.narration_distance,
            )
            if part
        ),
    }


async def _assign_narrator_voice(
    *,
    profile: NarratorVoiceProfile,
    settings: Settings,
    provider: TTSProvider,
    on_step_progress: Any = None,
    project_language: str = "",
) -> NarratorVoiceProfile:
    """Give a narrator profile an actual provider voice, with a safe fallback."""
    if profile.voice_id:
        resolved = profile.model_copy(
            update={
                "provider": provider,
                "model_id": profile.model_id or resolve_tts_model(settings, provider),
                "voice_source": "manual",
                "expires_at": None,
                "activation_deadline": None,
            }
        )
        _emit_tts_progress(
            on_step_progress,
            "narrator_voice_ready",
            {"source": "manual", "voice_id": resolved.voice_id},
        )
        return resolved

    prompt = build_narrator_voice_design_prompt(profile)
    adapter = TTSAdapterRegistry.get_instance(settings).get_adapter(provider)
    try:
        capabilities = await adapter.discover_capabilities()
    except Exception as exc:
        _log.warning(
            "Unable to inspect narrator voice capabilities for %s: %s", provider.value, exc
        )
        capabilities = None

    if capabilities is not None and capabilities.voice_design:
        try:
            response = await adapter.design_voice(
                VoiceDesignRequest(
                    description=prompt,
                    preview_text=(profile.sample_narration_text or "故事从这一刻开始。")[:200],
                    model_id=resolve_tts_model(settings, provider, purpose="design"),
                    provider=provider,
                )
            )
            if response.status == VoiceCloneStatus.READY and response.voice_id:
                resolved = profile.model_copy(
                    update={
                        "voice_id": response.voice_id,
                        "model_id": response.model_id,
                        "provider": provider,
                        "voice_source": "designed",
                        "expires_at": response.expires_at,
                        "activation_deadline": response.activation_deadline,
                        "voice_design_prompt": prompt,
                    }
                )
                _emit_tts_progress(
                    on_step_progress,
                    "narrator_voice_ready",
                    {"source": "designed", "voice_id": resolved.voice_id},
                )
                return resolved
            fallback_detail = response.message or "Provider returned no ready narrator voice"
        except Exception as exc:
            fallback_detail = str(exc)
            _log.warning("Narrator voice design failed for %s: %s", provider.value, exc)
    else:
        fallback_detail = "当前平台不支持旁白音色设计"

    available_voices: list[dict[str, Any]] = []
    try:
        available_voices = await adapter.list_system_voices(
            limit=500,
            language=project_language or None,
        )
    except Exception as exc:
        _log.warning("Unable to load narrator system voice catalog for %s: %s", provider.value, exc)
    narrator_match_input = _narrator_voice_match_input(profile)
    if project_language:
        narrator_match_input["project_language"] = project_language
    voice_id = match_system_voice(
        narrator_match_input,
        available_voices=available_voices,
        provider=provider,
    )
    resolved = profile.model_copy(
        update={
            "voice_id": voice_id,
            "model_id": resolve_tts_model(settings, provider),
            "provider": provider,
            "voice_source": "system",
            "expires_at": None,
            "activation_deadline": None,
            "voice_design_prompt": prompt,
        }
    )
    _emit_tts_progress(
        on_step_progress,
        "narrator_voice_fallback",
        {"voice_id": resolved.voice_id, "detail": fallback_detail},
    )
    return resolved
