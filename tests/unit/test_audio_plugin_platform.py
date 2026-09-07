"""Tests for capability-driven audio plugin registration and planning."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from novel_forge.core.config import Settings
from novel_forge.tts.platform.benchmark import (
    run_audio_benchmark,
    save_audio_benchmark_report,
)
from novel_forge.tts.platform.config import (
    build_audio_execution_plan,
    constraints_from_settings,
    registry_from_settings,
)
from novel_forge.tts.platform.field_mapping import (
    FieldSupport,
    VocalDirectionField,
    provider_field_profile,
    provider_language_value,
)
from novel_forge.tts.platform.planner import AudioExecutionPlanner
from novel_forge.tts.platform.preflight import (
    preflight_audio_execution_plan,
    run_live_audio_preflight,
)
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.routing import route_segment
from novel_forge.tts.platform.schemas import (
    AudioBenchmarkSample,
    AudioCapability,
    AudioExecution,
    AudioExecutionStage,
    AudioHardwareProfile,
    AudioLocationPolicy,
    AudioMemoryClass,
    AudioPluginCapabilities,
    AudioPluginManifest,
    AudioPluginRuntime,
    AudioPreflightStatus,
    AudioProjectConstraints,
    AudioQualityPreset,
)
from novel_forge.tts.platform.sidecar import AudioSidecarClient
from novel_forge.tts.schemas import DubbingSegment, LanguageRun, SegmentType


def _target(plan, stage: AudioExecutionStage):  # type: ignore[no-untyped-def]
    route = plan.assignment_for(stage)
    assert route is not None and route.primary is not None
    return route.primary


def test_builtin_registry_exposes_pipeline_capabilities() -> None:
    registry = AudioPluginRegistry.builtins()

    assert registry.require("qwen3-asr-0.6b").supplies(AudioCapability.ASR)
    aligner = registry.require("qwen3-forced-aligner-0.6b")
    assert aligner.capabilities.known_text_alignment is True
    assert aligner.capabilities.supports_language("zh-CN") is True
    assert aligner.capabilities.supports_language("ar") is False
    assert registry.require("stable-audio-3-small-sfx").supplies(AudioCapability.SFX_GENERATION)
    assert registry.require("minimax-music-2.6").runtime.is_cloud is True
    assert registry.require("volcengine-agent-plan-seed-tts-2.0").provider_id == "volcengine_ark"
    assert registry.require("mimo-v2.5-tts").provider_id == "mimo"
    assert "backup" in registry.require("qwen3-tts-1.7b-customvoice").tags
    assert (
        registry.require("qwen3-asr-0.6b").quality.priority
        > registry.require("qwen3-tts-1.7b-customvoice").quality.priority
    )


def test_user_manifest_can_extend_registry_without_python_import(tmp_path) -> None:
    manifest = AudioPluginManifest(
        plugin_id="studio.custom-aligner",
        display_name="Studio Custom Aligner",
        provider_id="studio",
        capabilities=AudioPluginCapabilities(
            services={AudioCapability.FORCED_ALIGNMENT},
            languages={"ar"},
            known_text_alignment=True,
            offline=True,
        ),
        runtime=AudioPluginRuntime(execution=AudioExecution.LOCAL_SIDECAR),
    )
    path = tmp_path / "studio.audio-plugin.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")

    registry = AudioPluginRegistry.builtins()
    loaded = registry.load_manifest_dirs([tmp_path])

    assert loaded == ["studio.custom-aligner"]
    assert registry.require("studio.custom-aligner").capabilities.supports_language("ar")


def test_new_provider_manifest_is_automatically_pinned_for_tts(tmp_path) -> None:
    manifest = AudioPluginManifest(
        plugin_id="studio.cloud-voice",
        display_name="Studio Cloud Voice",
        provider_id="studio",
        model_id="studio-v1",
        tags={"formal"},
        capabilities=AudioPluginCapabilities(
            services={AudioCapability.SPEECH_SYNTHESIS},
            languages={"zh"},
        ),
        runtime=AudioPluginRuntime(execution=AudioExecution.CLOUD_API),
    )
    (tmp_path / "studio.audio-plugin.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        tts_default_provider="studio",
        audio_plugin_manifest_dirs=str(tmp_path),
    )

    plan = build_audio_execution_plan(settings, languages=["zh"])

    assignment = plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    assert assignment is not None
    assert assignment.primary is not None
    assert assignment.primary.plugin_id == "studio.cloud-voice"
    assert assignment.overridden is True


def test_selected_tts_platform_is_kept_while_other_stages_remain_capability_driven() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="minimax",
        audio_quality_preset="production",
        audio_memory_budget="high",
    )

    plan = build_audio_execution_plan(settings, languages=["zh", "ja"])

    tts = plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    assert tts is not None and tts.primary is not None
    assert tts.primary.plugin_id == "minimax-speech-2.8-hd"
    assert tts.overridden is True
    assert _target(plan, AudioExecutionStage.ASR).plugin_id == "whisperx"
    assert _target(plan, AudioExecutionStage.ALIGN).plugin_id == "whisperx"


def test_minimax_hybrid_plan_freezes_cloud_voice_and_local_post_pipeline() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="minimax",
        tts_minimax_api_key="test-key",
        audio_location_policy="hybrid",
        audio_memory_budget="high",
    )

    plan = build_audio_execution_plan(settings, languages=["zh"], project_id="book-1")

    assert plan.frozen is True
    assert plan.project_id == "book-1"
    assert plan.manifest_digest
    assert _target(plan, AudioExecutionStage.VOICE_DESIGN).provider_id == "minimax"
    assert _target(plan, AudioExecutionStage.VOICE_CLONE).provider_id == "minimax"
    assert _target(plan, AudioExecutionStage.TTS_PREVIEW).provider_id == "minimax"
    formal = plan.assignment_for(AudioExecutionStage.TTS_FORMAL)
    assert formal is not None and formal.primary is not None
    assert formal.primary.plugin_id == "minimax-speech-2.8-hd"
    assert formal.primary.endpoint == "https://api.minimax.io"
    assert formal.fallbacks
    # Cross-provider fallbacks require voice mapping; same-provider (MiniMax) do not
    cross_provider_fallbacks = [
        item for item in formal.fallbacks if item.provider_id != "minimax"
    ]
    same_provider_fallbacks = [
        item for item in formal.fallbacks if item.provider_id == "minimax"
    ]
    assert all(item.voice_mapping_required for item in cross_provider_fallbacks)
    assert all(not item.voice_mapping_required for item in same_provider_fallbacks)
    assert _target(plan, AudioExecutionStage.ASR).plugin_id == "whisperx"
    assert _target(plan, AudioExecutionStage.ALIGN).plugin_id == "whisperx"
    assert _target(plan, AudioExecutionStage.SFX).provider_id == "stable_audio"
    assert _target(plan, AudioExecutionStage.MUSIC).model_id == "small-music"
    assert _target(plan, AudioExecutionStage.SOUNDSCAPE).model_id == "small-sfx"


def test_local_only_plan_never_contains_cloud_fallback() -> None:
    constraints = AudioProjectConstraints(
        preset=AudioQualityPreset.PRODUCTION,
        location_policy=AudioLocationPolicy.LOCAL_ONLY,
        languages=["zh"],
        memory_budget=AudioMemoryClass.HIGH,
    )

    plan = AudioExecutionPlanner(AudioPluginRegistry.builtins()).plan(
        constraints,
        hardware=AudioHardwareProfile(memory_class=AudioMemoryClass.HIGH),
    )

    for assignment in plan.routes:
        if assignment.primary:
            assert assignment.primary.execution != AudioExecution.CLOUD_API
        assert all(route.offline for route in assignment.fallbacks)


def test_minimax_preflight_rejects_missing_cloud_credentials() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="minimax",
        audio_memory_budget="high",
    )
    registry = registry_from_settings(settings)
    plan = build_audio_execution_plan(settings, languages=["zh"])

    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry,
        active_stages={AudioExecutionStage.TTS_FORMAL},
    )

    assert report.passed is False
    assert any(
        item.kind == "credential"
        and item.stage == AudioExecutionStage.TTS_FORMAL
        and item.status == AudioPreflightStatus.FAILED
        for item in report.checks
    )


def test_volcengine_preflight_uses_agent_plan_key() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="volcengine_ark",
        volcengine_ark_api_key="agent-plan-key",
    )
    registry = registry_from_settings(settings)
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry,
        active_stages={AudioExecutionStage.TTS_FORMAL},
    )
    assert not any(
        item.kind == "credential" and item.status == AudioPreflightStatus.FAILED
        for item in report.checks
    )


def test_hybrid_preflight_blocks_missing_active_local_models(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "audio-models"),
        sound_generation_stable_audio_models_dir=str(tmp_path / "stable-audio"),
        tts_default_provider="minimax",
        tts_minimax_api_key="test-key",
        audio_memory_budget="high",
    )
    registry = registry_from_settings(settings)
    plan = build_audio_execution_plan(settings, languages=["zh"])

    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry,
        active_stages={
            AudioExecutionStage.ASR,
            AudioExecutionStage.ALIGN,
            AudioExecutionStage.VAD,
        },
    )

    missing = {
        item.plugin_id
        for item in report.checks
        if item.kind == "model_install" and item.status == AudioPreflightStatus.FAILED
    }
    assert "silero-vad-onnx" in missing
    assert any(
        item.kind == "model_install"
        and item.plugin_id == "whisperx"
        and item.status == AudioPreflightStatus.WARNING
        for item in report.checks
    )


async def test_live_preflight_checks_frozen_sidecar_models(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "audio-models"),
        tts_default_provider="minimax",
        tts_minimax_api_key="test-key",
        audio_memory_budget="high",
    )
    plan = build_audio_execution_plan(settings, languages=["zh"])
    report = preflight_audio_execution_plan(
        plan,
        settings=settings,
        registry=registry_from_settings(settings),
        active_stages={
            AudioExecutionStage.ASR,
            AudioExecutionStage.ALIGN,
            AudioExecutionStage.VAD,
        },
    )
    tested_models: list[str] = []

    class _HealthySidecar:
        def __init__(self, endpoint: str, *, api_key: str = "") -> None:
            self.endpoint = endpoint
            self.api_key = api_key

        async def health(self):
            return {"ok": True}

        async def self_test(self, *, model_id: str = ""):
            tested_models.append(model_id)
            return {"passed": True, "model_id": model_id}

        async def version(self):
            return {"runtime": "test-1"}

        async def aclose(self) -> None:
            return None

    await run_live_audio_preflight(
        plan,
        report,
        settings=settings,
        active_stages={
            AudioExecutionStage.ASR,
            AudioExecutionStage.ALIGN,
            AudioExecutionStage.VAD,
        },
        sidecar_factory=_HealthySidecar,
    )

    assert {"whisperx", "silero-vad.onnx"} <= set(tested_models)
    assert all(
        item.status == AudioPreflightStatus.PASSED
        for item in report.checks
        if item.kind in {"health", "model_self_test"}
    )


def test_master_plan_uses_independent_alignment_validator() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="qwen3",
        audio_quality_preset="master",
        audio_memory_budget="high",
    )

    plan = build_audio_execution_plan(settings, languages=["zh", "en"])

    primary = plan.assignment_for(AudioExecutionStage.ALIGN)
    validator = plan.assignment_for(AudioExecutionStage.ALIGNMENT_VALIDATOR)
    assert primary is not None and primary.primary is not None
    assert validator is not None and validator.primary is not None
    assert primary.primary.plugin_id == "whisperx"
    assert validator.primary.plugin_id == "qwen3-forced-aligner-0.6b"
    assert validator.primary.plugin_id != primary.primary.plugin_id


def test_local_only_plan_routes_music_without_cloud_candidates() -> None:
    constraints = AudioProjectConstraints(
        preset=AudioQualityPreset.PRODUCTION,
        location_policy=AudioLocationPolicy.LOCAL_ONLY,
        languages=["zh"],
    )
    plan = AudioExecutionPlanner(AudioPluginRegistry.builtins()).plan(
        constraints,
        hardware=AudioHardwareProfile(
            platform="macos",
            accelerator="mps",
            memory_class=AudioMemoryClass.HIGH,
        ),
    )

    music = plan.assignment_for(AudioExecutionStage.MUSIC)
    assert music is not None and music.primary is not None
    assert music.primary.plugin_id == "stable-audio-3-small-music"
    assert music.primary.execution == AudioExecution.LOCAL_CLI
    assert all(route.offline for route in music.fallbacks)


def test_settings_parse_stage_and_language_overrides() -> None:
    settings = Settings(
        _env_file=None,
        audio_plugin_overrides=json.dumps({"asr": "sherpa-sensevoice-int8"}),
        audio_language_overrides=json.dumps({"ja": "whisperx"}),
        audio_disabled_plugins="montreal-forced-aligner",
    )

    constraints = constraints_from_settings(settings, languages=["zh", "ja"])

    assert constraints.plugin_overrides[AudioExecutionStage.ASR] == "sherpa-sensevoice-int8"
    assert constraints.language_overrides["ja"] == "whisperx"
    assert "montreal-forced-aligner" in constraints.disabled_plugins


def test_bailian_stage_routes_follow_configured_bound_models() -> None:
    settings = Settings(
        _env_file=None,
        tts_default_provider="bailian",
        tts_dashscope_model="qwen3-tts-instruct-flash",
        tts_dashscope_preview_model="qwen3-tts-flash",
        tts_dashscope_voice_clone_model="qwen3-tts-vc-2026-01-22",
        tts_dashscope_voice_design_model="qwen3-tts-vd-2026-01-26",
    )

    constraints = constraints_from_settings(settings)

    assert (
        constraints.plugin_overrides[AudioExecutionStage.TTS_FORMAL]
        == "dashscope-qwen3-tts-instruct-flash"
    )
    assert (
        constraints.plugin_overrides[AudioExecutionStage.TTS_PREVIEW]
        == "dashscope-qwen3-tts-flash"
    )
    assert (
        constraints.plugin_overrides[AudioExecutionStage.VOICE_CLONE]
        == "dashscope-qwen3-tts-vc-2026-01-22"
    )
    assert (
        constraints.plugin_overrides[AudioExecutionStage.VOICE_DESIGN]
        == "dashscope-qwen3-tts-vd-2026-01-26"
    )


def test_segment_routes_language_runs_independently() -> None:
    settings = Settings(_env_file=None, tts_default_provider="qwen3")
    plan = build_audio_execution_plan(settings, languages=["zh", "en"])
    segment = DubbingSegment(
        segment_index=2,
        segment_type=SegmentType.DIALOGUE,
        text="雨停了, but the wind remains",
        language_code="zh",
        language_runs=[
            LanguageRun(language="zh", text="雨停了, ", start_char=0, end_char=5),
            LanguageRun(
                language="en",
                text="but the wind remains",
                start_char=5,
                end_char=25,
            ),
        ],
    )

    routed = route_segment(segment, plan)

    assert [item.language for item in routed] == ["zh", "en"]
    assert all(item.aligner_plugin_id == "whisperx" for item in routed)


def test_provider_field_profiles_preserve_platform_specific_capabilities() -> None:
    minimax = provider_field_profile("minimax")
    qwen = provider_field_profile("qwen3")

    assert minimax.mapping_for(VocalDirectionField.SPATIAL_EFFECT).support == FieldSupport.NATIVE
    assert minimax.mapping_for(VocalDirectionField.ENERGY).support == FieldSupport.DIRECTOR_ONLY
    assert minimax.mapping_for(VocalDirectionField.RESONANCE).support == FieldSupport.DIRECTOR_ONLY
    assert qwen.mapping_for(VocalDirectionField.DELIVERY_STYLE).support == FieldSupport.NATIVE
    assert provider_language_value("minimax", "yue") == "Chinese,Yue"
    assert provider_language_value("minimax", "pt-BR") == "Portuguese"
    assert provider_language_value("minimax", "unknown-language") == "auto"
    assert provider_language_value("qwen3", "ja") == "Japanese"


@pytest.mark.asyncio
async def test_uniform_sidecar_client_uses_stable_protocol(tmp_path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFFtest")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json={"services": ["asr", "forced_alignment"]})
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"model": "example", "runtime": "1.0"})
        if request.url.path.endswith("/models") and request.method == "GET":
            return httpx.Response(200, json={"models": [{"model_id": "example"}]})
        if request.url.path.endswith("/models/install"):
            return httpx.Response(200, json={"ok": True, "state": "installed"})
        if request.url.path.endswith("/models/delete"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/align"):
            return httpx.Response(200, json={"items": [{"text": "雨", "start_ms": 0}]})
        return httpx.Response(404)

    client = AudioSidecarClient(
        "http://sidecar/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.health() == {"ok": True}
        assert "forced_alignment" in (await client.capabilities())["services"]
        assert (await client.version())["runtime"] == "1.0"
        assert (await client.list_models())["models"][0]["model_id"] == "example"
        assert (await client.install_model(plugin_id="example", model_id="example"))[
            "state"
        ] == "installed"
        assert (await client.delete_model(plugin_id="example", model_id="example"))["ok"]
        result = await client.align(audio, text="雨", language="zh")
        assert result["items"][0]["text"] == "雨"
    finally:
        await client.aclose()


def test_registry_from_settings_loads_external_manifests(tmp_path) -> None:
    manifest = AudioPluginManifest(
        plugin_id="external.vad",
        display_name="External VAD",
        provider_id="external",
        capabilities=AudioPluginCapabilities(
            services={AudioCapability.VAD},
            offline=True,
        ),
        runtime=AudioPluginRuntime(execution=AudioExecution.LOCAL_SIDECAR),
    )
    (tmp_path / "vad.audio-plugin.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, audio_plugin_manifest_dirs=str(tmp_path))

    assert registry_from_settings(settings).get("external.vad") is not None


async def test_project_benchmark_produces_reusable_scorecards(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF")

    class _Client:
        async def transcribe(self, *_args, **_kwargs):
            return {"text": "雨停了"}

        async def align(self, *_args, **_kwargs):
            return {
                "items": [
                    {"text": "雨", "start_ms": 0, "end_ms": 100},
                    {"text": "停了", "start_ms": 120, "end_ms": 300},
                ]
            }

        async def diagnostics(self):
            return {"peak_memory_mb": 1400}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "novel_forge.tts.platform.benchmark._client_for_manifest",
        lambda _settings, _manifest: _Client(),
    )
    report = await run_audio_benchmark(
        settings=Settings(_env_file=None),
        samples=[
            AudioBenchmarkSample(
                sample_id="s1",
                audio_path=str(audio),
                expected_text="雨停了",
                language="zh",
            )
        ],
        registry=AudioPluginRegistry.builtins(),
        plugin_ids=["qwen3-asr-0.6b", "qwen3-forced-aligner-0.6b"],
    )
    save_audio_benchmark_report(tmp_path / "scorecards.json", report)

    assert len(report.scorecards) == 2
    assert all(item.quality_score == 1.0 for item in report.scorecards)
    assert all(item.peak_memory_mb == 1400 for item in report.scorecards)
    loaded = json.loads((tmp_path / "scorecards.json").read_text(encoding="utf-8"))
    assert len(loaded["scorecards"]) == 2


@pytest.mark.asyncio
async def test_sidecar_client_transcodes_mp3_to_wav(tmp_path, monkeypatch) -> None:
    """Non-WAV audio is transcoded to WAV before upload so Sherpa/WhisperX
    backends both receive a universally readable container."""
    mp3 = tmp_path / "seg_0000.mp3"
    mp3.write_bytes(b"ID3fake-mp3-data")
    uploaded_filenames: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transcribe"):
            # Extract filename from multipart Content-Disposition header
            body = request.content.decode("utf-8", errors="replace")
            for line in body.split("\r\n"):
                if "filename=" in line:
                    # filename="tmpXXXX.wav"
                    fname = line.split('filename="')[-1].rstrip('"')
                    uploaded_filenames.append(fname)
                    break
            return httpx.Response(200, json={"text": "识别结果", "items": []})
        return httpx.Response(404)

    # Stub configure_pydub + AudioSegment so no real ffmpeg is needed.
    captured: dict[str, object] = {}

    class FakeSegment:
        def __init__(self, _: str) -> None:
            captured["input"] = _

        @classmethod
        def from_file(cls, path: str) -> "FakeSegment":
            return cls(path)

        def set_channels(self, n: int) -> "FakeSegment":
            return self

        def set_frame_rate(self, rate: int) -> "FakeSegment":
            return self

        def set_sample_width(self, w: int) -> "FakeSegment":
            return self

        def export(self, dest: str, format: str = "wav") -> None:
            Path(dest).write_bytes(b"RIFFfake-wav")
            captured["export_path"] = dest

    fake_pydub = type("pydub", (), {"AudioSegment": FakeSegment})
    monkeypatch.setattr(
        "novel_forge.tts.runtime.audio_runtime.configure_pydub", lambda: True
    )
    # _ensure_wav does a local import of pydub inside the function body,
    # so we patch sys.modules to inject our fake.
    import sys

    monkeypatch.setitem(sys.modules, "pydub", fake_pydub)

    client = AudioSidecarClient(
        "http://sidecar/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.transcribe(mp3, language="zh", expected_text="测试")
    finally:
        await client.aclose()

    assert uploaded_filenames, "transcribe should have been called"
    assert uploaded_filenames[0].endswith(".wav"), (
        f"uploaded file should be .wav, got {uploaded_filenames[0]!r}"
    )
    assert "export_path" in captured, "AudioSegment.export should have been called"
    # Temp WAV should have been cleaned up after upload
    assert not Path(str(captured["export_path"])).exists()


@pytest.mark.asyncio
async def test_sidecar_client_skips_transcode_for_wav(tmp_path) -> None:
    """WAV files are uploaded directly without any transcoding."""
    wav = tmp_path / "seg_0000.wav"
    wav.write_bytes(b"RIFFtest")
    uploaded_filenames: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transcribe"):
            body = request.content.decode("utf-8", errors="replace")
            for line in body.split("\r\n"):
                if "filename=" in line:
                    fname = line.split('filename="')[-1].rstrip('"')
                    uploaded_filenames.append(fname)
                    break
            return httpx.Response(200, json={"text": "ok", "items": []})
        return httpx.Response(404)

    client = AudioSidecarClient(
        "http://sidecar/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.transcribe(wav, language="zh")
    finally:
        await client.aclose()

    assert uploaded_filenames == ["seg_0000.wav"]


@pytest.mark.asyncio
async def test_sidecar_client_falls_back_when_pydub_unavailable(tmp_path, monkeypatch) -> None:
    """If pydub/ffmpeg is unavailable, the original file is uploaded as-is
    (the sidecar will handle or error -- no crash in the client)."""
    mp3 = tmp_path / "seg_0000.mp3"
    mp3.write_bytes(b"ID3fake")
    uploaded_filenames: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/align"):
            body = request.content.decode("utf-8", errors="replace")
            for line in body.split("\r\n"):
                if "filename=" in line:
                    fname = line.split('filename="')[-1].rstrip('"')
                    uploaded_filenames.append(fname)
                    break
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    monkeypatch.setattr(
        "novel_forge.tts.runtime.audio_runtime.configure_pydub", lambda: False
    )

    client = AudioSidecarClient(
        "http://sidecar/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.align(mp3, text="测试", language="zh")
    finally:
        await client.aclose()

    # Fell back to original mp3 filename
    assert uploaded_filenames == ["seg_0000.mp3"]
