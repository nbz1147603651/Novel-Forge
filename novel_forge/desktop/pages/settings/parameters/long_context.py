"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains long_context.py builders.
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


def _build_long_context_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 6: Long Novel — Context Feeding."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("长篇状态 — 上下文、投喂与归档", expanded=False)

    layout = _make_settings_subsection(
        sec,
        "状态档案",
        "控制角色资料和关系字段进入提示词的规模。",
    ).body_layout
    row, widgets["_long_max_profiles"] = make_spin_setting(
        "最多投喂角色数",
        "推荐 8-16",
        s.long_prompt_max_character_profiles,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_long_max_profile_chars"] = make_spin_setting(
        "保留字段字符数",
        "推荐 160-800",
        s.long_prompt_max_profile_field_chars,
        50,
        2000,
    )
    layout.addWidget(row)
    row, widgets["_long_max_relations"] = make_spin_setting(
        "每角色最大关系数",
        "推荐 4-10",
        s.long_prompt_max_relationships_per_profile,
        1,
        30,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "Canon 注入",
        "控制近期事件、角色、伏笔和世界事实的注入上限。",
    ).body_layout
    row, widgets["_canon_events"] = make_spin_setting(
        "近期事件",
        "推荐 20-60",
        s.canon_context_max_recent_events,
        1,
        200,
    )
    layout.addWidget(row)
    row, widgets["_canon_chars"] = make_spin_setting(
        "角色条目",
        "推荐 15-40",
        s.canon_context_max_characters,
        1,
        100,
    )
    layout.addWidget(row)
    row, widgets["_canon_foreshadow"] = make_spin_setting(
        "伏笔条目",
        "推荐 10-40",
        s.canon_context_max_foreshadowing,
        1,
        100,
    )
    layout.addWidget(row)
    row, widgets["_plan_foreshadow"] = make_spin_setting(
        "规划步骤伏笔上限",
        "只注入最近 N 条，节省 token（推荐 5‐15）",
        s.long_plan_max_foreshadowing,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_canon_facts"] = make_spin_setting(
        "世界事实",
        "推荐 40-120",
        s.canon_context_max_world_facts,
        1,
        500,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "上下文压缩",
        "控制章节推进后的 Canon 压缩节奏和保留策略。",
    ).body_layout
    row, widgets["_long_compact_interval"] = make_spin_setting(
        "压缩间隔",
        "每隔 N 章触发一次 Canon 压缩。推荐 5",
        s.long_chapter_compact_interval,
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_start"] = make_spin_setting(
        "压缩起始章节",
        "从第 N 章开始启用压缩。推荐 10",
        s.long_chapter_compact_start_chapter,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_stale"] = make_spin_setting(
        "陈旧判定章数",
        "超过 N 章未活跃的角色/物品标记为陈旧。推荐 8",
        s.long_chapter_compact_stale_chapters,
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_lookahead"] = make_spin_setting(
        "大纲前瞻章数",
        "压缩时向前看 N 章大纲，保护未来出场角色。推荐 12",
        s.long_chapter_compact_outline_lookahead,
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_min_chars"] = make_spin_setting(
        "最少保留角色数",
        "压缩后至少保留的活跃角色数。推荐 8",
        s.long_chapter_compact_min_active_characters,
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_target_facts"] = make_spin_setting(
        "世界事实目标数",
        "压缩后世界事实的目标数量。推荐 120",
        s.long_chapter_compact_target_world_facts,
        10,
        500,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_keep_recent_facts"] = make_spin_setting(
        "保留近期世界事实",
        "强制保留最近 N 章内的世界事实。推荐 40",
        s.long_chapter_compact_keep_recent_world_facts,
        5,
        100,
    )
    layout.addWidget(row)
    row, widgets["_long_compact_archive_foreshadowing"] = make_spin_setting(
        "伏笔归档延迟",
        "伏笔回收后 N 章归档。推荐 6",
        s.long_chapter_compact_archive_resolved_foreshadowing_after,
        1,
        20,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "实体引用与桥接检索",
        "本章实体固定链接由 source slice 完整投影；桥接因果会完整进入检索 query，这里只控制动态证据预算。",
    ).body_layout
    row, widgets["_narrative_evidence_candidates"] = make_spin_setting(
        "动态证据候选池",
        "Zvec 先按章节截止时间过滤，再从候选池中按检索排名装入证据预算；推荐 16-48",
        int(getattr(s, "long_narrative_evidence_candidate_limit", 24)),
        1,
        128,
    )
    layout.addWidget(row)
    row, widgets["_narrative_evidence_token_budget"] = make_spin_setting(
        "动态证据预算",
        "规划/复审的历史证据 token 预算；不影响完整 P0 固定事实，推荐 1600-4000",
        int(getattr(s, "long_narrative_evidence_token_budget", 2400)),
        256,
        16000,
        step=256,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "边界与事实预算",
        "控制章节边界窗口；本章 P0 固定事实完整传递，不再按条数静默截断。",
    ).body_layout
    row, widgets["_boundary_prev_tail_paragraphs"] = make_spin_setting(
        "上章末尾段数",
        "Bridge / Edit-Polish 复审 / Repair 共用的上一章末尾段落数（推荐 3-5）",
        int(getattr(s, "long_boundary_prev_tail_paragraphs", 5)),
        1,
        12,
    )
    layout.addWidget(row)
    row, widgets["_boundary_opening_paragraphs"] = make_spin_setting(
        "本章开头段数",
        "开场衔接检查与修复的目标窗口段落数（推荐 1-3）",
        int(getattr(s, "long_boundary_opening_paragraphs", 3)),
        1,
        8,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "Prompt 诊断",
        "记录章节生成链路的估算 token 和最大上下文字段，便于调注意力预算。",
    ).body_layout
    row, widgets["_draft_prompt_diag_enabled"] = make_combo_setting(
        "Draft Prompt 诊断",
        "记录估算 token 和最大上下文字段，便于调注意力预算",
        ["true", "false"],
        current=str(getattr(s, "long_draft_prompt_diagnostics_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_draft_prompt_warn_tokens"] = make_spin_setting(
        "Draft 警告阈值",
        "估算 prompt token 达到该值写 warning；0=只记录 info",
        int(getattr(s, "long_draft_prompt_warn_tokens", 32000)),
        0,
        200000,
        step=1000,
    )
    layout.addWidget(row)
    row, widgets["_prompt_diag_enabled"] = make_combo_setting(
        "生成链路 Prompt 诊断",
        "记录 Bridge/Plan/Edit 等章节生成阶段的估算 token 和最大上下文字段",
        ["true", "false"],
        current=str(getattr(s, "long_prompt_diagnostics_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_prompt_warn_tokens"] = make_spin_setting(
        "生成链路警告阈值",
        "非 Draft 章节生成 prompt 估算 token 达到该值写 warning；0=只记录 info",
        int(getattr(s, "long_prompt_warn_tokens", 32000)),
        0,
        200000,
        step=1000,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "叙事状态",
        "控制 LLM 裁定状态账本与下一章注入规模。",
    ).body_layout
    row, widgets["_narrative_state_enabled"] = make_combo_setting(
        "启用裁判状态",
        "启用 LLM 裁判驱动的 narrative ledger/projection（推荐开启）",
        ["true", "false"],
        current=str(getattr(s, "narrative_state_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_required"] = make_combo_setting(
        "失败阻断流程",
        "初始化或章节裁判失败时阻断，而不是降级跳过（推荐开启）",
        ["true", "false"],
        current=str(getattr(s, "narrative_state_required", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_repair_rounds"] = make_spin_setting(
        "裁判修复轮数",
        "LLM 最终裁判要求修复时最多重试几轮；0=只裁判不自动修",
        int(getattr(s, "narrative_state_max_repair_rounds", 1)),
        0,
        5,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "裁判输入预算",
        "全书连跑平衡建议：候选 8，账本 12，待定 6，事实路径 30，证据 2，最终合并 18000 字符。",
    ).body_layout
    row, widgets["_narrative_state_candidate_max"] = make_spin_setting(
        "候选变化上限",
        "每章常规进入单候选裁判的最大数。必达契约证据会额外保留；建议 6-10。",
        int(getattr(s, "narrative_state_candidate_max_count", 8)),
        1,
        200,
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_candidate_evidence_limit"] = make_spin_setting(
        "每候选证据数",
        "每个状态候选保留的正文证据条数。建议 2；超过 3 通常只会增加输入重复。",
        int(getattr(s, "narrative_state_candidate_evidence_limit", 2)),
        1,
        4,
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_final_context_max_chars"] = make_spin_setting(
        "最终裁决上下文预算(字符)",
        "仅影响有残留模糊/修复问题时的最终 LLM 合并。超出会先压缩说明；若仍超出，保留逐项裁判结果并跳过重复合并，不丢 ID/状态。建议 18000。",
        int(getattr(s, "narrative_state_final_context_max_chars", 18000)),
        8000,
        64000,
        step=1000,
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_final_target_max_chars"] = make_spin_setting(
        "最终契约目标字数",
        "最终合并中每条契约目标的说明长度。建议 96；ID、状态路径和类型不会被截断。",
        int(getattr(s, "narrative_state_final_target_max_chars", 96)),
        40,
        240,
    )
    layout.addWidget(row)
    row, widgets["_narrative_state_pending_tail"] = make_spin_setting(
        "待定项注入条数",
        "注入最近多少条 ambiguous/defer 待定事项，提醒模型不要提前定论",
        int(getattr(s, "narrative_state_pending_tail_items", 6)),
        0,
        100,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "剧情控制",
        "控制未来泄露、契约执行和表达通道治理。",
    ).body_layout
    row, widgets["_plot_progression_strictness"] = make_combo_setting(
        "剧情推进强度",
        "warn=只报告；block=阻断未来泄露/禁用推进；strict=同时阻断必达缺失",
        ["block", "warn", "strict"],
        current=str(getattr(s, "plot_progression_strictness", "block")),
        item_labels=["阻断", "只警告", "严格"],
    )
    layout.addWidget(row)
    row, widgets["_future_leak_guard_enabled"] = make_combo_setting(
        "未来泄露护栏",
        "检查当前章是否提前兑现未来里程碑或高光触发方式",
        ["true", "false"],
        current=str(getattr(s, "long_future_leak_guard_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_contract_audit_enabled"] = make_combo_setting(
        "契约执行审计",
        "归档前核对章节契约、里程碑窗口和推进账本",
        ["true", "false"],
        current=str(getattr(s, "long_contract_audit_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_contract_audit_strictness"] = make_combo_setting(
        "契约审计强度",
        "warn=只报告；block=阻断未来泄露/禁用推进；strict=同时阻断必达缺失",
        ["block", "warn", "strict"],
        current=str(getattr(s, "long_contract_audit_strictness", "block")),
        item_labels=["阻断", "只警告", "严格"],
    )
    layout.addWidget(row)
    row, widgets["_expression_channel_detection"] = make_combo_setting(
        "表达通道检测",
        "把近义生理反应、动作标签、感官锚点等合并为语义通道治理",
        ["true", "false"],
        current=str(getattr(s, "expression_channel_detection_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_expression_cooldown_window"] = make_spin_setting(
        "表达冷却章数",
        "通道进入冷却后多少章内提醒 Draft 改换表达机制",
        int(getattr(s, "expression_channel_cooldown_chapters", 3)),
        0,
        30,
    )
    layout.addWidget(row)
    row, widgets["_arc_liveness_window"] = make_spin_setting(
        "弧光沉睡窗口",
        "副线/角色弧光超过多少章未推进时给 Plan 轻触提示",
        int(getattr(s, "arc_liveness_window", 6)),
        1,
        60,
    )
    layout.addWidget(row)
    row, widgets["_stage_visibility_debug"] = make_combo_setting(
        "阶段可见性诊断",
        "记录 Bridge/Plan/Draft/Judge 实际收到的里程碑摘要",
        ["true", "false"],
        current=str(getattr(s, "stage_visibility_debug_enabled", True)).lower(),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "Canon 抽取输入",
        "控制 Canon 提取时注入的前态线索、关系与角色快照。",
    ).body_layout
    row, widgets["_extract_existing_thread_ids"] = make_spin_setting(
        "已有线索 ID 上限",
        "推荐 20-40；0=不注入已有 thread_id 列表",
        s.extract_canon_max_existing_thread_ids,
        0,
        200,
    )
    layout.addWidget(row)
    row, widgets["_extract_prior_relationships"] = make_spin_setting(
        "前态关系上限",
        "推荐 8-16；0=不注入人物关系基线",
        s.extract_canon_max_prior_relationships,
        0,
        100,
    )
    layout.addWidget(row)
    row, widgets["_extract_recent_character_window"] = make_spin_setting(
        "前态角色回看章数",
        "推荐 3-8；0=不过滤最近章节窗口",
        s.extract_canon_recent_character_window_chapters,
        0,
        50,
    )
    layout.addWidget(row)
    row, widgets["_extract_prior_characters"] = make_spin_setting(
        "前态角色快照上限",
        "推荐 8-16；0=不注入角色快照",
        s.extract_canon_max_prior_characters,
        0,
        100,
    )
    layout.addWidget(row)
    row, widgets["_extract_prior_plot_threads"] = make_spin_setting(
        "前态线索上限",
        "推荐 6-12；0=不注入活跃线索基线",
        s.extract_canon_max_prior_plot_threads,
        0,
        100,
    )
    layout.addWidget(row)
    row, widgets["_extract_prior_plot_thread_summary_chars"] = make_spin_setting(
        "线索摘要字符上限",
        "推荐 40-80；0=只传标题和状态",
        s.extract_canon_prior_plot_thread_summary_chars,
        0,
        500,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "Canon 抽取输出",
        "控制 Canon 提取输出预算和 delta 数量上限。",
    ).body_layout
    row, widgets["_extract_output_base_tokens"] = make_spin_setting(
        "输出基础 token",
        "基础输出预算；推荐 3072-4096",
        s.extract_canon_output_base_tokens,
        256,
        32768,
    )
    layout.addWidget(row)
    row, widgets["_extract_output_per_character_tokens"] = make_spin_setting(
        "每角色追加 token",
        "每个已知角色额外分配的输出预算；推荐 300-600",
        s.extract_canon_output_tokens_per_character,
        0,
        4000,
    )
    layout.addWidget(row)
    row, widgets["_extract_output_per_2500_chars"] = make_spin_setting(
        "每2500字追加 token",
        "正文越长，按块追加输出预算；推荐 512-1024",
        s.extract_canon_output_tokens_per_2500_chars,
        0,
        4096,
    )
    layout.addWidget(row)
    row, widgets["_extract_output_max_tokens"] = make_spin_setting(
        "输出 token 上限",
        "Canon 提取单次调用的 max_tokens；推荐 8192-16384",
        s.extract_canon_output_max_tokens,
        1024,
        65536,
    )
    layout.addWidget(row)
    row, widgets["_extract_abort_on_severe_damage"] = make_combo_setting(
        "重度损坏即停",
        "解析后若检测到重度结构损坏，则终止流程而不是继续污染 canon",
        ["true", "false"],
        current=str(s.extract_canon_abort_on_severe_damage).lower(),
    )
    layout.addWidget(row)
    row, widgets["_extract_severe_damage_threshold"] = make_spin_setting(
        "缺段停机阈值",
        "原始响应里出现但解析后丢失的顶层区块达到 N 个时停止；推荐 2",
        s.extract_canon_severe_damage_missing_section_threshold,
        1,
        4,
    )
    layout.addWidget(row)
    row, widgets["_extract_max_character_state_deltas"] = make_spin_setting(
        "角色状态 delta 上限",
        "只保留变化最明确的角色状态项；推荐 4-10",
        s.extract_canon_max_character_state_deltas,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_extract_max_relationship_deltas"] = make_spin_setting(
        "关系 delta 上限",
        "只保留变化最大的关系项；推荐 4-10",
        s.extract_canon_max_relationship_deltas,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_extract_max_plot_thread_deltas"] = make_spin_setting(
        "线索 delta 上限",
        "只保留本章真正推进的线索；推荐 4-10",
        s.extract_canon_max_plot_thread_deltas,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_extract_max_exit_state_characters"] = make_spin_setting(
        "章末角色状态上限",
        "chapter_exit_state 中最多保留的关键角色数；推荐 4-8",
        s.extract_canon_max_exit_state_characters,
        1,
        30,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "语义压缩",
        "控制 plan_chapter 前的语义压缩触发和输出上限。",
    ).body_layout
    row, widgets["_compress_enabled"] = make_combo_setting(
        "启用压缩",
        "plan_chapter 前语义压缩",
        ["true", "false"],
        current=str(s.long_context_compress_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_compress_min"] = make_spin_setting(
        "最小触发字符数",
        "推荐 200-500",
        s.long_context_compress_min_chars,
        50,
        5000,
    )
    layout.addWidget(row)
    row, widgets["_compress_max_tokens"] = make_spin_setting(
        "压缩后上限",
        "推荐 1024-4096",
        s.long_context_compress_max_tokens,
        256,
        16384,
    )
    layout.addWidget(row)

    return sec, widgets
