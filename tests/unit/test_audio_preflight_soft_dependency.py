"""Tests for soft-dependency grading in live audio preflight.

ASR and ALIGN sidecars are *soft* dependencies: ``AlignSpeechTimelineStep``
degrades gracefully when they are offline (falling back to a segment-scoped
timeline with ``coverage=0.0``).  A live-probe connection failure for these
stages must therefore become a ``WARNING`` (visible but non-blocking) so that a
purely cloud-TTS project is not blocked by a local alignment sidecar it never
needs.  Hard stages (TTS_FORMAL, RENDERER, QUALITY) keep ``FAILED``.

VAD is excluded from ``active_stages`` entirely because no runtime step
consumes the VAD route -- it is frozen into the plan for audit completeness
only.
"""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.tts.platform.config import (
    build_audio_execution_plan,
    registry_from_settings,
)
from novel_forge.tts.platform.preflight import (
    preflight_audio_execution_plan,
    run_live_audio_preflight,
)
from novel_forge.tts.platform.schemas import (
    AudioExecutionStage,
    AudioPreflightStatus,
)


def _make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "audio-models"),
        tts_default_provider="minimax",
        tts_minimax_api_key="test-key",
        audio_memory_budget="high",
    )


class _UnreachableSidecar:
    """A sidecar factory whose every call raises -- simulates offline sidecar."""

    def __init__(self, endpoint: str, *, api_key: str = "") -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    async def health(self):
        raise ConnectionError(f"cannot reach {self.endpoint}")

    async def self_test(self, *, model_id: str = ""):
        raise ConnectionError(f"cannot reach {self.endpoint}")

    async def version(self):
        raise ConnectionError(f"cannot reach {self.endpoint}")

    async def aclose(self) -> None:
        return None


async def test_asr_sidecar_offline_is_warning_not_failed(tmp_path) -> None:
    """ASR sidecar unreachable -> WARNING, report still passes."""
    settings = _make_settings(tmp_path)
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry_from_settings(settings),
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
    )
    await run_live_audio_preflight(
        plan,
        report,
        settings=settings,
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
        sidecar_factory=_UnreachableSidecar,
    )

    asr_health_checks = [
        item
        for item in report.checks
        if item.stage == AudioExecutionStage.ASR and item.kind == "health"
    ]
    assert asr_health_checks, "expected at least one ASR health check"
    for check in asr_health_checks:
        assert check.status == AudioPreflightStatus.WARNING, (
            f"ASR sidecar offline should be WARNING, got {check.status}: {check.message}"
        )
    assert report.passed, "ASR/ALIGN offline must not block synthesis (report.passed)"


async def test_align_sidecar_offline_is_warning_not_failed(tmp_path) -> None:
    """ALIGN sidecar unreachable -> WARNING, report still passes."""
    settings = _make_settings(tmp_path)
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry_from_settings(settings),
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
    )
    await run_live_audio_preflight(
        plan,
        report,
        settings=settings,
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
        sidecar_factory=_UnreachableSidecar,
    )

    align_health_checks = [
        item
        for item in report.checks
        if item.stage == AudioExecutionStage.ALIGN and item.kind == "health"
    ]
    # ALIGN may share a route with ASR (whisperx serves both); either way any
    # ALIGN-tagged health check must be WARNING, never FAILED.
    for check in align_health_checks:
        assert check.status == AudioPreflightStatus.WARNING, (
            f"ALIGN sidecar offline should be WARNING, got {check.status}: {check.message}"
        )
    assert report.passed


async def test_soft_failure_message_mentions_degradation(tmp_path) -> None:
    """The WARNING message should tell the user alignment will degrade."""
    settings = _make_settings(tmp_path)
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry_from_settings(settings),
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
    )
    await run_live_audio_preflight(
        plan,
        report,
        settings=settings,
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
        sidecar_factory=_UnreachableSidecar,
    )

    soft_checks = [
        item
        for item in report.checks
        if item.stage in {AudioExecutionStage.ASR, AudioExecutionStage.ALIGN}
        and item.kind == "health"
        and item.status == AudioPreflightStatus.WARNING
    ]
    assert soft_checks, "expected at least one soft-dependency WARNING"
    for check in soft_checks:
        assert "降级" in check.message or "不影响合成" in check.message, (
            f"WARNING message should mention degradation: {check.message}"
        )


async def test_vad_stage_not_probed_when_excluded(tmp_path) -> None:
    """VAD should not appear in live-probe health checks when excluded from active_stages."""
    settings = _make_settings(tmp_path)
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry_from_settings(settings),
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
    )
    await run_live_audio_preflight(
        plan,
        report,
        settings=settings,
        active_stages={AudioExecutionStage.ASR, AudioExecutionStage.ALIGN},
        sidecar_factory=_UnreachableSidecar,
    )

    vad_live_checks = [
        item
        for item in report.checks
        if item.stage == AudioExecutionStage.VAD
        and item.kind in {"health", "model_self_test"}
        and item.status in {AudioPreflightStatus.FAILED, AudioPreflightStatus.WARNING}
    ]
    # VAD is not in active_stages, so run_live_audio_preflight must not probe it.
    # (Static model_install checks for VAD may still exist from
    # preflight_audio_execution_plan, but those are not live probes.)
    assert not vad_live_checks, (
        f"VAD should not be live-probed when excluded from active_stages, "
        f"got: {[(c.kind, c.status) for c in vad_live_checks]}"
    )
