from __future__ import annotations

from novel_forge.core.exceptions import FinalReportFreshnessError, ModelGatewayError
from novel_forge.desktop.errors import DesktopErrorCategory, summarize_desktop_error


class InitCoherenceError(RuntimeError):
    """Local stand-in; desktop classification relies on the exception chain name."""


def test_init_coherence_recheck_limit_is_not_reported_as_model_service_error() -> None:
    try:
        try:
            raise InitCoherenceError(
                "章节契约一致性裁判未通过：contract_coherence 局部复检 claims=753 "
                "超过上限 240，已停止自动复检以避免循环消耗。\n"
                "修复建议：在 .env 中配置其他 provider 作为备用路由。"
            )
        except InitCoherenceError as exc:
            raise RuntimeError(f"{exc}\n运行日志: data/魂玉/logs/run") from exc
    except RuntimeError as exc:
        summary = summarize_desktop_error(exc)

    assert summary.category == DesktopErrorCategory.VALIDATION
    assert summary.title == "初始化一致性校验未通过"
    assert "复检范围过大" in summary.summary
    assert "不是 API Key 或网络故障" in summary.summary
    assert "模型服务未正常返回" not in summary.summary
    assert "运行日志: data/魂玉/logs/run" in summary.detail


def test_model_gateway_error_keeps_model_service_summary() -> None:
    summary = summarize_desktop_error(ModelGatewayError("provider timeout", is_transient=True))

    assert summary.category == DesktopErrorCategory.MODEL
    assert summary.title == "模型调用失败"
    assert "模型服务未正常返回" in summary.summary


def test_transient_final_review_gateway_error_exposes_retry_recovery() -> None:
    exc = ModelGatewayError(
        "provider timeout",
        is_transient=True,
        context={
            "stage": "finalize_report_refresh",
            "retryable": True,
            "recovery_actions": [
                {"action": "retry_final_review", "description": "重试终稿质量复检。"}
            ],
        },
    )

    summary = summarize_desktop_error(exc)

    assert summary.title == "终稿质量复检暂停"
    assert summary.retryable is True
    assert summary.recovery_actions[0]["action"] == "retry_final_review"


def test_final_report_freshness_error_explains_archive_block() -> None:
    exc = FinalReportFreshnessError(
        chapter_number=3,
        expected_text_hash="abc",
        dimensions=["continuity"],
        detail="连贯性报告过期",
    )

    summary = summarize_desktop_error(exc)

    assert summary.category == DesktopErrorCategory.VALIDATION
    assert summary.title == "终稿质量报告未就绪"
    assert "未归档" in summary.summary
    assert summary.recovery_actions
