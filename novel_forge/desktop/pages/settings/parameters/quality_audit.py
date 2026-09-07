"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains quality_audit.py builders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
)

from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
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


def _build_quality_audit_params(
    s: Any,
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 7: Quality Audit (includes sub-sections 7.5)."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("质量、门控与自动修复", expanded=False)

    layout = _make_settings_subsection(
        sec,
        "质量阈值与裁判策略",
        "控制基础评分线、主线护栏和本章 AI 审查器的上下文与输出预算。",
    ).body_layout
    row, widgets["_long_align_threshold"] = make_float_setting(
        "对齐阈值",
        "低于此值视为需要修复（推荐 6.5-7.5）",
        s.long_alignment_threshold,
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_long_guard_mode"] = make_combo_setting(
        "主线护栏模式",
        "free / balanced / strict / ai_judge",
        ["free", "balanced", "strict", "ai_judge"],
        current=s.long_plot_guard_mode,
    )
    layout.addWidget(row)

    row, widgets["_judge_ctx_chapters"] = make_spin_setting(
        "最大上下文章节",
        "推荐 12-36",
        s.long_ai_judge_max_context_chapters,
        1,
        100,
    )
    layout.addWidget(row)
    row, widgets["_judge_max_tokens"] = make_spin_setting(
        "响应 token 上限",
        "推荐 1024-2048",
        s.long_ai_judge_max_tokens,
        256,
        16384,
    )
    layout.addWidget(row)
    row, widgets["_judge_entity"] = make_combo_setting(
        "回写实体动作",
        "是否把裁决回写到创作报告",
        ["true", "false"],
        current=str(s.long_ai_judge_apply_entity_actions).lower(),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "归档门控",
        "这些设置只负责最终归档裁决：低于硬线会阻断归档并触发重新规划或失败。"
        "追读力与 AI 护栏带有主观创作属性，默认较宽松；需要强监管时再收紧。",
    ).body_layout
    row, widgets["_min_accept_score"] = make_float_setting(
        "总评硬线",
        "最终综合评分低于此值会阻断归档（推荐 5.0）",
        float(getattr(s, "long_min_accept_score", 5.0)),
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_continuity_hard_block"] = make_float_setting(
        "连贯性硬线",
        "最终连贯性评分低于此值会阻断归档（推荐 4.0）",
        float(getattr(s, "long_continuity_hard_block_threshold", 4.0)),
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_causal_hard_block"] = make_float_setting(
        "因果硬线",
        "最终因果评分低于此值会阻断归档（推荐 4.0）",
        float(getattr(s, "long_causal_hard_block_threshold", 4.0)),
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_word_count_archive_gate_enabled"] = make_combo_setting(
        "归档前字数闸门",
        "开启时按目标字数重整或阻断；关闭后仅保留极短正文保护",
        ["true", "false"],
        current=str(getattr(s, "long_word_count_archive_gate_enabled", True)).lower(),
        item_labels=["开启（推荐）", "关闭"],
    )
    layout.addWidget(row)
    row, widgets["_wave_word_count_policy"] = make_combo_setting(
        "WAVE 字数后置检查",
        "仅控制 WAVE 后置条件中的字数偏差；继承=跟随归档前字数闸门。跨场景引用和场景锚点仍由 WAVE 后置策略处理。",
        ["inherit", "enforce", "warn"],
        current=str(getattr(s, "long_wave_word_count_policy", "inherit") or "inherit"),
        item_labels=["继承归档字数闸门（推荐）", "始终执行", "只警告"],
    )
    layout.addWidget(row)
    row, widgets["_word_count_archive_gate_max_rejections"] = make_spin_setting(
        "字数拒绝上限",
        "同一章连续因目标字数不达标被拒绝的最大次数。达到上限后降级为警告归档；"
        "0=不因目标字数阻断，但极短正文保护仍然生效（推荐 2）。",
        int(getattr(s, "long_word_count_archive_gate_max_rejections", 2)),
        0,
        10,
    )
    layout.addWidget(row)
    row, widgets["_rp_archive_policy"] = make_combo_setting(
        "追读力归档策略",
        "默认只在极低分时阻断；更严格模式会把核心高危问题也视为阻断项",
        ["off", "floor_only", "floor_or_core_high"],
        current=str(getattr(s, "long_reading_power_archive_policy", "floor_only")),
        item_labels=["仅报告与下章提示", "极低分阻断（推荐）", "极低分 + 核心高危阻断"],
    )
    layout.addWidget(row)
    row, widgets["_rp_hard_block_threshold"] = make_float_setting(
        "追读力硬线",
        "仅在追读力归档策略启用分数硬线时生效（推荐 3.0）",
        float(getattr(s, "long_reading_power_hard_block_threshold", 3.0)),
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_guard_archive_policy"] = make_combo_setting(
        "AI 护栏归档策略",
        "warn=只记录质量门；block_actionable=高置信可修复违约会阻断归档",
        ["warn", "block_actionable"],
        current=str(getattr(s, "long_guard_archive_policy", "warn")),
        item_labels=["仅告警记录（推荐）", "可行动违约阻断"],
    )
    layout.addWidget(row)
    row, widgets["_guard_archive_confidence"] = make_float_setting(
        "护栏阻断置信度",
        "AI 护栏策略为可行动违约阻断时生效（推荐 0.8）",
        float(getattr(s, "long_guard_archive_block_min_confidence", 0.8)),
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "长篇可靠性增强",
        "面向数百章以上作品的低频质量监控。默认关闭；开启后先记录问题与趋势，"
        "不默认改变生成文本或阻断归档。",
    ).body_layout
    row, widgets["_summary_drift_check_enabled"] = make_combo_setting(
        "卷末摘要漂移检查",
        "在每卷结束后比对卷摘要、章节摘要与 StoryKernel 关键事实；"
        "发现遗漏/泄密等高危问题会写入项目问题台账，供下一卷规划避坑。",
        ["false", "true"],
        current=str(getattr(s, "long_summary_drift_check_enabled", False)).lower(),
        item_labels=["关闭（默认，零额外检查）", "开启（卷末检查，低频）"],
    )
    layout.addWidget(row)
    row, widgets["_quality_trend_tracker_enabled"] = make_combo_setting(
        "长期质量趋势监控",
        "每章归档后记录总评、追读力、连贯性、伏笔老化和召回质量；"
        "用于发现慢性下滑，只发出告警和报告，不直接改文。",
        ["false", "true"],
        current=str(getattr(s, "long_quality_trend_tracker_enabled", False)).lower(),
        item_labels=["关闭（默认）", "开启（每章记录趋势）"],
    )
    layout.addWidget(row)
    add_setting_group_description(
        layout,
        "建议：中短篇或试写阶段保持关闭；长篇连跑、卷数较多或已有事实漂移时开启。\n"
        "摘要漂移检查是卷末低频检查，适合发现关键事实遗漏；质量趋势监控是每章轻量记录，"
        "适合观察创作质量是否连续走低。两者都不会自动增加 prompt 注入量，只有写入的问题台账"
        "会以小预算进入后续规划/起草上下文。",
    )

    layout = _make_settings_subsection(
        sec,
        "宏观护栏",
        "控制多章节轨迹审计、调纲冷却和偏离阈值。",
    ).body_layout
    row, widgets["_macro_guard_enabled"] = make_combo_setting(
        "宏观护栏",
        "启用多章节轨迹宏观审计（推荐开启）",
        ["true", "false"],
        current=str(s.long_macro_guard_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_interval"] = make_spin_setting(
        "宏观审计间隔",
        "每N章触发一次全量宏观审计（推荐 3-5）",
        s.long_macro_guard_interval,
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_max_adjustments"] = make_spin_setting(
        "最大调纲次数",
        "整本书最多允许调整大纲几次（推荐 3）",
        s.long_macro_guard_max_adjustments_per_book,
        0,
        10,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_cooldown"] = make_spin_setting(
        "调纲冷却期",
        "大纲调整后跳过几章不审计（推荐 5）",
        s.long_macro_guard_cooldown_chapters,
        0,
        20,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_drift_warning"] = make_float_setting(
        "宏观偏离警告阈值",
        "drift_score超过此值输出规划提示（推荐 0.3）",
        s.long_macro_guard_drift_threshold_warning,
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_drift_alert"] = make_float_setting(
        "宏观偏离告警阈值",
        "drift_score超过此值触发调纲确认（推荐 0.5）",
        s.long_macro_guard_drift_threshold_alert,
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_drift_critical"] = make_float_setting(
        "宏观偏离严重阈值",
        "drift_score超过此值建议回滚锚点（推荐 0.7）",
        s.long_macro_guard_drift_threshold_critical,
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_macro_guard_auto_apply_hint"] = make_combo_setting(
        "自动应用宏观提示",
        "连跑模式下自动将warning_hint约束应用到下一章规划",
        ["true", "false"],
        current=str(s.long_macro_guard_auto_apply_hint).lower(),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "全书审计",
        "控制深度分析、智能漏斗和审计对话框的全局默认值。",
    ).body_layout

    # 注意：以下参数为全局默认值，可在"全书审计"对话框中临时覆盖。
    # 对话框参数优先级 > 设置页面参数 > 代码默认值。
    priority_note = QLabel(
        "注意：以下参数为全局默认值，可在「全书审计」对话框中临时覆盖。对话框参数优先级 > 设置页面参数 > 代码默认值。"
    )
    priority_note.setObjectName("settingHint")
    layout.addWidget(priority_note)

    row, widgets["_book_audit_mode"] = make_combo_setting(
        "默认分析模式",
        "full_text=注入章节全文深审；summary=仅摘要（更省 token）",
        ["full_text", "summary"],
        current=str(getattr(s, "long_book_audit_default_mode", "full_text")).lower(),
        item_labels=["全文深审（推荐）", "摘要审计（更快）"],
    )
    layout.addWidget(row)
    row, widgets["_book_audit_chapter_max_chars"] = make_spin_setting(
        "单章最大字符数",
        "仅 full_text 模式生效；超出会截断并标记（推荐 8000-20000）",
        int(getattr(s, "long_book_audit_chapter_max_chars", 12000)),
        1000,
        100000,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_prompt_char_budget"] = make_spin_setting(
        "单次输入预算",
        "全书审计会按该字符预算自动拆分全文，避免模型上下文超限（推荐 32000-80000）",
        int(getattr(s, "long_book_audit_prompt_char_budget", 48000)),
        12000,
        500000,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_max_chapters_per_batch"] = make_spin_setting(
        "每批审查章节",
        "full_text 模式单次模型请求最多审查的章节数；还会受单次输入预算保护（推荐 6-16）",
        int(getattr(s, "long_book_audit_max_chapters_per_batch", 12)),
        1,
        500,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_max_tokens"] = make_spin_setting(
        "审计输出上限",
        "全书审计返回 JSON 的 max_tokens（推荐 4096-16384）",
        int(getattr(s, "long_book_audit_max_tokens", 8192)),
        1024,
        65536,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_max_issues_per_chunk"] = make_spin_setting(
        "每块最多问题",
        "单次模型调用最多返回的问题数；建议 8-12，过高容易导致 JSON 输出截断",
        int(getattr(s, "long_book_audit_max_issues_per_chunk", 12)),
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_issue_pool_max_items"] = make_spin_setting(
        "问题池条目上限",
        "注入全书审计提示词的问题面板条目上限，按严重度优先（推荐 80-200）",
        int(getattr(s, "long_book_audit_issue_pool_max_items", 160)),
        0,
        1000,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_location_strictness"] = make_combo_setting(
        "定位严格度",
        "strict=优先精确落段；balanced=平衡；loose=覆盖优先",
        ["strict", "balanced", "loose"],
        current=str(getattr(s, "long_book_audit_location_strictness", "balanced")).lower(),
        item_labels=["严格（优先精确到段）", "平衡（推荐）", "宽松（覆盖更多）"],
    )
    layout.addWidget(row)
    row, widgets["_book_audit_prompt_hint"] = make_line_setting(
        "审计附加提示词",
        "可留空；用于强调本项目的重点审计规则",
        str(getattr(s, "long_book_audit_prompt_hint", "")),
    )
    layout.addWidget(row)
    cb = QCheckBox("启用智能漏斗")
    cb.setToolTip("先摘要筛查，再对目标章节全文深审；建议开启，避免全量全文拆成数百块")
    cb.setChecked(bool(getattr(s, "long_book_audit_two_phase_enabled", True)))
    widgets["_book_audit_two_phase_enabled"] = cb
    layout.addWidget(cb)
    row, widgets["_book_audit_two_phase_threshold"] = make_float_setting(
        "漏斗标记阈值",
        "摘要标记章节占比超过该值时按优先级截断目标章节，不回退全量全文",
        float(getattr(s, "long_book_audit_two_phase_threshold", 0.7)),
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_two_phase_max_target_chapters"] = make_spin_setting(
        "漏斗目标章节上限",
        "摘要筛查后进入全文深审的最多章节数（推荐 12-24）",
        int(getattr(s, "long_book_audit_two_phase_max_target_chapters", 24)),
        1,
        500,
    )
    layout.addWidget(row)
    # Note: auto_repair removed from UI; dialog's repair_mode combo (off/targeted) controls this.
    # The config field long_book_audit_auto_repair is kept for backward compatibility.
    layout = _make_settings_subsection(
        sec,
        "全书修复",
        "控制全书审计后的自动修复、问题池锚定和防污染闸门。",
    ).body_layout
    row, widgets["_book_audit_repair_min_severity"] = make_combo_setting(
        "自动修复最低级别",
        "仅修复达到该严重度的审计问题",
        ["critical", "warning", "info"],
        current=str(getattr(s, "long_book_audit_repair_min_severity", "warning")).lower(),
    )
    layout.addWidget(row)
    row, widgets["_book_audit_repair_max_chapters"] = make_spin_setting(
        "自动修复最多章节",
        "防止一次全书审计触发过多修复任务（推荐 6-20）",
        int(getattr(s, "long_book_audit_repair_max_chapters", 12)),
        1,
        500,
    )
    layout.addWidget(row)
    cb = QCheckBox("启用问题池锚定")
    cb.setToolTip("复用问题面板（连贯性/因果）作为修复索引锚点，提升精准定位与修复命中率")
    cb.setChecked(bool(getattr(s, "long_book_audit_use_issue_panel_pool", True)))
    widgets["_book_audit_use_issue_pool"] = cb
    layout.addWidget(cb)
    row, widgets["_book_audit_repair_concurrency"] = make_spin_setting(
        "自动修复并发上限",
        "1=串行最稳；>1 使用有界并发调度（写入阶段仍受项目锁保护）",
        int(getattr(s, "long_book_audit_repair_concurrency", 1)),
        1,
        8,
    )
    layout.addWidget(row)
    cb = QCheckBox("生成卷帙修复报告")
    cb.setToolTip("审计完成后在 reports/ 生成可在卷帙直接阅读的全书修复报告")
    cb.setChecked(bool(getattr(s, "long_book_audit_generate_repair_report", True)))
    widgets["_book_audit_generate_repair_report"] = cb
    layout.addWidget(cb)
    cb = QCheckBox("修复后防污染闸门")
    cb.setToolTip("疑似提示词/JSON/重复段落/异常大改动写入正文时，自动回滚该章并转人工复核")
    cb.setChecked(bool(getattr(s, "long_book_audit_repair_guard_enabled", True)))
    widgets["_book_audit_repair_guard_enabled"] = cb
    layout.addWidget(cb)
    row, widgets["_book_audit_repair_guard_max_delta_ratio"] = make_float_setting(
        "闸门改动比例上限",
        "单章自动修复改动比例超过此值会回滚（推荐 0.08-0.15）",
        float(getattr(s, "long_book_audit_repair_guard_max_delta_ratio", 0.12)),
        0.01,
        1.0,
        decimals=2,
        step=0.01,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_repair_guard_max_added_chars"] = make_spin_setting(
        "闸门新增字数上限",
        "单章自动修复新增字数超过此值会回滚（推荐 400-800）",
        int(getattr(s, "long_book_audit_repair_guard_max_added_chars", 600)),
        100,
        10000,
    )
    layout.addWidget(row)
    row, widgets["_book_audit_max_continue_batches"] = make_spin_setting(
        "自动续修最大批次数",
        "达到此批次数后停止自动续修（防止无限循环）",
        int(getattr(s, "long_book_audit_max_continue_batches", 10)),
        1,
        50,
    )
    layout.addWidget(row)
    cb = QCheckBox("分块并行")
    cb.setToolTip("将审计拆分为多块并发调用（2-4x 加速）")
    cb.setChecked(bool(getattr(s, "long_book_audit_parallel_chunks", True)))
    widgets["_book_audit_parallel_chunks"] = cb
    layout.addWidget(cb)
    cb = QCheckBox("维度并行")
    cb.setToolTip("命名/时间线/世界观/角色/漂移分别审查（3-5x 加速，成本更高）")
    cb.setChecked(bool(getattr(s, "long_book_audit_parallel_dimensions", True)))
    widgets["_book_audit_parallel_dimensions"] = cb
    layout.addWidget(cb)

    layout = _make_settings_subsection(
        sec,
        "连续性与开场修复",
        "控制跨章连贯性、禁用元素和开场承接修复。",
    ).body_layout
    row, widgets["_continuity_repair_threshold"] = make_float_setting(
        "触发阈值",
        "分数低于此值才触发修复（推荐 8.5-9.5，0.0=始终修复）",
        s.long_continuity_repair_threshold,
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_continuity_max_repair_rounds"] = make_spin_setting(
        "最大修复轮数",
        "每轮修复后重新校验，无必修问题时提前退出。"
        "0=禁用连贯性修复；推荐 2-3（增加轮数可提高禁用元素等问题的修复成功率）",
        s.long_continuity_max_repair_rounds,
        0,
        5,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_cross_chapter_window"] = make_spin_setting(
        "跨章节禁用窗口（章）",
        "0=仅上章硬禁；N=最近N章作为软约束提示（推荐 3-6）",
        s.forbidden_elements_cross_chapter_window,
        0,
        30,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_hard_max_items"] = make_spin_setting(
        "硬禁提示上限",
        "单章进入生产提示的硬禁修辞项数量；会先按来源和本章语境相关性排序。",
        int(getattr(s, "forbidden_elements_hard_max_items", 8)),
        0,
        50,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_soft_max_items"] = make_spin_setting(
        "软禁提示上限",
        "单章进入生产提示的软禁修辞项数量；默认 12，过高会分散模型注意力。",
        int(getattr(s, "forbidden_elements_soft_max_items", 12)),
        0,
        80,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_quota_max_items"] = make_spin_setting(
        "限额回环上限",
        "单章有限额复用/回环项数量上限；用于保护必要锚点但避免铺太多约束。",
        int(getattr(s, "forbidden_elements_quota_max_items", 6)),
        0,
        50,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_sources_max_items"] = make_spin_setting(
        "禁用来源记录上限",
        "每章保留的禁用元素来源追踪记录数量；仅用于审计和调试。",
        int(getattr(s, "forbidden_element_sources_max_items", 24)),
        0,
        160,
    )
    layout.addWidget(row)
    row, widgets["_forbidden_rank_by_relevance"] = make_combo_setting(
        "禁用项相关性排序",
        "开启后先按来源置信度和本章语境相关性排序，再取各类上限内的前若干项。",
        ["true", "false"],
        current=str(getattr(s, "forbidden_elements_rank_by_relevance", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_opening_guard_enabled"] = make_combo_setting(
        "开场硬门禁",
        "质量检查前先做开场承接预筛并尝试窄窗口修复",
        ["true", "false"],
        current=str(getattr(s, "long_opening_guard_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_opening_guard_max_issues"] = make_spin_setting(
        "门禁问题上限",
        "开场硬门禁单次最多处理的问题数（按严重度/置信度排序）",
        int(getattr(s, "long_opening_guard_max_issues", 2)),
        1,
        6,
    )
    layout.addWidget(row)
    row, widgets["_plan_max_key_revelations"] = make_spin_setting(
        "单章重大揭示上限",
        "规划阶段允许的 key_revelations 最大数量（推荐 1-3）",
        int(getattr(s, "long_plan_max_key_revelations_per_chapter", 2)),
        1,
        8,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "因果修复",
        "控制因果逻辑校验和修复退出策略。",
    ).body_layout
    row, widgets["_causal_repair_enabled"] = make_combo_setting(
        "启用因果修复",
        "是否对章节进行因果逻辑修复",
        ["true", "false"],
        current=str(s.long_causal_repair_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_causal_threshold"] = make_float_setting(
        "退出阈值",
        "修复过程中分数达到此值即提前停止（推荐 4.0-6.0，0.0=始终跑满最大轮次）",
        s.long_causal_threshold,
        0.0,
        10.0,
        decimals=1,
        step=0.5,
    )
    layout.addWidget(row)
    row, widgets["_causal_max_repair_rounds"] = make_spin_setting(
        "最大修复轮数",
        "每轮修复后重新校验，分数达标时提前退出（0=禁用，推荐 2-3）",
        s.long_causal_max_repair_rounds,
        0,
        5,
    )
    layout.addWidget(row)
    row, widgets["_causal_fail_mode"] = make_combo_setting(
        "因果失败策略",
        "warn_unknown=标记未知并告警；fail_open=沿用旧行为（失败视为通过）",
        ["warn_unknown", "fail_open"],
        current=str(getattr(s, "causal_validation_fail_mode", "warn_unknown")),
        item_labels=["告警并标记未知（Recommended）", "失败时放行"],
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "修复流程与本地检查",
        "控制自动修复策略、章内流程检查和本地护栏预筛。",
    ).body_layout
    row, widgets["_repair_control_mode"] = make_combo_setting(
        "修复控制模式",
        "manual=只给建议与预览；ai_assisted=低风险自动修，高风险确认；ai_auto=自动执行、复检与回滚",
        ["manual", "ai_assisted", "ai_auto"],
        current=str(getattr(s, "repair_control_mode", "ai_assisted")),
        item_labels=["全手动", "AI 伴随（推荐）", "AI 全自动"],
    )
    layout.addWidget(row)
    row, widgets["_max_auto_repair_attempts"] = make_spin_setting(
        "最大自动修复次数",
        "普通问题的自动重试上限（推荐 2-4，必修级别不受此限制）",
        s.max_auto_repair_attempts,
        1,
        10,
    )
    layout.addWidget(row)
    row, widgets["_repair_must_fix_severity"] = make_combo_setting(
        "必修级别",
        "达到此严重程度时强制继续修复，无视最大次数限制（连贯性与因果链共用）",
        ["critical", "high", "medium", "off"],
        current=s.repair_must_fix_severity,
    )
    layout.addWidget(row)
    row, widgets["_recheck_strategy"] = make_combo_setting(
        "复检策略",
        "修复后复检范围：建议启用全局高危守卫，避免\u201c修旧生新\u201d漏检",
        ["targeted_with_global_guard", "strict_targeted"],
        current=s.recheck_strategy,
        item_labels=["目标点验 + 全局高危守卫（推荐）", "严格目标点验（更快）"],
    )
    layout.addWidget(row)
    row, widgets["_change_budget_threshold"] = make_float_setting(
        "单轮改动预算",
        "单轮修复改动占比上限（0.15=15%，超限会触发全量复审）",
        s.change_budget_threshold,
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_repair_display_min_severity"] = make_combo_setting(
        "面板显示最低严重程度",
        "低于此级别的问题不在问题面板中显示（不影响已存报告）",
        ["low", "medium", "high", "critical"],
        current=s.repair_display_min_severity,
    )
    layout.addWidget(row)
    row, widgets["_repair_always_reaudit"] = make_combo_setting(
        "修复后始终重审核",
        "开启后：不论 AI 是否改动了文本，修复完成后都会额外运行一次审核来刷新问题面板。"
        "关闭（默认）：仅 AI 实际修改了文本（applied=True）时才重审核，节省 token。",
        ["false", "true"],
        current=str(s.repair_always_reaudit).lower(),
        item_labels=[
            "关闭（AI 无改动时跳过重审，节省 token）",
            "开启（始终重审核，确认问题是否真正解决）",
        ],
    )
    layout.addWidget(row)
    add_setting_group_description(
        layout,
        "\u201c必修级别\u201d建议保持 critical（默认），防止严重问题因重试次数限制而搁置。\n"
        "设为 medium 可让中等问题也无视上限持续修复，适合追求更高质量的场景。\n"
        "\u201c复检策略\u201d建议保持\u201c目标点验 + 全局高危守卫\u201d，避免修复后新增高危问题漏检。\n"
        "\u201c单轮改动预算\u201d建议 0.10-0.20；值越小越保守，越能抑制大改写导致的连锁回归。\n"
        "\u201c面板显示最低严重程度\u201d设为 medium 可隐藏纯建议性轻微问题，减少干扰。\n"
        "\u201c修复后始终重审核\u201d用于排查\u201c问题是否真正解决\u201d。AI 未改动文本时开启会多消耗"
        " token 但结果大概率不变；确认问题解决后建议关闭以节省成本。",
    )

    row, widgets["_check_chapter_enabled"] = make_combo_setting(
        "章内质量检查",
        "章节生成后运行章内质量检查（提示词泄露、非法时间、重复、POV 等）",
        ["true", "false"],
        current=str(s.long_check_chapter_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_max_consistency_replans"] = make_spin_setting(
        "一致性重试上限",
        "章节写作后质量校验未通过时，自动重新规划并重写的最大次数。"
        "0=校验失败直接标记为失败；1-5=允许自动重试（推荐 2）。"
        "归档前仍受总评、连贯性、因果和可配置归档策略约束；未达硬线会重新规划或失败。",
        s.long_max_consistency_replans,
        0,
        5,
    )
    layout.addWidget(row)

    row, widgets["_local_check_enabled"] = make_combo_setting(
        "本地检查模式",
        "prescreen=快速预筛选（推荐），off=仅 LLM",
        ["prescreen", "off"],
        current="prescreen" if s.local_check_as_prescreen else "off",
    )
    layout.addWidget(row)
    row, widgets["_local_confidence_threshold"] = make_float_setting(
        "本地检查置信度阈值",
        "仅传递 >= 此置信度的结果（推荐 0.5-0.8）",
        s.local_check_confidence_threshold,
        0.0,
        1.0,
        decimals=2,
        step=0.1,
    )
    layout.addWidget(row)
    row, widgets["_guardrails_trust"] = make_combo_setting(
        "本地护栏信任级别",
        "balanced=本地检测提示+LLM判定（推荐）",
        ["strict", "balanced", "llm_first"],
        current=s.local_guardrails_trust_level,
    )
    layout.addWidget(row)
    row, widgets["_pronoun_autofix_mode"] = make_combo_setting(
        "代词自动修复",
        "默认建议关闭自动改写，仅做检查告警；需要时再开启",
        ["off", "pov_only", "pov_or_many"],
        current=s.pronoun_autofix_mode,
        item_labels=[
            "off（仅检查告警，最稳）",
            "pov_only（仅修复 POV 代词错误）",
            "pov_or_many（POV或问题较多时修复，含机械兜底）",
        ],
    )
    layout.addWidget(row)

    sec_element_progress = _make_settings_subsection(
        sec,
        "元素进度裁判",
        "默认仅使用规则判定。开启后，仅对灰区结果触发低频 LLM 复判，"
        "用于减少要素命中误判（会增加少量 token 消耗）。",
    )
    row, widgets["_element_progress_arbiter_enabled"] = make_combo_setting(
        "启用灰区仲裁",
        "开启后按灰区规则触发低频复判",
        ["true", "false"],
        current=str(s.element_progress_llm_arbiter_enabled).lower(),
    )
    sec_element_progress.body_layout.addWidget(row)
    row, widgets["_element_progress_arbiter_max_items"] = make_spin_setting(
        "每章仲裁上限",
        "每章最多仲裁的要素数量（0=禁用）",
        s.element_progress_llm_arbiter_max_items_per_chapter,
        0,
        5,
    )
    sec_element_progress.body_layout.addWidget(row)
    row, widgets["_element_progress_gray_low"] = make_float_setting(
        "灰区分数下界",
        "规则分数落在灰区才会触发仲裁（含边界）",
        s.element_progress_llm_gray_score_low,
        0.0,
        10.0,
        decimals=2,
        step=0.1,
    )
    sec_element_progress.body_layout.addWidget(row)
    row, widgets["_element_progress_gray_high"] = make_float_setting(
        "灰区分数上界",
        "建议高于下界；若反填将由运行时自动纠正",
        s.element_progress_llm_gray_score_high,
        0.0,
        10.0,
        decimals=2,
        step=0.1,
    )
    sec_element_progress.body_layout.addWidget(row)
    row, widgets["_element_progress_arbiter_max_tokens"] = make_spin_setting(
        "仲裁输出上限",
        "单次仲裁输出 token 上限（建议 128-384）",
        s.element_progress_llm_arbiter_max_tokens,
        64,
        1024,
    )
    sec_element_progress.body_layout.addWidget(row)
    row, widgets["_element_progress_arbiter_temp"] = make_float_setting(
        "仲裁温度",
        "建议低温（0.0-0.2）以保持判定稳定",
        s.element_progress_llm_arbiter_temperature,
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    sec_element_progress.body_layout.addWidget(row)
    widgets["_element_progress_hint_label"] = QLabel("")
    widgets["_element_progress_hint_label"].setObjectName("fieldHint")
    widgets["_element_progress_hint_label"].setWordWrap(True)
    sec_element_progress.body_layout.addWidget(widgets["_element_progress_hint_label"])
    widgets["_element_progress_notes"] = QLabel(
        "参数备注：\n"
        "1) 启用灰区仲裁：仅对规则判定不确定（灰区）的要素进行复判。\n"
        "2) 每章仲裁上限：控制每章最多触发多少次复判，直接影响额外 token 消耗。\n"
        "3) 灰区分数上下界：规则分数落在该区间才会触发复判；区间越宽触发越多。\n"
        "4) 仲裁输出上限：单次复判回答长度上限，建议 128-384。\n"
        "5) 仲裁温度：建议低温（0.0-0.2）保证判定稳定。"
    )
    widgets["_element_progress_notes"].setObjectName("panelDescription")
    widgets["_element_progress_notes"].setWordWrap(True)
    sec_element_progress.body_layout.addWidget(widgets["_element_progress_notes"])

    return sec, widgets

