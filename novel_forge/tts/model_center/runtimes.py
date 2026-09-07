"""Transactional Python environments and supervised sidecar processes."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.model_center.locking import model_center_lock
from novel_forge.tts.model_center.python_runtime import (
    RUNTIME_PYTHON_VERSION,
    ManagedPythonRuntime,
)
from novel_forge.tts.model_center.schemas import (
    AudioRuntimeDescriptor,
    AudioRuntimeState,
    RuntimeInstallState,
)

CommandRunner = Callable[[Sequence[str], Path], None]
RuntimeProgressCallback = Callable[[str, int], None]
_RUNTIME_RESOLUTION_CUTOFF = "2026-07-14"
_SIDECAR_LIFECYCLE_WAIT_S = 30.0
_SIDECAR_LIFECYCLE_LOCKS: dict[str, Any] = {}
_SIDECAR_LIFECYCLE_LOCKS_GUARD = threading.Lock()
_SIDECAR_LIFECYCLE_HELD = threading.local()


def audio_runtime_catalog() -> tuple[AudioRuntimeDescriptor, ...]:
    return (
        AudioRuntimeDescriptor(
            runtime_id="qwen3-tts",
            display_name="Qwen3-TTS Sidecar",
            version="2",
            packages=[
                "qwen-tts",
                "uvicorn[standard]",
                "fastapi",
                "python-multipart",
                "pydantic-settings",
            ],
            resolution_exclude_newer=_RUNTIME_RESOLUTION_CUTOFF,
            self_test_imports=["qwen_tts", "fastapi", "uvicorn"],
            entry_module="novel_forge.tts.sidecars.qwen3_sidecar",
            endpoint="http://127.0.0.1:8011/v1",
            port=8011,
            health_path="/v1/health",
            supported_plugins=[
                "qwen3-tts-0.6b-customvoice",
                "qwen3-tts-1.7b-customvoice",
                "qwen3-tts-1.7b-voice-design",
                "qwen3-tts-1.7b-base",
            ],
        ),
        AudioRuntimeDescriptor(
            runtime_id="qwen3-asr",
            display_name="Qwen3-ASR 0.6B Sidecar",
            version="1",
            packages=[
                "qwen-asr",
                "uvicorn[standard]",
                "fastapi",
                "python-multipart",
                "pydantic-settings",
            ],
            resolution_exclude_newer=_RUNTIME_RESOLUTION_CUTOFF,
            self_test_imports=["qwen_asr", "fastapi", "uvicorn"],
            entry_module="novel_forge.tts.sidecars.qwen3_asr_sidecar",
            endpoint="http://127.0.0.1:8012/v1",
            port=8012,
            health_path="/v1/health",
            supported_plugins=["qwen3-asr-0.6b", "qwen3-forced-aligner-0.6b"],
        ),
        AudioRuntimeDescriptor(
            runtime_id="whisperx",
            display_name="WhisperX Sidecar",
            version="3",
            packages=[
                "whisperx",
                "numpy",
                "uvicorn[standard]",
                "fastapi",
                "python-multipart",
                "pydantic-settings",
            ],
            resolution_exclude_newer=_RUNTIME_RESOLUTION_CUTOFF,
            self_test_imports=["whisperx", "numpy", "fastapi", "uvicorn"],
            entry_module="novel_forge.tts.sidecars.whisperx_sidecar",
            endpoint="http://127.0.0.1:8013/v1",
            port=8013,
            health_path="/v1/health",
            supported_plugins=["whisperx"],
        ),
        AudioRuntimeDescriptor(
            runtime_id="sherpa-onnx",
            display_name="Sherpa ONNX Sidecar",
            version="4",
            packages=[
                "sherpa-onnx",
                "numpy",
                "uvicorn[standard]",
                "fastapi",
                "python-multipart",
                "pydantic-settings",
            ],
            resolution_exclude_newer=_RUNTIME_RESOLUTION_CUTOFF,
            self_test_imports=["sherpa_onnx", "numpy", "fastapi", "uvicorn"],
            entry_module="novel_forge.tts.sidecars.sherpa_onnx_sidecar",
            endpoint="http://127.0.0.1:8014/v1",
            port=8014,
            health_path="/v1/health",
            supported_plugins=["sherpa-sensevoice-int8", "silero-vad-onnx"],
        ),
    )


class RuntimeLifecycleError(RuntimeError):
    pass


class AudioRuntimeManager:
    """Keep old environments intact until a new version is proven usable."""

    def __init__(
        self,
        root: Path,
        *,
        command_runner: CommandRunner | None = None,
        uv_executable: Path | None = None,
        python_runtime: ManagedPythonRuntime | None = None,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> None:
        # Resolve once so subprocess working directories cannot reinterpret
        # application-managed runtime paths.
        self.root = root.expanduser().resolve()
        self.runtimes_root = self.root / "runtimes"
        self.logs_root = self.root / "logs"
        self._catalog = {item.runtime_id: item for item in audio_runtime_catalog()}
        self._command_runner = command_runner or self._run_command
        self._environment_overrides = {
            str(key): str(value)
            for key, value in (environment_overrides or {}).items()
            if str(value).strip()
        }
        self.python_runtime = python_runtime or ManagedPythonRuntime(
            self.root,
            uv_executable=uv_executable,
        )

    def descriptor(self, runtime_id: str) -> AudioRuntimeDescriptor:
        try:
            return self._catalog[runtime_id]
        except KeyError as exc:
            raise RuntimeLifecycleError(f"未登记运行时：{runtime_id}") from exc

    @contextmanager
    def _sidecar_lifecycle_lock(self, runtime_id: str) -> Iterator[None]:
        """Serialize a runtime lifecycle transition across every local UI.

        PySide calls the model-center service directly while Tauri calls it
        through the local Engine.  They are separate processes, so an
        in-memory lock cannot prevent two ``start`` calls from both deciding
        that the fixed sidecar port is free.  The file lease is shared by both
        clients; the local re-entrant lock keeps nested rollback/restart calls
        from trying to acquire that lease twice.
        """

        key = str((self.root / "locks" / f"sidecar-{runtime_id}.lock").resolve())
        with _SIDECAR_LIFECYCLE_LOCKS_GUARD:
            local_lock = _SIDECAR_LIFECYCLE_LOCKS.setdefault(key, threading.RLock())
        held: set[str] = getattr(_SIDECAR_LIFECYCLE_HELD, "keys", set())
        with local_lock:
            if key in held:
                yield
                return
            with model_center_lock(
                Path(key),
                wait_timeout_s=_SIDECAR_LIFECYCLE_WAIT_S,
            ):
                _SIDECAR_LIFECYCLE_HELD.keys = {*held, key}
                try:
                    yield
                finally:
                    _SIDECAR_LIFECYCLE_HELD.keys = held

    def install(
        self,
        runtime_id: str,
        *,
        version: str = "",
        progress: RuntimeProgressCallback | None = None,
    ) -> AudioRuntimeState:
        descriptor = self.descriptor(runtime_id)
        emit = progress or (lambda _message, _percent: None)
        emit("正在检查已有独立环境…", 5)
        selected = version or descriptor.version
        before = self.status(runtime_id)
        previous = before.version
        was_running = before.state == RuntimeInstallState.RUNNING
        runtime_root = self.runtimes_root / runtime_id
        destination = runtime_root / "versions" / selected
        staging = destination.with_name(f".{selected}.installing")
        with model_center_lock(self.root / "locks" / f"runtime-{runtime_id}.lock"):
            if destination.is_dir():
                try:
                    emit("正在验证已有环境…", 30)
                    self._validate_environment(destination, descriptor)
                except Exception:
                    shutil.rmtree(destination, ignore_errors=True)
                else:
                    emit("已有环境通过校验，正在激活…", 80)
                    self._activate(
                        runtime_id,
                        selected,
                        keep_current_as_previous=before.state != RuntimeInstallState.FAILED,
                    )
                    self._mark_installed(runtime_id, selected, destination)
                    self._prune_versions(runtime_id)
                    if was_running and previous != selected:
                        emit("正在重启已运行的 sidecar…", 92)
                        self._restart_after_activation(runtime_id, previous)
                    emit("独立环境已就绪。", 100)
                    return self.status(runtime_id)
            shutil.rmtree(staging, ignore_errors=True)
            try:
                emit("正在创建隔离 Python 环境…", 15)
                self.python_runtime.create_environment(
                    staging,
                    python_constraint=descriptor.python_constraint,
                )
                emit(f"正在安装 {len(descriptor.packages)} 项运行时依赖…", 35)
                self.python_runtime.install_packages(
                    staging,
                    descriptor.packages,
                    exclude_newer=descriptor.resolution_exclude_newer,
                )
                emit("依赖已下载，正在验证导入…", 78)
                self._validate_environment(staging, descriptor)
                emit("验证通过，正在激活新版本…", 88)
                atomic_write_json(
                    staging / "runtime.json",
                    descriptor.model_copy(update={"version": selected}).model_dump(mode="json"),
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging.replace(destination)
                self._activate(
                    runtime_id,
                    selected,
                    keep_current_as_previous=before.state != RuntimeInstallState.FAILED,
                )
                self._mark_installed(runtime_id, selected, destination)
                self._prune_versions(runtime_id)
                emit("正在整理可复用安装缓存…", 96)
                self._prune_package_cache()
            except Exception as exc:
                shutil.rmtree(staging, ignore_errors=True)
                failed = self._load_state(runtime_id)
                failed.version = selected
                failed.environment_path = ""
                failed.pid = None
                failed.state = RuntimeInstallState.FAILED
                failed.last_error = str(exc).strip()[-2000:] or type(exc).__name__
                self._set_creator_metadata(failed)
                self._save_state(failed)
                emit(f"安装失败：{failed.last_error}", 0)
                raise
        if was_running and previous != selected:
            emit("正在重启已运行的 sidecar…", 98)
            self._restart_after_activation(runtime_id, previous)
        emit("独立环境已就绪。", 100)
        return self.status(runtime_id)

    def _prune_package_cache(self) -> None:
        """Best-effort cleanup that preserves uv artifacts reusable by other runtimes."""

        try:
            self.python_runtime.prune_cache()
        except Exception:
            # Cache maintenance must never invalidate an already activated runtime.
            pass

    def _validate_environment(
        self,
        environment: Path,
        descriptor: AudioRuntimeDescriptor,
    ) -> None:
        python = self._python_path(environment)
        source_root = str(self._sidecar_source_root())
        self._command_runner(
            [
                str(python),
                "-c",
                ";".join(
                    [
                        "import sys",
                        f"sys.path.insert(0, {source_root!r})",
                        (
                            "from novel_forge.tts.model_center.python_runtime "
                            "import python_version_satisfies"
                        ),
                        (
                            "assert python_version_satisfies("
                            "'.'.join(map(str, sys.version_info[:3])), "
                            f"{descriptor.python_constraint!r})"
                        ),
                    ]
                ),
            ],
            environment,
        )
        modules_to_check = [*descriptor.self_test_imports]
        if descriptor.entry_module:
            modules_to_check.append(descriptor.entry_module)
        import_check = ";".join(
            [
                f"import sys;sys.path.insert(0, {source_root!r})",
                *(f"import {module}" for module in modules_to_check),
            ]
        )
        self._command_runner([str(python), "-c", import_check], environment)

    def rollback(self, runtime_id: str) -> AudioRuntimeState:
        with self._sidecar_lifecycle_lock(runtime_id):
            return self._rollback_locked(runtime_id)

    def _rollback_locked(self, runtime_id: str) -> AudioRuntimeState:
        runtime_root = self.runtimes_root / runtime_id
        pointer = self._load_pointer(runtime_id)
        previous = str(pointer.get("previous") or "")
        if not previous or not (runtime_root / "versions" / previous).is_dir():
            raise RuntimeLifecycleError("没有可回滚的运行时版本。")
        was_running = self.status(runtime_id).state == RuntimeInstallState.RUNNING
        self.stop(runtime_id)
        self._activate(runtime_id, previous)
        return self.start(runtime_id) if was_running else self.status(runtime_id)

    def start(
        self,
        runtime_id: str,
        *,
        progress: RuntimeProgressCallback | None = None,
        _recovery_attempt: bool = False,
    ) -> AudioRuntimeState:
        with self._sidecar_lifecycle_lock(runtime_id):
            return self._start_locked(
                runtime_id,
                progress=progress,
                _recovery_attempt=_recovery_attempt,
            )

    def _start_locked(
        self,
        runtime_id: str,
        *,
        progress: RuntimeProgressCallback | None = None,
        _recovery_attempt: bool = False,
    ) -> AudioRuntimeState:
        descriptor = self.descriptor(runtime_id)
        emit = progress or (lambda _message, _percent: None)
        emit("正在检查 sidecar 状态…", 10)
        if not descriptor.managed_process or not descriptor.entry_module:
            raise RuntimeLifecycleError("该运行时尚未提供应用托管的 sidecar 入口。")
        state = self.status(runtime_id)
        if state.state == RuntimeInstallState.RUNNING:
            emit("sidecar 已在运行。", 100)
            return state
        if state.state == RuntimeInstallState.NOT_INSTALLED or not state.environment_path:
            raise RuntimeLifecycleError("请先安装运行时。")
        # A desktop build can be allowed to make HTTP requests to an existing
        # loopback sidecar while its lower-level socket probe is denied by the
        # host sandbox.  Prefer the actual service health check, so we adopt a
        # healthy process instead of repeatedly trying (and failing) to bind
        # a second server to the same port.
        if self._health_ok(descriptor):
            emit("已接管健康 sidecar。", 100)
            state.state = RuntimeInstallState.RUNNING
            state.should_run = True
            state.restart_count = 0
            state.last_error = ""
            self._save_state(state)
            return state
        occupied = self._port_open(descriptor.port)
        if occupied:
            raise RuntimeLifecycleError(
                f"端口 {descriptor.port} 已被其他进程占用，且未通过 sidecar 健康检查。"
            )
        environment = Path(state.environment_path)
        python = self._python_path(environment)
        log_path = self.logs_root / f"{runtime_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        env = os.environ.copy()
        env.update(self._environment_overrides)
        source_root = str(self._sidecar_source_root())
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, env.get("PYTHONPATH", "")]))
        env["NOVEL_FORGE_AUDIO_MODELS_ROOT"] = str(self.root)
        env["NOVEL_FORGE_SIDECAR_RUNTIME_VERSION"] = descriptor.version
        if runtime_id == "qwen3-tts":
            env.setdefault("QWEN3_TTS_DEVICE", "auto")
            env.setdefault("QWEN3_TTS_DTYPE", "auto")
            env.setdefault("QWEN3_TTS_ALLOW_REMOTE_MODELS", "false")
            env.setdefault(
                "QWEN3_TTS_VOICE_STORE",
                str(self.root / "voices" / "qwen3"),
            )
            env.setdefault(
                "QWEN3_TTS_MODEL_REGISTRY",
                str(self.root / "registries" / "qwen3" / "models.json"),
            )
        try:
            emit("正在启动 sidecar 进程…", 45)
            proc = subprocess.Popen(
                [str(python), "-m", descriptor.entry_module, "--port", str(descriptor.port)],
                cwd=environment,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        finally:
            log.close()
        emit("正在等待健康检查…", 75)
        if not self._wait_until_healthy(descriptor, proc):
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass
            state.pid = None
            state.port = descriptor.port
            state.should_run = True
            state.state = RuntimeInstallState.FAILED
            state.last_error = self._startup_failure_summary(log_path)
            self._set_creator_metadata(state, environment)
            self._save_state(state)
            emit(f"启动失败：{state.last_error}", 0)
            raise RuntimeLifecycleError(state.last_error)
        state.pid = proc.pid
        state.port = descriptor.port
        state.should_run = True
        state.state = RuntimeInstallState.RUNNING
        state.last_error = ""
        if not _recovery_attempt:
            # A deliberate start is a new recovery window.  Automatic restarts
            # preserve their counter until a later health observation proves
            # the process stable, so real crash loops still stop at the cap.
            state.restart_count = 0
        self._set_creator_metadata(state, environment)
        self._save_state(state)
        emit("sidecar 已启动并通过健康检查。", 100)
        return state

    def _set_creator_metadata(
        self,
        state: AudioRuntimeState,
        environment: Path | None = None,
    ) -> None:
        python = self._python_path(environment) if environment else None
        version = self.python_runtime.python_version(python) if python and python.is_file() else ""
        metadata = (
            self.python_runtime.creator_metadata(environment) if environment is not None else {}
        )
        state.creator_python = str(
            metadata.get("base_python") or python or self.python_runtime.uv_executable or ""
        )
        state.creator_python_version = (
            version or metadata.get("base_python_version") or RUNTIME_PYTHON_VERSION
        )
        state.creator_python_source = metadata.get("source") or "uv_managed"

    def stop(self, runtime_id: str) -> AudioRuntimeState:
        with self._sidecar_lifecycle_lock(runtime_id):
            return self._stop_locked(runtime_id)

    def _stop_locked(self, runtime_id: str) -> AudioRuntimeState:
        state = self.status(runtime_id)
        pid = state.pid
        stop_error = ""
        if pid and self._pid_alive(pid):
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                    )
                else:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 3.0
                    while self._pid_alive(pid) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if self._pid_alive(pid):
                        os.kill(pid, signal.SIGKILL)
            except OSError as exc:
                stop_error = str(exc)
        state.pid = None
        state.should_run = False
        state.restart_count = 0
        state.last_error = stop_error
        state.state = (
            RuntimeInstallState.STOPPED
            if state.environment_path
            else RuntimeInstallState.NOT_INSTALLED
        )
        self._save_state(state)
        return state

    def quiesce_for_repository_move(self) -> list[str]:
        """Stop owned sidecars and return the runtimes that should resume afterward."""

        resume_ids: list[str] = []
        stopped_ids: list[str] = []
        try:
            for runtime_id in self._catalog:
                state = self.status(runtime_id)
                requested = state.should_run or state.state == RuntimeInstallState.RUNNING
                if not requested:
                    continue
                if state.state == RuntimeInstallState.RUNNING and not state.pid:
                    raise RuntimeLifecycleError(
                        f"{runtime_id} 正由外部进程提供，无法在迁移前安全停止。"
                    )
                resume_ids.append(runtime_id)
                self.stop(runtime_id)
                stopped_ids.append(runtime_id)
        except Exception:
            for runtime_id in stopped_ids:
                try:
                    self.start(runtime_id)
                except Exception:
                    pass
            raise
        return resume_ids

    def mark_for_restart(self, runtime_ids: Sequence[str]) -> None:
        """Persist restart intent without launching a process from the old repository."""

        requested = set(runtime_ids)
        for runtime_id in requested:
            state = self.status(runtime_id)
            if not state.environment_path:
                continue
            state.pid = None
            state.should_run = True
            state.state = RuntimeInstallState.STOPPED
            state.last_error = ""
            self._save_state(state)

    def restart_requested(self, runtime_ids: Sequence[str]) -> None:
        """Restore sidecars after a repository move fails before activation."""

        for runtime_id in runtime_ids:
            try:
                self.mark_for_restart([runtime_id])
                self.start(runtime_id)
            except Exception:
                # Preserve the original migration failure; the persisted restart
                # intent lets the next status reconciliation try again.
                continue

    def reconcile(self, runtime_id: str, *, max_restarts: int = 3) -> AudioRuntimeState:
        with self._sidecar_lifecycle_lock(runtime_id):
            return self._reconcile_locked(runtime_id, max_restarts=max_restarts)

    def _reconcile_locked(
        self,
        runtime_id: str,
        *,
        max_restarts: int = 3,
    ) -> AudioRuntimeState:
        """Recover a requested sidecar after an unexpected process exit."""

        state = self.status(runtime_id)
        if state.should_run and state.state != RuntimeInstallState.RUNNING:
            # A healthy sidecar may outlive the desktop process that launched it.
            # Adopt that real service before consulting the persisted crash
            # counter, otherwise repeated duplicate-bind failures can permanently
            # mislabel a healthy process as a crash loop.
            if state.environment_path and self._health_ok(self.descriptor(runtime_id)):
                state.pid = None
                state.state = RuntimeInstallState.RUNNING
                state.restart_count = 0
                state.last_error = ""
                self._set_creator_metadata(state, Path(state.environment_path))
                self._save_state(state)
                return state
            if state.restart_count >= max_restarts:
                state.state = RuntimeInstallState.FAILED
                summary = "sidecar 连续崩溃，已停止自动重启。"
                detail = str(state.last_error or "").strip()
                if detail and not detail.startswith(summary):
                    state.last_error = f"{summary}\n最近错误：{detail}"
                elif not detail:
                    state.last_error = summary
                self._save_state(state)
                return state
            state.restart_count += 1
            self._save_state(state)
            return self.start(runtime_id, _recovery_attempt=True)
        return state

    def record_healthy(self, runtime_id: str) -> AudioRuntimeState:
        """Persist authoritative HTTP health observed by the async client."""

        state = self.status(runtime_id)
        if not state.environment_path:
            return state
        state.state = RuntimeInstallState.RUNNING
        state.should_run = True
        state.restart_count = 0
        state.last_error = ""
        self._set_creator_metadata(state, Path(state.environment_path))
        self._save_state(state)
        return state

    def status(self, runtime_id: str) -> AudioRuntimeState:
        pointer = self._load_pointer(runtime_id)
        version = str(pointer.get("current") or "")
        previous = str(pointer.get("previous") or "")
        previous_path = self.runtimes_root / runtime_id / "versions" / previous
        environment = self.runtimes_root / runtime_id / "versions" / version if version else None
        state = self._load_state(runtime_id)
        state.rollback_available = bool(previous and previous_path.is_dir())
        state.version = version or state.version
        state.environment_path = str(environment) if environment and environment.is_dir() else ""
        if not state.environment_path:
            if state.state != RuntimeInstallState.FAILED:
                state.state = RuntimeInstallState.NOT_INSTALLED
            state.pid = None
        elif state.pid and self._pid_alive(state.pid):
            state.state = RuntimeInstallState.RUNNING
        elif state.state == RuntimeInstallState.RUNNING:
            # A healthy sidecar can be owned by a previous desktop process or
            # another trusted local launcher, so it has no PID that this
            # manager may supervise.  Verify the service before treating that
            # missing PID as a crash.
            if self._health_ok(self.descriptor(runtime_id)):
                state.should_run = True
            else:
                state.state = RuntimeInstallState.FAILED
                state.last_error = "sidecar 进程已退出。"
        elif state.state == RuntimeInstallState.NOT_INSTALLED:
            state.state = RuntimeInstallState.INSTALLED
        return state

    def _activate(
        self,
        runtime_id: str,
        version: str,
        *,
        keep_current_as_previous: bool = True,
    ) -> None:
        pointer = self._load_pointer(runtime_id)
        current = str(pointer.get("current") or "")
        previous = pointer.get("previous", "")
        if current != version:
            previous = current if keep_current_as_previous else ""
        atomic_write_json(
            self.runtimes_root / runtime_id / "current.json",
            {"current": version, "previous": previous},
        )

    def _mark_installed(self, runtime_id: str, version: str, environment: Path) -> None:
        state = self._load_state(runtime_id)
        process_running = bool(state.pid and self._pid_alive(state.pid))
        state.version = version
        state.environment_path = str(environment.resolve())
        state.state = (
            RuntimeInstallState.RUNNING if process_running else RuntimeInstallState.INSTALLED
        )
        state.last_error = ""
        self._set_creator_metadata(state, environment)
        self._save_state(state)

    def _restart_after_activation(self, runtime_id: str, previous: str) -> None:
        with self._sidecar_lifecycle_lock(runtime_id):
            self._restart_after_activation_locked(runtime_id, previous)

    def _restart_after_activation_locked(self, runtime_id: str, previous: str) -> None:
        self.stop(runtime_id)
        try:
            self.start(runtime_id)
        except Exception:
            if previous:
                self._activate(runtime_id, previous)
                self.start(runtime_id)
            raise

    def _prune_versions(self, runtime_id: str) -> None:
        """Keep only the active environment and its single rollback anchor."""

        pointer = self._load_pointer(runtime_id)
        previous = str(pointer.get("previous") or "")
        previous_path = self.runtimes_root / runtime_id / "versions" / previous
        if previous and previous_path.is_dir():
            try:
                self._validate_environment(previous_path, self.descriptor(runtime_id))
            except Exception:
                pointer["previous"] = ""
                atomic_write_json(
                    self.runtimes_root / runtime_id / "current.json",
                    pointer,
                )
        keep = {
            str(pointer.get("current") or ""),
            str(pointer.get("previous") or ""),
        }
        versions_root = self.runtimes_root / runtime_id / "versions"
        if not versions_root.is_dir():
            return
        for candidate in versions_root.iterdir():
            if (
                candidate.is_dir()
                and not candidate.name.startswith(".")
                and candidate.name not in keep
            ):
                shutil.rmtree(candidate, ignore_errors=True)

    def _load_pointer(self, runtime_id: str) -> dict[str, Any]:
        path = self.runtimes_root / runtime_id / "current.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _load_state(self, runtime_id: str) -> AudioRuntimeState:
        path = self.runtimes_root / runtime_id / "state.json"
        try:
            return AudioRuntimeState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return AudioRuntimeState(runtime_id=runtime_id)

    def _save_state(self, state: AudioRuntimeState) -> None:
        state.updated_at = datetime.now(timezone.utc)
        path = self.runtimes_root / state.runtime_id / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, state.model_dump(mode="json"))

    @staticmethod
    def _python_path(environment: Path) -> Path:
        return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    @staticmethod
    def _run_command(command: Sequence[str], cwd: Path) -> None:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
            raise RuntimeLifecycleError(detail or f"运行时命令失败，exit={completed.returncode}")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    @staticmethod
    def _port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def _health_ok(self, descriptor: AudioRuntimeDescriptor) -> bool:
        try:
            url = descriptor.endpoint.removesuffix("/v1") + descriptor.health_path
            headers: dict[str, str] = {}
            if descriptor.runtime_id == "qwen3-tts":
                token = self._environment_overrides.get("QWEN3_TTS_API_KEY", "")
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                status = int(getattr(response, "status", 0))
                return 200 <= status < 300
        except Exception:
            return False

    def _wait_until_healthy(
        self,
        descriptor: AudioRuntimeDescriptor,
        process: subprocess.Popen[Any],
        *,
        timeout_s: float = 15.0,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            if self._health_ok(descriptor):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _sidecar_source_root() -> Path:
        bundled_value = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if bundled_value:
            bundled_source = Path(bundled_value) / "runtime_support"
            if bundled_source.is_dir():
                return bundled_source
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _log_tail(path: Path, *, limit: int = 4000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]
        except OSError:
            return ""

    @classmethod
    def _startup_failure_summary(cls, path: Path) -> str:
        """Return one actionable error while retaining the full sidecar log on disk."""

        tail = cls._log_tail(path, limit=16_000)
        if not tail:
            return "sidecar 启动后未通过健康检查；请查看运行日志。"
        ignored_fragments = (
            '"GET /v1/health HTTP/1.1" 200',
            '"GET /v1/version HTTP/1.1" 200',
            '"GET /v1/models HTTP/1.1" 200',
            "Started server process",
            "Waiting for application startup",
            "Application startup complete",
            "Uvicorn running on",
        )
        candidates = [
            line.strip()
            for line in tail.splitlines()
            if line.strip()
            and not line.lstrip().startswith("INFO:")
            and not any(fragment in line for fragment in ignored_fragments)
        ]
        if not candidates:
            return "sidecar 启动后未通过健康检查；日志中仅有正常探测记录。"
        # Exception tails normally end with the most useful exception type and
        # message.  Keep persisted state compact; traceback and access details
        # remain available in ``models/audio/logs/<runtime>.log``.
        summary = candidates[-1]
        return summary[-500:]
