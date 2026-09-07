"""
Voice-library and audio asset persistence.

Extracted from execution.py: preview cache management, audio execution
plan loading, voice-library persistence and MiniMax voice activation.
Depends only on execution_shared.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    atomic_write_bytes,
    atomic_write_json,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.voice_library import (
    VoiceLibrary,
    VoiceLibraryEntry,
    VoiceLibraryUsage,
)
from novel_forge.tts.gateway.factory import (
    resolve_tts_model,
)
from novel_forge.tts.platform.config import build_audio_execution_plan
from novel_forge.tts.platform.schemas import (
    AudioExecutionPlan,
)
from novel_forge.tts.runtime.audio_runtime import apply_portable_controls, remux_for_qt_playback
from novel_forge.tts.schemas import (
    NarratorVoiceProfile,
    TTSProvider,
    TTSRequest,
    TTSResponse,
    VoiceTeamContract,
)
from novel_forge.workspace.tts_ops.execution_shared import (
    _load_json,
    _load_voice_team,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    bounded_preview_timeout as _bounded_preview_timeout,
)
from novel_forge.workspace.tts_ops.lock_manager import (
    tts_voice_library_lock as _tts_voice_library_lock,
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

async def _synthesize_preview_with_timeout(
    *,
    adapter: Any,
    request: TTSRequest,
    settings: Settings,
) -> TTSResponse:
    """Bound a live provider preview while preserving cancellation semantics."""
    timeout_s = _bounded_preview_timeout(
        settings.tts_preview_synthesis_timeout_s,
        default=45.0,
        maximum=300.0,
    )
    try:
        return await asyncio.wait_for(adapter.synthesize(request), timeout=timeout_s)
    except TimeoutError as exc:
        raise TimeoutError(
            f"试听合成在 {timeout_s:g} 秒内未收到 {request.provider.value} 的音频结果；"
            "请检查平台连接后重试。"
        ) from exc


async def _persist_voice_library_updates(
    *,
    storage_root: Path,
    new_entries: list[VoiceLibraryEntry],
    usage: list[VoiceLibraryUsage],
) -> list[VoiceLibraryEntry]:
    """Merge voice-library mutations under one global cross-process lock.

    A voice-team build is project-scoped, while the library is shared by all
    projects.  Reloading while holding this lock prevents two simultaneous
    projects from overwriting each other's additions or usage counters.
    """
    if not new_entries and not usage:
        return []
    try:
        async with _tts_voice_library_lock(storage_root):
            library = VoiceLibrary.load(storage_root)
            for entry in new_entries:
                library.add_or_update(entry)
            for hit in usage:
                library.increment_usage(hit.voice_id, hit.provider)
            library.save(storage_root)
    except Exception as exc:
        _log.warning("Failed to persist global voice library updates: %s", exc)
        return []
    return list(new_entries)


async def _clear_voice_library_activation_deadlines(
    *,
    storage_root: Path,
    voice_ids: set[str],
    provider: TTSProvider | None = None,
) -> bool:
    """Persist one-time provider activation after successful speech synthesis."""
    if not voice_ids:
        return False
    try:
        async with _tts_voice_library_lock(storage_root):
            library = VoiceLibrary.load(storage_root)
            if not library.clear_activation_deadlines(voice_ids, provider):
                return False
            library.save(storage_root)
    except Exception as exc:
        _log.warning("Failed to persist voice-library activation state: %s", exc)
        return False
    return True


def _recent_audio_reference_lufs(
    layout: ProjectLayout,
    chapter_number: int,
    *,
    lookback: int = 5,
) -> list[float]:
    """Load recent passed chapter loudness measurements for whole-book continuity."""

    values: list[float] = []
    for number in range(chapter_number - 1, max(0, chapter_number - lookback - 1), -1):
        payload = _load_json(layout.tts_audio_quality_report_path(number))
        value = payload.get("integrated_lufs")
        if payload.get("passed") and isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _load_audio_execution_plan(layout: ProjectLayout) -> AudioExecutionPlan:
    """Load the one authoritative frozen route plan for this project."""
    path = layout.tts_execution_plan_path
    if not path.is_file():
        raise FileNotFoundError("项目尚未生成 AudioExecutionPlan")
    plan = AudioExecutionPlan.model_validate(_load_json(path))
    if not plan.frozen or not plan.manifest_digest:
        raise ValueError("AudioExecutionPlan 未冻结，不能进入正式生产")
    return plan


def _load_or_freeze_audio_execution_plan(
    *,
    project_id: str,
    settings: Settings,
    layout: ProjectLayout,
    languages: list[str],
) -> AudioExecutionPlan:
    """Load the project authority, creating it exactly once at production boundary."""
    try:
        return _load_audio_execution_plan(layout)
    except FileNotFoundError:
        plan = build_audio_execution_plan(
            settings,
            languages=languages,
            scorecard_path=layout.tts_model_scorecards_path,
            project_id=project_id,
        )
        atomic_write_json(layout.tts_execution_plan_path, plan.model_dump(mode="json"))
        return plan


def _sync_narrator_voice_team_binding(
    *,
    layout: ProjectLayout,
    settings: Settings,
    profile: NarratorVoiceProfile,
) -> None:
    """Keep the legacy team-level narrator binding aligned with the profile."""
    if not profile.voice_id:
        return
    team = _load_voice_team(layout)
    if team is None:
        team = VoiceTeamContract(
            narrator_voice_id=profile.voice_id,
            narrator_provider=profile.provider,
            default_provider=profile.provider,
            default_tts_model=resolve_tts_model(settings, profile.provider),
        )
    else:
        if (
            team.narrator_voice_id == profile.voice_id
            and team.narrator_provider == profile.provider
        ):
            return
        team.narrator_voice_id = profile.voice_id
        team.narrator_provider = profile.provider
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(layout.tts_voice_team_path, team.model_dump(mode="json"))


def _preview_cache_path(
    layout: ProjectLayout,
    *,
    target_id: str,
    fingerprint: str,
    audio_format: str,
) -> Path:
    """Return the durable cache location for one exact preview request."""
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in target_id)
    return layout.tts_audio_dir(0) / f"preview_{safe_id}_{fingerprint}.{audio_format}"


def _existing_preview_cache_path(
    layout: ProjectLayout,
    *,
    target_id: str,
    fingerprint: str,
) -> Path | None:
    """Find a cached preview even when a provider chose another audio suffix."""
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in target_id)
    candidates = sorted(layout.tts_audio_dir(0).glob(f"preview_{safe_id}_{fingerprint}.*"))
    return next((path for path in candidates if path.is_file()), None)


def _preview_format(settings: Settings, response_format: str = "") -> str:
    """Normalize a provider's preview format to a supported file extension."""
    audio_format = str(response_format or settings.tts_output_format).strip().lower()
    return audio_format if audio_format in {"mp3", "wav", "flac", "pcm", "ogg", "m4a"} else "mp3"


def _write_preview_audio(preview_path: Path, audio_data: bytes) -> None:
    """Persist an audition clip in a form Qt's player can decode.

    Some providers (notably MiniMax) prepend a large AIGC-watermark ID3 tag
    that Qt's FFmpeg probe fails to see past (it reports ``InvalidMedia`` and
    the audition plays no sound).  Remux through FFmpeg with a generous probe
    so the cache file is always cleanly playable; fall back to a raw write if
    FFmpeg is unavailable so the path degrades rather than breaks.
    """
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=preview_path.suffix, delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = Path(tmp.name)
        try:
            if remux_for_qt_playback(tmp_path, preview_path):
                return
            # Remux failed: keep the raw bytes so the file at least exists.
            atomic_write_bytes(preview_path, audio_data)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        _log.warning("Preview remux failed for %s; writing raw bytes", preview_path)
        atomic_write_bytes(preview_path, audio_data)


def _allows_parallel_voice_build(provider: TTSProvider) -> bool:
    """Return whether a provider benefits from parallel voice operations."""
    return provider not in {
        TTSProvider.LOCAL,
        TTSProvider.QWEN3,
        TTSProvider.COSYVOICE,
        TTSProvider.OPENVOICE,
    }


async def _apply_preview_audio_controls(
    *,
    settings: Settings,
    audio_data: bytes,
    request: TTSRequest,
    audio_format: str,
    label: str,
) -> bytes:
    """Run local preview post-processing under the shared CPU budget."""
    broker = configure_local_model_resources(settings)
    async with broker.lease(
        LocalResourceRequest(
            workload="audio_postprocess",
            label=label,
            memory_class=LocalMemoryClass.LIGHT,
            accelerator=False,
            cpu_heavy=True,
            priority=LocalResourcePriority.INTERACTIVE,
            timeout_s=float(settings.local_model_resource_wait_timeout_s),
        )
    ):
        return await asyncio.to_thread(
            apply_portable_controls,
            audio_data,
            request=request,
            audio_format=audio_format,
        )
