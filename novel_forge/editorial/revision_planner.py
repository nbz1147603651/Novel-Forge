"""Convert editorial audit findings into structured revision actions."""

from __future__ import annotations

from novel_forge.editorial.schemas import (
    EditorialContract,
    EditorialFinding,
    EditorialRevisionAction,
)
from novel_forge.pipeline.steps.book_audit_runner import BookAuditChapter


def build_structured_revision_plan(
    *,
    findings: list[EditorialFinding],
    chapters: list[BookAuditChapter],
    contract: EditorialContract,
) -> dict[str, list[dict[str, object]]]:
    """Build a structured, non-patch revision plan for human/editorial use."""

    actions: list[EditorialRevisionAction] = []
    actions.extend(_actions_from_findings(findings, chapters=chapters, contract=contract))
    actions.extend(_actions_from_element_directives(contract))
    actions.extend(_actions_from_time_policy(contract, chapters=chapters))
    deduped = _dedupe_actions(actions)
    return {
        "actions": [action.model_dump(mode="json") for action in deduped],
        "chapter_merge_plan": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type == "merge_chapters"
        ],
        "element_directive_actions": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type.startswith("apply_editorial_element:")
        ],
        "language_actions": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type in {"rewrite_language", "trim_symbol_explanation"}
        ],
        "title_actions": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type == "rename_titles"
        ],
        "paragraph_actions": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type == "split_paragraphs"
        ],
        "time_bridge_actions": [
            action.model_dump(mode="json")
            for action in deduped
            if action.action_type == "add_time_bridge"
        ],
    }


def _actions_from_findings(
    findings: list[EditorialFinding],
    *,
    chapters: list[BookAuditChapter],
    contract: EditorialContract,
) -> list[EditorialRevisionAction]:
    actions: list[EditorialRevisionAction] = []
    chapter_numbers = [chapter.chapter_number for chapter in chapters]
    last_chapter = max(chapter_numbers) if chapter_numbers else 0
    main = contract.main_climax()
    for finding in findings:
        if finding.issue_type == "denouement_overrun":
            start = main.chapter_number + 1
            end = last_chapter or start
            target_count = max(1, contract.denouement_budget.expected_chapters)
            actions.append(
                EditorialRevisionAction(
                    action_type="merge_chapters",
                    priority="high",
                    chapter_range=[start, end],
                    target="主高潮后余波",
                    rationale=finding.summary,
                    instruction=(
                        f"将第 {start}-{end} 章按功能压缩为约 {target_count} 个节点："
                        f"{'、'.join(contract.denouement_budget.required_new_functions)}。"
                    ),
                )
            )
        elif finding.issue_type in {"title_repetition", "outline_title_reuse_over_budget"}:
            title_target = str(finding.metadata.get("title") or "").strip()
            if not title_target and isinstance(finding.metadata.get("titles"), dict):
                title_target = "、".join(str(title) for title in finding.metadata["titles"])
            actions.append(
                EditorialRevisionAction(
                    action_type="rename_titles",
                    priority="medium",
                    chapter_range=[],
                    target=title_target or "重复标题",
                    rationale=finding.summary,
                    instruction=contract.title_policy.naming_strategy,
                )
            )
        elif finding.issue_type in {"explanation_density", "voice_convergence"}:
            actions.append(
                EditorialRevisionAction(
                    action_type="rewrite_language",
                    priority="medium",
                    chapter_range=[finding.chapter_number] if finding.chapter_number else [],
                    target=finding.issue_type,
                    rationale=finding.summary,
                    instruction=finding.recommendation or "按编辑契约删解释、分声纹、补动作后果。",
                )
            )
        elif finding.issue_type == "paragraph_visual_fatigue":
            actions.append(
                EditorialRevisionAction(
                    action_type="split_paragraphs",
                    priority="medium",
                    chapter_range=[finding.chapter_number] if finding.chapter_number else [],
                    target=f"第 {finding.metadata.get('paragraph_index', '?')} 段",
                    rationale=finding.summary,
                    instruction=(
                        finding.recommendation
                        or "按动作转折、对白插入、信息揭示或情绪落点拆分长段。"
                    ),
                )
            )
        elif finding.issue_type == "symbol_over_explanation":
            actions.append(
                EditorialRevisionAction(
                    action_type="trim_symbol_explanation",
                    priority="medium",
                    chapter_range=[finding.chapter_number] if finding.chapter_number else [],
                    target=str(finding.metadata.get("symbol") or "象征物"),
                    rationale=finding.summary,
                    instruction=finding.recommendation or "保留物件动作，删除含义说明。",
                )
            )
    return actions


def _actions_from_element_directives(
    contract: EditorialContract,
) -> list[EditorialRevisionAction]:
    actions: list[EditorialRevisionAction] = []
    for directive in contract.editorial_element_directives:
        actions.append(
            EditorialRevisionAction(
                action_type=f"apply_editorial_element:{directive.element_id}",
                priority="medium",
                target=directive.element_name or directive.element_id,
                rationale=directive.requirement,
                instruction=(
                    f"{directive.target_window}：{directive.requirement}"
                    f"；完成标准：{directive.success_criteria}"
                ),
            )
        )
    return actions


def _actions_from_time_policy(
    contract: EditorialContract,
    *,
    chapters: list[BookAuditChapter],
) -> list[EditorialRevisionAction]:
    if not contract.time_bridge_policies or len(chapters) < 8:
        return []
    return [
        EditorialRevisionAction(
            action_type="add_time_bridge",
            priority="medium",
            target="跨章节时间桥",
            rationale="编辑契约要求大跨度转场明确标记时间。",
            instruction="检查章节开头与大段转场，补出日期、月份、季节、几周后或事件间隔。",
        )
    ]


def _dedupe_actions(actions: list[EditorialRevisionAction]) -> list[EditorialRevisionAction]:
    seen: set[tuple[str, str, tuple[int, ...]]] = set()
    result: list[EditorialRevisionAction] = []
    for action in actions:
        key = (action.action_type, action.target, tuple(action.chapter_range))
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result
