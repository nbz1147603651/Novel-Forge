"""Appearance settings for the desktop settings page."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from novel_forge.desktop.theme.palettes import (
    DEFAULT_DESKTOP_THEME_ID,
    desktop_theme_choices,
    list_desktop_themes,
    normalize_theme_id,
)
from novel_forge.desktop.tokens.typography import (
    FONT_SCALE_CHOICES,
    READING_FONT_CHOICES,
    UI_FONT_CHOICES,
    normalize_font_scale,
)
from novel_forge.desktop.widgets import (
    CollapsibleSection,
    add_setting_group_description,
    make_combo_setting,
)


def _build_theme_params(s: Any) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Build the desktop theme selector."""

    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("界面主题 — 配色与风格", expanded=False)

    add_setting_group_description(
        sec.body_layout,
        "选择桌面配色，用于侧栏、卡片、按钮与状态提示。",
    )

    choices = desktop_theme_choices()
    values = [theme_id for theme_id, _label in choices]
    labels = [label for _theme_id, label in choices]
    current = normalize_theme_id(getattr(s, "desktop_theme", DEFAULT_DESKTOP_THEME_ID))
    row, combo = make_combo_setting(
        "桌面主题",
        "选择后立即预览，保存后作为默认主题",
        values,
        current=current,
        item_labels=labels,
    )
    for index, theme in enumerate(list_desktop_themes()):
        combo.setItemData(index, theme.description, Qt.ItemDataRole.ToolTipRole)
    widgets["_desktop_theme"] = combo
    sec.body_layout.addWidget(row)

    ui_font_row, ui_font_combo = make_combo_setting(
        "界面字体",
        "用于表单、按钮、任务卡与导航；切换后立即预览，保存后成为默认设置",
        [value for value, _label in UI_FONT_CHOICES],
        current=str(getattr(s, "desktop_ui_font_family", "source_sans")),
        item_labels=[label for _value, label in UI_FONT_CHOICES],
    )
    widgets["_desktop_ui_font_family"] = ui_font_combo
    sec.body_layout.addWidget(ui_font_row)

    reading_font_row, reading_font_combo = make_combo_setting(
        "阅读字体",
        "用于正文、报告与文学标题；切换后立即预览，且与新 UI 使用同一配置",
        [value for value, _label in READING_FONT_CHOICES],
        current=str(getattr(s, "desktop_reading_font_family", "source_serif")),
        item_labels=[label for _value, label in READING_FONT_CHOICES],
    )
    widgets["_desktop_reading_font_family"] = reading_font_combo
    sec.body_layout.addWidget(reading_font_row)

    font_scale = normalize_font_scale(getattr(s, "desktop_font_scale", 1.0))
    font_scale_row, font_scale_combo = make_combo_setting(
        "字体缩放",
        "100% 为 1440×900 复刻基线；切换后立即预览，保存后成为默认设置",
        [value for value, _label in FONT_SCALE_CHOICES],
        current=f"{font_scale:.1f}",
        item_labels=[label for _value, label in FONT_SCALE_CHOICES],
    )
    widgets["_desktop_font_scale"] = font_scale_combo
    sec.body_layout.addWidget(font_scale_row)

    pet_row, pet_combo = make_combo_setting(
        "Nimo 案头宠物",
        "在页面右下角显示任务状态、流式输出与 Token 用量；按 Ctrl+Alt+N（macOS：Control+Option+N）可隐藏或召回",
        ["true", "false"],
        current=str(bool(getattr(s, "desktop_pet_visible", True))).lower(),
        item_labels=["显示", "隐藏"],
    )
    widgets["_desktop_pet_visible"] = pet_combo
    sec.body_layout.addWidget(pet_row)
    return sec, widgets
