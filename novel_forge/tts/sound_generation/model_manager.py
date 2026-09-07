"""Managed local cache for Stable Audio model weights.

The manager deliberately owns only Hugging Face cache operations.  It does not
install the heavyweight Stable Audio runtime or import its inference package,
so desktop settings can inspect and maintain model files without loading
PyTorch.  The generation provider receives the same ``HF_HOME`` directory,
which keeps download and inference cache locations aligned.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shlex
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tqdm.auto import tqdm as _TqdmBase

from novel_forge.tts.sound_generation.models.catalog import (
    SOUND_MODEL_CATALOG,
    SoundModelDescriptor,
)

DownloadProgress = Callable[[str, int], None]
CancelCallback = Callable[[], bool]


def _format_bytes(value: float) -> str:
    """Format a byte count into a human-readable string."""
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(value, 0))
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}" if idx else f"{int(size)} {units[idx]}"


def _build_tqdm_bridge_class(
    emit: DownloadProgress,
    should_cancel: CancelCallback | None = None,
) -> type:
    """Build a tqdm-compatible class that forwards byte-level download progress.

    ``huggingface_hub.snapshot_download`` creates multiple tqdm bars internally
    (e.g. "Fetching files", "Downloading bytes", "Reconstructing").  This bridge
    intercepts the one whose ``unit`` is ``"B"`` (the byte-level transfer) and
    forwards human-readable progress to the UI callback.  Other bars are silently
    ignored so the terminal stays clean.
    """

    class _ProgressBridge(_TqdmBase):
        """Full tqdm subclass compatible with Hugging Face thread_map."""

        def __init__(
            self,
            iterable: Any = None,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            self._ui_count = float(kwargs.get("initial") or 0)
            self._ui_started_at = time.monotonic()
            self._ui_is_reconstruction_bar = str(
                kwargs.get("unit") or ""
            ) == "B" and "Reconstruct" in str(kwargs.get("desc") or "")
            kwargs["disable"] = bool(getattr(sys, "frozen", False))
            super().__init__(iterable, *args, **kwargs)

        # -- tqdm interface used by snapshot_download -------------------------

        def update(self, n: int | float = 1) -> Any:
            if should_cancel is not None and should_cancel():
                raise StableAudioModelDownloadCancelled("下载已取消，已保留缓存中的已下载文件。")
            self._ui_count += n
            result = super().update(n)
            if self.disable:
                self.n = self._ui_count
            total = float(self.total or 0)
            if not self._ui_is_reconstruction_bar or total <= 0:
                return result
            elapsed = max(time.monotonic() - self._ui_started_at, 0.001)
            speed = self._ui_count / elapsed
            pct = min(int(self._ui_count / total * 100), 99)
            downloaded = _format_bytes(self._ui_count)
            total_str = _format_bytes(total)
            speed_str = f"{_format_bytes(speed)}/s"
            if speed > 0:
                eta_s = max(0, int((total - self._ui_count) / speed))
                eta = f"{eta_s // 60}m{eta_s % 60:02d}s" if eta_s >= 60 else f"{eta_s}s"
                message = f"下载中 {downloaded} / {total_str} · {speed_str} · 剩余 {eta}"
            else:
                message = f"下载中 {downloaded} / {total_str}"
            emit(message, pct)
            return result

        def __iter__(self) -> Any:
            for item in super().__iter__():
                if should_cancel is not None and should_cancel():
                    raise StableAudioModelDownloadCancelled(
                        "下载已取消，已保留缓存中的已下载文件。"
                    )
                yield item

    return _ProgressBridge


class StableAudioModelManagerError(RuntimeError):
    """Raised when a managed Stable Audio model operation cannot complete."""


class StableAudioModelDownloadCancelled(StableAudioModelManagerError):
    """Raised when the user stops a Hugging Face model transfer."""


@dataclass(frozen=True)
class StableAudioRuntimeStatus:
    """Availability of the configured Stable Audio command and Python package."""

    command: str
    cli_available: bool
    python_package_available: bool
    huggingface_hub_available: bool
    detail: str


@dataclass(frozen=True)
class ManagedSoundModelStatus:
    """One Stable Audio descriptor combined with its local cache state."""

    descriptor: SoundModelDescriptor
    installed: bool
    installed_size_bytes: int
    cache_path: Path


class StableAudioModelManager:
    """Download, list, and safely remove Stable Audio models in one cache root."""

    _CACHE_DIRECTORY_NAME = "hub"

    def __init__(self, *, models_dir: str | Path, command: str = "stable-audio") -> None:
        self._models_dir = Path(models_dir).expanduser()
        self._command = command.strip()

    @property
    def models_dir(self) -> Path:
        """Root exposed to Stable Audio through the ``HF_HOME`` environment variable."""
        return self._models_dir

    @property
    def cache_dir(self) -> Path:
        """Hugging Face Hub cache path below the configured model root."""
        return self.models_dir / self._CACHE_DIRECTORY_NAME

    def runtime_status(self) -> StableAudioRuntimeStatus:
        """Return a non-invasive runtime check; no subprocess is started."""
        try:
            command_parts = shlex.split(self._command)
        except ValueError:
            command_parts = []
        executable = command_parts[0] if command_parts else ""
        cli_available = bool(
            executable
            and (Path(executable).expanduser().is_file() or shutil.which(executable) is not None)
        )
        python_package_available = importlib.util.find_spec("stable_audio_3") is not None
        huggingface_hub_available = importlib.util.find_spec("huggingface_hub") is not None
        if not huggingface_hub_available:
            detail = "当前运行时缺少 huggingface-hub；模型下载不可用，请更新桌面应用。"
        elif self._command and not command_parts:
            detail = "Stable Audio CLI 路径格式无效；请检查引号和命令配置。"
        elif cli_available:
            detail = f"CLI 已就绪：{executable}"
        elif python_package_available:
            detail = "检测到 stable_audio_3，但未找到 stable-audio CLI；请配置可执行路径。"
        else:
            detail = "未检测到 Stable Audio 3 运行时；可先下载模型，再按官方说明安装运行时。"
        return StableAudioRuntimeStatus(
            command=self._command,
            cli_available=cli_available,
            python_package_available=python_package_available,
            huggingface_hub_available=huggingface_hub_available,
            detail=detail,
        )

    def list_models(self) -> list[ManagedSoundModelStatus]:
        """List all built-in Stable Audio models and their state in this cache root."""
        statuses: list[ManagedSoundModelStatus] = []
        for descriptor in SOUND_MODEL_CATALOG.values():
            if descriptor.provider_id != "stable_audio" or not descriptor.repository_id:
                continue
            cache_path = self._cache_path_for(descriptor)
            installed = self._is_installed(cache_path, descriptor)
            statuses.append(
                ManagedSoundModelStatus(
                    descriptor=descriptor,
                    installed=installed,
                    installed_size_bytes=self._directory_size(cache_path) if installed else 0,
                    cache_path=cache_path,
                )
            )
        return statuses

    def download(
        self,
        model_id: str,
        *,
        token: str = "",
        progress: DownloadProgress | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> ManagedSoundModelStatus:
        """Download one approved descriptor into the configured Hugging Face cache.

        Access approval remains an explicit Hugging Face account action.  A token
        is optional here because an already logged-in local Hugging Face client
        may supply credentials, but gated Stable Audio repositories normally
        require the user to accept their terms first.
        """
        descriptor = self._descriptor_for(model_id)
        emit = progress or (lambda _message, _percent: None)
        if should_cancel is not None and should_cancel():
            raise StableAudioModelDownloadCancelled("下载已取消。")
        emit(f"准备下载 {descriptor.display_name}", -1)
        try:
            module = importlib.import_module("huggingface_hub")
        except ImportError as exc:
            raise StableAudioModelManagerError(
                "当前 NIMO 运行时缺少 huggingface-hub，无法下载模型。"
                "请更新或重新安装桌面应用；该依赖已随正式桌面包提供。"
            ) from exc
        candidate = getattr(module, "snapshot_download", None)
        if not callable(candidate):
            raise StableAudioModelManagerError("当前 huggingface-hub 未提供 snapshot_download。")
        snapshot_download = cast(Callable[..., str], candidate)

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Disable the xet download protocol — standard HTTP is more reliable
        # for large LFS files in environments where the xet client or its
        # TLS chain is not fully functional.  Keep Hugging Face's partial cache
        # intact so an explicit cancel or transient failure can resume instead
        # of downloading multi-gigabyte files from zero.
        prev_xet = os.environ.get("HF_HUB_DISABLE_XET")
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        tqdm_class = _build_tqdm_bridge_class(emit, should_cancel)
        try:
            emit("正在从 Hugging Face 下载模型文件…", -1)
            snapshot_download(
                repo_id=descriptor.repository_id,
                cache_dir=str(self.cache_dir),
                token=token.strip() or None,
                force_download=False,
                tqdm_class=tqdm_class,
            )
        except StableAudioModelDownloadCancelled:
            raise
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            if descriptor.requires_access_approval:
                message = (
                    f"{message}\n请先在模型页登录 Hugging Face、接受 Stable Audio 与 Gemma 条款，"
                    "再在此处填入具备访问权限的 HF Token。"
                )
            raise StableAudioModelManagerError(message) from exc
        finally:
            if prev_xet is None:
                os.environ.pop("HF_HUB_DISABLE_XET", None)
            else:
                os.environ["HF_HUB_DISABLE_XET"] = prev_xet

        status = self.model_status(model_id)
        if not status.installed:
            raise StableAudioModelManagerError("下载完成但未在受管缓存中找到完整模型文件。")
        emit("下载完成并已校验本地缓存。", 100)
        return status

    def model_status(self, model_id: str) -> ManagedSoundModelStatus:
        """Return one model status, rejecting unsupported or unmanaged IDs."""
        descriptor = self._descriptor_for(model_id)
        cache_path = self._cache_path_for(descriptor)
        installed = self._is_installed(cache_path, descriptor)
        return ManagedSoundModelStatus(
            descriptor=descriptor,
            installed=installed,
            installed_size_bytes=self._directory_size(cache_path) if installed else 0,
            cache_path=cache_path,
        )

    def delete(self, model_id: str) -> None:
        """Remove only this manager's repository cache, never an arbitrary path."""
        descriptor = self._descriptor_for(model_id)
        cache_path = self._cache_path_for(descriptor)
        if not cache_path.exists():
            return
        try:
            cache_path.resolve().relative_to(self.cache_dir.resolve())
        except ValueError as exc:
            raise StableAudioModelManagerError("拒绝删除缓存根目录之外的路径。") from exc
        shutil.rmtree(cache_path)

    def ensure_models_dir(self) -> Path:
        """Create and return the configured root when the user opens it explicitly."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        return self.models_dir

    def _descriptor_for(self, model_id: str) -> SoundModelDescriptor:
        descriptor = SOUND_MODEL_CATALOG.get(model_id)
        if (
            descriptor is None
            or descriptor.provider_id != "stable_audio"
            or descriptor.integration_status != "built_in"
            or not descriptor.repository_id
        ):
            raise StableAudioModelManagerError(f"不支持受管下载的 Stable Audio 模型：{model_id}")
        return descriptor

    def _cache_path_for(self, descriptor: SoundModelDescriptor) -> Path:
        safe_repository_name = descriptor.repository_id.replace("/", "--")
        return self.cache_dir / f"models--{safe_repository_name}"

    @staticmethod
    def _is_installed(cache_path: Path, descriptor: SoundModelDescriptor | None = None) -> bool:
        """Return whether the cache contains a plausible complete model.

        Beyond checking that ``snapshots/`` holds at least one valid file, this
        method also rejects broken symlinks (left behind by interrupted
        ``snapshot_download`` runs) and caches whose total size is far below
        the expected download footprint.
        """
        snapshots_dir = cache_path / "snapshots"
        if not snapshots_dir.is_dir():
            return False
        has_valid_file = False
        for path in snapshots_dir.rglob("*"):
            if path.is_symlink() and not path.exists():
                continue  # broken symlink from interrupted download
            if path.is_file():
                has_valid_file = True
                break
        if not has_valid_file:
            return False
        if descriptor is not None and descriptor.estimated_download_bytes > 0:
            min_bytes = max(100_000_000, descriptor.estimated_download_bytes // 20)
            return StableAudioModelManager._directory_size(cache_path) >= min_bytes
        return True

    @staticmethod
    def _directory_size(path: Path) -> int:
        try:
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        except OSError:
            return 0
