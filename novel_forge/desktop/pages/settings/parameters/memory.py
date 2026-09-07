"""Sub-module of novel_forge.desktop.pages.settings.parameters.

Auto-generated in the M3.6 split. Contains memory.py builders.
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


def _build_memory_params(
    s: Any,
    embedding_profile_choices: list[tuple[str, str]],
) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Section 8: Memory Module."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("记忆模块 — 语义检索与质量增强", expanded=False)

    layout = _make_settings_subsection(
        sec,
        "情节记忆",
        "控制语义检索和嵌入模型。",
    ).body_layout
    row, widgets["_memory_episodic_enabled"] = make_combo_setting(
        "启用情景记忆",
        "📊 语义检索历史事件，增强上下文连贯性 [嵌入模型]",
        ["true", "false"],
        current=str(s.memory_episodic_enabled).lower(),
    )
    layout.addWidget(row)

    current_embedding = s.memory_embedding_profile_id or "auto"
    if current_embedding not in [c[0] for c in embedding_profile_choices]:
        current_embedding = "auto"
    row, widgets["_memory_embedding_profile"] = make_combo_setting(
        "嵌入模型",
        "📊 选择用于向量检索的嵌入模型；正式记忆路径需要真实 embedding",
        [c[0] for c in embedding_profile_choices],
        current=current_embedding,
        item_labels=[c[1] for c in embedding_profile_choices],
    )
    layout.addWidget(row)

    current_backend = str(getattr(s, "memory_vector_store_backend", "zvec") or "zvec")
    if current_backend not in {"zvec", "in_memory"}:
        current_backend = "zvec"
    row, widgets["_memory_vector_store_backend"] = make_combo_setting(
        "向量后端",
        "zvec 为正式后端；in_memory 仅用于测试或模拟 embedding。",
        ["zvec", "in_memory"],
        current=current_backend,
        item_labels=["zvec（正式）", "in_memory（测试/mock）"],
    )
    layout.addWidget(row)

    current_zvec_index = str(getattr(s, "memory_zvec_index_type", "hnsw") or "hnsw")
    zvec_index_choices = ["hnsw", "ivf", "flat", "hnsw_rabitq", "diskann"]
    if current_zvec_index not in zvec_index_choices:
        current_zvec_index = "hnsw"
    row, widgets["_memory_zvec_index_type"] = make_combo_setting(
        "zvec 索引类型",
        "HNSW 为默认；DiskANN 适合大规模低内存场景，但需底层平台支持。",
        zvec_index_choices,
        current=current_zvec_index,
        item_labels=["HNSW（默认）", "IVF", "FLAT", "HNSW RaBitQ", "DiskANN"],
    )
    layout.addWidget(row)

    row, widgets["_memory_semantic_search"] = make_combo_setting(
        "语义搜索增强",
        "📊 在 CanonRetriever 中启用语义检索 [嵌入模型]",
        ["true", "false"],
        current=str(s.memory_semantic_search_enabled).lower(),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "摘要层级",
        "控制场景、章节、卷和弧线摘要。",
    ).body_layout
    row, widgets["_memory_summary_enabled"] = make_combo_setting(
        "启用摘要服务",
        "📝 生成场景/章节/卷/弧线级别的多粒度摘要 [通用模型]",
        ["true", "false"],
        current=str(s.memory_multi_granularity_summary_enabled).lower(),
    )
    layout.addWidget(row)

    row, widgets["_memory_chapter_summary_target_words"] = make_spin_setting(
        "章节摘要目标字数",
        "SUMMARIZE_CHAPTER 的 summary 字段目标长度；越长越利于承接，越短越省 token",
        int(getattr(s, "memory_chapter_summary_target_words", 200)),
        50,
        2000,
    )
    layout.addWidget(row)

    row, widgets["_memory_volume_summary_target_words"] = make_spin_setting(
        "卷级摘要目标字数",
        "跨卷承接与全局记忆的目标长度；超长篇推荐 800-1600",
        int(getattr(s, "memory_volume_summary_target_words", 1000)),
        300,
        5000,
        step=100,
    )
    layout.addWidget(row)

    row, widgets["_memory_summary_input_token_budget"] = make_spin_setting(
        "摘要单次输入预算",
        "超过预算自动做完整覆盖的 LLM 分块归纳，不会截掉后半段；推荐 16000-48000",
        int(getattr(s, "memory_summary_input_token_budget", 24000)),
        2048,
        120000,
        step=1024,
    )
    layout.addWidget(row)

    row, widgets["_memory_summary_recent_chapters"] = make_spin_setting(
        "近章摘要窗口",
        "规划/生成直接注入的最近章数；更早内容由 Zvec 和卷级摘要承担，推荐 3-8",
        int(getattr(s, "memory_summary_recent_chapters", 5)),
        1,
        20,
    )
    layout.addWidget(row)

    row, widgets["_memory_volume_summary_threshold"] = make_spin_setting(
        "不分卷时卷摘要注入阈值(章)",
        "当前章节超过此值时注入卷级摘要（仅不分卷项目回退用；分卷项目按卷进度自动判断）",
        s.memory_volume_summary_inject_threshold,
        1,
        200,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "压缩与索引",
        "控制带质量验证的上下文压缩和章节归档后的并发索引。",
    ).body_layout
    compression_layout = layout
    row, widgets["_memory_compression_enabled"] = make_combo_setting(
        "启用自适应压缩",
        "📝 带质量验证的上下文压缩 [通用模型]",
        ["true", "false"],
        current=str(s.memory_adaptive_compression_enabled).lower(),
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "母题基础",
        "控制母题追踪、重复检查和当前章关联窗口。",
    ).body_layout
    row, widgets["_memory_motif_enabled"] = make_combo_setting(
        "启用母题追踪",
        "📝 追踪主题/母题的回调与重复 [通用模型]",
        ["true", "false"],
        current=str(s.memory_motif_tracking_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_memory_motif_repetition"] = make_combo_setting(
        "重复检查",
        "检测非刻意的母题重复",
        ["true", "false"],
        current=str(s.memory_motif_check_repetition).lower(),
    )
    layout.addWidget(row)
    row, widgets["_motif_suggestion_min_chapters"] = make_spin_setting(
        "建议最小章数间隔",
        "母题在此章数内曾使用则不提示（默认 5，调高可减少提示频率）",
        s.motif_suggestion_min_chapters,
        2,
        30,
    )
    layout.addWidget(row)
    row, widgets["_motif_suggestion_min_occurrences"] = make_spin_setting(
        "建议最小出现次数",
        "母题至少出现过此次数才会生成建议（默认 3）",
        s.motif_suggestion_min_occurrences,
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_memory_motif_related_lookback"] = make_spin_setting(
        "母题关联窗口(章)",
        "控制'当前章关联母题'范围：0=仅本章，2=前两章+本章（默认）。会影响后台提示与章台显示。",
        s.memory_motif_related_lookback_chapters,
        0,
        20,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "母题提示与重复治理",
        "控制母题进入提示词的预算、裁剪和近距重复检测。",
    ).body_layout
    row, widgets["_motif_prompt_token_budget"] = make_spin_setting(
        "母题提示预算(token)",
        "控制母题上下文进入提示词的估算 token 上限；500 偏保守，母题密集可调到 800-1000。",
        int(getattr(s, "motif_prompt_token_budget", 500)),
        100,
        5000,
    )
    layout.addWidget(row)
    row, widgets["_motif_prompt_max_items"] = make_spin_setting(
        "预算裁剪上限(项)",
        "预算超限时列表裁剪的单类上限；默认 8。越高越保留信息，但更占 prompt。",
        int(getattr(s, "motif_prompt_max_items", 8)),
        1,
        30,
    )
    layout.addWidget(row)
    row, widgets["_motif_forbidden_max_items"] = make_spin_setting(
        "避重复提示上限",
        "每次写作提示允许进入 prompt 的近期避重复母题数量；默认 3，调高会更谨慎但可能抑制表达。",
        int(getattr(s, "motif_forbidden_max_items", 3)),
        0,
        30,
    )
    layout.addWidget(row)
    row, widgets["_motif_repetition_lookback"] = make_spin_setting(
        "重复检查回看(章)",
        "控制母题无意识重复检测回看多少章；默认 5。独立于活跃母题提示窗口。",
        int(getattr(s, "motif_repetition_lookback_chapters", 5)),
        0,
        50,
    )
    layout.addWidget(row)
    row, widgets["_motif_repetition_recent_gap"] = make_spin_setting(
        "近距重复阈值(章)",
        "章距小于该值时才判为过近复用；默认 2 表示只拦截紧邻上一章。",
        int(getattr(s, "motif_repetition_recent_gap_chapters", 2)),
        1,
        20,
    )
    layout.addWidget(row)
    row, widgets["_motif_dormant_callback_min"] = make_spin_setting(
        "沉睡回调间隔(章)",
        "母题至少沉睡多少章后进入长线回调建议；默认 20。",
        int(getattr(s, "motif_dormant_callback_min_chapters", 20)),
        5,
        200,
    )
    layout.addWidget(row)
    layout = _make_settings_subsection(
        sec,
        "母题遗忘与维护",
        "控制非重要母题自动退役、角色历史回溯和母题重新提取。",
    ).body_layout
    row, widgets["_motif_auto_forget_ephemeral"] = make_combo_setting(
        "自动遗忘非重要母题",
        "自动退役低重要度、低出现次数、长期未复现的母题；保留历史但不再进入提示词。",
        ["true", "false"],
        current=str(getattr(s, "motif_auto_forget_ephemeral_enabled", True)).lower(),
    )
    layout.addWidget(row)
    row, widgets["_motif_ephemeral_forget_after"] = make_spin_setting(
        "非重要母题遗忘间隔(章)",
        "低重要度母题在多少章未复现后自动退役；默认 12。",
        int(getattr(s, "motif_ephemeral_forget_after_chapters", 12)),
        3,
        200,
    )
    layout.addWidget(row)
    row, widgets["_motif_ephemeral_max_occurrences"] = make_spin_setting(
        "遗忘出现次数上限",
        "只自动遗忘出现次数不超过该值的母题；默认 1，避免误伤已形成模式的母题。",
        int(getattr(s, "motif_ephemeral_max_occurrences", 1)),
        0,
        10,
    )
    layout.addWidget(row)
    row, widgets["_motif_ephemeral_importance_threshold"] = make_spin_setting(
        "遗忘重要度阈值(%)",
        "importance_score 不高于该百分比才可自动退役；默认 35。",
        int(getattr(s, "motif_ephemeral_importance_threshold_pct", 35)),
        0,
        100,
    )
    layout.addWidget(row)
    row, widgets["_draft_char_history_lookback"] = make_spin_setting(
        "角色历史回溯窗口(章)",
        "草稿阶段从记忆系统获取角色状态历史与关系变化的前溯章数。"
        "值越大角色行为越一致，但上下文更长。默认 10，范围 1-50。",
        s.long_draft_character_history_lookback,
        1,
        50,
    )
    layout.addWidget(row)
    row, widgets["_memory_motif_re_extract_concurrency"] = make_spin_setting(
        "母题重新提取并发数",
        "修补母题历史→强制重新提取时，同时处理的章节数（默认 3，范围 1-10）。"
        "值越高越快，但并发 LLM 调用越多。",
        s.memory_motif_re_extract_concurrency,
        1,
        10,
    )
    layout.addWidget(row)
    row, widgets["_temp_extract_motifs"] = make_float_setting(
        "母题提取温度",
        "母题提取 LLM 调用的温度参数（0=确定性，1=创造性，默认 0.3）",
        s.temp_extract_motifs,
        0.0,
        2.0,
        decimals=1,
        step=0.1,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "Critic 评审",
        "控制独立评审代理的异步、超时和缓存。",
    ).body_layout
    row, widgets["_memory_critic_enabled"] = make_combo_setting(
        "启用评审代理",
        "📝 独立的章节质量评审 [通用模型]",
        ["true", "false"],
        current=str(s.memory_critic_agent_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_async"] = make_combo_setting(
        "异步运行",
        "在后台异步运行评审（不阻塞主流程）",
        ["true", "false"],
        current=str(s.memory_critic_agent_run_async).lower(),
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_timeout"] = make_float_setting(
        "评审软超时(秒)",
        "并发评审任务超时后返回可用结果；母题提取较慢，建议设为 90 秒以上",
        s.memory_critic_agent_timeout_s,
        5.0,
        300.0,
        decimals=1,
        step=5.0,
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_timeout_extend_attempts"] = make_spin_setting(
        "超时自动延长次数",
        "首轮超时后自动追加等待轮次（0=不延长，建议 1-2）",
        s.memory_critic_agent_timeout_extend_attempts,
        0,
        5,
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_timeout_extend_multiplier"] = make_float_setting(
        "延长倍率",
        "每次追加等待 = 上一轮超时 × 倍率（例如 1.5）",
        s.memory_critic_agent_timeout_extend_multiplier,
        1.0,
        3.0,
        decimals=2,
        step=0.1,
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_cache_enabled"] = make_combo_setting(
        "评审缓存",
        "缓存相同输入的评审结果；路由/模型变化会自动失效",
        ["true", "false"],
        current=str(s.memory_critic_agent_cache_enabled).lower(),
    )
    layout.addWidget(row)
    row, widgets["_memory_critic_cache_max"] = make_spin_setting(
        "缓存条目上限",
        "Critic 结果缓存最大条目数（建议 16-64）",
        s.memory_critic_agent_cache_max_entries,
        1,
        200,
    )
    layout.addWidget(row)

    row, widgets["_memory_concurrent_indexing"] = make_combo_setting(
        "并发索引",
        "章节归档后母题提取、摘要生成、片段索引是否并发执行；关闭则依次顺序执行",
        ["true", "false"],
        current=str(s.memory_concurrent_indexing).lower(),
    )
    compression_layout.addWidget(row)

    return sec, widgets
