"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains init_protocol.py builders.
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
    make_line_setting,
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


def _add_init_protocol_controls(
    sec: CollapsibleSection,
    widgets: dict[str, Any],
    s: Any,
) -> None:
    protocol_sec = _make_settings_subsection(
        sec,
        "初始化叙事协议",
        "该组参数只影响“新项目初始化”生成的 narrative_contract；"
        "已初始化项目不会自动回写，需重新初始化或手动覆盖合同文件。",
    )
    layout = protocol_sec.body_layout
    row, widgets["_init_cont_protocol_enabled"] = make_combo_setting(
        "启用初始化协议",
        "是否在初始化阶段注入连贯性协议到 bridge/plan/draft",
        ["true", "false"],
        current=str(getattr(s, "init_continuity_protocol_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_cont_loc_required"] = make_combo_setting(
        "地点转场强制",
        "地点变化时是否强制要求开场交代位移动作链",
        ["true", "false"],
        current=str(getattr(s, "init_continuity_location_transition_required", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_cont_loc_window"] = make_spin_setting(
        "转场句窗（句）",
        "地点变化时，开场前 N 句需出现位移动作链（推荐 2-4）",
        int(getattr(s, "init_continuity_location_transition_window_sentences", 3)),
        1,
        6,
    )
    layout.addWidget(row)
    row, widgets["_init_cont_bridge_ratio"] = make_float_setting(
        "桥接窗口比例",
        "bridge 回声窗口 = 每章字数 × 比例（推荐 0.20-0.35）",
        float(getattr(s, "init_continuity_bridge_echo_ratio", 0.28)),
        0.10,
        0.60,
        decimals=2,
        step=0.01,
    )
    layout.addWidget(row)
    row, widgets["_init_cont_bridge_min"] = make_spin_setting(
        "桥接窗口下限（字）",
        "动态窗口下限（推荐 400-700）",
        int(getattr(s, "init_continuity_bridge_echo_min_chars", 450)),
        200,
        2000,
    )
    layout.addWidget(row)
    row, widgets["_init_cont_bridge_max"] = make_spin_setting(
        "桥接窗口上限（字）",
        "动态窗口上限（推荐 1000-1800）",
        int(getattr(s, "init_continuity_bridge_echo_max_chars", 1400)),
        300,
        4000,
    )
    layout.addWidget(row)
    row, widgets["_init_cont_time_profile"] = make_combo_setting(
        "时间制式",
        "auto=按语言自动；traditional_cn=传统时辰刻度；locale_default=本地常规时间",
        ["auto", "traditional_cn", "locale_default"],
        current=str(getattr(s, "init_continuity_time_notation_profile", "auto") or "auto").lower(),
        item_labels=["自动（推荐）", "传统时辰刻度（中文古风）", "本地常规时间表达"],
    )
    layout.addWidget(row)
    row, widgets["_init_cont_time_ke_range"] = make_line_setting(
        "传统时辰刻度范围",
        "仅 traditional_cn 生效，默认一至四刻",
        str(getattr(s, "init_continuity_traditional_time_ke_range", "一至四刻")),
    )
    layout.addWidget(row)
    row, widgets["_init_cont_pov_rule"] = make_line_setting(
        "POV可见性规则",
        "注入 bridge/plan/draft 的 POV 限制文本",
        str(
            getattr(
                s,
                "init_continuity_pov_visibility_rule",
                "限知视角仅描写可观察事实，禁止直接写非POV角色内心。",
            )
        ),
    )
    layout.addWidget(row)
    row, widgets["_init_cont_forbidden_rule"] = make_line_setting(
        "禁复用规则",
        "注入 bridge/plan/draft 的禁重复规则文本",
        str(
            getattr(
                s,
                "init_continuity_forbidden_repetition_rule",
                "禁复用仅针对修辞性意象；人物、实体与剧情锚点不在此限。"
                "命中后必须替换为全新意象，不得使用近义改写。",
            )
        ),
    )
    layout.addWidget(row)
    row, widgets["_init_cont_max_reveals"] = make_spin_setting(
        "单章揭示上限",
        "初始化协议中的单章重大揭示预算（推荐 1-3）",
        int(getattr(s, "init_continuity_max_key_revelations_per_chapter", 2)),
        1,
        8,
    )
    layout.addWidget(row)
    row, widgets["_init_cont_min_unresolved"] = make_spin_setting(
        "最少未决线索",
        "初始化协议中的悬念留存预算（0=不强制）",
        int(getattr(s, "init_continuity_min_unresolved_threads_to_keep", 1)),
        0,
        6,
    )
    layout.addWidget(row)

