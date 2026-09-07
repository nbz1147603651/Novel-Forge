"""Init-repair policy for scene-level chapter plans."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan
from novel_forge.pipeline.long.services.constraints.world_rule_plan_contract import (
    materialize_plan_world_rule_bindings,
)
from novel_forge.pipeline.long.services.generation.scene_writing import validate_scene_plan_locally
from novel_forge.pipeline.long.services.init_repair.models import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairReport,
)


class ScenePlanRepairPolicy:
    """Targeted repair for scene-level planning structure."""

    artifact = InitArtifact.SCENE_PLAN

    def normalize(self, payload: Any, ctx: InitRepairContext) -> ChapterPlan:
        plan = _coerce_plan(payload)
        normalized = _normalize_plan_payload(plan)
        return ChapterPlan.model_validate(materialize_plan_world_rule_bindings(normalized))

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        try:
            plan = self.normalize(payload, ctx)
        except ValidationError as exc:
            return InitRepairReport(
                errors=[str(exc)],
                issues=[
                    InitRepairIssue(
                        kind=InitRepairIssueKind.SCHEMA_SHAPE,
                        message=str(exc),
                        field="scene_intents",
                    )
                ],
            )
        bridge = _bridge_from_context(ctx)
        target_word_count = int(ctx.artifacts.get("target_word_count") or 0)
        report = validate_scene_plan_locally(
            plan=plan,
            bridge=bridge,
            target_word_count=target_word_count,
        )
        issues = [
            _issue_from_scene_report(item)
            for item in list(report.get("issues", []) or [])
            if isinstance(item, dict)
            and str(item.get("severity", "") or "").lower() in {"critical", "high"}
        ]
        return InitRepairReport(
            errors=[issue.message for issue in issues],
            warnings=[
                str(item.get("message") or "")
                for item in list(report.get("issues", []) or [])
                if isinstance(item, dict)
                and str(item.get("severity", "") or "").lower() not in {"critical", "high"}
                and str(item.get("message") or "").strip()
            ],
            issues=issues,
            raw=report,
        )

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> ChapterPlan:
        service_ctx = ctx.service_ctx
        call_with_retry = getattr(service_ctx, "call_with_retry", None)
        if not callable(call_with_retry):
            raise RuntimeError("scene plan LLM repair requires call_with_retry")
        plan = self.normalize(payload, ctx)
        target_scene_ids = _target_scene_ids(report)
        if not target_scene_ids:
            raise RuntimeError("scene plan LLM repair has no focused scenes")
        response = await call_with_retry(
            TaskType.PLAN_CHAPTER_SCENES,
            {
                "chapter_outline": ctx.artifacts.get("chapter_outline", {}),
                "bridge": _bridge_from_context(ctx).model_dump(mode="json"),
                "scene_plan": plan.model_dump(mode="json"),
                "repair_issues": [issue.to_dict() for issue in report.issues],
                "focus_scene_ids": target_scene_ids,
            },
            required_keys=("scene_intents",),
            max_retries=2,
        )
        repaired = _merge_repaired_scene_intents(plan, response, target_scene_ids)
        return self.normalize(repaired, ctx)

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> ChapterPlan | None:
        plan = self.normalize(payload, ctx)
        target_scene_ids = _target_scene_ids(report)
        if not target_scene_ids:
            return None
        plan_data = plan.model_dump(mode="json")
        target_set = set(target_scene_ids)
        scenes = []
        for scene in list(plan_data.get("scene_intents") or []):
            if not isinstance(scene, dict):
                continue
            if str(scene.get("scene_id") or "") in target_set:
                scene = dict(scene)
                scene["dependency_scene_ids"] = []
                scene["parallel_group"] = ""
            scenes.append(scene)
        plan_data["scene_intents"] = scenes
        return self.normalize(plan_data, ctx)


def _coerce_plan(payload: Any) -> ChapterPlan:
    if isinstance(payload, ChapterPlan):
        return payload
    if hasattr(payload, "model_dump"):
        return ChapterPlan.model_validate(payload.model_dump(mode="json"))
    return ChapterPlan.model_validate(payload)


def _bridge_from_context(ctx: InitRepairContext) -> ChapterBridge:
    bridge = ctx.artifacts.get("bridge")
    if isinstance(bridge, ChapterBridge):
        return bridge
    if isinstance(bridge, dict):
        return ChapterBridge.model_validate(bridge)
    return ChapterBridge(to_chapter=max(1, int(ctx.artifacts.get("chapter_number") or 1)))


def _normalize_plan_payload(payload: Any) -> dict[str, Any]:
    plan_data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
    scenes = []
    used: set[str] = set()
    for index, scene in enumerate(list(plan_data.get("scene_intents") or []), start=1):
        if not isinstance(scene, dict):
            continue
        item = dict(scene)
        scene_id = str(item.get("scene_id") or "").strip()
        if not scene_id or scene_id in used:
            scene_id = _unique_scene_id(index, used)
        used.add(scene_id)
        item["scene_id"] = scene_id
        if int(item.get("draft_order") or 0) <= 0:
            item["draft_order"] = index
        # Upgrade legacy scene plans created before ``owned_events`` became a
        # required atomic acceptance list.  Each semicolon/newline-delimited
        # hard outcome is already an explicit authored obligation, so carrying
        # it forward does not invent narrative content.
        if not list(item.get("owned_events") or []):
            raw_outcome = str(item.get("required_outcome") or "")
            owned_events = [
                part.strip() for part in re.split(r"[；;\n]+", raw_outcome) if part.strip()
            ]
            if owned_events:
                item["owned_events"] = list(dict.fromkeys(owned_events))
        scenes.append(item)
    id_set = {str(item.get("scene_id") or "") for item in scenes}
    for item in scenes:
        scene_id = str(item.get("scene_id") or "")
        deps = []
        seen_deps: set[str] = set()
        for dep in list(item.get("dependency_scene_ids") or []):
            dep_id = str(dep or "").strip()
            if dep_id and dep_id in id_set and dep_id != scene_id and dep_id not in seen_deps:
                seen_deps.add(dep_id)
                deps.append(dep_id)
        item["dependency_scene_ids"] = deps
    _break_dependency_cycles(scenes)
    plan_data["scene_intents"] = scenes
    return plan_data


def _unique_scene_id(index: int, used: set[str]) -> str:
    candidate = f"scene_{index:02d}"
    suffix = index
    while candidate in used:
        suffix += 1
        candidate = f"scene_{suffix:02d}"
    return candidate


def _break_dependency_cycles(scenes: list[dict[str, Any]]) -> None:
    id_set = {str(item.get("scene_id") or "") for item in scenes}
    for _ in range(len(scenes)):
        edges = {
            str(item.get("scene_id") or ""): [
                str(dep or "")
                for dep in list(item.get("dependency_scene_ids") or [])
                if str(dep or "") in id_set
            ]
            for item in scenes
        }
        cycle = _first_cycle(edges)
        if not cycle:
            return
        cycle_set = set(cycle)
        for item in scenes:
            if str(item.get("scene_id") or "") in cycle_set:
                item["dependency_scene_ids"] = [
                    dep
                    for dep in list(item.get("dependency_scene_ids") or [])
                    if str(dep or "") not in cycle_set
                ]
                break


def _first_cycle(edges: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            try:
                return stack[stack.index(node) :]
            except ValueError:
                return [node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for dep in edges.get(node, []):
            cycle = visit(dep)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in edges:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def _issue_from_scene_report(item: dict[str, Any]) -> InitRepairIssue:
    code = str(item.get("code") or "scene_plan_issue")
    kind = (
        InitRepairIssueKind.SCHEMA_SHAPE
        if code in {"duplicate_scene_id", "missing_scenes", "invalid_dependency"}
        else InitRepairIssueKind.SEMANTIC_CONFLICT
    )
    return InitRepairIssue(
        kind=kind,
        message=str(item.get("message") or code),
        field="scene_intents",
        metadata={
            "code": code,
            "scene_ids": [
                str(scene_id)
                for scene_id in list(item.get("scene_ids") or [])
                if str(scene_id or "").strip()
            ],
        },
    )


def _target_scene_ids(report: InitRepairReport) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for issue in report.issues:
        for scene_id in list(issue.metadata.get("scene_ids") or []):
            text = str(scene_id or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def _merge_repaired_scene_intents(
    plan: ChapterPlan,
    response: Any,
    target_scene_ids: list[str],
) -> dict[str, Any]:
    data = plan.model_dump(mode="json")
    raw_scenes = response.get("scene_intents") if isinstance(response, dict) else None
    if not isinstance(raw_scenes, list):
        scene_plan = response.get("scene_plan") if isinstance(response, dict) else None
        raw_scenes = scene_plan.get("scene_intents") if isinstance(scene_plan, dict) else []
    if not isinstance(raw_scenes, list):
        raw_scenes = []
    replacements = {
        str(item.get("scene_id") or ""): item
        for item in raw_scenes
        if isinstance(item, dict) and str(item.get("scene_id") or "") in set(target_scene_ids)
    }
    if not replacements:
        raise RuntimeError("scene plan LLM repair returned no focused scene intents")
    data["scene_intents"] = [
        replacements.get(str(item.get("scene_id") or ""), item)
        for item in list(data.get("scene_intents") or [])
        if isinstance(item, dict)
    ]
    return data


__all__ = ["ScenePlanRepairPolicy"]
