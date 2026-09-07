"""Internal helpers shared by every reports/ render submodule.

The ``evaluation``, ``generic_report`` and ``humanize`` submodules each
previously duplicated the same set of label dictionaries and small
helpers. They have been consolidated here so each submodule imports
shared labels/helpers instead of redefining them.

Note: ``_safe_int`` differs by signature between ``evaluation`` /
``generic_report`` (returns ``int(bool) == 0 or 1``) and ``humanize``
(returns ``default`` for booleans, accepts an explicit ``default``
parameter). For that reason ``_safe_int`` is intentionally NOT in
this shared module — each submodule keeps its local variant.
"""

from __future__ import annotations

from typing import Any

__all__ = (
    "_BIBLE_FIELD_LABELS",
    "_CREATIVE_CHARACTER_IMPORTANCE_LABELS",
    "_EVAL_DIM_LABELS",
    "_GENRE_LABELS",
    "_GENERIC_KEY_LABELS",
    "_TONE_LABELS",
    "_creative_character_status",
    "_eval_dim_label",
    "_safe_float",
    "_truthy_report_flag",
    "generic_key_label",
)


_BIBLE_FIELD_LABELS: dict[str, str] = {
    "premise": "故事前提",
    "era": "时代背景",
    "geography": "地理场景",
    "culture": "社会文化",
    "magic_or_tech": "核心设定 / 魔法·技术",
    "tone": "叙事基调",
}

_GENRE_LABELS: dict[str, str] = {
    "romance": "言情",
    "fantasy": "奇幻",
    "mystery": "悬疑",
    "scifi": "科幻",
    "literary": "文学",
    "thriller": "惊悚",
    "humor": "幽默",
    "horror": "恐怖",
    "historical": "历史",
    "wuxia": "武侠",
    "xianxia": "仙侠",
}

_TONE_LABELS: dict[str, str] = {
    "warm": "温暖",
    "dark": "暗黑",
    "humorous": "幽默",
    "lyrical": "诗意",
    "gritty": "粗粝",
    "epic": "史诗",
    "intimate": "私密",
    "satirical": "讽刺",
}

_EVAL_DIM_LABELS: dict[str, str] = {
    "consistency": "设定一致",
    "continuity": "场景连贯",
    "character": "人物塑造",
    "style": "文笔风格",
    "engagement": "吸引力",
    "pacing": "节奏控制",
    "causal_chain": "因果链",
    "tension": "张力控制",
    # legacy / extended
    "coherence": "连贯性",
    "tech_density": "术语密度",
    "plot": "情节逻辑",
    "originality": "独创性",
    "theme": "主题表达",
    "world_building": "世界构建",
    "dialogue": "对话质量",
    "rewrite_compliance": "重写落地",
}

_CREATIVE_CHARACTER_IMPORTANCE_LABELS: dict[str, str] = {
    "major": "主要",
    "supporting": "配角",
    "minor": "次要",
    "incidental": "临场",
}


# Truncated marker — the full _GENERIC_KEY_LABELS dictionary is enormous
# (~235 entries) and lives below to keep top-of-file summary readable.
_GENERIC_KEY_LABELS: dict[str, str] = {
    "from_chapter": "衔接自章",
    "to_chapter": "过渡至章",
    "opening_time": "开场时间",
    "opening_location": "开场地点",
    "opening_pov": "开场视角",
    "transition_mode": "过渡方式",
    "emotional_carryover": "情感承接",
    "action_handoff": "行动交接",
    "pending_questions": "悬而未决的问题",
    "forbidden_repetition": "下一章避免重复",
    "bridge_summary": "桥接概述",
    "sensory_anchors": "感官锚点",
    "relationship_beat": "关系节拍",
    "current_trust_level": "信任度",
    "unspoken_tension": "潜在张力",
    "power_dynamic": "权力动态",
    "scene_intents": "场景编排",
    "opening_contract": "开篇契约",
    "closing_contract": "收束契约",
    "emotional_arc": "情感弧线",
    "required_state_transitions": "状态转换",
    "foreshadowing_plan": "伏笔计划",
    "key_revelations": "关键揭露",
    "scene_id": "场景 ID",
    "summary": "摘要",
    "purpose": "目的",
    "conflict": "冲突",
    "required_characters": "涉及角色",
    "character_motivations": "角色动机",
    "entry_state_refs": "进入状态",
    "required_outcome": "必要结果",
    "exit_target_state": "退出状态",
    "location": "地点",
    "time_marker": "时间标记",
    "character": "角色",
    "motivation": "动机",
    "stake": "风险",
    "alignment_score": "对齐分数",
    "risk_level": "风险级别",
    "missing_main_points": "缺失主线要点",
    "supportive_subplot_points": "支线正面贡献",
    "weak_subplot_points": "薄弱支线",
    "repair_actions": "修复建议",
    "continuity_score": "连贯性分数",
    "issues": "问题列表",
    "recommendations": "建议",
    "violations": "违规项",
    "analysis": "分析",
    "severity": "严重性",
    "description": "描述",
    "chapter_number": "章节编号",
    "chapter_outline": "章节大纲",
    "artifact": "产物",
    "artifact_manifest": "产物清单",
    "artifacts": "产物清单",
    "workflow": "工作流",
    "step": "步骤",
    "input_hashes": "输入指纹",
    "output_hashes": "输出指纹",
    "paths": "文件路径",
    "metadata": "元数据",
    "reusable_failure": "可复用失败",
    "repair_rounds": "修复轮次",
    "updated_at": "更新时间",
    "schema_version": "结构版本",
    "reveal_guard_version": "揭示边界版本",
    "reveal_guard_input_hashes": "揭示边界输入指纹",
    "outline_reveal_guard": "大纲揭示边界",
    "outline_output": "大纲输出指纹",
    "editorial_revelation_ladder": "编辑揭示阶梯",
    "narrative_contract": "叙事契约",
    "canon_context": "Canon 上下文",
    "previous_exit_state": "上章退出状态",
    "previous_chapter_ending": "上章结尾",
    "previous_creative_report": "上章创作报告",
    "character_profiles": "角色资料",
    "active_relationships": "活跃关系",
    "active_plot_threads": "活跃情节线",
    "must_carry_forward": "必须承接",
    "known_characters": "已知角色",
    "bridge": "桥接",
    "overall_score": "总评分",
    "scores": "各维度评分",
    "passed": "是否通过",
    "threshold": "阈值",
    "dimension": "维度",
    "score": "分数",
    "comment": "评语",
    "genre": "题材",
    "tone": "基调",
    "title": "标题",
    "name": "名称",
    "role": "角色定位",
    "status": "状态",
    "word_count": "字数",
    "guard_report": "护栏报告",
    "roster": "角色清单",
    "profiles": "角色资料",
    "relationship_edges": "人物关系边",
    "identity_links": "身份/指代链接",
    "audit": "审计记录",
    "entities": "实体",
    "entity_links": "实体链接",
    "entity_id": "实体 ID",
    "entity_type": "实体类型",
    "source_id": "源 ID",
    "target_id": "目标 ID",
    "source_name": "源名称",
    "target_name": "目标名称",
    "link_type": "链接类型",
    "time_layer": "时间层",
    "confidence": "置信度",
    "world_rules": "世界规则",
    "character_arcs": "角色弧光",
    "plot_threads": "情节线",
    "promise_plan": "承诺规划",
    "notes": "备注",
    "chapter_contracts": "章节契约",
    "entry_state_requirements": "入口状态要求",
    "required_events": "必要事件",
    "allowed_changes": "允许变化",
    "forbidden_changes": "禁止变化",
    "promise_ops": "承诺操作",
    "relationship_ops": "关系操作",
    "item_ops": "物件操作",
    "knowledge_ops": "认知操作",
    "cognitive_constraints": "认知揭示约束",
    "exit_state_targets": "出口状态目标",
    "required_progressions": "必须推进",
    "allowed_progressions": "允许推进",
    "forbidden_progressions": "禁止推进",
    "completion_criteria": "完成标准",
    "future_leak_risks": "未来泄露风险",
    "milestone_window": "里程碑窗口",
    "progression_ledger_tail": "推进账本尾部",
    "contract_execution": "契约执行",
    "missing_required_progressions": "缺失必达推进",
    "unexpected_progressions": "意外推进",
    "forbidden_progression_hits": "禁用推进命中",
    "future_leak_hits": "未来泄露命中",
    "cognitive_constraint_hits": "认知越界命中",
    "contract_completion_score": "契约完成分",
    "repair_or_replan_decision": "修复/重规划决定",
    "should_block_archive": "是否阻断归档",
    "expression_repetition": "表达通道重复",
    "records": "记录",
    "hits": "命中",
    "repair_guidance": "修复指引",
    "channel": "表达通道",
    "channel_id": "通道 ID",
    "cooldown_chapters": "冷却章数",
    "actor_scope": "角色范围",
    "examples": "例句",
    "arc_liveness": "角色弧光活跃度",
    "dormant_arcs": "沉睡弧光",
    "last_progress_chapter": "上次推进章节",
    "expected_next_touch": "预计下次触碰",
    "planning_hint": "规划提示",
    "stage_visibility": "阶段可见性",
    "stage_visibility_diagnostics": "阶段可见性诊断",
    "visible_current_milestones": "可见当前里程碑",
    "visible_future_guardrails": "可见未来护栏",
    "withheld_future_count": "隐藏未来节点数",
    "policy": "策略",
    "emotional_engine": "情感引擎",
    "thematic_promises": "主题承诺",
    "signature_motifs": "核心意象",
    "relationship_tensions": "关系张力",
    "anti_cliche_rules": "反套路清单",
    "scene_potential": "关键场面潜力",
    "synopsis": "故事概要",
    "volumes": "分卷",
    "narrative_phases": "叙事阶段",
    "key_turning_points": "关键转折",
    "subplot_plan": "支线规划",
    "suspense_schedule": "悬念规划",
    "ending_strategy": "收束策略",
    "assembly_mode": "组装模式",
    "last_chapter": "最近章节",
    "recent_summaries": "近期摘要",
    "accepted_updates": "已接受更新",
    "facts_by_path": "状态路径事实",
    "pending_items": "待定事项",
    "memory_index_path": "记忆索引路径",
    "allowed": "是否允许",
    "required": "是否必需",
    "block_min_severity": "阻断级别",
    "stages": "阶段结果",
    "repairs": "修复记录",
    "repair_effectiveness": "修复效果",
    "remaining_issues": "剩余问题",
    "claims_count": "一致性 Claims 总数",
    "extracted_claims_count": "抽取一致性 Claims",
    "active_claims_count": "活跃一致性 Claims",
    "candidate_count": "候选数",
    "degraded_memory": "记忆降级",
    "claim_ledger_path": "一致性 Claims 账本",
    "efficiency": "效率统计",
    "model_call_count_by_task": "模型调用",
    "stage_durations": "阶段耗时",
    "effective_batch_sizes": "有效批量",
    "verdict": "裁决",
    "blocked": "是否阻断",
    "issue_count": "问题数",
    "blocking_issue_count": "阻断问题",
    "findings": "发现项",
    "metrics": "指标",
    "critical_count": "严重问题",
    "high_count": "高风险问题",
    "finding_count": "发现数",
    "character_voice_count": "角色声纹",
    "climax_marker_count": "高潮标记",
    "theme_policy_count": "主题规则",
    "symbol_policy_count": "象征策略",
    "scene_resistance_rule_count": "场景阻力",
    "revelation_step_count": "揭示节点",
    "element_directive_count": "要素指令",
    "state_update": "状态更新",
    "evidence_quotes": "证据摘录",
    "candidate_id": "候选 ID",
    "delta_type": "状态类型",
    "scope": "范围",
    "state_path": "状态路径",
    "value": "状态值",
    "next_impact": "后续影响",
    "entity_ids": "实体 ID",
    "by_delta_type": "按类型归档",
    "entries": "条目",
    "project_id": "项目",
    "project_mode": "项目模式",
    "current_chapter": "当前章节",
    "active_volume": "当前卷",
    "created_at": "创建时间",
    "source_chapter": "来源章节",
    "relationships": "关系",
    "timeline": "时间线",
}


def _safe_int(value: Any) -> int:
    """Best-effort integer conversion for renderer counters."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def _safe_float(value: Any) -> float | None:
    """Best-effort conversion for renderer numeric fields."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _eval_dim_label(dim: Any) -> str:
    """Return a user-friendly eval dimension label."""
    raw = str(dim or "?")
    mapped = _EVAL_DIM_LABELS.get(raw)
    if mapped:
        return mapped
    if "_" in raw:
        return raw.replace("_", " ")
    return raw


def _truthy_report_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "需要", "建档"}


def _creative_character_status(character: dict[str, Any]) -> tuple[str, str]:
    matched_existing = str(character.get("matched_existing_name") or "").strip()
    if matched_existing:
        return "已有角色别名", "[[nf:text.muted]]"
    importance = str(character.get("importance") or "").strip().lower()
    if _truthy_report_flag(character.get("should_add_to_bible")) and importance in {
        "major",
        "supporting",
        "minor",
    }:
        return "建档候选", "[[nf:status.success.warm]]"
    return "仅报告", "[[nf:text.muted]]"


def generic_key_label(key: str) -> str:
    """Return the human-readable label for a generic report key."""
    return _GENERIC_KEY_LABELS.get(key, key)
