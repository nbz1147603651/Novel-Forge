"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Migration P2-step (cluster D, sub reading_power_window): ``render_reading_power_window_config`` reads reading_power_window_config from style_profile.json.

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

__all__ = ['render_reading_power_window_config']


def render_reading_power_window_config(data: JsonDict) -> QTextBrowser:
    """Render reading_power_window_config from style_profile.json."""
    parts: list[str] = []
    cfg = data.get("reading_power_window_config")
    if not isinstance(cfg, dict):
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无追读力窗口配置数据</div>'
        )
        return _make_browser(_html_wrap("\n".join(parts), "追读力窗口配置"))

    # System status
    enabled = cfg.get("enabled", True)
    parts.append(
        f'<div style="text-align:center; margin-bottom:12px;">'
        f'<span class="tag {"tag" if enabled else "tag-muted"}>{"已启用" if enabled else "已禁用"}</span></div>'
    )

    # Window boundaries
    parts.append("<h2>窗口边界</h2>")
    rows = []
    if cfg.get("window_size") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">窗口大小</span> {_esc(str(cfg["window_size"]))} 章</div>'
        )
    if cfg.get("window_left_offset") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">左偏移</span> {_esc(str(cfg["window_left_offset"]))}</div>'
        )
    if cfg.get("window_right_offset") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">右偏移</span> {_esc(str(cfg["window_right_offset"]))}</div>'
        )
    if rows:
        parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Suspense tracking
    parts.append("<h2>悬念追踪</h2>")
    rows = []
    if cfg.get("suspense_delay_threshold") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">延迟阈值</span> {_esc(str(cfg["suspense_delay_threshold"]))} 章</div>'
        )
    if cfg.get("force_resolve_threshold") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">强制兑现</span> {_esc(str(cfg["force_resolve_threshold"]))} 章</div>'
        )
    if rows:
        parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Hook alternation
    parts.append("<h2>钩子交替</h2>")
    rows = []
    if cfg.get("hook_alternation_threshold") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">交替阈值</span> {_esc(str(cfg["hook_alternation_threshold"]))}</div>'
        )
    if cfg.get("max_consecutive_same_hook") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">最大连续同类型</span> {_esc(str(cfg["max_consecutive_same_hook"]))}</div>'
        )
    if rows:
        parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Tension matching
    parts.append("<h2>张力匹配</h2>")
    rows = []
    if cfg.get("tension_deviation_tolerance") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">偏差容忍度</span> {_esc(str(cfg["tension_deviation_tolerance"]))}</div>'
        )
    if cfg.get("tension_recovery_factor") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">恢复因子</span> {_esc(str(cfg["tension_recovery_factor"]))}</div>'
        )
    if rows:
        parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # Scoring weights
    weights = cfg.get("hook_strength_weights")
    if isinstance(weights, dict) and weights:
        parts.append("<h2>评分权重</h2>")
        weight_rows = []
        for key, val in weights.items():
            label = {
                "hook_strength": "钩子强度",
                "payoff_density": "兑现密度",
                "suspense_timing": "悬念时机",
                "hook_alternation": "钩子交替",
                "tension_match": "张力匹配",
            }.get(str(key), str(key))
            weight_rows.append(
                f'<div class="kv-row"><span class="kv-label">{_esc(label)}</span> {_esc(str(round(float(val) * 100)))}%</div>'
            )
        if cfg.get("payoff_cap") is not None:
            weight_rows.append(
                f'<div class="kv-row"><span class="kv-label">兑现计分上限</span> {_esc(str(cfg["payoff_cap"]))}</div>'
            )
        parts.append('<div class="section">' + "\n".join(weight_rows) + "</div>")

    # Alert thresholds
    parts.append("<h2>预警阈值</h2>")
    rows = []
    if cfg.get("critical_score_threshold") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">临界阈值</span> <span style="color:[[nf:accent.primary]];">{_esc(str(cfg["critical_score_threshold"]))}</span></div>'
        )
    if cfg.get("warning_score_threshold") is not None:
        rows.append(
            f'<div class="kv-row"><span class="kv-label">警告阈值</span> <span style="color:[[nf:accent.rank.label]];">{_esc(str(cfg["warning_score_threshold"]))}</span></div>'
        )
    if rows:
        parts.append('<div class="section">' + "\n".join(rows) + "</div>")

    # System toggles
    toggles = []
    if cfg.get("enable_force_resolve"):
        toggles.append("强制兑现检查")
    if cfg.get("enable_hook_alternation_check"):
        toggles.append("钩子交替检查")
    if cfg.get("enable_tension_recovery"):
        toggles.append("张力恢复计算")
    if toggles:
        tags = "".join(f'<span class="tag">{_esc(t)}</span>' for t in toggles)
        parts.append(f'<div class="section"><div class="kv-row">{tags}</div></div>')

    if not parts:
        parts.append(
            '<div style="color:[[nf:text.muted]]; padding:32px; text-align:center;">暂无追读力窗口配置数据</div>'
        )
    return _make_browser(_html_wrap("\n".join(parts), "追读力窗口配置"))

