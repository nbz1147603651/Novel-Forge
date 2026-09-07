"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains long_temp.py builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
)

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.temperature_jitter import (
    is_temperature_jitter_protected,
)
from novel_forge.core.task_catalog import (
    LONG_TEMPERATURE_TASKS,
)
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_header,
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


def _build_long_temp_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 9: Temperature Fine-tuning."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("温度微调", expanded=False)
    widgets["_long_temp_spins"] = {}

    init_keys = {
        TaskType.INIT_STORY_BIBLE.value,
        TaskType.INIT_CHARACTER_BIBLE.value,
        TaskType.PROFILE_STYLE.value,
        TaskType.PROFILE_STRUCTURE.value,
        TaskType.BLUEPRINT_ELEMENT_SELECT.value,
        TaskType.PLAN_OUTLINE.value,
        TaskType.PLAN_OUTLINE_BATCH.value,
        TaskType.PLAN_OUTLINE_CONTINUE.value,
        TaskType.POLISH_OUTLINE.value,
        TaskType.PLAN_CHAPTER_CONTRACTS.value,
        TaskType.INIT_ENTITY_REGISTRY.value,
        TaskType.REFINE_INIT_COHERENCE_PROFILE.value,
        TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS.value,
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS.value,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS.value,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES.value,
        TaskType.REPAIR_INIT_ARTIFACT_PATCH.value,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE.value,
        TaskType.INIT_KNOWLEDGE_BOUNDARIES.value,
    }
    chapter_keys = {
        TaskType.PLAN_CHAPTER.value,
        TaskType.BRIDGE_CHAPTER.value,
        TaskType.DRAFT_CHAPTER.value,
        TaskType.WAVE_CHAPTER.value,
        TaskType.EDIT_CHAPTER.value,
        TaskType.POLISH_CHAPTER.value,
        TaskType.CHECK_CHAPTER.value,
        TaskType.CHECK_ALIGNMENT.value,
        TaskType.KNOWLEDGE_BOUNDARY_AUDIT.value,
        TaskType.CHECK_CONTINUITY.value,
        TaskType.VALIDATE_CAUSAL.value,
        TaskType.EVALUATE_READING_POWER.value,
        TaskType.REPAIR_CONTINUITY.value,
        TaskType.REPAIR_CAUSAL.value,
        TaskType.REPAIR_READING_POWER.value,
        TaskType.REPAIR_KNOWLEDGE_BOUNDARY.value,
        TaskType.EXTRACT_KNOWLEDGE_DELTAS.value,
        TaskType.PATCH_CHAPTER.value,
        TaskType.EXTRACT_CANON.value,
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS.value,
        TaskType.ADJUDICATE_STATE_DELTA.value,
        TaskType.ADJUDICATE_FINAL_STATE.value,
        TaskType.REPAIR_ADJUDICATED_ISSUE.value,
        TaskType.MACRO_GUARD_AUDIT.value,
    }

    def add_temperature_group(title: str, keys: set[str]) -> None:
        add_setting_group_header(sec.body_layout, title)
        for task in LONG_TEMPERATURE_TASKS:
            if task.key not in keys:
                continue
            val = getattr(s, task.setting_attr)
            row, spin = make_float_setting(
                task.label,
                (
                    f"固定温度 {val}；受保护任务不参与浮动"
                    if is_temperature_jitter_protected(task.task_type)
                    else f"基础火候 {val}；适用范围包含时会随机"
                ),
                val,
                0.0,
                2.0,
                decimals=2,
                step=0.1,
            )
            widgets["_long_temp_spins"][task.key] = spin
            sec.body_layout.addWidget(row)

    add_temperature_group("初始化温度", init_keys)
    add_temperature_group("章节创作温度", chapter_keys)

    remaining_keys = {task.key for task in LONG_TEMPERATURE_TASKS} - init_keys - chapter_keys
    add_temperature_group("维护与记忆温度", remaining_keys)

    return sec, widgets

