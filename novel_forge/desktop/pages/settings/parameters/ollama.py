"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains ollama.py builders.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
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


def _build_ollama_params(
    s: Any,
    build_ollama_model_panel: Callable[[], QWidget],
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 10: Ollama Local Models."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("Ollama 本地模型 — 免费、隐私保护", expanded=False)
    ollama_columns = QGridLayout()
    ollama_columns.setContentsMargins(0, 0, 0, 0)
    ollama_columns.setHorizontalSpacing(18)
    ollama_columns.setVerticalSpacing(12)
    ollama_columns.setColumnStretch(0, 0)
    ollama_columns.setColumnStretch(1, 1)

    ollama_config = QWidget()
    ollama_config.setMaximumWidth(760)
    ollama_config_layout = QVBoxLayout(ollama_config)
    ollama_config_layout.setContentsMargins(0, 0, 0, 0)
    ollama_config_layout.setSpacing(12)

    add_setting_group_description(
        ollama_config_layout,
        "Ollama 允许你在本地运行开源大模型，无需 API Key，完全免费。"
        "左侧提交 Engine 运行配置，右侧只观察和控制 Engine 主机上的持久模型任务。",
    )
    add_setting_group_header(ollama_config_layout, "Ollama", "控制 Engine 主机上的服务地址和默认模型。")

    row, widgets["_ollama_base_url"] = make_line_setting(
        "服务地址",
        "Ollama API 地址（默认：http://localhost:11434/v1）",
        s.ollama_base_url,
    )
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_model"] = make_line_setting(
        "生成模型",
        "用于文本生成的模型（推荐：llama3.2）",
        s.ollama_model,
    )
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_embedding_model"] = make_line_setting(
        "嵌入模型",
        "用于语义检索的嵌入模型（推荐：nomic-embed-text）",
        s.ollama_embedding_model,
    )
    ollama_config_layout.addWidget(row)

    add_setting_group_header(
        ollama_config_layout, "本地 Sidecar", "控制桌面版 bundled Ollama 接管策略。"
    )

    row, widgets["_ollama_sidecar_enabled"] = make_combo_setting(
        "启用 sidecar",
        "允许 Engine 自动接管 bundled Ollama；关闭后只连接外部/系统 Ollama 服务",
        ["true", "false"],
        current=str(s.ollama_sidecar_enabled).lower(),
    )
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_sidecar_auto_start"] = make_combo_setting(
        "自动拉起",
        "当 Engine 主机的服务地址不可用时，尝试自动启动 bundled Ollama",
        ["true", "false"],
        current=str(s.ollama_sidecar_auto_start).lower(),
    )
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_sidecar_prefer_local"] = make_combo_setting(
        "优先本地通路",
        "若 Engine 主机检测到托管 Ollama 可用，优先作为默认 Provider（仅在未显式指定默认通路时生效）",
        ["true", "false"],
        current=str(s.ollama_sidecar_prefer_local).lower(),
    )
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_sidecar_binary_path"] = make_line_setting(
        "可执行路径",
        "留空则自动搜索打包目录中的 vendor/ollama；也可指定二进制或其所在目录",
        s.ollama_sidecar_binary_path,
    )
    widgets["_ollama_sidecar_binary_browse_btn"] = _append_compact_button(row, "选择目录")
    ollama_config_layout.addWidget(row)
    row, widgets["_ollama_sidecar_models_dir"] = make_line_setting(
        "模型目录",
        "留空则跟随 bundled Ollama 自动推断；填写后会作为 OLLAMA_MODELS 使用",
        s.ollama_sidecar_models_dir,
    )
    widgets["_ollama_sidecar_models_browse_btn"] = _append_compact_button(row, "选择目录")
    ollama_config_layout.addWidget(row)

    add_setting_group_description(
        ollama_config_layout,
        "点击右侧“应用运行设置”后由 Engine 保存并热加载。外部服务的实际模型目录不由客户端推断或展示。",
    )

    ollama_install_hint = QLabel(
        "\n**如何安装 Ollama：**\n\n1. 访问 [ollama.com](https://ollama.com) 下载安装程序"
        "\n2. 安装后运行 `ollama pull nomic-embed-text` 拉取嵌入模型"
        "\n3. 运行 `ollama pull llama3.2` 拉取文本生成模型"
        "\n4. Ollama 会自动在后台运行，无需额外配置"
    )
    ollama_install_hint.setObjectName("panelDescription")
    ollama_install_hint.setWordWrap(True)
    ollama_install_hint.setOpenExternalLinks(True)
    ollama_install_hint.setTextFormat(Qt.TextFormat.MarkdownText)
    ollama_config_layout.addWidget(ollama_install_hint)
    ollama_config_layout.addStretch()

    ollama_columns.addWidget(ollama_config, 0, 0)
    ollama_columns.addWidget(build_ollama_model_panel(), 0, 1)
    sec.body_layout.addLayout(ollama_columns)

    return sec, widgets
