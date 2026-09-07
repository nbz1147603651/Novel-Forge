"""Tests for pluggable BGM, ambience, and SFX generation orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from novel_forge.core.config import (
    Settings,
    get_application_assets_dir,
    get_application_models_dir,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import (
    load_sound_library,
    resolve_sound_cues,
    save_sound_library,
    set_sound_asset_approval,
)
from novel_forge.tts.platform.config import build_audio_execution_plan
from novel_forge.tts.platform.schemas import AudioExecutionStage
from novel_forge.tts.schemas import (
    BGMTiming,
    DubbingScript,
    SFXCue,
    SoundAsset,
    SoundLibraryManifest,
    SoundscapeCue,
)
from novel_forge.tts.sound_generation.models.catalog import SOUND_MODEL_CATALOG
from novel_forge.tts.sound_generation.providers.base import SoundGenerationProvider
from novel_forge.tts.sound_generation.providers.minimax_music import MiniMaxMusicProvider
from novel_forge.tts.sound_generation.providers.registry import SoundGenerationRegistry
from novel_forge.tts.sound_generation.schemas import (
    GeneratedSoundAsset,
    SoundGenerationKind,
    SoundGenerationRequest,
)
from novel_forge.tts.sound_generation.service import SoundGenerationService


class _FakeSoundProvider(SoundGenerationProvider):
    def __init__(self) -> None:
        self.requests: list[SoundGenerationRequest] = []

    @property
    def provider_id(self) -> str:
        return "stable_audio"

    async def generate(self, request: SoundGenerationRequest) -> GeneratedSoundAsset:
        self.requests.append(request)
        return GeneratedSoundAsset(
            provider=self.provider_id,
            model_id=request.model_id,
            audio_format=request.output_format,
            duration_ms=request.duration_ms,
            audio_data=b"generated-audio",
        )


async def test_minimax_music_trims_full_song_to_requested_cue(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {"audio": b"full-song".hex()},
                "extra_info": {"music_duration": 290_000},
                "base_resp": {"status_code": 0},
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(
        "novel_forge.tts.sound_generation.providers.minimax_music.httpx.AsyncClient",
        lambda **_kwargs: _Client(),
    )
    provider = MiniMaxMusicProvider(
        api_key="test",
        endpoint="https://example.invalid/music",
        default_model="music-2.6",
    )
    trim = AsyncMock(return_value=b"trimmed")
    monkeypatch.setattr(provider, "_trim_audio", trim)

    result = await provider.generate(
        SoundGenerationRequest(
            request_id="music-1",
            chapter_number=1,
            kind=SoundGenerationKind.BGM,
            cue_index=0,
            cue_label="transition",
            prompt="restrained cinematic transition",
            duration_ms=3_000,
            provider="minimax_music",
            model_id="music-2.6",
        )
    )

    assert result.audio_data == b"trimmed"
    assert result.duration_ms == 3_000
    assert result.metadata["original_duration_ms"] == 290_000
    assert result.metadata["trimmed_to_request"] is True
    trim.assert_awaited_once()


def test_default_stable_audio_directory_is_application_scoped() -> None:
    settings = Settings(_env_file=None)

    assert settings.sound_generation_stable_audio_models_dir == str(
        get_application_models_dir() / "audio" / "stable-audio"
    )
    assert "/tts/models" not in settings.sound_generation_stable_audio_models_dir


def test_application_resources_follow_the_application_data_root(
    monkeypatch, tmp_path: Path
) -> None:
    application_root = tmp_path / "application-data"
    monkeypatch.setenv("NOVEL_FORGE_APPLICATION_DATA_ROOT", str(application_root))

    settings = Settings(_env_file=None)

    assert get_application_models_dir() == Path("models")
    assert get_application_assets_dir() == application_root / "assets"
    assert settings.audio_models_root == "models/audio"
    assert settings.tts_cosyvoice_voice_store == str(
        application_root / "assets" / "tts-voices" / "cosyvoice"
    )
    assert settings.tts_openvoice_voice_store == str(
        application_root / "assets" / "tts-voices" / "openvoice"
    )


async def test_generated_sound_asset_is_registered_and_resolved(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
        sound_generation_auto_approve=True,
        sound_generation_minimax_api_key="test-key",
        # Unit environment has no real ffprobe/ffmpeg toolchain; the quality
        # gate and post-processor are covered by their own dedicated tests,
        # so this flow-level test keeps them disabled.
        sound_generation_quality_gate_enabled=False,
        sound_generation_postprocess_enabled=False,
    )
    provider = _FakeSoundProvider()
    service = SoundGenerationService(
        settings=settings,
        layout=layout,
        registry=SoundGenerationRegistry(settings, provider_factory=lambda _: provider),
    )
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="夜雨", description="窗外夜雨与远处车流")],
        sfx_cues=[SFXCue(effect_name="沉重木门合上", duration_ms=1200)],
    )

    outcome = await service.resolve_or_generate(script)
    library = load_sound_library(layout)

    assert outcome.summary.generated_count == 2
    assert outcome.summary.failed_count == 0
    assert outcome.resolution.matched_count == 2
    assert outcome.resolution.unresolved_count == 0
    assert len(library.assets) == 2
    assert all(asset.source == "generated" for asset in library.assets)
    assert all(asset.approval_status == "approved" for asset in library.assets)
    assert all(asset.commercial_use_status == "review_required" for asset in library.assets)
    assert all(asset.generation_request_hash for asset in library.assets)
    assert all(
        (layout.tts_sound_assets_dir / asset.relative_path).is_file() for asset in library.assets
    )
    assert SOUND_MODEL_CATALOG["musicgen"].default_commercial_use_status == "restricted"


async def test_pending_generated_sound_is_not_mixed_without_approval(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
        sound_generation_auto_approve=False,
        sound_generation_minimax_api_key="test-key",
        # Unit environment has no real ffprobe/ffmpeg toolchain.
        sound_generation_quality_gate_enabled=False,
        sound_generation_postprocess_enabled=False,
    )
    provider = _FakeSoundProvider()
    service = SoundGenerationService(
        settings=settings,
        layout=layout,
        registry=SoundGenerationRegistry(settings, provider_factory=lambda _: provider),
    )
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="清晨鸟鸣", description="稀疏的清晨林间鸟鸣")],
    )

    outcome = await service.resolve_or_generate(script)
    library = load_sound_library(layout)

    assert outcome.summary.pending_review_count == 1
    assert outcome.resolution.unresolved_count == 1
    assert "待作者审核" in outcome.resolution.resolutions[0].reason
    assert library.assets[0].approval_status == "pending"
    pending_report = resolve_sound_cues(layout=layout, script=script, library=library)
    pending_request = service._prepare_request(  # noqa: SLF001
        service._build_requests(script, pending_report)[0]  # noqa: SLF001
    )
    assert pending_request.fingerprint == library.assets[0].generation_request_hash
    assert service.required_execution_stages(script) == set()

    set_sound_asset_approval(layout, library.assets[0].asset_id, status="rejected")
    assert service.required_execution_stages(script) == {
        AudioExecutionStage.SOUNDSCAPE,
    }


async def test_autonomous_retry_promotes_cached_assisted_candidate(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    assisted_settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
        sound_generation_auto_approve=False,
        sound_generation_minimax_api_key="test-key",
        # Unit environment has no real ffprobe/ffmpeg toolchain.
        sound_generation_quality_gate_enabled=False,
        sound_generation_postprocess_enabled=False,
    )
    provider = _FakeSoundProvider()
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="清晨鸟鸣", description="稀疏的清晨林间鸟鸣")],
    )
    assisted = SoundGenerationService(
        settings=assisted_settings,
        layout=layout,
        registry=SoundGenerationRegistry(
            assisted_settings,
            provider_factory=lambda _: provider,
        ),
    )
    first = await assisted.resolve_or_generate(script)
    assert first.resolution.unresolved_count == 1
    assert len(provider.requests) == 1

    autonomous_settings = assisted_settings.model_copy(
        update={"sound_generation_auto_approve": True}
    )
    autonomous = SoundGenerationService(
        settings=autonomous_settings,
        layout=layout,
        registry=SoundGenerationRegistry(
            autonomous_settings,
            provider_factory=lambda _: provider,
        ),
    )
    second = await autonomous.resolve_or_generate(script)

    assert second.summary.cached_count == 1
    assert second.summary.pending_review_count == 0
    assert second.resolution.unresolved_count == 0
    assert len(provider.requests) == 1
    assert load_sound_library(layout).assets[0].approval_status == "approved"


def test_preflight_skips_optional_generators_when_approved_assets_cover_cues(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    for name in ("rain.wav", "score.wav", "door.wav"):
        (layout.tts_sound_assets_dir / name).write_bytes(b"audio")
    save_sound_library(
        layout,
        SoundLibraryManifest(
            assets=[
                SoundAsset(
                    asset_id="rain",
                    kind="soundscape",
                    display_name="夜雨",
                    relative_path="rain.wav",
                    tags=["夜雨"],
                ),
                SoundAsset(
                    asset_id="score",
                    kind="bgm",
                    display_name="悬疑底乐",
                    relative_path="score.wav",
                    tags=["悬疑", "紧张"],
                ),
                SoundAsset(
                    asset_id="door",
                    kind="sfx",
                    display_name="木门合上",
                    relative_path="door.wav",
                    tags=["木门", "关门"],
                ),
            ]
        ),
    )
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
    )
    service = SoundGenerationService(settings=settings, layout=layout)
    script = DubbingScript(
        chapter_number=1,
        bgm_suggestions=[BGMTiming(track_name="悬疑底乐", mood="紧张")],
        soundscapes=[SoundscapeCue(name="夜雨")],
        sfx_cues=[SFXCue(effect_name="木门合上")],
    )

    assert service.required_execution_stages(script) == set()


def test_preflight_activates_only_generators_for_unresolved_cue_kinds(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
    )
    service = SoundGenerationService(settings=settings, layout=layout)
    script = DubbingScript(
        chapter_number=1,
        bgm_suggestions=[BGMTiming(track_name="章节配乐", mood="温暖")],
        sfx_cues=[SFXCue(effect_name="玻璃破碎")],
    )

    assert service.required_execution_stages(script) == {
        AudioExecutionStage.MUSIC,
        AudioExecutionStage.SFX,
    }


def test_preflight_does_not_require_generators_when_auto_generation_is_off(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_enabled=True,
        sound_generation_auto_generate=False,
    )
    service = SoundGenerationService(settings=settings, layout=layout)

    assert (
        service.required_execution_stages(
            DubbingScript(
                chapter_number=1,
                soundscapes=[SoundscapeCue(name="海边风声")],
            )
        )
        == set()
    )


async def test_project_palette_generates_review_first_reusable_candidates(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_default_provider="minimax_music",
        sound_generation_minimax_api_key="test-key",
        sound_generation_auto_approve=True,
        # Unit environment has no real ffprobe/ffmpeg toolchain.
        sound_generation_quality_gate_enabled=False,
        sound_generation_postprocess_enabled=False,
    )
    provider = _FakeSoundProvider()
    service = SoundGenerationService(
        settings=settings,
        layout=layout,
        registry=SoundGenerationRegistry(settings, provider_factory=lambda _: provider),
    )

    assets = await service.generate_project_palette(
        {
            "title": "青瓦梦魇",
            "genre": "古风悬疑",
            "premise": "一名仵作追查旧城连环谜案。",
            "tone": "幽冷克制",
            "era": "架空古代",
            "themes": ["真相", "记忆"],
            "audio_aesthetic": "近距旁白，留白充分",
        }
    )
    library = load_sound_library(layout)

    assert len(assets) == 5
    assert len(library.assets) == 5
    assert sum(asset.kind == "bgm" for asset in assets) == 3
    assert sum(asset.kind == "soundscape" for asset in assets) == 2
    assert all(asset.approval_status == "pending" for asset in assets)
    assert all("青瓦梦魇" in asset.tags for asset in assets)
    assert all("一名仵作追查旧城连环谜案" in asset.generation_prompt for asset in assets)

    set_sound_asset_approval(layout, assets[0].asset_id, status="approved")
    await service.generate_project_palette(
        {
            "title": "青瓦梦魇",
            "genre": "古风悬疑",
            "premise": "一名仵作追查旧城连环谜案。",
            "tone": "幽冷克制",
            "era": "架空古代",
            "themes": ["真相", "记忆"],
            "audio_aesthetic": "近距旁白，留白充分",
        }
    )
    assert load_sound_library(layout).assets[0].approval_status == "approved"


def test_auto_routing_prefers_minimax_for_long_audio_when_key_exists() -> None:
    settings = Settings(_env_file=None, sound_generation_minimax_api_key="test-key")
    registry = SoundGenerationRegistry(settings)
    request = SoundGenerationRequest(
        request_id="bgm-1",
        chapter_number=1,
        kind="bgm",
        cue_index=0,
        cue_label="悬疑底乐",
        prompt="subtle suspense instrumental",
    )

    prepared = registry.prepare_request(request)

    assert prepared.provider == "minimax_music"
    assert prepared.model_id == "music-2.6"

    ambience = request.model_copy(
        update={"request_id": "ambience-1", "kind": "soundscape", "cue_label": "夜雨底床"}
    )
    prepared_ambience = registry.prepare_request(ambience)
    assert prepared_ambience.provider == "minimax_music"
    assert prepared_ambience.model_id == "music-2.6"


async def test_hybrid_execution_plan_routes_music_ambience_and_sfx_locally(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        tts_default_provider="minimax",
        tts_minimax_api_key="test-key",
        audio_location_policy="hybrid",
        audio_memory_budget="high",
        sound_generation_enabled=True,
        sound_generation_auto_generate=True,
        sound_generation_auto_approve=True,
        # Unit environment has no real ffprobe/ffmpeg toolchain.
        sound_generation_quality_gate_enabled=False,
        sound_generation_postprocess_enabled=False,
    )
    plan = build_audio_execution_plan(settings, languages=["zh"], project_id="demo")
    provider = _FakeSoundProvider()
    service = SoundGenerationService(
        settings=settings,
        layout=layout,
        registry=SoundGenerationRegistry(settings, provider_factory=lambda _: provider),
        execution_plan=plan,
    )
    script = DubbingScript(
        chapter_number=1,
        bgm_suggestions=[BGMTiming(track_name="悬疑底乐", mood="紧张")],
        soundscapes=[SoundscapeCue(name="夜雨", description="稳定的夜雨底床")],
        sfx_cues=[SFXCue(effect_name="木门合上", duration_ms=1000)],
    )

    outcome = await service.resolve_or_generate(script)

    assert outcome.summary.generated_count == 3
    routed = {
        request.kind.value: (request.provider, request.model_id) for request in provider.requests
    }
    assert routed == {
        "bgm": ("stable_audio", "small-music"),
        "soundscape": ("stable_audio", "small-sfx"),
        "sfx": ("stable_audio", "small-sfx"),
    }
    assert all(
        request.metadata["execution_plan_id"] == plan.plan_id for request in provider.requests
    )
    assert all(attempt.route_plugin_id for attempt in outcome.summary.attempts)
    assert all(attempt.request_hash for attempt in outcome.summary.attempts)
    assert all(attempt.output_hash for attempt in outcome.summary.attempts)
    assert all(attempt.latency_ms >= 0 for attempt in outcome.summary.attempts)


def test_sound_provider_uses_frozen_route_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_minimax_api_key="test-key",
        sound_generation_minimax_music_endpoint="https://mutable.invalid/music",
    )
    registry = SoundGenerationRegistry(settings)

    provider = registry.get_provider(
        "minimax_music",
        endpoint="https://frozen.example/v1/music",
    )

    assert provider._endpoint == "https://frozen.example/v1/music"  # noqa: SLF001


def test_stable_audio_cache_uses_the_application_managed_directory(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        sound_generation_stable_audio_models_dir="/application-model-library/stable-audio",
    )

    service = SoundGenerationService(settings=settings, layout=layout)
    provider = service._registry.get_provider("stable_audio")  # noqa: SLF001

    assert provider._models_dir == "/application-model-library/stable-audio"  # noqa: SLF001


def test_stable_audio_provider_receives_huggingface_token_without_persisting_it() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_huggingface_token="hf-private-token",
    )

    provider = SoundGenerationRegistry(settings).get_provider("stable_audio")

    assert provider._huggingface_token == "hf-private-token"  # noqa: SLF001


def test_catalog_keeps_open_source_models_and_integration_status_visible() -> None:
    assert SOUND_MODEL_CATALOG["small-sfx"].integration_status == "built_in"
    assert SOUND_MODEL_CATALOG["small-sfx"].repository_id == "stabilityai/stable-audio-3-small-sfx"
    assert SOUND_MODEL_CATALOG["small-sfx"].requires_access_approval is True
    assert SOUND_MODEL_CATALOG["ace-step-1.5"].integration_status == "built_in"
    assert SOUND_MODEL_CATALOG["ace-step-1.5"].repository_id == "ace-step/ACE-Step-1.5"


def test_ace_step_provider_routing_when_configured() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_ace_step_command="/usr/local/bin/ace-step",
    )
    registry = SoundGenerationRegistry(settings)
    provider = registry.get_provider("ace_step")
    assert provider.provider_id == "ace_step"


def test_ace_step_provider_raises_when_not_configured() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_ace_step_command="",
    )
    registry = SoundGenerationRegistry(settings)
    try:
        registry.get_provider("ace_step")
        raise AssertionError("Should have raised ValueError")
    except ValueError as exc:
        assert "ACE_STEP_COMMAND" in str(exc)


def test_ace_step_auto_routing_for_bgm_without_minimax_key() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_ace_step_command="/usr/local/bin/ace-step",
        sound_generation_minimax_api_key="",
        tts_minimax_api_key="",
        minimax_api_key="",
    )
    registry = SoundGenerationRegistry(settings)
    request = SoundGenerationRequest(
        request_id="bgm-route-test",
        chapter_number=1,
        kind=SoundGenerationKind.BGM,
        cue_index=0,
        cue_label="test bgm",
        prompt="instrumental score for testing",
        provider="auto",
    )
    prepared = registry.prepare_request(request)
    assert prepared.provider == "ace_step"
    assert prepared.model_id == "ace-step-1.5"
