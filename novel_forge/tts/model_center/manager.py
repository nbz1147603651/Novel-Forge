"""Transactional application-level storage for managed audio model weights."""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
import tarfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from tqdm.auto import tqdm as _TqdmBase

from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.tts.model_center.catalog import audio_model_catalog
from novel_forge.tts.model_center.downloads import PersistentDownloadManager
from novel_forge.tts.model_center.locking import model_center_lock
from novel_forge.tts.model_center.schemas import (
    AudioModelDescriptor,
    AudioModelInstallState,
    AudioModelInventory,
    AudioModelInventoryRecord,
    AudioModelSource,
    AudioModelStatus,
)
from novel_forge.tts.sound_generation.model_manager import (
    StableAudioModelDownloadCancelled,
    StableAudioModelManager,
)

ProgressCallback = Callable[[str, int], None]
CancelCallback = Callable[[], bool]


class AudioModelCenterError(RuntimeError):
    pass


class AudioModelDownloadCancelled(AudioModelCenterError):
    """Raised when a user stops an in-progress model transfer."""


def _build_huggingface_progress_bridge(
    emit: ProgressCallback,
    should_cancel: CancelCallback | None,
) -> type:
    """Forward Hugging Face progress to the UI and stop at safe update points."""

    class _ProgressBridge(_TqdmBase):
        """Real tqdm subclass required by Hugging Face's concurrent downloader."""

        def __init__(
            self,
            iterable: Any = None,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            self._ui_unit = str(kwargs.get("unit") or "")
            self._ui_desc = str(kwargs.get("desc") or "")
            self._ui_count = float(kwargs.get("initial") or 0)
            # Source/debug launches should retain terminal diagnostics.  Frozen
            # desktop builds render progress only in Qt and stay terminal-silent.
            kwargs["disable"] = bool(getattr(sys, "frozen", False))
            super().__init__(iterable, *args, **kwargs)

        def _check_cancel(self) -> None:
            if should_cancel is not None and should_cancel():
                raise AudioModelDownloadCancelled("下载已取消，已保留可恢复的分片。")

        def update(self, amount: int | float = 1) -> Any:
            self._check_cancel()
            self._ui_count += amount
            result = super().update(amount)
            if self.disable:
                # tqdm skips its counter when disabled, but Hugging Face reads
                # ``n`` while aggregating Xet transfer/reconstruction progress.
                self.n = self._ui_count
            # snapshot_download creates byte bars with total=0, then grows
            # ``bar.total`` as file metadata arrives.  Read the live value here
            # rather than the constructor argument.
            total = float(self.total or 0)
            is_reconstruction = self._ui_unit == "B" and "Reconstruct" in self._ui_desc
            if is_reconstruction and total > 0:
                percent = min(99, int(self._ui_count * 100 / total))
                emit(
                    "正在接收并重建模型文件 "
                    f"{self._ui_count / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB",
                    percent,
                )
            return result

        def __iter__(self) -> Any:
            for item in super().__iter__():
                self._check_cancel()
                yield item

    return _ProgressBridge


class ApplicationAudioModelManager:
    """Own files and inventory; inference frameworks remain in sidecars."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.audio_models_root).expanduser().resolve()
        self.plugins_root = self.root / "plugins"
        self.inventory_path = self.root / "inventory.json"
        self.downloads = PersistentDownloadManager(self.root / "downloads")
        self._catalog = {item.plugin_id: item for item in audio_model_catalog()}

    def list_models(self) -> list[AudioModelStatus]:
        return [self.model_status(item.plugin_id) for item in self._catalog.values()]

    def descriptor(self, plugin_id: str) -> AudioModelDescriptor:
        """Return a catalog descriptor without exposing manager internals."""

        return self._descriptor(plugin_id)

    def model_status(self, plugin_id: str) -> AudioModelStatus:
        descriptor = self._descriptor(plugin_id)
        inventory_record = self._inventory_record(plugin_id)
        installed_revision = inventory_record.revision if inventory_record is not None else ""
        references = self.project_references(plugin_id)
        license_accepted = self._license_accepted(plugin_id)
        if descriptor.source == AudioModelSource.SIDECAR:
            return AudioModelStatus(
                descriptor=descriptor,
                state=AudioModelInstallState.EXTERNAL,
                project_references=references,
                license_accepted=license_accepted,
                installed_revision=installed_revision,
                detail="模型由 sidecar 管理；连接运行时后可安装并执行自检。",
            )
        if descriptor.source == AudioModelSource.STABLE_AUDIO:
            manager = StableAudioModelManager(
                models_dir=self.settings.sound_generation_stable_audio_models_dir,
                command=self.settings.sound_generation_stable_audio_command,
            )
            status = manager.model_status(descriptor.model_id)
            return AudioModelStatus(
                descriptor=descriptor,
                state=(
                    AudioModelInstallState.INSTALLED
                    if status.installed
                    else AudioModelInstallState.NOT_INSTALLED
                ),
                local_path=str(status.cache_path),
                installed_size_bytes=status.installed_size_bytes,
                project_references=references,
                license_accepted=license_accepted,
                installed_revision=installed_revision,
            )
        path = self._model_dir(descriptor)
        present = path.exists()
        valid = present and self._validate_files(path, descriptor)
        state = (
            AudioModelInstallState.INSTALLED
            if valid
            else AudioModelInstallState.INCOMPLETE
            if present
            else AudioModelInstallState.NOT_INSTALLED
        )
        return AudioModelStatus(
            descriptor=descriptor,
            state=state,
            local_path=str(path),
            installed_size_bytes=self._directory_size(path) if present else 0,
            project_references=references,
            license_accepted=license_accepted,
            installed_revision=installed_revision,
            detail="本地文件完整。" if valid else "等待下载。" if not present else "文件不完整。",
        )

    def record_license_acceptance(self, plugin_id: str) -> AudioModelStatus:
        """Persist an affirmative license acknowledgement before installation."""

        descriptor = self._descriptor(plugin_id)
        if not descriptor.requires_license_acceptance:
            raise AudioModelCenterError("此模型不要求单独确认许可条款。")
        with model_center_lock(self.root / "locks" / f"model-{plugin_id}.lock"):
            self._record_install(descriptor, accept_license=True)
        return self.model_status(plugin_id)

    def install(
        self,
        plugin_id: str,
        *,
        token: str = "",
        accept_license: bool = False,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> AudioModelStatus:
        descriptor = self._descriptor(plugin_id)
        if descriptor.requires_license_acceptance and not accept_license:
            raise AudioModelCenterError("必须先确认已阅读并接受该模型许可条款。")
        if descriptor.source == AudioModelSource.SIDECAR:
            raise AudioModelCenterError("此模型必须通过对应 sidecar 安装。")
        emit = progress or (lambda _message, _percent: None)
        resolved_revision = descriptor.revision
        if should_cancel is not None and should_cancel():
            raise AudioModelDownloadCancelled("下载已取消。")
        with model_center_lock(self.root / "locks" / f"model-{plugin_id}.lock"):
            existing = self.model_status(plugin_id)
            if existing.state == AudioModelInstallState.INSTALLED:
                self._record_install(descriptor, accept_license=accept_license)
                emit("模型已安装并通过文件校验，直接复用现有文件。", 100)
                return existing
            if descriptor.source == AudioModelSource.STABLE_AUDIO:
                manager = StableAudioModelManager(
                    models_dir=self.settings.sound_generation_stable_audio_models_dir,
                    command=self.settings.sound_generation_stable_audio_command,
                )
                try:
                    manager.download(
                        descriptor.model_id,
                        token=token,
                        progress=emit,
                        should_cancel=should_cancel,
                    )
                except StableAudioModelDownloadCancelled as exc:
                    raise AudioModelDownloadCancelled(str(exc)) from exc
            else:
                destination = self._model_dir(descriptor)
                staging = destination.with_name(f".{destination.name}.partial")
                staging.mkdir(parents=True, exist_ok=True)
                try:
                    if descriptor.source == AudioModelSource.HUGGINGFACE:
                        resolved_revision = self._download_huggingface(
                            descriptor,
                            staging,
                            token=token,
                            emit=emit,
                            should_cancel=should_cancel,
                        )
                    elif descriptor.source == AudioModelSource.RELEASE_ARCHIVE:
                        self._download_archive(
                            descriptor,
                            staging,
                            emit=emit,
                            should_cancel=should_cancel,
                        )
                    elif descriptor.source == AudioModelSource.DIRECT_FILE:
                        self._download_direct(
                            descriptor,
                            staging,
                            emit=emit,
                            should_cancel=should_cancel,
                        )
                    if not self._validate_files(staging, descriptor):
                        raise AudioModelCenterError("下载结束，但模型文件清单校验失败。")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        shutil.rmtree(destination)
                    staging.replace(destination)
                    if descriptor.source == AudioModelSource.RELEASE_ARCHIVE:
                        self._archive_path(descriptor).unlink(missing_ok=True)
                except Exception:
                    # Keep provider-native cache metadata and HTTP .part files;
                    # the next run resumes into the same staging directory.
                    raise
        self._record_install(
            descriptor,
            accept_license=accept_license,
            revision=resolved_revision,
        )
        emit("安装完成并通过文件校验。", 100)
        return self.model_status(plugin_id)

    def delete(self, plugin_id: str, *, force: bool = False) -> None:
        descriptor = self._descriptor(plugin_id)
        references = self.project_references(plugin_id)
        if references and not force:
            raise AudioModelCenterError(
                "该模型仍被项目引用：" + "、".join(references[:6]) + "。确认后可强制删除。"
            )
        if descriptor.source == AudioModelSource.SIDECAR:
            raise AudioModelCenterError("请通过对应 sidecar 删除该模型。")
        with model_center_lock(self.root / "locks" / f"model-{plugin_id}.lock"):
            if descriptor.source == AudioModelSource.STABLE_AUDIO:
                StableAudioModelManager(
                    models_dir=self.settings.sound_generation_stable_audio_models_dir,
                    command=self.settings.sound_generation_stable_audio_command,
                ).delete(descriptor.model_id)
            else:
                path = self._model_dir(descriptor)
                if path.exists():
                    try:
                        path.resolve().relative_to(self.plugins_root.resolve())
                    except ValueError as exc:
                        raise AudioModelCenterError("拒绝删除应用模型库之外的路径。") from exc
                    shutil.rmtree(path)
            inventory = self._load_inventory()
            inventory.records = [item for item in inventory.records if item.plugin_id != plugin_id]
            self._save_inventory(inventory)

    def project_references(self, plugin_id: str) -> list[str]:
        root = Path(self.settings.storage_root)
        references: list[str] = []
        if not root.is_dir():
            return references
        for plan_path in root.glob("*/tts/audio_execution_plan.json"):
            try:
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            routes = payload.get("routes", []) if isinstance(payload, dict) else []
            if any(
                isinstance(item, dict)
                and (
                    (
                        isinstance(item.get("primary"), dict)
                        and item["primary"].get("plugin_id") == plugin_id
                    )
                    or any(
                        isinstance(fallback, dict) and fallback.get("plugin_id") == plugin_id
                        for fallback in item.get("fallbacks", [])
                    )
                )
                for item in routes
            ):
                references.append(plan_path.parents[1].name)
        return sorted(set(references))

    def _download_huggingface(
        self,
        descriptor: AudioModelDescriptor,
        destination: Path,
        *,
        token: str,
        emit: ProgressCallback,
        should_cancel: CancelCallback | None,
    ) -> str:
        try:
            module = importlib.import_module("huggingface_hub")
        except ImportError as exc:
            raise AudioModelCenterError("当前应用缺少 huggingface-hub，无法下载模型。") from exc
        snapshot_download = cast(Any, getattr(module, "snapshot_download", None))
        if not callable(snapshot_download):
            raise AudioModelCenterError("huggingface-hub 不支持 snapshot_download。")
        revision_marker = destination / ".novel-forge-revision"
        try:
            resolved_revision = revision_marker.read_text(encoding="utf-8").strip()
        except OSError:
            resolved_revision = ""
        if len(resolved_revision) < 7:
            api_class = getattr(module, "HfApi", None)
            if not callable(api_class):
                raise AudioModelCenterError("huggingface-hub 不支持解析不可变模型版本。")
            try:
                info = api_class(token=token.strip() or None).model_info(
                    descriptor.repository_id,
                    revision=descriptor.revision,
                )
                resolved_revision = str(getattr(info, "sha", "") or "").strip()
            except Exception as exc:
                raise AudioModelCenterError(f"无法解析模型的不可变版本：{exc}") from exc
        if len(resolved_revision) < 7:
            raise AudioModelCenterError("模型仓库未返回可审计的提交版本，已停止下载。")
        revision_marker.write_text(resolved_revision, encoding="utf-8")
        emit(f"正在下载 {descriptor.display_name}…", -1)
        snapshot_download(
            repo_id=descriptor.repository_id,
            revision=resolved_revision,
            local_dir=str(destination),
            token=token.strip() or None,
            tqdm_class=_build_huggingface_progress_bridge(emit, should_cancel),
        )
        return resolved_revision

    def _download_archive(
        self,
        descriptor: AudioModelDescriptor,
        destination: Path,
        *,
        emit: ProgressCallback,
        should_cancel: CancelCallback | None,
    ) -> None:
        archive = self._archive_path(descriptor)
        self._stream_download(descriptor, archive, emit, should_cancel=should_cancel)
        # A prior extraction may have failed halfway. Re-extract from the verified
        # archive instead of mixing old and new files or downloading it again.
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:bz2") as handle:
            for member in handle.getmembers():
                target = (destination / member.name).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise AudioModelCenterError("模型压缩包包含不安全路径。")
            handle.extractall(destination, filter="data")
        children = [item for item in destination.iterdir()]
        if len(children) == 1 and children[0].is_dir():
            nested = children[0]
            for item in nested.iterdir():
                shutil.move(str(item), destination / item.name)
            nested.rmdir()

    def _download_direct(
        self,
        descriptor: AudioModelDescriptor,
        destination: Path,
        *,
        emit: ProgressCallback,
        should_cancel: CancelCallback | None,
    ) -> None:
        filename = descriptor.required_files[0]
        self._stream_download(
            descriptor,
            destination / filename,
            emit,
            should_cancel=should_cancel,
        )

    def _stream_download(
        self,
        descriptor: AudioModelDescriptor,
        target: Path,
        emit: ProgressCallback,
        *,
        should_cancel: CancelCallback | None,
    ) -> None:
        raw = f"{descriptor.plugin_id}|{descriptor.revision}|{target.name}"
        task_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        self.downloads.create_task(
            task_id=task_id,
            plugin_id=descriptor.plugin_id,
            url=descriptor.download_url,
            target_path=target,
            expected_sha256=descriptor.sha256,
            signature=descriptor.signature,
            signature_key_id=descriptor.signature_key_id,
        )
        task = self.downloads.run(task_id, progress=emit, should_pause=should_cancel)
        if task.state.value == "paused":
            raise AudioModelDownloadCancelled("下载已取消，已保留可恢复的分片。")

    def _model_dir(self, descriptor: AudioModelDescriptor) -> Path:
        return self.plugins_root / descriptor.plugin_id / descriptor.revision

    def _archive_path(self, descriptor: AudioModelDescriptor) -> Path:
        return (
            self.plugins_root / descriptor.plugin_id / f".{descriptor.plugin_id}.download.tar.bz2"
        )

    @staticmethod
    def _validate_files(path: Path, descriptor: AudioModelDescriptor) -> bool:
        return bool(path.is_dir()) and all(
            any(path.rglob(pattern)) for pattern in descriptor.required_files
        )

    @staticmethod
    def _directory_size(path: Path) -> int:
        try:
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        except OSError:
            return 0

    def _descriptor(self, plugin_id: str) -> AudioModelDescriptor:
        descriptor = self._catalog.get(plugin_id)
        if descriptor is None:
            raise AudioModelCenterError(f"模型中心未登记插件：{plugin_id}")
        return descriptor

    def _load_inventory(self) -> AudioModelInventory:
        if not self.inventory_path.is_file():
            return AudioModelInventory()
        try:
            return AudioModelInventory.model_validate_json(
                self.inventory_path.read_text(encoding="utf-8")
            )
        except Exception:
            return AudioModelInventory()

    def _save_inventory(self, inventory: AudioModelInventory) -> None:
        self.inventory_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.inventory_path, inventory.model_dump(mode="json"))

    def _record_install(
        self,
        descriptor: AudioModelDescriptor,
        *,
        accept_license: bool,
        revision: str = "",
    ) -> None:
        inventory = self._load_inventory()
        existing = next(
            (item for item in inventory.records if item.plugin_id == descriptor.plugin_id),
            None,
        )
        inventory.records = [
            item for item in inventory.records if item.plugin_id != descriptor.plugin_id
        ]
        inventory.records.append(
            AudioModelInventoryRecord(
                plugin_id=descriptor.plugin_id,
                model_id=descriptor.model_id,
                revision=revision
                or (existing.revision if existing is not None else descriptor.revision),
                local_path=str(self._model_dir(descriptor)),
                license_accepted_at=(
                    datetime.now(timezone.utc)
                    if accept_license
                    else existing.license_accepted_at
                    if existing is not None
                    else None
                ),
            )
        )
        self._save_inventory(inventory)

    def _inventory_record(self, plugin_id: str) -> AudioModelInventoryRecord | None:
        return next(
            (item for item in self._load_inventory().records if item.plugin_id == plugin_id),
            None,
        )

    def _license_accepted(self, plugin_id: str) -> bool:
        return any(
            item.plugin_id == plugin_id and item.license_accepted_at is not None
            for item in self._load_inventory().records
        )
