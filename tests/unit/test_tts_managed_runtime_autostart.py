"""On-demand startup for application-managed TTS runtimes."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.config import Settings
from novel_forge.tts.schemas import TTSProvider
from novel_forge.workspace.tts_ops import execution_shared as execution_tts


async def test_qwen_execution_starts_installed_managed_runtime(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FakeAudioModelCenterService:
        def __init__(self, settings: Settings) -> None:
            assert settings.audio_models_root == str(tmp_path / "models")

        async def ensure_runtime_ready(self, runtime_id: str):
            calls.append(runtime_id)
            return SimpleNamespace(environment_path=str(tmp_path / "runtime"))

    monkeypatch.setattr(
        execution_tts,
        "AudioModelCenterService",
        FakeAudioModelCenterService,
    )
    settings = Settings(_env_file=None, audio_models_root=str(tmp_path / "models"))

    await execution_tts._ensure_managed_tts_runtime(settings, TTSProvider.QWEN3)

    assert calls == ["qwen3-tts"]


async def test_cloud_execution_does_not_touch_managed_runtime(tmp_path, monkeypatch) -> None:
    class UnexpectedAudioModelCenterService:
        def __init__(self, _settings: Settings) -> None:
            raise AssertionError("cloud providers must not inspect local audio runtimes")

    monkeypatch.setattr(
        execution_tts,
        "AudioModelCenterService",
        UnexpectedAudioModelCenterService,
    )
    settings = Settings(_env_file=None, audio_models_root=str(tmp_path / "models"))

    await execution_tts._ensure_managed_tts_runtime(settings, TTSProvider.MINIMAX)
