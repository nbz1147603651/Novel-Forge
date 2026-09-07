"""Shared rich-text rendering for every Desktop streaming surface."""

from __future__ import annotations

import html
import json
from enum import StrEnum
from typing import Any

from novel_forge.desktop.task_observation import StreamSegment

_STREAM_DISPLAY_LIMIT = 50_000
_JSON_INLINE_STRING_LIMIT = 180
_JSON_LONG_STRING_LIMIT = 1800
_STREAM_P_STYLE = (
    "margin: 0 0 5px 0; line-height: 1.58; white-space: normal; "
    "word-break: break-all; overflow-wrap: anywhere;"
)
_STREAM_INDENTED_P_STYLE = (
    "text-indent: 2em; margin: 0 0 0.32em 0; line-height: 1.62; "
    "word-break: break-all; overflow-wrap: anywhere;"
)
_STREAM_BLANK_P_STYLE = "margin: 0 0 2px 0; line-height: 1.1;"
_STREAM_JSON_BREAK_STYLE = "word-break: break-all; overflow-wrap: anywhere;"


class StreamRenderKind(StrEnum):
    TEXT = "text"
    JSON = "json"
    JSON_PARTIAL = "json_partial"
    REPORT = "report"

    @property
    def label(self) -> str:
        return {
            StreamRenderKind.TEXT: "文本",
            StreamRenderKind.JSON: "JSON",
            StreamRenderKind.JSON_PARTIAL: "JSON 片段",
            StreamRenderKind.REPORT: "结构化报告",
        }[self]


def _compact_text_lines(text: str) -> list[str | None]:
    rows: list[str | None] = []
    blank_pending = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line:
            rows.append(line)
            blank_pending = False
        elif rows and not blank_pending:
            rows.append(None)
            blank_pending = True
    return rows


def detect_stream_render_kind(text: str) -> StreamRenderKind:
    """Detect the renderer without requiring callers to know output contracts."""

    _prefix, candidate = _split_json_candidate(text)
    if not candidate:
        return StreamRenderKind.TEXT
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return StreamRenderKind.JSON_PARTIAL
    if isinstance(parsed, dict) and (
        any(
            key in parsed
            for key in (
                "overall_score",
                "alignment_score",
                "continuity_score",
                "causal_score",
                "humanize_score",
            )
        )
        or _looks_like_guard_report(parsed)
        or bool(str(parsed.get("report_type") or "").strip())
    ):
        return StreamRenderKind.REPORT
    if isinstance(parsed, (dict, list)):
        return StreamRenderKind.JSON
    return StreamRenderKind.TEXT


def stream_html_from_text(
    text: str,
    *,
    paragraph_indent: bool = False,
    cursor: bool = False,
) -> str:
    """Render prose, complete JSON, partial JSON, or report JSON consistently."""

    if not text.strip():
        if cursor:
            return "<p class='stream-cursor'>▍</p>"
        return (
            "<p class='placeholder'>当前节点尚未产生可预览正文。</p>"
            "<p class='hint'>正在等待模型返回首个片段；非增量调用完成后也会显示响应预览。</p>"
        )
    clipped = text
    clipped_notice = ""
    if len(clipped) > _STREAM_DISPLAY_LIMIT:
        clipped = clipped[-_STREAM_DISPLAY_LIMIT:]
        clipped_notice = "<p class='placeholder'>前文较长，已折叠，仅显示最近输出。</p>"
    report_block = _report_block_html(clipped)
    if report_block:
        return clipped_notice + report_block + (_cursor_html() if cursor else "")
    json_block = _json_block_html(clipped)
    if json_block:
        return clipped_notice + json_block + (_cursor_html() if cursor else "")

    paragraph_style = _STREAM_INDENTED_P_STYLE if paragraph_indent else _STREAM_P_STYLE
    paragraphs: list[str] = []
    for line in _compact_text_lines(clipped):
        if line is not None:
            paragraphs.append(f"<p style='{paragraph_style}'>{html.escape(line)}</p>")
        else:
            paragraphs.append(f"<p class='blank' style='{_STREAM_BLANK_P_STYLE}'>&nbsp;</p>")
    if cursor:
        if paragraphs:
            paragraphs[-1] = paragraphs[-1].replace("</p>", f"{_cursor_html()}</p>")
        else:
            paragraphs.append(f"<p style='{paragraph_style}'>{_cursor_html()}</p>")
    return clipped_notice + "".join(paragraphs)


def stream_html_from_segments(
    segments: tuple[StreamSegment, ...],
    *,
    truncated: bool = False,
) -> str:
    if not segments:
        return stream_html_from_text("")
    parts: list[str] = []
    if truncated:
        parts.append("<p class='placeholder'>前文较长，已折叠，仅显示最近输出。</p>")
    for segment in segments:
        if segment.kind == "reasoning":
            if not segment.text.strip():
                continue
            body_html = "".join(
                f"<p style='{_STREAM_P_STYLE}'>{html.escape(line)}</p>"
                if line is not None
                else f"<p class='blank' style='{_STREAM_BLANK_P_STYLE}'>&nbsp;</p>"
                for line in _compact_text_lines(segment.text)
            )
            parts.append(
                "<div class='reasoning-block'><div class='reasoning-header'>思考</div>"
                f"<div class='reasoning-body'>{body_html}</div></div>"
            )
        else:
            parts.append(stream_html_from_text(segment.text))
    return "".join(parts) if parts else stream_html_from_text("")


def stream_document_html(body_html: str) -> str:
    """Wrap a body in the one CSS contract used by QTextBrowser surfaces."""

    from novel_forge.desktop.pages.standalone.renderer_html import stream_report_css

    return (
        "<html><head><style>"
        "body { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; "
        "font-size: 13px; line-height: 1.58; margin: 0; white-space: normal; "
        "word-break: break-all; overflow-wrap: anywhere; }"
        "p { margin: 0 0 5px 0; line-height: 1.58; white-space: normal; "
        "word-break: break-all; overflow-wrap: anywhere; }"
        ".placeholder, .hint { color: #8b8074; }"
        ".blank { margin: 0 0 2px 0; line-height: 1.1; }"
        ".stream-cursor { color: #b4532a; font-weight: 700; padding-left: 2px; }"
        ".reasoning-block { margin: 0 0 7px 0; padding: 7px 10px; "
        "background: rgba(139, 128, 116, 0.10); border-left: 3px solid "
        "rgba(139, 128, 116, 0.35); border-radius: 0 6px 6px 0; }"
        ".reasoning-header { margin: 0 0 4px 0; font-size: 12px; font-weight: 700; "
        "color: #8b8074; letter-spacing: 0.04em; }"
        ".reasoning-body { color: #6c6358; font-size: 12px; }"
        ".reasoning-body p { margin: 0 0 4px 0; line-height: 1.48; }"
        ".json-card, .report-card { margin: 0; padding: 10px 12px; "
        "background: rgba(255, 248, 238, 0.94); border: 1px solid "
        "rgba(141, 107, 76, 0.16); border-radius: 8px; }"
        ".json-header, .report-header { margin: 0 0 8px 0; color: #7a3f25; "
        "font-weight: 800; }"
        ".json-header span, .report-header span { margin-left: 8px; color: #8b8074; "
        "font-weight: 500; font-size: 12px; }"
        ".json-row { margin: 4px 0 0 0; padding-left: 10px; border-left: 2px solid "
        "rgba(196, 77, 38, 0.18); }"
        ".json-key, .json-index { font-family: SFMono-Regular, Menlo, Consolas, "
        "monospace; font-size: 12px; font-weight: 700; color: #b24f28; }"
        ".json-index { color: #6c7a4d; }"
        ".json-value, .report-body { margin: 3px 0 0 0; color: #2f2923; "
        "word-break: break-all; overflow-wrap: anywhere; }"
        ".json-string { color: #2f2923; white-space: pre-wrap; }"
        ".json-long, .json-raw { display: block; margin: 4px 0 0 0; padding: 8px 10px; "
        "background: rgba(255, 255, 255, 0.62); border-radius: 7px; "
        "white-space: pre-wrap; }"
        ".json-number { color: #2f6b65; }"
        ".json-bool, .json-null, .json-empty { color: #7c6b5d; "
        "font-family: SFMono-Regular, Menlo, Consolas, monospace; }"
        + stream_report_css()
        + "</style></head><body>"
        + body_html
        + "</body></html>"
    )


def _cursor_html() -> str:
    return "<span class='stream-cursor'>▍</span>"


def _report_block_html(text: str) -> str:
    prefix, candidate = _split_json_candidate(text)
    if not candidate:
        return ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    report = _render_report_body(parsed)
    if report is None:
        return ""
    title, body_html = report
    return prefix + _wrap_report_inline(title, body_html)


def _render_report_body(data: dict[str, Any]) -> tuple[str, str] | None:
    from novel_forge.desktop.pages.document_renderer_reports import (
        render_alignment_report_body_html,
        render_causal_report_body_html,
        render_continuity_report_body_html,
        render_eval_report_body_html,
        render_generic_report_body_html,
        render_guard_report_body_html,
        render_humanize_report_body_html,
    )

    try:
        if "overall_score" in data:
            return "质量评估报告", render_eval_report_body_html(data)
        if "alignment_score" in data:
            return "对齐报告", render_alignment_report_body_html(data)
        if "continuity_score" in data:
            return "连贯性报告", render_continuity_report_body_html(data)
        if "causal_score" in data:
            return "因果链报告", render_causal_report_body_html(data)
        if "humanize_score" in data:
            return "拟人化扫描", render_humanize_report_body_html(data)
        if _looks_like_guard_report(data):
            return "AI 护栏报告", render_guard_report_body_html(data)
        if str(data.get("report_type") or "").strip():
            return "结构化报告", render_generic_report_body_html(data)
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _looks_like_guard_report(data: dict[str, Any]) -> bool:
    decision = data.get("decision")
    if not isinstance(decision, dict):
        return False
    return bool(
        {
            "decision",
            "risk_level",
            "outline_action",
            "entity_actions",
            "next_chapter_constraints",
            "reasoning_brief",
        }.intersection(decision)
    )


def _wrap_report_inline(title: str, body_html: str) -> str:
    return (
        "<div class='report-card'>"
        f"<div class='report-header'>{html.escape(title)}<span>结构化报告</span></div>"
        f"<div class='report-body'>{body_html}</div></div>"
    )


def _json_block_html(text: str) -> str:
    prefix, candidate = _split_json_candidate(text)
    if not candidate:
        return ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return prefix + _raw_json_block_html(candidate, title="JSON 块尚未完成")
    if not isinstance(parsed, (dict, list)):
        return ""
    summary = f"对象 · {len(parsed)} 项" if isinstance(parsed, dict) else f"数组 · {len(parsed)} 项"
    return (
        prefix
        + "<div class='json-card'>"
        + f"<div class='json-header'>JSON 输出<span>{html.escape(summary)}</span></div>"
        + _render_json_value(parsed)
        + "</div>"
    )


def _split_json_candidate(text: str) -> tuple[str, str]:
    candidate = _json_candidate(text)
    if candidate:
        return "", candidate
    head, separator, tail = text.strip().partition("\n\n")
    if separator:
        candidate = _json_candidate(tail)
        if candidate:
            return f"<p class='placeholder'>{html.escape(head)}</p>", candidate
    return "", ""


def _json_candidate(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            body = stripped[first_newline + 1 :]
            closing = body.rfind("```")
            if closing >= 0:
                body = body[:closing]
            return body.strip()
    if stripped.startswith(("{", "[")):
        return stripped
    return ""


def _render_json_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return "<span class='json-empty'>{}</span>"
        rows = [
            "<div class='json-row'>"
            f"<span class='json-key'>{html.escape(str(key))}</span>"
            f"<div class='json-value'>{_render_json_value(item)}</div></div>"
            for key, item in value.items()
        ]
        return "<div class='json-object'>" + "".join(rows) + "</div>"
    if isinstance(value, list):
        if not value:
            return "<span class='json-empty'>[]</span>"
        rows = [
            "<div class='json-row json-array-row'>"
            f"<span class='json-index'>{index}</span>"
            f"<div class='json-value'>{_render_json_value(item)}</div></div>"
            for index, item in enumerate(value, start=1)
        ]
        return "<div class='json-array'>" + "".join(rows) + "</div>"
    return _render_json_scalar(value)


def _render_json_scalar(value: Any) -> str:
    if value is None:
        return "<span class='json-null'>null</span>"
    if isinstance(value, bool):
        return f"<span class='json-bool'>{str(value).lower()}</span>"
    if isinstance(value, int | float):
        return f"<span class='json-number'>{value}</span>"
    text = str(value)
    if len(text) > _JSON_LONG_STRING_LIMIT:
        text = text[:_JSON_LONG_STRING_LIMIT].rstrip() + "\n\n…已截断，仅显示前部预览。"
    escaped = html.escape(text)
    if "\n" in text or len(text) > _JSON_INLINE_STRING_LIMIT:
        return f"<div class='json-string json-long' style='{_STREAM_JSON_BREAK_STYLE}'>{escaped}</div>"
    return f"<span class='json-string'>{escaped}</span>"


def _raw_json_block_html(text: str, *, title: str) -> str:
    return (
        "<div class='json-card'>"
        f"<div class='json-header'>{html.escape(title)}<span>等待完整对象/数组</span></div>"
        f"<pre class='json-raw' style='{_STREAM_JSON_BREAK_STYLE}'>{html.escape(text.strip())}</pre>"
        "</div>"
    )


# Compatibility exports for older imports and focused tests.
_html_from_text = stream_html_from_text
_html_from_segments = stream_html_from_segments
