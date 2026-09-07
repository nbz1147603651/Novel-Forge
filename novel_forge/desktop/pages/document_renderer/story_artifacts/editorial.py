"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Migration P2-step (cluster C): editorial & style-profile renderers +
``_editorial_*`` text helpers now live here. The shared ``_fragment_count_row``
and ``_fragment_dict_count`` counters live in :mod:`._common`.

Render functions:

- ``render_profile_style`` — style_profile.json view
- ``render_editorial_contract`` — editorial_contract.json view
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _fragment_count_row,
    _fragment_dict_count,
)
from novel_forge.desktop.pages.document_renderer_reports import generic_key_label
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = [
    "render_profile_style",
    "render_editorial_contract",
]


def render_profile_style(data: JsonDict) -> QTextBrowser:
    """Render style_profile.json with structured sections."""
    parts: list[str] = []

    # Summary
    summary = data.get("summary", "")
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')

    # Source elements
    source = data.get("source_elements", [])
    if source:
        parts.append(
            f'<div style="font-size: 11pt;color:[[nf:text.muted]];margin-bottom:10px;">'
            f"来源要素：{_esc('、'.join(str(s) for s in source))}"
            f"</div>"
        )

    # Global style config
    global_style = data.get("global_style")
    if isinstance(global_style, dict):
        parts.append("<h2>风格基调</h2>")
        rows = []
        if global_style.get("dialogue_ratio"):
            rows.append(
                f'<span class="kv-label">对话占比</span> {_esc(global_style["dialogue_ratio"])}'
            )
        if global_style.get("pace_mode"):
            rows.append(f'<span class="kv-label">节奏模式</span> {_esc(global_style["pace_mode"])}')
        if global_style.get("emotional_style"):
            rows.append(
                f'<span class="kv-label">情绪风格</span> {_esc(global_style["emotional_style"])}'
            )
        if global_style.get("environment_ratio"):
            rows.append(
                f'<span class="kv-label">环境描写</span> {_esc(global_style["environment_ratio"])}'
            )
        if global_style.get("info_density"):
            rows.append(
                f'<span class="kv-label">信息密度</span> {_esc(global_style["info_density"])}'
            )
        if rows:
            parts.append(
                '<div class="section">'
                + "<br>".join(f'<div class="kv-row">{r}</div>' for r in rows)
                + "</div>"
            )

    # Hook config
    hook_config = data.get("hook_config")
    if isinstance(hook_config, dict):
        parts.append("<h2>钩子要求</h2>")
        rows = []
        preferred = hook_config.get("preferred_types", [])
        if preferred:
            tags = "".join(f'<span class="tag">{_esc(str(t))}</span>' for t in preferred)
            rows.append(f'<div class="kv-row"><span class="kv-label">偏好类型</span> {tags}</div>')
        if hook_config.get("strength_baseline"):
            rows.append(
                f'<div class="kv-row"><span class="kv-label">强度基准</span> {_esc(hook_config["strength_baseline"])}</div>'
            )
        if hook_config.get("chapter_end_required"):
            rows.append('<div class="kv-row"><span class="tag">章末必须钩子</span></div>')
        if rows:
            parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Cool point config
    cool_config = data.get("cool_point_config")
    if isinstance(cool_config, dict):
        patterns = cool_config.get("preferred_patterns", [])
        density = cool_config.get("density_per_chapter", "")
        if patterns or density:
            parts.append("<h2>爽点模式</h2>")
            rows = []
            if patterns:
                tags = "".join(f'<span class="tag">{_esc(str(p))}</span>' for p in patterns)
                rows.append(
                    f'<div class="kv-row"><span class="kv-label">偏好模式</span> {tags}</div>'
                )
            if density:
                rows.append(
                    f'<div class="kv-row"><span class="kv-label">爽点密度</span> {_esc(density)}</div>'
                )
            if rows:
                parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Micro payoff config
    payoff_config = data.get("micro_payoff_config")
    if isinstance(payoff_config, dict):
        preferred = payoff_config.get("preferred_types", [])
        min_count = payoff_config.get("min_per_chapter")
        if preferred or min_count is not None:
            parts.append("<h2>微兑现要求</h2>")
            rows = []
            if preferred:
                tags = "".join(f'<span class="tag">{_esc(str(t))}</span>' for t in preferred)
                rows.append(
                    f'<div class="kv-row"><span class="kv-label">偏好类型</span> {tags}</div>'
                )
            if min_count is not None:
                rows.append(
                    f'<div class="kv-row"><span class="kv-label">每章最低</span> {_esc(str(min_count))} 个</div>'
                )
            if rows:
                parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Strand config
    strand_config = data.get("strand_config")
    if isinstance(strand_config, dict):
        parts.append("<h2>三线节奏阈值</h2>")
        rows = []
        if strand_config.get("quest_max_consecutive") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">Quest线最大连续</span> {_esc(str(strand_config["quest_max_consecutive"]))} 章</div>'
            )
        if strand_config.get("fire_max_absent") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">Fire线最大断档</span> {_esc(str(strand_config["fire_max_absent"]))} 章</div>'
            )
        if strand_config.get("constellation_max_absent") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">Constellation线最大断档</span> {_esc(str(strand_config["constellation_max_absent"]))} 章</div>'
            )
        if strand_config.get("stagnation_threshold") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">停滞预警阈值</span> {_esc(str(strand_config["stagnation_threshold"]))} 章</div>'
            )
        if rows:
            parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Pacing config
    pacing_config = data.get("pacing_config")
    if isinstance(pacing_config, dict):
        parts.append("<h2>节奏参数</h2>")
        rows = []
        if pacing_config.get("min_tension_chapters") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">低张力上限</span> {_esc(str(pacing_config["min_tension_chapters"]))} 章</div>'
            )
        if pacing_config.get("climax_spacing_chapters") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">高潮间隔</span> {_esc(str(pacing_config["climax_spacing_chapters"]))} 章</div>'
            )
        if rows:
            parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Hook score config
    score_config = data.get("hook_score_config")
    if isinstance(score_config, dict):
        parts.append("<h2>追读力评分权重</h2>")
        rows = []
        if score_config.get("hook_score_strong") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">强钩子</span> {_esc(str(score_config["hook_score_strong"]))} 分</div>'
            )
        if score_config.get("hook_score_medium") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">中钩子</span> {_esc(str(score_config["hook_score_medium"]))} 分</div>'
            )
        if score_config.get("hook_score_weak") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">弱钩子</span> {_esc(str(score_config["hook_score_weak"]))} 分</div>'
            )
        if score_config.get("payoff_cap") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">兑现计分上限</span> {_esc(str(score_config["payoff_cap"]))}</div>'
            )
        if score_config.get("transition_penalty") is not None:
            rows.append(
                f'<div class="kv-row"><span class="kv-label">过渡章惩罚</span> {_esc(str(score_config["transition_penalty"]))} 分</div>'
            )
        if rows:
            parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Writing modules
    modules = data.get("modules", [])
    if isinstance(modules, list) and modules:
        parts.append("<h2>写作技法规范</h2>")
        for mod in modules:
            if not isinstance(mod, dict):
                continue
            mod_name = mod.get("name", "")
            rules = mod.get("rules", [])
            positive = mod.get("positive_example", "")
            negative = mod.get("negative_example", "")
            if not mod_name and not rules:
                continue
            parts.append(f'<div class="section"><h3>{_esc(mod_name)}</h3>')
            if rules:
                for rule in rules:
                    parts.append(f'<div class="rule-item">· {_esc(str(rule))}</div>')
            if positive:
                parts.append(f'<div class="theme-item">✓ {_esc(positive)}</div>')
            if negative:
                parts.append(
                    f'<div class="rule-item" style="border-left-color:rgba([[nf:accent.primary]], 0.6);">✗ {_esc(negative)}</div>'
                )
            parts.append("</div>")

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无风格规范数据</div>'
        )
    return _make_browser(_html_wrap("\n".join(parts), "风格规范"))


def _editorial_text(value: Any) -> str:
    return str(value or "").strip()


def _editorial_text_list(value: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value[:limit]:
        text = _editorial_text(raw)
        if text:
            items.append(text)
    return items


def _editorial_join(value: Any, *, limit: int = 5, empty: str = "—") -> str:
    items = _editorial_text_list(value, limit=limit)
    if not items:
        return empty
    suffix = ""
    if isinstance(value, list) and len(value) > limit:
        suffix = f"；另有 {len(value) - limit} 项"
    return "；".join(items) + suffix


def render_editorial_contract(data: JsonDict) -> QTextBrowser:
    """Render plans/editorial_contract.json with character voices first."""

    parts: list[str] = []
    project_title = _editorial_text(data.get("project_title"))
    voices = data.get("character_voices")
    climax_markers = data.get("climax_markers")
    theme_policies = data.get("theme_policies")
    symbol_policies = data.get("symbol_policies")
    scene_rules = data.get("scene_resistance_rules")
    directives = data.get("editorial_element_directives")
    revision_priorities = data.get("revision_priorities")
    forbidden_phrases = data.get("forbidden_confirmation_phrases")
    channel_budget = data.get("expression_channel_budget")

    if project_title:
        parts.append(f'<div class="hint-block">项目：{_esc(project_title)}</div>')

    count_rows = [
        _fragment_count_row("角色声纹", voices),
        _fragment_count_row("高潮标记", climax_markers),
        _fragment_count_row("主题规则", theme_policies),
        _fragment_count_row("象征物策略", symbol_policies),
        _fragment_count_row("场景阻力", scene_rules),
        _fragment_count_row("要素指令", directives),
        f"<td>{_esc('表达预算')}</td><td><b>{_fragment_dict_count(channel_budget)}</b></td>",
    ]
    parts.append(
        "<h2>契约概览</h2><table><tbody>"
        + "".join(f"<tr>{row}</tr>" for row in count_rows)
        + "</tbody></table>"
    )

    if isinstance(voices, list) and voices:
        parts.append("<h2>角色声纹</h2>")
        for raw in voices:
            if not isinstance(raw, dict):
                continue
            character = _editorial_text(raw.get("character")) or "未命名角色"
            sentence = _editorial_text(raw.get("sentence_profile")) or "未提供句式画像"
            explanation = _editorial_text(raw.get("explanation_bias")) or "未提供解释倾向"
            emotion = _editorial_text(raw.get("emotion_syntax")) or "未提供情绪句法"
            signature = _editorial_join(raw.get("signature_moves"))
            taboo = _editorial_join(raw.get("taboo_patterns"))
            samples = _editorial_join(raw.get("sample_lines"), limit=3)
            parts.append(
                '<div class="section">'
                f"<h3>{_esc(character)}</h3>"
                f'<div class="kv-row"><span class="kv-label">句式画像</span> '
                f'<span class="kv-value">{_nl2br(sentence)}</span></div>'
                f'<div class="kv-row"><span class="kv-label">解释倾向</span> '
                f'<span class="kv-value">{_nl2br(explanation)}</span></div>'
                f'<div class="kv-row"><span class="kv-label">情绪句法</span> '
                f'<span class="kv-value">{_nl2br(emotion)}</span></div>'
                f'<div class="kv-row"><span class="kv-label">标志动作/句法</span> '
                f'<span class="kv-value">{_nl2br(signature)}</span></div>'
                f'<div class="kv-row"><span class="kv-label">禁用口吻</span> '
                f'<span class="kv-value">{_nl2br(taboo)}</span></div>'
                f'<div class="kv-row"><span class="kv-label">样例台词</span> '
                f'<span class="kv-value">{_nl2br(samples)}</span></div>'
                "</div>"
            )
    else:
        parts.append('<div class="hint-block">暂无角色声纹数据。</div>')

    if isinstance(revision_priorities, list) and revision_priorities:
        parts.append("<h2>修订优先级</h2>")
        for item in revision_priorities[:8]:
            text = _editorial_text(item)
            if text:
                parts.append(f'<div class="rule-item">· {_nl2br(text)}</div>')

    if isinstance(channel_budget, dict) or forbidden_phrases:
        parts.append("<h2>语言执行约束</h2>")
        budget_rows: list[str] = []
        if isinstance(channel_budget, dict):
            for key, value in channel_budget.items():
                budget_rows.append(
                    f'<div class="kv-row"><span class="kv-label">{_esc(generic_key_label(str(key)))}</span> '
                    f'<span class="kv-value">{_esc(str(value))}</span></div>'
                )
        if forbidden_phrases:
            budget_rows.append(
                '<div class="kv-row"><span class="kv-label">禁用确认句</span> '
                f'<span class="kv-value">{_nl2br(_editorial_join(forbidden_phrases, limit=8))}</span></div>'
            )
        if budget_rows:
            parts.append('<div class="section">' + "\n".join(budget_rows) + "</div>")

    if not parts:
        parts.append('<div class="hint-block">暂无编辑契约数据。</div>')
    return _make_browser(_html_wrap("\n".join(parts), "编辑契约"))
