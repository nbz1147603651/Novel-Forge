"""Parameter builder functions for the Settings page.

Each function builds one collapsible section of parameter controls and returns
a tuple of (section_widget, widgets_dict) so the caller can merge the dict
into the page's namespace.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
)

from novel_forge.desktop.notification_sounds import (
    NOTIFICATION_SOUND_CHOICES,
    preview_notification_sound,
)
from novel_forge.desktop.pages.settings.parameters.appearance import _build_theme_params
from novel_forge.desktop.pages.settings.parameters.debug import _build_debug_section
from novel_forge.desktop.pages.settings.parameters.init_coherence import (
    _add_init_coherence_controls,
)
from novel_forge.desktop.pages.settings.parameters.init_protocol import _add_init_protocol_controls
from novel_forge.desktop.pages.settings.parameters.long_context import _build_long_context_params
from novel_forge.desktop.pages.settings.parameters.long_creation import _build_long_creation_params
from novel_forge.desktop.pages.settings.parameters.long_initialization import (
    _build_long_initialization_params,
)
from novel_forge.desktop.pages.settings.parameters.long_temp import _build_long_temp_params
from novel_forge.desktop.pages.settings.parameters.memory import _build_memory_params
from novel_forge.desktop.pages.settings.parameters.ollama import _build_ollama_params
from novel_forge.desktop.pages.settings.parameters.quality_audit import _build_quality_audit_params
from novel_forge.desktop.pages.settings.parameters.reading_power import _build_reading_power_params
from novel_forge.desktop.pages.settings.parameters.research import (
    _build_research_params,
    research_preset_payload,
)
from novel_forge.desktop.pages.settings.parameters.storage import _build_storage_params
from novel_forge.desktop.pages.settings.parameters.temperature import (
    _build_temperature_jitter_params,
    collect_temperature_jitter_custom_tasks,
)
from novel_forge.desktop.pages.settings.parameters.tts import _build_tts_params
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_nested_setting_section,
    make_spin_setting,
)

__all__ = [
    "_add_init_coherence_controls",
    "_add_init_protocol_controls",
    "_append_compact_button",
    "_build_debug_section",
    "_build_desktop_notification_params",
    "_build_long_context_params",
    "_build_long_creation_params",
    "_build_long_initialization_params",
    "_build_long_temp_params",
    "_build_memory_params",
    "_build_ollama_params",
    "_build_quality_audit_params",
    "_build_reading_power_params",
    "_build_research_params",
    "_build_short_params",
    "_build_storage_params",
    "_build_temperature_jitter_params",
    "_build_theme_params",
    "_build_tts_params",
    "_make_model_combo_setting",
    "_make_settings_subsection",
    "collect_temperature_jitter_custom_tasks",
    "research_preset_payload",
]


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


def _build_desktop_notification_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Desktop notification sound controls."""

    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("桌面提醒 — 任务提示音", expanded=False)

    add_setting_group_description(
        sec.body_layout,
        "当前桌面端可触达用户的运行提醒分为：任务完成、等待人工决策、任务失败。"
        "提交与排队状态保持静默，避免批量任务启动时反复打扰。",
    )
    row, widgets["_desktop_notify_sound_enabled"] = make_combo_setting(
        "启用提示音",
        "关闭后所有任务状态变化只保留界面提示",
        ["true", "false"],
        current=str(getattr(s, "desktop_notification_sound_enabled", True)).lower(),
        item_labels=["开启", "关闭"],
    )
    sec.body_layout.addWidget(row)

    sound_keys = [key for key, _label in NOTIFICATION_SOUND_CHOICES]
    sound_labels = [label for _key, label in NOTIFICATION_SOUND_CHOICES]
    sound_rows = (
        (
            "_desktop_notify_success_sound",
            "任务完成",
            "短篇、立项、章节写作、修复、导出等任务成功结束时播放",
            getattr(s, "desktop_notification_success_sound", "chime"),
        ),
        (
            "_desktop_notify_decision_sound",
            "等待决策",
            "章节检查点或守卫流程需要人工处理时播放",
            getattr(s, "desktop_notification_decision_sound", "bell"),
        ),
        (
            "_desktop_notify_failure_sound",
            "任务失败",
            "非用户取消的任务失败时播放",
            getattr(s, "desktop_notification_failure_sound", "alert"),
        ),
    )
    for key, label, hint, current in sound_rows:
        row, combo = make_combo_setting(
            label,
            hint,
            sound_keys,
            current=str(current or ""),
            item_labels=sound_labels,
        )
        widgets[key] = combo
        preview_btn = _append_compact_button(row, "试听")
        preview_btn.clicked.connect(
            lambda _checked=False, source=combo: preview_notification_sound(
                str(source.currentData() or source.currentText())
            )
        )
        sec.body_layout.addWidget(row)

    return sec, widgets
