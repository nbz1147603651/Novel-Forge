"""Workspace command dispatch used by UI-facing job services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import signature
from typing import Any, Protocol, cast

from novel_forge.app_service.contracts import JobCommand, JobKind, JobScope
from novel_forge.app_service.ollama_control import (
    OllamaControlError,
    OllamaOperationCancelled,
    OllamaPullProgress,
    get_ollama_control_service,
    validate_model_name,
)
from novel_forge.app_service.performance_metrics import (
    CountingStorageProxy,
    RunPerformanceMetrics,
)
from novel_forge.app_service.repair_commands import RepairCommands
from novel_forge.core.schemas.repair import RepairCaseJobRequest
from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration.mission_factory import (
    causal_repair_mission,
    continuity_repair_mission,
    issues_repair_mission,
)
from novel_forge.tts.schemas import ChapterAudioResult
from novel_forge.tts.services.automation import AudioAutomationMode, resolve_audio_automation_mode
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError, require_delivery_ready
from novel_forge.workspace.contracts import (
    AdvancePlanningHorizonRequest,
    AuthoringMessageJobRequest,
    BookConsistencyRequest,
    BookEditorialAuditRequest,
    ExportBookRequest,
    ExtendOutlineRequest,
    GlobalRepairQueueRequest,
    InitLongRequest,
    PolishChapterRequest,
    PolishOutlineRequest,
    PrepareChapterRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    ReextractRelationshipsRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
    SemanticConsistencyRefreshRequest,
    SyncChapterContractsRequest,
    TTSBuildVoiceTeamRequest,
    TTSExportAudiobookRequest,
    TTSExportAudioRequest,
    TTSFullPipelineRequest,
    TTSGenerateScriptRequest,
    TTSPostArchiveRequest,
    TTSSynthesizeRequest,
)
from novel_forge.workspace.execution import (
    execute_book_consistency,
    execute_book_editorial_audit,
    execute_build_voice_team,
    execute_export_audio_delivery,
    execute_export_audiobook_delivery,
    execute_export_book,
    execute_extend_outline,
    execute_full_tts_pipeline,
    execute_generate_dubbing_script,
    execute_global_repair_queue,
    execute_init_long,
    execute_polish_chapter,
    execute_prepare_chapter,
    execute_rebuild_memory_vectors,
    execute_reevaluate_chapter,
    execute_reextract_relationships,
    execute_repair,
    execute_repair_motif_history,
    execute_resolve_chapter_checkpoint,
    execute_run_chapter,
    execute_run_short,
    execute_sync_chapter_contracts,
    execute_synthesize_chapter,
)
from novel_forge.workspace.execution_outline_polish import execute_polish_outline
from novel_forge.workspace.result_payloads import (
    build_init_long_result_payload,
    build_repair_chapter_result_payload,
    build_run_chapter_result_payload,
    build_run_short_result_payload,
    build_usage_payload,
    extract_trace_step_usage,
    normalize_chapter_session_payload,
    normalize_prepare_chapter_payload,
)
from novel_forge.workspace.runtime import (
    RuntimeServices,
    repair_control_mode_from_request,
    request_runtime_overrides,
)

StepCallback = Callable[[str, Any], None]


class TTSPayloadError(RuntimeError):
    """RuntimeError carrying a structured TTS failure payload.

    The workspace TTS services already return actionable diagnostics
    (``error_code`` / ``action`` / ``missing_prerequisites``) in their result
    payloads.  The delivery gate raises this error so the structured fields
    survive the app-service error path and land in the run summary for the
    UI to render, instead of being flattened into a bare message.

    Author: novel-forge
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.error_summary = {
            "title": "配音任务失败",
            "summary": str(payload.get("error") or "TTS command failed"),
            "detail": str(payload.get("detail") or payload.get("error") or "TTS command failed"),
            "error_code": str(payload.get("error_code") or ""),
            "recovery_actions": list(payload.get("recovery_actions") or []),
            "log_path": str(payload.get("run_log_dir") or ""),
        }
        super().__init__(str(payload.get("error") or "TTS command failed"))


class LoggedCommandError(RuntimeError):
    """Carries a bounded, structured error summary plus the durable run-log path."""

    def __init__(self, cause: Exception, *, run_log_dir: str) -> None:
        detail = str(cause).strip() or type(cause).__name__
        context = getattr(cause, "context", {})
        context_mapping = dict(context) if isinstance(context, dict) else {}
        recovery_actions = context_mapping.get("recovery_actions")
        retryable = context_mapping.get("retryable")
        if retryable is None:
            retryable = context_mapping.get(
                "is_transient",
                getattr(
                    cause, "is_transient_error", isinstance(cause, (TimeoutError, ConnectionError))
                ),
            )
        self.error_summary = {
            "title": "任务失败",
            "summary": f"{type(cause).__name__}: {detail[:320]}",
            "detail": detail[:1600],
            "exception_type": type(cause).__name__,
            "error_code": str(getattr(cause, "error_code", "") or ""),
            "retryable": bool(retryable),
            "recovery_actions": (
                [dict(item) for item in recovery_actions if isinstance(item, dict)]
                if isinstance(recovery_actions, list)
                else []
            ),
            "log_path": run_log_dir,
        }
        super().__init__(detail)


# Maps voice-team reuse failure reasons to Chinese labels for UI rendering.
# Must stay in sync with REASON_* constants in
# novel_forge/workspace/tts_ops/execution.py.
_DIAGNOSIS_REASON_LABELS: dict[str, str] = {
    "missing": "音色缺失",
    "provider_mismatch": "平台不一致",
    "clone_not_ready": "未就绪",
    "pending_approval": "待确认",
    "expired": "已过期",
    "narrator_unavailable": "旁白不可用",
    "unknown": "未知原因",
}


@dataclass(frozen=True)
class PreparedCommand:
    kind: JobKind
    request: Any
    label: str
    project_id: str
    command_name: str
    metadata: dict[str, Any]


class CommandExecutor(Protocol):
    def prepare(self, command: JobCommand) -> PreparedCommand: ...

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: RuntimeServices,
        on_step: StepCallback,
    ) -> dict[str, Any]: ...


def _dump_request(request: Any) -> dict[str, Any]:
    if isinstance(request, dict):
        return dict(request)
    if hasattr(request, "model_dump"):
        return cast(dict[str, Any], request.model_dump(mode="json"))
    return {}


def _dict_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    return {}


def _init_long_request_from_meta(layout: ProjectLayout, project_id: str) -> InitLongRequest:
    meta_path = layout.init_request_meta_path
    if not meta_path.exists():
        raise ValueError("缺少 states/init_request_meta.json，无法安全重试立项修复。")
    try:
        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取立项参数失败：{exc}") from exc
    request_payload = meta.get("request") if isinstance(meta, dict) else None
    if not isinstance(request_payload, dict):
        raise ValueError("立项参数元数据不完整，无法安全重试。")
    init_input = request_payload.get("init_input")
    generation_options = request_payload.get("generation_options")
    if not isinstance(init_input, dict) or not isinstance(generation_options, dict):
        raise ValueError("立项参数元数据缺少 init_input 或 generation_options。")
    premise = str(init_input.get("premise") or "").strip()
    if not premise:
        raise ValueError("立项参数里缺少 premise，无法安全重试。")

    def _int_option(key: str, fallback: int) -> int:
        try:
            return int(generation_options.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    preferences = generation_options.get("blueprint_element_preferences")
    if not isinstance(preferences, dict):
        preferences = {}
    return InitLongRequest(
        project_id=project_id,
        premise=premise,
        genre=str(init_input.get("genre") or "other"),
        tone=str(init_input.get("tone") or "neutral"),
        total_chapters=_int_option("total_chapters", 20),
        words_per_chapter=_int_option("words_per_chapter", 3000),
        volume_mode=str(generation_options.get("volume_mode_setting") or "auto"),
        chapters_per_volume=_int_option("chapters_per_volume_setting", 0),
        title=str(init_input.get("title") or ""),
        language=str(init_input.get("language") or "zh"),
        characters_hint=str(init_input.get("characters_hint") or ""),
        world_hint=str(init_input.get("world_hint") or ""),
        conflict_hint=str(init_input.get("conflict_hint") or ""),
        pov_hint=str(init_input.get("pov_hint") or ""),
        opening_style=str(init_input.get("opening_style") or ""),
        ending_style=str(init_input.get("ending_style") or ""),
        extra_instructions=str(init_input.get("extra_instructions") or ""),
        polish_hint=str(generation_options.get("polish_hint") or ""),
        research_enabled=bool(generation_options.get("research_enabled", False)),
        research_provider=str(generation_options.get("research_provider") or "auto"),
        research_query_hint=str(generation_options.get("research_query_hint") or ""),
        blueprint_element_preferences=preferences,
    )


def _init_readiness_blocks_only_source_artifacts(layout: ProjectLayout) -> bool:
    path = layout.reports_dir / "init_readiness.json"
    if not path.exists():
        return False
    try:
        import json

        readiness = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(readiness, dict) or bool(readiness.get("allowed", False)):
        return False
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    source_stage = stages.get("source_artifacts")
    if not isinstance(source_stage, dict) or not bool(source_stage.get("blocked", False)):
        return False
    for stage_name, stage_payload in stages.items():
        if stage_name == "source_artifacts" or not isinstance(stage_payload, dict):
            continue
        if bool(stage_payload.get("blocked", False)):
            return False
    remaining = readiness.get("remaining_issues")
    if isinstance(remaining, list) and remaining:
        return all(
            isinstance(issue, dict) and issue.get("stage") == "source_artifacts"
            for issue in remaining
        )
    return True


def _init_readiness_blocked_artifacts(layout: ProjectLayout) -> set[str]:
    path = layout.reports_dir / "init_readiness.json"
    if not path.exists():
        return set()
    try:
        import json

        readiness = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(readiness, dict):
        return set()
    aliases = {
        "blueprint": "blueprint",
        "narrative_blueprint": "blueprint",
        "plans/narrative_blueprint.json": "blueprint",
        "outline": "outline",
        "story_outline": "outline",
        "outline.json": "outline",
        "chapter_contracts": "chapter_contracts",
        "contracts": "chapter_contracts",
        "plans/chapter_contracts.json": "chapter_contracts",
        "source_artifacts": "source_artifacts",
    }
    stage_hints = {
        "blueprint_coherence": "blueprint",
        "outline_inheritance": "outline",
        "contract_coherence": "chapter_contracts",
        "claim_contract_coverage": "chapter_contracts",
        "source_artifacts": "source_artifacts",
    }
    artifacts: set[str] = set()

    def add(value: object) -> None:
        raw = str(value or "").strip().lower()
        artifact = aliases.get(raw, raw)
        if artifact:
            artifacts.add(artifact)

    remaining = readiness.get("remaining_issues")
    if isinstance(remaining, list):
        for issue in remaining:
            if not isinstance(issue, dict):
                continue
            scopes = issue.get("repair_scope")
            scoped = False
            if isinstance(scopes, list):
                for scope in scopes:
                    if isinstance(scope, dict):
                        add(scope.get("artifact"))
                        scoped = True
            if not scoped:
                add(stage_hints.get(str(issue.get("stage") or "").strip(), ""))
    return artifacts


def _drop_init_repair_history_for_artifact(layout: ProjectLayout, *, artifact: str) -> None:
    path = layout.reports_dir / "init_artifact_repair.json"
    if not path.exists():
        return
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    repairs = payload.get("repairs") if isinstance(payload, dict) else None
    if not isinstance(repairs, list):
        return
    normalized_artifact = artifact.strip().lower()
    remaining = [
        item
        for item in repairs
        if not (
            isinstance(item, dict)
            and str(item.get("artifact") or "").strip().lower() == normalized_artifact
        )
    ]
    if len(remaining) == len(repairs):
        return
    try:
        atomic_write_json(path, {"repairs": remaining})
    except OSError:
        return


def _reset_init_readiness_retry_state(
    layout: ProjectLayout,
    *,
    reset_repair_history: bool,
) -> None:
    source_artifacts_only = _init_readiness_blocks_only_source_artifacts(layout)
    blocked_artifacts = (
        set() if source_artifacts_only else _init_readiness_blocked_artifacts(layout)
    )
    stale_report_paths = (
        (
            layout.reports_dir / "init_readiness.json",
            layout.reports_dir / "outline_inheritance.json",
        )
        if not source_artifacts_only
        else ()
    )
    for path in stale_report_paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    if reset_repair_history:
        if source_artifacts_only:
            _drop_init_repair_history_for_artifact(layout, artifact="source_artifacts")
            _drop_init_repair_history_for_artifact(layout, artifact="chapter_contracts")
        else:
            artifacts = blocked_artifacts or {"outline"}
            for artifact in artifacts:
                _drop_init_repair_history_for_artifact(layout, artifact=artifact)


def _serialize_short_result(project_id: str, result: Any) -> dict[str, Any]:
    payload = build_run_short_result_payload(
        project_id,
        result,
        preview_chars=280,
        preview_ellipsis=True,
    )
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    step_usage = extract_trace_step_usage(result)
    if step_usage:
        payload["token_steps"] = step_usage
    return payload


def _serialize_init_result(project_id: str, result: Any) -> dict[str, Any]:
    payload = build_init_long_result_payload(project_id, result)
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    return payload


def _serialize_chapter_result(project_id: str, chapter_number: int, result: Any) -> dict[str, Any]:
    payload = build_run_chapter_result_payload(
        project_id,
        result,
        chapter_number=chapter_number,
        preview_chars=280,
        preview_ellipsis=True,
    )
    payload.update(build_usage_payload(result, include_trace_aliases=True))
    step_usage = extract_trace_step_usage(result)
    if step_usage:
        payload["token_steps"] = step_usage
    return payload


def _serialize_polish_result(result: Any, project_id: str, chapter_number: int) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "original_word_count": getattr(result, "original_word_count", 0),
        "polished_word_count": getattr(result, "polished_word_count", 0),
        "warnings": list(getattr(result, "warnings", []) or []),
    }


def _serialize_consistency_result(result: Any, project_id: str) -> dict[str, Any]:
    auto_repair = getattr(result, "auto_repair", None)
    repair_report_path = (
        auto_repair.get("repair_report_path") if isinstance(auto_repair, dict) else None
    )
    return {
        "project_id": project_id,
        "issue_count": len(getattr(result, "issues", [])),
        "consistency_score": getattr(result, "consistency_score", 0.0),
        "summary": getattr(result, "summary", ""),
        "analysis_mode": getattr(result, "analysis_mode", "summary"),
        "chapters_audited": list(getattr(result, "chapters_audited", []) or []),
        "truncated_chapters": list(getattr(result, "truncated_chapters", []) or []),
        "auto_repair": auto_repair,
        "repair_report_path": repair_report_path,
    }


RequestFactory = Callable[[dict[str, Any], JobCommand], Any]
ProjectIdResolver = Callable[[Any, JobCommand], str]
LabelBuilder = Callable[[Any, JobCommand, str], str]
CommandRunResult = tuple[dict[str, Any], str]
RunHandler = Callable[
    [PreparedCommand, RuntimeServices, StepCallback, RunPerformanceMetrics],
    Awaitable[CommandRunResult],
]


@dataclass(frozen=True)
class CommandSpec:
    request_factory: RequestFactory
    project_id: ProjectIdResolver
    label: LabelBuilder
    command_name: str
    run_handler: RunHandler


def _request_model(model: Any) -> RequestFactory:
    def _factory(payload: dict[str, Any], _command: JobCommand) -> Any:
        return model.model_validate(payload)

    return _factory


def _request_project_id(request: Any, _command: JobCommand) -> str:
    return str(getattr(request, "project_id", "") or "")


def _init_repair_retry_request(payload: dict[str, Any], command: JobCommand) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or command.project_id or "").strip()
    if not project_id:
        raise ValueError("缺少项目 ID，无法提交初始化修复复审。")
    return {
        "project_id": project_id,
        "reset_repair_history": bool(payload.get("reset_repair_history", False)),
    }


def _dict_project_id(request: Any, _command: JobCommand) -> str:
    return str(request.get("project_id") or "") if isinstance(request, dict) else ""


def _system_project_id(_request: Any, _command: JobCommand) -> str:
    """System jobs intentionally never acquire a project ownership boundary."""

    return ""


def _ollama_runtime_request(payload: dict[str, Any], _command: JobCommand) -> dict[str, Any]:
    operation = str(payload.get("operation") or "ensure").strip().lower()
    if operation not in {"ensure", "restart", "stop"}:
        raise ValueError("Ollama 运行控制只支持 ensure、restart 或 stop。")
    return {"operation": operation}


def _ollama_model_request(payload: dict[str, Any], _command: JobCommand) -> dict[str, Any]:
    return {"model": validate_model_name(str(payload.get("model") or ""))}


def _label_run_short(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"短篇创作 · {project_id or '自动项目'}"


def _label_init_long(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"长篇立项 · {project_id or '自动项目'}"


def _label_init_repair_retry(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"长篇立项 · {project_id}"


def _label_repair_case(request: RepairCaseJobRequest, command: JobCommand, project_id: str) -> str:
    action = {
        "prepare": "准备修复候选",
        "verify": "复验修复候选",
        "shadow_compare": "影子比较",
    }[request.operation]
    return command.label or f"{action} · {project_id}"


def _label_run_chapter(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"章节续写 · {project_id} / 第 {request.chapter_number} 章"


def _label_prepare_chapter(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"章节方案 · {project_id} / 第 {request.chapter_number} 章"


def _label_resolve_checkpoint(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"章节断点继续 · {project_id} / 第 {request.chapter_number} 章"


def _label_repair_continuity(request: Any, command: JobCommand, project_id: str) -> str:
    issue_count: int | str = len(request.issue_indices) if request.issue_indices else "全部"
    return (
        command.label
        or f"连贯性修复（{issue_count} 条问题） · {project_id} / 第 {request.chapter_number} 章"
    )


def _label_repair_causal(request: Any, command: JobCommand, project_id: str) -> str:
    issue_count: int | str = len(request.issue_indices) if request.issue_indices else "全部"
    return (
        command.label
        or f"因果链修复（{issue_count} 条问题） · {project_id} / 第 {request.chapter_number} 章"
    )


def _label_repair_issues(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"修复选中问题 · {project_id} / 第 {request.chapter_number} 章"


def _label_reevaluate_chapter(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"重新评估 · {project_id} / 第 {request.chapter_number} 章"


def _label_polish_chapter(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"精修润色 · {project_id} / 第 {request.chapter_number} 章"


def _label_book_consistency(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"全书一致性审计 · {project_id}"


def _label_book_editorial_audit(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"全书出版编辑审查 · {project_id}"


def _label_global_repair_queue(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"执行全书修复队列（最多 {request.max_items} 项） · {project_id}"


def _label_export_book(request: Any, command: JobCommand, project_id: str) -> str:
    fmt_label = {"markdown": "Markdown", "epub": "EPUB", "txt": "纯文本"}.get(
        request.format,
        request.format,
    )
    return command.label or f"导出 {fmt_label} · {project_id}"


def _label_reextract_relationships(request: Any, command: JobCommand, project_id: str) -> str:
    scope = f"第 {request.chapter_number} 章" if request.chapter_number > 0 else "全部章节"
    return command.label or f"重新提取关系 ({scope}) · {project_id}"


def _label_repair_motif_history(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"修补母题历史 · {project_id} / 第 {request.chapter_number} 章"


def _label_rebuild_memory_vectors(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"重建向量索引 · {project_id}"


def _label_polish_outline(request: Any, command: JobCommand, project_id: str) -> str:
    scope = request.chapter_range.strip() or "已选章节"
    return command.label or f"大纲润色 · {scope} · {project_id}"


def _label_sync_chapter_contracts(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"同步章节契约 · {project_id}"


def _label_extend_outline(request: Any, command: JobCommand, project_id: str) -> str:
    target = request.target_total or request.additional_chapters or 0
    action = f"至 {target} 章" if request.target_total else f"追加 {target} 章"
    return command.label or f"延长全书{action} · {project_id}"


def _label_tts_synthesize(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"章节配音 · {project_id} / 第 {request.chapter_number} 章"


def _label_tts_export_audio(request: Any, command: JobCommand, project_id: str) -> str:
    scope = "全书音频" if request.scope == "book" else f"第 {request.chapter_number} 章音频"
    return command.label or f"导出{scope} · {project_id}"


def _label_tts_export_audiobook(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"导出有声书包 · {project_id}"


def _label_tts_build_voice_team(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"组建配音团队 · {project_id}"


def _label_tts_generate_script(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"生成配音脚本 · {project_id} / 第 {request.chapter_number} 章"


def _label_tts_full_pipeline(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"完整配音流程 · {project_id} / 第 {request.chapter_number} 章"


def _label_tts_post_archive(request: Any, command: JobCommand, project_id: str) -> str:
    return command.label or f"自动配音 · {project_id} / 第 {request.chapter_number} 章"


def _label_ollama_runtime(request: Any, command: JobCommand, _project_id: str) -> str:
    action = {"ensure": "检查/启动", "restart": "重启", "stop": "停止"}[request["operation"]]
    return command.label or f"Ollama · {action}"


def _label_ollama_pull(request: Any, command: JobCommand, _project_id: str) -> str:
    return command.label or f"下载 Ollama 模型 · {request['model']}"


def _label_ollama_delete(request: Any, command: JobCommand, _project_id: str) -> str:
    return command.label or f"删除 Ollama 模型 · {request['model']}"


async def _execute_logged_prepared(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
    *,
    execute: Callable[[StepCallback], Awaitable[Any]],
    project_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    return await _run_with_project_logger(
        runtime=runtime,
        project_id=project_id or prepared.project_id,
        command=prepared.command_name,
        metadata=metadata or prepared.metadata,
        on_step=on_step,
        metrics=metrics,
        execute=execute,
    )


def _ollama_sidecar_payload(result: Any) -> dict[str, Any]:
    return {
        "status": str(getattr(result, "status", "failed")),
        "detail": str(getattr(result, "detail", "")),
        "available": bool(getattr(result, "available", False)),
        "auto_started": bool(getattr(result, "auto_started", False)),
        "owned": bool(getattr(result, "auto_started", False)),
    }


async def _run_ollama_runtime_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    _metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = cast(dict[str, str], prepared.request)
    control = get_ollama_control_service(runtime.settings)
    operation = request["operation"]
    on_step("ollama_runtime", {"operation": operation, "status": "starting"})
    if operation == "stop":
        await asyncio.to_thread(control.stop_runtime)
        payload = {
            "operation": operation,
            "status": "stopped",
            "detail": "已停止 Engine 托管的 Ollama。",
        }
    else:
        result = await asyncio.to_thread(
            control.restart_runtime if operation == "restart" else control.ensure_runtime
        )
        payload = {"operation": operation, **_ollama_sidecar_payload(result)}
    on_step("ollama_runtime", payload)
    return payload, ""


async def _run_ollama_pull_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    _metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = cast(dict[str, str], prepared.request)
    model = request["model"]
    is_cancelled = prepared.metadata.get("_is_cancelled")
    cancellation_probe = is_cancelled if callable(is_cancelled) else None
    control = get_ollama_control_service(runtime.settings)
    on_step("ollama_pull", {"model": model, "status": "starting", "percent": -1})

    if await asyncio.to_thread(control.model_exists, model):
        payload = {"model": model, "status": "already_present", "percent": 100}
        on_step("ollama_pull", payload)
        return payload, ""

    def _progress(value: OllamaPullProgress) -> None:
        on_step(
            "ollama_pull",
            {
                "model": model,
                "status": value.status,
                "percent": value.percent,
                "completed": value.completed,
                "total": value.total,
            },
        )

    for attempt in range(1, 4):
        try:
            on_step("ollama_pull", {"model": model, "attempt": attempt, "max_attempts": 3})
            await asyncio.to_thread(
                control.pull_model,
                model,
                on_progress=_progress,
                is_cancelled=cancellation_probe,
            )
            break
        except OllamaOperationCancelled:
            on_step("ollama_pull", {"model": model, "status": "cancelled"})
            raise asyncio.CancelledError from None
        except OllamaControlError as exc:
            if not exc.retryable or attempt == 3:
                raise
            on_step(
                "ollama_pull_retry",
                {
                    "model": model,
                    "attempt": attempt,
                    "max_attempts": 3,
                    "detail": str(exc),
                },
            )
    payload = {"model": model, "status": "completed", "percent": 100}
    on_step("ollama_pull", payload)
    return payload, ""


async def _run_ollama_delete_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    _metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = cast(dict[str, str], prepared.request)
    model = request["model"]
    control = get_ollama_control_service(runtime.settings)
    checkpoint = str(prepared.metadata.get("configuration_checkpoint") or "impact_checked")
    on_step("ollama_delete", {"model": model, "checkpoint": checkpoint})
    on_step("ollama_delete", {"model": model, "checkpoint": "model_delete_started"})
    await asyncio.to_thread(control.delete_model, model)
    on_step("ollama_delete", {"model": model, "checkpoint": "model_deleted"})
    payload = {"model": model, "status": "completed", "checkpoint": "committed"}
    on_step("ollama_delete", payload)
    return payload, ""


async def _run_short_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    effective_project_id = request.project_id.strip() or runtime.create_project_id("short")
    effective_request = request.model_copy(update={"project_id": effective_project_id})
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        project_id=effective_project_id,
        metadata={**prepared.metadata, "project_id": effective_project_id},
        execute=lambda ts: execute_run_short(runtime, effective_request, on_step_progress=ts),
    )
    return _serialize_short_result(execution.project_id, execution.result), run_log_dir


async def _run_init_long_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    effective_project_id = request.project_id.strip() or runtime.create_project_id("long")
    effective_request = request.model_copy(update={"project_id": effective_project_id})
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        project_id=effective_project_id,
        metadata={**prepared.metadata, "project_id": effective_project_id},
        execute=lambda ts: execute_init_long(runtime, effective_request, on_step_progress=ts),
    )
    return _serialize_init_result(execution.project_id, execution.result), run_log_dir


async def _run_init_repair_retry_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    project_id = str(request["project_id"])
    layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
    from novel_forge.persistence.foundation_guard import require_versioned_foundation_write

    require_versioned_foundation_write(layout.root)
    init_request = _init_long_request_from_meta(layout, project_id)
    _reset_init_readiness_retry_state(
        layout,
        reset_repair_history=bool(request.get("reset_repair_history", False)),
    )
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        project_id=project_id,
        execute=lambda ts: execute_init_long(runtime, init_request, on_step_progress=ts),
    )
    return _serialize_init_result(execution.project_id, execution.result), run_log_dir


async def _run_chapter_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    with request_runtime_overrides(runtime, request):
        execution, run_log_dir = await _execute_logged_prepared(
            prepared,
            runtime,
            on_step,
            metrics,
            execute=lambda ts: execute_run_chapter(
                runtime,
                request,
                on_step_progress=ts,
                defer_post_archive_tts=True,
            ),
        )
    return (
        _serialize_chapter_result(
            execution.project_id,
            request.chapter_number,
            execution.result,
        ),
        run_log_dir,
    )


async def _run_prepare_chapter_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    with request_runtime_overrides(runtime, request):
        execution, run_log_dir = await _execute_logged_prepared(
            prepared,
            runtime,
            on_step,
            metrics,
            execute=lambda ts: execute_prepare_chapter(runtime, request, on_step_progress=ts),
        )
    return normalize_prepare_chapter_payload(execution.result), run_log_dir


async def _run_resolve_chapter_checkpoint_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    with request_runtime_overrides(runtime, request):
        execution, run_log_dir = await _execute_logged_prepared(
            prepared,
            runtime,
            on_step,
            metrics,
            execute=lambda ts: execute_resolve_chapter_checkpoint(
                runtime,
                request,
                on_step_progress=ts,
                defer_post_archive_tts=True,
            ),
        )
    return normalize_chapter_session_payload(execution.result), run_log_dir


async def _run_repair_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    with request_runtime_overrides(runtime, request):
        if prepared.kind == JobKind.REPAIR_CONTINUITY:
            mission = continuity_repair_mission(request, settings=runtime.settings)
        elif prepared.kind == JobKind.REPAIR_CAUSAL:
            mission = causal_repair_mission(request, settings=runtime.settings)
        else:
            mission = issues_repair_mission(request, settings=runtime.settings)
        execution, run_log_dir = await _execute_logged_prepared(
            prepared,
            runtime,
            on_step,
            metrics,
            metadata={
                **prepared.metadata,
                "repair_control_mode": repair_control_mode_from_request(request) or None,
            },
            execute=lambda ts: execute_repair(
                runtime,
                mission,
                on_step_progress=ts,
                on_audit_update=lambda _proj, _chap, _res: ts(
                    "audit_result_update",
                    {"chapter_number": _chap, "audit_result": _res},
                ),
            ),
        )
    return (
        build_repair_chapter_result_payload(
            prepared.project_id,
            execution.result,
            chapter_number=request.chapter_number,
        ),
        run_log_dir,
    )


async def _run_reevaluate_chapter_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_reevaluate_chapter(runtime, request, on_step_progress=ts),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_polish_chapter_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_polish_chapter(runtime, request, on_step_progress=ts),
    )
    return _serialize_polish_result(
        execution.result, prepared.project_id, request.chapter_number
    ), run_log_dir


async def _run_polish_outline_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_polish_outline(runtime, request, on_step_progress=ts),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_book_consistency_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_book_consistency(runtime, request, on_step_progress=ts),
    )
    return _serialize_consistency_result(execution.result, prepared.project_id), run_log_dir


async def _run_global_repair_queue_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_global_repair_queue(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_book_editorial_audit_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_book_editorial_audit(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_export_book_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_export_book(runtime, request, on_step_progress=ts),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_reextract_relationships_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_reextract_relationships(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_repair_motif_history_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_repair_motif_history(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_rebuild_memory_vectors_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_rebuild_memory_vectors(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_sync_chapter_contracts_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_sync_chapter_contracts(
            runtime,
            request,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_extend_outline_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_extend_outline(runtime, request, on_step_progress=ts),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_planning_horizon_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    from novel_forge.workspace.planning_jobs import execute_planning_horizon_job

    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_planning_horizon_job(
            runtime, prepared.request, on_step_progress=ts
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_authoring_chat_command(prepared: PreparedCommand, runtime: RuntimeServices, on_step: StepCallback, metrics: RunPerformanceMetrics) -> CommandRunResult:
    from novel_forge.workspace.authoring_conversation import execute_authoring_message

    execution, run_log_dir = await _execute_logged_prepared(prepared, runtime, on_step, metrics,
        execute=lambda ts: execute_authoring_message(runtime, prepared.request, on_step_progress=ts))
    return _dict_result(execution.result), run_log_dir


async def _run_repair_case_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = cast(RepairCaseJobRequest, prepared.request)

    async def _execute(ts: StepCallback) -> dict[str, Any]:
        ts(
            "repair_case_operation_started",
            {
                "case_id": request.case_id,
                "operation": request.operation,
                "case_version": request.case_version,
                "candidate_version": request.candidate_version,
            },
        )
        commands = RepairCommands(runtime.storage)
        if request.operation == "prepare":
            detail = await commands.prepare(
                request.project_id,
                request.case_id,
                case_version=request.case_version,
            )
            payload: dict[str, Any] = {
                "project_id": request.project_id,
                "case_id": request.case_id,
                "operation": request.operation,
                "case": detail.case.model_dump(mode="json"),
            }
        elif request.operation == "verify":
            detail = await commands.verify(
                request.project_id,
                request.case_id,
                case_version=request.case_version,
                candidate_version=request.candidate_version,
            )
            payload = {
                "project_id": request.project_id,
                "case_id": request.case_id,
                "operation": request.operation,
                "case": detail.case.model_dump(mode="json"),
            }
        else:
            payload = commands.shadow_compare(
                request.project_id,
                request.case_id,
                case_version=request.case_version,
                candidate_version=request.candidate_version,
            )
            payload["operation"] = request.operation
        ts(
            "repair_case_operation_completed",
            {
                "case_id": request.case_id,
                "operation": request.operation,
                "shadow_only": request.operation == "shadow_compare",
            },
        )
        return payload

    payload, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    return payload, run_log_dir


async def _run_semantic_consistency_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    from novel_forge.workspace.semantic_consistency import refresh_semantic_consistency

    payload, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: refresh_semantic_consistency(
            runtime,
            cast(SemanticConsistencyRefreshRequest, prepared.request),
            on_step_progress=ts,
        ),
    )
    return _dict_result(payload), run_log_dir


def _require_tts_delivery(execution: Any, on_step: StepCallback) -> Any:
    payload = _dict_result(execution.result)
    if payload.get("error"):
        raise TTSPayloadError(payload)
    audio_result = ChapterAudioResult.model_validate(payload)
    try:
        require_delivery_ready(audio_result)
    except TTSDeliveryNotReadyError as exc:
        on_step(
            "tts_delivery_blocked",
            {
                "chapter": audio_result.chapter_number,
                "blocking_reasons": list(exc.reasons),
            },
        )
        raise
    return execution


async def _run_tts_synthesize_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))
    automation_mode = resolve_audio_automation_mode(
        request.automation_mode,
        settings=runtime.settings,
    )

    async def _execute(ts: StepCallback) -> Any:
        def _segment_progress(segment_index: int, status: str) -> None:
            ts(
                "tts_segment",
                {
                    "chapter": request.chapter_number,
                    "segment_index": segment_index,
                    "status": status,
                },
            )

        execution = await execute_synthesize_chapter(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            settings=runtime.settings,
            layout=layout,
            provider=request.provider,
            automation_mode=automation_mode,
            on_step_progress=ts,
            on_segment_progress=_segment_progress,
        )
        if automation_mode == AudioAutomationMode.AUTONOMOUS:
            return _require_tts_delivery(execution, ts)
        return execution

    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    return _dict_result(execution.result), run_log_dir


async def _run_tts_build_voice_team_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_build_voice_team(
            project_id=request.project_id,
            characters=[],
            settings=runtime.settings,
            layout=layout,
            provider=request.provider,
            rebuild_character_ids=request.rebuild_character_ids or None,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_tts_generate_script_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))
    chapter_path = layout.chapter_path(request.chapter_number)
    if not chapter_path.is_file():
        raise ValueError(f"第 {request.chapter_number} 章尚未定稿，无法生成配音脚本")
    chapter_text = chapter_path.read_text(encoding="utf-8").strip()
    if not chapter_text:
        raise ValueError(f"第 {request.chapter_number} 章正文为空，无法生成配音脚本")
    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=lambda ts: execute_generate_dubbing_script(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            chapter_text=chapter_text,
            settings=runtime.settings,
            layout=layout,
            provider=request.provider,
            reference_style_strength=request.reference_style_strength,
            on_step_progress=ts,
        ),
    )
    return _dict_result(execution.result), run_log_dir


async def _run_tts_full_pipeline_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))
    automation_mode = resolve_audio_automation_mode(
        request.automation_mode,
        settings=runtime.settings,
    )

    async def _execute(ts: StepCallback) -> Any:
        def _segment_progress(segment_index: int, status: str) -> None:
            ts(
                "tts_segment",
                {
                    "chapter": request.chapter_number,
                    "segment_index": segment_index,
                    "status": status,
                },
            )

        execution = await execute_full_tts_pipeline(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            chapter_text=request.chapter_text,
            characters=request.characters,
            settings=runtime.settings,
            layout=layout,
            provider=request.provider,
            automation_mode=automation_mode,
            genre=request.genre,
            tone=request.tone,
            scene_context=request.scene_context or None,
            on_step_progress=ts,
            on_segment_progress=_segment_progress,
        )
        if automation_mode == AudioAutomationMode.AUTONOMOUS:
            return _require_tts_delivery(execution, ts)
        return execution

    execution, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    return _dict_result(execution.result), run_log_dir


async def _run_tts_post_archive_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request

    async def _execute(ts: StepCallback) -> dict[str, Any] | None:
        from novel_forge.workspace.post_archive_tts import run_post_archive_tts

        record = await run_post_archive_tts(
            runtime,
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            on_step_progress=ts,
        )
        if isinstance(record, dict) and record.get("status") == "failed":
            # Attach a structured error_summary so the UI can render per-character
            # diagnoses (which voice to confirm/expire/switch) instead of one
            # opaque string.  _error_payload in job_service merges this attribute.
            from novel_forge.workspace.post_archive_tts import TTSPostArchiveError

            diagnoses = list(record.get("diagnoses") or [])
            error_msg = str(record.get("error") or "自动配音任务失败")
            error_code = str(record.get("error_code") or "")
            missing_artifact = str(record.get("missing_artifact") or "")
            if diagnoses:
                summary_parts = [
                    f"{d.get('character_name') or d.get('character_id') or '?'}"
                    f"({_DIAGNOSIS_REASON_LABELS.get(d.get('reason', ''), d.get('reason', ''))})"
                    for d in diagnoses[:6]
                ]
                detail_lines = [
                    f"- {d.get('character_name') or d.get('character_id') or '旁白'}："
                    f"{_DIAGNOSIS_REASON_LABELS.get(d.get('reason', ''), d.get('reason', ''))} "
                    f"- {d.get('detail', '')}"
                    for d in diagnoses
                ]
                error_summary = {
                    "title": "自动配音失败",
                    "summary": "、".join(summary_parts),
                    "detail": error_msg + "\n" + "\n".join(detail_lines),
                    "error_code": error_code,
                    "missing_artifact": missing_artifact,
                    "diagnoses": diagnoses,
                    "confirmable_character_ids": [
                        d.get("character_id", "")
                        for d in diagnoses
                        if d.get("reason") == "pending_approval" and d.get("character_id")
                    ],
                }
            else:
                error_summary = {
                    "title": "自动配音失败",
                    "summary": error_msg[:200],
                    "detail": error_msg,
                    "error_code": error_code,
                    "missing_artifact": missing_artifact,
                    "diagnoses": [],
                    "confirmable_character_ids": [],
                }
            exc = TTSPostArchiveError(
                message=error_msg,
                error_code=error_code,
                missing_artifact=missing_artifact,
                diagnoses=diagnoses,
                confirmable_character_ids=list(error_summary["confirmable_character_ids"]),
                error_summary=error_summary,
            )
            raise exc
        return record

    record, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    payload = dict(record or {})
    payload.setdefault("status", "skipped")
    payload.setdefault("chapter_number", request.chapter_number)
    payload.setdefault("parent_job_id", request.parent_job_id)
    return payload, run_log_dir


async def _run_tts_export_audio_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))

    async def _execute(ts: StepCallback) -> dict[str, Any]:
        ts(
            "tts_export_start",
            {
                "scope": request.scope,
                "chapter_number": request.chapter_number,
                "format": request.format,
            },
        )
        execution = await execute_export_audio_delivery(
            project_id=request.project_id,
            layout=layout,
            scope=request.scope,
            chapter_number=request.chapter_number,
            format=request.format,
            include_subtitles=request.include_subtitles,
            target_lufs=request.target_lufs,
        )
        result = _dict_result(execution.result)
        if result.get("error"):
            raise TTSPayloadError(result)
        ts("tts_export_complete", {"filename": result.get("export_filename", "")})
        return result

    payload, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    return payload, run_log_dir


async def _run_tts_export_audiobook_command(
    prepared: PreparedCommand,
    runtime: RuntimeServices,
    on_step: StepCallback,
    metrics: RunPerformanceMetrics,
) -> CommandRunResult:
    request = prepared.request
    layout = ProjectLayout(runtime.storage.project_dir(request.project_id))

    async def _execute(ts: StepCallback) -> dict[str, Any]:
        ts("tts_audiobook_export_start", {"chapter_numbers": request.chapter_numbers})
        execution = await execute_export_audiobook_delivery(
            project_id=request.project_id,
            layout=layout,
            chapter_numbers=request.chapter_numbers or None,
            require_delivery_ready=request.require_delivery_ready,
        )
        result = _dict_result(execution.result)
        if result.get("error"):
            raise TTSPayloadError(result)
        ts("tts_audiobook_export_complete", {"filename": result.get("export_filename", "")})
        return result

    payload, run_log_dir = await _execute_logged_prepared(
        prepared,
        runtime,
        on_step,
        metrics,
        execute=_execute,
    )
    return payload, run_log_dir


COMMAND_REGISTRY: dict[JobKind, CommandSpec] = {
    JobKind.RUN_SHORT: CommandSpec(
        request_factory=_request_model(RunShortRequest),
        project_id=_request_project_id,
        label=_label_run_short,
        command_name="app-service-run-short",
        run_handler=_run_short_command,
    ),
    JobKind.INIT_LONG: CommandSpec(
        request_factory=_request_model(InitLongRequest),
        project_id=_request_project_id,
        label=_label_init_long,
        command_name="app-service-init-long",
        run_handler=_run_init_long_command,
    ),
    JobKind.INIT_REPAIR_RETRY: CommandSpec(
        request_factory=_init_repair_retry_request,
        project_id=_dict_project_id,
        label=_label_init_repair_retry,
        command_name="app-service-init-repair-retry",
        run_handler=_run_init_repair_retry_command,
    ),
    JobKind.RUN_CHAPTER: CommandSpec(
        request_factory=_request_model(RunChapterRequest),
        project_id=_request_project_id,
        label=_label_run_chapter,
        command_name="app-service-run-chapter",
        run_handler=_run_chapter_command,
    ),
    JobKind.PREPARE_CHAPTER: CommandSpec(
        request_factory=_request_model(PrepareChapterRequest),
        project_id=_request_project_id,
        label=_label_prepare_chapter,
        command_name="app-service-prepare-chapter",
        run_handler=_run_prepare_chapter_command,
    ),
    JobKind.RESOLVE_CHAPTER_CHECKPOINT: CommandSpec(
        request_factory=_request_model(ResolveChapterCheckpointRequest),
        project_id=_request_project_id,
        label=_label_resolve_checkpoint,
        command_name="app-service-resolve-chapter-checkpoint",
        run_handler=_run_resolve_chapter_checkpoint_command,
    ),
    JobKind.RESOLVE_CHAPTER_CHECKPOINT_FINALIZE: CommandSpec(
        request_factory=_request_model(ResolveChapterCheckpointRequest),
        project_id=_request_project_id,
        label=_label_resolve_checkpoint,
        command_name="app-service-resolve-chapter-checkpoint",
        run_handler=_run_resolve_chapter_checkpoint_command,
    ),
    JobKind.REPAIR_CONTINUITY: CommandSpec(
        request_factory=_request_model(RepairContinuityRequest),
        project_id=_request_project_id,
        label=_label_repair_continuity,
        command_name="app-service-repair-continuity",
        run_handler=_run_repair_command,
    ),
    JobKind.REPAIR_CAUSAL: CommandSpec(
        request_factory=_request_model(RepairCausalRequest),
        project_id=_request_project_id,
        label=_label_repair_causal,
        command_name="app-service-repair-causal",
        run_handler=_run_repair_command,
    ),
    JobKind.REPAIR_ISSUES: CommandSpec(
        request_factory=_request_model(RepairIssuesRequest),
        project_id=_request_project_id,
        label=_label_repair_issues,
        command_name="app-service-repair-issues",
        run_handler=_run_repair_command,
    ),
    JobKind.REEVALUATE_CHAPTER: CommandSpec(
        request_factory=_request_model(ReevaluateChapterRequest),
        project_id=_request_project_id,
        label=_label_reevaluate_chapter,
        command_name="app-service-reevaluate-chapter",
        run_handler=_run_reevaluate_chapter_command,
    ),
    JobKind.POLISH_CHAPTER: CommandSpec(
        request_factory=_request_model(PolishChapterRequest),
        project_id=_request_project_id,
        label=_label_polish_chapter,
        command_name="app-service-polish-chapter",
        run_handler=_run_polish_chapter_command,
    ),
    JobKind.BOOK_CONSISTENCY: CommandSpec(
        request_factory=_request_model(BookConsistencyRequest),
        project_id=_request_project_id,
        label=_label_book_consistency,
        command_name="app-service-book-consistency",
        run_handler=_run_book_consistency_command,
    ),
    JobKind.GLOBAL_REPAIR_QUEUE: CommandSpec(
        request_factory=_request_model(GlobalRepairQueueRequest),
        project_id=_request_project_id,
        label=_label_global_repair_queue,
        command_name="app-service-global-repair-queue",
        run_handler=_run_global_repair_queue_command,
    ),
    JobKind.BOOK_EDITORIAL_AUDIT: CommandSpec(
        request_factory=_request_model(BookEditorialAuditRequest),
        project_id=_request_project_id,
        label=_label_book_editorial_audit,
        command_name="app-service-book-editorial-audit",
        run_handler=_run_book_editorial_audit_command,
    ),
    JobKind.EXPORT_BOOK: CommandSpec(
        request_factory=_request_model(ExportBookRequest),
        project_id=_request_project_id,
        label=_label_export_book,
        command_name="app-service-export-book",
        run_handler=_run_export_book_command,
    ),
    JobKind.REEXTRACT_RELATIONSHIPS: CommandSpec(
        request_factory=_request_model(ReextractRelationshipsRequest),
        project_id=_request_project_id,
        label=_label_reextract_relationships,
        command_name="app-service-reextract-relationships",
        run_handler=_run_reextract_relationships_command,
    ),
    JobKind.REPAIR_MOTIF_HISTORY: CommandSpec(
        request_factory=_request_model(RepairMotifHistoryRequest),
        project_id=_request_project_id,
        label=_label_repair_motif_history,
        command_name="app-service-repair-motif-history",
        run_handler=_run_repair_motif_history_command,
    ),
    JobKind.REBUILD_MEMORY_VECTORS: CommandSpec(
        request_factory=_request_model(RebuildMemoryVectorsRequest),
        project_id=_request_project_id,
        label=_label_rebuild_memory_vectors,
        command_name="app-service-rebuild-memory-vectors",
        run_handler=_run_rebuild_memory_vectors_command,
    ),
    JobKind.POLISH_OUTLINE: CommandSpec(
        request_factory=_request_model(PolishOutlineRequest),
        project_id=_request_project_id,
        label=_label_polish_outline,
        command_name="app-service-polish-outline",
        run_handler=_run_polish_outline_command,
    ),
    JobKind.SYNC_CHAPTER_CONTRACTS: CommandSpec(
        request_factory=_request_model(SyncChapterContractsRequest),
        project_id=_request_project_id,
        label=_label_sync_chapter_contracts,
        command_name="app-service-sync-chapter-contracts",
        run_handler=_run_sync_chapter_contracts_command,
    ),
    JobKind.EXTEND_OUTLINE: CommandSpec(
        request_factory=_request_model(ExtendOutlineRequest),
        project_id=_request_project_id,
        label=_label_extend_outline,
        command_name="app-service-extend-outline",
        run_handler=_run_extend_outline_command,
    ),
    JobKind.PLANNING_HORIZON: CommandSpec(
        request_factory=_request_model(AdvancePlanningHorizonRequest),
        project_id=_request_project_id,
        label=lambda request, command, project_id: f"细化至第 {request.target_chapter} 章",
        command_name="app-service-planning-horizon",
        run_handler=_run_planning_horizon_command,
    ),
    JobKind.AUTHORING_CHAT: CommandSpec(request_factory=_request_model(AuthoringMessageJobRequest),
        project_id=_request_project_id, label=lambda request, command, project_id: "小说共创对话",
        command_name="app-service-authoring-chat", run_handler=_run_authoring_chat_command),
    JobKind.REPAIR_CASE: CommandSpec(
        request_factory=_request_model(RepairCaseJobRequest),
        project_id=_request_project_id,
        label=_label_repair_case,
        command_name="app-service-repair-case",
        run_handler=_run_repair_case_command,
    ),
    JobKind.SEMANTIC_CONSISTENCY: CommandSpec(
        request_factory=_request_model(SemanticConsistencyRefreshRequest),
        project_id=_request_project_id,
        label=lambda request, command, project_id: "刷新语义一致性",
        command_name="app-service-semantic-consistency",
        run_handler=_run_semantic_consistency_command,
    ),
    JobKind.TTS_SYNTHESIZE: CommandSpec(
        request_factory=_request_model(TTSSynthesizeRequest),
        project_id=_request_project_id,
        label=_label_tts_synthesize,
        command_name="app-service-tts-synthesize",
        run_handler=_run_tts_synthesize_command,
    ),
    JobKind.TTS_BUILD_VOICE_TEAM: CommandSpec(
        request_factory=_request_model(TTSBuildVoiceTeamRequest),
        project_id=_request_project_id,
        label=_label_tts_build_voice_team,
        command_name="app-service-tts-build-voice-team",
        run_handler=_run_tts_build_voice_team_command,
    ),
    JobKind.TTS_GENERATE_SCRIPT: CommandSpec(
        request_factory=_request_model(TTSGenerateScriptRequest),
        project_id=_request_project_id,
        label=_label_tts_generate_script,
        command_name="app-service-tts-generate-script",
        run_handler=_run_tts_generate_script_command,
    ),
    JobKind.TTS_FULL_PIPELINE: CommandSpec(
        request_factory=_request_model(TTSFullPipelineRequest),
        project_id=_request_project_id,
        label=_label_tts_full_pipeline,
        command_name="app-service-tts-full-pipeline",
        run_handler=_run_tts_full_pipeline_command,
    ),
    JobKind.TTS_POST_ARCHIVE: CommandSpec(
        request_factory=_request_model(TTSPostArchiveRequest),
        project_id=_request_project_id,
        label=_label_tts_post_archive,
        command_name="app-service-tts-post-archive",
        run_handler=_run_tts_post_archive_command,
    ),
    JobKind.TTS_EXPORT_AUDIO: CommandSpec(
        request_factory=_request_model(TTSExportAudioRequest),
        project_id=_request_project_id,
        label=_label_tts_export_audio,
        command_name="app-service-tts-export-audio",
        run_handler=_run_tts_export_audio_command,
    ),
    JobKind.TTS_EXPORT_AUDIOBOOK: CommandSpec(
        request_factory=_request_model(TTSExportAudiobookRequest),
        project_id=_request_project_id,
        label=_label_tts_export_audiobook,
        command_name="app-service-tts-export-audiobook",
        run_handler=_run_tts_export_audiobook_command,
    ),
    JobKind.OLLAMA_RUNTIME_CONTROL: CommandSpec(
        request_factory=_ollama_runtime_request,
        project_id=_system_project_id,
        label=_label_ollama_runtime,
        command_name="app-service-ollama-runtime",
        run_handler=_run_ollama_runtime_command,
    ),
    JobKind.OLLAMA_PULL_MODEL: CommandSpec(
        request_factory=_ollama_model_request,
        project_id=_system_project_id,
        label=_label_ollama_pull,
        command_name="app-service-ollama-pull",
        run_handler=_run_ollama_pull_command,
    ),
    JobKind.OLLAMA_DELETE_MODEL: CommandSpec(
        request_factory=_ollama_model_request,
        project_id=_system_project_id,
        label=_label_ollama_delete,
        command_name="app-service-ollama-delete",
        run_handler=_run_ollama_delete_command,
    ),
}


def command_registry() -> dict[JobKind, CommandSpec]:
    """Return a copy of the internal workspace command registry."""

    return dict(COMMAND_REGISTRY)


def _build_step_trace_summary(step_history: list[str]) -> dict[str, Any] | None:
    if not step_history:
        return None
    return {
        "completed_step_count": len(step_history),
        "completed_steps": step_history,
        "last_step": step_history[-1],
    }


def _extract_trace_summary(result: Any) -> dict[str, Any]:
    direct = getattr(result, "trace_summary", None)
    if isinstance(direct, dict) and direct:
        return direct
    nested = getattr(result, "result", None)
    nested_trace = (
        nested.get("trace_summary")
        if isinstance(nested, dict)
        else getattr(nested, "trace_summary", None)
    )
    if isinstance(nested_trace, dict) and nested_trace:
        return nested_trace
    return direct if isinstance(direct, dict) else {}


def _model_call_observation_payload(
    event: str,
    payload: dict[str, Any],
    *,
    run_log_dir: str,
) -> dict[str, Any]:
    if event not in {
        "api_call_start",
        "api_call_done",
        "api_call_error",
        "api_call_timeout",
        "api_call_incomplete",
        "api_call_empty_response",
        "api_call_length_truncated",
        "api_stream_start",
        "api_stream_done",
        "api_stream_error",
    }:
        return {}
    status = "running" if event.endswith("_start") else "success"
    if event.endswith(("_error", "_timeout", "_incomplete")):
        status = "error"
    if event in {"api_call_empty_response", "api_call_length_truncated"}:
        status = "retrying" if payload.get("will_retry") else "error"
    result: dict[str, Any] = {"event": event, "status": status, "run_log_dir": run_log_dir}
    for key in (
        "call_id",
        "task",
        "provider",
        "model",
        "route",
        "max_tokens",
        "temperature",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_usd",
        "timeout_s",
        "attempt",
        "max_attempts",
        "next_max_tokens",
        "model_output_limit",
        "finish_reason",
        "will_retry",
    ):
        if key in payload:
            result[key] = payload[key]
    return result


def _trace_with_performance(
    trace_summary: dict[str, Any] | None,
    metrics: RunPerformanceMetrics | None,
) -> dict[str, Any]:
    summary = dict(trace_summary or {})
    if metrics is not None:
        summary["performance_metrics"] = metrics.snapshot()
    return summary


async def execute_with_project_logger(
    *,
    runtime: RuntimeServices,
    project_id: str,
    command: str,
    metadata: dict[str, Any],
    on_step: StepCallback,
    execute: Callable[[StepCallback], Awaitable[Any]],
    metrics: RunPerformanceMetrics | None = None,
) -> tuple[Any, str]:
    if metrics is None:
        return await _execute_with_project_logger(
            runtime=runtime,
            project_id=project_id,
            command=command,
            metadata=metadata,
            on_step=on_step,
            execute=execute,
            metrics=None,
        )

    original_storage = runtime.storage
    runtime.storage = CountingStorageProxy(original_storage, metrics)  # type: ignore[assignment]
    try:
        return await _execute_with_project_logger(
            runtime=runtime,
            project_id=project_id,
            command=command,
            metadata=metadata,
            on_step=on_step,
            execute=execute,
            metrics=metrics,
        )
    finally:
        runtime.storage = original_storage


async def _execute_with_project_logger(
    *,
    runtime: RuntimeServices,
    project_id: str,
    command: str,
    metadata: dict[str, Any],
    on_step: StepCallback,
    execute: Callable[[StepCallback], Awaitable[Any]],
    metrics: RunPerformanceMetrics | None = None,
) -> tuple[Any, str]:
    layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    run_logger = ProjectRunLogger(
        layout=layout,
        project_id=project_id,
        command=command,
        keep_runs=runtime.settings.log_keep_runs,
        metadata=metadata,
    )
    step_history: list[str] = []

    def _tracked_step(step: str, data: Any) -> None:
        step_history.append(step)
        if metrics is not None:
            metrics.observe_step(step, data)
        run_logger.log_step(step, data)
        on_step(step, data)

    run_log_payload = {
        "project_id": project_id,
        "command": command,
        "run_id": run_logger.run_id,
        "run_log_dir": str(run_logger.run_dir),
    }
    run_logger.log_step("run_log_started", run_log_payload)
    on_step("run_log_started", run_log_payload)

    def _record_router_event(event: str, payload: dict[str, Any]) -> None:
        if metrics is not None:
            metrics.observe_router_event(event, payload)
        run_logger.record_router_event(event, payload)
        control_plane = getattr(runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.call_ledger.record_router_event(event, payload, project_id=project_id)
        model_call_payload = _model_call_observation_payload(
            event,
            payload,
            run_log_dir=str(run_logger.run_dir),
        )
        if model_call_payload:
            on_step("model_call_update", model_call_payload)

    with run_logger.activate(), runtime.router.observe(_record_router_event):
        try:
            result = await execute(_tracked_step)
            performance_payload = metrics.snapshot() if metrics is not None else {}
            if performance_payload:
                run_logger.log_step("performance_metrics", performance_payload)
                on_step("performance_metrics", performance_payload)
            run_logger.finalize(
                status="success",
                result=result,
                trace_summary=_trace_with_performance(_extract_trace_summary(result), metrics),
            )
            return result, str(run_logger.run_dir)
        except asyncio.CancelledError:
            run_logger.finalize(
                status="cancelled",
                trace_summary=_trace_with_performance(
                    _build_step_trace_summary(step_history),
                    metrics,
                ),
            )
            raise
        except Exception as exc:
            run_logger.finalize(
                status="error",
                trace_summary=_trace_with_performance(
                    _build_step_trace_summary(step_history),
                    metrics,
                ),
                error=exc,
            )
            if isinstance(exc, TTSPayloadError):
                raise TTSPayloadError(
                    {**exc.payload, "run_log_dir": str(run_logger.run_dir)}
                ) from exc
            raise LoggedCommandError(exc, run_log_dir=str(run_logger.run_dir)) from exc


def _accepts_metrics_parameter(func: Any) -> bool:
    try:
        return "metrics" in signature(func).parameters
    except (TypeError, ValueError):
        return False


async def _run_with_project_logger(
    *,
    runtime: RuntimeServices,
    project_id: str,
    command: str,
    metadata: dict[str, Any],
    on_step: StepCallback,
    execute: Callable[[StepCallback], Awaitable[Any]],
    metrics: RunPerformanceMetrics,
) -> tuple[Any, str]:
    kwargs: dict[str, Any] = {
        "runtime": runtime,
        "project_id": project_id,
        "command": command,
        "metadata": metadata,
        "on_step": on_step,
        "execute": execute,
    }
    if _accepts_metrics_parameter(execute_with_project_logger):
        kwargs["metrics"] = metrics
    return await execute_with_project_logger(**kwargs)


class WorkspaceCommandExecutor:
    def prepare(self, command: JobCommand) -> PreparedCommand:
        kind = command.kind
        payload = command.payload
        spec = COMMAND_REGISTRY.get(kind)
        if spec is None:
            raise ValueError(f"Unsupported job kind: {kind.value}")

        request = spec.request_factory(payload, command)
        project_id = spec.project_id(request, command).strip()
        if command.scope == JobScope.SYSTEM:
            if command.project_id.strip() or project_id:
                raise ValueError("系统任务不能绑定作品项目。")
        elif command.kind in {
            JobKind.OLLAMA_RUNTIME_CONTROL,
            JobKind.OLLAMA_PULL_MODEL,
            JobKind.OLLAMA_DELETE_MODEL,
        }:
            raise ValueError("Ollama 管理任务必须使用 system scope。")
        label = spec.label(request, command, project_id)
        command_name = spec.command_name

        command_name = str(command.metadata.get("command_name") or command_name)
        metadata = {"kind": kind.value, **_dump_request(request), **command.metadata}
        return PreparedCommand(
            kind=kind,
            request=request,
            label=label,
            project_id=command.project_id.strip() or project_id,
            command_name=command_name,
            metadata=metadata,
        )

    async def run(
        self,
        prepared: PreparedCommand,
        runtime: RuntimeServices,
        on_step: StepCallback,
    ) -> dict[str, Any]:
        metrics = RunPerformanceMetrics()
        spec = COMMAND_REGISTRY.get(prepared.kind)
        if spec is None:
            raise ValueError(f"Unsupported job kind: {prepared.kind.value}")
        payload, run_log_dir = await spec.run_handler(prepared, runtime, on_step, metrics)

        payload.setdefault("project_id", prepared.project_id)
        chapter_number = getattr(prepared.request, "chapter_number", None)
        if chapter_number is not None:
            payload.setdefault("chapter_number", chapter_number)
        payload["run_log_dir"] = run_log_dir
        return payload
