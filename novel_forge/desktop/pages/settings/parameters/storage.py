"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains storage.py builders.
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


def _build_storage_params(
    s: Any,
    profile_choices: list[tuple[str, str, bool]],
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 11: Storage & Logging."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("存储、日志与格式修复", expanded=False)
    add_setting_group_header(sec.body_layout, "存储日志", "控制日志保留、API 超时和运行记录。")
    row, widgets["_log_level"] = make_combo_setting(
        "日志级别",
        "排查路由/模型返回/流程中断原因时有用",
        ["DEBUG", "INFO", "WARNING", "ERROR"],
        current=s.log_level,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_log_keep_runs"] = make_spin_setting(
        "日志保留数量",
        "保留最近 N 次运行记录（0=不限制）",
        s.log_keep_runs,
        0,
        100,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_api_timeout"] = make_spin_setting(
        "API 超时时间",
        "单位秒，推荐 600（10分钟）",
        int(s.api_call_timeout_s),
        30,
        3600,
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(
        sec.body_layout,
        "故事内核",
        "故事内核是初始化字段池与章节事实状态的持久化层。"
        "文件位置留空时，每个项目会自动使用项目目录下的 story_kernel.db。",
    )
    row, widgets["_story_kernel_db_path"] = make_line_setting(
        "数据库文件位置",
        "留空=自动使用项目目录；也可填普通 .db 文件路径",
        str(getattr(s, "story_kernel_db_path", "") or ""),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_story_kernel_wal_mode"] = make_combo_setting(
        "SQLite WAL 模式",
        "开启后提升读写并发稳定性；建议保持 true",
        ["true", "false"],
        current=str(getattr(s, "story_kernel_wal_mode", True)).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_story_kernel_zvec_enabled"] = make_combo_setting(
        "ZVec 语义增强",
        "为故事内核字段启用可选语义检索层；仅在安装 zvec 并需要语义召回时开启",
        ["false", "true"],
        current=str(getattr(s, "story_kernel_zvec_enabled", False)).lower(),
    )
    sec.body_layout.addWidget(row)

    add_setting_group_header(
        sec.body_layout, "格式修复", "控制结构化输出失败后的重试和专用修复模型。"
    )
    row, widgets["_llm_format_retry_attempts"] = make_spin_setting(
        "格式总尝试次数",
        "JSON/结构化输出格式错误时的总尝试次数（含首次调用，推荐 2-3）",
        int(getattr(s, "llm_format_retry_attempts", 2)),
        1,
        5,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_retry_temperature"] = make_float_setting(
        "格式重试温度",
        "格式重试时使用的低温度；越低越稳定（推荐 0.0）",
        float(getattr(s, "llm_format_retry_temperature", 0.0)),
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_retry_raw_char_limit"] = make_spin_setting(
        "重试错误原文上限",
        "普通格式重试时注入给原任务模型的上一轮错误输出字符预算",
        int(getattr(s, "llm_format_retry_raw_char_limit", 6000)),
        1000,
        60000,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_repair_enabled"] = make_combo_setting(
        "专用格式修复",
        "重试用尽前优先本地修复；本地无法安全修复时调用专用修复 LLM",
        ["true", "false"],
        current=str(getattr(s, "llm_format_repair_enabled", True)).lower(),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_repair_model"] = _make_model_combo_setting(
        "格式修复模型",
        "可选 provider:model；留空复用 repair_model 或原任务路由",
        profile_choices,
        current=str(getattr(s, "llm_format_repair_model", "") or ""),
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_repair_max_tokens"] = make_spin_setting(
        "格式修复 Token",
        "专用修复 LLM 的最大输出 token（推荐 4096-8192）",
        int(getattr(s, "llm_format_repair_max_tokens", 4096)),
        512,
        32768,
    )
    sec.body_layout.addWidget(row)
    row, widgets["_llm_format_repair_raw_char_limit"] = make_spin_setting(
        "修复原文字符上限",
        "发送给专用修复 LLM 的错误原文上限；过低可能丢失尾部字段",
        int(getattr(s, "llm_format_repair_raw_char_limit", 60000)),
        1000,
        200000,
    )
    sec.body_layout.addWidget(row)

    return sec, widgets

