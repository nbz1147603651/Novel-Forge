"""World-rule source validation, chapter projection, and compliance checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.core.schemas.world_rules import (
    WorldRuleApplication,
    WorldRuleBook,
    WorldRuleCard,
    WorldRuleCardEntry,
    WorldRuleComplianceReport,
    WorldRuleIssue,
    WorldRuleSpec,
)
from novel_forge.core.utils.audit_issue import stable_issue_id


@dataclass(frozen=True)
class WorldRuleGovernanceConfig:
    """Parameterized governance thresholds for the world rule book.

    All values default to the original hardcoded constants so callers that
    don't pass settings get the pre-existing behavior.
    """

    rule_count_min: int = 10
    rule_count_max: int = 14
    hard_rule_min: int = 3
    always_on_hard_cap: int = 4
    category_min: int = 4
    ability_required: bool = True

    @classmethod
    def from_settings(cls, settings: Any) -> "WorldRuleGovernanceConfig":
        """Extract governance config from a Settings object (or return defaults)."""
        if settings is None:
            return cls()
        return cls(
            rule_count_min=int(getattr(settings, "world_rule_count_min", 10) or 10),
            rule_count_max=int(getattr(settings, "world_rule_count_max", 14) or 14),
            hard_rule_min=int(getattr(settings, "world_rule_hard_min", 3) or 3),
            always_on_hard_cap=int(getattr(settings, "world_rule_always_on_hard_cap", 4) or 4),
            category_min=int(getattr(settings, "world_rule_category_min", 4) or 4),
            ability_required=bool(getattr(settings, "world_rule_ability_required", True)),
        )


def _enforce_always_on_hard_rule_cap(
    book: WorldRuleBook,
    *,
    cap: int = 4,
) -> WorldRuleBook:
    """Downgrade excess ``always_on`` hard rules to conditional rules.

    The chapter-model attention budget allows at most ``cap`` always-on hard
    rules. LLMs routinely emit more (the 青瓦梦匙 output had 7) because the
    prompt cap is guidance, not a hard constraint. Rather than blocking
    initialization, we keep every rule but flip the excess (sorted by rule_id
    for determinism) to ``always_on=false`` and inject an
    ``applicability_tags`` entry so they remain selectable by chapter
    projections.
    """
    always_on_hard = [r for r in book.rules if r.always_on and r.severity == "hard"]
    if len(always_on_hard) <= cap:
        return book

    # Sort by rule_id for deterministic downgrade selection.
    sorted_always_on = sorted(always_on_hard, key=lambda r: r.rule_id)
    downgrade_ids = {r.rule_id for r in sorted_always_on[cap:]}

    new_rules: list[WorldRuleSpec] = []
    for rule in book.rules:
        if rule.rule_id in downgrade_ids:
            tags = list(rule.applicability_tags)
            if "coerce_downgraded_always_on" not in tags:
                tags.append("coerce_downgraded_always_on")
            rule = rule.model_copy(update={"always_on": False, "applicability_tags": tags})
        new_rules.append(rule)
    book.rules = new_rules
    return book


def coerce_world_rule_book(
    value: Any,
    *,
    settings: Any = None,
) -> WorldRuleBook:
    """Coerce a raw value into a ``WorldRuleBook``, downgrading excess always_on rules.

    When ``settings`` is provided, the always_on hard-rule cap is read from
    ``settings.world_rule_always_on_hard_cap``; otherwise the default (4) is used.
    """
    cfg = WorldRuleGovernanceConfig.from_settings(settings)
    if isinstance(value, WorldRuleBook):
        return _enforce_always_on_hard_rule_cap(value, cap=cfg.always_on_hard_cap)
    if hasattr(value, "model_dump"):
        book = WorldRuleBook.model_validate(value.model_dump(mode="json"))
    else:
        book = WorldRuleBook.model_validate(value or {})
    return _enforce_always_on_hard_rule_cap(book, cap=cfg.always_on_hard_cap)


def validate_world_rule_book(
    book: WorldRuleBook,
    *,
    magic_or_tech: str = "",
    settings: Any = None,
) -> list[str]:
    """Return source-quality issues for the initialized rule book.

    When ``settings`` is provided, thresholds are read from the project
    settings (``world_rule_count_min/max``, ``world_rule_hard_min``, etc.);
    otherwise the original hardcoded defaults are used.
    """
    cfg = WorldRuleGovernanceConfig.from_settings(settings)

    issues: list[str] = []
    count = len(book.rules)
    if not cfg.rule_count_min <= count <= cfg.rule_count_max:
        issues.append(
            f"world_rule_book 需要 {cfg.rule_count_min}–{cfg.rule_count_max} 条规则，"
            f"当前为 {count} 条。"
        )
    hard_rules = book.hard_rules
    if len(hard_rules) < cfg.hard_rule_min:
        issues.append(
            f"world_rule_book 至少需要 {cfg.hard_rule_min} 条硬规则。"
        )
    if not any(rule.always_on and rule.severity == "hard" for rule in book.rules):
        issues.append("world_rule_book 至少需要一条始终生效的硬规则。")
    always_on_hard_count = sum(
        1 for rule in book.rules if rule.always_on and rule.severity == "hard"
    )
    if always_on_hard_count > cfg.always_on_hard_cap:
        issues.append(
            f"始终生效的硬规则不得超过 {cfg.always_on_hard_cap} 条"
            f"（当前 {always_on_hard_count} 条），避免分散章节模型注意力。"
        )
    categories = {rule.category for rule in book.rules if rule.category}
    if len(categories) < cfg.category_min:
        issues.append(
            f"world_rule_book 至少需要覆盖 {cfg.category_min} 个规则类别。"
        )
    if cfg.ability_required:
        requires_ability_rules = any(
            marker in magic_or_tech.lower()
            for marker in (
                "magic", "ability", "technology", "tech",
                "魔法", "异能", "超自然", "法术", "科技体系", "未来科技",
            )
        )
        if requires_ability_rules and not any(
            rule.category in {"ability_tech", "magic", "technology", "physics"}
            for rule in book.rules
        ):
            issues.append("世界包含魔法/科技描述时，规则账本必须覆盖 ability_tech 类别。")
    seen_content: set[str] = set()
    seen_ids: set[str] = set()
    for rule in book.rules:
        normalized = "".join(rule.content.split())
        if not normalized:
            issues.append("world_rule_book 存在空规则。")
            continue
        if normalized in seen_content:
            issues.append(f"world_rule_book 存在重复规则：{rule.content[:60]}")
        seen_content.add(normalized)
        if rule.rule_id in seen_ids:
            issues.append(f"world_rule_book 存在重复 rule_id：{rule.rule_id}")
        seen_ids.add(rule.rule_id)
        if rule.severity == "hard":
            if not rule.always_on and not rule.applicability_tags:
                issues.append(f"硬规则 {rule.rule_id} 缺少 always_on 或 applicability_tags。")
            if not rule.forbidden_behavior and not rule.cost_or_consequence:
                issues.append(f"硬规则 {rule.rule_id} 缺少禁止行为或代价/后果，无法验收。")
    return issues


def build_world_rule_card(
    *,
    rule_book: WorldRuleBook | dict[str, Any] | None,
    chapter_number: int,
    chapter_contract: dict[str, Any],
    chapter_outline: dict[str, Any],
    relevant_entities: list[Any],
) -> WorldRuleCard:
    """Select deterministic chapter rules without exposing the raw source bible."""

    book = coerce_world_rule_book(rule_book)
    context_parts = [
        str(chapter_contract),
        str(chapter_outline),
        " ".join(_entity_names(relevant_entities)),
    ]
    context = " ".join(context_parts).lower()
    always_on: list[WorldRuleCardEntry] = []
    relevant: list[WorldRuleCardEntry] = []
    omitted: list[str] = []
    selected_ids: set[str] = set()
    for rule in book.rules:
        if rule.severity == "hard" and rule.always_on:
            always_on.append(
                WorldRuleCardEntry(rule=rule, selection_reason="始终生效的硬规则")
            )
            selected_ids.add(rule.rule_id)
    candidates: list[tuple[WorldRuleSpec, str]] = []
    for rule in book.rules:
        if rule.rule_id in selected_ids:
            continue
        matched_tags = [tag for tag in rule.applicability_tags if tag.lower() in context]
        if matched_tags:
            candidates.append((rule, "匹配本章适用标签：" + "、".join(matched_tags)))
        else:
            omitted.append(rule.rule_id)
    candidates.sort(key=lambda item: (item[0].severity != "hard", item[0].rule_id))
    for rule, reason in candidates[:8]:
        relevant.append(WorldRuleCardEntry(rule=rule, selection_reason=reason))
        selected_ids.add(rule.rule_id)
    omitted = [rule_id for rule_id in omitted if rule_id not in selected_ids]
    omitted.extend(rule.rule_id for rule, _ in candidates[8:])
    return WorldRuleCard(
        chapter_number=chapter_number,
        rule_book_version=book.version,
        source_hash=book.source_hash,
        always_on=always_on,
        relevant_rules=relevant,
        omitted_rule_ids=list(dict.fromkeys(omitted)),
    )


def coerce_world_rule_card(value: WorldRuleCard | dict[str, Any]) -> WorldRuleCard:
    """Parse a source card or a stage projection without leaking view metadata.

    Stage projections intentionally add fields such as ``view_stage`` for
    observability.  ``WorldRuleCard`` remains the strict source contract, so
    projection-only metadata must be stripped at the boundary instead of being
    added to the authoritative schema.
    """
    if isinstance(value, WorldRuleCard):
        return value
    allowed = set(WorldRuleCard.model_fields)
    payload = {key: item for key, item in value.items() if key in allowed}
    return WorldRuleCard.model_validate(payload)


def validate_plan_world_rule_coverage(
    plan: Any, card: WorldRuleCard | dict[str, Any] | None
) -> list[str]:
    """Ensure every selected hard rule is executable or explicitly inapplicable."""

    if card is None:
        return ["章节源头缺少 world_rule_card，必须重新初始化。"]
    resolved_card = coerce_world_rule_card(card)
    applications = list(getattr(plan, "world_rule_applications", []) or [])
    if isinstance(plan, dict):
        applications = list(plan.get("world_rule_applications", []) or [])
    normalized: dict[str, WorldRuleApplication] = {}
    for item in applications:
        application = (
            item
            if isinstance(item, WorldRuleApplication)
            else WorldRuleApplication.model_validate(item)
        )
        if application.rule_id:
            normalized[application.rule_id] = application
    scenes = list(getattr(plan, "scene_intents", []) or [])
    if isinstance(plan, dict):
        scenes = list(plan.get("scene_intents", []) or [])
    scene_by_id: dict[str, Any] = {}
    for scene in scenes:
        scene_id = str(
            scene.get("scene_id") if isinstance(scene, dict) else getattr(scene, "scene_id", "")
        ).strip()
        if scene_id:
            scene_by_id[scene_id] = scene
    scene_ids = set(scene_by_id)
    issues: list[str] = []
    for entry in resolved_card.all_entries:
        rule = entry.rule
        if rule.severity != "hard":
            continue
        app: WorldRuleApplication | None = normalized.get(rule.rule_id)
        if app is None:
            issues.append(f"硬规则 {rule.rule_id} 未绑定场景，也未声明不适用。")
            continue
        if app.applicability == "not_applicable":
            if not app.not_applicable_reason:
                issues.append(f"硬规则 {rule.rule_id} 标记不适用但未说明理由。")
            continue
        if not app.scene_id or app.scene_id not in scene_ids:
            issues.append(f"硬规则 {rule.rule_id} 未绑定有效 scene_id。")
        if not app.usage or not app.expected_evidence or not app.forbidden_boundary:
            issues.append(f"硬规则 {rule.rule_id} 缺少遵守方式、预期正文证据或禁止边界。")
            continue
        scene = scene_by_id.get(app.scene_id)
        if scene is None:
            continue

        def scene_field(name: str, default: Any, scene_value: Any = scene) -> Any:
            if isinstance(scene_value, dict):
                return scene_value.get(name, default)
            return getattr(scene_value, name, default)

        scene_rule_ids = {str(item).strip() for item in list(scene_field("world_rule_ids", []))}
        scene_usage = str(scene_field("world_rule_usage", "") or "").strip()
        scene_evidence = [
            str(item).strip()
            for item in list(scene_field("world_rule_evidence_expectations", []))
            if str(item).strip()
        ]
        scene_boundaries = [
            str(item).strip()
            for item in list(scene_field("world_rule_forbidden_boundaries", []))
            if str(item).strip()
        ]
        if (
            rule.rule_id not in scene_rule_ids
            or not scene_usage
            or not scene_evidence
            or not scene_boundaries
        ):
            issues.append(
                f"硬规则 {rule.rule_id} 的应用记录未完整下沉到场景 {app.scene_id} "
                "的 world_rule 执行字段。"
            )
    return issues


def project_world_rule_card_for_stage(
    card: WorldRuleCard | dict[str, Any] | None,
    *,
    stage: str,
) -> dict[str, Any]:
    """Return the smallest rule view that lets one stage perform its job."""

    if not card:
        return {}
    source = coerce_world_rule_card(card)
    stage_name = str(stage or "").strip().lower()
    relevant_limit = {
        "bridge": 2,
        "plan": 8,
        "draft": 0,
        "scene": 0,
        "wave": 4,
        "review": 8,
        "edit": 8,
        "repair": 8,
        "continuity_repair": 8,
        "causal_repair": 8,
        "reading_power_repair": 2,
        "polish": 0,
        "humanize": 0,
    }.get(stage_name, 4)
    detailed = stage_name in {"plan", "review", "repair", "continuity_repair", "causal_repair"}

    def entry_payload(entry: WorldRuleCardEntry) -> dict[str, Any]:
        rule = entry.rule.model_dump(mode="json")
        if not detailed:
            rule = {
                key: rule.get(key)
                for key in (
                    "rule_id",
                    "content",
                    "severity",
                    "always_on",
                    "forbidden_behavior",
                    "cost_or_consequence",
                )
                if rule.get(key) not in (None, "", [], {})
            }
        return {
            "rule": rule,
            "selection_reason": entry.selection_reason,
            "applicable_scene_ids": list(entry.applicable_scene_ids),
        }

    relevant_entries = list(source.relevant_rules[:relevant_limit]) if relevant_limit else []
    return {
        "chapter_number": source.chapter_number,
        "rule_book_version": source.rule_book_version,
        "source_hash": source.source_hash,
        "view_stage": stage_name,
        "always_on": [entry_payload(item) for item in source.always_on],
        "relevant_rules": [entry_payload(item) for item in relevant_entries],
        # Only planning/review need to know that a source selection was capped;
        # writers receive scene-owned applications instead of irrelevant ids.
        "omitted_rule_ids": list(source.omitted_rule_ids)
        if stage_name in {"plan", "review"}
        else [],
    }


async def check_world_rule_compliance(
    *,
    runner: Any,
    chapter_text: str,
    chapter_number: int,
    world_rule_card: WorldRuleCard | dict[str, Any] | None,
    applications: list[WorldRuleApplication | dict[str, Any]],
    rule_ids: set[str] | None = None,
) -> WorldRuleComplianceReport:
    """Evaluate only rules the approved plan makes executable in this chapter.

    The rule card is a bounded source projection, not a mandate to re-check every
    listed rule on every pass.  ``applications`` is the Plan-owned applicability
    proof.  Only applications marked ``applied`` are executable checks.  A
    declared ``not_applicable`` application is authoritative, even for an
    always-on rule: it was selected for the chapter but the approved plan proves
    that its activation condition is absent.  ``always_on`` controls source-card
    visibility and writing guidance; it never bypasses Plan ownership at review.
    ``rule_ids`` supports a post-patch recheck of just the repaired rules.
    """

    if world_rule_card is None:
        return WorldRuleComplianceReport(
            chapter_number=chapter_number,
            source_text_hash=source_text_hash(chapter_text),
            summary="缺少 world_rule_card，无法执行世界规则审校。",
        )
    card = coerce_world_rule_card(world_rule_card)
    from novel_forge.pipeline.long.stages.quality_checks_runner import (
        check_guard_constraint_compliance,
    )

    application_by_rule: dict[str, WorldRuleApplication] = {}
    for value in applications:
        application = (
            value
            if isinstance(value, WorldRuleApplication)
            else WorldRuleApplication.model_validate(value)
        )
        if application.rule_id:
            application_by_rule[application.rule_id] = application

    entries: list[WorldRuleCardEntry] = []
    skipped_rule_ids: list[str] = []
    requested_rule_ids = set(rule_ids or [])
    for entry in card.all_entries:
        rule = entry.rule
        bound_application = application_by_rule.get(rule.rule_id)
        is_requested = not requested_rule_ids or rule.rule_id in requested_rule_ids
        is_executable = (
            bound_application is not None and bound_application.applicability == "applied"
        )
        if is_requested and is_executable:
            entries.append(entry)
        else:
            skipped_rule_ids.append(rule.rule_id)
    constraints = [
        _rule_constraint(entry.rule, application_by_rule.get(entry.rule.rule_id))
        for entry in entries
    ]
    raw_report = await check_guard_constraint_compliance(
        runner,
        None,
        None,
        chapter_text,
        chapter_number,
        constraints=constraints,
    )
    raw_results = list(raw_report.get("compliance_results") or [])
    issues: list[WorldRuleIssue] = []
    for index, entry in enumerate(entries):
        result = raw_results[index] if index < len(raw_results) else {}
        if not isinstance(result, dict):
            result = {}
        status = str(result.get("status") or "unknown")
        verdict = "compliant" if status == "compliant" else "conflict" if status == "non_compliant" else "unknown"
        if verdict == "compliant":
            continue
        severity = "medium"
        if verdict == "conflict" and entry.rule.severity == "hard":
            severity = "critical" if entry.rule.always_on else "high"
        adjudication_notes = str(result.get("notes") or "").strip()
        summary = (
            f"世界规则{('冲突' if verdict == 'conflict' else '无法确认')}："
            f"{entry.rule.content}"
        )
        if adjudication_notes:
            summary += f"；裁决理由：{adjudication_notes}"
        issues.append(
            WorldRuleIssue(
                rule_id=entry.rule.rule_id,
                verdict=verdict,
                severity=severity,
                summary=summary,
                evidence=str(result.get("evidence") or ""),
                repair_goal=(
                    "仅修复命中片段，使其满足该规则的禁止行为、前提或代价；不得扩写新设定。"
                ),
                root_cause="text" if verdict == "conflict" else "unknown",
            )
        )
    return WorldRuleComplianceReport(
        chapter_number=chapter_number,
        source_text_hash=source_text_hash(chapter_text),
        rule_book_hash=card.source_hash,
        checked_rule_ids=[entry.rule.rule_id for entry in entries],
        skipped_rule_ids=list(dict.fromkeys(skipped_rule_ids)),
        issues=issues,
        summary=(
            f"已检查 {len(entries)} 条本章可执行世界规则，"
            f"跳过 {len(set(skipped_rule_ids))} 条未绑定/不适用规则；"
            f"发现 {len(issues)} 条待处理问题。"
        ),
    )


def world_rule_report_to_findings(
    report: WorldRuleComplianceReport,
    *,
    include_unverified: bool = False,
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for issue in report.issues:
        if issue.verdict != "conflict" and not include_unverified:
            continue
        issue_id = stable_issue_id(
            "world_rule",
            chapter_number=report.chapter_number,
            issue_type="world_rule_conflict",
            summary=issue.summary,
            repair_surface="chapter_text",
        )
        findings.append(
            ReviewFinding(
                finding_id=f"world_rule_{issue_id}",
                chapter_number=report.chapter_number,
                source_module="world_rule_compliance",
                dimension="world_rule",
                issue_type="world_rule_conflict" if issue.verdict == "conflict" else "world_rule_unknown",
                severity=issue.severity,
                confidence=0.9 if issue.verdict == "conflict" else 0.5,
                summary=issue.summary,
                evidence_quote=issue.evidence,
                repair_goal=issue.repair_goal,
                suggested_mode="replace" if issue.evidence else "window",
                blocks_finalize=issue.verdict == "conflict" and issue.severity in {"critical", "high"},
                source_text_hash=report.source_text_hash,
                metadata={"rule_id": issue.rule_id, "root_cause": issue.root_cause},
            )
        )
    return findings


def _rule_constraint(
    rule: WorldRuleSpec,
    application: WorldRuleApplication | None = None,
) -> str:
    parts = [f"[世界规则 {rule.rule_id}] {rule.content}"]
    if rule.trigger_conditions:
        parts.append("触发条件：" + "；".join(rule.trigger_conditions))
    if rule.forbidden_behavior:
        parts.append("禁止：" + "；".join(rule.forbidden_behavior))
    if rule.cost_or_consequence:
        parts.append("代价/后果：" + "；".join(rule.cost_or_consequence))
    if rule.exceptions:
        parts.append("例外：" + "；".join(rule.exceptions))
    if application is not None:
        # The approved Plan owns chapter-level applicability and the concrete
        # compliance proof. Without this projection the reviewer sees only the
        # abstract rule and can misclassify the exact approved action.
        if application.usage:
            parts.append("已批准计划中的遵守方式：" + application.usage)
        if application.expected_evidence:
            parts.append("应视为合规的正文证据：" + application.expected_evidence)
        if application.forbidden_boundary:
            parts.append("计划明确禁止越过的边界：" + application.forbidden_boundary)
    return "。".join(parts)


def _entity_names(entities: list[Any]) -> list[str]:
    names: list[str] = []
    for entity in entities:
        if isinstance(entity, dict):
            name = entity.get("canonical_name") or entity.get("name")
        else:
            name = getattr(entity, "canonical_name", None) or getattr(entity, "name", None)
        if str(name or "").strip():
            names.append(str(name).strip())
    return names


__all__ = [
    "build_world_rule_card",
    "check_world_rule_compliance",
    "coerce_world_rule_card",
    "coerce_world_rule_book",
    "project_world_rule_card_for_stage",
    "validate_plan_world_rule_coverage",
    "validate_world_rule_book",
    "world_rule_report_to_findings",
]
