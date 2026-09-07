"""Plan-owned world-rule bindings and their deterministic scene projection."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.schemas.world_rules import (
    WorldRuleApplication,
    WorldRuleCard,
)
from novel_forge.core.utils.type_coerce import coerce_text_list
from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    coerce_world_rule_card,
)


def _numeric_suffix(value: str) -> int | None:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else None


def resolve_plan_scene_id(scene_id: str, scenes: list[dict[str, Any]]) -> str:
    """Resolve a provider scene id against the current normalized namespace."""

    requested = str(scene_id or "").strip()
    if not requested:
        return ""
    scene_ids = [str(scene.get("scene_id") or "").strip() for scene in scenes]
    if requested in scene_ids:
        return requested
    ordinal = _numeric_suffix(requested)
    if ordinal is None:
        return requested
    candidates = [
        current
        for current, scene in zip(scene_ids, scenes, strict=False)
        if current
        and (_numeric_suffix(current) == ordinal or int(scene.get("draft_order") or 0) == ordinal)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if 1 <= ordinal <= len(scene_ids):
        return scene_ids[ordinal - 1]
    return requested


def _label(rule_id: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if rule_id in text else f"[{rule_id}] {text}"


def _append_text(existing: Any, rule_id: str, addition: Any) -> str:
    current = str(existing or "").strip()
    incoming = _label(rule_id, addition)
    if not incoming or incoming in current:
        return current
    return f"{current}\n{incoming}" if current else incoming


def _append_list(existing: Any, rule_id: str, addition: Any) -> list[str]:
    items = coerce_text_list(existing)
    for raw in coerce_text_list(addition):
        item = _label(rule_id, raw)
        if item and item not in items:
            items.append(item)
    return items


def materialize_plan_world_rule_bindings(plan: Any) -> dict[str, Any]:
    """Build all prose-facing scene fields from the Plan application ledger.

    ``world_rule_applications`` is the sole semantic source. Scene fields are
    rebuilt on every boundary crossing, so model aliases, scene-id rewrites,
    and focused repairs cannot create a second, drifting rule contract.
    """

    if hasattr(plan, "model_dump"):
        payload = plan.model_dump(mode="json")
    elif isinstance(plan, dict):
        payload = dict(plan)
    else:
        return {}
    scenes = [
        dict(scene) for scene in list(payload.get("scene_intents") or []) if isinstance(scene, dict)
    ]
    for scene in scenes:
        scene["world_rule_ids"] = []
        scene["world_rule_usage"] = ""
        scene["world_rule_evidence_expectations"] = []
        scene["world_rule_forbidden_boundaries"] = []

    raw_applications = payload.get("world_rule_applications", [])
    if isinstance(raw_applications, dict):
        raw_applications = [raw_applications]
    applications: list[dict[str, Any]] = []
    scene_by_id = {
        str(scene.get("scene_id") or "").strip(): scene
        for scene in scenes
        if str(scene.get("scene_id") or "").strip()
    }
    for raw in list(raw_applications or []):
        if not isinstance(raw, dict):
            continue
        application = WorldRuleApplication.model_validate(raw).model_dump(mode="json")
        if application["applicability"] != "applied":
            applications.append(application)
            continue
        resolved_scene_id = resolve_plan_scene_id(application["scene_id"], scenes)
        if resolved_scene_id:
            application["scene_id"] = resolved_scene_id
        applications.append(application)
        scene = scene_by_id.get(resolved_scene_id)
        rule_id = str(application.get("rule_id") or "").strip()
        if scene is None or not rule_id:
            continue
        rule_ids = coerce_text_list(scene.get("world_rule_ids"))
        if rule_id not in rule_ids:
            rule_ids.append(rule_id)
        scene["world_rule_ids"] = rule_ids
        scene["world_rule_usage"] = _append_text(
            scene.get("world_rule_usage"), rule_id, application.get("usage")
        )
        scene["world_rule_evidence_expectations"] = _append_list(
            scene.get("world_rule_evidence_expectations"),
            rule_id,
            application.get("expected_evidence"),
        )
        scene["world_rule_forbidden_boundaries"] = _append_list(
            scene.get("world_rule_forbidden_boundaries"),
            rule_id,
            application.get("forbidden_boundary"),
        )
    payload["scene_intents"] = scenes
    payload["world_rule_applications"] = applications
    return payload


def validate_plan_world_rule_coverage(
    plan: Any, card: WorldRuleCard | dict[str, Any] | None
) -> list[str]:
    """Validate every selected hard-rule application and its scene projection."""

    if card is None:
        return ["章节源头缺少 world_rule_card，必须重新初始化。"]
    resolved_card = coerce_world_rule_card(card)
    applications = list(getattr(plan, "world_rule_applications", []) or [])
    scenes = list(getattr(plan, "scene_intents", []) or [])
    if isinstance(plan, dict):
        applications = list(plan.get("world_rule_applications", []) or [])
        scenes = list(plan.get("scene_intents", []) or [])
    grouped: dict[str, list[WorldRuleApplication]] = {}
    for item in applications:
        application = (
            item
            if isinstance(item, WorldRuleApplication)
            else WorldRuleApplication.model_validate(item)
        )
        if application.rule_id:
            grouped.setdefault(application.rule_id, []).append(application)
    scene_by_id: dict[str, Any] = {}
    for scene in scenes:
        scene_id = str(
            scene.get("scene_id") if isinstance(scene, dict) else getattr(scene, "scene_id", "")
        ).strip()
        if scene_id:
            scene_by_id[scene_id] = scene

    issues: list[str] = []
    for entry in resolved_card.all_entries:
        rule = entry.rule
        if rule.severity != "hard":
            continue
        rule_applications = grouped.get(rule.rule_id, [])
        if not rule_applications:
            issues.append(f"硬规则 {rule.rule_id} 未绑定场景，也未声明不适用。")
            continue
        for application in rule_applications:
            if application.applicability == "not_applicable":
                if not application.not_applicable_reason:
                    issues.append(f"硬规则 {rule.rule_id} 标记不适用但未说明理由。")
                continue
            if not application.scene_id or application.scene_id not in scene_by_id:
                issues.append(f"硬规则 {rule.rule_id} 未绑定有效 scene_id。")
                continue
            if (
                not application.usage
                or not application.expected_evidence
                or not application.forbidden_boundary
            ):
                issues.append(f"硬规则 {rule.rule_id} 缺少遵守方式、预期正文证据或禁止边界。")
                continue
            scene = scene_by_id[application.scene_id]

            def field(name: str, default: Any, scene_value: Any = scene) -> Any:
                if isinstance(scene_value, dict):
                    return scene_value.get(name, default)
                return getattr(scene_value, name, default)

            if (
                rule.rule_id not in set(coerce_text_list(field("world_rule_ids", [])))
                or not str(field("world_rule_usage", "") or "").strip()
                or not coerce_text_list(field("world_rule_evidence_expectations", []))
                or not coerce_text_list(field("world_rule_forbidden_boundaries", []))
            ):
                issues.append(
                    f"硬规则 {rule.rule_id} 的应用记录未完整下沉到场景 "
                    f"{application.scene_id} 的 world_rule 执行字段。"
                )
    return issues
