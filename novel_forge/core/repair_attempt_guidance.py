"""Shared guidance for non-repetitive repair retries."""

from __future__ import annotations

from typing import Any

_DIRECTION_CYCLE: dict[str, tuple[dict[str, Any], ...]] = {
    "continuity": (
        {
            "strategy_id": "anchor_reconstruction",
            "new_direction": "先恢复上一章余波、本章契约和角色状态锚点，再做局部文字补丁。",
            "avoid": "不要只补一个说明句或复述上一轮的关键词。",
            "steps": ("标出缺失锚点", "用动作/感官/对话承接", "检查开头和结尾契约仍成立"),
        },
        {
            "strategy_id": "window_rewrite",
            "new_direction": "扩大到问题段前后窗口重写，让转场、地点、POV 和情绪余波同时落地。",
            "avoid": "不要在原段落末尾机械追加解释。",
            "steps": ("重排窗口内动作顺序", "补足转场因果", "保持窗口外正文不动"),
        },
        {
            "strategy_id": "cause_effect_bridge",
            "new_direction": "把断裂处改成明确的因果桥：因为上一动作/信息，所以角色现在采取新动作。",
            "avoid": "不要用总结性回顾替代场景内行动。",
            "steps": ("找出断裂前因", "补出角色即时反应", "用下一动作接回本章目标"),
        },
    ),
    "causal": (
        {
            "strategy_id": "motivation_first",
            "new_direction": "先补角色做决定前获得的信息、压力或欲望，再让事件自然发生。",
            "avoid": "不要只说明「他决定」或「于是发生」。",
            "steps": ("明确触发信息", "补内在/外在压力", "让决定以动作或对话显形"),
        },
        {
            "strategy_id": "scene_causality",
            "new_direction": "改写事件所在场景的起因-行动-后果链，而不是只修单句逻辑。",
            "avoid": "不要把因果解释塞成旁白。",
            "steps": ("定位事件前一拍", "加入触发动作", "写出可见后果"),
        },
        {
            "strategy_id": "question_payoff_timing",
            "new_direction": "调整问题回应时机：未回应就补阶段性兑现，过早揭晓就改为新疑问或半答案。",
            "avoid": "不要直接给出后续章节答案。",
            "steps": ("确认读者期待", "选择兑现/反转/延迟", "保留下一章驱动力"),
        },
    ),
    "reading_power": (
        {
            "strategy_id": "payoff_then_hook",
            "new_direction": "先补本章微兑现，再把章尾钩子接在兑现后的新代价或新证据上。",
            "avoid": "不要只把章尾改成一句悬念口号。",
            "steps": ("补一个可感兑现", "制造新代价", "让章尾形成下一章必须处理的问题"),
        },
        {
            "strategy_id": "opening_response",
            "new_direction": "先回应上一章钩子，再用回应产生的新阻力推动本章中段。",
            "avoid": "不要跳过上一章读者期待直接开新线。",
            "steps": ("前半章回应旧钩子", "让回应带来变化", "中后段递进到新钩子"),
        },
        {
            "strategy_id": "ending_reframe",
            "new_direction": "重构最后 2-4 段，让结尾从静态悬念变成角色必须行动的具体压力。",
            "avoid": "不要提前揭秘答案或加入计划外重大事件。",
            "steps": ("保留本章结果", "加具体证据/选择", "以动作或对白落下钩子"),
        },
    ),
    "guard": (
        {
            "strategy_id": "constraint_without_mainline_damage",
            "new_direction": "绕开主线推进段，在非核心窗口兑现护栏约束。",
            "avoid": "不要为了满足护栏 ticket 改写、删除或稀释主线锚点。",
            "steps": ("定位主线段并冻结", "选择邻近非核心窗口", "用一句动作/对白兑现约束"),
        },
        {
            "strategy_id": "minimal_acceptance_patch",
            "new_direction": "只满足 ticket 的验收条件，避免扩写成新支线。",
            "avoid": "不要新增角色、地点、后续答案或额外事件链。",
            "steps": ("提取验收条件", "补最小可验证文本", "复查对齐分风险"),
        },
    ),
    "generic": (
        {
            "strategy_id": "different_surface",
            "new_direction": "换一个修复面：若上轮补描述，本轮改用动作/对话；若上轮补单句，本轮改写窗口。",
            "avoid": "不要重复上一轮同类补丁。",
            "steps": ("判断上轮失败点", "选择不同表达通道", "只改必要窗口"),
        },
    ),
}


def _issue_text(issue: Any, field: str) -> str:
    if isinstance(issue, dict):
        return str(issue.get(field, "") or "").strip()
    return str(getattr(issue, field, "") or "").strip()


def _issue_type(issue: Any) -> str:
    return _issue_text(issue, "issue_type").lower() or _issue_text(issue, "type").lower()


def _issue_summary(issue: Any) -> str:
    return _issue_text(issue, "summary") or _issue_text(issue, "target_summary")


def _score_delta(current_score: float | None, previous_score: float | None) -> float | None:
    if current_score is None or previous_score is None:
        return None
    return float(current_score) - float(previous_score)


def build_repair_attempt_guidance(
    *,
    domain: str,
    round_number: int,
    max_rounds: int,
    issues: list[Any] | tuple[Any, ...],
    current_score: float | None = None,
    score_threshold: float | None = None,
    previous_score: float | None = None,
    previous_issues: list[Any] | tuple[Any, ...] | None = None,
    previous_strategy: str = "",
) -> dict[str, Any]:
    """Build a compact retry plan that pushes each repair round in a new direction."""
    normalized_domain = domain if domain in _DIRECTION_CYCLE else "generic"
    directions = _DIRECTION_CYCLE[normalized_domain]
    index = max(0, int(round_number) - 1) % len(directions)
    selected = directions[index]

    issue_types = sorted({item for item in (_issue_type(issue) for issue in issues) if item})
    summaries = [_issue_summary(issue) for issue in issues]
    summaries = [item for item in summaries if item][:5]
    previous_summaries = [_issue_summary(issue) for issue in (previous_issues or ())]
    previous_summaries = [item for item in previous_summaries if item][:5]
    delta = _score_delta(current_score, previous_score)

    diagnosis: list[str] = []
    if round_number <= 1:
        diagnosis.append("首轮修复：先处理最高优先级问题，并建立可复检的场景证据。")
    else:
        diagnosis.append("上一轮后仍有问题残留，本轮必须更换表达通道或扩大修复窗口。")
    if delta is not None:
        if delta < -0.1:
            diagnosis.append(f"上一轮导致分数下降 {abs(delta):.1f}，需先恢复被稀释的主线/因果锚点。")
        elif delta < 0.3:
            diagnosis.append(f"上一轮提升不足（{delta:+.1f}），说明机械补句没有解决语义根因。")
        else:
            diagnosis.append(f"上一轮已有改善（{delta:+.1f}），本轮只补残留问题，避免扩大改写。")
    if score_threshold is not None and current_score is not None and current_score < score_threshold:
        diagnosis.append(f"当前分 {current_score:.1f} 仍低于目标 {score_threshold:.1f}，必须给出可验证文本证据。")
    if previous_summaries:
        diagnosis.append("残留问题与上一轮重叠，禁止复用上一轮同样的修复动作。")
    if previous_strategy:
        diagnosis.append(f"上一轮策略为 {previous_strategy}，本轮需要换方向。")

    return {
        "domain": normalized_domain,
        "round_number": int(round_number),
        "max_rounds": int(max_rounds),
        "strategy_id": selected["strategy_id"],
        "new_direction": selected["new_direction"],
        "avoid": selected["avoid"],
        "steps": list(selected["steps"]),
        "diagnosis": diagnosis,
        "issue_types": issue_types,
        "issue_focus": summaries,
        "previous_issue_focus": previous_summaries,
        "previous_strategy": previous_strategy,
        "score_delta": delta,
    }
