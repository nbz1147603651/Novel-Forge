"""Launcher for the primary React + Tauri desktop client.

``nimo`` owns the primary client boundary and can either start a managed local
FastAPI Engine or connect the same Tauri build to an explicitly configured
cloud URL. ``nimo-t`` remains a command compatibility alias; ``nimo-p`` starts
the frozen PySide6 safety/fallback client.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from novel_forge.common.runtime_identity import (
    LOCAL_ENGINE_INSTANCE_ID_ENV,
    LOCAL_ENGINE_MANAGER_ENV,
    current_engine_revision,
)
from novel_forge.core.config import get_application_data_dir

_DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
_DEFAULT_FRONTEND_URL = "http://127.0.0.1:1420"
_LAUNCHER_ID = "nimo"
_COMPATIBLE_MANAGER_IDS = frozenset({_LAUNCHER_ID, "nimo-t"})
_LOCAL_BACKEND_FALLBACK_PORT_COUNT = 20
_FRONTEND_PORT_RELEASE_GRACE_S = 2.0
_FRONTEND_PORT_POLL_INTERVAL_S = 0.1
_HEALTH_PATH = "/health"
_RUNTIME_PATH = "/api/v1/engine/runtime"
_REUSE_FRONTEND_CONFIG = json.dumps(
    {"build": {"beforeDevCommand": None}},
    separators=(",", ":"),
)


@dataclass(frozen=True)
class BackendTarget:
    base_url: str
    host: str
    port: int
    is_loopback: bool


@dataclass(frozen=True)
class LocalBackendOwnership:
    """Persistent proof that this launcher may stop a local Engine process."""

    base_url: str
    backend_pid: int
    instance_id: str
    revision: str
    reload_enabled: bool


@dataclass(frozen=True)
class BackendStartupState:
    mode: str
    read_only: bool
    diagnostic: str = ""


def _backend_target(value: str, *, allow_insecure_remote: bool = False) -> BackendTarget:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("后端地址必须是完整的 http:// 或 https:// URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("后端地址不能包含凭据、查询参数或片段。")
    is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_loopback and not allow_insecure_remote:
        raise ValueError("远程后端必须使用 HTTPS；仅本机回环地址允许 HTTP。")
    return BackendTarget(
        base_url=base_url,
        host=parsed.hostname,
        port=parsed.port or (443 if parsed.scheme == "https" else 80),
        is_loopback=is_loopback,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tauri_command(
    *,
    repo_root: Path,
    explicit_binary: str | None,
    force_dev: bool,
) -> tuple[list[str], Path]:
    client_root = repo_root / "clients" / "nimo-desktop"
    binary_value = explicit_binary or os.environ.get("NIMO_TAURI_BINARY", "").strip()
    if binary_value and not force_dev:
        binary = Path(binary_value).expanduser().resolve()
        if not binary.is_file():
            raise FileNotFoundError(f"找不到 NIMO Tauri 客户端：{binary}")
        return [str(binary)], binary.parent

    if not (client_root / "package.json").is_file():
        raise FileNotFoundError(
            "当前安装不包含 Tauri 前端源码；请设置 NIMO_TAURI_BINARY 指向已打包客户端。"
        )
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise FileNotFoundError("未找到 pnpm，无法启动 Tauri 开发客户端。")
    return [pnpm, "--filter", "@nimo/desktop", "tauri", "dev"], repo_root


def _health_ready(base_url: str, *, timeout_s: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}{_HEALTH_PATH}", timeout=timeout_s) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def _runtime_probe(
    base_url: str,
    *,
    access_token: str = "",
    timeout_s: float = 0.8,
) -> dict[str, Any] | None:
    """Read the versioned runtime contract, returning None for legacy Engines."""

    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    request = urllib.request.Request(f"{base_url}{_RUNTIME_PATH}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            if int(response.status) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _ownership_path(target: BackendTarget) -> Path:
    digest = hashlib.sha256(target.base_url.encode("utf-8")).hexdigest()[:16]
    return get_application_data_dir() / "runtime" / f"nimo-engine-{digest}.json"


def _load_ownership(target: BackendTarget) -> LocalBackendOwnership | None:
    path = _ownership_path(target)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LocalBackendOwnership(
            base_url=str(raw["base_url"]),
            backend_pid=int(raw["backend_pid"]),
            instance_id=str(raw["instance_id"]),
            revision=str(raw["revision"]),
            reload_enabled=bool(raw.get("reload_enabled", False)),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_ownership(target: BackendTarget, ownership: LocalBackendOwnership) -> None:
    path = _ownership_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "base_url": ownership.base_url,
                "backend_pid": ownership.backend_pid,
                "instance_id": ownership.instance_id,
                "revision": ownership.revision,
                "reload_enabled": ownership.reload_enabled,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _remove_ownership(target: BackendTarget) -> None:
    try:
        _ownership_path(target).unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _ownership_matches_runtime(
    target: BackendTarget,
    ownership: LocalBackendOwnership | None,
    probe: dict[str, Any] | None,
) -> bool:
    """Require PID + opaque instance token before treating a process as ours."""

    return bool(
        ownership is not None
        and probe is not None
        and ownership.base_url == target.base_url
        and ownership.instance_id == str(probe.get("instanceId", ""))
        and str(probe.get("managedBy", "")) in _COMPATIBLE_MANAGER_IDS
        and _pid_alive(ownership.backend_pid)
    )


def _runtime_has_active_jobs(probe: dict[str, Any]) -> bool:
    return int(probe.get("activeJobCount", 0) or 0) + int(probe.get("queuedJobCount", 0) or 0) > 0


def _probe_requires_restart(probe: dict[str, Any], expected_revision: str) -> bool:
    return (
        str(probe.get("status", "")) != "ready"
        or str(probe.get("bootRevision", "")) != expected_revision
        or str(probe.get("currentRevision", "")) != expected_revision
    )


def _stop_owned_backend(ownership: LocalBackendOwnership) -> None:
    """Stop only a process already authenticated by its runtime instance token."""

    if not _pid_alive(ownership.backend_pid):
        return
    os.kill(ownership.backend_pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while _pid_alive(ownership.backend_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(ownership.backend_pid):
        os.kill(ownership.backend_pid, signal.SIGKILL)


def _nimo_frontend_ready(base_url: str = _DEFAULT_FRONTEND_URL, *, timeout_s: float = 0.8) -> bool:
    """Return whether an existing dev server is serving this NIMO client."""
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=timeout_s) as response:
            if int(response.status) != 200:
                return False
            document = response.read(64 * 1024).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError):
        return False
    return "<title>NIMO</title>" in document and "/src/main.tsx" in document


def _tcp_port_in_use(host: str, port: int, *, timeout_s: float = 0.25) -> bool:
    """Return whether a TCP listener has already reserved this exact address.

    A connect probe alone reports a port as free while a process has bound it
    but is not accepting connections yet.  That state is precisely when a
    second Uvicorn process would otherwise fail with ``Errno 48``.  Binding a
    short-lived socket lets the launcher distinguish that conflict before it
    attempts to create its Engine process.
    """

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout_s)
            probe.bind((host, port))
    except OSError as exc:
        return exc.errno == errno.EADDRINUSE
    return False


def _select_available_local_backend_target(target: BackendTarget) -> BackendTarget:
    """Choose a loopback fallback only when the default port has an unusable listener."""

    if _health_ready(target.base_url) or not _tcp_port_in_use(target.host, target.port):
        return target

    host = f"[{target.host}]" if ":" in target.host else target.host
    for candidate_port in range(
        target.port + 1,
        target.port + _LOCAL_BACKEND_FALLBACK_PORT_COUNT + 1,
    ):
        if not _tcp_port_in_use(target.host, candidate_port):
            return _backend_target(f"http://{host}:{candidate_port}")

    last_port = target.port + _LOCAL_BACKEND_FALLBACK_PORT_COUNT
    raise RuntimeError(
        f"本地 Engine 默认端口 {target.port} 已被占用，且未能在 "
        f"{target.port + 1}–{last_port} 找到可用备用端口。"
    )


def _prepare_client_command(
    command: list[str],
    *,
    frontend_port_release_grace_s: float = _FRONTEND_PORT_RELEASE_GRACE_S,
) -> tuple[list[str], bool]:
    """Prepare a Tauri dev launch without racing a just-closing Vite process.

    A healthy NIMO Vite server can be reused. A listener that is not yet
    serving NIMO is commonly a prior ``tauri dev`` child still releasing its
    port after the window closes, so wait briefly for it to become reusable or
    disappear. Never stop a process that did not belong to this launcher.
    """
    if command[-2:] != ["tauri", "dev"]:
        return command, False

    deadline = time.monotonic() + max(0.0, frontend_port_release_grace_s)
    while True:
        if _nimo_frontend_ready():
            return [*command, "--config", _REUSE_FRONTEND_CONFIG], True
        if not _tcp_port_in_use("127.0.0.1", 1420):
            return command, False
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            raise RuntimeError(
                "前端端口 1420 在等待退出后仍被非 NIMO 服务占用；"
                "NIMO 未启动，也未修改本地 Engine。请确认占用进程后重试。"
            )
        time.sleep(min(_FRONTEND_PORT_POLL_INTERVAL_S, remaining_s))


def _wait_for_backend(
    process: subprocess.Popen[bytes],
    target: BackendTarget,
    *,
    instance_id: str,
    access_token: str,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"本地 Engine 启动失败，退出码 {process.returncode}。")
        probe = _runtime_probe(target.base_url, access_token=access_token)
        if probe is not None and str(probe.get("instanceId", "")) == instance_id:
            return
        time.sleep(0.15)
    raise TimeoutError(f"等待本地 Engine 超时：{target.base_url}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _spawn_backend(
    *,
    target: BackendTarget,
    environment: dict[str, str],
    instance_id: str,
    backend_reload: bool,
) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "novel_forge.api.app:app",
        "--host",
        target.host,
        "--port",
        str(target.port),
    ]
    if backend_reload:
        command.extend(["--reload", "--reload-dir", str(_repo_root())])
    backend_environment = environment.copy()
    backend_environment[LOCAL_ENGINE_INSTANCE_ID_ENV] = instance_id
    backend_environment[LOCAL_ENGINE_MANAGER_ENV] = _LAUNCHER_ID
    return subprocess.Popen(
        command,
        cwd=str(_repo_root()),
        env=backend_environment,
    )


def _restart_idle_owned_backend(
    *,
    target: BackendTarget,
    process: subprocess.Popen[bytes],
    ownership: LocalBackendOwnership,
    environment: dict[str, str],
    access_token: str,
    expected_revision: str,
    timeout_s: float,
) -> tuple[subprocess.Popen[bytes], LocalBackendOwnership]:
    """Replace only this launcher's authenticated, idle, stale child process."""

    if ownership.reload_enabled or process.poll() is not None:
        return process, ownership
    probe = _runtime_probe(target.base_url, access_token=access_token)
    if (
        probe is None
        or process.pid != ownership.backend_pid
        or not _ownership_matches_runtime(target, ownership, probe)
        or any(
            type(probe.get(field)) is not int or probe[field] < 0
            for field in ("activeJobCount", "queuedJobCount")
        )
        or _runtime_has_active_jobs(probe)
        or not _probe_requires_restart(probe, expected_revision)
        or str(probe.get("currentRevision", "")) != expected_revision
    ):
        return process, ownership

    # Revision protection has already stopped new submissions. No active or
    # queued jobs remain; parked autoruns are durable and recover on startup.
    _stop_process(process)
    _remove_ownership(target)
    instance_id = secrets.token_urlsafe(24)
    replacement = _spawn_backend(
        target=target,
        environment=environment,
        instance_id=instance_id,
        backend_reload=False,
    )
    try:
        _wait_for_backend(
            replacement,
            target,
            instance_id=instance_id,
            access_token=access_token,
            timeout_s=timeout_s,
        )
        updated = LocalBackendOwnership(
            base_url=target.base_url,
            backend_pid=int(replacement.pid),
            instance_id=instance_id,
            revision=expected_revision,
            reload_enabled=False,
        )
        _write_ownership(target, updated)
    except BaseException:
        _stop_process(replacement)
        raise
    print("nimo: 空闲受管后端已安全更新，已保存的连跑将自动恢复。", file=sys.stderr)
    return replacement, updated


def _resolve_local_backend(
    *,
    target: BackendTarget,
    access_token: str,
    expected_revision: str,
    backend_reload: bool,
    restart_owned_backend: bool,
) -> tuple[BackendStartupState, LocalBackendOwnership | None]:
    """Classify an existing loopback backend without ever killing an outsider."""

    if not _health_ready(target.base_url):
        if _tcp_port_in_use(target.host, target.port):
            raise RuntimeError(
                f"本地 Engine 需要的端口 {target.port} 已被其他服务占用，且该服务未通过 "
                f"{target.base_url}{_HEALTH_PATH} 健康检查；NIMO 未启动，以免影响它。"
                f"请先使用 `lsof -nP -iTCP:{target.port} -sTCP:LISTEN` 确认占用进程；"
                "确认可停止后执行 `kill <PID>`，再重新运行 nimo。"
            )
        return BackendStartupState(mode="managed-local", read_only=False), None

    probe = _runtime_probe(target.base_url, access_token=access_token)
    ownership = _load_ownership(target)
    if probe is None:
        if restart_owned_backend:
            raise RuntimeError("现有本地 Engine 不支持运行时身份校验，无法安全重启。")
        return (
            BackendStartupState(
                mode="external",
                read_only=True,
                diagnostic="当前本地 Engine 缺少运行时诊断接口，可能是旧版本；已禁止新任务。",
            ),
            None,
        )

    owned = _ownership_matches_runtime(target, ownership, probe)
    needs_restart = _probe_requires_restart(probe, expected_revision)
    reload_changed = owned and ownership is not None and ownership.reload_enabled != backend_reload
    if owned and (needs_restart or reload_changed or restart_owned_backend):
        if _runtime_has_active_jobs(probe):
            return (
                BackendStartupState(
                    mode="managed-protected",
                    read_only=True,
                    diagnostic="受管 Engine 仍有运行或排队任务，已保护任务并拒绝重启。",
                ),
                ownership,
            )
        _stop_owned_backend(ownership)
        _remove_ownership(target)
        return BackendStartupState(mode="managed-local", read_only=False), None

    if restart_owned_backend and not owned:
        raise RuntimeError("现有本地 Engine 不属于本次 NIMO 启动器，已拒绝停止。")
    if owned:
        return BackendStartupState(mode="managed-local", read_only=False), ownership
    if needs_restart:
        return (
            BackendStartupState(
                mode="external",
                read_only=True,
                diagnostic="检测到外部或旧版本地 Engine，无法安全接管；请手动停止后重新运行 nimo。",
            ),
            None,
        )
    return BackendStartupState(mode="external", read_only=False), None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 NIMO React + Tauri 客户端")
    parser.add_argument(
        "--backend-url",
        default=None,
        help="本地或云端 Engine API 根地址",
    )
    parser.add_argument(
        "--access-token",
        default=os.environ.get(
            "NIMO_ENGINE_ACCESS_TOKEN",
            os.environ.get("NOVEL_FORGE_API_ACCESS_TOKEN", ""),
        ),
        help="Engine Bearer token；仅通过进程环境注入 WebView 内存",
    )
    parser.add_argument("--no-backend", action="store_true", help="不自动启动本地 FastAPI Engine")
    parser.add_argument("--allow-insecure-remote", action="store_true")
    parser.add_argument("--dev", action="store_true", help="强制使用源码 Tauri 开发模式")
    parser.add_argument("--binary", help="已打包 Tauri 可执行文件路径")
    parser.add_argument("--backend-timeout", type=float, default=20.0)
    parser.add_argument(
        "--backend-reload",
        action="store_true",
        help="开发期显式启用 Uvicorn reload；保存后端代码会中断正在运行的任务。",
    )
    parser.add_argument(
        "--restart-owned-backend",
        action="store_true",
        help="仅安全重启本启动器拥有且没有活动任务的本地 Engine。",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="仅输出脱敏后的启动配置，不启动进程",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configured_backend_url = os.environ.get("NIMO_ENGINE_BASE_URL", "").strip()
    backend_url = args.backend_url or configured_backend_url or _DEFAULT_BACKEND_URL
    may_select_fallback_port = args.backend_url is None and not configured_backend_url
    try:
        target = _backend_target(
            backend_url,
            allow_insecure_remote=args.allow_insecure_remote,
        )
        command, cwd = _tauri_command(
            repo_root=_repo_root(),
            explicit_binary=args.binary,
            force_dev=args.dev,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"nimo: {exc}", file=sys.stderr)
        return 2

    should_start_backend = target.is_loopback and not args.no_backend
    if args.backend_reload and not should_start_backend:
        print("nimo: --backend-reload 只能用于由 nimo 管理的本地后端。", file=sys.stderr)
        return 2
    if args.restart_owned_backend and not target.is_loopback:
        print("nimo: --restart-owned-backend 只能用于回环地址。", file=sys.stderr)
        return 2
    expected_revision = current_engine_revision() if target.is_loopback else ""
    if args.print_config:
        print(
            json.dumps(
                {
                    "backendUrl": target.base_url,
                    "backendMode": "managed-local" if should_start_backend else "external",
                    "expectedRevision": expected_revision or None,
                    "backendReload": bool(args.backend_reload),
                    "hasAccessToken": bool(args.access_token.strip()),
                    "clientCommand": command,
                },
                ensure_ascii=False,
            )
        )
        return 0

    environment = os.environ.copy()
    backend_process: subprocess.Popen[bytes] | None = None
    spawned_ownership: LocalBackendOwnership | None = None
    try:
        # Resolve the frontend first. If a prior Tauri/Vite child is still
        # releasing 1420, no backend fallback is selected or announced yet.
        client_command, reused_frontend = _prepare_client_command(command)
        if reused_frontend:
            print("nimo: 检测到已运行的 NIMO 前端，正在复用 1420 端口。", file=sys.stderr)

        if should_start_backend and may_select_fallback_port:
            fallback_target = _select_available_local_backend_target(target)
            if fallback_target != target:
                print(
                    f"nimo: 端口 {target.port} 已被其他服务占用，已自动改用本地 Engine 端口 "
                    f"{fallback_target.port}。",
                    file=sys.stderr,
                )
                target = fallback_target
        environment["NIMO_ENGINE_MODE"] = "legacy"
        environment["NIMO_ENGINE_BASE_URL"] = target.base_url
        if args.access_token.strip():
            environment["NIMO_ENGINE_ACCESS_TOKEN"] = args.access_token.strip()

        startup = BackendStartupState(mode="external", read_only=False)
        if should_start_backend:
            startup, _ = _resolve_local_backend(
                target=target,
                access_token=args.access_token.strip(),
                expected_revision=expected_revision,
                backend_reload=bool(args.backend_reload),
                restart_owned_backend=bool(args.restart_owned_backend),
            )
            if startup.mode == "managed-local" and not _health_ready(target.base_url):
                instance_id = secrets.token_urlsafe(24)
                backend_environment = environment.copy()
                if args.access_token.strip():
                    backend_environment["NOVEL_FORGE_API_ACCESS_TOKEN"] = args.access_token.strip()
                backend_process = _spawn_backend(
                    target=target,
                    environment=backend_environment,
                    instance_id=instance_id,
                    backend_reload=bool(args.backend_reload),
                )
                _wait_for_backend(
                    backend_process,
                    target,
                    instance_id=instance_id,
                    access_token=args.access_token.strip(),
                    timeout_s=max(1.0, args.backend_timeout),
                )
                spawned_ownership = LocalBackendOwnership(
                    base_url=target.base_url,
                    backend_pid=int(backend_process.pid),
                    instance_id=instance_id,
                    revision=expected_revision,
                    reload_enabled=bool(args.backend_reload),
                )
                _write_ownership(target, spawned_ownership)

        environment["NIMO_ENGINE_BACKEND_MODE"] = startup.mode
        environment["NIMO_ENGINE_EXPECTED_REVISION"] = expected_revision
        environment["NIMO_ENGINE_READ_ONLY"] = "1" if startup.read_only else "0"
        if startup.diagnostic:
            environment["NIMO_ENGINE_STARTUP_DIAGNOSTIC"] = startup.diagnostic
        if args.backend_reload:
            environment["NIMO_ENGINE_BACKEND_RELOAD"] = "1"

        client_process = subprocess.Popen(client_command, cwd=str(cwd), env=environment)
        if backend_process is None or spawned_ownership is None:
            return client_process.wait()
        observed_revision = expected_revision
        while True:
            try:
                return client_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                revision = current_engine_revision()
                # Require the source revision to remain stable across polls;
                # don't restart in the middle of an editor's multi-file save.
                if revision == observed_revision:
                    backend_process, spawned_ownership = _restart_idle_owned_backend(
                        target=target,
                        process=backend_process,
                        ownership=spawned_ownership,
                        environment=backend_environment,
                        access_token=args.access_token.strip(),
                        expected_revision=revision,
                        timeout_s=max(1.0, args.backend_timeout),
                    )
                observed_revision = revision
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"nimo: {exc}", file=sys.stderr)
        return 1
    finally:
        if backend_process is not None:
            _stop_process(backend_process)
        if spawned_ownership is not None:
            _remove_ownership(target)


if __name__ == "__main__":
    raise SystemExit(main())
