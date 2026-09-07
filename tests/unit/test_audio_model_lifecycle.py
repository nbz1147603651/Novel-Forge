"""Durable downloads, runtime rollback, locks, and repository migration."""

from __future__ import annotations

import hashlib
import io
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import novel_forge.tts.model_center.downloads as downloads_module
import novel_forge.tts.model_center.migration as migration_module
import novel_forge.tts.model_center.runtimes as runtimes_module
from novel_forge.tts.model_center.compatibility import check_runtime_compatibility
from novel_forge.tts.model_center.downloads import (
    DownloadIntegrityError,
    PersistentDownloadManager,
)
from novel_forge.tts.model_center.locking import ModelCenterLockedError, model_center_lock
from novel_forge.tts.model_center.migration import ModelRepositoryMigrator
from novel_forge.tts.model_center.python_runtime import ManagedPythonRuntime
from novel_forge.tts.model_center.runtimes import AudioRuntimeManager
from novel_forge.tts.model_center.schemas import (
    AudioModelDescriptor,
    AudioModelSource,
    AudioRuntimeState,
    DownloadTaskState,
    RuntimeInstallState,
)


def test_model_center_helper_import_does_not_load_optional_provider_stack() -> None:
    """Sidecar validation must not require unrelated cloud-provider dependencies."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.modules['httpx'] = None; "
                "import novel_forge.tts.model_center.python_runtime"
            ),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


class _Response(io.BytesIO):
    status = 206
    headers = {"Content-Length": "3"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _FullResponse(_Response):
    status = 200


def _fake_python_runtime(root: Path) -> ManagedPythonRuntime:
    uv = root / "uv"
    uv.parent.mkdir(parents=True, exist_ok=True)
    uv.write_text("", encoding="utf-8")

    def run(command, _cwd, _env) -> None:
        if command[1] == "venv":
            python = ManagedPythonRuntime.python_path(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")

    return ManagedPythonRuntime(
        root,
        uv_executable=uv,
        command_runner=run,
        preferred_python=root / "missing-python",
    )


def _hold_model_center_lock(path_value: str, ready: Any, release: Any) -> None:
    """Child-process helper for the cross-UI lifecycle lock regression test."""

    with model_center_lock(Path(path_value)):
        ready.set()
        release.wait(timeout=5)


def test_download_task_resumes_after_restart_and_verifies_sha256(tmp_path, monkeypatch) -> None:
    target = tmp_path / "model.bin"
    partial = target.with_name("model.bin.part")
    partial.write_bytes(b"abc")
    expected = hashlib.sha256(b"abcdef").hexdigest()
    seen_range = []

    def fake_open(request, timeout):
        del timeout
        seen_range.append(request.headers.get("Range"))
        return _Response(b"def")

    monkeypatch.setattr(downloads_module.urllib.request, "urlopen", fake_open)
    first = PersistentDownloadManager(tmp_path / "state")
    first.create_task(
        task_id="resume-1",
        plugin_id="demo",
        url="https://example.invalid/model.bin",
        target_path=target,
        expected_sha256=expected,
    )
    second = PersistentDownloadManager(tmp_path / "state")
    task = second.run("resume-1")

    assert seen_range == ["bytes=3-"]
    assert target.read_bytes() == b"abcdef"
    assert task.state == DownloadTaskState.COMPLETED


def test_download_rejects_bad_digest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        downloads_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"bad"),
    )
    manager = PersistentDownloadManager(tmp_path / "state")
    manager.create_task(
        task_id="bad-1",
        plugin_id="demo",
        url="https://example.invalid/model.bin",
        target_path=tmp_path / "model.bin",
        expected_sha256="0" * 64,
    )
    with pytest.raises(DownloadIntegrityError, match="SHA256"):
        manager.run("bad-1")


def test_download_resets_progress_when_server_ignores_range(tmp_path, monkeypatch) -> None:
    target = tmp_path / "model.bin"
    target.with_name("model.bin.part").write_bytes(b"old")
    monkeypatch.setattr(
        downloads_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FullResponse(b"fresh"),
    )
    manager = PersistentDownloadManager(tmp_path / "state")
    manager.create_task(
        task_id="restart-1",
        plugin_id="demo",
        url="https://example.invalid/model.bin",
        target_path=target,
    )

    task = manager.run("restart-1")

    assert target.read_bytes() == b"fresh"
    assert task.bytes_downloaded == len(b"fresh")


def test_download_can_pause_at_a_chunk_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        downloads_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FullResponse(b"payload"),
    )
    manager = PersistentDownloadManager(tmp_path / "state")
    manager.create_task(
        task_id="pause-1",
        plugin_id="demo",
        url="https://example.invalid/model.bin",
        target_path=tmp_path / "model.bin",
    )

    task = manager.run("pause-1", should_pause=lambda: True)

    assert task.state == DownloadTaskState.PAUSED
    assert (tmp_path / "model.bin.part").read_bytes() == b"payload"


def test_completed_download_is_reused_without_network(tmp_path, monkeypatch) -> None:
    target = tmp_path / "model.bin"
    target.write_bytes(b"complete")
    manager = PersistentDownloadManager(tmp_path / "state")
    task = manager.create_task(
        task_id="complete-1",
        plugin_id="demo",
        url="https://example.invalid/model.bin",
        target_path=target,
        expected_sha256=hashlib.sha256(b"complete").hexdigest(),
    )
    task.state = DownloadTaskState.COMPLETED
    manager._save(task)
    monkeypatch.setattr(
        downloads_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("completed file must not be downloaded again"),
    )

    reused = manager.run("complete-1")

    assert reused.state == DownloadTaskState.COMPLETED
    assert target.read_bytes() == b"complete"


def test_model_center_lock_rejects_a_live_owner(tmp_path) -> None:
    lock = tmp_path / "operation.lock"
    with model_center_lock(lock):
        with pytest.raises(ModelCenterLockedError):
            with model_center_lock(lock):
                pass


def test_model_center_lock_can_wait_for_another_process(tmp_path) -> None:
    lock = tmp_path / "sidecar-qwen3-tts.lock"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_model_center_lock,
        args=(str(lock), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=3)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    started = time.monotonic()
    try:
        with model_center_lock(lock, wait_timeout_s=2):
            pass
    finally:
        release.set()
        timer.cancel()
        holder.join(timeout=3)
    assert not holder.is_alive()
    assert time.monotonic() - started >= 0.08


def test_runtime_upgrade_keeps_previous_version_for_rollback(tmp_path) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    first = manager.install("qwen3-tts", version="1")
    assert first.version == "1"
    assert first.rollback_available is False
    assert manager.install("qwen3-tts", version="2").version == "2"
    assert manager.status("qwen3-tts").rollback_available is True
    rolled_back = manager.rollback("qwen3-tts")
    assert rolled_back.version == "1"
    assert (tmp_path / "runtimes/qwen3-tts/versions/2").is_dir()


def test_runtime_keeps_only_current_and_one_rollback_version(tmp_path) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    manager.install("qwen3-tts", version="1")
    manager.install("qwen3-tts", version="2")
    manager.install("qwen3-tts", version="3")

    versions = {
        path.name for path in (tmp_path / "runtimes/qwen3-tts/versions").iterdir() if path.is_dir()
    }
    assert versions == {"2", "3"}
    assert manager.rollback("qwen3-tts").version == "2"


def test_relative_repository_runtime_command_is_resolved_once(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    commands: list[tuple[list[str], Path]] = []
    manager = AudioRuntimeManager(
        Path("models/audio"),
        command_runner=lambda command, cwd: commands.append((list(command), cwd)),
        python_runtime=_fake_python_runtime(tmp_path / "models/audio"),
    )
    state = manager.install("qwen3-tts")

    assert manager.root == (tmp_path / "models/audio").resolve()
    assert commands
    assert Path(commands[0][0][0]).is_absolute()
    assert commands[0][1].is_absolute()
    assert state.state == RuntimeInstallState.INSTALLED


def test_runtime_install_failure_is_persisted_and_can_retry(tmp_path) -> None:
    def fail(_command, _cwd):
        raise RuntimeError("pip dependency resolution failed")

    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=fail,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    with pytest.raises(RuntimeError, match="pip dependency"):
        manager.install("qwen3-tts")
    failed = manager.status("qwen3-tts")
    assert failed.state == RuntimeInstallState.FAILED
    assert "pip dependency" in failed.last_error

    manager._command_runner = lambda _command, _cwd: None
    installed = manager.install("qwen3-tts")
    assert installed.state == RuntimeInstallState.INSTALLED
    assert installed.last_error == ""


def test_runtime_records_uv_managed_python(tmp_path) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )

    state = manager.install("qwen3-tts")

    assert state.creator_python_source == "uv_managed"
    assert state.creator_python_version == "3.12"


def test_runtime_repository_quiesce_rejects_unowned_sidecar(tmp_path, monkeypatch) -> None:
    manager = AudioRuntimeManager(tmp_path)
    monkeypatch.setattr(
        manager,
        "status",
        lambda runtime_id: AudioRuntimeState(
            runtime_id=runtime_id,
            state=(
                RuntimeInstallState.RUNNING
                if runtime_id == "qwen3-tts"
                else RuntimeInstallState.NOT_INSTALLED
            ),
            should_run=runtime_id == "qwen3-tts",
            pid=None,
        ),
    )

    with pytest.raises(RuntimeError, match="外部进程"):
        manager.quiesce_for_repository_move()


def test_runtime_repository_quiesce_stops_owned_sidecars(tmp_path, monkeypatch) -> None:
    manager = AudioRuntimeManager(tmp_path)
    stopped: list[str] = []

    def status(runtime_id: str) -> AudioRuntimeState:
        return AudioRuntimeState(
            runtime_id=runtime_id,
            state=(
                RuntimeInstallState.RUNNING
                if runtime_id == "qwen3-tts"
                else RuntimeInstallState.NOT_INSTALLED
            ),
            should_run=runtime_id == "qwen3-tts",
            pid=4321 if runtime_id == "qwen3-tts" else None,
        )

    monkeypatch.setattr(manager, "status", status)
    monkeypatch.setattr(
        manager,
        "stop",
        lambda runtime_id: stopped.append(runtime_id) or status(runtime_id),
    )

    assert manager.quiesce_for_repository_move() == ["qwen3-tts"]
    assert stopped == ["qwen3-tts"]


def test_runtime_install_reports_phase_progress(tmp_path) -> None:
    events: list[tuple[str, int]] = []
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )

    manager.install("sherpa-onnx", progress=lambda message, percent: events.append((message, percent)))

    assert events[0] == ("正在检查已有独立环境…", 5)
    assert ("正在创建隔离 Python 环境…", 15) in events
    assert ("依赖已下载，正在验证导入…", 78) in events
    assert events[-1] == ("独立环境已就绪。", 100)


def test_managed_runtime_prunes_only_unreachable_shared_cache_objects(tmp_path) -> None:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    runtime = ManagedPythonRuntime(
        tmp_path,
        uv_executable=uv,
        command_runner=lambda command, _cwd, _env: commands.append(list(command)),
    )

    runtime.prune_cache()

    assert commands == [[str(uv), "cache", "prune", "--no-config"]]


def test_frozen_runtime_uses_importable_sidecar_source(tmp_path, monkeypatch) -> None:
    bundled = tmp_path / "bundle"
    runtime_support = bundled / "runtime_support"
    runtime_support.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)

    assert AudioRuntimeManager._sidecar_source_root() == runtime_support


def test_runtime_health_uses_versioned_path_and_qwen_auth(tmp_path, monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def open_request(request, timeout):
        assert timeout == 2
        seen.append((request.full_url, request.get_header("Authorization") or ""))
        return Response()

    monkeypatch.setattr(runtimes_module.urllib.request, "urlopen", open_request)
    manager = AudioRuntimeManager(
        tmp_path,
        environment_overrides={"QWEN3_TTS_API_KEY": "token"},
    )

    assert manager._health_ok(manager.descriptor("qwen3-tts"))
    assert manager._health_ok(manager.descriptor("whisperx"))
    assert seen == [
        ("http://127.0.0.1:8011/v1/health", "Bearer token"),
        ("http://127.0.0.1:8013/v1/health", ""),
    ]


def test_runtime_adopts_healthy_sidecar_before_socket_probe(tmp_path, monkeypatch) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    installed = manager.install("qwen3-tts")
    installed.state = RuntimeInstallState.FAILED
    installed.should_run = True
    manager._save_state(installed)
    monkeypatch.setattr(manager, "_health_ok", lambda _descriptor: True)
    monkeypatch.setattr(
        manager,
        "_port_open",
        lambda _port: pytest.fail("healthy sidecar must be adopted before socket probing"),
    )

    adopted = manager.start("qwen3-tts")

    assert adopted.state == RuntimeInstallState.RUNNING
    assert adopted.restart_count == 0
    assert adopted.last_error == ""
    assert manager.status("qwen3-tts").state == RuntimeInstallState.RUNNING


def test_runtime_start_serializes_concurrent_ui_launches(tmp_path, monkeypatch) -> None:
    """PySide and the local Tauri Engine must not race to bind one sidecar."""

    primary = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    primary.install("qwen3-tts")
    secondary = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    first_spawned = threading.Event()
    release_first_spawn = threading.Event()
    healthy = threading.Event()
    launches: list[list[str]] = []
    errors: list[BaseException] = []

    class FakeProcess:
        def __init__(self, command, **_kwargs) -> None:
            launches.append(list(command))
            first_spawned.set()
            if len(launches) == 1:
                assert release_first_spawn.wait(timeout=5)
                healthy.set()
            self.pid = os.getpid()

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(runtimes_module.subprocess, "Popen", FakeProcess)
    for manager in (primary, secondary):
        monkeypatch.setattr(manager, "_health_ok", lambda _descriptor: healthy.is_set())
        monkeypatch.setattr(manager, "_port_open", lambda _port: False)
        monkeypatch.setattr(manager, "_set_creator_metadata", lambda *_args: None)

    def launch(manager: AudioRuntimeManager) -> None:
        try:
            manager.start("qwen3-tts")
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=launch, args=(primary,))
    second = threading.Thread(target=launch, args=(secondary,))
    first.start()
    assert first_spawned.wait(timeout=2)
    second.start()
    time.sleep(0.1)
    assert len(launches) == 1
    assert second.is_alive()

    release_first_spawn.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(launches) == 1


def test_reconcile_adopts_healthy_sidecar_after_restart_limit(tmp_path, monkeypatch) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    installed = manager.install("qwen3-tts")
    installed.state = RuntimeInstallState.FAILED
    installed.should_run = True
    installed.restart_count = 3
    installed.last_error = "duplicate bind failed"
    manager._save_state(installed)
    monkeypatch.setattr(manager, "_health_ok", lambda _descriptor: True)

    adopted = manager.reconcile("qwen3-tts")

    assert adopted.state == RuntimeInstallState.RUNNING
    assert adopted.restart_count == 0
    assert adopted.last_error == ""


def test_reconcile_preserves_latest_crash_detail_at_restart_limit(
    tmp_path,
    monkeypatch,
) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    installed = manager.install("qwen3-tts")
    installed.state = RuntimeInstallState.FAILED
    installed.should_run = True
    installed.restart_count = 3
    installed.last_error = "ModuleNotFoundError: missing_runtime_dependency"
    manager._save_state(installed)
    monkeypatch.setattr(manager, "_health_ok", lambda _descriptor: False)

    failed = manager.reconcile("qwen3-tts")

    assert failed.state == RuntimeInstallState.FAILED
    assert "连续崩溃" in failed.last_error
    assert "ModuleNotFoundError: missing_runtime_dependency" in failed.last_error


def test_startup_failure_summary_ignores_successful_probe_access_logs(tmp_path) -> None:
    log_path = tmp_path / "qwen3-tts.log"
    log_path.write_text(
        "\n".join(
            [
                'INFO: 127.0.0.1:50001 - "GET /v1/health HTTP/1.1" 200 OK',
                'INFO: 127.0.0.1:50002 - "GET /v1/version HTTP/1.1" 200 OK',
                'INFO: 127.0.0.1:50003 - "GET /v1/models HTTP/1.1" 200 OK',
            ]
        ),
        encoding="utf-8",
    )

    summary = AudioRuntimeManager._startup_failure_summary(log_path)

    assert "正常探测记录" in summary
    assert "127.0.0.1" not in summary


def test_startup_failure_summary_keeps_actionable_exception(tmp_path) -> None:
    log_path = tmp_path / "whisperx.log"
    log_path.write_text(
        "\n".join(
            [
                'INFO: 127.0.0.1:50001 - "GET /v1/health HTTP/1.1" 200 OK',
                "ModuleNotFoundError: No module named 'ctranslate2'",
            ]
        ),
        encoding="utf-8",
    )

    assert AudioRuntimeManager._startup_failure_summary(log_path) == (
        "ModuleNotFoundError: No module named 'ctranslate2'"
    )


def test_explicit_stop_clears_stale_crash_detail(tmp_path) -> None:
    manager = AudioRuntimeManager(
        tmp_path,
        command_runner=lambda _command, _cwd: None,
        python_runtime=_fake_python_runtime(tmp_path),
    )
    installed = manager.install("qwen3-tts")
    installed.last_error = "stale crash log"
    installed.restart_count = 3
    installed.should_run = True
    manager._save_state(installed)

    stopped = manager.stop("qwen3-tts")

    assert stopped.state == RuntimeInstallState.STOPPED
    assert stopped.last_error == ""
    assert stopped.restart_count == 0
    assert stopped.should_run is False


def test_repository_migration_verifies_and_rolls_back(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    migrator = ModelRepositoryMigrator(tmp_path / "state")
    record = migrator.migrate(source, tmp_path / "target")
    assert record.state == "committed"
    assert (tmp_path / "target/weights.bin").read_bytes() == b"weights"
    record = migrator.rollback(record.migration_id)
    assert record.state == "rolled_back"
    assert source.is_dir()
    assert not (tmp_path / "target").exists()


def test_repository_migration_does_not_copy_its_live_lock(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    migrator = ModelRepositoryMigrator(source / "lifecycle")

    record = migrator.migrate(source, tmp_path / "target")

    copied_lock = tmp_path / "target/lifecycle/locks/repository-migration.lock"
    assert not copied_lock.exists()
    target_migrator = ModelRepositoryMigrator(tmp_path / "target/lifecycle")
    assert target_migrator.rollback(record.migration_id).state == "rolled_back"


def test_repository_rollback_allows_volatile_runtime_and_log_changes(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    migrator = ModelRepositoryMigrator(tmp_path / "state")
    record = migrator.migrate(source, tmp_path / "target")
    target = tmp_path / "target"
    (target / "logs").mkdir()
    (target / "logs/sidecar.log").write_text("started", encoding="utf-8")
    (target / "runtimes/qwen3-tts").mkdir(parents=True)
    (target / "runtimes/qwen3-tts/state.json").write_text("{}", encoding="utf-8")
    (target / "downloads/tasks").mkdir(parents=True)
    (target / "downloads/tasks/task.json").write_text("{}", encoding="utf-8")

    rolled_back = migrator.rollback(record.migration_id)

    assert rolled_back.state == "rolled_back"
    assert not target.exists()


def test_repository_rollback_rejects_new_model_content(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    migrator = ModelRepositoryMigrator(tmp_path / "state")
    record = migrator.migrate(source, tmp_path / "target")
    (tmp_path / "target/new-model.bin").write_bytes(b"new")

    with pytest.raises(RuntimeError, match="模型或运行时已发生变化"):
        migrator.rollback(record.migration_id)


def test_repository_migration_rejects_insufficient_target_space(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    monkeypatch.setattr(
        migration_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1, used=1, free=1),
    )

    with pytest.raises(RuntimeError, match="目标磁盘空间不足"):
        ModelRepositoryMigrator(tmp_path / "state").migrate(source, tmp_path / "target")


def test_compatibility_matrix_rejects_missing_runtime() -> None:
    model = AudioModelDescriptor(
        plugin_id="qwen3-tts-1.7b-customvoice",
        model_id="demo",
        display_name="demo",
        family="qwen",
        source=AudioModelSource.HUGGINGFACE,
        runtime_id="qwen3-tts",
        minimum_runtime_version="1",
    )
    assert not check_runtime_compatibility(model, None).compatible
    assert check_runtime_compatibility(
        model,
        AudioRuntimeState(
            runtime_id="qwen3-tts",
            version="1",
            state=RuntimeInstallState.INSTALLED,
        ),
    ).compatible
    assert not check_runtime_compatibility(
        model,
        AudioRuntimeState(
            runtime_id="qwen3-tts",
            version="1",
            state=RuntimeInstallState.FAILED,
        ),
    ).compatible
