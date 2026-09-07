"""Domain profiles for the unified audit architecture.

The audit layer is intentionally shared, but each dimension keeps its own
diagnostic focus, repair lane, gate policy, and acceptance conditions here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditPostconditionSpec:
    """Lightweight postcondition template for one issue type."""

    validator_id: str
    description: str
    evidence_hint: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "description": self.description,
            "evidence_hint": self.evidence_hint,
            "required": self.required,
        }


@dataclass(frozen=True)
class AuditIssueTypeProfile:
    """Dimension-specific behavior for a known issue type."""

    repair_surface: str = ""
    repair_focus: str = ""
    postconditions: tuple[AuditPostconditionSpec, ...] = ()


@dataclass(frozen=True)
class AuditRecheckPolicy:
    """How a dimension should behave during post-repair verification."""

    strategy: str = "targeted_with_global_guard"
    target_first: bool = True
    allow_new_issue_scan: bool = True
    new_issue_severities: tuple[str, ...] = ("critical", "high")
    scan_scope: str = "只检查修复触及区域及其直接结构邻接关系。"
    solved_issue_rule: str = "已修复的问题不要再次输出到 issues。"
    unresolved_issue_rule: str = "未修复的问题必须沿用原 issue_id，并说明未满足的验收条件。"
    regression_rule: str = "仅报告修复引入的 critical/high 级结构性回归。"

    def to_prompt_context(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "target_first": self.target_first,
            "allow_new_issue_scan": self.allow_new_issue_scan,
            "new_issue_severities": list(self.new_issue_severities),
            "scan_scope": self.scan_scope,
            "solved_issue_rule": self.solved_issue_rule,
            "unresolved_issue_rule": self.unresolved_issue_rule,
            "regression_rule": self.regression_rule,
        }


@dataclass(frozen=True)
class AuditDimensionProfile:
    """Review dimension profile used by gates, ledgers, and repair planning."""

    dimension: str
    namespace: str
    score_attr: str
    score_label: str
    threshold: float
    hard_severities: tuple[str, ...] = ("critical",)
    default_repair_surface: str = "chapter_text"
    repair_lane: str = "text_repair"
    reviewer_focus: str = ""
    recheck_policy: AuditRecheckPolicy = field(default_factory=AuditRecheckPolicy)
    issue_types: dict[str, AuditIssueTypeProfile] = field(default_factory=dict)

    def issue_profile(self, issue_type: str) -> AuditIssueTypeProfile:
        return self.issue_types.get(str(issue_type or "").strip().lower(), AuditIssueTypeProfile())


def _pc(
    validator_id: str,
    description: str,
    evidence_hint: str = "",
) -> AuditPostconditionSpec:
    return AuditPostconditionSpec(
        validator_id=validator_id,
        description=description,
        evidence_hint=evidence_hint,
    )


_TARGETED_RECHECK = AuditRecheckPolicy()
_STATE_RECHECK = AuditRecheckPolicy(
    scan_scope="只核验状态裁判指出的候选 delta、权威状态投影和正文证据链。",
    regression_rule="仅报告会继续阻断归档的状态冲突或状态投影回退。",
)
_GUARD_RECHECK = AuditRecheckPolicy(
    scan_scope="只核验本轮护栏约束及其原证据位置。",
    solved_issue_rule="护栏约束已可由正文证据确认时，不要重复输出。",
    regression_rule="仅报告新出现的 hard-forbidden 违约或必须项缺失。",
)
_KNOWLEDGE_BOUNDARY_RECHECK = AuditRecheckPolicy(
    scan_scope="只核验知识边界审计指出的正文证据位置及其相邻段落。",
    solved_issue_rule="原证据位置不再表达越界知识时，不要重复输出。",
    regression_rule="仅报告修复引入的新高置信知识泄漏、提前揭示或无证据获知。",
)


AUDIT_PROFILES: dict[str, AuditDimensionProfile] = {
    "continuity": AuditDimensionProfile(
        dimension="continuity",
        namespace="cont",
        score_attr="continuity_score",
        score_label="连续性分",
        threshold=6.0,
        hard_severities=("critical",),
        default_repair_surface="chapter_text",
        repair_lane="continuity_repair",
        reviewer_focus="跨章桥接、上一章出口、本章开场承接、状态/地点/POV 合同。",
        recheck_policy=_TARGETED_RECHECK,
        issue_types={
            "opening_gap": AuditIssueTypeProfile(
                repair_focus="补足开场承接锚点，不改变既定 POV 和桥接事实。",
                postconditions=(
                    _pc(
                        "opening_transition_validator",
                        "开头窗口自然落地上一章出口与 bridge/action_handoff 锚点。",
                        "opening paragraphs + bridge.action_handoff",
                    ),
                ),
            ),
            "carry_forward_missing": AuditIssueTypeProfile(
                repair_focus="补足必须延续的人物状态、情绪或行动交接。",
                postconditions=(
                    _pc(
                        "carry_forward_validator",
                        "正文明确承接 must_carry_forward 中的关键状态或行动。",
                        "chapter_state_packet.must_carry_forward",
                    ),
                ),
            ),
            "location_jump": AuditIssueTypeProfile(
                repair_focus="解释地点变化或确认合法 POV/镜头切换。",
                postconditions=(
                    _pc(
                        "location_transition_validator",
                        "地点变化有桥接、镜头或 POV 过渡依据，不制造同一人物瞬移。",
                        "previous_exit + chapter_bridge + opening paragraphs",
                    ),
                ),
            ),
            "bridge_contract_not_followed": AuditIssueTypeProfile(
                repair_focus="区分正文未履约与 bridge 元数据错误，修复对应产物。",
                postconditions=(
                    _pc(
                        "bridge_contract_validator",
                        "bridge 合同字段与正文开场、上一章出口和本章计划保持一致。",
                        "chapter_bridge + opening paragraphs",
                    ),
                ),
            ),
        },
    ),
    "causal": AuditDimensionProfile(
        dimension="causal",
        namespace="causal",
        score_attr="causal_score",
        score_label="因果分",
        threshold=7.0,
        hard_severities=("critical",),
        default_repair_surface="chapter_text",
        repair_lane="causal_repair",
        reviewer_focus="事件因果、角色动机、行动触发、问题兑现与过早解决。",
        recheck_policy=_TARGETED_RECHECK,
        issue_types={
            "opening_causal_gap": AuditIssueTypeProfile(
                repair_focus="补出开场行动为何发生以及由上一章如何触发。",
                postconditions=(
                    _pc(
                        "causal_opening_validator",
                        "开场事件有明确触发原因，并能回扣上一章或 bridge.causal_link。",
                        "opening paragraphs + causal_link",
                    ),
                ),
            ),
            "event_without_cause": AuditIssueTypeProfile(
                repair_focus="为关键事件补充可见触发、准备或人物能力依据。",
                postconditions=(
                    _pc(
                        "event_cause_validator",
                        "关键事件前存在足以支撑其发生的触发、准备或世界规则依据。",
                        "target paragraph + preceding setup",
                    ),
                ),
            ),
            "unmotivated_decision": AuditIssueTypeProfile(
                repair_focus="补足角色做出选择的欲望、压力、代价或信息来源。",
                postconditions=(
                    _pc(
                        "motivation_validator",
                        "角色决定可从其目标、压力、信息或情绪变化中推出。",
                        "decision paragraph + character notes",
                    ),
                ),
            ),
            "causal_contradiction": AuditIssueTypeProfile(
                repair_focus="消除前后因果互斥，保留已通过的主线结果。",
                postconditions=(
                    _pc(
                        "causal_contradiction_validator",
                        "修复后前后因果链不再互相否定。",
                        "target paragraphs",
                    ),
                ),
            ),
        },
    ),
    "reading_power": AuditDimensionProfile(
        dimension="reading_power",
        namespace="reading_power",
        score_attr="overall_score",
        score_label="追读力分",
        threshold=5.0,
        hard_severities=("critical", "high"),
        default_repair_surface="chapter_text",
        repair_lane="reading_power_repair",
        reviewer_focus="章尾钩子、上章钩子兑现、章内微兑现、读者点击下一章的具体理由。",
        recheck_policy=AuditRecheckPolicy(
            scan_scope="只核验章尾钩子、上章钩子回应和本轮补入的微兑现位置。",
            regression_rule="仅报告修复引入的高风险读者承诺破坏，例如旧钩子被删除或章尾钩子失效。",
        ),
        issue_types={
            "hook_missing": AuditIssueTypeProfile(
                repair_focus="构造具体可感的下一章阅读驱动力。",
                postconditions=(
                    _pc(
                        "reading_power_hook_validator",
                        "章尾存在具体、可感、可追踪的下一章阅读驱动力。",
                        "chapter ending + hook_description",
                    ),
                ),
            ),
            "hook_too_weak": AuditIssueTypeProfile(
                repair_focus="让章尾出现角色必须应对的动作后果、证据发现、选择代价或情绪冲击。",
                postconditions=(
                    _pc(
                        "reading_power_hook_validator",
                        "章尾钩子强度不为 weak/none，且不是纯情绪余波。",
                        "chapter ending + hook_strength",
                    ),
                ),
            ),
            "prev_hook_unfulfilled": AuditIssueTypeProfile(
                repair_focus="回应上一章留下的承诺、问题或危机。",
                postconditions=(
                    _pc(
                        "reading_power_prev_hook_validator",
                        "上一章钩子在本章得到明确回应、兑现、反转或阶段性推进。",
                        "previous_hook + opening/middle paragraphs",
                    ),
                ),
            ),
            "payoff_missing": AuditIssueTypeProfile(
                repair_focus="补充信息、关系、能力、资源或线索类微兑现。",
                postconditions=(
                    _pc(
                        "reading_power_payoff_validator",
                        "本章至少补出一个与期待相关的微兑现。",
                        "micro_payoffs",
                    ),
                ),
            ),
            "information_pacing_slow": AuditIssueTypeProfile(
                repair_focus="补出新线索、关系确认、局势变化或阶段性答案。",
                postconditions=(
                    _pc(
                        "reading_power_information_pacing_validator",
                        "章节出现可见的新信息、线索推进或局势变化。",
                        "information_pacing + micro_payoffs",
                    ),
                ),
            ),
            "information_pacing_stagnant": AuditIssueTypeProfile(
                repair_focus="补出清晰的新信息或局势变化，避免章节原地踏步。",
                postconditions=(
                    _pc(
                        "reading_power_information_pacing_validator",
                        "信息释放不再停滞，读者获得新的可追踪期待。",
                        "information_pacing + chapter events",
                    ),
                ),
            ),
            "main_plot_surface": AuditIssueTypeProfile(
                repair_focus="让主线目标、阻力、结果或角色认知至少产生一处可见推进。",
                postconditions=(
                    _pc(
                        "reading_power_main_plot_validator",
                        "主线推进不再停留在表层，正文中可定位到推进证据。",
                        "main_plot_depth + advancement notes",
                    ),
                ),
            ),
            "main_plot_stalled": AuditIssueTypeProfile(
                repair_focus="恢复主线行动链，让本章有明确推进结果。",
                postconditions=(
                    _pc(
                        "reading_power_main_plot_validator",
                        "本章主线目标、阻力、结果或角色认知发生可见变化。",
                        "main_plot_depth + chapter events",
                    ),
                ),
            ),
            "tension_depressed": AuditIssueTypeProfile(
                repair_focus="强化关键段落压力、代价、期待或章尾承接压力。",
                postconditions=(
                    _pc(
                        "reading_power_tension_validator",
                        "章节张力与当前阶段预期更接近。",
                        "tension_match + chapter ending",
                    ),
                ),
            ),
            "character_drive_weak": AuditIssueTypeProfile(
                repair_focus="让核心角色做出带动机的选择、试探、拒绝或承担后果的行动。",
                postconditions=(
                    _pc(
                        "reading_power_character_drive_validator",
                        "核心角色至少有一次主动选择或承担后果的行动。",
                        "character_drive + character_drive_notes",
                    ),
                ),
            ),
            "revelation_over_budget": AuditIssueTypeProfile(
                repair_focus="将超预算重大揭示后移、拆分或降级为阶段性线索。",
                postconditions=(
                    _pc(
                        "reading_power_revelation_budget_validator",
                        "重大揭示数量回到预算内或被改成可消化的阶段性线索。",
                        "revelation_count + revelation_over_budget",
                    ),
                ),
            ),
        },
    ),
    "alignment": AuditDimensionProfile(
        dimension="alignment",
        namespace="alignment",
        score_attr="alignment_score",
        score_label="对齐分",
        threshold=7.0,
        hard_severities=("critical", "high"),
        default_repair_surface="chapter_text",
        repair_lane="alignment_repair",
        reviewer_focus="章节正文是否落实本章目标、主线点、支线要求和大纲钩子。",
        recheck_policy=AuditRecheckPolicy(
            scan_scope="只核验缺失主线点/支线点是否落地，以及修复是否破坏既有大纲目标。",
            regression_rule="仅报告修复导致的大纲硬目标冲突或主线结果回退。",
        ),
        issue_types={
            "outline_main_point_missing": AuditIssueTypeProfile(
                repair_focus="补足缺失主线点，不重写已完成剧情。",
                postconditions=(
                    _pc(
                        "alignment_main_point_validator",
                        "正文明确落实缺失的本章主线点或目标结果。",
                        "chapter_outline.main_plot_points + chapter text",
                    ),
                ),
            ),
            "outline_subplot_weak": AuditIssueTypeProfile(
                repair_focus="局部补强支线存在感和承接价值。",
                postconditions=(
                    _pc(
                        "alignment_subplot_validator",
                        "支线点在正文中有可见推进或与主线形成有效呼应。",
                        "subplot plan + chapter text",
                    ),
                ),
            ),
            "alignment_conflict": AuditIssueTypeProfile(
                repair_focus="消除正文和大纲目标的高风险冲突。",
                postconditions=(
                    _pc(
                        "alignment_conflict_validator",
                        "修复后正文结果不再违背章节大纲的硬性目标。",
                        "chapter_outline + chapter text",
                    ),
                ),
            ),
        },
    ),
    "guard": AuditDimensionProfile(
        dimension="guard",
        namespace="guard",
        score_attr="overall_compliance_rate",
        score_label="AI 护栏合规",
        threshold=0.8,
        hard_severities=("critical", "high"),
        default_repair_surface="chapter_text",
        repair_lane="guardrail_repair",
        reviewer_focus="用户/系统护栏约束是否被落实，特别是禁止项、必须回应项和不可引入项。",
        recheck_policy=_GUARD_RECHECK,
        issue_types={
            "guard_constraint_missing": AuditIssueTypeProfile(
                repair_focus="补足必须兑现的护栏约束。",
                postconditions=(
                    _pc(
                        "guard_constraint_validator",
                        "正文明确回应并落实该护栏约束。",
                        "constraint + repaired text",
                    ),
                ),
            ),
            "guard_constraint_partial": AuditIssueTypeProfile(
                repair_focus="把弱兑现或部分兑现补强到可复核。",
                postconditions=(
                    _pc(
                        "guard_constraint_validator",
                        "护栏约束不再只是暗示或弱兑现，而是可从正文证据确认。",
                        "constraint + evidence",
                    ),
                ),
            ),
            "guard_constraint_unverified": AuditIssueTypeProfile(
                repair_surface="manual",
                repair_focus="证据不足时转人工复核，避免自动改写造成误伤。",
                postconditions=(
                    _pc(
                        "guard_manual_review_validator",
                        "人工确认该护栏是否真实违规，再决定是否进入正文修复。",
                        "constraint + source text",
                    ),
                ),
            ),
        },
    ),
    "knowledge_boundary": AuditDimensionProfile(
        dimension="knowledge_boundary",
        namespace="knowledge_boundary",
        score_attr="knowledge_boundary_score",
        score_label="知识边界安全分",
        threshold=9.0,
        hard_severities=("critical", "high"),
        default_repair_surface="chapter_text",
        repair_lane="text_repair",
        reviewer_focus="POV 可见知识、非 POV 私密知识、未来揭示顺序、当前章 knowledge_ops 合法性。",
        recheck_policy=_KNOWLEDGE_BOUNDARY_RECHECK,
        issue_types={
            "knowledge_leak": AuditIssueTypeProfile(
                repair_focus="删除或改写非 POV/当前章不可见知识，只保留角色可感知证据。",
                postconditions=(
                    _pc(
                        "knowledge_boundary_validator",
                        "正文不再让角色或叙述提前掌握该隐藏知识。",
                        "target paragraph + knowledge_boundary finding",
                    ),
                ),
            ),
            "premature_reveal": AuditIssueTypeProfile(
                repair_focus="把未来揭示降级为当前章可见线索，或移除提前确认。",
                postconditions=(
                    _pc(
                        "premature_reveal_validator",
                        "未来事实不再被提前确认或完整揭示。",
                        "target paragraph + candidate_id",
                    ),
                ),
            ),
            "unsupported_knowledge_gain": AuditIssueTypeProfile(
                repair_focus="补足当前章可见获知证据，或改写为怀疑/误解/外显观察。",
                postconditions=(
                    _pc(
                        "unsupported_knowledge_gain_validator",
                        "角色获知方式可由当前章正文证据支撑。",
                        "target paragraph + allowed_current_ops",
                    ),
                ),
            ),
        },
    ),
    "state_adjudication": AuditDimensionProfile(
        dimension="state_adjudication",
        namespace="state_adjudication",
        score_attr="final_adjudication.confidence",
        score_label="状态裁判置信",
        threshold=0.0,
        hard_severities=("critical", "high"),
        default_repair_surface="state_packet",
        repair_lane="state_adjudication_repair",
        reviewer_focus="LLM 裁决后的权威叙事状态、候选状态冲突、是否允许归档。",
        recheck_policy=_STATE_RECHECK,
        issue_types={
            "state_archive_block": AuditIssueTypeProfile(
                repair_surface="state_packet",
                repair_focus="修复正文提取状态、候选 delta 或最终状态投影冲突。",
                postconditions=(
                    _pc(
                        "state_archive_block_validator",
                        "最终状态裁判不再要求阻断归档，权威状态与正文结尾一致。",
                        "final_adjudication + state projection",
                    ),
                ),
            ),
            "state_projection_conflict": AuditIssueTypeProfile(
                repair_surface="state_packet",
                repair_focus="消除 narrative_state_projection 与正文事实冲突。",
                postconditions=(
                    _pc(
                        "state_projection_validator",
                        "narrative_state_projection 与正文可证据化状态一致。",
                        "state projection + chapter text evidence",
                    ),
                ),
            ),
        },
    ),
    "chapter_quality": AuditDimensionProfile(
        dimension="chapter_quality",
        namespace="chapter_quality",
        score_attr="quality_score",
        score_label="章节质量分",
        threshold=8.0,
        hard_severities=("critical", "high"),
        default_repair_surface="chapter_text",
        repair_lane="chapter_quality_repair",
        reviewer_focus="提示词泄露、非法时辰/事实、POV 侵入、重复表达、硬禁元素。",
        recheck_policy=AuditRecheckPolicy(
            scan_scope="只核验原质量问题所在段落及其邻接句。",
            regression_rule="仅报告新出现的提示词泄露、硬禁元素、非法时间或 POV 硬错误。",
        ),
        issue_types={
            "prompt_leak": AuditIssueTypeProfile(
                repair_focus="删除元语言并改写为角色当下叙事。",
                postconditions=(
                    _pc(
                        "prompt_leak_validator",
                        "正文中不再出现提示词、系统指令或规划层元语言。",
                        "full chapter text",
                    ),
                ),
            ),
            "time_marker_invalid": AuditIssueTypeProfile(
                repair_focus="修正非法时辰刻度或改为合法/模糊时间锚点。",
                postconditions=(
                    _pc(
                        "time_marker_validator",
                        "时间表达符合项目时间系统或不再使用非法刻度。",
                        "target paragraph",
                    ),
                ),
            ),
            "pov_intrusion": AuditIssueTypeProfile(
                repair_focus="把非 POV 内心改成外显动作、表情或语气暗示。",
                postconditions=(
                    _pc(
                        "pov_intrusion_validator",
                        "修复窗口不再直接进入非 POV 角色内心。",
                        "target paragraph",
                    ),
                ),
            ),
        },
    ),
}


DEFAULT_AUDIT_PROFILE = AuditDimensionProfile(
    dimension="unknown",
    namespace="audit",
    score_attr="score",
    score_label="审查分",
    threshold=0.0,
    hard_severities=("critical",),
    default_repair_surface="chapter_text",
    repair_lane="generic_repair",
    reviewer_focus="通用审查问题。",
    recheck_policy=_TARGETED_RECHECK,
)


def get_audit_profile(dimension: str) -> AuditDimensionProfile:
    """Return the profile for a review dimension."""
    key = str(dimension or "").strip().lower()
    return AUDIT_PROFILES.get(key, DEFAULT_AUDIT_PROFILE)


def recheck_policy_context(dimension: str) -> dict[str, Any]:
    """Return prompt-safe recheck policy for a dimension."""
    return get_audit_profile(dimension).recheck_policy.to_prompt_context()
