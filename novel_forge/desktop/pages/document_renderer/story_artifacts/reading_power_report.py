"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Migration P2-step (cluster D, sub reading_power_report): ``render_reading_power_report`` renders reports/reading_power_report.json. Its 4 small helpers (``_float_or_none``, ``_score_fragment``, ``_fmt_score``, ``_fmt_weight``) were nested inside the function body in the original and are lifted to module scope here.

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
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_color as _score_color

JsonDict = dict[str, Any]

__all__ = ['render_reading_power_report']


def render_reading_power_report(data: JsonDict) -> QTextBrowser:
    """Render a reading_power_report.json with structured sections."""
    parts: list[str] = []

    def _float_or_none(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _score_fragment(value: Any, *, max_score: float = 2.0) -> str:
        score = _float_or_none(value)
        if score is None:
            return '<span style="color:[[nf:text.muted]];">未给分</span>'
        color = _score_color(score * (10.0 / max_score))
        return f'<span style="color:{color}; font-weight:600;">{score:.1f}/{max_score:.0f}</span>'

    def _fmt_score(value: Any) -> str:
        score = _float_or_none(value)
        return "-" if score is None else f"{score:.1f}"

    def _fmt_weight(value: Any) -> str:
        weight = _float_or_none(value)
        return "-" if weight is None else f"{weight * 100:.0f}%"

    chapter = data.get("chapter", "?")
    overall_score = data.get("overall_score", 0.0)
    is_fallback = bool(
        data.get("is_fallback") or str(data.get("evaluation_status", "")).lower() == "fallback"
    )
    score_color = _score_color(overall_score)

    # Header with score
    status_suffix = " · 兜底报告" if is_fallback else ""
    parts.append(
        f'<div style="text-align:center; margin-bottom:16px;">'
        f'<span style="font-size: 28pt; font-weight:700; color:{score_color};">{overall_score:.1f}</span>'
        f'<span style="font-size: 12pt; color:[[nf:text.muted]]; margin-left:6px;">/ 10 分 · 第 {chapter} 章{status_suffix}</span>'
        f"</div>"
    )
    if is_fallback:
        reason = data.get("fallback_reason", "unknown")
        parts.append(
            f'<div class="section" style="border-left:3px solid [[nf:accent.primary]];">'
            f'<strong style="color:[[nf:status.danger.deep]];">评估未完成</strong>'
            f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; margin-top:4px;">'
            f"当前内容是兜底报告，原因：{_esc(str(reason))}。不要把该分数当作真实追读力诊断。"
            f"</div></div>"
        )

    information_pacing = str(data.get("information_pacing", "") or "").lower()
    tension_match = str(data.get("tension_match", "") or "").lower()
    information_score = _float_or_none(data.get("information_pacing_score"))
    tension_score = _float_or_none(data.get("tension_match_score"))
    score_warnings: list[str] = []
    if information_pacing == "balanced" and information_score == 0.0:
        score_warnings.append("信息释放节奏标记为 balanced，但分项分为 0，旧报告可能低估综合分。")
    if tension_match == "matched" and tension_score == 0.0:
        score_warnings.append("张力匹配标记为 matched，但分项分为 0，旧报告可能低估综合分。")
    if score_warnings and not is_fallback:
        parts.append(
            '<div class="section" style="border-left:3px solid [[nf:accent.primary]];">'
            '<strong style="color:[[nf:status.danger.deep]];">分数需要复核</strong>'
            f'<div style="color:[[nf:text.secondary]]; font-size: 12pt; margin-top:4px;">'
            f"{_nl2br(_esc('；'.join(score_warnings)))}</div></div>"
        )

    # Score breakdown
    score_breakdown = data.get("score_breakdown", {})
    if isinstance(score_breakdown, dict) and isinstance(score_breakdown.get("components"), dict):
        component_labels = {
            "hook_strength": "章尾钩子",
            "payoff_density": "微兑现密度",
            "information_pacing": "信息节奏",
            "main_plot_depth": "主线推进",
            "tension_match": "张力匹配",
        }
        component_rows: list[str] = []
        components = score_breakdown.get("components", {})
        for key in (
            "hook_strength",
            "payoff_density",
            "information_pacing",
            "main_plot_depth",
            "tension_match",
        ):
            comp = components.get(key, {})
            if not isinstance(comp, dict):
                continue
            normalized = _float_or_none(comp.get("normalized_score"))
            score_color_local = _score_color(normalized or 0.0)
            component_rows.append(
                '<div class="kv-row">'
                f'<span class="kv-label">{component_labels[key]}</span> '
                f'<span style="color:{score_color_local}; font-weight:600;">'
                f"{_fmt_score(comp.get('normalized_score'))}/10</span>"
                f' <span style="color:[[nf:text.muted]];">× {_fmt_weight(comp.get("weight"))}'
                f" = {_fmt_score(comp.get('weighted_score'))}</span>"
                f' <span class="tag-muted tag">{_esc(str(comp.get("label", "") or ""))}</span>'
                "</div>"
            )
        base_score = score_breakdown.get("base_score")
        adjustments = score_breakdown.get("adjustments", [])
        if component_rows:
            parts.append("<h2>评分拆解</h2>")
            parts.append('<div class="section">' + "\n".join(component_rows) + "</div>")
            adjustment_rows = [
                f'<div class="kv-row"><span class="kv-label">加权基础分</span> {_fmt_score(base_score)}</div>'
            ]
            if isinstance(adjustments, list):
                for adj in adjustments:
                    if not isinstance(adj, dict):
                        continue
                    label = _esc(str(adj.get("label", adj.get("reason", "")) or ""))
                    if adj.get("kind") == "multiplier":
                        adjustment_rows.append(
                            '<div class="kv-row"><span class="kv-label">校准</span> '
                            f"{label} × {_fmt_score(adj.get('factor'))}</div>"
                        )
                    elif adj.get("kind") == "penalty":
                        adjustment_rows.append(
                            '<div class="kv-row"><span class="kv-label">扣分</span> '
                            f"{label} {_fmt_score(adj.get('points'))}</div>"
                        )
            adjustment_rows.append(
                f'<div class="kv-row"><span class="kv-label">最终分</span> {_fmt_score(score_breakdown.get("final_score", overall_score))}</div>'
            )
            parts.append('<div class="section">' + "\n".join(adjustment_rows) + "</div>")

    # Hook assessment
    hook_type = data.get("hook_type", "none")
    hook_strength = data.get("hook_strength", "weak")
    hook_desc = data.get("hook_description", "")
    prev_fulfilled = data.get("prev_hook_fulfilled", True)

    parts.append("<h2>章尾钩子</h2>")
    hook_rows = []
    type_label = {
        "crisis": "危机钩",
        "mystery": "悬念钩",
        "emotion": "情绪钩",
        "choice": "选择钩",
        "desire": "渴望钩",
        "none": "无明显钩子",
    }.get(hook_type, hook_type)
    strength_tag = {
        "strong": '<span class="tag">强</span>',
        "medium": '<span class="tag-muted tag">中</span>',
        "weak": '<span class="tag-muted tag">弱</span>',
    }.get(hook_strength, "")
    hook_rows.append(
        f'<div class="kv-row"><span class="kv-label">钩子类型</span> {type_label} {strength_tag}</div>'
    )
    if hook_desc:
        hook_rows.append(
            f'<div class="kv-row"><span class="kv-label">钩子描述</span> <div class="hint-block" style="margin:4px 0;">{_nl2br(_esc(hook_desc))}</div></div>'
        )
    fulfilled_tag = (
        '<span class="tag">已回应</span>'
        if prev_fulfilled
        else '<span style="color:[[nf:accent.primary]];">未回应</span>'
    )
    hook_rows.append(
        f'<div class="kv-row"><span class="kv-label">上章回应</span> {fulfilled_tag}</div>'
    )
    parts.append('<div class="section">' + "\n".join(hook_rows) + "</div>")

    # Score dimensions
    dimension_rows: list[str] = []
    pacing_label = {
        "rushed": "过快",
        "balanced": "适中",
        "slow": "偏慢",
        "stagnant": "停滞",
    }.get(information_pacing, information_pacing or "未评估")
    main_plot_depth = str(data.get("main_plot_depth", "") or "").lower()
    main_plot_label = {
        "deep": "实质推进",
        "moderate": "阶段进展",
        "surface": "触及未推进",
        "stalled": "停滞",
    }.get(main_plot_depth, main_plot_depth or "未评估")
    tension_label = {
        "matched": "匹配",
        "elevated": "高于预期",
        "depressed": "低于预期",
    }.get(tension_match, tension_match or "未评估")
    character_drive = str(data.get("character_drive", "") or "").lower()
    drive_label = {
        "strong": "主动强",
        "moderate": "有立场",
        "weak": "偏被动",
    }.get(character_drive, character_drive or "未评估")
    if information_pacing or data.get("information_pacing_score") is not None:
        dimension_rows.append(
            f'<div class="kv-row"><span class="kv-label">信息节奏</span> '
            f"{_esc(pacing_label)} {_score_fragment(data.get('information_pacing_score'))}</div>"
        )
    if main_plot_depth:
        notes = str(data.get("main_plot_advancement_notes", "") or "").strip()
        dimension_rows.append(
            f'<div class="kv-row"><span class="kv-label">主线推进</span> {_esc(main_plot_label)}</div>'
        )
        if notes:
            dimension_rows.append(
                f'<div class="hint-block" style="margin:4px 0;">{_nl2br(_esc(notes))}</div>'
            )
    if tension_match or data.get("tension_match_score") is not None:
        dimension_rows.append(
            f'<div class="kv-row"><span class="kv-label">张力匹配</span> '
            f"{_esc(tension_label)} {_score_fragment(data.get('tension_match_score'))}</div>"
        )
    if character_drive:
        dimension_rows.append(
            f'<div class="kv-row"><span class="kv-label">角色驱动力</span> {_esc(drive_label)}</div>'
        )
    if dimension_rows:
        parts.append("<h2>评分维度</h2>")
        parts.append('<div class="section">' + "\n".join(dimension_rows) + "</div>")

    # Micro-payoffs
    payoffs = data.get("micro_payoffs", [])
    if isinstance(payoffs, list) and payoffs:
        parts.append("<h2>微兑现 ({})</h2>".format(len(payoffs)))
        payoff_type_label = {
            "information": "信息",
            "relationship": "关系",
            "ability": "能力",
            "resource": "资源",
            "recognition": "认可",
            "emotion": "情绪",
            "clue": "线索",
        }
        for p in payoffs:
            if not isinstance(p, dict):
                continue
            ptype = payoff_type_label.get(
                str(p.get("payoff_type", "")), str(p.get("payoff_type", ""))
            )
            pstrength = {"strong": "强", "medium": "中", "weak": "弱"}.get(
                str(p.get("strength", "")), ""
            )
            pdesc = p.get("description", "")
            desc_html = (
                f'<div class="hint-block" style="margin-top:6px;">{_nl2br(_esc(str(pdesc)))}</div>'
                if pdesc
                else ""
            )
            parts.append(
                f'<div class="section">'
                f'<div class="kv-row"><span class="tag">{_esc(ptype)}</span> <span class="tag-muted tag">{_esc(pstrength)}</span></div>'
                f"{desc_html}"
                f"</div>"
            )
    else:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:12px; text-align:center; font-size: 12pt;">未检测到微兑现</div>'
        )

    # Chapter pacing
    is_transition = data.get("is_transition", False)
    next_reason = data.get("next_chapter_reason", "")
    if is_transition or next_reason:
        parts.append("<h2>章节节奏</h2>")
        pace_rows = []
        if is_transition:
            pace_rows.append('<div class="kv-row"><span class="tag-muted tag">过渡章</span></div>')
        if next_reason:
            pace_rows.append(
                f'<div class="kv-row"><span class="kv-label">下一章驱动力</span> <div class="hint-block" style="margin:4px 0;">{_nl2br(_esc(next_reason))}</div></div>'
            )
        parts.append('<div class="section">' + "\n".join(pace_rows) + "</div>")

    # Sub-scores
    sub_scores = data.get("sub_scores", {})
    if isinstance(sub_scores, dict) and sub_scores:
        parts.append("<h2>分项评分</h2>")
        score_rows = []
        score_labels = {
            "hook_strength": "钩子强度",
            "payoff_density": "兑现密度",
            "suspense_timing": "悬念时机",
            "hook_alternation": "钩子交替",
            "tension_match": "张力匹配",
        }
        for key, val in sub_scores.items():
            label = score_labels.get(str(key), str(key))
            c = _score_color(val)
            score_rows.append(
                f'<div class="kv-row"><span class="kv-label">{_esc(label)}</span> <span style="color:{c}; font-weight:600;">{val:.1f}</span></div>'
            )
        parts.append('<div class="section">' + "\n".join(score_rows) + "</div>")

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无追读力评估数据</div>'
        )
    return _make_browser(_html_wrap("\n".join(parts), "追读力评估"))


