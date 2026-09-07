"""Application-level audio model inventory, transactions, and reference safety."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from tqdm.contrib.concurrent import thread_map

import novel_forge.tts.model_center.manager as manager_module
from novel_forge.core.config import Settings
from novel_forge.tts.model_center.manager import (
    ApplicationAudioModelManager,
    AudioModelCenterError,
    AudioModelDownloadCancelled,
    _build_huggingface_progress_bridge,
)
from novel_forge.tts.model_center.schemas import (
    AudioModelInstallState,
    AudioRuntimeState,
    RuntimeInstallState,
)
from novel_forge.tts.model_center.service import AudioModelCenterService


def test_direct_model_install_is_atomic_and_application_scoped(tmp_path, monkeypatch) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )

    def fake_download(_descriptor, target, emit, **_kwargs):
        target.write_bytes(b"onnx-model")
        emit("downloaded", 90)

    monkeypatch.setattr(manager, "_stream_download", fake_download)
    status = manager.install("silero-vad-onnx")

    assert status.state == AudioModelInstallState.INSTALLED
    assert status.local_path.startswith(str(tmp_path / "models"))
    assert manager.inventory_path.is_file()
    assert not list(manager.plugins_root.rglob("*.partial"))


def test_existing_valid_model_is_reused_without_download(tmp_path, monkeypatch) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )
    model_path = manager.plugins_root / "silero-vad-onnx" / "main"
    model_path.mkdir(parents=True)
    (model_path / "silero_vad.onnx").write_bytes(b"existing")
    monkeypatch.setattr(
        manager,
        "_stream_download",
        lambda *_args, **_kwargs: pytest.fail("valid model must not be downloaded again"),
    )

    status = manager.install("silero-vad-onnx")

    assert status.state == AudioModelInstallState.INSTALLED
    assert (model_path / "silero_vad.onnx").read_bytes() == b"existing"


def test_license_acceptance_is_persisted_across_manager_instances(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "projects",
        audio_models_root=str(tmp_path / "models" / "audio"),
    )

    accepted = ApplicationAudioModelManager(settings).record_license_acceptance(
        "stable-audio-3-small-sfx"
    )
    restored = ApplicationAudioModelManager(settings).model_status("stable-audio-3-small-sfx")

    assert accepted.license_accepted is True
    assert restored.license_accepted is True


def test_model_install_honors_cancel_before_transfer(tmp_path) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )

    with pytest.raises(AudioModelDownloadCancelled, match="已取消"):
        manager.install("silero-vad-onnx", should_cancel=lambda: True)


def test_huggingface_progress_bridge_supports_concurrent_downloads() -> None:
    progress: list[tuple[str, int]] = []
    bridge = _build_huggingface_progress_bridge(
        lambda message, percent: progress.append((message, percent)),
        lambda: False,
    )

    assert thread_map(lambda value: value * 2, [1, 2], tqdm_class=bridge) == [2, 4]
    byte_bar = bridge(total=0, unit="B", desc="Reconstructing (incomplete total...)")
    byte_bar.total = 100
    byte_bar.update(25)
    byte_bar.close()

    assert progress[-1][1] == 25


def test_huggingface_install_resolves_and_records_immutable_revision(
    tmp_path, monkeypatch
) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )
    resolved = "a" * 40
    seen: dict[str, object] = {}

    class FakeApi:
        def __init__(self, *, token=None) -> None:
            seen["token"] = token

        def model_info(self, repository_id, *, revision):
            seen["repository_id"] = repository_id
            seen["requested_revision"] = revision
            return SimpleNamespace(sha=resolved)

    def snapshot_download(*, local_dir, revision, **_kwargs):
        seen["download_revision"] = revision
        destination = manager_module.Path(local_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "config.json").write_text("{}", encoding="utf-8")
        (destination / "model.safetensors").write_bytes(b"weights")

    fake_module = SimpleNamespace(HfApi=FakeApi, snapshot_download=snapshot_download)
    monkeypatch.setattr(manager_module.importlib, "import_module", lambda _name: fake_module)

    status = manager.install("qwen3-tts-0.6b-customvoice", token="hf-token")

    assert status.state == AudioModelInstallState.INSTALLED
    assert status.installed_revision == resolved
    assert seen == {
        "token": "hf-token",
        "repository_id": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "requested_revision": "main",
        "download_revision": resolved,
    }


def test_archive_is_reused_after_validation_failure_then_removed_on_success(
    tmp_path, monkeypatch
) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:bz2") as handle:
        for name, content in {
            "bundle/model.int8.onnx": b"onnx",
            "bundle/tokens.txt": b"tokens",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            handle.addfile(info, io.BytesIO(content))
    archive_downloads = 0

    def fake_download(_descriptor, target, _emit, **_kwargs):
        nonlocal archive_downloads
        if not target.exists():
            archive_downloads += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.getvalue())

    validations = iter([False, True, True])
    monkeypatch.setattr(manager, "_stream_download", fake_download)
    monkeypatch.setattr(manager, "_validate_files", lambda _path, _descriptor: next(validations))

    with pytest.raises(AudioModelCenterError, match="文件清单校验失败"):
        manager.install("sherpa-sensevoice-int8")

    descriptor = manager.descriptor("sherpa-sensevoice-int8")
    archive = manager._archive_path(descriptor)
    assert archive.is_file()

    status = manager.install("sherpa-sensevoice-int8")

    assert status.state == AudioModelInstallState.INSTALLED
    assert archive_downloads == 1
    assert not archive.exists()


def test_model_delete_requires_confirmation_when_a_project_references_it(
    tmp_path, monkeypatch
) -> None:
    projects = tmp_path / "projects"
    plan_path = projects / "demo" / "tts" / "audio_execution_plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "stage": "vad",
                        "primary": {"plugin_id": "silero-vad-onnx"},
                        "fallbacks": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=projects,
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )
    model_path = manager.plugins_root / "silero-vad-onnx" / "main"
    model_path.mkdir(parents=True)
    (model_path / "silero_vad.onnx").write_bytes(b"onnx")

    with pytest.raises(AudioModelCenterError, match="demo"):
        manager.delete("silero-vad-onnx")

    manager.delete("silero-vad-onnx", force=True)
    assert not model_path.exists()


def test_model_center_exposes_both_local_stable_audio_models(tmp_path) -> None:
    manager = ApplicationAudioModelManager(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
            sound_generation_stable_audio_models_dir=str(tmp_path / "stable-audio"),
        )
    )

    assert manager.descriptor("stable-audio-3-small-sfx").model_id == "small-sfx"
    assert manager.descriptor("stable-audio-3-small-music").model_id == "small-music"


async def test_sidecar_model_requires_explicit_installed_signal(tmp_path, monkeypatch) -> None:
    service = AudioModelCenterService(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )

    async def probe(_endpoint: str, *, api_key: str = ""):
        del api_key
        return (
            True,
            "3",
            [
                {
                    "model_id": "large-v2",
                    "local_path": str(tmp_path / "empty"),
                    "installed": False,
                    "loaded": False,
                }
            ],
        )

    monkeypatch.setattr(service, "_probe_endpoint", probe)
    statuses = await service.list_models()
    whisper = next(item for item in statuses if item.descriptor.plugin_id == "whisperx")

    assert whisper.state == AudioModelInstallState.EXTERNAL
    assert whisper.runtime_healthy is True


async def test_runtime_list_uses_successful_async_probe_as_authoritative_health(
    tmp_path,
    monkeypatch,
) -> None:
    service = AudioModelCenterService(
        Settings(_env_file=None, audio_models_root=str(tmp_path / "models" / "audio"))
    )
    manager = service.runtime_manager
    environment = manager.runtimes_root / "whisperx" / "versions" / "3"
    environment.mkdir(parents=True)
    manager._activate("whisperx", "3")
    manager._save_state(
        AudioRuntimeState(
            runtime_id="whisperx",
            version="3",
            state=RuntimeInstallState.FAILED,
            environment_path=str(environment),
            should_run=True,
            restart_count=3,
            last_error=(
                'INFO: 127.0.0.1:50001 - "GET /v1/health HTTP/1.1" 200 OK'
            ),
        )
    )
    monkeypatch.setattr(manager, "_health_ok", lambda _descriptor: False)
    service._live_runtime_health["whisperx"] = True

    payloads = await service.list_runtimes()
    whisper = next(
        AudioRuntimeState.model_validate(item["state"])
        for item in payloads
        if item["descriptor"]["runtime_id"] == "whisperx"
    )

    assert whisper.state == RuntimeInstallState.RUNNING
    assert whisper.restart_count == 0
    assert whisper.last_error == ""


async def test_sidecar_model_cancel_stops_request_and_restarts_managed_runtime(
    tmp_path, monkeypatch
) -> None:
    service = AudioModelCenterService(
        Settings(
            _env_file=None,
            storage_root=tmp_path / "projects",
            audio_models_root=str(tmp_path / "models" / "audio"),
        )
    )
    started = asyncio.Event()
    cancel_requested = False

    class FakeClient:
        cancelled = False
        closed = False

        async def install_model(self, **_kwargs):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        async def aclose(self):
            self.closed = True

    class FakeRuntimeManager:
        calls: list[tuple[str, str]] = []

        def status(self, runtime_id: str):
            return SimpleNamespace(pid=4321, runtime_id=runtime_id)

        def stop(self, runtime_id: str):
            self.calls.append(("stop", runtime_id))

        def start(self, runtime_id: str):
            self.calls.append(("start", runtime_id))

    client = FakeClient()
    runtime_manager = FakeRuntimeManager()
    service.runtime_manager = runtime_manager  # type: ignore[assignment]
    monkeypatch.setattr(service, "_client_for", lambda *_args, **_kwargs: client)

    task = asyncio.create_task(
        service.install("whisperx", should_cancel=lambda: cancel_requested)
    )
    await started.wait()
    cancel_requested = True

    with pytest.raises(AudioModelDownloadCancelled, match="Sidecar 已终止并重启"):
        await asyncio.wait_for(task, timeout=2)

    assert client.cancelled is True
    assert client.closed is True
    assert runtime_manager.calls == [("stop", "whisperx"), ("start", "whisperx")]


def test_sidecar_progress_uses_physical_cache_bytes_without_counting_symlinks(
    tmp_path,
) -> None:
    cache = tmp_path / "plugins" / "whisperx" / "main"
    blob = cache / "blobs" / "model.bin"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x" * 1_000)
    snapshot = cache / "snapshots" / "revision" / "model.bin"
    snapshot.parent.mkdir(parents=True)
    snapshot.symlink_to(blob)

    assert AudioModelCenterService._directory_regular_file_size(cache) == 1_000
    assert AudioModelCenterService._sidecar_progress_percent(1_000, 2_000) == 50


def test_model_center_passes_qwen_token_to_runtime_manager(tmp_path) -> None:
    service = AudioModelCenterService(
        Settings(
            _env_file=None,
            audio_models_root=str(tmp_path / "models" / "audio"),
            tts_qwen3_api_key="secret-token",
        )
    )

    assert service.runtime_manager._environment_overrides["QWEN3_TTS_API_KEY"] == "secret-token"


async def test_ensure_runtime_ready_starts_installed_stopped_sidecar(tmp_path) -> None:
    service = AudioModelCenterService(
        Settings(_env_file=None, audio_models_root=str(tmp_path / "models" / "audio"))
    )
    calls: list[str] = []

    class FakeRuntimeManager:
        def descriptor(self, runtime_id: str):
            return SimpleNamespace(runtime_id=runtime_id, endpoint="http://127.0.0.1:8011/v1")

        def status(self, runtime_id: str) -> AudioRuntimeState:
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.STOPPED,
                environment_path=str(tmp_path / "runtime"),
            )

        def start(self, runtime_id: str) -> AudioRuntimeState:
            calls.append(runtime_id)
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.RUNNING,
                environment_path=str(tmp_path / "runtime"),
            )

    service.runtime_manager = FakeRuntimeManager()  # type: ignore[assignment]

    async def offline(_descriptor) -> bool:
        return False

    service._managed_runtime_health = offline  # type: ignore[method-assign]

    state = await service.ensure_runtime_ready("qwen3-tts")

    assert state.state == RuntimeInstallState.RUNNING
    assert calls == ["qwen3-tts"]


async def test_execution_plugins_start_matching_analysis_runtimes(tmp_path) -> None:
    service = AudioModelCenterService(
        Settings(_env_file=None, audio_models_root=str(tmp_path / "models" / "audio"))
    )
    calls: list[str] = []

    class FakeRuntimeManager:
        def descriptor(self, runtime_id: str):
            return SimpleNamespace(runtime_id=runtime_id, endpoint="http://127.0.0.1/v1")

        def status(self, runtime_id: str) -> AudioRuntimeState:
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.STOPPED,
                environment_path=str(tmp_path / runtime_id),
            )

        def start(self, runtime_id: str) -> AudioRuntimeState:
            calls.append(runtime_id)
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.RUNNING,
                environment_path=str(tmp_path / runtime_id),
            )

    service.runtime_manager = FakeRuntimeManager()  # type: ignore[assignment]

    async def offline(_descriptor) -> bool:
        return False

    service._managed_runtime_health = offline  # type: ignore[method-assign]

    states = await service.ensure_runtimes_for_plugins(
        {"qwen3-asr-0.6b", "whisperx", "cloud-plugin"}
    )

    assert set(states) == {"qwen3-asr", "whisperx"}
    assert calls == ["qwen3-asr", "whisperx"]


async def test_ensure_runtime_ready_adopts_already_healthy_sidecar(tmp_path) -> None:
    service = AudioModelCenterService(
        Settings(_env_file=None, audio_models_root=str(tmp_path / "models" / "audio"))
    )
    calls: list[str] = []

    class FakeRuntimeManager:
        def descriptor(self, runtime_id: str):
            return SimpleNamespace(runtime_id=runtime_id, endpoint="http://127.0.0.1:8013/v1")

        def status(self, runtime_id: str) -> AudioRuntimeState:
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.STOPPED,
                environment_path=str(tmp_path / "runtime"),
            )

        def record_healthy(self, runtime_id: str) -> AudioRuntimeState:
            calls.append(f"adopt:{runtime_id}")
            return AudioRuntimeState(
                runtime_id=runtime_id,
                state=RuntimeInstallState.RUNNING,
                environment_path=str(tmp_path / "runtime"),
            )

        def start(self, runtime_id: str) -> AudioRuntimeState:
            raise AssertionError(f"must not duplicate-bind healthy sidecar {runtime_id}")

    service.runtime_manager = FakeRuntimeManager()  # type: ignore[assignment]

    async def healthy(_descriptor) -> bool:
        return True

    service._managed_runtime_health = healthy  # type: ignore[method-assign]

    state = await service.ensure_runtime_ready("whisperx")

    assert state.state == RuntimeInstallState.RUNNING
    assert calls == ["adopt:whisperx"]


def test_global_light_budget_caps_qwen_residency_even_when_audio_plan_is_high(
    tmp_path,
) -> None:
    service = AudioModelCenterService(
        Settings(
            _env_file=None,
            audio_models_root=str(tmp_path / "models" / "audio"),
            audio_memory_budget="high",
            local_model_resource_budget="light",
        )
    )

    overrides = service.runtime_manager._environment_overrides
    assert overrides["QWEN3_TTS_MAX_RESIDENT_MODELS"] == "1"
    assert overrides["QWEN3_TTS_IDLE_UNLOAD_S"] == "15"
