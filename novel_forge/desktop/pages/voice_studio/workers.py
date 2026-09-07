"""TTS Workers for Desktop UI.

Provides BaseJobWorker subclasses for TTS operations that run
in background threads with Qt signal progress reporting.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal

from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.platform.benchmark import (
    benchmark_samples_from_chapter,
    run_audio_benchmark,
    save_audio_benchmark_report,
)
from novel_forge.tts.platform.config import registry_from_settings
from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript, NarratorVoiceProfile
from novel_forge.tts.services.automation import AudioAutomationMode
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError
from novel_forge.tts.services.voice_preview import (
    build_preview_plan,
    confirm_preview,
    generate_candidate_previews,
)
from novel_forge.workspace.tts_ops.execution import (
    execute_accept_segment_take,
    execute_approve_character_voice,
    execute_build_narrator_profile,
    execute_build_voice_team,
    execute_clone_character_voice,
    execute_confirm_voice_team,
    execute_design_character_voice,
    execute_export_audio_delivery,
    execute_export_audiobook_delivery,
    execute_full_tts_pipeline,
    execute_generate_dubbing_script,
    execute_list_tts_voices,
    execute_prepare_voice_team_previews,
    execute_preview_character_voice,
    execute_preview_narrator_voice,
    execute_reassemble_chapter_audio,
    execute_synthesize_chapter,
    execute_synthesize_segment,
)

logger = logging.getLogger(__name__)


def _tts_error_detail(exc: BaseException) -> str:
    """Prefer framework messages without appending internal context dictionaries."""
    framework_message = str(getattr(exc, "message", "") or "").strip()
    return framework_message or str(exc).strip() or type(exc).__name__


class TTSWorkerSignals(BaseJobWorkerSignals):
    """Signals for TTS workers."""

    # Progress signals
    step_progress = Signal(str, dict)  # (step_name, data)
    voice_team_updated = Signal(dict)  # voice team data
    narrator_profile_updated = Signal(dict)  # persisted LLM narrator voice profile
    script_updated = Signal(dict)  # script data
    synthesis_progress = Signal(int, int)  # (completed, total)
    segment_progress = Signal(int, str)  # (segment_idx, status_str)
    segment_audio_completed = Signal(dict)  # one approved audition take
    audio_completed = Signal(dict)  # audio result data
    voices_listed = Signal(list)  # list of voice dicts [{voice_id, name, gender, tags}]
    provider_status = Signal(dict)  # provider capabilities and catalog summary
    sound_library_updated = Signal(dict)  # project-level generated/reviewed asset summary
    audio_benchmark_completed = Signal(dict)  # project-local model scorecards


class VoicePreviewWorkerSignals(BaseJobWorkerSignals):
    """Signals for multi-candidate voice A/B preview generation and confirmation."""

    # 一个角色的完整预览计划（candidates 含 sample_path / error）
    preview_plan_ready = Signal(dict)
    # 候选合成进度 (done, total)
    preview_progress = Signal(int, int)
    # 流式合成时当前候选已接收字节数（用于“边生成边播放”的进度反馈）
    preview_chunk = Signal(int)
    # 确认选中后更新的 voice team
    voice_team_updated = Signal(dict)


class StableAudioModelWorkerSignals(BaseJobWorkerSignals):
    """Signals for user-managed Stable Audio model cache operations."""

    listed = Signal(bool, str, object, object)
    download_progress = Signal(str, int)
    download_finished = Signal(bool, str, object)
    delete_finished = Signal(bool, str)
    migration_finished = Signal(bool, str)


class AudioModelCenterWorkerSignals(BaseJobWorkerSignals):
    models_listed = Signal(list)
    runtimes_listed = Signal(list)
    repository_status = Signal(dict)
    operation_progress = Signal(str, int)
    runtime_progress = Signal(str, str, int)
    operation_finished = Signal(bool, str, dict)


class AudioModelCenterWorker(BaseJobWorker):
    """Run application-level model inventory and lifecycle operations off the UI thread."""

    signals_cls = AudioModelCenterWorkerSignals
    pool = "aux"

    def __init__(
        self,
        *,
        settings: Any,
        operation: str,
        plugin_id: str = "",
        token: str = "",
        accept_license: bool = False,
        force: bool = False,
        runtime_id: str = "",
        target_root: str = "",
    ) -> None:
        super().__init__()
        self._settings = settings
        self._operation = operation
        self._plugin_id = plugin_id
        self._token = token
        self._accept_license = accept_license
        self._force = force
        self._runtime_id = runtime_id
        self._target_root = target_root

    async def _run_async(self) -> None:
        from novel_forge.tts.model_center.manager import AudioModelDownloadCancelled
        from novel_forge.tts.model_center.service import AudioModelCenterService

        service = AudioModelCenterService(self._settings)
        if self._operation == "list":
            statuses = await service.list_models()
            self.signals.models_listed.emit([item.model_dump(mode="json") for item in statuses])
            self.signals.runtimes_listed.emit(await service.list_runtimes())
            self.signals.repository_status.emit(await service.repository_status(statuses))
            return
        try:
            if self._operation == "install":
                status = await service.install(
                    self._plugin_id,
                    token=self._token,
                    accept_license=self._accept_license,
                    progress=lambda message, percent: self.signals.operation_progress.emit(
                        message, percent
                    ),
                    should_cancel=self._cancel_event.is_set,
                )
                payload = status.model_dump(mode="json")
                message = f"已安装 {status.descriptor.display_name}"
            elif self._operation == "accept_license":
                status = await service.accept_license(self._plugin_id)
                payload = status.model_dump(mode="json")
                message = f"已记录 {status.descriptor.display_name} 的条款确认"
            elif self._operation == "delete":
                await service.delete(self._plugin_id, force=self._force)
                payload = {"plugin_id": self._plugin_id}
                message = "模型已删除"
            elif self._operation == "self_test":
                payload = await service.self_test(self._plugin_id)
                message = "自检通过" if payload.get("ok") else "自检未通过"
            elif self._operation.endswith("_runtime"):
                payload = await service.runtime_operation(
                    self._runtime_id,
                    self._operation,
                    progress=lambda message, percent: self.signals.runtime_progress.emit(
                        self._runtime_id, message, percent
                    ),
                )
                state = str(payload.get("state") or "")
                message = f"{self._runtime_id}：{state or '运行时操作已完成'}"
            elif self._operation == "migrate_repository":
                payload = await service.migrate_repository(self._target_root)
                message = "模型库已校验并迁移"
            elif self._operation == "rollback_repository":
                payload = await service.rollback_repository()
                message = "已回滚到原模型库"
            else:
                raise ValueError(f"未知模型中心操作：{self._operation}")
            self.signals.operation_finished.emit(True, message, payload)
        except AudioModelDownloadCancelled as exc:
            self.signals.operation_finished.emit(
                False,
                str(exc).strip() or "下载已取消。",
                {"plugin_id": self._plugin_id, "cancelled": True},
            )
        except Exception as exc:
            self.signals.operation_finished.emit(
                False,
                str(exc).strip() or type(exc).__name__,
                {"plugin_id": self._plugin_id},
            )

    def _on_cancel_requested(self) -> None:
        if self._operation == "install":
            # Downloads run in a worker thread.  Let their cooperative
            # cancellation callback stop the transfer at a safe boundary
            # instead of merely cancelling the awaiting coroutine.
            return
        super()._on_cancel_requested()


class StableAudioModelWorker(BaseJobWorker):
    """Manage the Stable Audio Hugging Face cache without loading TTS adapters."""

    signals_cls = StableAudioModelWorkerSignals
    pool = "aux"

    def __init__(
        self,
        *,
        models_dir: str,
        command: str,
        operation: str,
        model_id: str = "",
        token: str = "",
        source_dir: str = "",
    ) -> None:
        super().__init__()
        self._models_dir = models_dir
        self._command = command
        self._operation = operation
        self._model_id = model_id.strip()
        self._token = token
        self._source_dir = source_dir.strip()
        self._running = False
        self._finished = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._finished = False
        self.submit()

    def isRunning(self) -> bool:  # noqa: N802 - preserve Qt worker naming
        return self._running and not self._finished

    async def _run_async(self) -> None:
        import asyncio

        try:
            await asyncio.to_thread(self._run_sync)
        except asyncio.CancelledError:
            raise
        finally:
            self._finished = True
            self._running = False

    def _run_sync(self) -> None:
        from novel_forge.tts.sound_generation.model_manager import StableAudioModelManager

        manager = StableAudioModelManager(models_dir=self._models_dir, command=self._command)
        signals = self.signals
        try:
            if self._operation == "list":
                signals.listed.emit(
                    True, "已刷新本地模型状态。", manager.list_models(), manager.runtime_status()
                )
            elif self._operation == "download":
                if not self._model_id:
                    raise ValueError("模型 ID 为空")
                status = manager.download(
                    self._model_id,
                    token=self._token,
                    progress=lambda message, percent: signals.download_progress.emit(
                        message, percent
                    ),
                )
                signals.download_finished.emit(
                    True, f"已下载 {status.descriptor.display_name}", status
                )
            elif self._operation == "delete":
                if not self._model_id:
                    raise ValueError("模型 ID 为空")
                manager.delete(self._model_id)
                signals.delete_finished.emit(True, f"已删除 {self._model_id} 的受管缓存。")
            elif self._operation == "migrate":
                source = Path(self._source_dir).expanduser()
                if not source.is_dir():
                    raise ValueError("旧项目模型缓存目录不存在")
                destination = manager.ensure_models_dir()
                shutil.copytree(source, destination, dirs_exist_ok=True)
                signals.migration_finished.emit(
                    True,
                    f"已复制旧项目模型缓存到应用模型库：{destination}",
                )
            else:
                raise ValueError(f"未知 Stable Audio 操作: {self._operation}")
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            if self._operation == "list":
                signals.listed.emit(False, message, [], manager.runtime_status())
            elif self._operation == "download":
                signals.download_finished.emit(False, message, None)
            elif self._operation == "delete":
                signals.delete_finished.emit(False, message)
            elif self._operation == "migrate":
                signals.migration_finished.emit(False, message)


class TTSJobWorker(BaseJobWorker):
    """Base worker that owns TTS adapters for one private event loop."""

    def __init__(self, *, settings: Any, mock: bool = False) -> None:
        super().__init__(mock=mock)
        self._tts_registry = TTSAdapterRegistry.get_instance(settings)

    async def _cleanup_async_resources(self) -> None:
        await self._tts_registry.shutdown_current_loop()


def _audio_preflight_failure_detail(result: dict[str, Any]) -> str:
    """Turn structured preflight evidence into an actionable UI explanation.

    Preflight failures are grouped by ``severity``: hard failures (blocking)
    are listed first with their remediation hint; soft failures (alignment
    sidecars that degrade gracefully) are shown separately as non-blocking.
    """

    # Prefer the enriched ``failed_checks`` (with severity/guidance) when the
    # backend provided them; fall back to filtering ``checks`` by status.
    failed_checks = result.get("failed_checks")
    if not isinstance(failed_checks, list) or not failed_checks:
        failed_checks = [
            item
            for item in result.get("checks", [])
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "failed"
        ]
    if not failed_checks:
        return (
            "合成尚未发起，因此没有可续跑的片段。"
            "请在“平台设置”检查当前平台的凭据、路由和本地运行时后重试。"
        )

    stage_labels = {
        "tts_formal": "正式人声合成",
        "asr": "语音识别校验",
        "align": "时间轴对齐",
        "vad": "静音检测",
        "music": "BGM 生成",
        "soundscape": "环境声生成",
        "sfx": "音效生成",
        "renderer": "音频渲染",
        "quality": "音频质检",
    }

    def _format_item(item: dict[str, Any]) -> str:
        stage = str(item.get("stage") or "").strip()
        plugin_id = str(item.get("plugin_id") or "").strip()
        scope = stage_labels.get(stage, stage or "音频路由")
        if plugin_id:
            scope = f"{scope} / {plugin_id}"
        message = str(item.get("message") or "未通过生产预检").strip()
        guidance = str(item.get("guidance") or "").strip()
        line = f"• {scope}：{message}"
        if guidance:
            line += f"\n  -> {guidance}"
        return line

    hard_failures = [
        item for item in failed_checks if str(item.get("severity") or "hard") == "hard"
    ]
    soft_failures = [item for item in failed_checks if item.get("severity") == "soft"]

    lines = [
        "合成尚未发起，因此没有可续跑的片段。",
    ]
    if hard_failures:
        lines.append("以下为阻断性预检项，必须修复后才能合成：")
        for item in hard_failures[:8]:
            lines.append(_format_item(item))
        remaining = len(hard_failures) - 8
        if remaining > 0:
            lines.append(f"• 另有 {remaining} 项阻断性预检未通过，请在平台设置中查看完整预检。")
    if soft_failures:
        lines.append("")
        lines.append("以下为非阻断性预检项（可忽略，但建议修复以提升质量）：")
        for item in soft_failures[:4]:
            lines.append(_format_item(item))
        remaining = len(soft_failures) - 4
        if remaining > 0:
            lines.append(f"• 另有 {remaining} 项非阻断性预检，可在平台设置中查看。")
    return "\n".join(lines)


def _synthesis_failure_payload(
    result: dict[str, Any],
    *,
    step: str,
) -> dict[str, Any]:
    """Preserve resumable segment failures without mislabeling preflight blocks."""

    if result.get("error_code") == "audio_preflight_failed":
        detail = _audio_preflight_failure_detail(result)
        return {
            "summary": "合成尚未开始：音频生产预检未通过",
            "message": detail,
            "detail": detail,
            "error_code": "audio_preflight_failed",
            "step": step,
        }

    segment_results = result.get("segment_results", [])
    completed = sum(
        1
        for item in segment_results
        if isinstance(item, dict) and item.get("status") == "completed"
    )
    total = len(segment_results)
    failures = [
        {
            "segment_index": int(item.get("segment_index", -1)),
            "error": str(item.get("error_message") or "未知错误"),
            "retry_count": int(item.get("retry_count", 0) or 0),
        }
        for item in segment_results
        if isinstance(item, dict) and item.get("status") == "failed"
    ]

    # When all segments succeeded but the quality gate blocked completion,
    # report a distinct "quality blocked" state instead of the misleading
    # "synthesis incomplete / can resume" message.
    if not failures and total > 0 and completed == total:
        blocking_reasons: list[str] = result.get("delivery_blocking_reasons") or []
        quality_meta = (result.get("metadata") or {}).get("audio_quality") or {}
        reason_text = "、".join(blocking_reasons) if blocking_reasons else "音频质量门禁未通过"
        detail_lines = [f"全部 {total} 段语音合成已成功，但质量评估阻断了最终交付。"]
        detail_lines.append(f"阻断原因：{reason_text}")
        if quality_meta.get("passed") is False:
            if quality_meta.get("masking_risk_events"):
                detail_lines.append(
                    f"掩蔽风险事件：{', '.join(str(e) for e in quality_meta['masking_risk_events'])}"
                )
            if quality_meta.get("transition_risk_events"):
                detail_lines.append(
                    f"转场风险事件：{', '.join(str(e) for e in quality_meta['transition_risk_events'])}"
                )
        detail_lines.append("建议：调整配音脚本中的音效时间轴，或在后处理设置中放宽质量门禁。")
        detail = "\n".join(detail_lines)
        return {
            "summary": f"合成已完成（{completed}/{total} 段），质量门禁阻断交付",
            "message": detail,
            "detail": detail,
            "error_code": "quality_gate_blocked",
            "failed_segments": [],
            "step": step,
        }

    detail = str(result.get("error") or "")
    if failures:
        failure_lines = [
            f"第 {int(str(item['segment_index'])) + 1} 段（已重试 {item['retry_count']} 次）：{item['error']}"
            for item in failures[:8]
        ]
        remaining = len(failures) - len(failure_lines)
        if remaining > 0:
            failure_lines.append(f"另有 {remaining} 段失败，请在配音室查看。")
        detail = "\n".join([detail, *failure_lines]).strip()
    if not detail and total:
        detail = f"已完成 {completed}/{total} 段；请根据失败片段详情续跑。"
    return {
        "summary": f"合成未完成：{completed}/{total} 段成功，可续跑",
        "message": detail or "合成未完成，请续跑缺失片段。",
        "detail": detail,
        "failed_segments": failures,
        "step": step,
    }


class AudioBenchmarkWorker(BaseJobWorker):
    """Benchmark configured ASR/alignment sidecars on one chapter's real voice."""

    signals_cls = TTSWorkerSignals
    pool = "aux"

    def __init__(
        self,
        *,
        settings: Any,
        layout: ProjectLayout,
        chapter_number: int,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._layout = layout
        self._chapter_number = chapter_number

    async def _run_async(self) -> None:
        script_path = self._layout.tts_dubbing_script_path(self._chapter_number)
        result_path = self._layout.tts_audio_result_path(self._chapter_number)
        if not script_path.is_file() or not result_path.is_file():
            raise RuntimeError("请先完成本章正式人声合成，再运行模型基准。")
        script = DubbingScript.model_validate_json(script_path.read_text(encoding="utf-8"))
        audio_result = ChapterAudioResult.model_validate_json(
            result_path.read_text(encoding="utf-8")
        )
        samples = benchmark_samples_from_chapter(script, audio_result, limit=30)
        report = await run_audio_benchmark(
            settings=self._settings,
            samples=samples,
            registry=registry_from_settings(self._settings),
        )
        save_audio_benchmark_report(self._layout.tts_model_scorecards_path, report)
        self.signals.audio_benchmark_completed.emit(report.model_dump(mode="json"))


class BuildVoiceTeamWorker(TTSJobWorker):
    """Worker for building voice team."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        characters: list[dict[str, Any]],
        settings: Any,
        layout: ProjectLayout,
        narrator_voice_id: str = "",
        provider: str = "",
        mock: bool = False,
        rebuild_character_ids: list[str] | None = None,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._characters = characters
        self._settings = settings
        self._layout = layout
        self._narrator_voice_id = narrator_voice_id
        self._provider = provider
        self._rebuild_character_ids = rebuild_character_ids

    async def _run_async(self) -> None:
        """Run voice team building."""
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self._check_cancel()
            self.signals.step_progress.emit(step, data)

        try:
            on_step(
                "voice_team_workflow_start",
                {
                    "total": len(self._characters),
                    "provider": self._provider or str(self._settings.tts_default_provider),
                    "phases": 5,
                },
            )
            # The narrator is a first-class member of the audio cast.  Legacy
            # profiles may contain a prose-only design with no actual voice,
            # so refresh those, expired voices, and provider-mismatched voices
            # before building the character team.
            on_step("voice_team_narrator_check_start", {"phase": 1, "phases": 5})
            needs_narrator_build = True
            if self._layout.tts_narrator_profile_path.exists():
                try:
                    profile = NarratorVoiceProfile.model_validate_json(
                        self._layout.tts_narrator_profile_path.read_text(encoding="utf-8")
                    )
                    selected_provider = self._provider or str(self._settings.tts_default_provider)
                    needs_narrator_build = (
                        not profile.voice_id
                        or profile.is_expired
                        or profile.provider.value != selected_provider
                    )
                except Exception:
                    needs_narrator_build = True
            if needs_narrator_build:
                narrator_result = await execute_build_narrator_profile(
                    project_id=self._project_id,
                    settings=self._settings,
                    layout=self._layout,
                    provider=self._provider,
                    on_step_progress=on_step,
                )
                if "error" not in narrator_result.result:
                    self.signals.narrator_profile_updated.emit(narrator_result.result)
                    on_step("voice_team_narrator_ready", {"rebuilt": True})
            else:
                on_step("voice_team_narrator_ready", {"rebuilt": False})
            on_step(
                "voice_team_cast_phase_start",
                {"total": len(self._characters), "phase": 2, "phases": 5},
            )
            result = await execute_build_voice_team(
                project_id=self._project_id,
                characters=self._characters,
                settings=self._settings,
                layout=self._layout,
                narrator_voice_id=self._narrator_voice_id,
                provider=self._provider,
                on_step_progress=on_step,
                rebuild_character_ids=self._rebuild_character_ids,
            )

            if "error" not in result.result:
                preview_result = await execute_prepare_voice_team_previews(
                    project_id=self._project_id,
                    characters=self._characters,
                    settings=self._settings,
                    layout=self._layout,
                    provider=self._provider,
                    character_ids=self._rebuild_character_ids,
                    on_step_progress=on_step,
                )
                if "error" not in preview_result.result:
                    result = preview_result
                on_step(
                    "voice_team_workflow_done",
                    {"total": len(self._characters), "phase": 5, "phases": 5},
                )
                self.signals.voice_team_updated.emit(result.result)
            else:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    {"message": result.result["error"], "step": "build_voice_team"},
                )
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"构建配音团队失败: {exc}",
                    "detail": str(exc),
                    "step": "build_voice_team",
                },
            )


class VoicePreviewWorker(TTSJobWorker):
    """Worker for multi-candidate voice A/B preview.

    Flow (aligned with Reference/audiobook Phase 3):
    1. ``build_preview_plan`` — deterministic candidate selection
       (language → gender → age hard filters, personality soft match);
    2. ``generate_candidate_previews`` — synthesize every candidate with the
       same sample text; when ``streaming=True`` and the provider supports it,
       WebSocket streaming is used so chunks arrive before the full clip is
       done (``preview_chunk`` drives the UI's live feedback).
    """

    signals_cls = VoicePreviewWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        characters: list[dict[str, Any]],
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        sample_text: str = "",
        streaming: bool = True,
        candidate_count: int = 3,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._characters = characters
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._sample_text = sample_text
        self._streaming = streaming
        self._candidate_count = candidate_count
        self._chunk_bytes = 0

    async def _run_async(self) -> None:
        self._check_cancel()
        try:
            plans = await build_preview_plan(
                layout=self._layout,
                settings=self._settings,
                characters=self._characters,
                provider=self._provider,
                sample_text=self._sample_text,
                candidate_count=self._candidate_count,
            )
            if not plans:
                raise RuntimeError("没有可预览的角色（请先在角色圣经选择角色）")
            for plan in plans:
                self._check_cancel()
                self._chunk_bytes = 0
                generated = await generate_candidate_previews(
                    layout=self._layout,
                    settings=self._settings,
                    plan=plan,
                    provider=self._provider,
                    streaming=self._streaming,
                    on_progress=lambda done, total: self.signals.preview_progress.emit(
                        done, total
                    ),
                    on_chunk=self._on_chunk,
                )
                self.signals.preview_plan_ready.emit(generated.model_dump(mode="json"))
        except Exception as exc:
            detail = _tts_error_detail(exc)
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"音色候选试听生成失败: {exc}",
                    "detail": detail,
                    "step": "voice_preview",
                },
            )

    def _on_chunk(self, data: bytes) -> None:
        self._chunk_bytes += len(data)
        self.signals.preview_chunk.emit(self._chunk_bytes)


class ConfirmVoicePreviewWorker(TTSJobWorker):
    """Persist the author's A/B-selected candidate into the voice team."""

    signals_cls = VoicePreviewWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        settings: Any,
        layout: ProjectLayout,
        character_id: str,
        voice_id: str,
        speed: float = 1.0,
        volume: float = 1.0,
        provider: str = "",
        model_id: str = "",
        sample_text: str = "",
        sample_path: str = "",
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._settings = settings
        self._layout = layout
        self._character_id = character_id
        self._voice_id = voice_id
        self._speed = speed
        self._volume = volume
        self._provider = provider
        self._model_id = model_id
        self._sample_text = sample_text
        self._sample_path = sample_path

    async def _run_async(self) -> None:
        import asyncio

        self._check_cancel()
        try:
            team = await asyncio.to_thread(
                confirm_preview,
                layout=self._layout,
                settings=self._settings,
                character_id=self._character_id,
                voice_id=self._voice_id,
                speed=self._speed,
                volume=self._volume,
                provider=self._provider,
                model_id=self._model_id,
                sample_text=self._sample_text,
                sample_path=self._sample_path,
            )
            self.signals.voice_team_updated.emit(team)
        except Exception as exc:
            detail = _tts_error_detail(exc)
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"确认音色失败: {exc}",
                    "detail": detail,
                    "step": "confirm_preview",
                },
            )


class BuildNarratorProfileWorker(TTSJobWorker):
    """Worker for rebuilding the LLM-derived narrator voice design."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._settings = settings
        self._layout = layout
        self._provider = provider

    async def _run_async(self) -> None:
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)

        try:
            result = await execute_build_narrator_profile(
                project_id=self._project_id,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                on_step_progress=on_step,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.narrator_profile_updated.emit(result.result)
        except Exception as exc:
            detail = _tts_error_detail(exc)
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"旁白设计异常: {exc}",
                    "detail": detail,
                    "step": "build_narrator_profile",
                },
            )


class GenerateScriptWorker(TTSJobWorker):
    """Worker for generating dubbing script."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        chapter_text: str,
        settings: Any,
        layout: ProjectLayout,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._chapter_text = chapter_text
        self._settings = settings
        self._layout = layout

    async def _run_async(self) -> None:
        """Run script generation."""
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)

        try:
            result = await execute_generate_dubbing_script(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                chapter_text=self._chapter_text,
                settings=self._settings,
                layout=self._layout,
                on_step_progress=on_step,
            )

            if "error" not in result.result:
                self.signals.script_updated.emit(result.result)
            else:
                # Pass the full structured error so the UI can render
                # actionable diagnostics (completeness report, draft path).
                error_payload: dict[str, Any] = {
                    "message": result.result["error"],
                    "step": "generate_script",
                    "error_code": result.result.get("error_code", ""),
                }
                if "completeness_report" in result.result:
                    error_payload["completeness_report"] = result.result[
                        "completeness_report"
                    ]
                if result.result.get("draft_script_path"):
                    error_payload["draft_script_path"] = result.result[
                        "draft_script_path"
                    ]
                if result.result.get("user_options"):
                    error_payload["user_options"] = result.result["user_options"]
                self.signals.worker_failed.emit(
                    self._worker_id,
                    error_payload,
                )
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"summary": f"生成脚本失败: {exc}", "detail": str(exc), "step": "generate_script"},
            )


class SynthesizeWorker(TTSJobWorker):
    """Worker for synthesizing chapter audio."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        automation_mode: AudioAutomationMode | str | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._automation_mode = automation_mode

    async def _run_async(self) -> None:
        """Run synthesis."""
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)
            if "completed" in data and "total" in data:
                self.signals.synthesis_progress.emit(data["completed"], data["total"])

        def on_segment(segment_idx: int, status_str: str) -> None:
            self.signals.segment_progress.emit(segment_idx, status_str)

        try:
            result = await execute_synthesize_chapter(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                automation_mode=self._automation_mode,
                on_step_progress=on_step,
                on_segment_progress=on_segment,
            )

            if "error" not in result.result and bool(result.result.get("is_complete", False)):
                self.signals.audio_completed.emit(result.result)
            else:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    _synthesis_failure_payload(result.result, step="synthesize"),
                )
        except Exception as exc:
            payload: dict[str, Any] = {
                "summary": f"合成失败: {exc}",
                "detail": str(exc),
                "step": "synthesize",
            }
            if isinstance(exc, TTSDeliveryNotReadyError):
                payload["error_code"] = "tts_delivery_blocked"
                payload["blocking_reasons"] = list(exc.reasons)
                payload["chapter_number"] = exc.chapter_number
                payload["summary"] = f"章节音频交付门未通过: {', '.join(exc.reasons)}"
            self.signals.worker_failed.emit(self._worker_id, payload)


class SynthesizeSegmentWorker(TTSJobWorker):
    """Generate one script segment for the voice-room audition workflow."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        segment_index: int,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        segment_override: dict[str, Any] | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._segment_index = segment_index
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._segment_override = segment_override

    async def _run_async(self) -> None:
        self._check_cancel()
        try:
            result = await execute_synthesize_segment(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                segment_index=self._segment_index,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                segment_override=self._segment_override,
            )
            if "error" in result.result:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    {
                        "summary": f"第 {self._segment_index + 1} 段未生成",
                        "detail": str(result.result["error"]),
                        "step": "synthesize_segment",
                    },
                )
                return
            self.signals.segment_audio_completed.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"第 {self._segment_index + 1} 段生成失败: {exc}",
                    "detail": str(exc),
                    "step": "synthesize_segment",
                },
            )


class AcceptSegmentTakeWorker(TTSJobWorker):
    """Promote an audition only after the author explicitly accepts it."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        take_id: str,
        settings: Any,
        layout: ProjectLayout,
    ) -> None:
        super().__init__(settings=settings, mock=False)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._take_id = take_id
        self._settings = settings
        self._layout = layout

    async def _run_async(self) -> None:
        self._check_cancel()
        try:
            result = await execute_accept_segment_take(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                take_id=self._take_id,
                settings=self._settings,
                layout=self._layout,
            )
            if "error" in result.result:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    {
                        "summary": "试听版本未接受",
                        "detail": str(result.result["error"]),
                        "step": "accept_segment_take",
                    },
                )
                return
            self.signals.segment_audio_completed.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"接受试听失败: {exc}",
                    "detail": str(exc),
                    "step": "accept_segment_take",
                },
            )


class AssembleChapterAudioWorker(TTSJobWorker):
    """Rebuild a chapter master from already approved segment takes."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        settings: Any,
        layout: ProjectLayout,
        mock: bool = False,
        fast: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._settings = settings
        self._layout = layout
        # When True the reassemble reuses the persisted speech timeline instead
        # of re-running ASR forced alignment; used by the Voice Room
        # accept-and-continue loop, where only one segment's audio changed.
        self._fast = fast

    async def _run_async(self) -> None:
        self._check_cancel()
        try:
            result = await execute_reassemble_chapter_audio(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                settings=self._settings,
                layout=self._layout,
                fast=self._fast,
            )
            if "error" in result.result:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    {
                        "summary": "后处理未完成",
                        "detail": str(result.result["error"]),
                        "step": "assemble_audio",
                    },
                )
                return
            self.signals.audio_completed.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"后处理失败: {exc}",
                    "detail": str(exc),
                    "step": "assemble_audio",
                },
            )


class GenerateSoundPaletteWorker(TTSJobWorker):
    """Generate review-first BGM and ambience candidates for the whole novel."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        story_context: dict[str, Any],
        settings: Any,
        layout: ProjectLayout,
    ) -> None:
        super().__init__(settings=settings, mock=False)
        self._story_context = story_context
        self._settings = settings
        self._layout = layout

    async def _run_async(self) -> None:
        from novel_forge.tts.sound_generation.service import SoundGenerationService

        self._check_cancel()

        def on_progress(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)

        try:
            assets = await SoundGenerationService(
                settings=self._settings,
                layout=self._layout,
            ).generate_project_palette(
                self._story_context,
                on_progress=on_progress,
            )
            self.signals.sound_library_updated.emit(
                {
                    "generated": len(assets),
                    "asset_ids": [asset.asset_id for asset in assets],
                    "pending_review": sum(asset.approval_status == "pending" for asset in assets),
                }
            )
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": f"作品声音方案生成失败: {exc}",
                    "detail": str(exc),
                    "step": "sound_palette",
                },
            )


class FullTTSPipelineWorker(TTSJobWorker):
    """Worker for running full TTS pipeline."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        chapter_text: str,
        characters: list[dict[str, Any]],
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        automation_mode: AudioAutomationMode | str | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._chapter_text = chapter_text
        self._characters = characters
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._automation_mode = automation_mode

    async def _run_async(self) -> None:
        """Run full pipeline."""
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)
            # Publish the persisted/reused script immediately, rather than
            # leaving the Script tab on its placeholder until the master audio
            # finishes. Subsequent segment events can then show real character
            # and synthesis progress on stable script rows.
            if step in {"tts_script_persisted", "tts_script_reused"}:
                script_path = self._layout.tts_dubbing_script_path(self._chapter_number)
                if script_path.is_file():
                    try:
                        script = DubbingScript.model_validate_json(
                            script_path.read_text(encoding="utf-8")
                        )
                    except Exception:
                        logger.exception(
                            "Failed to publish intermediate dubbing script for chapter %d",
                            self._chapter_number,
                        )
                    else:
                        self.signals.script_updated.emit(script.model_dump(mode="json"))

        def on_segment(segment_idx: int, status_str: str) -> None:
            self.signals.segment_progress.emit(segment_idx, status_str)

        try:
            result = await execute_full_tts_pipeline(
                project_id=self._project_id,
                chapter_number=self._chapter_number,
                chapter_text=self._chapter_text,
                characters=self._characters,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                automation_mode=self._automation_mode,
                on_step_progress=on_step,
                on_segment_progress=on_segment,
            )

            if "error" not in result.result and bool(result.result.get("is_complete", False)):
                self.signals.audio_completed.emit(result.result)
            else:
                self.signals.worker_failed.emit(
                    self._worker_id,
                    _synthesis_failure_payload(result.result, step="full_pipeline"),
                )
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"summary": f"全流程失败: {exc}", "detail": str(exc), "step": "full_pipeline"},
            )


class PreviewVoiceWorker(TTSJobWorker):
    """Worker for previewing/auditioning a voice with sample text."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        character_id: str,
        sample_text: str,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._character_id = character_id
        self._sample_text = sample_text
        self._settings = settings
        self._layout = layout
        self._provider = provider

    async def _run_async(self) -> None:
        """Run voice preview synthesis."""
        self._check_cancel()

        try:
            result = await execute_preview_character_voice(
                project_id=self._project_id,
                character_id=self._character_id,
                sample_text=self._sample_text,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.audio_completed.emit(result.result)
        except Exception as exc:
            detail = _tts_error_detail(exc)
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": "角色试听失败",
                    "message": detail,
                    "detail": detail,
                    "step": "preview",
                },
            )


class PreviewNarratorVoiceWorker(TTSJobWorker):
    """Worker for replaying the persisted narrator audition."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._settings = settings
        self._layout = layout
        self._provider = provider

    async def _run_async(self) -> None:
        self._check_cancel()
        try:
            result = await execute_preview_narrator_voice(
                project_id=self._project_id,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.audio_completed.emit(result.result)
        except Exception as exc:
            detail = _tts_error_detail(exc)
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "summary": "旁白试听失败",
                    "message": detail,
                    "detail": detail,
                    "step": "preview_narrator",
                },
            )


class CloneVoiceWorker(TTSJobWorker):
    """Worker for cloning a voice from reference audio."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        character_id: str,
        reference_audio_path: str,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        reference_transcript: str = "",
        authorized: bool = False,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._character_id = character_id
        self._reference_audio_path = reference_audio_path
        self._settings = settings
        self._layout = layout
        self._provider = provider
        self._reference_transcript = reference_transcript
        self._authorized = authorized

    async def _run_async(self) -> None:
        """Run voice cloning."""
        self._check_cancel()

        try:
            result = await execute_clone_character_voice(
                project_id=self._project_id,
                character_id=self._character_id,
                reference_audio=self._reference_audio_path,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                reference_transcript=self._reference_transcript,
                authorized=self._authorized,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.voice_team_updated.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"message": f"克隆异常: {exc}", "step": "clone"},
            )


class DesignVoiceWorker(TTSJobWorker):
    """Worker for designing a voice from text description."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        character_id: str,
        description: str,
        settings: Any,
        layout: ProjectLayout,
        provider: str = "",
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._character_id = character_id
        self._description = description
        self._settings = settings
        self._layout = layout
        self._provider = provider

    async def _run_async(self) -> None:
        """Run voice design."""
        self._check_cancel()

        def on_step(step: str, data: dict[str, Any]) -> None:
            self.signals.step_progress.emit(step, data)

        try:
            result = await execute_design_character_voice(
                project_id=self._project_id,
                character_id=self._character_id,
                description=self._description,
                settings=self._settings,
                layout=self._layout,
                provider=self._provider,
                on_step_progress=on_step,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.voice_team_updated.emit(result.result)
            preview_path = str(result.result.get("preview_audio_path") or "")
            if preview_path:
                self.signals.audio_completed.emit(
                    {"audio_path": preview_path, "source": "voice_design"}
                )
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"message": f"设计异常: {exc}", "step": "design"},
            )


class ApproveVoiceWorker(TTSJobWorker):
    """Worker for approving a pending cast entry after audition."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        character_id: str,
        settings: Any,
        layout: ProjectLayout,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._character_id = character_id
        self._settings = settings
        self._layout = layout

    async def _run_async(self) -> None:
        """Flip approval_status pending -> approved and persist."""
        self._check_cancel()
        try:
            result = await execute_approve_character_voice(
                project_id=self._project_id,
                character_id=self._character_id,
                settings=self._settings,
                layout=self._layout,
            )
            if "error" in result.result:
                raise RuntimeError(str(result.result["error"]))
            self.signals.voice_team_updated.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"message": f"确认音色异常: {exc}", "step": "approve"},
            )


class ConfirmVoiceTeamWorker(TTSJobWorker):
    """Worker for confirming the entire voice team as auto-dubbing-ready.

    This is the project-level counterpart to ``ApproveVoiceWorker``: the latter
    approves one voice, this approves the cast as a unit so post-archive TTS
    can short-circuit the per-entry check.  Emits ``voice_team_updated`` with
    the persisted contract (``confirmed=True``) on success.
    """

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        settings: Any,
        layout: ProjectLayout,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._settings = settings
        self._layout = layout

    async def _run_async(self) -> None:
        """Set VoiceTeamContract.confirmed=True after pre-checking all entries."""
        self._check_cancel()
        try:
            result = await execute_confirm_voice_team(
                project_id=self._project_id,
                settings=self._settings,
                layout=self._layout,
            )
            if "error" in result.result:
                # Surface structured diagnoses so the page can tell the author
                # which voice still needs approval/expiry handling.
                diagnoses = list(result.result.get("diagnoses") or [])
                msg = str(result.result.get("error") or "确认团队失败")
                self.signals.worker_failed.emit(
                    self._worker_id,
                    {
                        "message": msg,
                        "step": "confirm_team",
                        "diagnoses": diagnoses,
                    },
                )
                return
            self.signals.voice_team_updated.emit(result.result)
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"message": f"确认团队异常: {exc}", "step": "confirm_team"},
            )


class ExportAudioWorker(TTSJobWorker):
    """Adapt the shared delivery export to the PySide local save-file workflow."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        chapter_number: int,
        export_format: str = "mp3",
        output_path: str = "",
        settings: Any,
        layout: ProjectLayout,
        mock: bool = False,
        target_lufs: float | None = None,
        include_srt: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._chapter_number = chapter_number
        self._export_format = export_format
        self._output_path = output_path
        self._settings = settings
        self._layout = layout
        self._target_lufs = target_lufs
        self._include_srt = include_srt

    async def _run_async(self) -> None:
        """Delegate delivery validation and file publication to the shared Engine path."""
        self._check_cancel()
        is_book_export = self._export_format == "zip"
        default_name = (
            "tts_export.zip"
            if is_book_export
            else f"chapter_{self._chapter_number:03d}.{self._export_format}"
        )
        output_path = Path(self._output_path) if self._output_path else Path.home() / default_name
        execution = await execute_export_audio_delivery(
            project_id=self._project_id,
            layout=self._layout,
            scope="book" if is_book_export else "chapter",
            chapter_number=None if is_book_export else self._chapter_number,
            format="zip" if is_book_export else self._export_format,
            include_subtitles=self._include_srt,
            target_lufs=self._target_lufs,
            destination=output_path,
        )
        result = execution.result
        if result.get("error"):
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "message": str(result.get("error") or "音频导出失败"),
                    "step": "export",
                    "error_code": str(result.get("error_code") or ""),
                },
            )
            return
        self._check_cancel()
        self.signals.audio_completed.emit({"export_path": str(output_path)})


class ExportAudiobookWorker(TTSJobWorker):
    """Worker exporting the finished audiobook delivery package.

    Delegates to the same Engine delivery function used by Nimo: it reuses the
    assembled chapter masters (never re-renders), produces the package, and
    atomically publishes the final ZIP. The sequential chapter gate surfaces
    unconfirmed chapters as a structured failure so the UI can prompt
    "第 N 章确认后继续".
    """

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        project_id: str,
        settings: Any,
        layout: ProjectLayout,
        chapter_numbers: list[int] | None = None,
        require_delivery_ready: bool = True,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._project_id = project_id
        self._settings = settings
        self._layout = layout
        self._chapter_numbers = chapter_numbers
        self._require_delivery_ready = require_delivery_ready

    async def _run_async(self) -> None:
        self._check_cancel()
        execution = await execute_export_audiobook_delivery(
            project_id=self._project_id,
            layout=self._layout,
            chapter_numbers=self._chapter_numbers,
            require_delivery_ready=self._require_delivery_ready,
        )
        result = execution.result
        if result.get("error"):
            gate_chapters = result.get("gate_chapters") or []
            if gate_chapters:
                display = "、".join(str(number) for number in gate_chapters[:24])
                message = f"第 {display} 章尚未确认，请确认这些章节后继续导出。"
            else:
                message = str(result.get("error") or "有声书导出失败")
            self.signals.worker_failed.emit(
                self._worker_id,
                {
                    "message": message,
                    "step": "export_audiobook",
                    "gate_chapters": gate_chapters,
                },
            )
            return
        self.signals.audio_completed.emit(
            {
                "export_path": str(
                    self._layout.root / "exports" / "voice" / result.get("export_filename", "")
                ),
                "package_id": str(result.get("package_id", "")),
                "chapter_count": len(result.get("chapters", [])),
            }
        )


class ListVoicesWorker(TTSJobWorker):
    """Worker for listing available system voices from the current provider."""

    signals_cls = TTSWorkerSignals

    def __init__(
        self,
        *,
        settings: Any,
        provider: str = "",
        gender: str | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(settings=settings, mock=mock)
        self._settings = settings
        self._provider = provider
        self._gender = gender

    async def _run_async(self) -> None:
        """List system voices from the provider adapter."""
        self._check_cancel()
        provider_str = self._provider or self._settings.tts_default_provider
        try:
            result = await execute_list_tts_voices(
                project_id="desktop",
                settings=self._settings,
                provider=provider_str,
                gender=self._gender,
                limit=200,
            )
            self.signals.provider_status.emit(result.result)
            self.signals.voices_listed.emit(list(result.result.get("voices", [])))
        except Exception as exc:
            self.signals.worker_failed.emit(
                self._worker_id,
                {"summary": f"获取音色列表失败: {exc}", "detail": str(exc), "step": "list_voices"},
            )
