"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains init_coherence.py builders.
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


def _add_init_coherence_controls(
    sec: CollapsibleSection,
    widgets: dict[str, Any],
    s: Any,
) -> None:
    claims_sec = _make_settings_subsection(
        sec,
        "初始化一致性 Claims",
        "抽取蓝图、章纲和契约中的可裁判叙事声明，控制召回、分批和并发。",
    )
    layout = claims_sec.body_layout
    row, widgets["_init_coh_use_memory"] = make_combo_setting(
        "使用记忆召回",
        "在结构化索引之外启用语义向量召回相似叙事事实",
        ["true", "false"],
        current=str(getattr(s, "init_coherence_use_memory", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_coh_claim_batch_size"] = make_spin_setting(
        "Claims 批大小",
        "每批抽取 claims 的初始最大章节/片段数；块过大时会按载荷预算自动拆小",
        int(getattr(s, "init_coherence_claim_batch_size", 8)),
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_claim_payload_budget"] = make_spin_setting(
        "Claims 载荷预算",
        "单块输入 payload 字符预算；超出后自动缩小章节范围，默认 18000",
        int(getattr(s, "init_coherence_claim_payload_char_budget", 18000)),
        1000,
        200000,
        step=1000,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_claim_max_parallel"] = make_spin_setting(
        "Claims 并发",
        "同时抽取 claims 的最大分块数；2 最稳，3-4 可加速，过高易限流或触发坏 JSON 修复",
        int(getattr(s, "init_coherence_claim_max_parallel", 2)),
        1,
        8,
    )
    layout.addWidget(row)
    row, widgets["_init_blueprint_holistic_claims"] = make_combo_setting(
        "蓝图整体一致性 Claims",
        "完整阅读叙事蓝图补充跨字段一致性 claims",
        ["true", "false"],
        current=str(getattr(s, "init_blueprint_holistic_claims_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_blueprint_holistic_claim_tokens"] = make_spin_setting(
        "蓝图整体一致性 Claims Token",
        "完整蓝图一致性 Claims 抽取的基础输出 token 上限；越高越稳但更慢",
        int(getattr(s, "init_blueprint_holistic_claim_max_tokens", 4096)),
        1024,
        16000,
    )
    layout.addWidget(row)
    row, widgets["_init_stream_claim_prefetch"] = make_combo_setting(
        "流式预取",
        "章节大纲每批完成后立即预取一致性 Claims",
        ["true", "false"],
        current=str(getattr(s, "init_stream_claim_prefetch_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_coh_overlap_chapters"] = make_spin_setting(
        "重叠章节",
        "分批抽取时注入的前后章节上下文窗口；单章仍过大时会优先裁掉上下文",
        int(getattr(s, "init_coherence_overlap_chapters", 2)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_semantic_top_k"] = make_spin_setting(
        "语义召回 TopK",
        "每条 claim 从记忆中召回的相似事实数量",
        int(getattr(s, "init_coherence_semantic_top_k", 12)),
        0,
        50,
    )
    layout.addWidget(row)

    judge_sec = _make_settings_subsection(
        sec,
        "初始化一致性裁判与修复",
        "控制候选冲突裁判、局部修复、复查窗口和初始化阻断阈值。",
    )
    layout = judge_sec.body_layout
    row, widgets["_init_coh_candidate_max"] = make_spin_setting(
        "候选上限",
        "每个初始化阶段最多送入 LLM 裁判的候选冲突数",
        int(getattr(s, "init_coherence_candidate_max_per_batch", 80)),
        1,
        500,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_llm_candidate_batch"] = make_spin_setting(
        "裁判批大小",
        "每次 LLM 候选冲突裁判的候选组数量",
        int(getattr(s, "init_coherence_llm_candidate_batch_size", 8)),
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_llm_candidate_max_parallel"] = make_spin_setting(
        "裁判并发",
        "同时进行候选冲突裁判的最大批次数；2 最稳，3-4 可加速，过高会增加成本与限流风险",
        int(getattr(s, "init_coherence_llm_candidate_max_parallel", 2)),
        1,
        8,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_confidence_threshold"] = make_float_setting(
        "置信阈值",
        "记忆语义召回采用的默认相似度阈值",
        float(getattr(s, "init_coherence_confidence_threshold", 0.75)),
        0.0,
        1.0,
        decimals=2,
        step=0.05,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_recheck_window"] = make_spin_setting(
        "复查窗口",
        "局部修复后重查受影响章节前后的窗口大小",
        int(getattr(s, "init_coherence_recheck_affected_window", 2)),
        0,
        12,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_auto_repair"] = make_combo_setting(
        "LLM 局部修复",
        "裁判给出可定位 scope 时自动生成受限 JSON Patch",
        ["true", "false"],
        current=str(getattr(s, "init_coherence_auto_repair", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_coh_deterministic_repair"] = make_combo_setting(
        "确定性元数据修复",
        "先用本地规则修复章节归属、伏笔时序、重复 payoff_id 等结构化问题",
        ["true", "false"],
        current=str(getattr(s, "init_coherence_deterministic_repair", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_coh_repair_rounds"] = make_spin_setting(
        "修复轮次",
        "每层 artifact 自动修复最大轮次（推荐 2）",
        int(getattr(s, "init_coherence_max_repair_rounds", 2)),
        0,
        3,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_block_min_severity"] = make_combo_setting(
        "阻断严重度",
        "达到该严重度的裁判问题会阻断或触发修复",
        ["high", "critical", "medium", "low"],
        current=str(getattr(s, "init_coherence_block_min_severity", "high")),
    )
    layout.addWidget(row)
    row, widgets["_init_coh_patch_max_ops"] = make_spin_setting(
        "Patch 上限",
        "一次初始化局部修复最多允许的 JSON Patch 操作数",
        int(getattr(s, "init_coherence_patch_max_ops", 40)),
        1,
        200,
    )
    layout.addWidget(row)
    row, widgets["_init_coh_target_patch_batch_size"] = make_spin_setting(
        "Target 批次",
        "一次初始化精准修复最多下发给模型的定位目标数",
        int(getattr(s, "init_coherence_target_patch_batch_size", 12)),
        1,
        40,
    )
    layout.addWidget(row)
    row, widgets["_init_source_artifact_auto_repair"] = make_combo_setting(
        "源头准入自愈",
        "source_artifacts 准入发现可定位问题时，自动尝试受限修复并复验",
        ["true", "false"],
        current=str(getattr(s, "init_source_artifact_auto_repair", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_source_artifact_repair_rounds"] = make_spin_setting(
        "源头自愈轮次",
        "source_artifacts 准入自动修复最大轮次；0=只报告不修复",
        int(getattr(s, "init_source_artifact_repair_rounds", 2)),
        0,
        3,
    )
    layout.addWidget(row)

    readiness_sec = _make_settings_subsection(
        sec,
        "初始化准入与增强",
        "控制契约覆盖审计、准入硬门和低风险创意增强。",
    )
    layout = readiness_sec.body_layout
    row, widgets["_init_claim_coverage_enabled"] = make_combo_setting(
        "Claims 覆盖审计",
        "章节契约通过后，检查高价值 Claims 是否已被最终契约吸收；不进入章节 prompt",
        ["true", "false"],
        current=str(getattr(s, "init_claim_coverage_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_claim_coverage_block_p0"] = make_combo_setting(
        "P0 未覆盖阻断",
        "P0 Claim 未出现在章节契约中时阻断初始化准入",
        ["true", "false"],
        current=str(getattr(s, "init_claim_coverage_block_p0", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_claim_coverage_block_p1"] = make_combo_setting(
        "P1 未覆盖阻断",
        "P1 Claim 未覆盖时默认只告警；开发调试可开启阻断",
        ["false", "true"],
        current=str(getattr(s, "init_claim_coverage_block_p1", False)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_claim_coverage_block_degraded"] = make_combo_setting(
        "Claims 账本缺失阻断",
        "Claims 账本缺失或损坏时默认阻断初始化准入，避免契约覆盖假通过",
        ["true", "false"],
        current=str(getattr(s, "init_claim_coverage_block_degraded", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_disable_local_story_fallbacks"] = make_combo_setting(
        "禁用本地剧情兜底",
        "只保留通用安全校验，题材/剧情判断交给 LLM 裁判",
        ["true", "false"],
        current=str(getattr(s, "init_disable_local_story_fallbacks", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_creative_refinement_enabled"] = make_combo_setting(
        "保守创意增强",
        "蓝图审查前可选低风险增强；默认关闭",
        ["false", "true"],
        current=str(getattr(s, "init_creative_refinement_enabled", False)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_creative_refinement_auto_apply"] = make_combo_setting(
        "自动应用低风险增强",
        "仅应用带证据和 scope 的 low risk patch",
        ["true", "false"],
        current=str(getattr(s, "init_creative_refinement_auto_apply_low_risk", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_init_readiness_required"] = make_combo_setting(
        "准入报告必需",
        "章节生成前必须检查 init_readiness.json 是否允许继续",
        ["true", "false"],
        current=str(getattr(s, "init_readiness_required", True)).lower(),
    )
    layout.addWidget(row)
