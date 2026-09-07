"""Preflight failure classification and remediation guidance.

Extracted from execution.py to isolate preflight check enrichment logic.
"""

from __future__ import annotations

from typing import Any

from novel_forge.tts.platform.schemas import (
    AudioExecutionStage,
    AudioPreflightCheck,
)

# Stages whose failure blocks synthesis outright (no graceful degradation).
_HARD_FAILURE_STAGES = {
    AudioExecutionStage.TTS_FORMAL,
    AudioExecutionStage.RENDERER,
    AudioExecutionStage.QUALITY,
    AudioExecutionStage.VOICE_DESIGN,
    AudioExecutionStage.VOICE_CLONE,
    AudioExecutionStage.TTS_PREVIEW,
}


def _preflight_failure_severity(stage: AudioExecutionStage | None, kind: str) -> str:
    """Classify a failed preflight check as ``hard`` or ``soft``.

    Hard failures are synthesis-blocking (missing TTS route, renderer, quality
    engine, credentials, privacy).  Soft failures are alignment-related and
    non-blocking -- the downstream step degrades gracefully.
    """
    if stage is not None and stage in _HARD_FAILURE_STAGES:
        return "hard"
    if kind in {"credential", "privacy", "plan_frozen", "budget", "license", "license_acceptance"}:
        return "hard"
    return "soft"


def _preflight_failure_guidance(stage: AudioExecutionStage | None, kind: str) -> str:
    """Return a concrete remediation hint for a failed preflight check."""
    if kind == "credential":
        return "请在「平台设置」中补齐对应服务的 API Key 或访问令牌。"
    if kind == "privacy":
        return "请在「平台设置」中调整位置/隐私边界，或更换为符合隐私策略的模型。"
    if kind == "plan_frozen":
        return "请重新生成并冻结音频执行计划。"
    if kind == "license" or kind == "license_acceptance":
        return "请在「本机音频模型中心」接受对应模型的许可证条款。"
    if stage == AudioExecutionStage.TTS_FORMAL:
        return "正式人声合成路由不可用；请检查 TTS provider 配置与网络。"
    if stage == AudioExecutionStage.RENDERER or stage == AudioExecutionStage.QUALITY:
        return "内置 FFmpeg 渲染/质检运行时不可用；请检查应用完整性或重装。"
    if stage in {AudioExecutionStage.ASR, AudioExecutionStage.ALIGN}:
        return "对齐 sidecar 离线，时间线将降级为片段近似；如需精确对齐，请在「本机音频模型中心」启动。"
    if stage in {
        AudioExecutionStage.SFX,
        AudioExecutionStage.MUSIC,
        AudioExecutionStage.SOUNDSCAPE,
    }:
        return "声音生成运行时不可用；BGM/声场需配置 MiniMax API Key，SFX 需安装 Stable Audio CLI。"
    return "请检查对应路由、凭据与本地资源后重试。"


def _enrich_preflight_failure(check: AudioPreflightCheck) -> dict[str, Any]:
    """Add ``severity`` and ``guidance`` to a failed check for UI display."""
    payload = check.model_dump(mode="json")
    payload["severity"] = _preflight_failure_severity(check.stage, check.kind)
    payload["guidance"] = _preflight_failure_guidance(check.stage, check.kind)
    return payload
