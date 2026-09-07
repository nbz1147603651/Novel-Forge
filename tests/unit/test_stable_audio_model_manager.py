"""Tests for the user-managed Stable Audio model cache boundary."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest
from tqdm.contrib.concurrent import thread_map

from novel_forge.core.config import Settings
from novel_forge.tts.sound_generation.model_manager import (
    StableAudioModelManager,
    StableAudioModelManagerError,
)
from novel_forge.tts.sound_generation.providers.registry import SoundGenerationRegistry
from novel_forge.tts.sound_generation.schemas import SoundGenerationRequest


def _write_cached_sfx_model(models_dir: Path) -> Path:
    snapshot = (
        models_dir
        / "hub"
        / "models--stabilityai--stable-audio-3-small-sfx"
        / "snapshots"
        / "revision-1"
    )
    snapshot.mkdir(parents=True)
    # Write enough bytes to pass the _is_installed size threshold
    # (max(100 MB, estimated_download_bytes // 20) ≈ 113.5 MB for small-sfx).
    model_file = snapshot / "model.safetensors"
    chunk = b"\x00" * (1024 * 1024)  # 1 MiB
    with open(model_file, "wb") as fh:
        written = 0
        while written < 120_000_000:
            fh.write(chunk)
            written += len(chunk)
    return snapshot


def test_manager_lists_and_safely_deletes_only_its_managed_model(tmp_path: Path) -> None:
    models_dir = tmp_path / "audio-models"
    _write_cached_sfx_model(models_dir)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    manager = StableAudioModelManager(models_dir=models_dir, command="not-installed-stable-audio")

    status = manager.model_status("small-sfx")

    assert status.installed is True
    assert status.installed_size_bytes >= 120_000_000
    assert status.cache_path.is_dir()

    manager.delete("small-sfx")

    assert status.cache_path.exists() is False
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_manager_download_uses_configured_huggingface_cache_and_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models_dir = tmp_path / "audio-models"
    calls: dict[str, object] = {}
    fake_module = ModuleType("huggingface_hub")

    def snapshot_download(
        *,
        repo_id: str,
        cache_dir: str,
        token: str | None,
        force_download: bool = False,
        tqdm_class: type | None = None,
    ) -> str:
        calls.update(
            {
                "repo_id": repo_id,
                "cache_dir": cache_dir,
                "token": token,
                "force_download": force_download,
                "has_tqdm_class": tqdm_class is not None,
            }
        )
        assert tqdm_class is not None
        assert thread_map(lambda value: value, [1, 2], tqdm_class=tqdm_class) == [1, 2]
        byte_bar = tqdm_class(
            total=0,
            unit="B",
            desc="Reconstructing (incomplete total...)",
        )
        byte_bar.total = 100
        byte_bar.update(25)
        byte_bar.close()
        _write_cached_sfx_model(models_dir)
        return str(models_dir / "hub")

    fake_module.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)
    progress: list[tuple[str, int]] = []
    manager = StableAudioModelManager(models_dir=models_dir)

    status = manager.download(
        "small-sfx",
        token="hf_test",
        progress=lambda message, percentage: progress.append((message, percentage)),
    )

    assert status.installed is True
    assert calls == {
        "repo_id": "stabilityai/stable-audio-3-small-sfx",
        "cache_dir": str(models_dir / "hub"),
        "token": "hf_test",
        "force_download": False,
        "has_tqdm_class": True,
    }
    # HF_HUB_DISABLE_XET must be restored (or removed) after download
    assert os.environ.get("HF_HUB_DISABLE_XET") is None
    assert any(percentage == 25 for _message, percentage in progress)
    assert progress[-1] == ("下载完成并已校验本地缓存。", 100)


def test_manager_rejects_non_stable_audio_catalog_entries(tmp_path: Path) -> None:
    manager = StableAudioModelManager(models_dir=tmp_path)

    with pytest.raises(StableAudioModelManagerError, match="不支持受管下载"):
        manager.model_status("music-2.6")


def test_registry_stage_one_bgm_requires_minimax_key_and_passes_managed_cache_dir() -> None:
    settings = Settings(
        _env_file=None,
        sound_generation_stable_audio_models_dir="/tmp/managed-stable-audio",
    )
    registry = SoundGenerationRegistry(settings)
    bgm_request = SoundGenerationRequest(
        request_id="bgm-missing-key",
        chapter_number=1,
        kind="bgm",
        cue_index=0,
        cue_label="紧张底乐",
        prompt="subtle suspense instrumental",
    )

    with pytest.raises(ValueError, match="MiniMax Music API Key"):
        registry.prepare_request(bgm_request)

    provider = registry.get_provider("stable_audio")
    assert provider._models_dir == "/tmp/managed-stable-audio"  # noqa: SLF001
