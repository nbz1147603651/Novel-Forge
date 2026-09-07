"""Engine-owned Ollama runtime and model-control primitives.

This module deliberately has no Desktop, Qt, FastAPI, or Tauri dependency.
It is the single process-level owner of a bundled Ollama sidecar and the
only place that talks to Ollama's native management API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse


def native_base_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible or native Ollama endpoint to native API."""

    clean = str(base_url or "").strip() or "http://127.0.0.1:11434"
    clean = clean.rstrip("/")
    lower = clean.lower()
    for suffix in ("/v1", "/api"):
        if lower.endswith(suffix):
            clean = clean[: -len(suffix)].rstrip("/")
            break
    return clean or "http://127.0.0.1:11434"


def host_value(base_url: str) -> str:
    """Return the host:port accepted by ``OLLAMA_HOST``."""

    parsed = urlparse(native_base_url(base_url))
    if parsed.netloc:
        return parsed.netloc
    if parsed.path:
        return parsed.path.lstrip("/")
    return "127.0.0.1:11434"


def is_loopback_endpoint(base_url: str) -> bool:
    """Whether the endpoint is hosted beside the Engine process."""

    host = (urlparse(native_base_url(base_url)).hostname or "").strip().lower()
    return host in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _platform_variants() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("macos", "darwin")
    if sys.platform == "win32":
        return ("windows", "win32")
    return ("linux", sys.platform)


def _runtime_roots(explicit_root: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if explicit_root is not None:
        roots.append(explicit_root)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))

    exe_dir = Path(sys.executable).resolve().parent
    roots.append(exe_dir)
    if sys.platform == "darwin":
        roots.append(exe_dir.parent / "Resources")
    if not getattr(sys, "frozen", False):
        roots.append(Path(__file__).resolve().parents[2])

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _binary_candidates(root: Path) -> list[Path]:
    names = ["ollama.exe"] if sys.platform == "win32" else ["ollama"]
    candidates: list[Path] = []
    base_dirs = [root / "vendor" / "ollama", root / "vendor" / "ollama" / "bin"]
    for variant in _platform_variants():
        base_dirs.extend(
            [
                root / "vendor" / "ollama" / variant,
                root / "vendor" / "ollama" / variant / "bin",
            ]
        )
    for base in base_dirs:
        candidates.extend(base / name for name in names)
    return candidates


def _direct_binary_candidates(root: Path) -> list[Path]:
    names = ["ollama.exe"] if sys.platform == "win32" else ["ollama"]
    return [root / name for name in names]


class OllamaControlError(RuntimeError):
    """A bounded error returned by Ollama's native management API."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        self.retryable = retryable
        super().__init__(message)


class OllamaOperationCancelled(OllamaControlError):
    """The Engine cancelled an in-progress native Ollama operation."""


@dataclass(frozen=True)
class OllamaSidecarResult:
    status: Literal[
        "disabled",
        "healthy",
        "started",
        "missing-binary",
        "unavailable",
        "failed",
    ]
    detail: str
    available: bool
    auto_started: bool = False
    binary_path: Path | None = None
    models_dir: Path | None = None


@dataclass(frozen=True)
class OllamaModel:
    """Safe native-model metadata, without client supplied interpretation."""

    name: str
    size: int = 0
    modified_at: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class OllamaPullProgress:
    status: str
    percent: int = -1
    completed: int = 0
    total: int = 0


class OllamaSidecarService:
    """Manage a bundled Ollama process without any desktop dependency."""

    def __init__(
        self,
        *,
        base_url: str,
        enabled: bool = True,
        auto_start: bool = True,
        prefer_local_route: bool = True,
        binary_path: str | Path | None = None,
        models_dir: str | Path | None = None,
        runtime_root: Path | None = None,
        resource_budget: str = "medium",
        health_timeout_s: float = 1.5,
        startup_timeout_s: float = 20.0,
        poll_interval_s: float = 0.25,
    ) -> None:
        self.base_url = base_url
        self.enabled = enabled
        self.auto_start = auto_start
        self.prefer_local_route = prefer_local_route
        self._binary_path = Path(binary_path).expanduser() if binary_path else None
        self._models_dir = Path(models_dir).expanduser() if models_dir else None
        self._runtime_root = runtime_root
        normalized_budget = str(resource_budget or "medium").strip().lower()
        self._resource_budget = (
            normalized_budget if normalized_budget in {"light", "medium", "high"} else "medium"
        )
        self._health_timeout_s = health_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._poll_interval_s = poll_interval_s
        self._process: subprocess.Popen[bytes] | None = None
        self._owned_process = False
        self._last_result = OllamaSidecarResult(
            status="unavailable", detail="尚未检查 Engine Ollama 服务。", available=False
        )

    @classmethod
    def from_settings(cls, settings: object) -> "OllamaSidecarService":
        return cls(
            base_url=str(getattr(settings, "ollama_base_url", "http://127.0.0.1:11434/v1")),
            enabled=bool(getattr(settings, "ollama_sidecar_enabled", True)),
            auto_start=bool(getattr(settings, "ollama_sidecar_auto_start", True)),
            prefer_local_route=bool(getattr(settings, "ollama_sidecar_prefer_local", True)),
            binary_path=str(getattr(settings, "ollama_sidecar_binary_path", "") or ""),
            models_dir=str(getattr(settings, "ollama_sidecar_models_dir", "") or ""),
            resource_budget=str(getattr(settings, "local_model_resource_budget", "medium") or "medium"),
        )

    @property
    def last_result(self) -> OllamaSidecarResult:
        return self._last_result

    @property
    def owned_process(self) -> bool:
        return self._owned_process

    def ensure_available(self) -> OllamaSidecarResult:
        if not self.enabled:
            self._last_result = OllamaSidecarResult(
                status="disabled", detail="已禁用 Engine Ollama sidecar。", available=False
            )
            return self._last_result
        if self._check_health(timeout_s=self._health_timeout_s):
            self._last_result = OllamaSidecarResult(
                status="healthy",
                detail="检测到可用的 Ollama 服务。",
                available=True,
                binary_path=self.resolve_binary(),
                models_dir=self.resolve_models_dir(),
            )
            return self._last_result
        if not self.auto_start:
            self._last_result = OllamaSidecarResult(
                status="unavailable",
                detail="Ollama 服务不可用，且已禁用自动拉起。",
                available=False,
                binary_path=self.resolve_binary(),
                models_dir=self.resolve_models_dir(),
            )
            return self._last_result
        binary = self.resolve_binary()
        if binary is None:
            self._last_result = OllamaSidecarResult(
                status="missing-binary", detail="未找到 bundled Ollama 可执行文件。", available=False
            )
            return self._last_result
        try:
            self._process = self._launch_process(binary)
            self._owned_process = True
        except Exception as exc:
            self._last_result = OllamaSidecarResult(
                status="failed",
                detail=f"启动 bundled Ollama 失败：{type(exc).__name__}",
                available=False,
                binary_path=binary,
                models_dir=self.resolve_models_dir(),
            )
            return self._last_result
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if self._check_health(timeout_s=self._health_timeout_s):
                self._last_result = OllamaSidecarResult(
                    status="started",
                    detail="已自动拉起 Engine 托管的 Ollama。",
                    available=True,
                    auto_started=True,
                    binary_path=binary,
                    models_dir=self.resolve_models_dir(),
                )
                return self._last_result
            if self._process is not None and self._process.poll() is not None:
                self._last_result = OllamaSidecarResult(
                    status="failed",
                    detail="bundled Ollama 启动后提前退出。",
                    available=False,
                    binary_path=binary,
                    models_dir=self.resolve_models_dir(),
                )
                return self._last_result
            time.sleep(self._poll_interval_s)
        self.shutdown(timeout_s=1.0)
        self._last_result = OllamaSidecarResult(
            status="failed",
            detail="等待 bundled Ollama 就绪超时。",
            available=False,
            binary_path=binary,
            models_dir=self.resolve_models_dir(),
        )
        return self._last_result

    def shutdown(self, *, timeout_s: float = 5.0) -> None:
        """Stop only a process this service started itself."""

        if not self._owned_process or self._process is None:
            return
        process = self._process
        self._process = None
        self._owned_process = False
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=timeout_s)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=max(timeout_s, 0.1))
            except Exception:
                pass

    def resolve_binary(self) -> Path | None:
        explicit = self._binary_path
        if explicit is not None:
            explicit = explicit.resolve()
            if explicit.is_file():
                return explicit
            if explicit.is_dir():
                for candidate in [*_direct_binary_candidates(explicit), *_binary_candidates(explicit)]:
                    if candidate.is_file():
                        return candidate.resolve()
            return None
        for root in _runtime_roots(self._runtime_root):
            for candidate in _binary_candidates(root):
                if candidate.is_file():
                    return candidate.resolve()
        return None

    def resolve_models_dir(self) -> Path | None:
        if self._models_dir is not None:
            return self._models_dir.resolve()
        binary = self.resolve_binary()
        if binary is not None:
            candidate = binary.parent / "models"
            if candidate.exists():
                return candidate.resolve()
        for root in _runtime_roots(self._runtime_root):
            candidate = root / "vendor" / "ollama" / "models"
            if candidate.exists():
                return candidate.resolve()
        return None

    def is_local_endpoint(self) -> bool:
        return is_loopback_endpoint(self.base_url)

    def suggest_external_models_dir(self) -> Path | None:
        if not self.is_local_endpoint():
            return None
        explicit = os.environ.get("OLLAMA_MODELS", "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        return (Path.home() / ".ollama" / "models").resolve()

    def _launch_process(self, binary: Path) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        env.setdefault("OLLAMA_HOST", host_value(self.base_url))
        env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
        env.setdefault("OLLAMA_NUM_PARALLEL", "1")
        env.setdefault(
            "OLLAMA_KEEP_ALIVE", {"light": "15s", "medium": "60s", "high": "5m"}[self._resource_budget]
        )
        models_dir = self.resolve_models_dir()
        if models_dir is not None:
            models_dir.mkdir(parents=True, exist_ok=True)
            env.setdefault("OLLAMA_MODELS", str(models_dir))
        return subprocess.Popen(
            [str(binary), "serve"],
            cwd=str(binary.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _check_health(self, *, timeout_s: float) -> bool:
        request = urllib.request.Request(f"{native_base_url(self.base_url)}/api/version", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return int(response.status) == 200
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            return False


class OllamaControlService:
    """Single Engine-side owner for Ollama runtime and native model operations."""

    def __init__(self, settings: object) -> None:
        self._sidecar = OllamaSidecarService.from_settings(settings)
        self._lock = threading.RLock()

    @property
    def sidecar(self) -> OllamaSidecarService:
        return self._sidecar

    @property
    def base_url(self) -> str:
        return self._sidecar.base_url

    def ensure_runtime(self) -> OllamaSidecarResult:
        with self._lock:
            return self._sidecar.ensure_available()

    def stop_runtime(self) -> None:
        with self._lock:
            if not self._sidecar.owned_process:
                raise OllamaControlError("只能停止 Engine 启动的 Ollama sidecar。")
            self._sidecar.shutdown()

    def restart_runtime(self) -> OllamaSidecarResult:
        with self._lock:
            self._sidecar.shutdown()
            return self._sidecar.ensure_available()

    def shutdown(self) -> None:
        with self._lock:
            self._sidecar.shutdown()

    def version(self) -> str:
        payload = self._request_json("GET", "/api/version")
        return str(payload.get("version") or "")

    def list_models(self) -> list[OllamaModel]:
        payload = self._request_json("GET", "/api/tags")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return []
        models: list[OllamaModel] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            size = item.get("size")
            models.append(
                OllamaModel(
                    name=name,
                    size=int(size) if isinstance(size, (int, float)) else 0,
                    modified_at=str(item.get("modified_at") or ""),
                    details=dict(item.get("details")) if isinstance(item.get("details"), dict) else {},
                )
            )
        return models

    def model_exists(self, model: str) -> bool:
        """Resolve idempotent pull/delete recovery against the native catalog."""

        normalized = validate_model_name(model)
        return any(item.name == normalized for item in self.list_models())

    def pull_model(
        self,
        model: str,
        *,
        on_progress: Callable[[OllamaPullProgress], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        timeout_s: float = 900.0,
    ) -> None:
        normalized = validate_model_name(model)
        payload = json.dumps({"name": normalized, "stream": True}).encode("utf-8")
        request = urllib.request.Request(
            f"{native_base_url(self.base_url)}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                for raw_line in response:
                    if is_cancelled is not None and is_cancelled():
                        raise OllamaOperationCancelled("下载已取消；Ollama 服务端可能仍在完成缓存层。")
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise OllamaControlError(str(event["error"])[:800], retryable=False)
                    completed = int(event.get("completed") or 0)
                    total = int(event.get("total") or 0)
                    percent = min(100, int(completed * 100 / total)) if total > 0 else -1
                    if on_progress is not None:
                        on_progress(
                            OllamaPullProgress(
                                status=str(event.get("status") or "下载中"),
                                percent=percent,
                                completed=completed,
                                total=total,
                            )
                        )
        except OllamaControlError:
            raise
        except urllib.error.HTTPError as exc:
            raise OllamaControlError(
                _native_error_message(exc), retryable=not 400 <= exc.code < 500
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaControlError(_native_error_message(exc)) from exc

    def delete_model(self, model: str, *, timeout_s: float = 30.0) -> None:
        normalized = validate_model_name(model)
        payload = json.dumps({"name": normalized}).encode("utf-8")
        request = urllib.request.Request(
            f"{native_base_url(self.base_url)}/api/delete",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s):
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise OllamaControlError(_native_error_message(exc), retryable=False) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaControlError(_native_error_message(exc)) from exc

    def _request_json(self, method: str, path: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
        request = urllib.request.Request(f"{native_base_url(self.base_url)}{path}", method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaControlError(
                _native_error_message(exc), retryable=not 400 <= exc.code < 500
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaControlError("Ollama 返回了无效响应。") from exc
        if not isinstance(value, dict):
            raise OllamaControlError("Ollama 返回了无效响应。")
        return value


def validate_model_name(value: str) -> str:
    """Validate an Ollama name before it can reach the native API."""

    model = str(value or "").strip()
    if not model or len(model) > 160:
        raise OllamaControlError("模型名不能为空且不能超过 160 个字符。", retryable=False)
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-+")
    if any(char not in allowed for char in model) or model.startswith(("/", ".")):
        raise OllamaControlError("模型名包含不支持的字符。", retryable=False)
    return model


def _native_error_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read(2048).decode("utf-8", errors="replace")
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                return str(parsed["error"])[:800]
        except Exception:
            pass
        return f"Ollama 请求失败（HTTP {exc.code}）。"
    return f"无法连接 Engine 主机上的 Ollama：{type(exc).__name__}。"


_SERVICE_LOCK = threading.RLock()
_SERVICE: OllamaControlService | None = None
_SERVICE_FINGERPRINT = ""


def get_ollama_control_service(settings: object) -> OllamaControlService:
    """Return the Engine process singleton and preserve sidecar ownership."""

    global _SERVICE, _SERVICE_FINGERPRINT
    fingerprint = "|".join(
        str(getattr(settings, field, "") or "")
        for field in (
            "ollama_base_url",
            "ollama_sidecar_enabled",
            "ollama_sidecar_auto_start",
            "ollama_sidecar_binary_path",
            "ollama_sidecar_models_dir",
            "ollama_sidecar_prefer_local",
            "local_model_resource_budget",
        )
    )
    with _SERVICE_LOCK:
        if _SERVICE is not None and _SERVICE_FINGERPRINT == fingerprint:
            return _SERVICE
        if _SERVICE is not None:
            _SERVICE.shutdown()
        _SERVICE = OllamaControlService(settings)
        _SERVICE_FINGERPRINT = fingerprint
        return _SERVICE


def shutdown_ollama_control_service() -> None:
    """Release the Engine-owned sidecar during process shutdown or reload."""

    global _SERVICE, _SERVICE_FINGERPRINT
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            _SERVICE.shutdown()
        _SERVICE = None
        _SERVICE_FINGERPRINT = ""


__all__ = [
    "OllamaControlError",
    "OllamaControlService",
    "OllamaModel",
    "OllamaOperationCancelled",
    "OllamaPullProgress",
    "OllamaSidecarResult",
    "OllamaSidecarService",
    "get_ollama_control_service",
    "host_value",
    "is_loopback_endpoint",
    "native_base_url",
    "shutdown_ollama_control_service",
    "validate_model_name",
]
