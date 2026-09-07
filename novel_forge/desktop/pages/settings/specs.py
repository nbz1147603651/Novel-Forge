"""Declarative settings specs for the settings page.

The settings page still uses the existing SettingRow/CollapsibleSection visual
language.  Specs only centralize repetitive label/widget/env wiring so new
ordinary settings do not keep expanding the save collector by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from PySide6.QtWidgets import QVBoxLayout

from novel_forge.desktop.widgets import make_combo_setting, make_float_setting

SettingKind = Literal["combo", "float"]


@dataclass(frozen=True)
class SettingSpec:
    """A single ordinary settings control."""

    key: str
    env_name: str
    attr: str
    label: str
    hint: str
    kind: SettingKind
    tooltip: str = ""
    choices: tuple[str, ...] = ()
    choice_labels: tuple[str, ...] = ()
    min_value: float = 0.0
    max_value: float = 2.0
    decimals: int = 2
    step: float = 0.05


@dataclass(frozen=True)
class SettingGroupSpec:
    """A small group of related settings."""

    title: str
    description: str
    specs: tuple[SettingSpec, ...]


CREATIVE_TEMPERATURE_SCOPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("recommended", "推荐创意任务"),
    ("chapter_core", "章节核心"),
    ("init_and_chapter", "初始化 + 章节"),
    ("custom", "自定义"),
)


CREATIVE_TEMPERATURE_SETTING_GROUP = SettingGroupSpec(
    title="创意火候浮动",
    description="开启后仅创意类任务围绕基础火候轻微随机；保护任务保持固定温度。",
    specs=(
        SettingSpec(
            key="_creative_temp_jitter_enabled",
            env_name="NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED",
            attr="creative_temperature_jitter_enabled",
            label="启用浮动",
            hint="默认关闭；开启后基础 1.0 时约为 0.7–1.1",
            kind="combo",
            choices=("false", "true"),
            tooltip="只影响创意类任务。格式重试、检查、抽取、裁判、修复和审计任务不会被随机化。",
        ),
        SettingSpec(
            key="_creative_temp_jitter_scope",
            env_name="NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_SCOPE",
            attr="creative_temperature_jitter_scope",
            label="适用范围",
            hint="推荐模式最稳；自定义可逐项勾选",
            kind="combo",
            choices=tuple(value for value, _label in CREATIVE_TEMPERATURE_SCOPE_CHOICES),
            choice_labels=tuple(label for _value, label in CREATIVE_TEMPERATURE_SCOPE_CHOICES),
            tooltip="选择哪些创意任务允许围绕基础火候浮动。保护任务即使自定义勾选也会被过滤。",
        ),
        SettingSpec(
            key="_creative_temp_jitter_down",
            env_name="NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_DOWN_DELTA",
            attr="creative_temperature_jitter_down_delta",
            label="下浮幅度",
            hint="实际温度最多低于基础火候多少；推荐 0.30",
            kind="float",
            min_value=0.0,
            max_value=2.0,
            decimals=1,
            step=0.1,
        ),
        SettingSpec(
            key="_creative_temp_jitter_up",
            env_name="NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_UP_DELTA",
            attr="creative_temperature_jitter_up_delta",
            label="上浮幅度",
            hint="实际温度最多高于基础火候多少；推荐 0.10",
            kind="float",
            min_value=0.0,
            max_value=2.0,
            decimals=1,
            step=0.1,
        ),
    ),
)


def render_setting_group(
    layout: QVBoxLayout,
    settings: Any,
    group: SettingGroupSpec,
) -> dict[str, Any]:
    """Render a spec group into *layout* and return its widgets by key."""

    widgets: dict[str, Any] = {}
    for spec in group.specs:
        if spec.kind == "combo":
            current = getattr(settings, spec.attr, "")
            if isinstance(current, bool):
                current = str(current).lower()
            row, widget = make_combo_setting(
                spec.label,
                spec.hint,
                list(spec.choices),
                current=str(current),
                item_labels=list(spec.choice_labels) if spec.choice_labels else None,
            )
        elif spec.kind == "float":
            row, widget = make_float_setting(
                spec.label,
                spec.hint,
                float(getattr(settings, spec.attr, 0.0)),
                spec.min_value,
                spec.max_value,
                decimals=spec.decimals,
                step=spec.step,
            )
        else:
            continue

        if spec.tooltip:
            row.setToolTip(spec.tooltip)
            widget.setToolTip(spec.tooltip)
        widgets[spec.key] = widget
        layout.addWidget(row)
    return widgets


def collect_setting_spec_env_pairs(
    widgets: dict[str, Any],
    group: SettingGroupSpec,
) -> dict[str, str]:
    """Collect env pairs for a rendered spec group."""

    pairs: dict[str, str] = {}
    for spec in group.specs:
        widget = widgets.get(spec.key)
        if widget is None:
            continue
        if spec.kind == "combo":
            data = widget.currentData()
            value = str(data if data is not None else widget.currentText())
        elif spec.kind == "float":
            value = str(widget.value())
        else:
            continue
        pairs[spec.env_name] = value
    return pairs
