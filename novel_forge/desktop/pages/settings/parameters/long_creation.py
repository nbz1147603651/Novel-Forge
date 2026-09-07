"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains long_creation.py builders.
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


def _build_long_creation_params(
    s: Any,
    profile_choices: list[tuple[str, str, bool]],
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 5: Long Novel — Creation parameters."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("长篇生成 — 章节写作与节拍", expanded=False)

    add_setting_group_header(sec.body_layout, "章节节拍", "控制章节规划的节拍数量与场景颗粒度。")
    row, widgets["_long_beats_min"] = make_spin_setting(
        "最少节拍数",
        "推荐 3-6",
        s.long_plan_beats_min,
        1,
        20,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_long_beats_max"] = make_spin_setting(
        "最多节拍数",
        "推荐 6-10",
        s.long_plan_beats_max,
        1,
        30,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_long_scene_switches"] = make_spin_setting(
        "场景切换上限",
        "章节计划 scene_intents 的有效上限；执行时会至少放宽到本章大纲节拍数，推荐 8",
        int(getattr(s, "long_plan_max_scene_switches", 8)),
        2,
        12,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_long_sensory_anchor_limit"] = make_spin_setting(
        "每场感官候选上限",
        "Plan 可构思多候选，但 Draft/Edit 只接收排序后的前 N 个；1=更克制，2=默认",
        int(getattr(s, "long_plan_sensory_notes_max_items", 2)),
        1,
        2,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_long_beat_chars"] = make_spin_setting(
        "节拍描述字符上限",
        "推荐 200-320",
        s.long_plan_beat_max_chars,
        50,
        1000,
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(sec.body_layout, "草稿与编辑", "控制角色出场补全和归档前润色。")
    row, widgets["_auto_introduce_chars"] = make_combo_setting(
        "自动角色出场",
        "章节生成前自动检测并生成新出场角色档案",
        ["true", "false"],
        current=str(s.auto_introduce_characters).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_auto_introduce_char_limit"] = make_spin_setting(
        "每章新增角色上限",
        "限制自动写入人物设定的新角色数量；0=不新增，推荐 1-3",
        s.long_auto_introduce_max_new_characters,
        0,
        10,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_long_polish_enabled"] = make_combo_setting(
        "精修润色",
        "审核后、归档前对正文做文学性打磨（增加 token 消耗）",
        ["true", "false"],
        current=str(s.long_polish_enabled).lower(),
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(sec.body_layout, "润色与人味", "控制 AI 去痕检测和轻量文本修整。")
    row, widgets["_humanize_enabled"] = make_combo_setting(
        "启用 AI 去痕",
        "章节生成后自动检测并修复 AI 写作痕迹（默认关闭）",
        ["true", "false"],
        current=str(s.humanize_enabled).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_change_ratio_cap"] = make_float_setting(
        "变更率上限",
        "AI 去痕允许的最大文本变更比例（推荐 0.03-0.08）",
        float(getattr(s, "humanize_change_ratio_cap", 0.05)),
        0.01,
        0.30,
        decimals=2,
        step=0.01,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_min_text_length"] = make_spin_setting(
        "最短触发字数",
        "低于此字数的章节不触发 AI 去痕（推荐 300-1000）",
        int(getattr(s, "humanize_min_text_length", 500)),
        100,
        10000,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_model"] = _make_model_combo_setting(
        "去痕专用模型",
        "留空使用默认路由；格式如 deepseek:deepseek-chat",
        profile_choices,
        current=str(getattr(s, "humanize_model", "")),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_patch_confidence_floor"] = make_float_setting(
        "精确补丁置信门槛",
        "命中置信度低于此值时只记录报告，不执行精确替换（推荐 0.75-0.90）",
        float(getattr(s, "humanize_patch_confidence_floor", 0.80)),
        0.0,
        1.0,
        decimals=2,
        step=0.01,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_paragraph_confidence_floor"] = make_float_setting(
        "段落改写置信门槛",
        "段落级定向改写只收集高于此值的 AI 痕迹命中（推荐 0.65-0.85）",
        float(getattr(s, "humanize_paragraph_confidence_floor", 0.70)),
        0.0,
        1.0,
        decimals=2,
        step=0.01,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_humanize_library_sim_threshold"] = make_float_setting(
        "拟人化库相似阈值",
        "示例库检索的最低匹配度；越高越保守，越低召回越多（推荐 0.45-0.70）",
        float(getattr(s, "humanize_library_sim_threshold", 0.60)),
        0.0,
        1.0,
        decimals=2,
        step=0.01,
    )
    sec.body_layout.addWidget(row)

    return sec, widgets

