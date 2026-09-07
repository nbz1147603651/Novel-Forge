"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains reading_power.py builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
)

from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_float_setting,
    make_nested_setting_section,
    make_spin_setting,
)

if TYPE_CHECKING:
    pass


def _make_model_combo_setting(
    label: str,
    hint: str,
    profile_choices: list[tuple[str, str, bool]],
    current: str = "",
) -> tuple[Any, QComboBox]:
    """Build a SettingRow with a model-profile dropdown.

    Mirrors _TaskRouteRow._populate_combo: first item is '(未指定)'
    with empty data, then each profile with disabled/grayed items when
    can_route is False.
    """
    row = SettingRow(label, hint)
    combo = QComboBox()
    combo.setAccessibleName(label)
    combo.addItem("（未指定）", "")
    model = combo.model()
    for profile_id, name, can_route in profile_choices:
        combo.addItem(name, profile_id)
        if isinstance(model, QStandardItemModel):
            item = model.item(combo.count() - 1)
            if item is None:
                continue
            item.setEnabled(can_route)
            if not can_route:
                item.setForeground(QColor("#a29689"))
                item.setToolTip(f"{name}\n未配置 API Key，当前不可用于流程调用")
            else:
                item.setToolTip(str(name))
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    row.set_input(combo)
    return row, combo


def _append_compact_button(row: Any, label: str, *, variant: str = "secondary") -> ActionButton:
    button = ActionButton(label, variant=variant)
    button.setProperty("compact", True)
    try:
        row._input_slot.setSpacing(8)
        row._input_slot.addWidget(button)
    except AttributeError:
        pass
    return button


def _make_settings_subsection(
    parent: CollapsibleSection,
    title: str,
    hint: str = "",
) -> Any:
    section = make_nested_setting_section(title, hint, expanded=False)
    parent.body_layout.addWidget(section)
    return section


def _build_short_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 4: Short Story Parameters."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("短篇生成参数", expanded=False)

    add_setting_group_header(sec.body_layout, "短篇编辑", "控制短篇生成后的编辑迭代。")
    row, widgets["_short_max_edit"] = make_spin_setting(
        "最大编辑轮次",
        "越高越稳，但更慢更贵（推荐 1-3）",
        s.short_max_edit_rounds,
        0,
        10,
    )
    sec.body_layout.addWidget(row)

    return sec, widgets


def _build_reading_power_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 7.5: Reading Power Window."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("追读力 — 评估修复与下章提示", expanded=False)
    add_setting_group_description(
        sec.body_layout,
        "追读力分为两条链路：本章评估/修复用于处理明显的钩子缺失、兑现断裂与张力失衡；"
        "窗口提示用于把悬念、钩子和张力节奏投喂给下一章规划。",
    )

    add_setting_group_header(sec.body_layout, "评估修复", "控制本章追读力评估后的定向修复。")
    row, widgets["_rp_repair_enabled"] = make_combo_setting(
        "启用追读力修复",
        "低于修复线或出现核心追读问题时，尝试定向修复本章",
        ["true", "false"],
        current=str(getattr(s, "long_reading_power_repair_enabled", True)).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_repair_threshold"] = make_float_setting(
        "修复触发线",
        "追读力评分低于此值时进入修复循环（推荐 6.0）",
        float(getattr(s, "long_reading_power_repair_threshold", 6.0)),
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_max_repair_rounds"] = make_spin_setting(
        "最大修复轮数",
        "0=不自动修复；推荐 1-2，过高可能扰动章节结构",
        int(getattr(s, "long_reading_power_max_repair_rounds", 2)),
        0,
        5,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_repair_change_ratio"] = make_float_setting(
        "单轮改动上限",
        "追读力修复允许的最大文本改动比例（0.20=20%）",
        float(getattr(s, "long_reading_power_repair_max_change_ratio", 0.20)),
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(sec.body_layout, "窗口策略", "控制跨章追读力提示的历史与预览窗口。")
    row, widgets["_rp_enabled"] = make_combo_setting(
        "启用窗口提示",
        "开启后在规划/桥接阶段注入跨章追读力提示；不等于自动修复",
        ["true", "false"],
        current=str(s.reading_power_enabled).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_window_size"] = make_spin_setting(
        "窗口大小",
        "覆盖章节数（推荐 3-10）",
        s.reading_power_window_size,
        3,
        10,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_window_left_offset"] = make_spin_setting(
        "窗口左偏移",
        "向左扩展的历史章节数（负值表示向前扩展）",
        s.reading_power_window_left_offset,
        -5,
        0,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_window_right_offset"] = make_spin_setting(
        "窗口右偏移",
        "向右扩展的预览章节数",
        s.reading_power_window_right_offset,
        0,
        5,
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(sec.body_layout, "悬念预算", "控制悬念延迟预警和强制兑现阈值。")
    row, widgets["_rp_suspense_delay_threshold"] = make_spin_setting(
        "悬念延迟阈值",
        "超过此章数未兑现则触发预警",
        s.reading_power_suspense_delay_threshold,
        1,
        7,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_force_resolve_threshold"] = make_spin_setting(
        "强制兑现阈值",
        "超过此章数必须在下章兑现悬念",
        s.reading_power_force_resolve_threshold,
        3,
        10,
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(sec.body_layout, "下章提示", "控制钩子类型交替和张力偏差容忍度。")
    row, widgets["_rp_hook_alternation_threshold"] = make_spin_setting(
        "钩子交替阈值",
        "连续相同类型钩子的容忍度（推荐 1-4）",
        s.reading_power_hook_alternation_threshold,
        1,
        4,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_rp_tension_deviation_tolerance"] = make_float_setting(
        "张力偏差容忍",
        "实际张力与预期张力的最大偏差",
        s.reading_power_tension_deviation_tolerance,
        0.5,
        3.0,
        decimals=1,
        step=0.1,
    )
    sec.body_layout.addWidget(row)

    return sec, widgets

