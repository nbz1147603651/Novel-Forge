"""Deterministic preflight for a frozen project audio execution plan."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from novel_forge.core.config import Settings
from novel_forge.tts.model_center.manager import (
    ApplicationAudioModelManager,
    AudioModelCenterError,
)
from novel_forge.tts.model_center.schemas import (
    AudioModelInstallState,
    AudioModelSource,
)
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import (
    AudioExecution,
    AudioExecutionPlan,
    AudioExecutionStage,
    AudioLocationPolicy,
    AudioMemoryClass,
    AudioPreflightCheck,
    AudioPreflightReport,
    AudioPreflightStatus,
)
from novel_forge.tts.platform.sidecar import AudioSidecarClient
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.sound_generation.model_manager import StableAudioModelManager

_MEMORY_RANK = {
    AudioMemoryClass.LIGHT: 0,
    AudioMemoryClass.MEDIUM: 1,
    AudioMemoryClass.HIGH: 2,
}

_CORE_STAGES = {
    AudioExecutionStage.TTS_FORMAL,
    AudioExecutionStage.RENDERER,
    AudioExecutionStage.QUALITY,
}

# Stages whose sidecar is a *soft* dependency: the downstream step
# (``AlignSpeechTimelineStep``) already degrades gracefully when these are
# offline -- ASR/ALIGN failures fall back to a segment-scoped timeline with
# ``coverage=0.0`` rather than aborting synthesis.  A live-probe connection
# failure for these stages therefore becomes a ``WARNING`` (visible but
# non-blocking) instead of a hard ``FAILED``.  Hard stages (TTS_FORMAL,
# RENDERER, QUALITY) keep ``FAILED`` because synthesis cannot proceed without
# them.
_SOFT_DEPENDENCY_STAGES = {
    AudioExecutionStage.ASR,
    AudioExecutionStage.ALIGN,
}


class _SidecarProbe(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def self_test(self, *, model_id: str = "") -> dict[str, Any]: ...

    async def version(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class _SidecarFactory(Protocol):
    def __call__(self, base_url: str, *, api_key: str = "") -> _SidecarProbe: ...


def preflight_audio_execution_plan(
    plan: AudioExecutionPlan,
    *,
    settings: Settings,
    registry: AudioPluginRegistry,
    active_stages: Iterable[AudioExecutionStage] = (),
    model_manager: ApplicationAudioModelManager | None = None,
) -> AudioPreflightReport:
    """Check immutable routes without contacting a provider or exposing secrets.

    Provider adapters and sidecars may append live health/version/model-install
    evidence later. This pass enforces the hard privacy and credential boundary
    before any request is sent.
    """

    active = set(active_stages) | _CORE_STAGES
    managed_models = model_manager or ApplicationAudioModelManager(settings)
    checks: list[AudioPreflightCheck] = []
    checks.append(
        AudioPreflightCheck(
            kind="plan_frozen",
            status=(
                AudioPreflightStatus.PASSED
                if plan.frozen and plan.manifest_digest
                else AudioPreflightStatus.FAILED
            ),
            message=(
                "执行计划已冻结并带有清单摘要。"
                if plan.frozen and plan.manifest_digest
                else "执行计划尚未冻结，不能进入正式生产。"
            ),
        )
    )
    for assignment in plan.routes:
        required = assignment.stage in active
        primary = assignment.primary
        if primary is None:
            checks.append(
                AudioPreflightCheck(
                    kind="route",
                    status=(
                        AudioPreflightStatus.FAILED if required else AudioPreflightStatus.WARNING
                    ),
                    message=f"{assignment.stage.value} 没有可执行主路由。",
                    stage=assignment.stage,
                )
            )
            continue
        manifest = registry.get(primary.plugin_id)
        if manifest is None:
            checks.append(
                AudioPreflightCheck(
                    kind="plugin_manifest",
                    status=AudioPreflightStatus.FAILED,
                    message="冻结计划引用的插件清单已不存在。",
                    stage=assignment.stage,
                    plugin_id=primary.plugin_id,
                )
            )
            continue
        location_invalid = (
            plan.location_policy == AudioLocationPolicy.LOCAL_ONLY and manifest.runtime.is_cloud
        ) or (
            plan.location_policy == AudioLocationPolicy.CLOUD_ONLY and not manifest.runtime.is_cloud
        )
        checks.append(
            AudioPreflightCheck(
                kind="privacy_location",
                status=(
                    AudioPreflightStatus.FAILED if location_invalid else AudioPreflightStatus.PASSED
                ),
                message=(
                    "主路由违反位置策略。" if location_invalid else "主路由符合位置与隐私边界。"
                ),
                stage=assignment.stage,
                plugin_id=primary.plugin_id,
            )
        )
        missing_languages = [
            language
            for language in assignment.languages
            if not manifest.capabilities.supports_language(language)
        ]
        checks.append(
            AudioPreflightCheck(
                kind="language",
                status=(
                    AudioPreflightStatus.FAILED
                    if required and missing_languages
                    else (
                        AudioPreflightStatus.WARNING
                        if missing_languages
                        else AudioPreflightStatus.PASSED
                    )
                ),
                message=(
                    f"未声明支持语言：{', '.join(missing_languages)}。"
                    if missing_languages
                    else "语言能力匹配。"
                ),
                stage=assignment.stage,
                plugin_id=primary.plugin_id,
            )
        )
        if manifest.runtime.is_cloud:
            has_credential = any(
                bool(str(getattr(settings, field, "") or "").strip())
                for field in manifest.runtime.credential_settings
            )
            checks.append(
                AudioPreflightCheck(
                    kind="credential",
                    status=(
                        AudioPreflightStatus.PASSED
                        if has_credential
                        else (
                            AudioPreflightStatus.FAILED
                            if required
                            else AudioPreflightStatus.WARNING
                        )
                    ),
                    message=(
                        "云端凭据已配置（未写入执行计划）。"
                        if has_credential
                        else "云端路由缺少凭据。"
                    ),
                    stage=assignment.stage,
                    plugin_id=primary.plugin_id,
                )
            )
        memory_exceeded = not manifest.runtime.is_cloud and _MEMORY_RANK[
            manifest.runtime.memory_class
        ] > min(
            _MEMORY_RANK[plan.memory_budget],
            _MEMORY_RANK[plan.hardware.memory_class],
        )
        checks.append(
            AudioPreflightCheck(
                kind="memory",
                status=(
                    AudioPreflightStatus.FAILED
                    if required and memory_exceeded
                    else (
                        AudioPreflightStatus.WARNING
                        if memory_exceeded
                        else AudioPreflightStatus.PASSED
                    )
                ),
                message=(
                    "本地模型内存等级高于检测到的设备预算。"
                    if memory_exceeded
                    else "内存等级满足声明需求。"
                ),
                stage=assignment.stage,
                plugin_id=primary.plugin_id,
            )
        )
        checks.append(
            AudioPreflightCheck(
                kind="license",
                status=(
                    AudioPreflightStatus.PASSED
                    if manifest.license_summary
                    else AudioPreflightStatus.WARNING
                ),
                message=manifest.license_summary or "插件未声明许可证摘要，发布前需复核。",
                stage=assignment.stage,
                plugin_id=primary.plugin_id,
            )
        )
        if required and primary.execution not in {
            AudioExecution.CLOUD_API,
            AudioExecution.BUILTIN,
        }:
            try:
                model_status = managed_models.model_status(primary.plugin_id)
            except AudioModelCenterError:
                model_status = None
            if model_status is not None:
                externally_managed = model_status.descriptor.source == AudioModelSource.SIDECAR
                installed = model_status.state == AudioModelInstallState.INSTALLED
                checks.append(
                    AudioPreflightCheck(
                        kind="model_install",
                        status=(
                            AudioPreflightStatus.WARNING
                            if externally_managed
                            else (
                                AudioPreflightStatus.PASSED
                                if installed
                                else AudioPreflightStatus.FAILED
                            )
                        ),
                        message=(
                            "模型由 sidecar 管理，将以实时自检确认可用性。"
                            if externally_managed
                            else (
                                "本地模型已安装并通过文件校验。"
                                if installed
                                else "本地模型尚未安装或文件不完整。"
                            )
                        ),
                        stage=assignment.stage,
                        plugin_id=primary.plugin_id,
                        details={
                            "state": model_status.state.value,
                            "model_id": model_status.descriptor.model_id,
                            "local_path": model_status.local_path,
                        },
                    )
                )
                if model_status.descriptor.requires_license_acceptance:
                    checks.append(
                        AudioPreflightCheck(
                            kind="license_acceptance",
                            status=(
                                AudioPreflightStatus.PASSED
                                if model_status.license_accepted
                                else AudioPreflightStatus.FAILED
                            ),
                            message=(
                                "本地模型许可已明确接受。"
                                if model_status.license_accepted
                                else "必须先在音频模型中心明确接受该模型许可。"
                            ),
                            stage=assignment.stage,
                            plugin_id=primary.plugin_id,
                        )
                    )
        for fallback in assignment.fallbacks:
            fallback_invalid = (
                plan.location_policy == AudioLocationPolicy.LOCAL_ONLY and not fallback.offline
            ) or (plan.location_policy == AudioLocationPolicy.CLOUD_ONLY and fallback.offline)
            if fallback_invalid:
                checks.append(
                    AudioPreflightCheck(
                        kind="fallback_privacy",
                        status=AudioPreflightStatus.FAILED,
                        message="回退链越过了当前位置/隐私边界。",
                        stage=assignment.stage,
                        plugin_id=fallback.plugin_id,
                    )
                )
            if fallback.voice_mapping_required:
                checks.append(
                    AudioPreflightCheck(
                        kind="fallback_voice_mapping",
                        status=AudioPreflightStatus.WARNING,
                        message="跨平台人声回退必须先提供该平台的备用音色映射。",
                        stage=assignment.stage,
                        plugin_id=fallback.plugin_id,
                    )
                )
    budget_message = (
        f"单章预算上限为 ${plan.budget_limit_usd:.2f}；运行时继续累计实际费用。"
        if plan.budget_limit_usd > 0
        else "未设置单章云音频预算上限；仍会记录每段实际费用。"
    )
    checks.append(
        AudioPreflightCheck(
            kind="budget",
            status=(
                AudioPreflightStatus.PASSED
                if plan.budget_limit_usd > 0
                else AudioPreflightStatus.WARNING
            ),
            message=budget_message,
        )
    )
    return AudioPreflightReport(checks=checks)


def _probe_payload_passed(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False or payload.get("passed") is False:
        return False
    status = str(payload.get("status") or payload.get("state") or "").strip().lower()
    return status not in {"error", "failed", "unhealthy", "not_ready"}


async def run_live_audio_preflight(
    plan: AudioExecutionPlan,
    report: AudioPreflightReport,
    *,
    settings: Settings,
    active_stages: Iterable[AudioExecutionStage] = (),
    sidecar_factory: _SidecarFactory = AudioSidecarClient,
) -> AudioPreflightReport:
    """Append live sidecar, CLI and bundled-runtime evidence to the report."""

    active = set(active_stages) | _CORE_STAGES

    async def probe_sidecar(
        stage: AudioExecutionStage,
        plugin_id: str,
        endpoint: str,
        model_id: str,
        api_key: str,
    ) -> list[AudioPreflightCheck]:
        # Soft-dependency stages (ASR/ALIGN) degrade gracefully when offline --
        # ``AlignSpeechTimelineStep`` falls back to a segment-scoped timeline.
        # A connection failure is surfaced as WARNING (visible, non-blocking)
        # so a purely cloud-TTS project is not blocked by a local alignment
        # sidecar it never actually needs.  Hard stages keep FAILED.
        is_soft = stage in _SOFT_DEPENDENCY_STAGES
        if not endpoint:
            return [
                AudioPreflightCheck(
                    kind="health",
                    status=(
                        AudioPreflightStatus.WARNING if is_soft else AudioPreflightStatus.FAILED
                    ),
                    message=(
                        "对齐 sidecar 未配置端点；时间线将降级为片段近似，不影响合成。"
                        if is_soft
                        else "本地 sidecar 路由没有冻结端点。"
                    ),
                    stage=stage,
                    plugin_id=plugin_id,
                )
            ]
        client = sidecar_factory(endpoint, api_key=api_key)
        try:
            health, self_test, version = await asyncio.gather(
                client.health(),
                client.self_test(model_id=model_id),
                client.version(),
            )
            health_ok = _probe_payload_passed(health)
            model_ok = _probe_payload_passed(self_test)
            return [
                AudioPreflightCheck(
                    kind="health",
                    status=(
                        AudioPreflightStatus.PASSED if health_ok else AudioPreflightStatus.FAILED
                    ),
                    message=(
                        "本地 sidecar 健康检查通过。" if health_ok else "本地 sidecar 报告未就绪。"
                    ),
                    stage=stage,
                    plugin_id=plugin_id,
                    details={"endpoint": endpoint, "health": health, "version": version},
                ),
                AudioPreflightCheck(
                    kind="model_self_test",
                    status=(
                        AudioPreflightStatus.PASSED if model_ok else AudioPreflightStatus.FAILED
                    ),
                    message=(
                        "冻结模型通过 sidecar 自检。"
                        if model_ok
                        else "冻结模型未通过 sidecar 自检。"
                    ),
                    stage=stage,
                    plugin_id=plugin_id,
                    details={"model_id": model_id, "self_test": self_test},
                ),
            ]
        except Exception as exc:
            if is_soft:
                return [
                    AudioPreflightCheck(
                        kind="health",
                        status=AudioPreflightStatus.WARNING,
                        message=(
                            f"对齐 sidecar 离线（{str(exc)[:200]}）；"
                            "时间线将降级为片段近似，不影响合成。"
                            "如需精确对齐，请在「本机音频模型中心」启动对应 sidecar。"
                        ),
                        stage=stage,
                        plugin_id=plugin_id,
                        details={"endpoint": endpoint, "model_id": model_id},
                    )
                ]
            return [
                AudioPreflightCheck(
                    kind="health",
                    status=AudioPreflightStatus.FAILED,
                    message=f"本地 sidecar 无法连接或自检失败：{str(exc)[:300]}",
                    stage=stage,
                    plugin_id=plugin_id,
                    details={"endpoint": endpoint, "model_id": model_id},
                )
            ]
        finally:
            await client.aclose()

    sidecar_tasks = []
    for route in plan.routes:
        primary = route.primary
        if route.stage not in active or primary is None:
            continue
        if primary.execution == AudioExecution.LOCAL_SIDECAR:
            api_key = next(
                (
                    str(getattr(settings, field, "") or "").strip()
                    for field in primary.credential_settings
                    if str(getattr(settings, field, "") or "").strip()
                ),
                "",
            )
            sidecar_tasks.append(
                probe_sidecar(
                    route.stage,
                    primary.plugin_id,
                    primary.endpoint,
                    primary.model_id,
                    api_key,
                )
            )
        elif primary.execution == AudioExecution.LOCAL_CLI:
            if primary.provider_id == "stable_audio":
                runtime = StableAudioModelManager(
                    models_dir=settings.sound_generation_stable_audio_models_dir,
                    command=settings.sound_generation_stable_audio_command,
                ).runtime_status()
                report.checks.append(
                    AudioPreflightCheck(
                        kind="runtime",
                        status=(
                            AudioPreflightStatus.PASSED
                            if runtime.cli_available
                            else AudioPreflightStatus.FAILED
                        ),
                        message=runtime.detail,
                        stage=route.stage,
                        plugin_id=primary.plugin_id,
                        details={"command": runtime.command},
                    )
                )
        elif primary.execution == AudioExecution.BUILTIN and route.stage in {
            AudioExecutionStage.RENDERER,
            AudioExecutionStage.QUALITY,
        }:
            try:
                executable = Path(ffmpeg_executable())
                available = executable.is_file()
            except Exception as exc:
                executable = Path("")
                available = False
                error = str(exc)[:300]
            else:
                error = ""
            report.checks.append(
                AudioPreflightCheck(
                    kind="runtime",
                    status=(
                        AudioPreflightStatus.PASSED if available else AudioPreflightStatus.FAILED
                    ),
                    message=(
                        "内置 FFmpeg 渲染/质检运行时可用。"
                        if available
                        else f"内置 FFmpeg 不可用：{error or '未找到可执行文件'}"
                    ),
                    stage=route.stage,
                    plugin_id=primary.plugin_id,
                    details={"executable": str(executable) if available else ""},
                )
            )

    if sidecar_tasks:
        for checks in await asyncio.gather(*sidecar_tasks):
            report.checks.extend(checks)
    return report
