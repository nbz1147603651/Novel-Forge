"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Migration P2-step (cluster D, sub chapter_memory_diagnostics): ``render_chapter_memory_diagnostics`` renders reports/chapter_memory_diagnostics.json. Its 7 small helpers (``_tag``, ``_stage_label``, ``_layer_label``, ``_render_layer_tags``, ``_render_source_tags``, ``_render_flag_tags``, ``_render_count_text``) were nested inside the function body in the original and are lifted to module scope here.

The renderer below is a self-contained HTML view; it relies on
``renderer_html`` helpers (``_esc`` / ``_nl2br`` / ``_html_wrap`` /
``_make_browser``) but does not share private helpers with sibling
story_artifacts submodules.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser

JsonDict = dict[str, Any]

__all__ = ['render_chapter_memory_diagnostics']


def render_chapter_memory_diagnostics(data: JsonDict) -> QTextBrowser:
    """Render chapter memory diagnostics as a stage-oriented report."""
    chapter_number = data.get("chapter_number", "?")
    summary = data.get("summary", {})
    stages = data.get("stages", {})
    updated_at = str(data.get("updated_at", "") or "").strip()

    stage_labels = {
        "planning": "规划",
        "draft": "起草",
        "finalize": "收束",
    }
    layer_labels = {
        "L0_identity": "L0 项目身份",
        "L1_core_memory": "L1 核心记忆",
        "L2_on_demand": "L2 按需检索",
        "L3_deep_search": "L3 深层检索",
    }
    source_labels = {
        "prompt_context": "提示上下文",
        "layered_context": "宏观摘要",
        "relevant_history": "相关历史",
        "previous_chapter_events": "上一章事件",
        "outline_context": "大纲上下文",
        "motif_continuity": "母题连续性",
        "motif_suggestions": "母题建议",
        "expression_channel_records": "表达通道记录",
    }
    source_status_labels = {
        "generated": "已生成",
        "fetched": "已拉取",
        "prefetched": "已复用",
        "empty": "为空",
        "fetched_empty": "无命中",
        "prefetched_empty": "复用为空",
        "disabled": "未启用",
        "not_applicable": "不适用",
        "unavailable": "不可用",
        "error": "失败",
        "scheduled": "已调度",
        "pending": "处理中",
    }
    flag_labels = {
        "has_relevant_history": "相关历史",
        "has_previous_chapter_events": "上一章事件",
        "has_outline_context": "大纲上下文",
        "has_critique_context": "批注上下文",
        "has_motif_continuity": "母题连续性",
        "has_motif_suggestions": "母题建议",
        "has_prompt_summary": "提示摘要",
        "has_forbidden_repetition": "禁复提醒",
        "has_layered_context": "宏观摘要",
        "has_expression_channel_records": "表达通道",
    }
    count_labels = {
        "relevant_history": "历史片段",
        "previous_chapter_events": "上章事件",
        "outline_context_fields": "大纲字段",
        "motif_suggestions": "母题建议",
        "forbidden_repetition": "禁复词",
        "active_motif_ids": "活跃母题",
        "motif_active_entries": "母题条目",
        "prompt_context_fields": "提示字段",
        "expression_channel_records": "表达通道记录",
    }
    ordered_stage_names = [
        stage_name
        for stage_name in ("planning", "draft", "finalize")
        if (
            stage_name in list(summary.get("available_stages", []) or [])
            or (isinstance(stages, dict) and isinstance(stages.get(stage_name), dict))
        )
    ]
    if not ordered_stage_names and isinstance(stages, dict):
        ordered_stage_names = [
            str(stage_name) for stage_name, payload in stages.items() if isinstance(payload, dict)
        ]

    def _tag(text: str, *, tone: str = "muted") -> str:
        label = _esc(text)
        if tone == "strong":
            return f'<span class="tag">{label}</span>'
        if tone == "warn":
            return (
                '<span class="tag-muted tag" '
                'style="color:[[nf:status.danger.deep]]; background:rgba([[nf:status.danger.deep]], 0.08); border-color:rgba([[nf:status.danger.deep]], 0.14);">'
                f"{label}</span>"
            )
        if tone == "ok":
            return (
                '<span class="tag-muted tag" '
                'style="color:[[nf:status.success.warm]]; background:rgba([[nf:status.success.warm]], 0.08); border-color:rgba([[nf:status.success.warm]], 0.14);">'
                f"{label}</span>"
            )
        return f'<span class="tag-muted tag">{label}</span>'

    def _stage_label(stage_name: str) -> str:
        return stage_labels.get(stage_name, stage_name)

    def _layer_label(layer_name: str) -> str:
        return layer_labels.get(layer_name, layer_name)

    def _render_layer_tags(layers: Any, *, tone: str = "strong") -> str:
        values = [
            _layer_label(str(item).strip()) for item in list(layers or []) if str(item).strip()
        ]
        if not values:
            return _tag("无", tone="muted")
        return "".join(_tag(value, tone=tone) for value in values)

    def _render_source_tags(sources: Any) -> str:
        if not isinstance(sources, dict) or not sources:
            return _tag("无来源数据", tone="muted")
        chips: list[str] = []
        for key in source_labels:
            status = str(sources.get(key, "") or "").strip()
            if not status:
                continue
            label = source_status_labels.get(status, status)
            tone = "muted"
            if status in {"generated", "fetched", "prefetched"}:
                tone = "ok"
            elif status in {"unavailable", "error"}:
                tone = "warn"
            chips.append(_tag(f"{source_labels[key]} · {label}", tone=tone))
        return "".join(chips) if chips else _tag("无来源数据", tone="muted")

    def _render_flag_tags(flags: Any, *, history_reused: bool = False) -> str:
        chips: list[str] = []
        if isinstance(flags, dict):
            for key, label in flag_labels.items():
                if flags.get(key):
                    chips.append(_tag(label, tone="ok"))
        if history_reused:
            chips.append(_tag("复用历史", tone="ok"))
        return "".join(chips) if chips else _tag("无附加信号", tone="muted")

    def _render_count_text(counts_payload: Any) -> str:
        if not isinstance(counts_payload, dict):
            return "无计数数据"
        items: list[str] = []
        for key, label in count_labels.items():
            value = int(counts_payload.get(key, 0) or 0)
            if value > 0:
                items.append(f"{label} {value}")
        return " · ".join(items) if items else "无有效命中"

    parts: list[str] = []
    header_tags: list[str] = []
    if ordered_stage_names:
        header_tags.extend(
            _tag(_stage_label(stage_name), tone="strong") for stage_name in ordered_stage_names
        )
    latest_stage = str(summary.get("latest_stage", "") or "").strip()
    if latest_stage:
        header_tags.append(_tag(f"最新：{_stage_label(latest_stage)}", tone="muted"))
    if updated_at:
        header_tags.append(_tag(updated_at, tone="muted"))
    parts.append(
        '<div class="section" style="text-align:center; padding:18px;">'
        f"<div style=\"font-size: 28pt; font-weight:700; font-family:'Songti SC',serif; color:[[nf:text.heading]];\">第 {chapter_number} 章</div>"
        '<div style="color:[[nf:text.muted]]; font-size: 13pt; margin-top:6px;">记忆诊断</div>'
        + (f'<div style="margin-top:10px;">{"".join(header_tags)}</div>' if header_tags else "")
        + "</div>"
    )

    summary_counts = summary.get("counts", {})
    context_stages = [
        _stage_label(str(item).strip())
        for item in list(summary.get("context_available_stages", []) or [])
        if str(item).strip()
    ]
    reused_stages = [
        _stage_label(str(item).strip())
        for item in list(summary.get("history_reused_stages", []) or [])
        if str(item).strip()
    ]
    parts.append("<h2>总体概览</h2>")
    overview_rows = [
        '<div class="kv-row"><span class="kv-label">阶段覆盖</span> '
        + (
            "".join(
                _tag(_stage_label(stage_name), tone="strong") for stage_name in ordered_stage_names
            )
            if ordered_stage_names
            else _tag("暂无阶段", tone="muted")
        )
        + "</div>",
        f'<div class="kv-row"><span class="kv-label">总体命中</span> <span class="kv-value">{_esc(_render_count_text(summary_counts))}</span></div>',
    ]
    if context_stages:
        overview_rows.append(
            f'<div class="kv-row"><span class="kv-label">已接入记忆</span> <span class="kv-value">{_esc("、".join(context_stages))}</span></div>'
        )
    if reused_stages:
        overview_rows.append(
            f'<div class="kv-row"><span class="kv-label">复用历史</span> <span class="kv-value">{_esc("、".join(reused_stages))}</span></div>'
        )
    parts.append('<div class="section">' + "\n".join(overview_rows) + "</div>")

    if not ordered_stage_names:
        parts.append('<div class="hint-block">当前章节还没有可展示的阶段诊断。</div>')
        return _make_browser(_html_wrap("\n".join(parts), "记忆诊断"))

    resolved_summary = summary.get("resolved_layers_by_stage", {})
    missing_summary = summary.get("missing_layers_by_stage", {})
    parts.append("<h2>阶段详情</h2>")
    for stage_name in ordered_stage_names:
        payload = stages.get(stage_name, {}) if isinstance(stages, dict) else {}
        requested_layers = [
            str(item).strip()
            for item in list(payload.get("requested_layers", []) or [])
            if str(item).strip()
        ]
        resolved_layers = [
            str(item).strip()
            for item in list(
                payload.get("resolved_layers", []) or resolved_summary.get(stage_name, [])
            )
            if str(item).strip()
        ]
        missing_layers = [
            str(item).strip()
            for item in list(
                payload.get("missing_layers", []) or missing_summary.get(stage_name, [])
            )
            if str(item).strip()
        ]
        duration_ms = float(payload.get("duration_ms", 0.0) or 0.0)
        layer_char_counts = payload.get("layer_char_counts", {})
        counts_payload = payload.get("counts", {})
        sources_payload = payload.get("sources", {})
        flags_payload = payload.get("flags", {})
        history_reused = bool(payload.get("history_reused", False))

        header_meta: list[str] = []
        if requested_layers:
            header_meta.append(
                _tag(f"{len(resolved_layers)}/{len(requested_layers)} 层", tone="strong")
            )
        elif resolved_layers:
            header_meta.append(_tag(f"{len(resolved_layers)} 层", tone="strong"))
        if duration_ms > 0:
            header_meta.append(_tag(f"{duration_ms:.0f} ms", tone="muted"))
        if history_reused:
            header_meta.append(_tag("复用历史", tone="ok"))

        stage_rows = [
            '<div class="kv-row"><span class="kv-label">已命中层</span> '
            f"{_render_layer_tags(resolved_layers, tone='strong')}</div>"
        ]
        if missing_layers:
            stage_rows.append(
                '<div class="kv-row"><span class="kv-label">缺失层</span> '
                f"{_render_layer_tags(missing_layers, tone='warn')}</div>"
            )
        if isinstance(layer_char_counts, dict) and layer_char_counts:
            metrics = " · ".join(
                f"{_layer_label(str(key))} {int(value or 0)} 字"
                for key, value in layer_char_counts.items()
                if int(value or 0) > 0
            )
            if metrics:
                stage_rows.append(
                    f'<div class="kv-row"><span class="kv-label">层级字数</span> <span class="kv-value">{_esc(metrics)}</span></div>'
                )
        stage_rows.append(
            '<div class="kv-row"><span class="kv-label">数据来源</span> '
            f"{_render_source_tags(sources_payload)}</div>"
        )
        stage_rows.append(
            f'<div class="kv-row"><span class="kv-label">命中计数</span> <span class="kv-value">{_esc(_render_count_text(counts_payload))}</span></div>'
        )
        stage_rows.append(
            '<div class="kv-row"><span class="kv-label">附加信号</span> '
            f"{_render_flag_tags(flags_payload, history_reused=history_reused)}</div>"
        )

        parts.append(
            f"<h3>{_esc(_stage_label(stage_name))}阶段</h3>"
            '<div class="section">'
            + (
                f'<div style="margin-bottom:8px;">{"".join(header_meta)}</div>'
                if header_meta
                else ""
            )
            + "\n".join(stage_rows)
            + "</div>"
        )

    return _make_browser(_html_wrap("\n".join(parts), "记忆诊断"))
