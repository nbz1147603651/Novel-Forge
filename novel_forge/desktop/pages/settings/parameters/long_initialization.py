"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains long_initialization.py builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
)

from novel_forge.desktop.pages.settings.parameters.init_coherence import (
    _add_init_coherence_controls,
)
from novel_forge.desktop.pages.settings.parameters.init_protocol import (
    _add_init_protocol_controls,
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


def _add_world_rule_governance_controls(
    sec: CollapsibleSection,
    widgets: dict[str, Any],
    s: Any,
) -> None:
    """World rule book governance thresholds.

    These parameters control the validation and coercion of the structured
    ``world_rule_book`` generated during INIT_STORY_WORLD_RULES. They flow
    into ``validate_world_rule_book`` and ``coerce_world_rule_book`` via
    the project Settings.
    """
    sub = _make_settings_subsection(
        sec,
        "世界规则治理",
        "控制初始化生成的 world_rule_book 规则数量、硬规则下限、始终生效上限、"
        "类别覆盖与阻断策略。超出阈值的规则会被自动降级而非丢弃；"
        "默认仅警告不阻断初始化。",
    )
    layout = sub.body_layout

    row, widgets["_world_rule_count_min"] = make_spin_setting(
        "规则数下限",
        "world_rule_book 最少规则数；推荐 10",
        int(getattr(s, "world_rule_count_min", 10)),
        4,
        20,
    )
    layout.addWidget(row)
    row, widgets["_world_rule_count_max"] = make_spin_setting(
        "规则数上限",
        "world_rule_book 最多规则数；推荐 14",
        int(getattr(s, "world_rule_count_max", 14)),
        6,
        24,
    )
    layout.addWidget(row)
    row, widgets["_world_rule_hard_min"] = make_spin_setting(
        "硬规则下限",
        "至少需要 N 条 hard 规则；推荐 3",
        int(getattr(s, "world_rule_hard_min", 3)),
        1,
        10,
    )
    layout.addWidget(row)
    row, widgets["_world_rule_always_on_cap"] = make_spin_setting(
        "始终生效硬规则上限",
        "always_on=true 的硬规则上限；超出自动降级为条件规则。推荐 4，避免分散章节模型注意力",
        int(getattr(s, "world_rule_always_on_hard_cap", 4)),
        1,
        10,
    )
    layout.addWidget(row)
    row, widgets["_world_rule_category_min"] = make_spin_setting(
        "类别覆盖下限",
        "至少覆盖 N 个规则类别（time_space/social_language/resource_material/information/ability_tech/general）；推荐 4",
        int(getattr(s, "world_rule_category_min", 4)),
        1,
        6,
    )
    layout.addWidget(row)
    row, widgets["_world_rule_ability_required"] = make_combo_setting(
        "强制 ability_tech 类别",
        "世界包含魔法/异能/科技体系时，规则账本必须覆盖 ability_tech 类别",
        ["true", "false"],
        current=str(getattr(s, "world_rule_ability_required", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_world_rule_block_on_violation"] = make_combo_setting(
        "治理违反阻断初始化",
        "默认 false（仅警告）；true 时规则数/类别等违反会中止初始化",
        ["true", "false"],
        current=str(getattr(s, "world_rule_block_on_violation", False)).lower(),
    )
    layout.addWidget(row)


def _build_long_initialization_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Long-project initialization parameters."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("长篇生成 — 初始化与蓝图", expanded=False)

    layout = _make_settings_subsection(
        sec,
        "大纲批次与密度",
        "章节大纲与章节契约批次默认由初始化链路按当前模型输出能力自动计算："
        "章节大纲会按 JSON 输出稳定性保守分批，长项目通常不超过 3 章；"
        "下方填 0 保持自动；同时控制章纲结构密度。",
    ).body_layout
    row, widgets["_outline_batch_size"] = make_spin_setting(
        "大纲批次章数",
        "章节大纲首批/续写每批生成的章数上限；0=自动，长项目会自动压低以减少 JSON 失败",
        int(getattr(s, "outline_batch_size", 0)),
        0,
        50,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_beats_min"] = make_spin_setting(
        "章纲节拍下限",
        "每章 beats_summary 最少条数，推荐 4",
        int(getattr(s, "init_outline_beats_min", 4)),
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_beats_max"] = make_spin_setting(
        "章纲节拍上限",
        "每章 beats_summary 最多条数，推荐 8；过高会变成分场大纲",
        int(getattr(s, "init_outline_beats_max", 8)),
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_main_points_min"] = make_spin_setting(
        "主线点下限",
        "每章 main_plot_points 最少条数，推荐 2",
        int(getattr(s, "init_outline_main_plot_points_min", 2)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_main_points_max"] = make_spin_setting(
        "主线点上限",
        "每章 main_plot_points 最多条数，推荐 4",
        int(getattr(s, "init_outline_main_plot_points_max", 4)),
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_subplot_points_max"] = make_spin_setting(
        "支线点上限",
        "每章 subplot_points 最多条数，推荐 3；0=章纲不主动规划支线点",
        int(getattr(s, "init_outline_subplot_points_max", 3)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_element_focus_max"] = make_spin_setting(
        "要素聚焦上限",
        "每章 element_focus 最多数量，schema 上限为 3",
        int(getattr(s, "init_outline_element_focus_max", 3)),
        0,
        3,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_payoffs_min"] = make_spin_setting(
        "微兑现下限",
        "每章 expected_payoffs 最少条数，推荐 1",
        int(getattr(s, "init_outline_expected_payoffs_min", 1)),
        0,
        8,
    )
    layout.addWidget(row)
    row, widgets["_init_outline_payoffs_max"] = make_spin_setting(
        "微兑现上限",
        "每章 expected_payoffs 最多条数，推荐 3",
        int(getattr(s, "init_outline_expected_payoffs_max", 3)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_batch_size"] = make_spin_setting(
        "契约批次章数",
        "章节契约每批生成的章数；0=自动，调小更稳，调大调用更少但更吃输出预算",
        int(getattr(s, "chapter_contract_batch_size", 0)),
        0,
        50,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "初始化执行并发",
        "fresh init 固定走 StoryKernel v3 DAG；初始化一致性 gate 与 readiness 硬门不会被这些参数关闭。",
    ).body_layout
    row, widgets["_split_tasks_enabled"] = make_combo_setting(
        "巨型任务分片",
        "开启后章节抽取、全书审计等大 JSON 会拆分执行并本地合并",
        ["true", "false"],
        current=str(getattr(s, "split_tasks_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_fragment_max_parallel"] = make_spin_setting(
        "初始化分片并发",
        "Story/Character/Editorial 等分片任务的最大并发数；2-4 稳定，5-8 更快但更吃限流",
        int(getattr(s, "init_fragment_max_parallel", 4)),
        1,
        8,
    )
    layout.addWidget(row)
    row, widgets["_init_character_profile_parallel_min_roster"] = make_spin_setting(
        "角色档案并行阈值",
        "角色清单达到该人数后才把档案分批并行；低于阈值保持单批生成，提升小角色表自洽",
        int(getattr(s, "init_character_profile_parallel_min_roster", 11)),
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_init_character_profile_batch_size"] = make_spin_setting(
        "角色档案批次大小",
        "并行生成时每批补全的角色数；建议 4-6，越小越稳但调用更多",
        int(getattr(s, "init_character_profile_batch_size", 5)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_init_kb_phase_c_parallel"] = make_combo_setting(
        "知识边界并行",
        "知识边界生成与 Phase C（风格/实体图谱）并行；关闭则恢复 KB 先行串行结构",
        ["true", "false"],
        current=str(getattr(s, "init_kb_phase_c_parallel", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_entity_reference_max_parallel"] = make_spin_setting(
        "实体指称并发",
        "实体指称裁决的最大并发批次数；2-3 最稳，过高易限流",
        int(getattr(s, "init_entity_reference_max_parallel", 3)),
        1,
        8,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "初始化温度",
        "初始化各分片/批次的 LLM 温度；默认值经质量验证，仅在需要调节生成稳定性时修改。",
    ).body_layout
    row, widgets["_temp_init_story_bible"] = make_float_setting(
        "世界观温度",
        "StoryBible 分片生成温度；高更发散，低更稳定",
        float(getattr(s, "temp_init_story_bible", 0.7)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_temp_init_character_bible"] = make_float_setting(
        "角色圣经温度",
        "角色清单/档案/关系生成温度",
        float(getattr(s, "temp_init_character_bible", 0.7)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_temp_plan_outline_batch"] = make_float_setting(
        "大纲批次温度",
        "章纲分批生成温度",
        float(getattr(s, "temp_plan_outline_batch", 0.7)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_temp_plan_chapter_contracts"] = make_float_setting(
        "章节契约温度",
        "契约分批生成温度；低更稳",
        float(getattr(s, "temp_plan_chapter_contracts", 0.25)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_temp_init_entity_registry"] = make_float_setting(
        "实体注册表温度",
        "实体补充裁决温度",
        float(getattr(s, "temp_init_entity_registry", 0.2)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_temp_init_knowledge_boundaries"] = make_float_setting(
        "知识边界温度",
        "角色知识边界生成温度",
        float(getattr(s, "temp_init_knowledge_boundaries", 0.3)),
        0.0,
        2.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "大纲能力开关",
        "路由行的「思考/多轮」开关优先；未单独指定路由时，下面的默认开关和 allowlist "
        "决定蓝图/章节大纲是否启用 thinking 与多轮上下文。",
    ).body_layout
    row, widgets["_outline_thinking"] = make_combo_setting(
        "大纲思考默认",
        "未在路由中显式覆盖时，蓝图与章节大纲是否允许 thinking",
        ["true", "false"],
        current=str(getattr(s, "outline_thinking", False)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_outline_thinking_providers"] = make_line_setting(
        "思考 Provider allowlist",
        "逗号分隔；仅未设置路由覆盖时生效",
        str(getattr(s, "outline_thinking_providers", "tongyi,deepseek")),
    )
    layout.addWidget(row)
    row, widgets["_outline_thinking_models"] = make_line_setting(
        "思考模型 allowlist",
        "逗号分隔，空=不按模型限制；仅未设置路由覆盖时生效",
        str(getattr(s, "outline_thinking_models", "")),
    )
    layout.addWidget(row)
    row, widgets["_outline_multi_turn"] = make_combo_setting(
        "大纲续写多轮默认",
        "未在路由中显式覆盖时，PLAN_OUTLINE_CONTINUE 是否带入前批对话历史",
        ["true", "false"],
        current=str(getattr(s, "outline_multi_turn", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_outline_multi_turn_providers"] = make_line_setting(
        "大纲多轮 Provider allowlist",
        "逗号分隔；仅未设置路由覆盖时生效",
        str(getattr(s, "outline_multi_turn_providers", "tongyi,deepseek")),
    )
    layout.addWidget(row)
    row, widgets["_outline_multi_turn_models"] = make_line_setting(
        "大纲多轮模型 allowlist",
        "逗号分隔，空=不按模型限制；仅未设置路由覆盖时生效",
        str(getattr(s, "outline_multi_turn_models", "")),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "章节契约预算",
        "控制章节契约中的硬推进、软铺垫和出口状态预算。",
    ).body_layout
    row, widgets["_chapter_contract_context_window"] = make_spin_setting(
        "契约上下文窗口",
        "章节契约分批生成时注入前后多少章大纲作为上下文",
        int(getattr(s, "chapter_contract_context_window", 2)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_dynamic_budget"] = make_combo_setting(
        "契约动态预算",
        "按每章目标字数限制 required/allowed/出口状态条数，生成阶段减少模型注意力负担",
        ["true", "false"],
        current=str(getattr(s, "chapter_contract_dynamic_budget_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_hard_words_per_item"] = make_spin_setting(
        "硬约束字数/条",
        "目标字数每达到多少字允许 1 条硬性推进；值越大，硬约束越少",
        int(getattr(s, "chapter_contract_hard_words_per_item", 1000)),
        200,
        5000,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_hard_min_items"] = make_spin_setting(
        "硬约束下限",
        "每章 required_progressions 最少保留条数",
        int(getattr(s, "chapter_contract_hard_min_items", 3)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_hard_max_items"] = make_spin_setting(
        "硬约束上限",
        "每章 required_progressions 最多保留条数；建议 5-6",
        int(getattr(s, "chapter_contract_hard_max_items", 6)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_soft_words_per_item"] = make_spin_setting(
        "软铺垫字数/条",
        "目标字数每达到多少字允许 1 条 allowed_* 铺垫",
        int(getattr(s, "chapter_contract_soft_words_per_item", 900)),
        200,
        5000,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_soft_max_items"] = make_spin_setting(
        "软铺垫上限",
        "每章 allowed_changes/allowed_progressions 最多保留条数",
        int(getattr(s, "chapter_contract_soft_max_items", 5)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_state_words_per_item"] = make_spin_setting(
        "出口状态字数/条",
        "目标字数每达到多少字允许 1 条 exit_state/completion 标准",
        int(getattr(s, "chapter_contract_state_words_per_item", 1200)),
        200,
        5000,
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_state_max_items"] = make_spin_setting(
        "出口状态上限",
        "每章 exit_state_targets/completion_criteria 最多保留条数",
        int(getattr(s, "chapter_contract_state_max_items", 4)),
        1,
        12,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "章节契约生成与裁判",
        "控制章节契约多轮续写、批量一致性裁判和并发。",
    ).body_layout
    row, widgets["_chapter_contract_multi_turn"] = make_combo_setting(
        "章节契约多轮默认",
        "未在路由中显式覆盖时，PLAN_CHAPTER_CONTRACTS 是否带入前批对话历史",
        ["true", "false"],
        current=str(getattr(s, "chapter_contract_multi_turn", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_multi_turn_providers"] = make_line_setting(
        "契约多轮 Provider allowlist",
        "逗号分隔；仅未设置路由覆盖时生效",
        str(getattr(s, "chapter_contract_multi_turn_providers", "tongyi,deepseek,minimax")),
    )
    layout.addWidget(row)
    row, widgets["_chapter_contract_multi_turn_models"] = make_line_setting(
        "契约多轮模型 allowlist",
        "逗号分隔，空=不按模型限制；仅未设置路由覆盖时生效",
        str(getattr(s, "chapter_contract_multi_turn_models", "")),
    )
    layout.addWidget(row)
    row, widgets["_contract_coherence_batch_size"] = make_spin_setting(
        "契约裁判批大小",
        "每批纳入多少章契约做一致性裁判；越大调用更少，越小定位更细",
        int(getattr(s, "contract_coherence_batch_size", 12)),
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_contract_coherence_context_window"] = make_spin_setting(
        "契约裁判上下文窗口",
        "契约裁判时注入前后多少章契约作为邻近上下文",
        int(getattr(s, "contract_coherence_context_window", 2)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_contract_coherence_max_parallel"] = make_spin_setting(
        "契约裁判并发",
        "契约裁判分批检查的最大并发批次数；2 最稳，3-4 可加速",
        int(getattr(s, "contract_coherence_max_parallel", 2)),
        1,
        8,
    )
    layout.addWidget(row)
    _add_init_coherence_controls(sec, widgets, s)
    _add_world_rule_governance_controls(sec, widgets, s)

    layout = _make_settings_subsection(
        sec,
        "分卷与风格",
        "控制长项目自动分卷、默认卷规模和初始化写作风格规范。",
    ).body_layout
    row, widgets["_long_vol_auto_ch"] = make_spin_setting(
        "自动分卷章节阈值",
        "推荐 30-80。总章数超过此值时自动启用分卷",
        s.long_volume_auto_chapter_threshold,
        1,
        500,
    )
    layout.addWidget(row)
    row, widgets["_long_vol_auto_word"] = make_spin_setting(
        "自动分卷字数阈值",
        "推荐 100,000-400,000。总字数超过此值时自动启用分卷",
        s.long_volume_auto_word_threshold,
        10000,
        1000000,
        step=10000,
    )
    layout.addWidget(row)
    row, widgets["_long_default_cpv"] = make_spin_setting(
        "每卷默认章节数",
        "推荐 12-30。每卷包含的章节数量",
        s.long_default_chapters_per_volume,
        1,
        200,
    )
    layout.addWidget(row)

    row, widgets["_style_profile_enabled"] = make_combo_setting(
        "风格规范",
        "初始化时自动生成专属写作风格规范",
        ["true", "false"],
        current=str(s.style_profile_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_style_profile_required"] = make_combo_setting(
        "风格规范必需",
        "生成失败时阻塞流程而非静默降级",
        ["true", "false"],
        current=str(getattr(s, "style_profile_required", False)).lower(),
    )
    layout.addWidget(row)

    _add_init_protocol_controls(sec, widgets, s)

    return sec, widgets
