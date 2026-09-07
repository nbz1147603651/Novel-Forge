"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains temperature.py builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
)

from novel_forge.core.parsing.temperature_jitter import (
    CREATIVE_TEMPERATURE_SCOPE_CUSTOM,
    is_temperature_jitter_protected,
    parse_temperature_task_keys,
    resolve_temperature_jitter_tasks,
    serialize_temperature_task_keys,
)
from novel_forge.core.task_catalog import (
    ROUTING_GROUPS,
)
from novel_forge.desktop.pages.settings.specs import (
    CREATIVE_TEMPERATURE_SETTING_GROUP,
    render_setting_group,
)
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
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


def _build_temperature_jitter_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Creative temperature jitter controls."""

    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("创作火候 — 浮动与适用范围", expanded=False)

    add_setting_group_description(sec.body_layout, CREATIVE_TEMPERATURE_SETTING_GROUP.description)
    add_setting_group_header(sec.body_layout, "基础浮动", "设置基础火候附近的随机上下浮动范围。")

    widgets.update(render_setting_group(sec.body_layout, s, CREATIVE_TEMPERATURE_SETTING_GROUP))

    current_scope = str(getattr(s, "creative_temperature_jitter_scope", "recommended") or "")
    custom_tasks = parse_temperature_task_keys(
        getattr(s, "creative_temperature_jitter_custom_tasks", "") or ""
    )
    if current_scope == CREATIVE_TEMPERATURE_SCOPE_CUSTOM:
        checked_tasks = custom_tasks
    else:
        checked_tasks = custom_tasks or resolve_temperature_jitter_tasks(scope="recommended")

    custom_sec = make_nested_setting_section(
        "高级：自定义浮动任务",
        "仅在适用范围选择“自定义”时生效；灰色任务受保护，保持固定温度。",
        expanded=current_scope == "custom",
    )

    task_checks: dict[str, QCheckBox] = {}
    for group in ROUTING_GROUPS:
        add_setting_group_header(custom_sec.body_layout, group.name)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for index, task in enumerate(group.tasks):
            task_type = task.task_type
            checkbox = QCheckBox(task.label)
            checkbox.setToolTip(task.hint)
            checkbox.setChecked(task_type in checked_tasks)
            if is_temperature_jitter_protected(task_type):
                checkbox.setChecked(False)
                checkbox.setEnabled(False)
                checkbox.setToolTip("保护任务：检查、抽取、裁判、修复或审计链路保持固定温度。")
            task_checks[task.key] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        custom_sec.body_layout.addLayout(grid)

    widgets["_creative_temp_jitter_custom_section"] = custom_sec
    widgets["_creative_temp_jitter_task_checks"] = task_checks
    sec.body_layout.addWidget(custom_sec)

    scope_combo = widgets.get("_creative_temp_jitter_scope")

    def _sync_custom_visibility() -> None:
        try:
            is_custom = scope_combo.currentData() == CREATIVE_TEMPERATURE_SCOPE_CUSTOM
        except AttributeError:
            is_custom = False
        custom_sec.setVisible(is_custom)
        if is_custom:
            custom_sec.set_expanded(True)

    try:
        scope_combo.currentIndexChanged.connect(lambda _idx: _sync_custom_visibility())
    except AttributeError:
        pass
    _sync_custom_visibility()

    return sec, widgets


def collect_temperature_jitter_custom_tasks(widgets: dict[str, Any]) -> str:
    """Return comma-separated custom jitter task keys from the checkbox group."""

    checks = widgets.get("_creative_temp_jitter_task_checks", {})
    if not isinstance(checks, dict):
        return ""
    selected: list[str] = []
    for task_key, checkbox in checks.items():
        try:
            if checkbox.isEnabled() and checkbox.isChecked():
                selected.append(str(task_key))
        except AttributeError:
            continue
    return serialize_temperature_task_keys(selected)

