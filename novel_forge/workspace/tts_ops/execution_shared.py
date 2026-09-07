"""
Shared infrastructure for TTS workspace executions.

Extracted from execution.py: cross-domain helpers (progress events, JSON
loading, provider resolution, upstream context projection, voice-team
loading and diagnosis reason constants).  Kept import-free of sibling
domain modules so the dependency graph stays acyclic:
shared <- assets <- voice_team <- script <- synthesis <- export.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.factory import (
    registered_tts_provider_ids,
)
from novel_forge.tts.model_center.service import AudioModelCenterService
from novel_forge.tts.platform.schemas import (
    AudioExecutionPlan,
    AudioExecutionStage,
)
from novel_forge.tts.runtime.performance_policy import (
    hydrate_voice_team_performance_profiles,
)
from novel_forge.tts.schemas import (
    ChapterTTSMetadata,
    TTSProvider,
    VoiceTeamContract,
)
from novel_forge.tts.script_integrity import (
    compute_source_text_hash,
)
from novel_forge.workspace.tts_ops.preflight import (  # noqa: F401
    _enrich_preflight_failure,
    _preflight_failure_guidance,
    _preflight_failure_severity,
)
from novel_forge.workspace.tts_ops.voice_assignment import (  # noqa: F401
    _assign_narrator_voice,
    _narrator_profile_content_hash,
    _narrator_voice_match_input,
)

_log = get_logger("workspace.tts")

async def _ensure_managed_tts_runtime(settings: Settings, provider: TTSProvider) -> None:
    """Start an installed application-managed TTS sidecar on first real use."""

    if provider != TTSProvider.QWEN3:
        return
    try:
        state = await AudioModelCenterService(settings).ensure_runtime_ready("qwen3-tts")
    except Exception as exc:
        # Preview/synthesis still performs its normal provider health check and
        # reports the transport failure.  Keeping this best-effort avoids
        # replacing the richer preflight report with a lifecycle exception.
        _log.warning("Unable to start managed Qwen3-TTS runtime on demand: %s", exc)
        return
    if not state.environment_path:
        _log.info("Managed Qwen3-TTS runtime is not installed; provider preflight will report it")


async def _ensure_managed_plan_runtimes(
    settings: Settings,
    plan: AudioExecutionPlan,
    *,
    active_stages: set[AudioExecutionStage],
) -> None:
    """Start installed sidecars selected by the executable audio plan."""

    required_stages = set(active_stages)
    required_stages.add(AudioExecutionStage.TTS_FORMAL)
    plugin_ids = {
        route.primary.plugin_id
        for route in plan.routes
        if route.stage in required_stages and route.primary is not None
    }
    if not plugin_ids:
        return
    try:
        await AudioModelCenterService(settings).ensure_runtimes_for_plugins(plugin_ids)
    except Exception as exc:
        # The following live preflight remains the source of truth and reports
        # each hard/soft dependency with its concrete endpoint and model.
        _log.warning("Unable to start one or more managed audio runtimes: %s", exc)


def _emit_tts_progress(callback: Any, event: str, data: dict[str, Any]) -> None:
    """Deliver a non-critical UI progress event without breaking persistence.

    Delegates to the shared :func:`forward_step_event` so the event-forwarding
    logic lives in one place alongside ``TracedStep._emit``.
    """
    from novel_forge.pipeline.steps.base import forward_step_event  # noqa: PLC0415

    forward_step_event(callback, _log, event, data)


def _load_json(path: Path) -> dict[str, Any]:
    """Load an optional project artifact without making legacy projects fail."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("Failed to load optional TTS artifact %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _stable_hash(value: Any) -> str:
    """Create a stable short fingerprint for cache/checkpoint validation."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _resolve_provider(settings: Settings, provider: str = "") -> TTSProvider:
    """Resolve a provider value with one consistent validation boundary."""
    raw = (provider or settings.tts_default_provider).strip().lower()
    supported_ids = registered_tts_provider_ids()
    if raw not in supported_ids:
        supported = ", ".join(supported_ids)
        raise ValueError(f"Unsupported TTS provider {raw!r}; supported: {supported}")
    try:
        return TTSProvider(raw)
    except ValueError as exc:
        supported = ", ".join(supported_ids)
        raise ValueError(f"Unsupported TTS provider {raw!r}; supported: {supported}") from exc


def _load_voice_team(layout: ProjectLayout) -> VoiceTeamContract | None:
    if not layout.tts_voice_team_path.exists():
        return None
    try:
        team = VoiceTeamContract.model_validate(_load_json(layout.tts_voice_team_path))
        upstream = _load_tts_upstream_context(layout, chapter_number=1)
        characters = _merge_character_inputs(
            [],
            upstream["characters"],
            upstream["character_voices"],
        )
        return hydrate_voice_team_performance_profiles(team, characters)
    except Exception as exc:
        _log.warning("Invalid voice team %s: %s", layout.tts_voice_team_path, exc)
        return None


REASON_MISSING = "missing"


REASON_PROVIDER_MISMATCH = "provider_mismatch"


REASON_CLONE_NOT_READY = "clone_not_ready"


REASON_PENDING_APPROVAL = "pending_approval"


REASON_EXPIRED = "expired"


REASON_NARRATOR_UNAVAILABLE = "narrator_unavailable"


REASON_UNKNOWN = "unknown"


RETRYABLE_REASONS = frozenset(
    {
        REASON_PENDING_APPROVAL,
        REASON_EXPIRED,
        REASON_NARRATOR_UNAVAILABLE,
    }
)


def _source_text_hash(text: str) -> str:
    """Match the compact source hash persisted by GenerateDubbingScriptStep."""
    return compute_source_text_hash(text)


def _normalize_style_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten the persisted ProjectStyleProfile for the TTS prompt contract."""
    if not payload:
        return {}
    result = dict(payload)
    global_style = payload.get("global_style")
    if isinstance(global_style, dict):
        pace_mode = str(global_style.get("pace_mode", "") or "")
        emotional_style = str(global_style.get("emotional_style", "") or "")
        result.setdefault("narrative_pace", pace_mode)
        result.setdefault("emotional_tone", emotional_style)
        result.setdefault(
            "speed_range", {"fast": "1.0~1.3", "slow": "0.8~1.0"}.get(pace_mode, "0.8~1.2")
        )
    if not result.get("literary_style"):
        modules = payload.get("modules")
        if isinstance(modules, list):
            names = [str(item.get("name", "")) for item in modules if isinstance(item, dict)]
            result["literary_style"] = "、".join(name for name in names if name)
    return result


def _load_tts_upstream_context(
    layout: ProjectLayout,
    chapter_number: int,
) -> dict[str, Any]:
    """Load the bounded, authoritative upstream artifacts used by TTS.

    This is intentionally filesystem-based so Desktop, API and CLI executions
    consume the same project state.  The TTS module receives projections, not
    entire runtime objects, and every optional artifact degrades gracefully.
    """
    spec = _load_json(layout.spec_path)
    story_bible = _load_json(layout.bible_path)
    characters_payload = _load_json(layout.characters_path)
    raw_characters = characters_payload.get("characters", [])
    characters = raw_characters if isinstance(raw_characters, list) else []
    style_profile = _normalize_style_profile(_load_json(layout.style_profile_path))
    outline = _load_json(layout.outline_path)
    editorial_contract = _load_json(layout.editorial_contract_path)
    plan = _load_json(layout.chapter_plan_path(chapter_number))
    raw_scene_intents = plan.get("scene_intents", [])
    scene_intents = raw_scene_intents if isinstance(raw_scene_intents, list) else []

    metadata_payload = _load_json(
        layout.reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json"
    )
    tts_metadata: ChapterTTSMetadata | None = None
    if metadata_payload:
        try:
            candidate_metadata = ChapterTTSMetadata.model_validate(metadata_payload)
            chapter_path = layout.chapter_path(chapter_number)
            source_text = (
                chapter_path.read_text(encoding="utf-8") if chapter_path.is_file() else ""
            )
            expected_hash = compute_source_text_hash(source_text) if source_text else ""
            wrong_chapter = bool(
                candidate_metadata.chapter_number
                and candidate_metadata.chapter_number != chapter_number
            )
            stale_source = bool(
                candidate_metadata.source_text_hash
                and expected_hash
                and candidate_metadata.source_text_hash != expected_hash
            )
            if wrong_chapter or stale_source:
                _log.warning(
                    "Ignoring stale chapter TTS metadata for %d "
                    "(stored_chapter=%d stored_hash=%s expected_hash=%s)",
                    chapter_number,
                    candidate_metadata.chapter_number,
                    candidate_metadata.source_text_hash or "legacy",
                    expected_hash or "missing",
                )
            else:
                tts_metadata = candidate_metadata
        except Exception as exc:
            _log.warning("Invalid chapter TTS metadata for %d: %s", chapter_number, exc)

    character_voices = editorial_contract.get("character_voices", [])
    if not isinstance(character_voices, list):
        character_voices = []

    # The prompt expects a compact chapter-level context.  The complete scene
    # list is passed separately via scene_intents.
    first_scene = scene_intents[0] if scene_intents and isinstance(scene_intents[0], dict) else {}
    scene_context = {
        "location": first_scene.get("location", ""),
        "time_frame": first_scene.get("time_marker", ""),
        "atmosphere": first_scene.get("emotional_beat", ""),
        "intent": first_scene.get("purpose", first_scene.get("summary", "")),
        "total_scenes": len(scene_intents),
    }
    global_style = style_profile.get("global_style")
    if not isinstance(global_style, dict):
        global_style = {}
    story_sound_context = {
        "title": story_bible.get("title") or spec.get("title", ""),
        "genre": spec.get("genre", ""),
        "theme": spec.get("theme", ""),
        "premise": story_bible.get("premise") or spec.get("theme", ""),
        "tone": story_bible.get("tone")
        or spec.get("tone")
        or global_style.get("emotional_style", ""),
        "themes": story_bible.get("themes") or [],
        "era": story_bible.get("era", ""),
        "world": "；".join(
            str(value).strip()
            for value in (
                story_bible.get("era"),
                story_bible.get("geography"),
                story_bible.get("culture"),
            )
            if str(value or "").strip()
        ),
        "audio_aesthetic": story_bible.get("audio_aesthetic")
        or spec.get("audio_aesthetic_hint", ""),
    }
    location_acoustics = _project_location_acoustics(story_bible)
    upstream_revision = {
        "story_bible": _file_revision(layout.bible_path),
        "character_bible": _file_revision(layout.characters_path),
    }
    upstream_revision = {
        key: value for key, value in upstream_revision.items() if value
    }
    return {
        "spec": spec,
        "story_bible": story_bible,
        "characters": characters,
        "style_profile": style_profile,
        "outline": outline,
        "editorial_contract": editorial_contract,
        "character_voices": character_voices,
        "scene_intents": scene_intents,
        "scene_context": scene_context,
        "tts_metadata": tts_metadata,
        "story_sound_context": story_sound_context,
        "location_acoustics": location_acoustics,
        "upstream_revision": upstream_revision,
    }


def _file_revision(path: Path) -> str:
    """Content hash fingerprint of an upstream artifact (film-style revision).

    Mirrors ``film/source_projection.py::_revision()``: TTS stages record
    these revisions so upstream changes can be detected ("来源已更新")
    without diffing entire documents.
    """
    if not path.exists() or not path.is_file():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _project_location_acoustics(story_bible: dict[str, Any]) -> list[dict[str, Any]]:
    """Project StoryBible locations into bounded acoustic seed cards.

    Each card carries only the acoustic-relevant fields so downstream TTS
    stages (dubbing script context, sound design) treat initialization-time
    definitions as seeds instead of re-inferring soundscapes from prose.
    """
    acoustics: list[dict[str, Any]] = []
    for key in ("locations", "key_locations", "settings"):
        raw = story_bible.get(key)
        if isinstance(raw, list) and raw:
            for item in raw[:20]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("location") or "").strip()
                ambient = item.get("ambient_sound") or item.get("sound") or []
                if isinstance(ambient, list):
                    ambient_list = [
                        str(value).strip() for value in ambient if str(value or "").strip()
                    ]
                elif str(ambient or "").strip():
                    ambient_list = [str(ambient).strip()]
                else:
                    ambient_list = []
                if not name or not ambient_list:
                    continue
                acoustics.append(
                    {
                        "name": name,
                        "location_id": str(item.get("location_id") or item.get("id") or ""),
                        "ambient_sound": ambient_list[:6],
                        "audio_aesthetic": str(item.get("audio_aesthetic") or ""),
                    }
                )
            break
    return acoustics


def _merge_character_inputs(
    characters: list[dict[str, Any]],
    upstream_characters: list[Any],
    character_voices: list[Any],
) -> list[dict[str, Any]]:
    """Merge caller input with CharacterBible and EditorialContract projections."""
    by_key: dict[str, dict[str, Any]] = {}
    for raw in upstream_characters:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = str(item.get("character_id") or item.get("name") or "").strip()
        if key:
            by_key[key] = item
            if item.get("name"):
                by_key[str(item["name"])] = item

    voice_by_name: dict[str, dict[str, Any]] = {}
    for raw in character_voices:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("character") or raw.get("name") or "").strip()
        if name:
            voice_by_name[name] = raw

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_inputs = list(characters)
    if characters:
        source_inputs.extend(
            raw
            for raw in upstream_characters
            if isinstance(raw, dict)
            and str(raw.get("character_id") or raw.get("name") or "").strip()
            not in {
                str(item.get("character_id") or item.get("name") or "").strip()
                for item in characters
                if isinstance(item, dict)
            }
        )
    else:
        source_inputs = list(upstream_characters)
    for raw in source_inputs:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("character_id") or raw.get("name") or "").strip()
        base = dict(by_key.get(key, {}))
        base.update({k: v for k, v in raw.items() if v not in (None, "", [])})
        name = str(base.get("name") or key).strip()
        voice_profile = voice_by_name.get(name, {})
        hints = voice_profile.get("tts_voice_hints")
        if hints and not base.get("tts_voice_hints"):
            base["tts_voice_hints"] = hints
        if voice_profile and not base.get("voice_description"):
            voice_description = "；".join(
                str(voice_profile.get(field, "") or "")
                for field in (
                    "sentence_profile",
                    "explanation_bias",
                    "emotion_syntax",
                    "subtext_bias",
                    "signature_moves",
                )
                if voice_profile.get(field)
            )
            if voice_description:
                base["voice_description"] = voice_description
        character_id = str(base.get("character_id") or name).strip()
        base["character_id"] = character_id
        if character_id in seen:
            continue
        seen.add(character_id)
        merged.append(base)
    return merged
