"""Focused report-style document renderers extracted from the UI facade."""

from __future__ import annotations

import ast
from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br
from novel_forge.desktop.pages.standalone.renderer_html import score_bar_html as _score_bar_html
from novel_forge.desktop.pages.standalone.renderer_html import score_color as _score_color

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

_TRANSITION_MODE_LABELS: dict[str, str] = {
    "synthetic": "合成过渡",
    "direct": "直接衔接",
    "direct_continue": "直接延续",
    "action_handoff": "行动交接",
    "pov_switch": "视角切换",
    "time_skip": "时间跳跃",
    "flashback": "闪回",
    "parallel": "平行叙事",
}

_CONFLICT_LEVEL_STYLE: dict[str, tuple[str, str]] = {
    "low": ("[[nf:status.success.warm]]", "低冲突"),
    "medium": ("[[nf:accent.primary]]", "中冲突"),
    "high": ("[[nf:status.danger.deep]]", "高冲突"),
}

_RISK_LEVEL_STYLE: dict[str, tuple[str, str]] = {
    "low": ("[[nf:status.success.warm]]", "低风险"),
    "medium": ("[[nf:accent.primary]]", "中风险"),
    "high": ("[[nf:status.danger.deep]]", "高风险"),
}

_EXPRESSION_CHANNEL_LABELS: dict[str, str] = {
    "sensory_anchor": "感官锚点",
    "somatic_reaction": "身体反应",
    "emotion_beat": "情绪节拍",
    "dialogue_pattern": "对白句式",
    "metaphor": "隐喻意象",
    "syntax": "句式结构",
}

_EXPRESSION_LEVEL_LABELS: dict[str, str] = {
    "hard": "硬禁",
    "soft": "软约束",
    "quota": "限额",
    "warning": "提示",
}

def generic_key_label(key: str) -> str:
    """Return the human-readable label for a generic report key."""
    return _GENERIC_KEY_LABELS.get(key, key)

def render_story_bible(data: dict[str, Any]) -> QTextBrowser:
    ...

def render_spec(data: dict[str, Any]) -> QTextBrowser:
    ...

def render_eval_report_body_html(data: dict[str, Any]) -> str:
    ...

def render_eval_report(data: dict[str, Any]) -> QTextBrowser:
    ...

_HUMANIZE_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

_HUMANIZE_SEVERITY_LABELS: dict[str, str] = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}

def _humanize_severity_tone(severity: Any) -> str:
    raw = str(severity or "").strip().lower()
    return "accent" if raw in {"critical", "high"} else "muted"

def _humanize_score_label(score: float | None) -> str:
    if score is None:
        return "未评分"
    if score >= 8.5:
        return "自然"
    if score >= 7.0:
        return "轻微痕迹"
    if score >= 5.5:
        return "需要关注"
    return "AI 痕迹较重"

def _humanize_bool_label(value: Any) -> str:
    return "可自动修复" if bool(value) else "仅报告"

def _humanize_metric(label: str, value: Any, *, color: str | None = None) -> str:
    style = f' style="color:{color};"' if color else ""
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{_esc(label)}</div>'
        f'<div class="metric-value"{style}>{_esc(str(value))}</div>'
        "</div>"
    )

def _humanize_category_distribution(hits_by_category: dict[str, Any]) -> str:
    rows: list[str] = []
    normalized: list[tuple[str, int]] = []
    for category, raw_count in hits_by_category.items():
        count = _safe_int(raw_count, 0)
        if count > 0:
            normalized.append((str(category), count))
    if not normalized:
        return '<div class="hint-block">暂无分类命中。</div>'

    total = max(1, sum(count for _category, count in normalized))
    for category, count in sorted(normalized, key=lambda item: (-item[1], item[0])):
        width = max(4, min(100, int(round(count / total * 100))))
        rows.append(
            '<div style="margin:8px 0;">'
            '<div style="display:flex; justify-content:space-between; gap:8px; '
            'font-size: 12pt; color:[[nf:text.body.alt]];">'
            f"<strong>{_esc(category)}</strong><span>{count}</span></div>"
            '<div class="score-bar-bg" style="height:7px; margin-top:4px;">'
            f'<div class="score-bar-fill" style="width:{width}%; background:[[nf:accent.primary]];"></div>'
            "</div></div>"
        )
    return '<div class="section" style="padding:10px 12px;">' + "".join(rows) + "</div>"

def render_humanize_report_body_html(data: dict[str, Any]) -> str:
    ...

def render_humanize_report(data: dict[str, Any]) -> QTextBrowser:
    ...

_GENERIC_META_KEYS: set[str] = {"schema_version", "created_at"}

def render_generic_report_body_html(data: dict[str, Any]) -> str:
    ...

def render_generic_report(data: dict[str, Any], title: str = "报告") -> QTextBrowser:
    ...

_INIT_STAGE_LABELS: dict[str, str] = {
    "blueprint_coherence": "蓝图一致性",
    "outline_inheritance": "大纲继承",
    "contract_coherence": "契约一致性",
    "claim_contract_coverage": "契约覆盖",
}

def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default

def _severity_tone(value: Any) -> str:
    raw = str(value or "").lower()
    return "accent" if raw in {"high", "critical", "blocked", "fail", "failed"} else "muted"

def render_init_readiness_report(data: dict[str, Any]) -> QTextBrowser:
    """Render reports/init_readiness.json as a compact launch gate summary."""
    parts: list[str] = []
    allowed = bool(data.get("allowed"))
    required = bool(data.get("required"))
    block_min = data.get("block_min_severity") or "high"
    summary = str(data.get("summary") or "").strip()
    remaining = (
        data.get("remaining_issues") if isinstance(data.get("remaining_issues"), list) else []
    )
    repairs = data.get("repairs") if isinstance(data.get("repairs"), list) else []

    badges = [
        _generic_tag("准入通过" if allowed else "准入阻断", tone="accent" if allowed else "muted"),
        _generic_tag("强制检查" if required else "非强制"),
        _generic_tag(f"阻断阈值 {block_min}", tone=_severity_tone(block_min)),
        _generic_tag(f"剩余问题 {len(remaining)}", tone="accent" if remaining else "muted"),
    ]
    parts.append(f'<div style="margin:2px 0 12px 0;">{"".join(badges)}</div>')
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')

    metric_rows = [
        ("一致性 Claims", data.get("claims_count", 0)),
        ("活跃一致性 Claims", data.get("active_claims_count", 0)),
        ("候选裁判", data.get("candidate_count", 0)),
        (
            "修复轮次",
            _safe_int((data.get("repair_effectiveness") or {}).get("repair_round_count"))
            if isinstance(data.get("repair_effectiveness"), dict)
            else 0,
        ),
    ]
    parts.append(
        '<div class="metric-grid">'
        + "".join(_generic_metric(label, value) for label, value in metric_rows)
        + "</div>"
    )

    stages = data.get("stages")
    if isinstance(stages, dict) and stages:
        parts.append("<h2>阶段门禁</h2>")
        for raw_key, stage in stages.items():
            if not isinstance(stage, dict):
                continue
            label = _INIT_STAGE_LABELS.get(str(raw_key), generic_key_label(str(raw_key)))
            verdict = stage.get("verdict", "unknown")
            blocked = bool(stage.get("blocked"))
            issue_count = _safe_int(stage.get("issue_count"))
            blocking_count = _safe_int(stage.get("blocking_issue_count"))
            stage_summary = str(stage.get("summary") or "").strip()
            stage_badges = [
                _generic_tag(
                    f"裁决 {verdict}", tone="accent" if str(verdict) == "accept" else "muted"
                ),
                _generic_tag(
                    "阻断" if blocked else "未阻断", tone="muted" if not blocked else "accent"
                ),
                _generic_tag(f"问题 {issue_count}", tone="accent" if issue_count else "muted"),
                _generic_tag(
                    f"阻断 {blocking_count}", tone="accent" if blocking_count else "muted"
                ),
                _generic_tag(
                    f"一致性 Claims {stage.get('active_claims_count', stage.get('claims_count', 0))}"
                ),
            ]
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(label)}</h3>"
                f'<div style="margin:2px 0 8px 0;">{"".join(stage_badges)}</div>'
                + (f'<div class="kv-value">{_nl2br(stage_summary)}</div>' if stage_summary else "")
                + "</div>"
            )

    if remaining:
        parts.append("<h2>剩余问题</h2>")
        parts.append(_generic_render_value(remaining, limit=12))

    if repairs:
        parts.append("<h2>修复记录</h2>")
        parts.append(_generic_render_value(repairs, limit=8))

    effectiveness = data.get("repair_effectiveness")
    if isinstance(effectiveness, dict) and effectiveness:
        parts.append("<h2>修复效果</h2>")
        parts.append(_generic_render_dict(effectiveness, compact=False))

    efficiency = data.get("efficiency")
    if isinstance(efficiency, dict) and efficiency:
        call_counts = efficiency.get("model_call_count_by_task")
        if isinstance(call_counts, dict) and call_counts:
            rows = sorted(call_counts.items(), key=lambda item: str(item[0]))[:18]
            parts.append("<h2>模型调用</h2>")
            parts.append(
                "<table><tbody>"
                + "".join(
                    f"<tr><th>{_esc(str(key))}</th><td>{_esc(str(value))}</td></tr>"
                    for key, value in rows
                )
                + "</tbody></table>"
            )
        compact_efficiency = {
            key: value for key, value in efficiency.items() if key != "model_call_count_by_task"
        }
        if compact_efficiency:
            parts.append("<h2>效率摘要</h2>")
            parts.append(_generic_render_dict(compact_efficiency, compact=False))

    return _make_browser(_html_wrap("\n".join(parts), "初始化准入"))

def render_init_editorial_readiness_report(data: dict[str, Any]) -> QTextBrowser:
    """Render reports/init_editorial_readiness.json without exposing raw counters."""
    status = str(data.get("status") or "unknown")
    summary = str(data.get("summary") or "").strip()
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []

    parts: list[str] = [
        '<div style="margin:2px 0 12px 0;">'
        + _generic_tag(f"状态 {status}", tone="accent" if status in {"ok", "passed"} else "muted")
        + _generic_tag(f"发现 {len(findings)}", tone="accent" if findings else "muted")
        + _generic_tag(f"高风险 {metrics.get('high_count', 0)}")
        + "</div>"
    ]
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')

    count_keys = [
        ("角色声纹", "character_voice_count"),
        ("高潮标记", "climax_marker_count"),
        ("主题规则", "theme_policy_count"),
        ("象征策略", "symbol_policy_count"),
        ("场景阻力", "scene_resistance_rule_count"),
        ("揭示节点", "revelation_step_count"),
        ("要素指令", "element_directive_count"),
    ]
    parts.append(
        '<div class="metric-grid">'
        + "".join(_generic_metric(label, data.get(key, 0)) for label, key in count_keys)
        + "</div>"
    )

    if findings:
        parts.append("<h2>风险发现</h2>")
        for index, finding in enumerate(findings[:12], 1):
            if not isinstance(finding, dict):
                parts.append(f'<div class="rule-item">{_nl2br(str(finding))}</div>')
                continue
            severity = finding.get("severity", "unknown")
            title = finding.get("summary") or finding.get("issue_type") or f"发现 {index}"
            recommendation = str(finding.get("recommendation") or "").strip()
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(str(title))}</h3>"
                f'<div style="margin:2px 0 8px 0;">{_generic_tag(f"级别 {severity}", tone=_severity_tone(severity))}</div>'
                + (
                    f'<div class="kv-row"><span class="kv-label">建议</span> {_nl2br(recommendation)}</div>'
                    if recommendation
                    else ""
                )
                + "</div>"
            )
        if len(findings) > 12:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(findings) - 12} 条发现未展开。"
                "</div>"
            )

    return _make_browser(_html_wrap("\n".join(parts), "编辑契约准入"))

def render_chapter_contracts(data: dict[str, Any]) -> QTextBrowser:
    """Render plans/chapter_contracts.json as per-chapter execution cards."""
    contracts = data.get("chapter_contracts")
    if not isinstance(contracts, list):
        contracts = []

    parts: list[str] = [
        '<div style="margin:2px 0 12px 0;">'
        + _generic_tag(f"章节 {len(contracts)}", tone="accent" if contracts else "muted")
        + "</div>"
    ]
    if not contracts:
        parts.append('<div class="hint-block">暂无章节契约数据。</div>')
        return _make_browser(_html_wrap("\n".join(parts), "章节契约"))

    for index, raw in enumerate(contracts, 1):
        if not isinstance(raw, dict):
            continue
        number = raw.get("chapter_number", index)
        title = str(raw.get("title") or "未命名").strip()
        source = str(raw.get("source") or "").strip()
        header = f"第 {number} 章"
        if title:
            header += f" · {title}"
        badges = [
            _generic_tag(source or "契约", tone="muted"),
            _generic_tag(f"必达 {len(raw.get('required_events') or [])}"),
            _generic_tag(
                f"禁止 {len(raw.get('forbidden_changes') or []) + len(raw.get('forbidden_progressions') or [])}"
            ),
        ]
        parts.append('<div class="section">')
        parts.append(f"<h2>{_esc(header)}</h2>")
        parts.append(f'<div style="margin:2px 0 8px 0;">{"".join(badges)}</div>')
        for key, label in (
            ("entry_state_requirements", "入口状态"),
            ("required_events", "必要事件"),
            ("exit_state_targets", "出口目标"),
            ("required_progressions", "必达推进"),
            ("completion_criteria", "完成标准"),
            ("forbidden_changes", "禁止变化"),
            ("forbidden_progressions", "禁止推进"),
            ("future_leak_risks", "未来泄露风险"),
        ):
            value = raw.get(key)
            if isinstance(value, list) and value:
                parts.append(f"<h3>{_esc(label)}</h3>{_generic_list_items(value, limit=6)}")

        op_rows: list[str] = []
        for key, label in (
            ("relationship_ops", "关系操作"),
            ("item_ops", "物件操作"),
            ("knowledge_ops", "认知操作"),
            ("promise_ops", "承诺操作"),
        ):
            value = raw.get(key)
            if isinstance(value, list) and value:
                op_rows.append(f"<h3>{_esc(label)}</h3>{_generic_render_value(value, limit=4)}")
        if op_rows:
            parts.append("".join(op_rows))
        parts.append("</div>")

    return _make_browser(_html_wrap("\n".join(parts), "章节契约"))

def render_story_state_projection(data: dict[str, Any]) -> QTextBrowser:
    """Render narrative_state/story_state_projection.json as a state ledger summary."""
    accepted = (
        data.get("accepted_updates") if isinstance(data.get("accepted_updates"), list) else []
    )
    facts = data.get("facts_by_path") if isinstance(data.get("facts_by_path"), dict) else {}
    by_type = data.get("by_delta_type") if isinstance(data.get("by_delta_type"), dict) else {}
    pending = data.get("pending_items") if isinstance(data.get("pending_items"), list) else []
    last_chapter = data.get("last_chapter") or data.get("current_chapter") or "—"

    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("最近章节", last_chapter)
        + _generic_metric("已接受更新", len(accepted))
        + _generic_metric("状态事实", len(facts))
        + _generic_metric("待定事项", len(pending))
        + "</div>"
    ]

    if by_type:
        tags = []
        for key, value in sorted(by_type.items(), key=lambda item: str(item[0])):
            count = len(value) if isinstance(value, list) else _safe_int(value)
            tags.append(_generic_tag(f"{generic_key_label(str(key))} {count}"))
        parts.append(f'<div style="margin:2px 0 12px 0;">{"".join(tags)}</div>')

    if accepted:
        parts.append("<h2>最近状态更新</h2>")
        for index, item in enumerate(accepted[:16], 1):
            if not isinstance(item, dict):
                continue
            update = item.get("state_update") if isinstance(item.get("state_update"), dict) else {}
            title = item.get("summary") or update.get("summary") or f"更新 {index}"
            chapter = item.get("chapter_number")
            delta_type = item.get("delta_type")
            path = update.get("state_path")
            next_impact = update.get("next_impact")
            evidence = item.get("evidence_quotes")
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(str(title))}</h3>"
                '<div style="margin:2px 0 8px 0;">'
                + (_generic_tag(f"第 {chapter} 章") if chapter else "")
                + (_generic_tag(delta_type) if delta_type else "")
                + (_generic_tag(path) if path else "")
                + "</div>"
                + (
                    f'<div class="kv-row"><span class="kv-label">状态</span> {_nl2br(str(update.get("value") or ""))}</div>'
                    if update.get("value")
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">后续影响</span> {_nl2br(str(next_impact))}</div>'
                    if next_impact
                    else ""
                )
                + (
                    _generic_list_items(evidence, limit=2)
                    if isinstance(evidence, list) and evidence
                    else ""
                )
                + "</div>"
            )
        if len(accepted) > 16:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(accepted) - 16} 条状态更新未展开。"
                "</div>"
            )

    if facts:
        rows = []
        for path, value in list(facts.items())[:30]:
            rows.append(f"<tr><th>{_esc(str(path))}</th><td>{_nl2br(str(value))}</td></tr>")
        parts.append("<h2>状态事实</h2><table><tbody>" + "".join(rows) + "</tbody></table>")
        if len(facts) > 30:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(facts) - 30} 条事实未展开。"
                "</div>"
            )

    if pending:
        parts.append("<h2>待定事项</h2>")
        parts.append(_generic_render_value(pending, limit=10))

    return _make_browser(_html_wrap("\n".join(parts), "叙事状态投影"))

def _state_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

def _state_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _state_dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def _state_tag(text: Any, *, positive: bool | None = None) -> str:
    tone = "accent" if positive else "muted"
    return _generic_tag(text, tone=tone)

def _state_verdict_label(value: Any) -> str:
    raw = str(value or "unknown").strip()
    return {
        "accept": "通过",
        "reject": "拒绝",
        "defer": "待定",
        "pending": "待定",
        "needs_repair": "需修复",
        "repair": "需修复",
    }.get(raw, raw or "unknown")

def _state_severity_label(value: Any) -> str:
    raw = str(value or "unknown").strip()
    return {"low": "低", "medium": "中", "high": "高", "critical": "严重"}.get(
        raw, raw or "unknown"
    )

def _state_confidence(value: Any) -> str:
    score = _safe_float(value)
    if score is None:
        return "—"
    if 0 <= score <= 1:
        return f"{score * 100:.0f}%"
    return f"{score:.2f}"

def _state_count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        raw = str(item.get(key) or "unknown").strip() or "unknown"
        counts[raw] = counts.get(raw, 0) + 1
    return counts

def _state_update_cards(updates: list[Any], *, limit: int = 10) -> str:
    cards: list[str] = []
    for index, raw in enumerate(updates[:limit], 1):
        if not isinstance(raw, dict):
            continue
        title = raw.get("summary") or raw.get("state_path") or f"状态更新 {index}"
        path = raw.get("state_path")
        scope = raw.get("scope")
        value = raw.get("value")
        characters = raw.get("present_characters")
        cards.append(
            '<div class="mini-card">'
            f"<h3>{_esc(str(title))}</h3>"
            '<div style="margin:2px 0 8px 0;">'
            + (_state_tag(scope) if scope else "")
            + (_state_tag(path) if path else "")
            + "</div>"
            + (
                f'<div class="kv-row"><span class="kv-label">状态值</span> '
                f"{_nl2br(str(value))}</div>"
                if value
                else ""
            )
            + (
                f'<div class="kv-row"><span class="kv-label">出场人物</span> '
                f"{_nl2br('、'.join(str(name) for name in characters))}</div>"
                if isinstance(characters, list) and characters
                else ""
            )
            + "</div>"
        )
    if len(updates) > limit:
        cards.append(
            '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
            f"另有 {len(updates) - limit} 条状态更新未展开。"
            "</div>"
        )
    return "\n".join(cards)

def _state_evidence_cards(records: list[Any], *, limit: int = 8) -> str:
    cards: list[str] = []
    for index, raw in enumerate(records[:limit], 1):
        if not isinstance(raw, dict):
            continue
        title = raw.get("quote") or raw.get("candidate_id") or f"证据 {index}"
        chapter = raw.get("chapter_number")
        paragraph = raw.get("paragraph_index")
        found = raw.get("found")
        context = str(raw.get("context") or "").strip()
        cards.append(
            '<div class="mini-card">'
            f"<h3>{_nl2br(str(title))}</h3>"
            '<div style="margin:2px 0 8px 0;">'
            + (_state_tag(f"第 {chapter} 章") if chapter else "")
            + (_state_tag(f"段落 {paragraph}") if paragraph not in (None, "") else "")
            + (_state_tag("已命中", positive=True) if found is True else _state_tag("未命中"))
            + "</div>"
            + (
                f'<div class="kv-row"><span class="kv-label">上下文</span> '
                f"{_nl2br(context[:520])}</div>"
                if context
                else ""
            )
            + "</div>"
        )
    if len(records) > limit:
        cards.append(
            '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
            f"另有 {len(records) - limit} 条证据未展开。"
            "</div>"
        )
    return "\n".join(cards)

def render_state_adjudication_report(data: dict[str, Any]) -> QTextBrowser:
    """Render chapter state adjudication reports as a readable decision brief."""
    final = _state_dict(data.get("final_adjudication"))
    candidates = _state_dict_items(data.get("candidates"))
    decisions = _state_dict_items(data.get("decisions"))
    omitted = _state_list(data.get("omitted_candidates"))
    coverage = _state_dict(data.get("contract_coverage"))
    ledger_entries = _state_list(data.get("ledger_entries"))
    updates = _state_list(final.get("state_updates"))
    pending = _state_list(final.get("pending_items"))
    repairs = _state_list(final.get("repair_issues"))
    verdict = final.get("verdict") or data.get("final_verdict")
    severity = final.get("severity") or data.get("final_severity")
    confidence = final.get("confidence") or data.get("final_confidence")
    should_block = bool(final.get("should_block_archive") or data.get("should_block_archive"))

    accepted_ids = _state_list(final.get("accepted_candidate_ids"))
    rejected_ids = _state_list(final.get("rejected_candidate_ids"))
    repair_ids = _state_list(final.get("repair_candidate_ids"))
    pending_ids = _state_list(final.get("pending_candidate_ids"))

    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("章节", data.get("chapter_number", "—"))
        + _generic_metric("候选", len(candidates))
        + _generic_metric("裁决", len(decisions))
        + _generic_metric("通过", len(accepted_ids))
        + _generic_metric("拒绝", len(rejected_ids))
        + _generic_metric("待定", len(pending_ids) + len(pending))
        + _generic_metric("需修复", len(repair_ids) + len(repairs))
        + _generic_metric("置信度", _state_confidence(confidence))
        + _generic_metric("归档阻断", "是" if should_block else "否")
        + "</div>"
    ]

    parts.append(
        '<div style="margin:2px 0 12px 0;">'
        + _state_tag(f"裁决 {_state_verdict_label(verdict)}", positive=not should_block)
        + _state_tag(f"级别 {_state_severity_label(severity)}")
        + (_state_tag(f"遗漏候选 {len(omitted)}") if omitted else "")
        + "</div>"
    )

    summary = str(final.get("summary") or "").strip()
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')

    if updates:
        parts.append("<h2>写入状态</h2>")
        parts.append(_state_update_cards(updates, limit=12))

    if decisions:
        parts.append("<h2>候选裁决</h2>")
        for index, decision in enumerate(decisions[:12], 1):
            cid = decision.get("candidate_id") or f"候选 {index}"
            verdict_label = _state_verdict_label(decision.get("verdict"))
            decision_severity = _state_severity_label(decision.get("severity"))
            rationale = str(decision.get("rationale") or "").strip()
            quotes = _state_list(decision.get("evidence_quotes"))
            affected = _state_list(decision.get("affected_state_paths"))
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(str(cid))}</h3>"
                '<div style="margin:2px 0 8px 0;">'
                + _state_tag(verdict_label, positive=decision.get("verdict") == "accept")
                + _state_tag(f"级别 {decision_severity}")
                + _state_tag(f"置信度 {_state_confidence(decision.get('confidence'))}")
                + "</div>"
                + (
                    f'<div class="kv-row"><span class="kv-label">理由</span> '
                    f"{_nl2br(rationale[:520])}</div>"
                    if rationale
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">影响路径</span> '
                    f"{_generic_list_items(affected, limit=4)}</div>"
                    if affected
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">证据摘录</span> '
                    f"{_generic_list_items(quotes, limit=3)}</div>"
                    if quotes
                    else ""
                )
                + "</div>"
            )
        if len(decisions) > 12:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(decisions) - 12} 条裁决未展开。"
                "</div>"
            )

    if candidates:
        counts = _state_count_by(candidates, "delta_type")
        tags = "".join(
            _state_tag(f"{generic_key_label(key)} {count}") for key, count in counts.items()
        )
        parts.append(f"<h2>候选类型</h2><div>{tags}</div>")

    if coverage:
        total = coverage.get(
            "total_required_targets", coverage.get("total_required_target_count", "—")
        )
        covered = coverage.get("covered_count", coverage.get("contract_covered_count", 0))
        uncovered = _state_list(coverage.get("uncovered_targets"))
        unaccepted = _state_list(coverage.get("unaccepted_targets"))
        parts.append(
            "<h2>契约覆盖</h2>"
            '<div class="metric-grid">'
            + _generic_metric("必达目标", total)
            + _generic_metric("已覆盖", covered)
            + _generic_metric("未覆盖", len(uncovered))
            + _generic_metric("未接受", len(unaccepted))
            + _generic_metric("全部覆盖", "是" if coverage.get("all_required_covered") else "否")
            + "</div>"
        )
        if uncovered:
            parts.append("<h3>未覆盖目标</h3>")
            parts.append(_generic_render_value(uncovered, limit=8))
        if unaccepted:
            parts.append("<h3>未接受目标</h3>")
            parts.append(_generic_render_value(unaccepted, limit=8))

    if pending:
        parts.append("<h2>待定事项</h2>")
        parts.append(_generic_render_value(pending, limit=8))

    if repairs:
        parts.append("<h2>修复事项</h2>")
        parts.append(_generic_render_value(repairs, limit=8))

    if ledger_entries:
        parts.append("<h2>账本写入</h2>")
        parts.append(_generic_render_value(ledger_entries, limit=8))

    return _make_browser(_html_wrap("\n".join(parts), "状态裁判报告"))

def render_state_adjudication_index(data: dict[str, Any]) -> QTextBrowser:
    """Render narrative_state/adjudication_report_index.json as a chapter table."""
    reports = _state_dict_items(data.get("reports"))
    totals = {
        "candidate_count": sum(_safe_int(item.get("candidate_count")) for item in reports),
        "decision_count": sum(_safe_int(item.get("decision_count")) for item in reports),
        "accepted_count": sum(_safe_int(item.get("accepted_count")) for item in reports),
        "repair_count": sum(_safe_int(item.get("repair_count")) for item in reports),
        "pending_count": sum(_safe_int(item.get("pending_count")) for item in reports),
        "blocked_count": sum(1 for item in reports if item.get("should_block_archive")),
    }
    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("报告章节", len(reports))
        + _generic_metric("候选", totals["candidate_count"])
        + _generic_metric("裁决", totals["decision_count"])
        + _generic_metric("通过", totals["accepted_count"])
        + _generic_metric("待定", totals["pending_count"])
        + _generic_metric("需修复", totals["repair_count"])
        + _generic_metric("阻断", totals["blocked_count"])
        + "</div>"
    ]
    if not reports:
        parts.append('<div class="hint-block">暂无状态裁判索引。</div>')
        return _make_browser(_html_wrap("\n".join(parts), "状态裁判索引"))

    rows: list[str] = []
    for item in reports:
        rows.append(
            "<tr>"
            f"<th>第 {_esc(str(item.get('chapter_number') or '—'))} 章</th>"
            f"<td>{_esc(_state_verdict_label(item.get('final_verdict')))}</td>"
            f"<td>{_esc(_state_severity_label(item.get('final_severity')))}</td>"
            f"<td>{_esc(_state_confidence(item.get('final_confidence')))}</td>"
            f"<td>{_esc(str(item.get('accepted_count', 0)))}</td>"
            f"<td>{_esc(str(item.get('pending_count', 0)))}</td>"
            f"<td>{_esc(str(item.get('repair_count', 0)))}</td>"
            f"<td>{_esc('是' if item.get('should_block_archive') else '否')}</td>"
            f"<td>{_esc(str(item.get('contract_covered_count', 0)))} / "
            f"{_esc(str(item.get('contract_total_required_targets', '—')))}</td>"
            "</tr>"
        )
    parts.append(
        "<h2>章节裁判索引</h2>"
        "<table><tr><th>章节</th><th>裁决</th><th>级别</th><th>置信度</th>"
        "<th>通过</th><th>待定</th><th>修复</th><th>阻断</th><th>契约覆盖</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return _make_browser(_html_wrap("\n".join(parts), "状态裁判索引"))

def render_state_evidence_snapshot(data: dict[str, Any]) -> QTextBrowser:
    """Render chapter evidence snapshots with quote-first cards."""
    coverage = _state_dict(data.get("contract_coverage"))
    evidence_groups = _state_dict_items(data.get("evidence"))
    evidence_records: list[dict[str, Any]] = []
    for group in evidence_groups:
        for record in _state_dict_items(group.get("evidence")):
            merged = dict(record)
            merged.setdefault("candidate_id", group.get("candidate_id"))
            merged.setdefault("delta_type", group.get("delta_type"))
            evidence_records.append(merged)

    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("章节", data.get("chapter_number", "—"))
        + _generic_metric("候选组", len(evidence_groups))
        + _generic_metric("证据条目", len(evidence_records))
        + _generic_metric("命中证据", sum(1 for item in evidence_records if item.get("found")))
        + _generic_metric("契约目标", coverage.get("total_required_targets", "—"))
        + _generic_metric("已覆盖", coverage.get("covered_count", 0))
        + "</div>"
    ]

    if evidence_groups:
        parts.append("<h2>候选证据</h2>")
        for group in evidence_groups[:10]:
            group_records = _state_dict_items(group.get("evidence"))
            parts.append(
                '<div class="section">'
                f"<h2>{_esc(str(group.get('candidate_id') or '证据组'))}</h2>"
                '<div style="margin:2px 0 8px 0;">'
                + (_state_tag(group.get("delta_type")) if group.get("delta_type") else "")
                + (_state_tag("已裁判", positive=True) if group.get("adjudicated") else "")
                + _state_tag(f"证据 {len(group_records)}")
                + "</div>"
                + (
                    f'<div class="kv-row"><span class="kv-label">摘要</span> '
                    f"{_nl2br(str(group.get('summary') or ''))}</div>"
                    if group.get("summary")
                    else ""
                )
                + _state_evidence_cards(group_records, limit=3)
                + "</div>"
            )
        if len(evidence_groups) > 10:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(evidence_groups) - 10} 个证据组未展开。"
                "</div>"
            )

    if coverage:
        uncovered = _state_list(coverage.get("uncovered_targets"))
        if uncovered:
            parts.append("<h2>未覆盖契约目标</h2>")
            parts.append(_generic_render_value(uncovered, limit=10))

    return _make_browser(_html_wrap("\n".join(parts), "章节证据快照"))

def render_state_pending_queue(data: dict[str, Any]) -> QTextBrowser:
    """Render narrative_state/pending_queue.json."""
    items = _state_dict_items(data.get("pending_items"))
    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("待定项", len(items))
        + _generic_metric("涉及章节", len({item.get("chapter_number") for item in items}))
        + _generic_metric("中风险", sum(1 for item in items if item.get("severity") == "medium"))
        + _generic_metric("高风险", sum(1 for item in items if item.get("severity") == "high"))
        + "</div>"
    ]
    if not items:
        parts.append('<div class="hint-block">当前没有待定叙事状态项。</div>')
    else:
        for index, item in enumerate(items[:20], 1):
            title = item.get("summary") or item.get("pending_id") or f"待定项 {index}"
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(str(title))}</h3>"
                '<div style="margin:2px 0 8px 0;">'
                + (
                    _state_tag(f"第 {item.get('chapter_number')} 章")
                    if item.get("chapter_number")
                    else ""
                )
                + (_state_tag(item.get("delta_type")) if item.get("delta_type") else "")
                + (
                    _state_tag(f"级别 {_state_severity_label(item.get('severity'))}")
                    if item.get("severity")
                    else ""
                )
                + (_state_tag(item.get("verdict")) if item.get("verdict") else "")
                + "</div>"
                + (
                    f'<div class="kv-row"><span class="kv-label">原因</span> '
                    f"{_nl2br(str(item.get('pending_reason') or ''))}</div>"
                    if item.get("pending_reason")
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">修复建议</span> '
                    f"{_nl2br(str(item.get('repair_instruction') or ''))}</div>"
                    if item.get("repair_instruction")
                    else ""
                )
                + "</div>"
            )
        if len(items) > 20:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(items) - 20} 条待定项未展开。"
                "</div>"
            )
    return _make_browser(_html_wrap("\n".join(parts), "待定叙事队列"))

def render_state_ledger(records: list[dict[str, Any]]) -> QTextBrowser:
    """Render narrative_state/state_ledger.jsonl as accepted state updates."""
    by_type = _state_count_by(records, "delta_type")
    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("账本条目", len(records))
        + _generic_metric("涉及章节", len({item.get("chapter_number") for item in records}))
        + _generic_metric(
            "已接受",
            sum(
                1
                for item in records
                if _state_dict(item.get("decision")).get("verdict") == "accept"
            ),
        )
        + _generic_metric(
            "证据条目", sum(len(_state_list(item.get("evidence"))) for item in records)
        )
        + "</div>"
    ]
    if by_type:
        parts.append(
            '<div style="margin:2px 0 12px 0;">'
            + "".join(
                _state_tag(f"{generic_key_label(key)} {count}") for key, count in by_type.items()
            )
            + "</div>"
        )
    if not records:
        parts.append('<div class="hint-block">叙事状态账本暂无写入。</div>')
    else:
        for index, item in enumerate(records[:24], 1):
            update = _state_dict(item.get("state_update"))
            decision = _state_dict(item.get("decision"))
            title = (
                item.get("summary")
                or update.get("summary")
                or item.get("entry_id")
                or f"账本 {index}"
            )
            parts.append(
                '<div class="mini-card">'
                f"<h3>{_esc(str(title))}</h3>"
                '<div style="margin:2px 0 8px 0;">'
                + (
                    _state_tag(f"第 {item.get('chapter_number')} 章")
                    if item.get("chapter_number")
                    else ""
                )
                + (_state_tag(item.get("delta_type")) if item.get("delta_type") else "")
                + (_state_tag(update.get("state_path")) if update.get("state_path") else "")
                + (
                    _state_tag(_state_verdict_label(decision.get("verdict")), positive=True)
                    if decision
                    else ""
                )
                + "</div>"
                + (
                    f'<div class="kv-row"><span class="kv-label">状态值</span> '
                    f"{_nl2br(str(update.get('value') or ''))}</div>"
                    if update.get("value")
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">裁判理由</span> '
                    f"{_nl2br(str(decision.get('rationale') or '')[:420])}</div>"
                    if decision.get("rationale")
                    else ""
                )
                + _state_evidence_cards(_state_list(item.get("evidence")), limit=2)
                + "</div>"
            )
        if len(records) > 24:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(records) - 24} 条账本记录未展开。"
                "</div>"
            )
    return _make_browser(_html_wrap("\n".join(parts), "叙事状态账本"))

def render_canon_state(data: dict[str, Any]) -> QTextBrowser:
    """Render canon/canon_current.json without dumping the whole kernel JSON."""
    world_rules = data.get("world_rules") if isinstance(data.get("world_rules"), list) else []
    entities = data.get("entities") if isinstance(data.get("entities"), list) else []
    relationships = data.get("relationships") if isinstance(data.get("relationships"), list) else []
    timeline = data.get("timeline") if isinstance(data.get("timeline"), list) else []
    plot_threads = data.get("plot_threads") if isinstance(data.get("plot_threads"), list) else []

    parts: list[str] = [
        '<div class="metric-grid">'
        + _generic_metric("当前章节", data.get("current_chapter", "—"))
        + _generic_metric("当前卷", data.get("active_volume", "—"))
        + _generic_metric("世界规则", len(world_rules))
        + _generic_metric("实体", len(entities))
        + _generic_metric("关系", len(relationships))
        + _generic_metric("时间线", len(timeline))
        + "</div>"
    ]

    project_id = str(data.get("project_id") or "").strip()
    if project_id:
        parts.append(f'<div class="hint-block">项目：{_esc(project_id)}</div>')

    if world_rules:
        parts.append("<h2>世界规则</h2>")
        for rule in world_rules[:10]:
            if isinstance(rule, dict):
                label = rule.get("rule_id") or rule.get("title") or "规则"
                severity = rule.get("severity")
                parts.append(
                    '<div class="rule-item">'
                    f"<strong>{_esc(str(label))}</strong> "
                    + (_generic_tag(severity) if severity else "")
                    + f"<div>{_nl2br(str(rule.get('content') or ''))}</div>"
                    "</div>"
                )
            else:
                parts.append(f'<div class="rule-item">{_nl2br(str(rule))}</div>')
        if len(world_rules) > 10:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(world_rules) - 10} 条世界规则未展开。"
                "</div>"
            )

    if entities:
        rows: list[str] = []
        for entity in entities[:24]:
            if not isinstance(entity, dict):
                continue
            attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
            notes = attrs.get("notes") or entity.get("notes") or ""
            rows.append(
                "<tr>"
                f"<th>{_esc(str(entity.get('name') or entity.get('entity_id') or '实体'))}</th>"
                f"<td>{_esc(str(entity.get('entity_type') or ''))}</td>"
                f"<td>{_esc(str(entity.get('status') or ''))}</td>"
                f"<td>{_nl2br(str(notes))}</td>"
                "</tr>"
            )
        parts.append(
            "<h2>实体清单</h2><table><tr><th>名称</th><th>类型</th><th>状态</th><th>备注</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    if relationships:
        rows = []
        for rel in relationships[:16]:
            if not isinstance(rel, dict):
                continue
            pair = " → ".join(
                part
                for part in (
                    str(rel.get("source_entity_id") or rel.get("source_name") or "").strip(),
                    str(rel.get("target_entity_id") or rel.get("target_name") or "").strip(),
                )
                if part
            )
            rows.append(
                "<tr>"
                f"<th>{_esc(pair or str(rel.get('relationship_id') or '关系'))}</th>"
                f"<td>{_esc(str(rel.get('relation_type') or ''))}</td>"
                f"<td>{_nl2br(str(rel.get('label') or rel.get('description') or ''))}</td>"
                "</tr>"
            )
        parts.append(
            "<h2>关系网络</h2><table><tr><th>关系</th><th>类型</th><th>说明</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    if plot_threads:
        parts.append("<h2>情节线</h2>")
        parts.append(_generic_render_value(plot_threads, limit=10))

    return _make_browser(_html_wrap("\n".join(parts), "规范状态"))

def _report_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default

def _report_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []

def _tag(text: Any, *, tone: str = "muted") -> str:
    cls = "tag-muted tag" if tone == "muted" else "tag"
    return f'<span class="{cls}">{_esc(str(text))}</span>'

def _compact_items(items: list[Any], *, limit: int = 12) -> str:
    visible = items[:limit]
    rows = []
    for item in visible:
        if isinstance(item, dict):
            text = item.get("summary") or item.get("reason") or item.get("description") or str(item)
        else:
            text = str(item)
        if str(text).strip():
            rows.append(f'<div class="rule-item">{_nl2br(str(text))}</div>')
    if len(items) > limit:
        rows.append(
            f'<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">另有 {len(items) - limit} 项未展开。</div>'
        )
    return "\n".join(rows) or '<span class="kv-value" style="color:[[nf:text.muted]];">（空）</span>'

def _parse_stringified_dict(value: Any) -> dict[str, Any] | None:
    """Parse legacy stringified dict payloads without exposing raw Python reprs."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None

def _expression_channel_label(record: dict[str, Any], *, fallback: str = "表达通道") -> str:
    channel = _report_text(record.get("channel"))
    channel_id = _report_text(record.get("channel_id"))
    label = _EXPRESSION_CHANNEL_LABELS.get(channel, channel.replace("_", " ") if channel else "")
    if not label:
        label = fallback
    return f"{label} · {channel_id}" if channel_id else label

def _expression_level_tag(level: Any) -> str:
    raw = _report_text(level, "warning").lower()
    label = _EXPRESSION_LEVEL_LABELS.get(raw, raw)
    style_by_level = {
        "hard": ("[[nf:status.danger.deep]]", "rgba([[nf:status.danger.deep]], 0.09)", "rgba([[nf:status.danger.deep]], 0.18)"),
        "soft": ("[[nf:text.blueprint.badge]]", "rgba([[nf:status.warning.alt]], 0.10)", "rgba([[nf:status.warning.alt]], 0.18)"),
        "quota": ("[[nf:accent.primary]]", "rgba([[nf:accent.primary]], 0.10)", "rgba([[nf:accent.primary]], 0.18)"),
    }
    color, bg, border = style_by_level.get(
        raw,
        ("[[nf:text.muted]]", "rgba([[nf:text.muted]], 0.08)", "rgba([[nf:text.muted]], 0.16)"),
    )
    return (
        '<span class="tag-muted tag" '
        f'style="color:{color}; background:{bg}; border-color:{border};">'
        f"{_esc(label)}</span>"
    )

def _expression_record_meta(record: dict[str, Any]) -> str:
    rows: list[str] = []
    for key, label in (
        ("source", "来源"),
        ("reason", "原因"),
        ("cooldown_chapters", "冷却章数"),
        ("actor_scope", "作用范围"),
    ):
        value = record.get(key)
        if value in (None, "", []):
            continue
        rows.append(
            f'<div class="kv-row"><span class="kv-label">{label}</span> {_esc(str(value))}</div>'
        )
    return "\n".join(rows)

def _expression_guidance_payload(item: Any) -> dict[str, Any]:
    parsed = _parse_stringified_dict(item)
    if parsed is not None:
        return parsed
    if isinstance(item, dict):
        return item
    return {"action": str(item)}

_STAGE_VISIBILITY_ORDER = ("bridge", "plan", "draft", "judge")

_STAGE_VISIBILITY_LABELS: dict[str, str] = {
    "bridge": "桥接",
    "plan": "规划",
    "draft": "起草",
    "judge": "审查",
}

_STAGE_VISIBILITY_HELP: dict[str, str] = {
    "bridge": "承接上一章与当前章节入口",
    "plan": "生成章节计划与执行边界",
    "draft": "进入正文写作，压低未来信息",
    "judge": "评估、修复与归档前判断",
}

_STAGE_VISIBILITY_POLICIES: dict[str, str] = {
    "Draft only receives current execution cards; future guardrails are withheld.": (
        "草稿阶段只接收当前执行卡，未来护栏保持隐藏。"
    ),
    "Stage receives bounded milestone window, not full outline.": (
        "该阶段只接收有界里程碑窗口，而不是完整大纲。"
    ),
}

def _stage_visibility_count(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    number = _safe_float(value)
    if number is None:
        return 0
    return max(0, int(number))

def _stage_visibility_entries(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    diagnostics = data.get("stage_visibility_diagnostics")
    if isinstance(diagnostics, dict):
        payload = diagnostics
    else:
        payload = data.get("stage_visibility")
    if not isinstance(payload, dict):
        return []
    if "stage" in payload and any(
        key in payload
        for key in (
            "visible_current_milestones",
            "visible_future_guardrails",
            "withheld_future_count",
        )
    ):
        stage = str(payload.get("stage") or "stage").strip().lower()
        return [(stage, payload)]

    ordered_keys = [key for key in _STAGE_VISIBILITY_ORDER if key in payload]
    ordered_keys.extend(key for key in payload if key not in ordered_keys)
    entries: list[tuple[str, dict[str, Any]]] = []
    for key in ordered_keys:
        item = payload.get(key)
        if isinstance(item, dict):
            entries.append((str(key), item))
    return entries

def _stage_visibility_metric_cell(label: str, value: int, color: str = "[[nf:text.heading]]") -> str:
    return (
        '<td style="width:25%; border-bottom:none; text-align:center; '
        'padding:8px 10px;">'
        f'<div class="kv-label">{_esc(label)}</div>'
        f'<div style="font-size: 24pt; font-weight:700; color:{color}; '
        "font-family:'Songti SC',serif; line-height:1.25;\">"
        f"{value}</div></td>"
    )

def _stage_visibility_policy_text(value: Any) -> str:
    text = _report_text(value)
    return _STAGE_VISIBILITY_POLICIES.get(text, text)

def render_stage_visibility_report(data: dict[str, Any]) -> QTextBrowser:
    """Render stage visibility diagnostics as an operational control panel."""
    entries = _stage_visibility_entries(data)
    chapter = _report_text(data.get("chapter_number"))
    current_total = sum(
        _stage_visibility_count(item.get("visible_current_milestones")) for _stage, item in entries
    )
    future_total = sum(
        _stage_visibility_count(item.get("visible_future_guardrails")) for _stage, item in entries
    )
    withheld_total = sum(
        _stage_visibility_count(item.get("withheld_future_count")) for _stage, item in entries
    )

    parts: list[str] = []
    badges = [
        _tag(f"第 {chapter} 章" if chapter else "阶段控制", tone="muted"),
        _tag(f"阶段 {len(entries)}", tone="muted"),
        _tag(f"当前里程碑 {current_total}", tone="accent" if current_total else "muted"),
        _tag(f"未来护栏 {future_total}", tone="accent" if future_total else "muted"),
        _tag(f"隐藏未来 {withheld_total}", tone="accent" if withheld_total else "muted"),
    ]
    parts.append(f'<div style="margin:2px 0 12px 0;">{"".join(badges)}</div>')

    if not entries:
        parts.append(
            '<div class="section"><h2>阶段可见性诊断</h2>'
            '<div class="kv-value">暂无阶段控制数据。</div></div>'
        )
        return _make_browser(_html_wrap("\n".join(parts), "阶段控制"))

    parts.append(
        '<div class="section" style="padding:12px 14px;">'
        '<table style="margin:0;"><tr>'
        + _stage_visibility_metric_cell("阶段", len(entries))
        + _stage_visibility_metric_cell("当前里程碑", current_total, "[[nf:status.success.warm]]")
        + _stage_visibility_metric_cell("未来护栏", future_total, "[[nf:accent.primary]]")
        + _stage_visibility_metric_cell("隐藏未来", withheld_total, "[[nf:status.danger.deep]]")
        + "</tr></table></div>"
    )

    rows: list[str] = []
    for raw_stage, item in entries:
        stage = str(item.get("stage") or raw_stage).strip().lower()
        label = _STAGE_VISIBILITY_LABELS.get(stage, stage or raw_stage)
        help_text = _STAGE_VISIBILITY_HELP.get(stage, "阶段上下文可见性")
        current_count = _stage_visibility_count(item.get("visible_current_milestones"))
        future_count = _stage_visibility_count(item.get("visible_future_guardrails"))
        withheld_count = _stage_visibility_count(item.get("withheld_future_count"))
        policy = _stage_visibility_policy_text(item.get("policy"))
        status = "未来隐藏" if withheld_count and future_count == 0 else "有界可见"
        status_color = "[[nf:status.danger.deep]]" if withheld_count and future_count == 0 else "[[nf:status.success.warm]]"
        policy_html = (
            f'<div class="kv-value" style="font-size: 12pt;">{_nl2br(policy)}</div>'
            if policy
            else ""
        )
        rows.append(
            "<tr>"
            "<th>"
            f'<div style="font-size: 14pt; color:[[nf:text.heading]];">{_esc(label)}</div>'
            f'<div style="font-weight:400; color:[[nf:text.muted]]; font-size: 11pt;">{_esc(help_text)}</div>'
            f'<span class="tag tag-muted">{_esc(stage or raw_stage)}</span>'
            "</th>"
            f'<td style="text-align:center; font-weight:700;">{current_count}</td>'
            f'<td style="text-align:center; font-weight:700;">{future_count}</td>'
            f'<td style="text-align:center; font-weight:700;">{withheld_count}</td>'
            "<td>"
            f'<span style="color:{status_color}; font-weight:700;">{_esc(status)}</span>'
            + policy_html
            + "</td>"
            "</tr>"
        )

    parts.append(
        "<div class='section'><h2>阶段可见性诊断</h2>"
        "<table>"
        "<tr><th>阶段</th><th>当前里程碑</th><th>未来护栏</th><th>隐藏未来</th><th>策略</th></tr>"
        + "".join(rows)
        + "</table></div>"
    )

    return _make_browser(_html_wrap("\n".join(parts), "阶段控制"))

def render_repair_plan_report(data: dict[str, Any]) -> QTextBrowser:
    """Render chapter repair plan reports in a compact, actionable layout."""
    issues = [item for item in _report_list(data.get("issues")) if isinstance(item, dict)]
    deferred = _report_list(data.get("deferred_issues"))
    surfaces = [
        str(item) for item in _report_list(data.get("repair_surfaces")) if str(item).strip()
    ]
    target_sections = [
        item for item in _report_list(data.get("target_sections")) if isinstance(item, dict)
    ]
    must_keep = _report_list(data.get("must_keep"))
    must_change = _report_list(data.get("must_change"))

    parts: list[str] = []
    badges = [
        _tag(f"问题 {len(issues)}", tone="accent"),
        _tag(f"目标段 {len(target_sections)}", tone="muted"),
    ]
    if deferred:
        badges.append(_tag(f"延后 {len(deferred)}", tone="muted"))
    if bool(data.get("no_op", False)):
        badges.append(_tag("无需修复", tone="muted"))
    for surface in surfaces[:3]:
        badges.append(_tag(surface, tone="muted"))
    parts.append(f'<div style="margin:2px 0 12px 0;">{"".join(badges)}</div>')

    outcome = _report_text(data.get("expected_outcome"))
    if outcome:
        parts.append(
            '<div class="section"><h2>预期结果</h2>'
            f'<div class="kv-value">{_nl2br(outcome)}</div></div>'
        )

    if issues:
        parts.append("<h2>待修复问题</h2>")
        for index, issue in enumerate(issues, 1):
            severity = _report_text(issue.get("severity"), "unknown")
            issue_type = _report_text(issue.get("issue_type"), "issue")
            summary = _report_text(issue.get("summary"), "未提供摘要")
            evidence = _report_text(issue.get("evidence"))
            actions = _report_list(issue.get("fix_actions"))
            postconditions = _report_list(issue.get("postconditions"))
            parts.append(
                '<div class="section">'
                f"<h3>{index}. {_esc(summary)}</h3>"
                f'<div style="margin-bottom:8px;">{_tag(issue_type, tone="muted")}{_tag(severity, tone="accent")}</div>'
                + (
                    '<div class="kv-row"><span class="kv-label">证据：</span>'
                    f'<div class="kv-value">{_nl2br(evidence)}</div></div>'
                    if evidence
                    else ""
                )
                + (
                    '<div class="kv-row"><span class="kv-label">修复动作：</span>'
                    f"{_compact_items(actions, limit=6)}</div>"
                    if actions
                    else ""
                )
                + (
                    '<div class="kv-row"><span class="kv-label">验收条件：</span>'
                    f"{_compact_items(postconditions, limit=4)}</div>"
                    if postconditions
                    else ""
                )
                + "</div>"
            )
    else:
        parts.append(
            '<div class="section"><h2>待修复问题</h2><div class="kv-value">暂无阻断问题。</div></div>'
        )

    if target_sections:
        rows = []
        for section in target_sections:
            start = section.get("start_paragraph", "")
            end = section.get("end_paragraph", "")
            reason = _report_text(section.get("reason"), "未说明原因")
            section_type = _report_text(section.get("section_type"), "section")
            rows.append(
                "<tr>"
                f"<th>{_esc(section_type)}</th>"
                f"<td>段落 {start}-{end}</td>"
                f"<td>{_nl2br(reason)}</td>"
                "</tr>"
            )
        parts.append(
            "<div class='section'><h2>目标范围</h2>"
            "<table><tr><th>类型</th><th>范围</th><th>原因</th></tr>"
            + "".join(rows)
            + "</table></div>"
        )

    if must_change:
        parts.append(
            "<div class='section'><h2>必须修改</h2>"
            + _compact_items(must_change, limit=10)
            + "</div>"
        )
    if must_keep:
        parts.append(
            "<div class='section'><h2>必须保留</h2>"
            + _compact_items(must_keep, limit=14)
            + "</div>"
        )

    return _make_browser(_html_wrap("\n".join(parts), "修复方案"))

def render_expression_repetition_report(data: dict[str, Any]) -> QTextBrowser:
    """Render expression-channel reports as readable hit/guidance cards."""
    chapter = data.get("chapter_number", "")
    records = _report_list(data.get("records"))
    hits = [item for item in _report_list(data.get("hits")) if isinstance(item, dict)]
    guidance = [
        _expression_guidance_payload(item) for item in _report_list(data.get("repair_guidance"))
    ]

    parts: list[str] = []
    level_counts: dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        level = _report_text(record.get("level"), "warning").lower()
        level_counts[level] = level_counts.get(level, 0) + 1
    badges = [
        _tag(f"第 {chapter} 章" if chapter else "表达通道", tone="muted"),
        _tag(f"命中 {len(hits)}", tone="accent" if hits else "muted"),
        _tag(f"记录 {len(records)}", tone="muted"),
        _tag(f"建议 {len(guidance)}", tone="muted"),
    ]
    for level in ("hard", "soft", "quota"):
        count = level_counts.get(level, 0)
        if count:
            badges.append(
                _tag(f"{_EXPRESSION_LEVEL_LABELS.get(level, level)} {count}", tone="muted")
            )
    parts.append(f'<div style="margin:2px 0 12px 0;">{"".join(badges)}</div>')

    if hits:
        parts.append("<h2>命中问题</h2>")
        for index, hit in enumerate(hits, 1):
            summary = _report_text(hit.get("summary") or hit.get("text"), "未提供摘要")
            channel = _report_text(hit.get("channel") or hit.get("channel_id"))
            examples = _report_list(hit.get("examples"))
            parts.append(
                '<div class="section">'
                f"<h3>{index}. {_esc(channel or '表达重复')}</h3>"
                f'<div class="kv-value">{_nl2br(summary)}</div>'
                + (_compact_items(examples, limit=4) if examples else "")
                + "</div>"
            )
    else:
        parts.append(
            '<div class="section"><h2>命中问题</h2><div class="kv-value">暂无表达通道命中。</div></div>'
        )

    if guidance:
        parts.append("<h2>修复建议</h2>")
        for item in guidance[:10]:
            issue = _report_text(item.get("issue") or item.get("summary"))
            action = _report_text(item.get("action") or item.get("description"))
            priority = _report_text(item.get("priority"))
            tags = _tag(f"优先级 {priority}", tone="muted") if priority else ""
            body = (
                f'<div class="kv-row"><span class="kv-label">问题</span> {_nl2br(_esc(issue))}</div>'
                if issue
                else ""
            )
            if action:
                body += (
                    '<div class="kv-row"><span class="kv-label">建议</span> '
                    f'<div class="hint-block" style="margin:4px 0;">{_nl2br(_esc(action))}</div></div>'
                )
            parts.append(f'<div class="section">{tags}{body}</div>')

    if records:
        parts.append("<h2>通道限制</h2>")
        for index, record in enumerate(records[:12], 1):
            if isinstance(record, dict):
                title = _report_text(
                    record.get("text") or record.get("summary") or record.get("description"),
                    f"记录 {index}",
                )
                channel = _expression_channel_label(record, fallback=f"记录 {index}")
                examples = _report_list(record.get("examples"))
                tags = [
                    _expression_level_tag(record.get("level")),
                    _tag(channel, tone="muted"),
                ]
                parts.append(
                    '<div class="section">'
                    + f"<h3>{_esc(title)}</h3>"
                    + f'<div style="margin:2px 0 8px 0;">{"".join(tags)}</div>'
                    + _expression_record_meta(record)
                    + (_compact_items(examples, limit=4) if examples else "")
                    + "</div>"
                )
            else:
                parts.append(f'<div class="rule-item">{_nl2br(str(record))}</div>')
        if len(records) > 12:
            parts.append(
                '<div class="kv-value" style="color:[[nf:text.muted]];font-size: 12pt;">'
                f"另有 {len(records) - 12} 条通道记录未展开。"
                "</div>"
            )

    return _make_browser(_html_wrap("\n".join(parts), "表达通道报告"))

def render_narrative_contract(data: dict[str, Any]) -> QTextBrowser:
    """Render the deterministic runtime narrative contract."""
    parts: list[str] = []

    world_rules = data.get("world_rules") or []
    if world_rules:
        items = "".join(
            f'<div class="rule-item">{_esc(str(rule))}</div>'
            for rule in world_rules
            if str(rule).strip()
        )
        parts.append(f"<div class='section'><h2>世界规则</h2>{items}</div>")

    tone = str(data.get("tone") or "").strip()
    themes = [str(item).strip() for item in data.get("themes") or [] if str(item).strip()]
    if tone or themes:
        theme_tags = "".join(f'<span class="tag">{_esc(theme)}</span>' for theme in themes)
        parts.append(
            "<div class='section'><h2>基调与主题</h2>"
            + (
                f"<div class='kv-row'><span class='kv-label'>基调：</span>{_esc(tone)}</div>"
                if tone
                else ""
            )
            + (
                f"<div class='kv-row'><span class='kv-label'>主题：</span>{theme_tags}</div>"
                if theme_tags
                else ""
            )
            + "</div>"
        )

    world_context = data.get("world_context")
    if isinstance(world_context, dict) and world_context:
        rows = "".join(
            f"<tr><th>{_esc(generic_key_label(str(key)))}</th><td>{_nl2br(value)}</td></tr>"
            for key, value in world_context.items()
            if str(value).strip()
        )
        if rows:
            parts.append(f"<div class='section'><h2>世界上下文</h2><table>{rows}</table></div>")

    arcs = data.get("character_arcs") or []
    if arcs:
        rows = []
        for arc in arcs:
            if not isinstance(arc, dict):
                continue
            rows.append(
                "<tr>"
                f"<th>{_esc(str(arc.get('name') or arc.get('char_name') or '角色'))}</th>"
                f"<td><span class='tag tag-muted'>{_esc(str(arc.get('role') or ''))}</span></td>"
                f"<td>{_nl2br(arc.get('arc', arc.get('arc_summary', '')))}</td>"
                "</tr>"
            )
        if rows:
            parts.append(
                "<div class='section'><h2>角色弧光</h2>"
                "<table><tr><th>角色</th><th>定位</th><th>弧光</th></tr>"
                + "".join(rows)
                + "</table></div>"
            )

    turning_points = data.get("key_turning_points") or []
    if turning_points:
        rows = []
        for point in turning_points:
            if isinstance(point, dict):
                chapter = point.get("chapter") or point.get("chapter_number") or ""
                desc = point.get("description", "")
                rows.append(f"<tr><th>第 {_esc(str(chapter))} 章</th><td>{_nl2br(desc)}</td></tr>")
            else:
                rows.append(f"<tr><td colspan='2'>{_esc(str(point))}</td></tr>")
        parts.append(f"<div class='section'><h2>关键转折</h2><table>{''.join(rows)}</table></div>")

    ending = str(data.get("ending_strategy") or "").strip()
    if ending:
        parts.append(
            f"<div class='section'><h2>收束策略</h2><div class='kv-value'>{_nl2br(ending)}</div></div>"
        )

    protocol = data.get("continuity_protocol")
    if isinstance(protocol, dict) and protocol:
        rows = "".join(
            f"<tr><th>{_esc(generic_key_label(str(key)))}</th><td>{_esc(str(value))}</td></tr>"
            for key, value in protocol.items()
        )
        parts.append(f"<div class='section'><h2>连贯性协议</h2><table>{rows}</table></div>")

    llm_contract = data.get("llm_contract")
    if isinstance(llm_contract, dict):
        parts.append(
            "<div class='hint-block'>该视图已合并展示内嵌 LLM 裁判契约，"
            "不会再单独显示重复的 LLM叙事契约文件。</div>"
        )
        parts.extend(_render_llm_narrative_contract_sections(llm_contract, include_title=False))

    return _make_browser(_html_wrap("\n".join(parts), "叙事契约"))

def _format_thread_chapter_range(thread: dict[str, Any]) -> str:
    for key in ("chapters", "chapter_range", "chapter_span"):
        text = str(thread.get(key) or "").strip()
        if text:
            return text
    numbers = _collect_thread_chapter_numbers(thread)
    if not numbers:
        return "未指定"
    explicit_range = _explicit_thread_chapter_range(thread)
    if explicit_range:
        return explicit_range
    unique = sorted(set(numbers))
    if len(unique) == 1:
        return f"第 {unique[0]} 章"
    if unique == list(range(unique[0], unique[-1] + 1)):
        return f"第 {unique[0]}-{unique[-1]} 章"
    return "第 " + "、".join(str(item) for item in unique[:12]) + " 章"

def _explicit_thread_chapter_range(thread: dict[str, Any]) -> str:
    start = _first_positive_int(
        thread.get("start_chapter"),
        thread.get("setup_chapter"),
        thread.get("introduce_chapter"),
    )
    end = _first_positive_int(
        thread.get("end_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
    )
    if not start or not end:
        return ""
    lo, hi = sorted((start, end))
    return f"第 {lo} 章" if lo == hi else f"第 {lo}-{hi} 章"

def _collect_thread_chapter_numbers(thread: dict[str, Any]) -> list[int]:
    values: list[Any] = [
        thread.get("chapter_numbers"),
        thread.get("involved_chapters"),
        thread.get("start_chapter"),
        thread.get("end_chapter"),
        thread.get("setup_chapter"),
        thread.get("introduce_chapter"),
        thread.get("resolve_chapter"),
        thread.get("payoff_chapter"),
    ]
    for scene in thread.get("key_scenes") or []:
        if isinstance(scene, dict):
            values.extend([scene.get("chapter"), scene.get("chapter_number")])
    numbers: list[int] = []
    for value in values:
        numbers.extend(_extract_positive_ints(value))
    return numbers

def _extract_positive_ints(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value] if value > 0 else []
    if isinstance(value, float):
        iv = int(value)
        return [iv] if iv > 0 and iv == value else []
    if isinstance(value, (list, tuple, set)):
        numbers: list[int] = []
        for item in value:
            numbers.extend(_extract_positive_ints(item))
        return numbers
    text = str(value)
    import re

    return [int(match) for match in re.findall(r"\d+", text) if int(match) > 0]

def _first_positive_int(*values: Any) -> int:
    for value in values:
        numbers = _extract_positive_ints(value)
        if numbers:
            return numbers[0]
    return 0

def _render_llm_narrative_contract_sections(
    data: dict[str, Any],
    *,
    include_title: bool,
) -> list[str]:
    parts: list[str] = []

    title = str(data.get("title") or "").strip()
    if include_title and title:
        parts.append(
            f"<div class='section'><h2>标题</h2><div class='kv-value'>{_esc(title)}</div></div>"
        )

    world_rules = data.get("world_rules") or []
    if world_rules:
        rule_parts = []
        for rule in world_rules:
            if not isinstance(rule, dict):
                rule_parts.append(f'<div class="rule-item">{_esc(str(rule))}</div>')
                continue
            header = "｜".join(
                part
                for part in [
                    str(rule.get("rule_id") or "").strip(),
                    str(rule.get("title") or "").strip(),
                    str(rule.get("binding_level") or "").strip(),
                ]
                if part
            )
            rule_parts.append(
                "<div class='rule-item'>"
                f"<div><strong>{_esc(header or '世界规则')}</strong></div>"
                f"<div>{_nl2br(rule.get('content', ''))}</div>"
                f"<div><span class='kv-label'>执行：</span>{_nl2br(rule.get('enforcement', ''))}</div>"
                f"<div><span class='kv-label'>禁止：</span>{_nl2br(rule.get('forbidden_violations', ''))}</div>"
                "</div>"
            )
        parts.append(f"<div class='section'><h2>世界规则</h2>{''.join(rule_parts)}</div>")

    arcs = data.get("character_arcs") or []
    if arcs:
        arc_parts = []
        for arc in arcs:
            if not isinstance(arc, dict):
                continue
            stages = arc.get("arc_stages") or []
            stage_items = []
            for stage in stages:
                if not isinstance(stage, dict):
                    continue
                label = str(stage.get("stage_name") or stage.get("stage_id") or "阶段").strip()
                chapters = str(stage.get("chapters") or "").strip()
                requirement = stage.get("narrative_requirement", "")
                stage_items.append(
                    "<li>"
                    f"<strong>{_esc(label)}</strong>"
                    + (f" <span class='tag tag-muted'>{_esc(chapters)}</span>" if chapters else "")
                    + f"<div class='kv-value'>{_nl2br(requirement)}</div>"
                    "</li>"
                )
            arc_parts.append(
                "<div class='hint-block'>"
                f"<h3>{_esc(str(arc.get('char_name') or arc.get('char_id') or '角色'))}"
                f" <span class='tag tag-muted'>{_esc(str(arc.get('role') or ''))}</span></h3>"
                f"<ul>{''.join(stage_items)}</ul>"
                f"<div><span class='kv-label'>禁区：</span>{_nl2br(arc.get('forbidden_behavior', ''))}</div>"
                f"<div><span class='kv-label'>完成标准：</span>{_nl2br(arc.get('completion_criteria', ''))}</div>"
                "</div>"
            )
        parts.append(f"<div class='section'><h2>角色弧光</h2>{''.join(arc_parts)}</div>")

    threads = data.get("plot_threads") or []
    if threads:
        thread_parts = []
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            scenes = thread.get("key_scenes") or []
            scene_rows = []
            for scene in scenes:
                if not isinstance(scene, dict):
                    continue
                scene_rows.append(
                    "<tr>"
                    f"<th>{_esc(str(scene.get('chapter') or ''))}</th>"
                    f"<td>{_esc(str(scene.get('title') or scene.get('scene_id') or ''))}</td>"
                    f"<td>{_nl2br(scene.get('description', ''))}</td>"
                    "</tr>"
                )
            thread_parts.append(
                "<div class='hint-block'>"
                f"<h3>{_esc(str(thread.get('thread_name') or thread.get('thread_id') or '情节线'))}"
                f" <span class='tag tag-muted'>{_esc(str(thread.get('priority') or ''))}</span></h3>"
                f"<div><span class='kv-label'>章节范围：</span>{_esc(_format_thread_chapter_range(thread))}</div>"
                + (
                    "<table><tr><th>章</th><th>节点</th><th>描述</th></tr>"
                    + "".join(scene_rows)
                    + "</table>"
                    if scene_rows
                    else ""
                )
                + f"<div><span class='kv-label'>收束：</span>{_nl2br(thread.get('resolution', ''))}</div>"
                "</div>"
            )
        parts.append(f"<div class='section'><h2>情节线</h2>{''.join(thread_parts)}</div>")

    return parts

def render_llm_narrative_contract(data: dict[str, Any]) -> QTextBrowser:
    """Render the LLM-authored adjudication-oriented narrative contract."""
    parts = _render_llm_narrative_contract_sections(data, include_title=True)
    return _make_browser(_html_wrap("\n".join(parts), "LLM叙事契约"))

def render_bridge(data: dict[str, Any]) -> QTextBrowser:
    """Render chapter bridge JSON as a formatted HTML view."""
    from_ch = data.get("from_chapter", "?")
    to_ch = data.get("to_chapter", "?")
    mode = data.get("transition_mode", "")

    parts: list[str] = []
    mode_label = _TRANSITION_MODE_LABELS.get(mode, mode)
    parts.append(
        f'<div class="section" style="text-align:center; padding:16px;">'
        f"<div style=\"font-size: 28pt; font-weight:700; font-family:'Songti SC',serif; color:[[nf:text.heading]];\">"
        f"第 {from_ch} 章 → 第 {to_ch} 章</div>"
        f'<div style="color:[[nf:text.muted]]; font-size: 13pt; margin-top:6px;">'
    )
    if mode_label:
        parts.append(f'<span class="tag">{_esc(mode_label)}</span>')
    pov = data.get("opening_pov", "")
    if pov:
        parts.append(f'<span class="tag-muted tag">{_esc(pov)} 视角</span>')
    parts.append("</div></div>")

    time_val = data.get("opening_time", "")
    loc_val = data.get("opening_location", "")
    if time_val or loc_val:
        parts.append('<h2>开场设定</h2><div class="section">')
        if time_val:
            parts.append(
                f'<div class="kv-row"><span class="kv-label">时间：</span><span class="kv-value">{_esc(time_val)}</span></div>'
            )
        if loc_val:
            parts.append(
                f'<div class="kv-row"><span class="kv-label">地点：</span><span class="kv-value">{_esc(loc_val)}</span></div>'
            )
        parts.append("</div>")

    summary = data.get("bridge_summary", "")
    if summary:
        parts.append(f'<h2>桥接概述</h2><div class="hint-block">{_nl2br(summary)}</div>')

    emotion = data.get("emotional_carryover", "")
    if emotion:
        parts.append(f'<h2>情感承接</h2><div class="theme-item">{_nl2br(emotion)}</div>')

    handoff = data.get("action_handoff", "")
    if handoff:
        parts.append(f'<h2>行动交接</h2><div class="rule-item">{_nl2br(handoff)}</div>')

    causal_link = data.get("causal_link")
    if isinstance(causal_link, dict):
        prev_event = causal_link.get("previous_event", "")
        mechanism = causal_link.get("causal_mechanism", "")
        unresolved = causal_link.get("unresolved_question", "")
        open_threads = causal_link.get("open_threads", [])
        if prev_event or mechanism or unresolved or open_threads:
            parts.append('<h2>因果承接</h2><div class="section">')
            if prev_event:
                parts.append(
                    f'<div class="kv-row"><span class="kv-label">前章事件：</span><div class="kv-value">{_nl2br(prev_event)}</div></div>'
                )
            if mechanism:
                parts.append(
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">因果机制：</span><div class="kv-value">{_nl2br(mechanism)}</div></div>'
                )
            if unresolved:
                parts.append(
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">悬而未决：</span><div class="kv-value" style="color:[[nf:accent.primary]];">{_nl2br(unresolved)}</div></div>'
                )
            if open_threads:
                thread_html = "".join(f"<li>{_esc(t)}</li>" for t in open_threads)
                parts.append(
                    f'<div class="kv-row" style="margin-top:6px;"><span class="kv-label">开放线索：</span><ul style="margin:4px 0; padding-left:20px;">{thread_html}</ul></div>'
                )
            parts.append("</div>")

    rel_beat = data.get("relationship_beat")
    if isinstance(rel_beat, dict):
        parts.append('<h2>关系节拍</h2><div class="section">')
        for key, label in (
            ("current_trust_level", "信任度"),
            ("unspoken_tension", "潜在张力"),
            ("power_dynamic", "权力动态"),
        ):
            value = rel_beat.get(key, "")
            if value:
                parts.append(
                    f'<div class="kv-row"><span class="kv-label">{label}：</span>'
                    f'<div class="kv-value">{_nl2br(value)}</div></div>'
                )
        parts.append("</div>")

    anchors = data.get("sensory_anchors", [])
    if anchors:
        parts.append("<h2>感官锚点</h2>")
        parts.append("".join(f'<div class="rule-item">{_esc(anchor)}</div>' for anchor in anchors))

    questions = data.get("pending_questions", [])
    if questions:
        parts.append("<h2>悬而未决的问题</h2>")
        parts.append(f"<ul>{''.join(f'<li>{_esc(question)}</li>' for question in questions)}</ul>")

    forbidden = data.get("forbidden_repetition", [])
    if forbidden:
        parts.append("<h2>下一章避免重复</h2>")
        parts.append(f"<ul>{''.join(f'<li>{_esc(item)}</li>' for item in forbidden)}</ul>")

    return _make_browser(_html_wrap("\n".join(parts), "章节桥接"))

def render_chapter_plan(data: dict[str, Any]) -> QTextBrowser:
    """Render chapter plan JSON as a formatted HTML view."""
    parts: list[str] = []
    opening = data.get("opening_contract", "")
    closing = data.get("closing_contract", "")
    if opening or closing:
        parts.append('<div class="section">')
        if opening:
            parts.append(
                f'<div class="kv-row"><span class="kv-label">开篇契约：</span><div class="kv-value">{_nl2br(opening)}</div></div>'
            )
        if closing:
            parts.append(
                f'<div class="kv-row" style="margin-top:8px;"><span class="kv-label">收束契约：</span>'
                f'<div class="kv-value">{_nl2br(closing)}</div></div>'
            )
        parts.append("</div>")

    arc = data.get("emotional_arc", "")
    if arc:
        parts.append(
            f'<h2>情感弧线</h2><div class="theme-item" style="font-size: 14pt; font-weight:600;">{_esc(arc)}</div>'
        )

    scenes = data.get("scene_intents", [])
    if scenes:
        parts.append(f"<h2>场景编排 · 共 {len(scenes)} 场</h2>")
        for index, scene in enumerate(scenes, 1):
            sid = scene.get("scene_id", f"scene_{index:02d}")
            summary = scene.get("summary", "")
            location = scene.get("location", "")
            time_marker = scene.get("time_marker", "")
            chars = scene.get("required_characters", [])
            parts.append(
                f'<div class="section" style="margin:8px 0;"><h3 style="margin-top:0;">场景 {index} · {_esc(sid)}</h3>'
            )
            if summary:
                parts.append(
                    f'<div class="kv-value" style="margin-bottom:8px;">{_nl2br(summary)}</div>'
                )
            tags: list[str] = []
            if location:
                tags.append(f'<span class="tag-muted tag">📍 {_esc(location)}</span>')
            if time_marker:
                tags.append(f'<span class="tag-muted tag">🕐 {_esc(time_marker)}</span>')
            for character in chars:
                tags.append(f'<span class="tag">{_esc(character)}</span>')
            if tags:
                parts.append(f'<div style="margin:6px 0;">{"".join(tags)}</div>')
            for field_key, field_label in (
                ("purpose", "目的"),
                ("conflict", "冲突"),
                ("required_outcome", "必要结果"),
                ("exit_target_state", "退出状态"),
            ):
                field_value = scene.get(field_key, "")
                if field_value:
                    parts.append(
                        f'<div class="kv-row"><span class="kv-label">{field_label}：</span>'
                        f'<span class="kv-value">{_esc(field_value)}</span></div>'
                    )
            for motivation in scene.get("character_motivations", []):
                char = motivation.get("character", "")
                motive = motivation.get("motivation", "")
                stake = motivation.get("stake", "")
                if char and motive:
                    parts.append(
                        f'<div class="rule-item" style="margin:4px 0;"><strong>{_esc(char)}</strong>：{_esc(motive)}'
                    )
                    if stake:
                        parts.append(f'<br><span style="color:[[nf:text.muted]];">风险：{_esc(stake)}</span>')
                    parts.append("</div>")
            parts.append("</div>")

    for heading, items, item_class in (
        ("状态转换", data.get("required_state_transitions", []), "rule-item"),
        ("伏笔计划", data.get("foreshadowing_plan", []), "theme-item"),
        ("关键揭露", data.get("key_revelations", []), "rule-item"),
    ):
        if items:
            parts.append(f"<h2>{heading}</h2>")
            parts.append("".join(f'<div class="{item_class}">{_esc(item)}</div>' for item in items))

    return _make_browser(_html_wrap("\n".join(parts), "章节计划"))

def render_alignment_report_body_html(data: dict[str, Any]) -> str:
    """Render alignment report JSON as a formatted HTML body."""
    score = data.get("alignment_score")
    risk = data.get("risk_level", "")
    summary = data.get("summary", "")

    parts: list[str] = []
    conflict_level = data.get("conflict_level", "")
    if score is not None:
        score_val = float(score)
        color = _score_color(score_val)
        risk_color, risk_label = _RISK_LEVEL_STYLE.get(risk, ("[[nf:text.muted]]", risk))
        conf_color, conf_label = _CONFLICT_LEVEL_STYLE.get(conflict_level, ("", ""))
        risk_background = f"rgba({risk_color}, 0.1)"
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{color};\">{score_val:.1f}</div>"
            f'<div style="margin-top:4px;"><span class="tag" style="background:{risk_background}; color:{risk_color}; border-color:{risk_color};">{_esc(risk_label)}</span>'
            + (
                f' <span class="tag-muted tag" style="color:{conf_color};">{_esc(conf_label)}</span>'
                if conf_label
                else ""
            )
            + "</div></div>"
        )

    if summary:
        parts.append(f'<h2>总结</h2><div class="hint-block">{_nl2br(summary)}</div>')

    for heading, items, border_color, item_class in (
        ("缺失的主线要点", data.get("missing_main_points", []), "[[nf:status.danger.deep]]", "rule-item"),
        ("支线正面贡献", data.get("supportive_subplot_points", []), "", "theme-item"),
        ("薄弱支线", data.get("weak_subplot_points", []), "[[nf:accent.primary]]", "rule-item"),
    ):
        if items:
            parts.append(f"<h2>{heading}</h2>")
            if border_color:
                parts.append(
                    "".join(
                        f'<div class="{item_class}" style="border-left-color:{border_color};">{_esc(item)}</div>'
                        for item in items
                    )
                )
            else:
                parts.append(
                    "".join(f'<div class="{item_class}">{_esc(item)}</div>' for item in items)
                )

    repairs = data.get("repair_actions", [])
    if repairs:
        parts.append("<h2>修复建议</h2>")
        for repair in repairs:
            parts.append(f'<div class="hint-block">💡 {_esc(repair)}</div>')

    return "\n".join(parts)

def render_alignment_report(data: dict[str, Any]) -> QTextBrowser:
    """Render alignment report JSON as a formatted HTML view."""
    return _make_browser(_html_wrap(render_alignment_report_body_html(data), "对齐报告"))

_ISSUE_TYPE_LABELS: dict[str, str] = {
    "continuity_gap": "连贯缺口",
    "carry_forward_missing": "承接缺失",
    "character_inconsistency": "角色不一致",
    "prompt_leak": "提词泄漏",
    "location_jump": "场景跳切",
    "custody_break": "托管中断",
    "pov_jump": "视角跳切",
    "opening_gap": "开场衔接",
    "closing_gap": "结尾衔接",
    "closing_contract_mismatch": "结尾契约",
    "bridge_contract_not_followed": "桥接契约",
    "bridge_contract_violation": "桥接契约",
    "handoff_missing": "交接缺失",
    "knowledge_contradiction": "知识矛盾",
    "ending_ambiguity": "结尾模糊",
    "exit_state_gap": "退出状态",
    "question_ignored": "悬念搁置",
    "information_consistency": "信息一致",
    "relationship_change_support": "关系支撑",
    "relationship_development": "关系发展",
    "forbidden_element_violation": "硬禁元素",
    "forbidden_element_usage": "意象复用",
    "text_repetition": "文本重复",
    "sensory_anchor_repetition": "感官重复",
    "timeline_conflict": "时间线冲突",
    "location_inconsistency": "场景不一致",
    "plot_contradiction": "情节矛盾",
    "tone_shift": "基调偏移",
    "relationship_inconsistency": "关系不一致",
    "state_contradiction": "状态矛盾",
    "factual_error": "事实错误",
}

_SEVERITY_STYLE: dict[str, tuple[str, str]] = {
    "critical": ("[[nf:status.danger.deep]]", "严重"),
    "high": ("[[nf:status.danger.deep]]", "高"),
    "medium": ("[[nf:accent.primary]]", "中"),
    "low": ("[[nf:text.muted]]", "低"),
}

_REWRITE_SCOPE_LABELS: dict[str, str] = {
    "opening": "开头段落",
    "middle": "中间段落",
    "closing": "结尾段落",
    "chapter": "全章",
    "paragraph": "单段",
}

def render_continuity_report_body_html(data: dict[str, Any]) -> str:
    """Render continuity report JSON as a formatted HTML body."""
    score = data.get("continuity_score")
    issues = data.get("issues", [])
    summary = data.get("summary", "")
    recommendations = data.get("recommendations", [])

    parts: list[str] = []
    if score is not None:
        score_val = float(score)
        color = _score_color(score_val)
        issue_count = len(issues)
        badge_text = "无问题" if issue_count == 0 else f"{issue_count} 个问题"
        badge_color = "[[nf:status.success.warm]]" if issue_count == 0 else "[[nf:accent.primary]]"
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{color};\">{score_val:.1f}</div>"
            f'<div style="margin-top:4px;"><span class="tag" style="color:{badge_color};">{badge_text}</span></div></div>'
        )

    if summary:
        parts.append(f'<h2>总结</h2><div class="hint-block">{_nl2br(summary)}</div>')

    if issues:
        parts.append(f"<h2>连贯性问题 · {len(issues)} 项</h2>")
        for issue in issues:
            if isinstance(issue, dict):
                severity = issue.get("severity", "")
                sev_color, sev_label = _SEVERITY_STYLE.get(
                    severity, ("[[nf:text.muted]]", severity or "未知")
                )
                issue_type = issue.get("issue_type", "")
                type_label = _ISSUE_TYPE_LABELS.get(issue_type, issue_type)
                desc = issue.get("summary", "") or issue.get("description", str(issue))
                evidence = issue.get("evidence", "")
                affected = issue.get("affected_characters", [])
                scope = issue.get("rewrite_scope", "")
                scope_label = _REWRITE_SCOPE_LABELS.get(scope, scope)
                fix_actions = issue.get("fix_actions", [])

                parts.append(f'<div class="rule-item" style="border-left-color:{sev_color};">')
                # Header: severity + issue type
                parts.append(
                    f'<div style="margin-bottom:4px;">'
                    f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">{_esc(sev_label)}</span> '
                )
                if type_label:
                    parts.append(
                        f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(type_label)}</span> '
                    )
                parts.append("</div>")
                # Summary
                parts.append(f'<div style="margin-bottom:4px;">{_esc(desc)}</div>')
                # Evidence
                if evidence:
                    parts.append(
                        f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                        f"<b>依据：</b>{_esc(evidence)}</div>"
                    )
                # Affected characters + scope
                meta_parts: list[str] = []
                if affected:
                    meta_parts.append(f"涉及角色：{_esc('、'.join(affected))}")
                if scope_label:
                    meta_parts.append(f"修改范围：{_esc(scope_label)}")
                if meta_parts:
                    parts.append(
                        f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                        f"{'　|　'.join(meta_parts)}</div>"
                    )
                # Fix actions
                if fix_actions:
                    parts.append('<div style="margin-top:4px;">')
                    for fix in fix_actions:
                        parts.append(
                            f'<div style="color:[[nf:status.success.warm]]; font-size: 12pt;">💡 {_esc(fix)}</div>'
                        )
                    parts.append("</div>")
                parts.append("</div>")
            else:
                parts.append(f'<div class="rule-item">{_esc(str(issue))}</div>')

    if recommendations:
        parts.append("<h2>修复建议</h2>")
        for recommendation in recommendations:
            parts.append(f'<div class="hint-block">💡 {_esc(str(recommendation))}</div>')

    return "\n".join(parts)

def render_continuity_report(data: dict[str, Any]) -> QTextBrowser:
    """Render continuity report JSON as a formatted HTML view."""
    return _make_browser(_html_wrap(render_continuity_report_body_html(data), "连贯性报告"))

_GUARD_DECISION_LABELS: dict[str, tuple[str, str]] = {
    "continue": ("[[nf:status.success.warm]]", "继续"),
    "continue_with_constraints": ("[[nf:status.success.warm]]", "带约束继续"),
    "adjust_outline_fast": ("[[nf:accent.primary]]", "快速调整大纲"),
    "adjust_outline_smart": ("[[nf:accent.primary]]", "智能调整大纲"),
    "pause_for_human": ("[[nf:status.danger.deep]]", "等待人工决策"),
    "rollback_and_regen": ("[[nf:status.danger.deep]]", "回滚重生成"),
}

_ENTITY_ACTION_LABELS: dict[str, tuple[str, str]] = {
    "keep": ("[[nf:status.success.warm]]", "保留"),
    "defer": ("[[nf:accent.primary]]", "延后"),
    "merge": ("[[nf:text.muted]]", "合并"),
    "drop": ("[[nf:status.danger.deep]]", "丢弃"),
}

_ENTITY_TYPE_LABELS: dict[str, str] = {
    "character": "角色",
    "location": "场景",
    "item": "物品",
}

def render_guard_report_body_html(data: dict[str, Any]) -> str:
    """Render AI guardrail report JSON as a formatted HTML body."""
    decision_data = data.get("decision", {})
    if not isinstance(decision_data, dict):
        decision_data = {}

    decision = decision_data.get("decision", "")
    risk_level = decision_data.get("risk_level", "")
    outline_action = decision_data.get("outline_action", "none")
    entity_actions = decision_data.get("entity_actions", [])
    constraints = decision_data.get("next_chapter_constraints", [])
    reasoning = decision_data.get("reasoning_brief", "")

    parts: list[str] = []

    # Header: decision + risk level
    dec_color, dec_label = _GUARD_DECISION_LABELS.get(decision, ("[[nf:text.muted]]", decision or "未知"))
    risk_color, risk_label = _RISK_LEVEL_STYLE.get(risk_level, ("[[nf:text.muted]]", risk_level or "未知"))
    parts.append(
        f'<div class="section" style="text-align:center; padding:20px;">'
        f"<div style=\"font-size: 22pt; font-weight:700; font-family:'Songti SC',serif; color:{dec_color};\">{_esc(dec_label)}</div>"
        f'<div style="margin-top:6px;">'
        f'<span class="tag" style="color:{risk_color};">{_esc(risk_label)}</span>'
    )
    if outline_action and outline_action != "none":
        oa_label = {"fast": "快速调整", "smart": "智能调整"}.get(outline_action, outline_action)
        parts.append(f' <span class="tag-muted tag">大纲：{_esc(oa_label)}</span>')
    parts.append("</div></div>")

    # Reasoning
    if reasoning:
        parts.append(f'<h2>判定理由</h2><div class="hint-block">{_nl2br(reasoning)}</div>')

    # Entity actions
    if entity_actions:
        parts.append(f"<h2>实体处置 · {len(entity_actions)} 项</h2>")
        for entity in entity_actions:
            if not isinstance(entity, dict):
                parts.append(f'<div class="rule-item">{_esc(str(entity))}</div>')
                continue
            e_type = entity.get("entity_type", "")
            e_name = entity.get("name", "")
            e_action = entity.get("action", "")
            e_reason = entity.get("reason", "")
            e_merge = entity.get("merge_target", "")

            act_color, act_label = _ENTITY_ACTION_LABELS.get(e_action, ("[[nf:text.muted]]", e_action))
            type_label = _ENTITY_TYPE_LABELS.get(e_type, e_type)

            parts.append(f'<div class="rule-item" style="border-left-color:{act_color};">')
            parts.append(
                f'<span class="tag" style="color:{act_color}; font-size: 10pt;">{_esc(act_label)}</span> '
            )
            if type_label:
                parts.append(
                    f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(type_label)}</span> '
                )
            parts.append(f"<b>{_esc(e_name)}</b>")
            if e_reason:
                parts.append(
                    f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">{_esc(e_reason)}</span>'
                )
            if e_merge:
                parts.append(
                    f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">合并至：{_esc(e_merge)}</span>'
                )
            parts.append("</div>")

    # Next chapter constraints
    if constraints:
        parts.append(f"<h2>下一章约束 · {len(constraints)} 条</h2>")
        for idx, c in enumerate(constraints, 1):
            parts.append(f'<div class="rule-item">{idx}. {_esc(str(c))}</div>')

    return "\n".join(parts)

def render_guard_report(data: dict[str, Any]) -> QTextBrowser:
    """Render AI guardrail report JSON as a formatted HTML view."""
    chapter_num = data.get("chapter_number", "?")
    return _make_browser(
        _html_wrap(render_guard_report_body_html(data), f"第 {chapter_num} 章 · AI 护栏")
    )

_KB_VERDICT_LABELS: dict[str, tuple[str, str]] = {
    "pass": ("[[nf:status.success.warm]]", "通过"),
    "not_leak": ("[[nf:status.success.warm]]", "未越界"),
    "legitimate_reveal": ("[[nf:status.success.warm]]", "合法揭晓"),
    "ambiguous": ("[[nf:accent.primary]]", "存疑"),
    "leak": ("[[nf:status.danger.deep]]", "知识泄露"),
    "premature_reveal": ("[[nf:status.danger.deep]]", "提前揭晓"),
    "unsupported_knowledge_gain": ("[[nf:status.danger.deep]]", "无据获知"),
}

_KB_KNOWLEDGE_TYPE_LABELS: dict[str, str] = {
    "known": "已知",
    "suspected": "怀疑",
    "misbelief": "误信",
    "secret_kept": "守密",
}

_KB_VISIBILITY_LABELS: dict[str, str] = {
    "public": "公开",
    "private": "私密",
    "secret": "绝密",
}

_KB_BOUNDARY_REASON_LABELS: dict[str, str] = {
    "future_source_chapter": "来源章未到",
    "future_reveal": "揭晓章未到",
    "private_non_pov": "非 POV 私密",
}

def render_knowledge_boundary_report(data: dict[str, Any]) -> QTextBrowser:
    """Render knowledge boundary verification report as a formatted HTML view."""
    chapter = data.get("chapter")
    stage = data.get("stage", "")
    source_hash = data.get("source_text_hash", "")
    hidden_count = data.get("hidden_candidate_count", 0)
    prescreen_count = data.get("prescreen_hit_count", 0)
    prescreen_hits = data.get("prescreen_hits", [])
    verdict = data.get("verdict", "")
    fallback_used = data.get("fallback_used", False)
    issues = data.get("issues", [])
    findings = data.get("findings", [])
    repair_tickets = data.get("repair_tickets", [])
    audit_skipped_reason = data.get("audit_skipped_reason", "")

    parts: list[str] = []

    # 1. Verdict card
    v_color, v_label = _KB_VERDICT_LABELS.get(verdict, ("[[nf:text.muted]]", verdict or "未知"))
    parts.append(
        f'<div class="section" style="text-align:center; padding:20px;">'
        f"<div style=\"font-size: 36pt; font-weight:700; font-family:'Songti SC',serif; color:{v_color};\">"
        f"{_esc(v_label)}</div>"
    )
    if fallback_used:
        parts.append(
            '<div style="margin-top:6px;">'
            '<span class="tag-muted tag" style="font-size: 10pt;">回退方案</span></div>'
        )
    parts.append("</div>")

    if audit_skipped_reason:
        parts.append(
            f'<div class="hint-block" style="margin-bottom:8px;">'
            f"审计跳过：{_esc(audit_skipped_reason)}</div>"
        )

    # 2. Audit flow row
    finding_count = len(findings) if findings else len(issues)
    parts.append(
        f'<div style="text-align:center; margin-bottom:12px;">'
        f'<span class="tag-muted tag">隐藏候选 {_esc(str(hidden_count))}</span>'
        f" → "
        f'<span class="tag-muted tag">预筛命中 {_esc(str(prescreen_count))}</span>'
        f" → "
        f'<span class="tag-muted tag">审计发现 {_esc(str(finding_count))}</span>'
        f"</div>"
    )

    # 3. Metadata row
    meta_items: list[str] = []
    if chapter is not None:
        meta_items.append(f"第 {_esc(str(chapter))} 章")
    if stage:
        meta_items.append(_esc(stage))
    if source_hash:
        meta_items.append(_esc(source_hash[:12]) + "…")
    if meta_items:
        parts.append(
            f'<div style="text-align:center; color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:12px;">'
            f"{' · '.join(meta_items)}</div>"
        )

    # 4. Prescreen hits
    if prescreen_hits:
        parts.append(f"<h2>预筛命中 · {len(prescreen_hits)} 项</h2>")
        for hit in prescreen_hits:
            if not isinstance(hit, dict):
                parts.append(f'<div class="rule-item">{_esc(str(hit))}</div>')
                continue
            sev = hit.get("severity", "")
            sev_color, sev_label = _SEVERITY_STYLE.get(sev, ("[[nf:text.muted]]", sev or "未知"))
            k_type = _KB_KNOWLEDGE_TYPE_LABELS.get(
                hit.get("knowledge_type", ""), hit.get("knowledge_type", "")
            )
            vis = _KB_VISIBILITY_LABELS.get(hit.get("visibility", ""), hit.get("visibility", ""))
            entity = hit.get("entity_name", "")
            reason = hit.get("reason", "")
            confidence = hit.get("confidence")
            quote = hit.get("evidence_quote", "")

            parts.append(f'<div class="rule-item" style="border-left-color:{sev_color};">')
            parts.append(
                f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">{_esc(sev_label)}</span> '
            )
            if k_type:
                parts.append(
                    f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(k_type)}</span> '
                )
            if vis:
                parts.append(
                    f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(vis)}</span> '
                )
            if entity:
                parts.append(f"<b>{_esc(entity)}</b>")
            parts.append("</div>")
            if reason:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">{_esc(reason)}</div>'
                )
            if confidence is not None:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"置信度：{_esc(f'{float(confidence):.0%}') if isinstance(confidence, float) and confidence <= 1 else _esc(str(confidence))}</div>"
                )
            if quote:
                parts.append(
                    f'<div style="border-left:2px solid [[nf:text.muted]]; padding-left:8px; color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"{_esc(quote)}</div>"
                )

    # 5. Findings / issues
    display_findings = findings if findings else issues
    if display_findings:
        parts.append(f"<h2>审计发现 · {len(display_findings)} 项</h2>")
        for finding in display_findings:
            if not isinstance(finding, dict):
                parts.append(f'<div class="rule-item">{_esc(str(finding))}</div>')
                continue
            sev = finding.get("severity", "")
            sev_color, sev_label = _SEVERITY_STYLE.get(sev, ("[[nf:text.muted]]", sev or "未知"))
            issue_type = finding.get("issue_type", "")
            type_color, type_label = _KB_VERDICT_LABELS.get(issue_type, ("[[nf:text.muted]]", issue_type))
            summary = finding.get("summary", "")
            quote = finding.get("evidence_quote", "")
            p_start = finding.get("paragraph_start")
            p_end = finding.get("paragraph_end")
            repair_goal = finding.get("repair_goal", "")

            parts.append(f'<div class="rule-item" style="border-left-color:{sev_color};">')
            parts.append(
                f'<div style="margin-bottom:4px;">'
                f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">{_esc(sev_label)}</span> '
            )
            if type_label:
                parts.append(
                    f'<span class="tag" style="color:{type_color}; font-size: 10pt;">{_esc(type_label)}</span> '
                )
            parts.append("</div>")
            if summary:
                parts.append(f'<div style="margin-bottom:4px;">{_esc(summary)}</div>')
            if quote:
                parts.append(
                    f'<div style="border-left:2px solid {sev_color}; padding-left:8px; color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"{_esc(quote)}</div>"
                )
            para_parts: list[str] = []
            if p_start is not None:
                if p_end is not None and p_end != p_start:
                    para_parts.append(f"第 {_esc(str(p_start))}-{_esc(str(p_end))} 段")
                else:
                    para_parts.append(f"第 {_esc(str(p_start))} 段")
            if para_parts:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"{''.join(para_parts)}</div>"
                )
            if repair_goal:
                parts.append(
                    f'<div style="color:[[nf:status.success.warm]]; font-size: 12pt; margin-bottom:3px;">'
                    f"修复方向：{_esc(repair_goal)}</div>"
                )
            parts.append("</div>")

    # 6. Repair tickets
    if repair_tickets:
        parts.append(f"<h2>修复工单 · {len(repair_tickets)} 项</h2>")
        for idx, ticket in enumerate(repair_tickets, 1):
            if not isinstance(ticket, dict):
                parts.append(f'<div class="rule-item">{idx}. {_esc(str(ticket))}</div>')
                continue
            entry_id = ticket.get("entry_id", "")
            action = (
                ticket.get("suggested_action", "")
                or ticket.get("strategy", "")
                or ticket.get("description", "")
                or ticket.get("fix_mode", "")
            )
            parts.append(f'<div class="rule-item">{idx}. ')
            if entry_id:
                parts.append(f"<b>{_esc(str(entry_id))}</b> ")
            if action:
                parts.append(f"{_esc(str(action))}")
            parts.append("</div>")

    # 7. Empty-state copy
    if verdict == "pass" and hidden_count == 0:
        parts.append('<div class="hint-block">本次审计未发现异常 — 隐藏候选为 0，无需裁决。</div>')

    title = f"第 {chapter} 章 · 知识边界审计" if chapter is not None else "知识边界审计"
    return _make_browser(_html_wrap("\n".join(parts), title))

_CAUSAL_ISSUE_TYPE_LABELS: dict[str, str] = {
    "question_resolved_too_early": "悬念提前揭晓",
    "unmotivated_action": "动机不足",
    "missing_consequence": "缺失后果",
    "timeline_inconsistency": "时间线不一致",
    "logic_gap": "逻辑断层",
    "contradiction": "前后矛盾",
    "cause_missing": "因由缺失",
    "effect_missing": "结果缺失",
    "pacing_issue": "节奏问题",
    "foreshadowing_orphan": "埋线未收",
}

def render_causal_report_body_html(data: dict[str, Any]) -> str:
    """Render causal validation report JSON as a formatted HTML body."""
    score = data.get("causal_score")
    summary = data.get("summary", "")
    issues = data.get("issues", [])
    link_verified = data.get("causal_link_verified", None)

    parts: list[str] = []

    # Score header
    if score is not None:
        score_val = float(score)
        color = _score_color(score_val)
        issue_count = len(issues)
        badge_text = "因果链完好" if issue_count == 0 else f"{issue_count} 个问题"
        badge_color = "[[nf:status.success.warm]]" if issue_count == 0 else "[[nf:accent.primary]]"
        link_icon = ""
        if link_verified is True:
            link_icon = ' <span style="color:[[nf:status.success.warm]]; font-size: 12pt;">✓ 承接已核实</span>'
        elif link_verified is False:
            link_icon = ' <span style="color:[[nf:status.danger.deep]]; font-size: 12pt;">✗ 承接未核实</span>'
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f"<div style=\"font-size: 42pt; font-weight:700; font-family:'Songti SC',serif; color:{color};\">{score_val:.1f}</div>"
            f'<div style="margin-top:4px;">'
            f'<span class="tag" style="color:{badge_color};">{badge_text}</span>'
            f"{link_icon}"
            f"</div></div>"
        )

    # Summary
    if summary:
        parts.append(f'<h2>总结</h2><div class="hint-block">{_nl2br(summary)}</div>')

    # Issues
    if issues:
        parts.append(f"<h2>因果问题 · {len(issues)} 项</h2>")
        for issue in issues:
            if not isinstance(issue, dict):
                parts.append(f'<div class="rule-item">{_esc(str(issue))}</div>')
                continue

            severity = issue.get("severity", "")
            sev_color, sev_label = _SEVERITY_STYLE.get(severity, ("[[nf:text.muted]]", severity or "未知"))
            issue_type = issue.get("issue_type", "")
            type_label = _CAUSAL_ISSUE_TYPE_LABELS.get(issue_type, issue_type)
            desc = issue.get("summary", "")
            location = issue.get("location", "")
            evidence = issue.get("evidence", "")
            fix = issue.get("fix_suggestion", "")

            parts.append(f'<div class="rule-item" style="border-left-color:{sev_color};">')
            # Header: severity + type
            parts.append(
                f'<div style="margin-bottom:4px;">'
                f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">{_esc(sev_label)}</span> '
            )
            if type_label:
                parts.append(
                    f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(type_label)}</span> '
                )
            parts.append("</div>")
            # Summary
            if desc:
                parts.append(f'<div style="margin-bottom:4px;">{_esc(desc)}</div>')
            # Location
            if location:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"<b>位置：</b>{_esc(location)}</div>"
                )
            # Evidence
            if evidence:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">'
                    f"<b>依据：</b>{_nl2br(evidence)}</div>"
                )
            # Fix suggestion
            if fix:
                parts.append(
                    f'<div style="color:[[nf:status.success.warm]]; font-size: 12pt; margin-top:4px;">💡 {_esc(fix)}</div>'
                )
            parts.append("</div>")

    return "\n".join(parts)

def render_causal_report(data: dict[str, Any]) -> QTextBrowser:
    """Render causal validation report JSON as a formatted HTML view."""
    return _make_browser(_html_wrap(render_causal_report_body_html(data), "因果链报告"))

def render_creative_report(data: dict[str, Any]) -> QTextBrowser:
    """Render creative summary JSON as a formatted HTML view."""
    parts: list[str] = []

    summary = data.get("structured_summary", "")
    if summary:
        parts.append(
            f'<div class="section" style="padding:16px 20px;"><div style="font-size: 15pt; line-height:1.8; font-family:\'Songti SC\',serif;">{_nl2br(summary)}</div></div>'
        )

    for heading, entries, prefix, item_class in (
        ("创作亮点", data.get("creative_highlights", []), "✦ ", "hint-block"),
        ("必须承接", data.get("must_carry_forward", []), "", "rule-item"),
        ("桥接线索", data.get("bridge_hints", []), "🔗 ", "hint-block"),
    ):
        if entries:
            parts.append(f"<h2>{heading}</h2>")
            for entry in entries:
                parts.append(f'<div class="{item_class}">{prefix}{_esc(str(entry))}</div>')

    suggestion = data.get("suggestions_for_next_chapter", "")
    if suggestion:
        parts.append(f'<h2>下一章建议</h2><div class="hint-block">💡 {_nl2br(suggestion)}</div>')

    deviations = data.get("plot_deviations", [])
    if deviations:
        parts.append(f"<h2>情节偏移 · {len(deviations)} 项</h2>")
        for deviation in deviations:
            if isinstance(deviation, dict):
                outline_plan = _esc(deviation.get("outline_plan", ""))
                actual_plot = _esc(deviation.get("actual_plot", ""))
                level = (deviation.get("deviation_level") or "minor").lower()
                reason = _esc(deviation.get("reason", ""))
                impact = _esc(deviation.get("impact_on_future", ""))
                level_color = {"major": "[[nf:status.danger.deep]]", "moderate": "[[nf:accent.primary]]", "minor": "[[nf:text.muted]]"}.get(
                    level, "[[nf:text.muted]]"
                )
                level_label = {"major": "重大", "moderate": "中等", "minor": "轻微"}.get(
                    level, level
                )
                inner = f'<span style="color:{level_color}; font-weight:600; font-size: 11pt;">[{level_label}]</span>'
                if outline_plan:
                    inner += f'<br/><span style="color:[[nf:text.muted]]; font-size: 12pt;">计划：</span>{outline_plan}'
                if actual_plot:
                    inner += f'<br/><span style="color:[[nf:text.muted]]; font-size: 12pt;">实际：</span>{actual_plot}'
                if reason:
                    inner += (
                        f'<br/><span style="color:[[nf:text.muted]]; font-size: 12pt;">原因：</span>{reason}'
                    )
                if impact:
                    inner += (
                        f'<br/><span style="color:[[nf:text.muted]]; font-size: 12pt;">影响：</span>{impact}'
                    )
                parts.append(
                    f'<div class="rule-item" style="border-left-color:{level_color};">{inner}</div>'
                )
            else:
                # Fallback for plain string deviations
                parts.append(
                    f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]];">{_esc(str(deviation))}</div>'
                )

    new_chars = data.get("new_characters", [])
    if new_chars:
        parts.append(f"<h2>新增角色候选 · {len(new_chars)}</h2>")
        parts.append(
            '<div class="hint-block" style="font-size: 12pt;">'
            "这些条目来自创作报告；只有通过建档裁决后才会写入 CharacterBible。"
            "</div>"
        )
        for character in new_chars:
            if isinstance(character, dict):
                name = str(character.get("name", "未知") or "未知")
                canonical = str(character.get("canonical_name", "") or "").strip()
                role = str(character.get("role_in_story", "") or "")
                importance = str(character.get("importance", "") or "").strip().lower()
                desc = character.get("description", "")
                rels = character.get("relationship_to_existing", {})
                evidence = character.get("evidence", [])
                matched_existing = str(character.get("matched_existing_name", "") or "").strip()
                confidence = _safe_float(character.get("confidence"))
                status_label, status_color = _creative_character_status(character)
                parts.append(f'<div class="rule-item"><strong>{_esc(name)}</strong>')
                parts.append(
                    f' <span class="tag" style="color:{status_color};">{_esc(status_label)}</span>'
                )
                if role:
                    parts.append(f' <span class="tag">{_esc(role)}</span>')
                if importance:
                    importance_label = _CREATIVE_CHARACTER_IMPORTANCE_LABELS.get(
                        importance, importance
                    )
                    parts.append(f' <span class="tag">{_esc(importance_label)}</span>')
                if canonical and canonical != name:
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">规范名：{_esc(canonical)}</span>'
                    )
                if matched_existing:
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">匹配已有角色：{_esc(matched_existing)}</span>'
                    )
                if confidence is not None:
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">置信度：{confidence:.2f}</span>'
                    )
                if desc:
                    parts.append(f"<br>{_esc(desc)}")
                if rels and isinstance(rels, dict):
                    rel_text = "、".join(
                        f"{_esc(key)}→{_esc(value)}" for key, value in rels.items()
                    )
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">关系：{rel_text}</span>'
                    )
                if isinstance(evidence, list) and evidence:
                    evidence_text = "；".join(_esc(str(item)) for item in evidence[:3])
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">证据：{evidence_text}</span>'
                    )
                parts.append("</div>")
            else:
                parts.append(f'<div class="rule-item">{_esc(str(character))}</div>')

    locations = data.get("new_locations", [])
    items = data.get("new_key_items", [])
    if locations or items:
        parts.append("<h2>新增设定</h2>")
        if locations:
            parts.append(
                f'<div class="hint-block">📍 场景：{"、".join(_esc(str(location)) for location in locations)}</div>'
            )
        if items:
            parts.append(
                f'<div class="hint-block">🔑 物件：{"、".join(_esc(str(item)) for item in items)}</div>'
            )

    deltas = data.get("character_state_deltas", [])
    if deltas:
        parts.append(f"<h2>角色状态变化 · {len(deltas)}</h2>")
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            name = delta.get("name", "未知")
            change = delta.get("change_summary", "")
            parts.append(f'<div class="rule-item"><strong>{_esc(name)}</strong>')
            if change:
                parts.append(f"<br>{_esc(change)}")
            to_state = delta.get("to_state", {})
            if isinstance(to_state, dict):
                emotion = to_state.get("emotional", {})
                motivation = to_state.get("motivation", {})
                if isinstance(emotion, dict) and emotion.get("primary_emotion"):
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">情绪：{_esc(emotion["primary_emotion"])}'
                    )
                    if emotion.get("secondary_emotion"):
                        parts.append(f" / {_esc(emotion['secondary_emotion'])}")
                    parts.append("</span>")
                if isinstance(motivation, dict) and motivation.get("short_term_goal"):
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">短期目标：{_esc(motivation["short_term_goal"])}</span>'
                    )
            parts.append("</div>")

    rel_deltas = data.get("relationship_deltas", [])
    if rel_deltas:
        parts.append(f"<h2>关系变化 · {len(rel_deltas)}</h2>")
        for rel_delta in rel_deltas:
            if not isinstance(rel_delta, dict):
                continue
            pair = rel_delta.get("pair_id", "").replace("__", " ↔ ")
            change = rel_delta.get("change_summary", "")
            relationship = rel_delta.get("relationship", {})
            parts.append(f'<div class="rule-item"><strong>{_esc(pair)}</strong>')
            if change:
                parts.append(f"<br>{_esc(change)}")
            if isinstance(relationship, dict):
                bits: list[str] = []
                if relationship.get("trust") is not None:
                    bits.append(f"信任 {float(relationship['trust']):.2f}")
                if relationship.get("tension") is not None:
                    bits.append(f"张力 {float(relationship['tension']):.2f}")
                if bits:
                    parts.append(
                        f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">{" · ".join(bits)}</span>'
                    )
            parts.append("</div>")

    threads = data.get("plot_thread_updates", [])
    if threads:
        parts.append(f"<h2>情节线更新 · {len(threads)}</h2>")
        for update in threads:
            if not isinstance(update, dict):
                continue
            change = update.get("change_summary", "")
            thread = update.get("thread", {})
            title_text = (
                str(thread.get("title", update.get("thread_id", "")))
                if isinstance(thread, dict)
                else ""
            )
            status = thread.get("status", "") if isinstance(thread, dict) else ""
            summary_text = thread.get("summary", "") if isinstance(thread, dict) else ""
            parts.append(f'<div class="rule-item"><strong>{_esc(title_text)}</strong>')
            if status:
                status_color = {
                    "active": "[[nf:status.success.warm]]",
                    "resolved": "[[nf:text.muted]]",
                    "dormant": "[[nf:accent.primary]]",
                }.get(status, "[[nf:text.muted]]")
                parts.append(
                    f' <span class="tag" style="color:{status_color};">{_esc(status)}</span>'
                )
            if change:
                parts.append(f"<br>{_esc(change)}")
            if summary_text:
                parts.append(
                    f'<br><span style="color:[[nf:text.muted]]; font-size: 12pt;">{_esc(summary_text)}</span>'
                )
            parts.append("</div>")

    return _make_browser(_html_wrap("\n".join(parts), "创作总结"))

_URGENCY_STYLE: dict[str, tuple[str, str]] = {
    "critical": ("[[nf:status.danger.deep]]", "紧迫"),
    "high": ("[[nf:accent.primary]]", "高"),
    "medium": ("[[nf:status.success.warm]]", "中"),
    "low": ("[[nf:motif.purple]]", "低"),
}

_SUSPENSE_TYPE_LABELS: dict[str, str] = {
    "question": "疑问",
    "threat": "威胁",
    "secret": "秘密",
    "promise": "承诺",
    "mystery": "谜团",
    "cliffhanger": "悬念",
    "revelation": "揭露",
    "conflict": "冲突",
}

_DEVIATION_TYPE_LABELS: dict[str, str] = {
    "suspense_delay": "悬念延迟",
    "hook_monotony": "钩子单调",
    "tension_mismatch": "张力不匹配",
    "strand_dormancy": "情节线休眠",
}

_SEVERITY_STYLE_EXTENDED: dict[str, tuple[str, str]] = {
    "critical": ("[[nf:status.danger.deep]]", "严重"),
    "warning": ("[[nf:accent.primary]]", "警告"),
    "info": ("[[nf:motif.purple]]", "提示"),
}

_NARRATIVE_PHASE_LABELS: dict[str, str] = {
    "setup": "铺垫",
    "rising_action": "上升动作",
    "climax": "高潮",
    "falling_action": "下降动作",
    "resolution": "收束",
}

_COVERAGE_STRENGTH_LABELS: dict[str, str] = {
    "strong": "强",
    "medium": "中",
    "weak": "弱",
    "none": "无",
}

_SETUP_STRENGTH_LABELS: dict[str, str] = {
    "strong": "强",
    "medium": "中",
    "weak": "弱",
}

_SETUP_PHASE_LABELS: dict[str, str] = {
    "opening": "开篇",
    "middle": "中段",
    "closing": "结尾",
}

def render_reading_power_window_report(data: dict[str, Any]) -> QTextBrowser:
    """Render reading power timeline window report JSON as a formatted HTML view."""
    window_start = data.get("window_start_chapter", "?")
    window_center = data.get("window_center_chapter", "?")
    window_end = data.get("window_end_chapter", "?")

    composite_score = data.get("composite_score")
    timeline_score = data.get("timeline_execution_score")
    suspense_score = data.get("suspense_chain_score")
    strand_score = data.get("strand_balance_score")

    inherited_suspenses = data.get("inherited_suspenses", [])
    resolutions = data.get("suspense_resolutions_in_window", [])
    misses = data.get("suspense_misses_in_window", [])
    execution_verified = data.get("chapter_execution_verified", False)

    deviation_alerts = data.get("deviation_alerts", [])
    next_constraints = data.get("next_chapter_constraints")
    plot_progression = data.get("plot_progression")
    element_summary = data.get("element_progress_summary", {})

    parts: list[str] = []

    # ── Window header ──────────────────────────────────────────────────
    parts.append(
        f'<div class="section" style="text-align:center; padding:16px;">'
        f'<div style="font-size: 14pt; color:[[nf:text.muted]]; margin-bottom:4px;">追读力窗口</div>'
        f"<div style=\"font-size: 26pt; font-weight:700; font-family:'Songti SC',serif; color:[[nf:text.heading]];\">"
        f"第 {window_start} 章 → 第 {window_center} 章 → 第 {window_end} 章</div>"
        f'<div style="margin-top:6px;">'
        f'<span class="tag-muted tag">窗口跨度：{window_end - window_start + 1} 章</span>'
        f'<span class="tag-muted tag">中心章节：第 {window_center} 章</span>'
        f"</div></div>"
    )

    # ── Composite score ────────────────────────────────────────────────
    if composite_score is not None:
        comp_val = float(composite_score)
        comp_color = _score_color(comp_val)
        parts.append(
            f'<div class="section" style="text-align:center; padding:20px;">'
            f'<div style="font-size: 14pt; color:[[nf:text.muted]]; margin-bottom:4px;">追读力窗口综合评分</div>'
            f"<div style=\"font-size: 48pt; font-weight:700; font-family:'Songti SC',serif; color:{comp_color};\">{comp_val:.1f}</div>"
            f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:2px;">/ 10.0</div></div>'
        )

    # ── Sub-scores ─────────────────────────────────────────────────────
    sub_scores: list[tuple[str, float | None]] = [
        ("时间线执行", timeline_score),
        ("悬念链", suspense_score),
        ("情节线平衡", strand_score),
    ]
    has_sub_scores = any(v is not None for _, v in sub_scores)
    if has_sub_scores:
        parts.append("<h2>分项评分</h2>")
        for label, score in sub_scores:
            if score is not None:
                score_val = float(score)
                parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:3px;">'
                    f'<span class="kv-label">{_esc(label)}</span></div>'
                    f"{_score_bar_html(score_val)}"
                    f"</div>"
                )

    # ── Execution verification badge ───────────────────────────────────
    exec_color = "[[nf:status.success.warm]]" if execution_verified else "[[nf:status.danger.deep]]"
    exec_text = "✓ 已验证" if execution_verified else "✗ 未验证"
    parts.append(
        f'<div style="margin:8px 0; text-align:center;">'
        f'<span class="tag" style="color:{exec_color}; font-size: 12pt;">章节执行验证：{exec_text}</span>'
        f"</div>"
    )

    # ── Inherited suspenses grouped by urgency ─────────────────────────
    if inherited_suspenses:
        parts.append(f"<h2>继承悬念 · {len(inherited_suspenses)} 项</h2>")
        by_urgency: dict[str, list[dict[str, Any]]] = {}
        for s in inherited_suspenses:
            if not isinstance(s, dict):
                continue
            urg = s.get("urgency_level", "low")
            by_urgency.setdefault(urg, []).append(s)

        for urg in ("critical", "high", "medium", "low"):
            items = by_urgency.get(urg, [])
            if not items:
                continue
            urg_color, urg_label = _URGENCY_STYLE.get(urg, ("[[nf:text.muted]]", urg))
            parts.append(
                f'<h3><span class="tag" style="color:{urg_color}; font-size: 11pt;">{urg_label}</span> · {len(items)} 项</h3>'
            )
            for s in items:
                sid = s.get("suspense_id", "?")
                s_type = s.get("suspense_type", "")
                type_label = _SUSPENSE_TYPE_LABELS.get(s_type, s_type)
                desc = s.get("suspense_description", "")
                setup_ch = s.get("setup_chapter", "?")
                planned_ch = s.get("planned_resolution_chapter")
                window_start_ch = s.get("resolution_window_start")
                window_end_ch = s.get("resolution_window_end")
                deviation = s.get("resolution_timing_deviation", 0)
                setup_strength = s.get("setup_strength", "")
                setup_strength_label = _SETUP_STRENGTH_LABELS.get(setup_strength, setup_strength)
                setup_phase = s.get("setup_phase", "")
                setup_phase_label = _SETUP_PHASE_LABELS.get(setup_phase, setup_phase)

                parts.append(f'<div class="rule-item" style="border-left-color:{urg_color};">')
                header_parts = [f"<strong>{_esc(sid)}</strong>"]
                if type_label:
                    header_parts.append(
                        f' <span class="tag-muted tag" style="font-size: 10pt;">{_esc(type_label)}</span>'
                    )
                parts.append("".join(header_parts))

                if desc:
                    parts.append(f'<div style="margin:4px 0;">{_esc(desc)}</div>')

                meta_bits: list[str] = []
                meta_bits.append(f"设置：第 {setup_ch} 章")
                if setup_strength_label:
                    meta_bits.append(f"{setup_strength_label}设置")
                if setup_phase_label:
                    meta_bits.append(f"{setup_phase_label}")
                if planned_ch:
                    parts.append(
                        f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:3px;">计划兑现：第 {planned_ch} 章</div>'
                    )
                if window_start_ch is not None and window_end_ch is not None:
                    parts.append(
                        f'<div style="color:[[nf:text.muted]]; font-size: 12pt;">兑现窗口：第 {window_start_ch} 章 → 第 {window_end_ch} 章</div>'
                    )
                if deviation != 0:
                    dev_color = "[[nf:status.danger.deep]]" if deviation > 0 else "[[nf:status.success.warm]]"
                    dev_text = (
                        f"延迟 {deviation} 章" if deviation > 0 else f"提前 {abs(deviation)} 章"
                    )
                    parts.append(
                        f'<div style="color:{dev_color}; font-size: 12pt;">时机偏差：{dev_text}</div>'
                    )

                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:2px;">{" · ".join(meta_bits)}</div>'
                )
                parts.append("</div>")

    # ── Suspense resolutions in window ─────────────────────────────────
    if resolutions:
        parts.append(f"<h2>窗口内已兑现悬念 · {len(resolutions)} 项</h2>")
        for s in resolutions:
            if not isinstance(s, dict):
                continue
            sid = s.get("suspense_id", "?")
            desc = s.get("suspense_description", "")
            deviation = s.get("resolution_timing_deviation", 0)
            dev_color = "[[nf:status.success.warm]]" if deviation == 0 else ("[[nf:accent.primary]]" if deviation > 0 else "[[nf:motif.purple]]")
            dev_text = (
                "准时"
                if deviation == 0
                else (f"延迟 {deviation} 章" if deviation > 0 else f"提前 {abs(deviation)} 章")
            )

            parts.append(
                f'<div class="theme-item">'
                f"<strong>{_esc(sid)}</strong>"
                + (f" — {_esc(desc)}" if desc else "")
                + f'<br><span style="color:{dev_color}; font-size: 12pt;">兑现时机：{dev_text}</span>'
                f"</div>"
            )

    # ── Suspense misses in window ──────────────────────────────────────
    if misses:
        parts.append(f"<h2>错过兑现窗口 · {len(misses)} 项</h2>")
        for s in misses:
            if not isinstance(s, dict):
                continue
            sid = s.get("suspense_id", "?")
            desc = s.get("suspense_description", "")
            deviation = s.get("resolution_timing_deviation", 0)

            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:status.danger.deep]];">'
                f"<strong>{_esc(sid)}</strong>"
                + (f" — {_esc(desc)}" if desc else "")
                + (
                    f'<br><span style="color:[[nf:status.danger.deep]]; font-size: 12pt;">时机偏差：延迟 {deviation} 章</span>'
                    if deviation > 0
                    else ""
                )
                + "</div>"
            )

    # ── Deviation alerts ───────────────────────────────────────────────
    if deviation_alerts:
        parts.append(f"<h2>偏离预警 · {len(deviation_alerts)} 项</h2>")
        for alert in deviation_alerts:
            if not isinstance(alert, dict):
                continue
            severity = alert.get("severity", "")
            sev_color, sev_label = _SEVERITY_STYLE_EXTENDED.get(
                severity, ("[[nf:text.muted]]", severity or "未知")
            )
            alert_type = alert.get("alert_type", "")
            type_label = _DEVIATION_TYPE_LABELS.get(alert_type, alert_type)
            summary = alert.get("deviation_summary", "")
            planned = alert.get("planned_value")
            actual = alert.get("actual_value")
            correction = alert.get("correction_suggestion", "")

            parts.append(f'<div class="rule-item" style="border-left-color:{sev_color};">')
            parts.append(
                f'<div style="margin-bottom:4px;">'
                f'<span class="tag" style="color:{sev_color}; font-size: 10pt;">{sev_label}</span> '
            )
            if type_label:
                parts.append(
                    f'<span class="tag-muted tag" style="font-size: 10pt;">{_esc(type_label)}</span>'
                )
            parts.append("</div>")

            if summary:
                parts.append(f'<div style="margin-bottom:4px;">{_esc(summary)}</div>')

            if planned is not None or actual is not None:
                progress_bits: list[str] = []
                if planned is not None:
                    progress_bits.append(f"计划：{_esc(str(planned))}")
                if actual is not None:
                    progress_bits.append(f"实际：{_esc(str(actual))}")
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-bottom:3px;">{" · ".join(progress_bits)}</div>'
                )

            if correction:
                parts.append(
                    f'<div style="color:[[nf:status.success.warm]]; font-size: 12pt; margin-top:4px;">💡 {_esc(correction)}</div>'
                )
            parts.append("</div>")

    # ── Plot progression ───────────────────────────────────────────────
    if plot_progression and isinstance(plot_progression, dict):
        narrative_phase = plot_progression.get("current_narrative_phase", "")
        phase_progress = plot_progression.get("phase_progress")
        turning_covered = plot_progression.get("turning_points_covered", 0)
        turning_expected = plot_progression.get("turning_points_expected", 0)
        dormant_alerts = plot_progression.get("dormant_strand_alerts", [])

        parts.append("<h2>叙事推进</h2>")

        if narrative_phase:
            phase_label = _NARRATIVE_PHASE_LABELS.get(narrative_phase, narrative_phase)
            phase_badge = f'<span class="tag">{_esc(phase_label)}</span>'
            if phase_progress is not None:
                phase_badge += f' <span class="tag-muted tag">{float(phase_progress):.0%}</span>'
            parts.append(f'<div style="margin:6px 0;">{phase_badge}</div>')

        if turning_expected > 0:
            tp_color = "[[nf:status.success.warm]]" if turning_covered >= turning_expected else "[[nf:accent.primary]]"
            parts.append(
                f'<div style="color:{tp_color}; font-size: 13pt; margin:4px 0;">'
                f"转折点：{turning_covered}/{turning_expected}"
                f"</div>"
            )

        main_plot = plot_progression.get("main_plot_progression", [])
        if main_plot:
            parts.append(f"<h3>主线推进 · {len(main_plot)} 项</h3>")
            for mp in main_plot:
                if not isinstance(mp, dict):
                    continue
                plot_point = mp.get("plot_point", "")
                covered = mp.get("covered", False)
                strength = mp.get("coverage_strength", "none")
                strength_label = _COVERAGE_STRENGTH_LABELS.get(strength, strength)
                cov_color = "[[nf:status.success.warm]]" if covered else "[[nf:text.muted]]"
                cov_icon = "✓" if covered else "○"

                parts.append(
                    f'<div class="kv-row" style="margin:3px 0;">'
                    f'<span style="color:{cov_color};">{cov_icon}</span> '
                    f'<span class="kv-value">{_esc(plot_point)}</span>'
                    f' <span class="tag-muted tag" style="font-size: 10pt;">{strength_label}</span>'
                    f"</div>"
                )

        subplots = plot_progression.get("subplot_progression", [])
        if subplots:
            parts.append(f"<h3>支线推进 · {len(subplots)} 项</h3>")
            for sp in subplots:
                if not isinstance(sp, dict):
                    continue
                sp_id = sp.get("subplot_id", "?")
                sp_name = sp.get("subplot_name", "")
                active = sp.get("active_this_chapter", False)
                dormant = sp.get("dormant_chapters", 0)
                status_color = "[[nf:status.success.warm]]" if active else ("[[nf:accent.primary]]" if dormant > 2 else "[[nf:text.muted]]")
                status_text = (
                    "活跃" if active else (f"休眠 {dormant} 章" if dormant > 0 else "未活跃")
                )

                parts.append(
                    f'<div class="kv-row" style="margin:3px 0;">'
                    f'<span style="color:{status_color};">●</span> '
                    f'<span class="kv-value">{_esc(sp_name or sp_id)}</span>'
                    f' <span style="color:{status_color}; font-size: 11pt;">{status_text}</span>'
                    f"</div>"
                )

        strands = plot_progression.get("strand_activities", [])
        if strands:
            parts.append(f"<h3>情节线活动 · {len(strands)} 条</h3>")
            for sa in strands:
                if not isinstance(sa, dict):
                    continue
                strand_type = sa.get("strand_type", "?")
                planned_intensity = sa.get("planned_intensity", 0)
                actual_intensity = sa.get("actual_intensity", 0)
                dormant_since = sa.get("dormant_since", 0)

                intensity_diff = abs(float(actual_intensity) - float(planned_intensity))
                diff_color = (
                    "[[nf:status.success.warm]]"
                    if intensity_diff <= 2
                    else ("[[nf:accent.primary]]" if intensity_diff <= 4 else "[[nf:status.danger.deep]]")
                )

                parts.append(
                    f'<div style="margin:6px 0;">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:2px;">'
                    f'<span class="kv-label">{_esc(strand_type)}</span>'
                    f'<span style="font-size: 11pt; color:{diff_color};">计划 {float(planned_intensity):.1f} / 实际 {float(actual_intensity):.1f}</span>'
                    f"</div>"
                    f"{_score_bar_html(float(actual_intensity))}"
                    + (
                        f'<div style="color:[[nf:text.muted]]; font-size: 11pt; margin-top:2px;">休眠 {dormant_since} 章</div>'
                        if dormant_since > 0
                        else ""
                    )
                    + "</div>"
                )

        if dormant_alerts:
            parts.append(
                '<div style="margin:8px 0;">'
                '<span class="tag" style="color:[[nf:accent.primary]];">⚠ 休眠预警</span>'
                '<div style="color:[[nf:text.muted]]; font-size: 12pt; margin-top:4px;">'
                + "、".join(f"{_esc(a)}" for a in dormant_alerts)
                + "</div></div>"
            )

    # ── Element progress summary ───────────────────────────────────────
    if element_summary:
        parts.append(f"<h2>要素执行摘要 · {len(element_summary)} 项</h2>")
        for elem_id, status in element_summary.items():
            status_lower = (status or "").lower()
            status_color = {"hit": "[[nf:status.success.warm]]", "weak": "[[nf:accent.primary]]", "miss": "[[nf:status.danger.deep]]"}.get(
                status_lower, "[[nf:text.muted]]"
            )
            status_label = {"hit": "命中", "weak": "薄弱", "miss": "缺失"}.get(status_lower, status)
            parts.append(
                f'<div class="kv-row" style="margin:3px 0;">'
                f'<span style="color:{status_color};">●</span> '
                f'<span class="kv-value">{_esc(elem_id)}</span>'
                f' <span class="tag" style="color:{status_color}; font-size: 10pt;">{status_label}</span>'
                f"</div>"
            )

    # ── Next chapter constraints ───────────────────────────────────────
    if next_constraints and isinstance(next_constraints, dict):
        parts.append("<h2>下一章约束建议</h2>")
        parts.append('<div class="section">')

        suspense_to_resolve = next_constraints.get("suspense_ids_to_resolve", [])
        if suspense_to_resolve:
            parts.append(
                f'<div class="kv-row"><span class="kv-label">需兑现悬念：</span>'
                f'<span class="kv-value">{_esc("、".join(suspense_to_resolve))}</span></div>'
            )

        expected_hook = next_constraints.get("expected_hook_type")
        if expected_hook:
            parts.append(
                f'<div class="kv-row" style="margin-top:4px;"><span class="kv-label">建议钩子类型：</span>'
                f'<span class="tag">{_esc(expected_hook)}</span></div>'
            )

        tension_target = next_constraints.get("tension_adjustment_target")
        if tension_target is not None:
            tension_val = float(tension_target)
            parts.append(
                f'<div class="kv-row" style="margin-top:4px;"><span class="kv-label">张力调整目标：</span>'
                f"{_score_bar_html(tension_val)}</div>"
            )

        subplots_to_activate = next_constraints.get("subplots_to_activate", [])
        if subplots_to_activate:
            parts.append(
                f'<div class="kv-row" style="margin-top:4px;"><span class="kv-label">建议激活支线：</span>'
                f'<span class="kv-value">{_esc("、".join(subplots_to_activate))}</span></div>'
            )

        element_recs = next_constraints.get("element_focus_recommendation", [])
        if element_recs:
            parts.append(
                f'<div class="kv-row" style="margin-top:4px;"><span class="kv-label">重点关注要素：</span>'
                f'<span class="kv-value">{_esc("、".join(element_recs))}</span></div>'
            )

        strand_hint = next_constraints.get("strand_distribution_hint", {})
        if strand_hint:
            parts.append(
                '<div class="kv-row" style="margin-top:6px;"><span class="kv-label">情节线分布建议：</span></div>'
            )
            for strand_type, ratio in strand_hint.items():
                ratio_val = float(ratio)
                parts.append(
                    f'<div style="margin:3px 0 3px 12px;">'
                    f'<span class="kv-label" style="font-size: 11pt;">{_esc(strand_type)}</span> '
                    f'<span style="font-size: 11pt; color:[[nf:text.muted]];">{ratio_val:.0%}</span>'
                    f"</div>"
                )

        parts.append("</div>")

    return _make_browser(_html_wrap("\n".join(parts), "追读力窗口报告"))


# ── Web research report renderers ──────────────────────────────────────────

_RESEARCH_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "succeeded": ("[[nf:status.success.warm]]", "✓ 成功"),
    "partial": ("[[nf:accent.primary]]", "△ 部分成功"),
    "skipped": ("[[nf:text.muted]]", "— 已跳过"),
    "failed": ("[[nf:status.danger.deep]]", "✗ 失败"),
}

_PRIORITY_STYLE: dict[str, tuple[str, str]] = {
    "must": ("[[nf:status.danger.deep]]", "必须"),
    "should": ("[[nf:accent.primary]]", "建议"),
    "could": ("[[nf:text.muted]]", "可选"),
}


def render_web_research_report(data: dict[str, Any]) -> QTextBrowser:
    """Render init_web_research.json as a structured research report."""
    status = str(data.get("status", "unknown")).lower()
    provider = _report_text(data.get("provider"), "unknown")
    status_color, status_label = _RESEARCH_STATUS_STYLE.get(status, ("[[nf:text.muted]]", status))
    created_at = _report_text(data.get("created_at"))
    llm_planned = data.get("llm_planned", False)

    parts: list[str] = []

    # ── Header badges ────────────────────────────────────────────────────────
    badges = [
        _tag(f"状态：{status_label}", tone="accent" if status == "succeeded" else "muted"),
        _tag(f"检索后端：{provider}", tone="muted"),
    ]
    if llm_planned:
        badges.append(_tag("LLM 规划查询", tone="muted"))
    if created_at:
        badges.append(_tag(created_at[:19].replace("T", " "), tone="muted"))
    parts.append(f'<div style="margin:2px 0 14px 0;">{".".join(badges)}</div>')

    # ── Config summary ───────────────────────────────────────────────────────
    config = data.get("config") or {}
    if config:
        max_queries = config.get("max_queries")
        results_per_query = config.get("results_per_query")
        search_depth = config.get("search_depth")
        requested_provider = config.get("requested_provider")
        resolved_provider = config.get("resolved_provider")
        cfg_items = []
        if requested_provider:
            cfg_items.append(f"请求：{_esc(str(requested_provider))}")
        if resolved_provider and resolved_provider != requested_provider:
            cfg_items.append(f"解析：{_esc(str(resolved_provider))}")
        if max_queries:
            cfg_items.append(f"最大查询数：{_esc(str(max_queries))}")
        if results_per_query:
            cfg_items.append(f"每查询结果数：{_esc(str(results_per_query))}")
        if search_depth:
            cfg_items.append(f"检索深度：{_esc(str(search_depth))}")
        if cfg_items:
            parts.append(
                '<div class="section" style="padding:8px 12px;">'
                '<div style="color:[[nf:text.muted]]; font-size:11pt;">'
                + " · ".join(cfg_items)
                + "</div></div>"
            )

    # ── Queries ──────────────────────────────────────────────────────────────
    queries = _report_list(data.get("queries"))
    if queries:
        parts.append(f"<h2>查询计划 · {len(queries)} 条</h2>")
        for idx, q in enumerate(queries, 1):
            if not isinstance(q, dict):
                continue
            query_text = _report_text(q.get("query"), "（无查询词）")
            rationale = _report_text(q.get("rationale"))
            intent = _report_text(q.get("intent"))
            priority = str(q.get("priority", "")).lower()
            prio_color, prio_label = _PRIORITY_STYLE.get(priority, ("[[nf:text.muted]]", priority or "—"))
            risk = _report_text(q.get("risk_if_missing"))
            locale = _report_text(q.get("locale"))
            source_prefs = _report_list(q.get("source_preferences"))

            parts.append(
                f'<div class="section" style="border-left:3px solid {prio_color}; padding:10px 14px;">'
            )
            parts.append(
                f'<div style="display:flex; align-items:baseline; gap:8px; margin-bottom:4px;">'
                f'<span style="font-weight:600; color:[[nf:text.tab.hover]]; font-size:12pt;">{idx}. {_esc(query_text)}</span>'
                f'<span class="tag" style="color:{prio_color};">{prio_label}</span>'
                f"</div>"
            )
            if intent:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size:11pt; margin-bottom:3px;">'
                    f"目的：{_esc(intent)}</div>"
                )
            if rationale:
                parts.append(
                    f'<div style="color:[[nf:text.tab.hover]]; font-size:12pt; line-height:1.6;">'
                    f"{_nl2br(rationale)}</div>"
                )
            meta_parts = []
            if locale:
                meta_parts.append(f"语言：{_esc(locale)}")
            if source_prefs:
                meta_parts.append(f"来源偏好：{_esc('、'.join(str(p) for p in source_prefs))}")
            if risk:
                meta_parts.append(f"缺失风险：{_esc(risk)}")
            if meta_parts:
                parts.append(
                    '<div style="color:[[nf:text.muted]]; font-size:11pt; margin-top:4px; line-height:1.6;">'
                    + "<br>".join(meta_parts)
                    + "</div>"
                )
            parts.append("</div>")

    # ── Sources ──────────────────────────────────────────────────────────────
    sources = _report_list(data.get("sources"))
    if sources:
        parts.append(f"<h2>检索来源 · {len(sources)} 条</h2>")
        for idx, src in enumerate(sources, 1):
            if not isinstance(src, dict):
                continue
            title = _report_text(src.get("title"), "（无标题）")
            url = _report_text(src.get("url"))
            snippet = _report_text(src.get("snippet"))
            score = src.get("score")

            parts.append(
                '<div class="section" style="padding:8px 14px; border-left:2px solid [[nf:accent.primary]];">'
            )
            if url:
                parts.append(
                    f'<div style="font-weight:600; color:[[nf:text.tab.hover]]; font-size:12pt; margin-bottom:2px;">'
                    f'{idx}. <a href="{_esc(url)}" style="color:[[nf:accent.primary]];">{_esc(title)}</a></div>'
                )
            else:
                parts.append(
                    f'<div style="font-weight:600; color:[[nf:text.tab.hover]]; font-size:12pt; margin-bottom:2px;">'
                    f"{idx}. {_esc(title)}</div>"
                )
            if url:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size:10pt; word-break:break-all; margin-bottom:3px;">'
                    f"{_esc(url)}</div>"
                )
            if snippet:
                parts.append(
                    f'<div style="color:[[nf:text.tab.hover]]; font-size:12pt; line-height:1.6;">'
                    f"{_nl2br(snippet)}</div>"
                )
            if score is not None:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size:10pt; margin-top:2px;">'
                    f"相关度：{_esc(str(score))}</div>"
                )
            parts.append("</div>")

    # ── Brief / summary ──────────────────────────────────────────────────────
    brief = data.get("brief") or {}
    brief_summary = _report_text(brief.get("summary"))
    source_notes = _report_list(brief.get("source_notes"))
    if brief_summary or source_notes:
        parts.append("<h2>资料摘要</h2>")
        parts.append('<div class="section" style="padding:10px 14px;">')
        if brief_summary:
            parts.append(
                f'<div style="color:[[nf:text.tab.hover]]; font-size:12pt; line-height:1.7;">'
                f"{_nl2br(brief_summary)}</div>"
            )
        if source_notes:
            parts.append(
                '<div style="margin-top:10px; color:[[nf:text.muted]]; font-size:11pt;">来源备注：</div>'
            )
            for note in source_notes:
                parts.append(
                    f'<div class="rule-item" style="color:[[nf:text.tab.hover]]; font-size:12pt;">'
                    f"{_nl2br(str(note))}</div>"
                )
        parts.append("</div>")

    # ── Knowledge gaps ───────────────────────────────────────────────────────
    knowledge_gaps = _report_list(data.get("knowledge_gaps"))
    if knowledge_gaps:
        parts.append(f"<h2>知识空白 · {len(knowledge_gaps)} 项</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for gap in knowledge_gaps:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]]; color:[[nf:text.tab.hover]];">'
                f"{_esc(str(gap))}</div>"
            )
        parts.append("</div>")

    # ── Warnings ─────────────────────────────────────────────────────────────
    warnings = _report_list(data.get("warnings"))
    query_plan_warnings = _report_list(data.get("query_plan_warnings"))
    all_warnings = warnings + query_plan_warnings
    if all_warnings:
        parts.append(f"<h2>警告 · {len(all_warnings)} 条</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for w in all_warnings:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]]; color:[[nf:text.tab.hover]];">'
                f"{_nl2br(str(w))}</div>"
            )
        parts.append("</div>")

    # ── Model prior ──────────────────────────────────────────────────────────
    model_prior = data.get("model_prior") or {}
    if model_prior.get("enabled"):
        prior_status = _report_text(model_prior.get("status"))
        prior_notes = _report_list(model_prior.get("notes"))
        prior_terms = _report_list(model_prior.get("terminology"))
        prior_uncertainty = _report_list(model_prior.get("uncertainty_notes"))
        prior_warnings = _report_list(model_prior.get("warnings"))

        parts.append("<h2>模型先验补充</h2>")
        badges = [_tag(f"状态：{prior_status}", tone="muted")]
        parts.append(f'<div style="margin-bottom:8px;">{".".join(badges)}</div>')
        parts.append('<div class="section" style="padding:10px 14px;">')

        if prior_notes:
            parts.append(
                '<div style="color:[[nf:text.muted]]; font-size:11pt; margin-bottom:4px;">知识备注：</div>'
            )
            for note in prior_notes:
                parts.append(
                    f'<div class="rule-item" style="color:[[nf:text.tab.hover]]; font-size:12pt;">'
                    f"{_nl2br(str(note))}</div>"
                )

        if prior_terms:
            parts.append(
                '<div style="color:[[nf:text.muted]]; font-size:11pt; margin-top:8px; margin-bottom:4px;">'
                "术语：</div>"
            )
            parts.append(
                '<div style="display:flex; flex-wrap:wrap; gap:4px;">'
                + "".join(f'{_tag(str(t), tone="muted")}' for t in prior_terms)
                + "</div>"
            )

        if prior_uncertainty:
            parts.append(
                '<div style="color:[[nf:text.muted]]; font-size:11pt; margin-top:8px; margin-bottom:4px;">'
                "不确定性说明：</div>"
            )
            for u in prior_uncertainty:
                parts.append(
                    f'<div class="rule-item" style="color:[[nf:text.muted]]; font-size:12pt;">'
                    f"{_nl2br(str(u))}</div>"
                )

        if prior_warnings:
            parts.append(
                '<div style="color:[[nf:accent.primary]]; font-size:11pt; margin-top:8px; margin-bottom:4px;">'
                "警告：</div>"
            )
            for w in prior_warnings:
                parts.append(
                    f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]]; color:[[nf:text.tab.hover]];">'
                    f"{_nl2br(str(w))}</div>"
                )

        parts.append("</div>")

    return _make_browser(_html_wrap("\n".join(parts), "资料检索报告"))


def render_research_dossier_report(data: dict[str, Any]) -> QTextBrowser:
    """Render init_research_dossier.json as a compressed research dossier."""
    status = str(data.get("status", "unknown")).lower()
    provider = _report_text(data.get("provider"), "unknown")
    status_color, status_label = _RESEARCH_STATUS_STYLE.get(status, ("[[nf:text.muted]]", status))
    created_at = _report_text(data.get("created_at"))

    parts: list[str] = []

    # ── Header badges ────────────────────────────────────────────────────────
    badges = [
        _tag(f"状态：{status_label}", tone="accent" if status == "succeeded" else "muted"),
        _tag(f"来源后端：{provider}", tone="muted"),
    ]
    if created_at:
        badges.append(_tag(created_at[:19].replace("T", " "), tone="muted"))
    parts.append(f'<div style="margin:2px 0 14px 0;">{".".join(badges)}</div>')

    # ── Summary ──────────────────────────────────────────────────────────────
    summary = _report_text(data.get("summary"))
    if summary:
        parts.append(
            '<div class="section" style="padding:12px 16px; border-left:3px solid [[nf:status.success.warm]];">'
            f'<div style="color:[[nf:text.tab.hover]]; font-size:13pt; line-height:1.7;">'
            f"{_nl2br(summary)}</div></div>"
        )

    # ── Real-world constraints ───────────────────────────────────────────────
    constraints = _report_list(data.get("real_world_constraints"))
    if constraints:
        parts.append(f"<h2>现实约束 · {len(constraints)} 条</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for c in constraints:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]]; color:[[nf:text.tab.hover]];">'
                f"{_nl2br(str(c))}</div>"
            )
        parts.append("</div>")

    # ── Terminology ──────────────────────────────────────────────────────────
    terminology = _report_list(data.get("terminology"))
    if terminology:
        parts.append(f"<h2>术语表 · {len(terminology)} 项</h2>")
        parts.append(
            '<div class="section" style="padding:8px 14px;">'
            '<div style="display:flex; flex-wrap:wrap; gap:5px;">'
            + "".join(f'{_tag(str(t), tone="muted")}' for t in terminology)
            + "</div></div>"
        )

    # ── Inspiration notes ────────────────────────────────────────────────────
    inspiration_notes = _report_list(data.get("inspiration_notes"))
    if inspiration_notes:
        parts.append(f"<h2>创作灵感 · {len(inspiration_notes)} 条</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for note in inspiration_notes:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:status.success.warm]]; color:[[nf:text.tab.hover]];">'
                f"{_nl2br(str(note))}</div>"
            )
        parts.append("</div>")

    # ── Uncertainty notes ────────────────────────────────────────────────────
    uncertainty_notes = _report_list(data.get("uncertainty_notes"))
    if uncertainty_notes:
        parts.append(f"<h2>不确定性说明 · {len(uncertainty_notes)} 条</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for u in uncertainty_notes:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:text.muted]]; color:[[nf:text.tab.hover]];">'
                f"{_nl2br(str(u))}</div>"
            )
        parts.append("</div>")

    # ── Source refs ──────────────────────────────────────────────────────────
    source_refs = _report_list(data.get("source_refs"))
    if source_refs:
        parts.append(f"<h2>引用来源 · {len(source_refs)} 条</h2>")
        for idx, ref in enumerate(source_refs, 1):
            if not isinstance(ref, dict):
                parts.append(
                    f'<div class="rule-item">{idx}. {_esc(str(ref))}</div>'
                )
                continue
            title = _report_text(ref.get("title"), "（无标题）")
            url = _report_text(ref.get("url"))
            snippet = _report_text(ref.get("snippet"))
            parts.append(
                '<div class="section" style="padding:6px 14px; border-left:2px solid [[nf:text.muted]];">'
            )
            if url:
                parts.append(
                    f'<div style="font-weight:600; color:[[nf:text.tab.hover]]; font-size:12pt;">'
                    f'{idx}. <a href="{_esc(url)}" style="color:[[nf:accent.primary]];">{_esc(title)}</a></div>'
                )
            else:
                parts.append(
                    f'<div style="font-weight:600; color:[[nf:text.tab.hover]]; font-size:12pt;">'
                    f"{idx}. {_esc(title)}</div>"
                )
            if url:
                parts.append(
                    f'<div style="color:[[nf:text.muted]]; font-size:10pt; word-break:break-all;">'
                    f"{_esc(url)}</div>"
                )
            if snippet:
                parts.append(
                    f'<div style="color:[[nf:text.tab.hover]]; font-size:12pt; margin-top:2px;">'
                    f"{_nl2br(snippet)}</div>"
                )
            parts.append("</div>")

    # ── Model prior notes ────────────────────────────────────────────────────
    model_prior_notes = _report_list(data.get("model_prior_notes"))
    model_prior_terms = _report_list(data.get("model_prior_terminology"))
    model_prior_uncertainty = _report_list(data.get("model_prior_uncertainty_notes"))
    if model_prior_notes or model_prior_terms or model_prior_uncertainty:
        parts.append("<h2>模型先验补充</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        if model_prior_notes:
            for note in model_prior_notes:
                parts.append(
                    f'<div class="rule-item" style="color:[[nf:text.tab.hover]]; font-size:12pt;">'
                    f"{_nl2br(str(note))}</div>"
                )
        if model_prior_terms:
            parts.append(
                '<div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:4px;">'
                + "".join(f'{_tag(str(t), tone="muted")}' for t in model_prior_terms)
                + "</div>"
            )
        if model_prior_uncertainty:
            parts.append(
                '<div style="color:[[nf:text.muted]]; font-size:11pt; margin-top:8px; margin-bottom:4px;">'
                "不确定性：</div>"
            )
            for u in model_prior_uncertainty:
                parts.append(
                    f'<div class="rule-item" style="border-left-color:[[nf:text.muted]]; color:[[nf:text.muted]];">'
                    f"{_nl2br(str(u))}</div>"
                )
        parts.append("</div>")

    # ── Warnings ─────────────────────────────────────────────────────────────
    warnings = _report_list(data.get("warnings"))
    if warnings:
        parts.append(f"<h2>警告 · {len(warnings)} 条</h2>")
        parts.append('<div class="section" style="padding:8px 14px;">')
        for w in warnings:
            parts.append(
                f'<div class="rule-item" style="border-left-color:[[nf:accent.primary]]; color:[[nf:text.tab.hover]];">'
                f"{_nl2br(str(w))}</div>"
            )
        parts.append("</div>")

    return _make_browser(_html_wrap("\n".join(parts), "资料分析报告"))


# M3.4: re-export extracted render_* functions from sub-modules.
from novel_forge.desktop.pages.document_renderer.reports.evaluation import (  # noqa: E402
    render_eval_report,  # noqa: E402, F401, F811
    render_eval_report_body_html,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.reports.generic_report import (  # noqa: E402, F401  # noqa: E402
    _generic_is_empty,
    _generic_is_scalar,
    _generic_item_title,
    _generic_list_items,
    _generic_metric,
    _generic_render_dict,
    _generic_render_value,
    _generic_scalar_html,
    _generic_tag,
    render_generic_report,  # noqa: E402, F401, F811
    render_generic_report_body_html,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.reports.humanize import (  # noqa: E402
    render_humanize_report,  # noqa: E402, F401, F811
    render_humanize_report_body_html,  # noqa: E402, F401, F811
)
from novel_forge.desktop.pages.document_renderer.reports.spec import (  # noqa: E402
    render_spec,  # noqa: E402, F401, F811
    render_story_bible,  # noqa: E402, F401, F811
)
