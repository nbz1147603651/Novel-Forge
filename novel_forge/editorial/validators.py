"""Local validators for project editorial contracts."""

from __future__ import annotations

from novel_forge.editorial.schemas import EditorialAuditReport, EditorialContract, EditorialFinding
from novel_forge.pipeline.steps.blueprint_element_select.elements import get_library_by_id


def validate_editorial_contract(
    contract: EditorialContract,
    *,
    total_chapters: int = 0,
    selected_element_ids: set[str] | None = None,
    outline_titles: list[str] | None = None,
) -> EditorialAuditReport:
    """Validate the editorial contract before chapter generation begins.

    The validator stays generic: it checks budgets and element-library
    references, not story-specific prescriptions.
    """

    findings: list[EditorialFinding] = []
    findings.extend(_validate_structure_budget(contract, total_chapters=total_chapters))
    findings.extend(
        _validate_element_directives(contract, selected_element_ids=selected_element_ids)
    )
    findings.extend(_validate_revelation_ladder(contract, total_chapters=total_chapters))
    findings.extend(_validate_time_and_titles(contract, outline_titles=outline_titles or []))

    critical_count = sum(1 for item in findings if item.severity == "critical")
    high_count = sum(1 for item in findings if item.severity == "high")
    status = "blocked" if critical_count else ("warning" if findings else "passed")
    return EditorialAuditReport(
        summary=_summary(status, critical_count=critical_count, high_count=high_count),
        findings=findings,
        revision_plan=[
            item.recommendation for item in findings if item.recommendation
        ],
        metrics={
            "status": status,
            "critical_count": critical_count,
            "high_count": high_count,
            "finding_count": len(findings),
        },
    )


def _validate_structure_budget(
    contract: EditorialContract,
    *,
    total_chapters: int,
) -> list[EditorialFinding]:
    if total_chapters <= 0:
        return []
    findings: list[EditorialFinding] = []
    main = contract.main_climax()
    denouement_ratio = contract.denouement_budget.expected_chapters / max(1, total_chapters)
    aftermath_ratio = main.expected_aftermath_chapters / max(1, total_chapters)
    if main.chapter_number >= total_chapters:
        findings.append(
            EditorialFinding(
                issue_type="editorial_contract_main_climax_invalid",
                severity="critical",
                summary="主高潮章号不应落在全书最后一章之后或等于最后一章。",
                recommendation="重新生成编辑契约或调整蓝图，使主高潮后仍有可预算余波。",
                confidence=0.95,
                metadata={"main_climax_chapter": main.chapter_number, "total_chapters": total_chapters},
            )
        )
    if denouement_ratio > 0.22 or aftermath_ratio > 0.22:
        findings.append(
            EditorialFinding(
                issue_type="editorial_contract_denouement_budget_high",
                severity="high",
                summary="高潮后余波预算超过全书 22%，有后段退坡风险。",
                recommendation="压缩高潮后章节功能，只保留余波后果、制度化节点和终场意象等非重复功能。",
                confidence=0.86,
                metadata={
                    "denouement_ratio": round(denouement_ratio, 3),
                    "aftermath_ratio": round(aftermath_ratio, 3),
                },
            )
        )
    if contract.denouement_budget.max_confirmation_scenes > 2:
        findings.append(
            EditorialFinding(
                issue_type="editorial_contract_confirmation_budget_high",
                severity="medium",
                summary="确认性场景预算偏高，可能形成多次“再结尾”。",
                recommendation="将确认性场景预算控制在 1-2 次，其余尾声节点必须承担新信息或新后果。",
                confidence=0.78,
                metadata={
                    "max_confirmation_scenes": contract.denouement_budget.max_confirmation_scenes
                },
            )
        )
    return findings


def _validate_element_directives(
    contract: EditorialContract,
    *,
    selected_element_ids: set[str] | None,
) -> list[EditorialFinding]:
    library = get_library_by_id()
    selected = selected_element_ids or set()
    findings: list[EditorialFinding] = []
    for directive in contract.editorial_element_directives:
        if directive.element_id not in library:
            findings.append(
                EditorialFinding(
                    issue_type="editorial_element_unknown",
                    severity="critical",
                    summary=f"编辑契约引用了不存在的叙述要素：{directive.element_id}",
                    recommendation="先把该机制加入叙述要素库，或改用已存在的 element_id。",
                    confidence=0.98,
                    metadata={"element_id": directive.element_id},
                )
            )
            continue
        if selected and directive.element_id not in selected:
            findings.append(
                EditorialFinding(
                    issue_type="editorial_element_not_selected",
                    severity="medium",
                    summary=f"编辑契约引用了未进入本项目选择集的叙述要素：{directive.element_id}",
                    recommendation="将该要素加入 blueprint element selection，或移除该编辑指令。",
                    confidence=0.82,
                    metadata={"element_id": directive.element_id},
                )
            )
    return findings


def _validate_revelation_ladder(
    contract: EditorialContract,
    *,
    total_chapters: int,
) -> list[EditorialFinding]:
    if not contract.revelation_ladder:
        return []
    findings: list[EditorialFinding] = []
    by_thread: dict[str, list[tuple[int, int]]] = {}
    for step in contract.revelation_ladder:
        if total_chapters and step.target_chapter > total_chapters:
            findings.append(
                EditorialFinding(
                    issue_type="revelation_step_out_of_range",
                    severity="high",
                    summary=f"揭示阶梯「{step.thread}」目标章号超出全书范围。",
                    recommendation="将该揭示节点移入有效章节，或调整总章数/蓝图。",
                    confidence=0.9,
                    metadata={
                        "thread": step.thread,
                        "target_chapter": step.target_chapter,
                        "total_chapters": total_chapters,
                    },
                )
            )
        if step.target_chapter > 0:
            by_thread.setdefault(step.thread, []).append((step.stage_order, step.target_chapter))
    for thread, steps in by_thread.items():
        ordered = sorted(steps)
        chapters = [chapter for _, chapter in ordered]
        if chapters != sorted(chapters):
            findings.append(
                EditorialFinding(
                    issue_type="revelation_ladder_order_drift",
                    severity="medium",
                    summary=f"揭示阶梯「{thread}」的阶段顺序与章节顺序不一致。",
                    recommendation="让揭示阶段随章节递进，或明确这是倒叙/误导结构。",
                    confidence=0.76,
                    metadata={"thread": thread, "chapters": chapters},
                )
            )
    return findings


def _validate_time_and_titles(
    contract: EditorialContract,
    *,
    outline_titles: list[str],
) -> list[EditorialFinding]:
    findings: list[EditorialFinding] = []
    if contract.title_policy.max_reuse > 2:
        findings.append(
            EditorialFinding(
                issue_type="title_policy_reuse_budget_high",
                severity="low",
                summary="标题复用预算高于 2，目录辨识度可能下降。",
                recommendation="只允许核心回环标题复用，其余标题提示章节功能。",
                confidence=0.82,
                metadata={"max_reuse": contract.title_policy.max_reuse},
            )
        )
    if not contract.time_bridge_policies:
        findings.append(
            EditorialFinding(
                issue_type="time_bridge_policy_missing",
                severity="medium",
                summary="编辑契约缺少大跨度时间桥规则。",
                recommendation="补充跨月、跨季、几周后等时间桥标记规则。",
                confidence=0.75,
            )
        )
    allowed = set(contract.title_policy.allowed_repeated_titles)
    title_counts: dict[str, int] = {}
    for title in outline_titles:
        clean = title.strip()
        if clean:
            title_counts[clean] = title_counts.get(clean, 0) + 1
    repeated = {
        title: count
        for title, count in title_counts.items()
        if count > (contract.title_policy.max_reuse if title in allowed else 1)
    }
    if repeated:
        findings.append(
            EditorialFinding(
                issue_type="outline_title_reuse_over_budget",
                severity="medium",
                summary="初始化大纲中存在超出标题复用预算的章节标题。",
                evidence=[f"{title}×{count}" for title, count in repeated.items()],
                recommendation="保留少量核心回环标题，其余改成节点性标题。",
                confidence=0.9,
                metadata={
                    "titles": repeated,
                    "max_reuse": contract.title_policy.max_reuse,
                    "allowed_repeated_titles": sorted(allowed),
                },
            )
        )
    return findings


def _summary(status: str, *, critical_count: int, high_count: int) -> str:
    if status == "blocked":
        return f"编辑契约校验阻断：{critical_count} 个严重问题。"
    if status == "warning":
        return f"编辑契约校验通过但有风险：{high_count} 个高风险问题。"
    return "编辑契约校验通过。"
