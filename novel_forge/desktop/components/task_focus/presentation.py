"""Shared labels and formatting for task-observation widgets."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import QLabel, QSizePolicy

from novel_forge.desktop.task_observation import ObservedStreamState

_MODEL_BAR_LABEL_CHARS = 30
_MODEL_BAR_LABEL_WIDTH = 220


def parse_datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def elapsed_text(created_at: str) -> str:
    start = parse_datetime(created_at)
    if start is None:
        return ""
    seconds = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_cost(value: float) -> str:
    if value <= 0:
        return ""
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def format_count(value: int) -> str:
    return f"{value:,}"


def format_latency(ms: float) -> str:
    if ms <= 0:
        return ""
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def compact_label(value: str, *, limit: int = _MODEL_BAR_LABEL_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def constrain_label_width(label: QLabel, *, wrap: bool = True) -> None:
    label.setMinimumWidth(0)
    label.setWordWrap(wrap)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


def stream_notice(stream: ObservedStreamState | None) -> str:
    if stream is None:
        return "等待当前节点输出。"
    if stream.source == "model_call":
        return {
            "running": "正在请求模型；完成后会自动展示本次结果。",
            "complete": "模型调用已完成，以下为该次响应预览。",
            "error": "模型调用失败，以下为错误预览。",
        }.get(stream.status, "模型调用状态已记录。")
    return {
        "streaming": "预览中，未校验，未写入磁盘。",
        "complete": "模型输出已结束；没有后端校验结论时仍只作预览。",
        "validating": "模型输出已结束，正在进行后端结构校验。",
        "repairing": "后端正在修复结构化输出；当前内容仍是草稿。",
        "retrying": "结构化输出未通过，本次操作正在重试。",
        "validated": "后端校验已通过；正式写入仍由主流程决定。",
        "validation_failed": "后端校验未通过；以下片段仅作诊断证据。",
        "restarted": "本次预览已丢弃，正在等待新的重试输出。",
        "error": "流式输出出错；失败片段不会写入磁盘。",
    }.get(stream.status, "当前节点输出已记录为 UI 预览。")


def stream_status_label(status: str) -> str:
    return {
        "running": "调用中",
        "streaming": "输出中",
        "complete": "完成",
        "validating": "校验中",
        "repairing": "修复中",
        "retrying": "重试中",
        "validated": "已校验",
        "validation_failed": "校验失败",
        "error": "错误",
        "restarted": "已重试",
    }.get(str(status or "").strip(), str(status or "记录"))


def stream_duration_text(stream: ObservedStreamState) -> str:
    start = parse_datetime(stream.started_at)
    end = parse_datetime(stream.ended_at or stream.updated_at)
    if start is None or end is None:
        return ""
    seconds = max(0.0, (end - start).total_seconds())
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def stream_history_label(
    stream: ObservedStreamState,
    *,
    ordinal: int,
    latest_stream_id: str,
) -> str:
    parts = [
        f"{ordinal:03d}",
        stream.task or "文本生成",
        f"第 {stream.attempt} 轮" if stream.attempt else "",
        stream_status_label(stream.status),
        f"{stream.text_length or len(stream.text)} 字",
        f"思考 {stream.reasoning_length:,} 字" if stream.reasoning_length else "",
        stream.model or stream.provider,
        stream_duration_text(stream),
        "最新" if stream.stream_id == latest_stream_id else "",
    ]
    return " · ".join(part for part in parts if part)


def job_kind_label(kind: str) -> str:
    return {
        "run_chapter": "章节",
        "prepare_chapter": "预备",
        "polish_chapter": "润色",
        "init_long": "立项",
        "run_short": "短篇",
        "book_consistency": "全书",
        "export_book": "导出",
        "voice_studio": "声腔",
    }.get(str(kind or "").strip(), str(kind or "任务").replace("_", " "))


# Compatibility aliases kept local to this small presentation contract.
_parse_dt = parse_datetime
_elapsed_text = elapsed_text
_fmt_cost = format_cost
_fmt_count = format_count
_fmt_latency = format_latency
_compact_label = compact_label
_constrain_label_width = constrain_label_width
_stream_notice = stream_notice
_stream_status_label = stream_status_label
_stream_duration_text = stream_duration_text
_stream_history_label = stream_history_label
_job_kind_label = job_kind_label
