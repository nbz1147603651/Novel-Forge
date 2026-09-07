"""Async coordinator joining application files with isolated sidecar state."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.tts.model_center.compatibility import check_runtime_compatibility
from novel_forge.tts.model_center.manager import (
    ApplicationAudioModelManager,
    AudioModelDownloadCancelled,
)
from novel_forge.tts.model_center.migration import ModelRepositoryMigrator
from novel_forge.tts.model_center.runtimes import AudioRuntimeManager, audio_runtime_catalog
from novel_forge.tts.model_center.schemas import (
    AudioModelInstallState,
    AudioModelSource,
    AudioModelStatus,
    AudioRuntimeDescriptor,
    AudioRuntimeState,
    RuntimeInstallState,
)
from novel_forge.tts.platform.sidecar import AudioSidecarClient


class AudioModelCenterService:
    _SIDECAR_PROGRESS_POLL_S = 0.5
    _SIDECAR_STALL_NOTICE_S = 10.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.manager = ApplicationAudioModelManager(settings)
        memory_budget = str(settings.audio_memory_budget or "medium").strip().lower()
        global_budget = str(settings.local_model_resource_budget or "medium").strip().lower()
        budget_rank = {"light": 0, "medium": 1, "high": 2}
        effective_budget = min(
            (memory_budget, global_budget),
            key=lambda value: budget_rank.get(value, 1),
        )
        resident_limit = 2 if effective_budget == "high" else 1
        idle_unload_s = {"light": 15, "medium": 60, "high": 300}.get(
            effective_budget,
            60,
        )
        self._runtime_environment_overrides = {
            "QWEN3_TTS_API_KEY": settings.tts_qwen3_api_key,
            "QWEN3_TTS_FORMAL_MODEL": settings.tts_qwen3_formal_model,
            "QWEN3_TTS_DESIGN_MODEL": settings.tts_qwen3_design_model,
            "QWEN3_TTS_CLONE_MODEL": settings.tts_qwen3_clone_model,
            "QWEN3_TTS_MAX_RESIDENT_MODELS": str(resident_limit),
            "QWEN3_TTS_IDLE_UNLOAD_S": str(idle_unload_s),
        }
        self.runtime_manager = AudioRuntimeManager(
            self.manager.root,
            environment_overrides=self._runtime_environment_overrides,
        )
        self._live_runtime_health: dict[str, bool] = {}

    async def list_models(self) -> list[AudioModelStatus]:
        statuses = await asyncio.to_thread(self.manager.list_models)
        endpoint_cache: dict[str, tuple[bool, str, list[dict[str, Any]]]] = {}
        for status in statuses:
            setting_name = status.descriptor.endpoint_setting
            if not setting_name:
                continue
            endpoint = str(getattr(self.settings, setting_name, "") or "").strip()
            if not endpoint:
                continue
            if endpoint not in endpoint_cache:
                endpoint_cache[endpoint] = await self._probe_endpoint(
                    endpoint,
                    api_key=self._api_key_for(endpoint_setting=setting_name),
                )
            healthy, version, remote_models = endpoint_cache[endpoint]
            status.runtime_healthy = healthy
            status.runtime_version = version
            remote_match = next(
                (
                    item
                    for item in remote_models
                    if str(item.get("plugin_id") or "") == status.descriptor.plugin_id
                    or str(item.get("model_id") or "") == status.descriptor.model_id
                ),
                None,
            )
            if (
                status.descriptor.source == AudioModelSource.SIDECAR
                and remote_match
                and bool(remote_match.get("installed"))
            ):
                status.state = AudioModelInstallState.INSTALLED
                status.local_path = str(remote_match.get("local_path") or "")
                status.installed_size_bytes = int(remote_match.get("size_bytes") or 0)
            runtime_state = None
            if status.descriptor.runtime_id:
                self._live_runtime_health[status.descriptor.runtime_id] = healthy
                runtime_state = self.runtime_manager.status(status.descriptor.runtime_id)
                if healthy and version:
                    runtime_state = AudioRuntimeState(
                        runtime_id=status.descriptor.runtime_id,
                        version=version,
                        state=RuntimeInstallState.RUNNING,
                    )
            compatibility = check_runtime_compatibility(status.descriptor, runtime_state)
            status.compatible = compatibility.compatible
            status.compatibility_reason = compatibility.reason
            if not compatibility.compatible:
                status.detail = " ".join(filter(None, [status.detail, compatibility.reason]))
        return statuses

    async def install(
        self,
        plugin_id: str,
        *,
        token: str = "",
        accept_license: bool = False,
        progress: Any = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AudioModelStatus:
        descriptor = self.manager.descriptor(plugin_id)
        if descriptor.source == AudioModelSource.SIDECAR:
            emit = progress or (lambda _message, _percent: None)
            if should_cancel is not None and should_cancel():
                raise AudioModelDownloadCancelled("下载已取消。")
            client = self._client_for(descriptor.endpoint_setting, timeout_s=3600)
            try:
                emit("正在请求 Sidecar 下载模型…", -1)
                install_task = asyncio.create_task(
                    client.install_model(
                        plugin_id=descriptor.plugin_id,
                        model_id=descriptor.model_id,
                        revision=descriptor.revision,
                        accept_license=accept_license,
                    )
                )
                loop = asyncio.get_running_loop()
                model_path = self.manager.plugins_root / descriptor.plugin_id / "main"
                next_progress_at = 0.0
                last_size = -1
                last_change_at = loop.time()
                while not install_task.done():
                    if should_cancel is not None and should_cancel():
                        emit("正在终止 Sidecar 下载进程…", -1)
                        install_task.cancel()
                        await asyncio.gather(install_task, return_exceptions=True)
                        stopped, restarted = await asyncio.to_thread(
                            self._restart_managed_sidecar_after_cancel,
                            descriptor.runtime_id,
                        )
                        if restarted:
                            detail = "Sidecar 已终止并重启，已保留可续传文件。"
                        elif stopped:
                            detail = "Sidecar 已终止，但自动重启失败；可在运行时卡片中重新启动。"
                        else:
                            detail = (
                                "Sidecar 不由当前应用托管，已停止等待；"
                                "外部 Sidecar 下载可能仍在继续。"
                            )
                        raise AudioModelDownloadCancelled(f"下载已取消。{detail}")
                    now = loop.time()
                    if now >= next_progress_at:
                        size = await asyncio.to_thread(
                            self._directory_regular_file_size, model_path
                        )
                        if size != last_size:
                            last_size = size
                            last_change_at = now
                        emit(
                            self._sidecar_progress_message(
                                size,
                                descriptor.estimated_download_bytes,
                                stalled_for_s=now - last_change_at,
                            ),
                            self._sidecar_progress_percent(
                                size,
                                descriptor.estimated_download_bytes,
                            ),
                        )
                        next_progress_at = now + self._SIDECAR_PROGRESS_POLL_S
                    await asyncio.wait({install_task}, timeout=0.1)
                await install_task
                emit("Sidecar 已完成下载并加载模型。", 100)
            finally:
                await client.aclose()
            return next(
                item for item in await self.list_models() if item.descriptor.plugin_id == plugin_id
            )
        status = await asyncio.to_thread(
            self.manager.install,
            plugin_id,
            token=token,
            accept_license=accept_license,
            progress=progress,
            should_cancel=should_cancel,
        )
        if descriptor.endpoint_setting and status.local_path:
            try:
                client = self._client_for(descriptor.endpoint_setting)
                try:
                    await client.install_model(
                        plugin_id=descriptor.plugin_id,
                        model_id=descriptor.model_id,
                        revision=descriptor.revision,
                        local_path=status.local_path,
                        accept_license=accept_license,
                    )
                finally:
                    await client.aclose()
            except Exception:
                # Weight installation remains valid and auditable even when an
                # independently managed sidecar is currently offline.  The UI
                # reports runtime_healthy=False and allows a later self-test.
                pass
        return status

    @staticmethod
    def _directory_regular_file_size(path: Path) -> int:
        """Measure physical cache bytes without double-counting snapshot symlinks."""

        total = 0
        try:
            for item in path.rglob("*"):
                try:
                    if not item.is_symlink() and item.is_file():
                        total += item.stat().st_size
                except OSError:
                    continue
        except OSError:
            return total
        return total

    @staticmethod
    def _format_download_size(size_bytes: int) -> str:
        value = float(max(0, size_bytes))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1000 or unit == "GB":
                return f"{value:.2f} {unit}" if unit == "GB" else f"{value:.1f} {unit}"
            value /= 1000
        return f"{value:.2f} GB"

    @staticmethod
    def _sidecar_progress_percent(size_bytes: int, estimated_bytes: int) -> int:
        if size_bytes <= 0 or estimated_bytes <= 0:
            return -1
        return min(99, max(1, int(size_bytes * 100 / estimated_bytes)))

    def _sidecar_progress_message(
        self,
        size_bytes: int,
        estimated_bytes: int,
        *,
        stalled_for_s: float,
    ) -> str:
        if size_bytes <= 0:
            return "Sidecar 已接收任务，正在等待首个模型文件…"
        written = self._format_download_size(size_bytes)
        expected = self._format_download_size(estimated_bytes) if estimated_bytes > 0 else "未知"
        percent = self._sidecar_progress_percent(size_bytes, estimated_bytes)
        if stalled_for_s >= self._SIDECAR_STALL_NOTICE_S:
            if percent >= 95:
                return f"模型文件已写入约 {written}，Sidecar 正在加载与校验…"
            return f"已写入约 {written} / {expected}，正在等待下载端继续传输…"
        return f"Sidecar 下载中（磁盘估算）{written} / {expected}"

    def _restart_managed_sidecar_after_cancel(self, runtime_id: str) -> tuple[bool, bool]:
        if not runtime_id:
            return False, False
        state = self.runtime_manager.status(runtime_id)
        if not state.pid:
            return False, False
        try:
            self.runtime_manager.stop(runtime_id)
        except Exception:
            return False, False
        try:
            self.runtime_manager.start(runtime_id)
        except Exception:
            return True, False
        return True, True

    async def accept_license(self, plugin_id: str) -> AudioModelStatus:
        return await asyncio.to_thread(self.manager.record_license_acceptance, plugin_id)

    async def delete(self, plugin_id: str, *, force: bool = False) -> None:
        descriptor = self.manager.descriptor(plugin_id)
        if descriptor.source == AudioModelSource.SIDECAR:
            client = self._client_for(descriptor.endpoint_setting)
            try:
                await client.delete_model(plugin_id=plugin_id, model_id=descriptor.model_id)
            finally:
                await client.aclose()
            return
        await asyncio.to_thread(self.manager.delete, plugin_id, force=force)
        if descriptor.runtime_id and descriptor.endpoint_setting:
            try:
                client = self._client_for(descriptor.endpoint_setting)
                try:
                    await client.delete_model(
                        plugin_id=descriptor.plugin_id,
                        model_id=descriptor.model_id,
                    )
                finally:
                    await client.aclose()
            except Exception:
                # Local deletion is authoritative.  Sidecars reject stale
                # paths and the next successful start reconciles the registry.
                pass

    async def self_test(self, plugin_id: str) -> dict[str, Any]:
        descriptor = self.manager.descriptor(plugin_id)
        if not descriptor.endpoint_setting:
            status = await asyncio.to_thread(self.manager.model_status, plugin_id)
            return {"ok": status.state == AudioModelInstallState.INSTALLED, "detail": status.detail}
        client = self._client_for(descriptor.endpoint_setting)
        try:
            model_text = f"{descriptor.model_id} {descriptor.family}".lower()
            if "1.7b" in model_text or "whisper" in model_text or "stable" in model_text:
                memory_class = LocalMemoryClass.HIGH
            elif "int8" in model_text or "silero" in model_text:
                memory_class = LocalMemoryClass.LIGHT
            else:
                memory_class = LocalMemoryClass.MEDIUM
            cpu_only = "sherpa" in model_text or "silero" in model_text
            broker = configure_local_model_resources(self.settings)
            async with broker.lease(
                LocalResourceRequest(
                    workload="model_self_test",
                    label=f"{descriptor.display_name} · 自检",
                    memory_class=memory_class,
                    accelerator=not cpu_only,
                    cpu_heavy=cpu_only,
                    priority=LocalResourcePriority.MAINTENANCE,
                    timeout_s=float(self.settings.local_model_resource_wait_timeout_s),
                )
            ):
                return await client.self_test(model_id=descriptor.model_id)
        finally:
            await client.aclose()

    async def list_runtimes(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for descriptor in audio_runtime_catalog():
            runtime_id = descriptor.runtime_id
            state = await asyncio.to_thread(self.runtime_manager.status, runtime_id)
            live_healthy = self._live_runtime_health.get(runtime_id) is True
            if state.environment_path and not live_healthy:
                live_healthy = await self._managed_runtime_health(descriptor)
            if live_healthy:
                # ``list_models`` probes through the same async HTTP stack used
                # by real audio work.  Treat that successful response as
                # authoritative so a restricted synchronous probe cannot turn
                # normal 200 access logs into a false crash loop.
                state = await asyncio.to_thread(
                    self.runtime_manager.record_healthy,
                    runtime_id,
                )
            elif state.should_run and state.state != RuntimeInstallState.RUNNING:
                state = await asyncio.to_thread(self.runtime_manager.reconcile, runtime_id)
            payloads.append(
                {
                    "descriptor": descriptor.model_dump(mode="json"),
                    "state": state.model_dump(mode="json"),
                }
            )
        return payloads

    async def ensure_runtime_ready(self, runtime_id: str) -> AudioRuntimeState:
        """Start an installed managed runtime for an explicit audio operation.

        Listing the model center remains observational apart from crash
        recovery.  Preview, voice design and formal synthesis call this method
        deliberately, so a stopped but installed sidecar is usable without a
        separate trip through the settings screen.
        """

        state = await asyncio.to_thread(self.runtime_manager.status, runtime_id)
        if state.state == RuntimeInstallState.RUNNING:
            return state
        if not state.environment_path:
            return state
        descriptor = self.runtime_manager.descriptor(runtime_id)
        if await self._managed_runtime_health(descriptor):
            return await asyncio.to_thread(self.runtime_manager.record_healthy, runtime_id)
        return await asyncio.to_thread(self.runtime_manager.start, runtime_id)

    async def ensure_runtimes_for_plugins(
        self,
        plugin_ids: Iterable[str],
    ) -> dict[str, AudioRuntimeState]:
        """Start installed managed runtimes required by concrete plan plugins."""

        requested = {str(item).strip() for item in plugin_ids if str(item).strip()}
        states: dict[str, AudioRuntimeState] = {}
        for descriptor in audio_runtime_catalog():
            if not requested.intersection(descriptor.supported_plugins):
                continue
            states[descriptor.runtime_id] = await self.ensure_runtime_ready(descriptor.runtime_id)
        return states

    async def repository_status(
        self,
        model_statuses: list[AudioModelStatus] | None = None,
    ) -> dict[str, Any]:
        def inspect() -> dict[str, Any]:
            root = self.manager.root
            root.mkdir(parents=True, exist_ok=True)
            migration = ModelRepositoryMigrator(root / "lifecycle").latest_committed()
            statuses = model_statuses if model_statuses is not None else self.manager.list_models()
            return {
                "root": str(root),
                "size_bytes": sum(item.installed_size_bytes for item in statuses),
                "free_bytes": shutil.disk_usage(root).free,
                "rollback_available": migration is not None,
                "rollback_migration_id": migration.migration_id if migration is not None else "",
            }

        return await asyncio.to_thread(inspect)

    async def runtime_operation(
        self,
        runtime_id: str,
        operation: str,
        *,
        progress: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        operations = {
            "stop_runtime": self.runtime_manager.stop,
            "rollback_runtime": self.runtime_manager.rollback,
            "reconcile_runtime": self.runtime_manager.reconcile,
        }
        emit = progress or (lambda _message, _percent: None)
        if operation in {"install_runtime", "upgrade_runtime"}:
            state = await asyncio.to_thread(
                self.runtime_manager.install,
                runtime_id,
                progress=emit,
            )
        elif operation == "start_runtime":
            state = await asyncio.to_thread(
                self.runtime_manager.start,
                runtime_id,
                progress=emit,
            )
        else:
            handler = operations.get(operation)
            if handler is None:
                raise ValueError(f"未知运行时操作：{operation}")
            emit("正在执行运行时操作…", 20)
            state = await asyncio.to_thread(handler, runtime_id)
        if state.state == RuntimeInstallState.RUNNING:
            await self._sync_local_models(runtime_id)
        emit("运行时操作已完成。", 100)
        return state.model_dump(mode="json")

    async def migrate_repository(self, target_root: str) -> dict[str, Any]:
        migrator = ModelRepositoryMigrator(self.manager.root / "lifecycle")
        resume_runtime_ids = await asyncio.to_thread(
            self.runtime_manager.quiesce_for_repository_move
        )
        try:
            migration = await asyncio.to_thread(
                migrator.migrate,
                self.manager.root,
                Path(target_root).expanduser(),
                resume_runtime_ids=resume_runtime_ids,
            )
        except Exception:
            await asyncio.to_thread(
                self.runtime_manager.restart_requested,
                resume_runtime_ids,
            )
            raise
        target_manager = AudioRuntimeManager(
            Path(migration.target_root),
            environment_overrides=self._runtime_environment_overrides,
        )
        await asyncio.to_thread(target_manager.mark_for_restart, resume_runtime_ids)
        return migration.model_dump(mode="json")

    async def rollback_repository(self) -> dict[str, Any]:
        migrator = ModelRepositoryMigrator(self.manager.root / "lifecycle")
        latest = await asyncio.to_thread(migrator.latest_committed)
        if latest is None:
            raise RuntimeError("当前模型库没有可回滚的迁移记录。")
        resume_runtime_ids = await asyncio.to_thread(
            self.runtime_manager.quiesce_for_repository_move
        )
        try:
            migration = await asyncio.to_thread(migrator.rollback, latest.migration_id)
        except Exception:
            await asyncio.to_thread(
                self.runtime_manager.restart_requested,
                resume_runtime_ids,
            )
            raise
        source_manager = AudioRuntimeManager(
            Path(migration.source_root),
            environment_overrides=self._runtime_environment_overrides,
        )
        await asyncio.to_thread(source_manager.mark_for_restart, resume_runtime_ids)
        migration.resume_runtime_ids = list(resume_runtime_ids)
        return migration.model_dump(mode="json")

    async def _probe_endpoint(
        self,
        endpoint: str,
        *,
        api_key: str = "",
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        client = AudioSidecarClient(
            endpoint,
            api_key=api_key,
            timeout_s=5,
            connect_timeout_s=1,
        )
        try:
            health, version, models = await asyncio.gather(
                client.health(), client.version(), client.list_models()
            )
            raw_models = models.get("models", [])
            return (
                bool(health.get("ok", health.get("healthy", True))),
                str(version.get("version") or version.get("runtime") or ""),
                [item for item in raw_models if isinstance(item, dict)],
            )
        except Exception:
            return False, "", []
        finally:
            await client.aclose()

    async def _managed_runtime_health(self, descriptor: AudioRuntimeDescriptor) -> bool:
        api_key = (
            str(self.settings.tts_qwen3_api_key or "").strip()
            if descriptor.runtime_id == "qwen3-tts"
            else ""
        )
        client = AudioSidecarClient(
            descriptor.endpoint,
            api_key=api_key,
            timeout_s=3,
            connect_timeout_s=1,
        )
        try:
            payload = await client.health()
            if payload.get("ok") is False or payload.get("healthy") is False:
                return False
            return True
        except Exception:
            return False
        finally:
            await client.aclose()

    async def _sync_local_models(self, runtime_id: str) -> None:
        statuses = await asyncio.to_thread(self.manager.list_models)
        for status in statuses:
            descriptor = status.descriptor
            if (
                descriptor.runtime_id != runtime_id
                or descriptor.source == AudioModelSource.SIDECAR
                or status.state != AudioModelInstallState.INSTALLED
                or not descriptor.endpoint_setting
                or not status.local_path
            ):
                continue
            client = self._client_for(descriptor.endpoint_setting)
            try:
                await client.install_model(
                    plugin_id=descriptor.plugin_id,
                    model_id=descriptor.model_id,
                    revision=descriptor.revision,
                    local_path=status.local_path,
                )
            finally:
                await client.aclose()

    def _client_for(
        self,
        endpoint_setting: str,
        *,
        timeout_s: float = 180,
    ) -> AudioSidecarClient:
        endpoint = str(getattr(self.settings, endpoint_setting, "") or "").strip()
        if not endpoint:
            raise RuntimeError(f"未配置 sidecar 地址：{endpoint_setting}")
        api_key = self._api_key_for(endpoint_setting=endpoint_setting)
        return AudioSidecarClient(
            endpoint,
            api_key=api_key,
            timeout_s=timeout_s,
            connect_timeout_s=2,
        )

    def _api_key_for(self, *, endpoint_setting: str) -> str:
        api_key_setting = {
            "tts_qwen3_base_url": "tts_qwen3_api_key",
            "audio_qwen3_asr_base_url": "audio_qwen3_asr_api_key",
        }.get(endpoint_setting, "")
        return str(getattr(self.settings, api_key_setting, "") or "") if api_key_setting else ""
