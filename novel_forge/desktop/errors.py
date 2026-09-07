"""Structured desktop error summaries for UI-facing diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from pydantic import ValidationError

from novel_forge.core.exceptions import (
    ChapterSessionStaleError,
    FinalReportFreshnessError,
    ModelGatewayError,
    NovelForgeException,
    RuntimeConfigError,
)
from novel_forge.gateway.factory import TaskRoutingParseError


def _is_tts_post_archive_error(exc: BaseException) -> bool:
    """Lazy isinstance check to avoid importing workspace types at module level."""
    try:
        from novel_forge.workspace.post_archive_tts import TTSPostArchiveError

        return isinstance(exc, TTSPostArchiveError)
    except ImportError:
        return False


class DesktopErrorCategory(str, Enum):
    VALIDATION = "validation"
    MODEL = "model"
    CONFIGURATION = "configuration"
    FILESYSTEM = "filesystem"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class DesktopErrorSummary:
    """User-facing error summary with preserved developer detail."""

    category: DesktopErrorCategory
    title: str
    summary: str
    detail: str
    exception_type: str
    chain: list[str] = field(default_factory=list)
    retryable: bool = False
    recovery_actions: list[dict[str, str]] = field(default_factory=list)
    error_code: str = ""

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def summarize_desktop_error(exc: BaseException) -> DesktopErrorSummary:
    """Classify a raw exception for desktop display and diagnostics."""
    detail = str(exc).strip() or type(exc).__name__
    chain = _build_exception_chain(exc)

    if isinstance(exc, ValidationError):
        return DesktopErrorSummary(
            category=DesktopErrorCategory.VALIDATION,
            title="输入或校验错误",
            summary="提交参数未通过校验，请检查必填项和数值范围。",
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
        )
    if isinstance(exc, (RuntimeConfigError, TaskRoutingParseError, ValueError)) and _looks_like_config_error(exc):
        return DesktopErrorSummary(
            category=DesktopErrorCategory.CONFIGURATION,
            title="配置错误",
            summary="当前模型或运行配置不可用，任务未启动。",
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
        )
    if isinstance(exc, json.JSONDecodeError):
        return DesktopErrorSummary(
            category=DesktopErrorCategory.MODEL,
            title="AI 输出 JSON 格式错误",
            summary=(
                f"模型返回的内容无法解析为合法 JSON，可重试；若频繁出现请反馈。"
                f"\n位置：第 {exc.lineno} 行第 {exc.colno} 列（字符 {exc.pos}）"
                f"\n原因：{exc.msg}"
            ),
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
        )
    if _looks_like_init_coherence_error(exc, chain):
        summary = (
            "初始化一致性校验未通过，需要修正已生成的蓝图、大纲或章节契约后再继续。"
            "请查看错误详情和运行日志；这不是 API Key 或网络故障。"
        )
        if _looks_like_init_coherence_recheck_limit(exc, chain):
            summary = (
                "章节契约一致性复检范围过大，系统已停止自动复检以避免循环消耗。"
                "请查看错误详情并修正 chapter_contracts.json，或调整复检范围后重跑 init-long；"
                "这不是 API Key 或网络故障。"
            )
        return DesktopErrorSummary(
            category=DesktopErrorCategory.VALIDATION,
            title="初始化一致性校验未通过",
            summary=summary,
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
        )
    if isinstance(exc, FinalReportFreshnessError):
        return DesktopErrorSummary(
            category=DesktopErrorCategory.VALIDATION,
            title="终稿质量报告未就绪",
            summary="终稿已保留，但质量报告与最终正文不一致，因此未归档。",
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
            error_code=exc.error_code,
            recovery_actions=list(exc.context.get("recovery_actions") or []),
        )
    if isinstance(exc, ChapterSessionStaleError):
        context = exc.context or {}
        return DesktopErrorSummary(
            category=DesktopErrorCategory.VALIDATION,
            title="章节工作台状态已变化",
            summary=exc.message,
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
            retryable=bool(context.get("retryable", True)),
            error_code=exc.error_code,
            recovery_actions=list(context.get("recovery_actions") or []),
        )
    if _is_tts_post_archive_error(exc):
        # Lazy import keeps the workspace layer off the desktop import path.
        from novel_forge.workspace.post_archive_tts import TTSPostArchiveError

        tts_exc: TTSPostArchiveError = exc  # type: ignore[assignment]
        recovery_actions = _tts_recovery_actions(tts_exc)
        summary = tts_exc.args[0] if tts_exc.args else str(exc).strip()
        return DesktopErrorSummary(
            category=DesktopErrorCategory.RUNTIME,
            title="自动配音未就绪",
            summary=summary,
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
            error_code=tts_exc.error_code,
            recovery_actions=recovery_actions,
        )
    if isinstance(exc, ModelGatewayError) or _looks_like_model_error(exc):
        context = getattr(exc, "context", {}) if isinstance(exc, ModelGatewayError) else {}
        final_review_failure = context.get("stage") == "finalize_report_refresh"
        return DesktopErrorSummary(
            category=DesktopErrorCategory.MODEL,
            title="终稿质量复检暂停" if final_review_failure else "模型调用失败",
            summary=(
                "终稿已保留且未归档。模型路由恢复后，请重试终稿质量复检。"
                if final_review_failure
                else "模型服务未正常返回，请检查 API Key、路由和网络状态。"
            ),
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
            retryable=bool(context.get("retryable", False)),
            error_code=getattr(exc, "error_code", ""),
            recovery_actions=list(context.get("recovery_actions") or []),
        )
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return DesktopErrorSummary(
            category=DesktopErrorCategory.FILESYSTEM,
            title="文件系统错误",
            summary="读取或写入项目文件时失败，请检查路径、权限和磁盘状态。",
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
        )
    if isinstance(exc, NovelForgeException):
        # Structured framework exception that did not match a specialized branch
        # above: preserve its error_code and context so downstream consumers
        # (chapter studio auto-refresh, task-flow panels) can branch on it.
        context = exc.context or {}
        return DesktopErrorSummary(
            category=DesktopErrorCategory.RUNTIME,
            title="任务失败",
            summary=f"{type(exc).__name__}: {exc.message[:200]}",
            detail=detail,
            exception_type=type(exc).__name__,
            chain=chain,
            retryable=bool(context.get("retryable", False)),
            error_code=exc.error_code,
            recovery_actions=[
                dict(item) for item in (context.get("recovery_actions") or []) if isinstance(item, dict)
            ],
        )
    return DesktopErrorSummary(
        category=DesktopErrorCategory.RUNTIME,
        title="运行时内部错误",
        summary=f"任务在执行过程中异常中断。\n{type(exc).__name__}：{detail[:200]}",
        detail=detail,
        exception_type=type(exc).__name__,
        chain=chain,
    )


def _exception_search_text(exc: BaseException, chain: list[str]) -> str:
    return "\n".join([f"{type(exc).__name__}: {exc}", *chain]).lower()


def _looks_like_init_coherence_error(exc: BaseException, chain: list[str]) -> bool:
    text = _exception_search_text(exc, chain)
    markers = (
        "initcoherenceerror",
        "初始化准入阻断",
        "一致性裁判未通过",
        "章节契约一致性裁判未通过",
        "adjudicate_contract_coherence_failed",
        "contract_coherence_focus_recheck_claim_limit",
        "focus_recheck_claim_limit",
        "init_coherence_recheck_scope_stopped",
    )
    return any(marker in text for marker in markers)


def _looks_like_init_coherence_recheck_limit(exc: BaseException, chain: list[str]) -> bool:
    text = _exception_search_text(exc, chain)
    return (
        "focus_recheck_claim_limit" in text
        or "局部复检 claims" in text
        and "超过上限" in text
    )


def _build_exception_chain(exc: BaseException) -> list[str]:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        text = str(current).strip() or type(current).__name__
        parts.append(f"{type(current).__name__}: {text}")
        current = current.__cause__ or current.__context__
    return parts


def _looks_like_model_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "api key",
        "authentication",
        "unauthorized",
        "rate limit",
        "model_not_found",
        "timeout",
        "timed out",
        "connection",
        "network",
        "provider",
    )
    return any(marker in text for marker in markers)


def _looks_like_config_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "config",
        "routing",
        "provider",
        "model",
        "json",
        "setting",
        "env",
    )
    return any(marker in text for marker in markers)


def _tts_recovery_actions(exc: BaseException) -> list[dict[str, str]]:
    """Build recovery actions for TTS post-archive failures based on ``missing_artifact``."""
    missing_artifact = str(getattr(exc, "missing_artifact", "") or "").strip()
    actions: list[dict[str, str]] = []
    if missing_artifact == "narrator_voice_profile":
        actions.append(
            {
                "action": "build_narrator_profile",
                "description": "请先在声腔工作室构建或确认旁白音色。",
            }
        )
    elif missing_artifact == "voice_team":
        diag_count = len(getattr(exc, "diagnoses", []) or [])
        actions.append(
            {
                "action": "build_voice_team",
                "description": (
                    f"请先在声腔工作室构建或确认配音团队（{diag_count} 项诊断）。"
                    if diag_count
                    else "请先在声腔工作室构建或确认配音团队。"
                ),
            }
        )
    if not actions:
        actions.append(
            {
                "action": "open_voice_studio",
                "description": "请前往声腔工作室检查配音团队状态。",
            }
        )
    return actions
