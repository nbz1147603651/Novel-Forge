"""Implementation slice extracted from init_service.py (init_repair_targets.py)."""

from __future__ import annotations

from typing import Literal

from novel_forge.core.parsing.token_utils import count_text_tokens
from novel_forge.core.schemas.repair import RepairCandidate, RepairCase
from novel_forge.pipeline.long.services.init.init_common import (
    CHAPTER_CONTRACTS_ARTIFACT,
    OUTLINE_ARTIFACT,
    Any,
    InitCoherenceError,
    InitLongServiceContext,
    RepairFailurePolicy,
    RepairTarget,
    TaskCircuitOpenError,
    TaskType,
    _log,
    apply_scoped_init_patch,
    apply_targeted_init_patch,
    backup_init_artifact,
    blocking_issues,
    calculate_route_aware_max_tokens,
    collect_repair_scopes,
    copy,
    has_repair_scope,
    init_coherence_issue_id,
    json,
    normalize_artifact_key,
    normalize_coherence_report,
    resolve_init_repair_targets,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _record_init_repair_manifest_round,
)
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _init_coherence_event_payload,
    _init_coherence_max_repair_rounds,
    _init_coherence_report_path,
)
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_blocking_issue_ids,
    _init_coherence_list,
    _init_coherence_min_severity,
    _safe_init_int,
)


def _init_coherence_repair_round_start(
    settings: Any,
    repairs: list[dict[str, Any]],
    *,
    artifact: str,
    report: dict[str, Any],
) -> int:
    current_issue_ids = _init_coherence_blocking_issue_ids(settings, report)
    if not current_issue_ids:
        return 0
    max_round = 0
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        if normalize_artifact_key(repair.get("artifact")) != artifact:
            continue
        if (
            str(repair.get("status") or "") == "no_effect"
            and str(repair.get("repair_type") or "") == "summary_only"
        ):
            continue
        source_issue_ids = {
            str(item) for item in _init_coherence_list(repair.get("source_issue_ids")) if item
        }
        if not source_issue_ids or current_issue_ids.isdisjoint(source_issue_ids):
            continue
        max_round = max(max_round, _safe_init_int(repair.get("round")))
    return max_round


async def _adjudicate_init_artifact(
    ctx: InitLongServiceContext,
    *,
    task_type: TaskType,
    report_name: str,
    step_name: str,
    artifact: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    report = await ctx.call_with_retry(
        task_type,
        context,
        max_tokens=calculate_route_aware_max_tokens(
            ctx.router,
            task_type,
            4096,
            prompt_overhead=5200,
            min_tokens=2048,
        ),
        temperature=getattr(ctx.settings, f"temp_{task_type.value}", 0.1),
        required_keys=(
            "verdict",
            "issues",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
            "summary",
        ),
        max_retries=3,
    )
    normalized = normalize_coherence_report(report, artifact=artifact)
    ctx.storage.save_json(_init_coherence_report_path(ctx.layout, report_name), normalized)
    ctx.on_step(step_name, _init_coherence_event_payload(normalized))
    return normalized


def _record_init_artifact_repair_failure(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    round_index: int,
    repairs: list[dict[str, Any]],
    source_issue_ids: list[str],
    source_blocking_issue_count: int,
    exc: Exception,
) -> None:
    kind = RepairFailurePolicy.failure_kind(exc)
    record = {
        "artifact": artifact,
        "round": round_index,
        "status": "failed",
        "failed": True,
        "source_issue_ids": sorted(set(source_issue_ids)),
        "source_blocking_issue_count": source_blocking_issue_count,
        "patch_count": 0,
        "skipped_patch_count": 0,
        "patches": [],
        "skipped_patches": [],
        "error_kind": kind.value,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    repairs.append(record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    _record_init_repair_manifest_round(ctx, record)
    ctx.on_step("repair_init_artifact_patch_failed", record)


def _record_init_artifact_repair_skipped(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    round_index: int,
    repairs: list[dict[str, Any]],
    source_issue_ids: list[str],
    source_blocking_issue_count: int,
    exc: Exception,
) -> None:
    kind = RepairFailurePolicy.failure_kind(exc)
    record = {
        "artifact": artifact,
        "round": round_index,
        "status": "skipped",
        "failed": False,
        "source_issue_ids": sorted(set(source_issue_ids)),
        "source_blocking_issue_count": source_blocking_issue_count,
        "patch_count": 0,
        "skipped_patch_count": 0,
        "patches": [],
        "skipped_patches": [],
        "error_kind": kind.value,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    repairs.append(record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    _record_init_repair_manifest_round(ctx, record)
    ctx.on_step("repair_init_artifact_patch_skipped", record)


def _record_init_artifact_repair_no_effect(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    round_index: int,
    repairs: list[dict[str, Any]],
    source_issue_ids: list[str],
    source_blocking_issue_count: int,
    skipped_patches: list[dict[str, Any]],
    summary: str,
    repair_type: str,
    target_count: int = 0,
    located_count: int = 0,
    attempted_patches: list[dict[str, Any]] | None = None,
) -> None:
    record = {
        "artifact": artifact,
        "round": round_index,
        "status": "no_effect",
        "failed": False,
        "repair_type": repair_type,
        "source_issue_ids": sorted(set(source_issue_ids)),
        "source_blocking_issue_count": source_blocking_issue_count,
        "patch_count": 0,
        "skipped_patch_count": len(skipped_patches),
        "patches": [],
        "skipped_patches": skipped_patches,
        "summary": summary,
    }
    if attempted_patches:
        record["attempted_patches"] = attempted_patches
    if target_count or located_count:
        record["target_count"] = target_count
        record["located_count"] = located_count
        record["applied_target_count"] = 0
        record["skipped_target_count"] = sum(
            1 for item in skipped_patches if isinstance(item, dict) and item.get("target_id")
        )
    repairs.append(record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    _record_init_repair_manifest_round(ctx, record)
    ctx.on_step("repair_init_artifact_patch_no_effect", record)


_OUTLINE_IRREVERSIBLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("幼态人格最后一次在意识中提醒", "幼态人格在意识中再次提醒"),
    ("幼态人格最后一次提醒", "幼态人格给出一次关键提醒"),
    ("幼态人格最后一次出现", "幼态人格一次关键出现"),
    ("最后一次扯他袖口", "一次关键地扯他袖口"),
    ("最后一次出现", "一次关键出现"),
    ("最后一次提醒", "一次关键提醒"),
    ("彻底融入主人格，完成核心人格整合", "与主人格达成阶段性和解，整合进程取得关键突破"),
    ("彻底融入主人格", "与主人格达成阶段性和解"),
    ("完成核心人格整合", "推动核心人格整合进入巩固阶段"),
    ("人格整合初步完成", "人格整合进入稳定巩固阶段"),
    ("首次完全感知不到幼态人格的存在", "连续多日未感知到幼态人格的活动"),
    ("完全感知不到幼态人格的存在", "暂时感知不到幼态人格的活动"),
    ("以后我不用再出来", "以后我会少出来"),
    ("不用再出来", "会少出来"),
    ("不再出来", "减少出来"),
)


def _init_target_prompt_payload(targets: list[RepairTarget]) -> list[dict[str, Any]]:
    return [target.to_prompt_dict() for target in targets]


def _init_repair_target_batch_size(settings: Any, max_ops: int) -> int:
    raw = getattr(settings, "init_coherence_target_patch_batch_size", 12)
    try:
        value = int(raw or 12)
    except (TypeError, ValueError):
        value = 12
    return max(1, min(max_ops, value))


def _init_repair_issue_evidence_map(
    issues: list[dict[str, Any]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = init_coherence_issue_id(issue)
        tokens = _init_repair_evidence_tokens(
            issue.get("unresolved_entity") or issue.get("evidence") or issue.get("target_value")
        )
        if tokens:
            result[issue_id] = tokens
    return result


def _init_repair_evidence_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    tokens: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > 160:
            continue
        if text not in tokens:
            tokens.append(text)
    return tokens


def _init_repair_value_preview(value: Any, *, max_chars: int = 120) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = str(value)
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _init_repair_attempted_patch_preview(value: Any) -> list[dict[str, Any]]:
    patches = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for patch in patches[:8]:
        if not isinstance(patch, dict):
            continue
        patch_value = patch.get("value")
        preview: dict[str, Any] = {
            "target_id": str(patch.get("target_id") or ""),
            "path": str(patch.get("path") or ""),
            "issue_ids": _init_coherence_list(patch.get("issue_ids")),
            "rationale": str(patch.get("rationale") or "")[:160],
            "value_type": type(patch_value).__name__,
            "value_preview": _init_repair_value_preview(patch_value),
        }
        result.append({key: item for key, item in preview.items() if item != "" and item != []})
    return result


def _init_repair_patch_guidance_preview(value: Any, *, limit: int = 6) -> list[str]:
    items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        parts: list[str] = []
        for key in ("target_id", "path", "field", "chapter_number"):
            if item.get(key) not in (None, "", []):
                parts.append(f"{key}={item.get(key)}")
        issue_ids = _init_coherence_list(item.get("issue_ids"))
        if issue_ids:
            parts.append("issue_ids=" + ",".join(str(issue_id) for issue_id in issue_ids[:4]))
        if item.get("value_preview"):
            parts.append(f"value={item.get('value_preview')}")
        if item.get("rationale"):
            parts.append(f"rationale={str(item.get('rationale'))[:80]}")
        if item.get("reason"):
            parts.append(f"reason={str(item.get('reason'))[:80]}")
        if parts:
            result.append("；".join(parts))
    return result


def _build_init_target_repair_guidance(
    settings: Any,
    *,
    artifact: str,
    round_index: int,
    repairs: list[dict[str, Any]],
    source_issue_ids: list[str],
    targets: list[RepairTarget],
) -> dict[str, Any] | None:
    if round_index <= 1:
        return None
    source_id_set = {str(item) for item in source_issue_ids if item}
    previous: dict[str, Any] | None = None
    for repair in reversed(repairs):
        if not isinstance(repair, dict):
            continue
        if normalize_artifact_key(repair.get("artifact")) != artifact:
            continue
        repair_source_ids = {
            str(item) for item in _init_coherence_list(repair.get("source_issue_ids")) if item
        }
        if source_id_set and repair_source_ids and source_id_set.isdisjoint(repair_source_ids):
            continue
        previous = repair
        break
    if previous is None:
        return None
    skipped = [
        str(item.get("reason") or "")
        for item in previous.get("skipped_patches") or []
        if isinstance(item, dict) and item.get("reason")
    ][:6]
    previous_attempted = _init_repair_patch_guidance_preview(
        previous.get("attempted_patches") or []
    )
    previous_applied = _init_repair_patch_guidance_preview(previous.get("patches") or [])
    previous_skipped = _init_repair_patch_guidance_preview(previous.get("skipped_patches") or [])
    target_preview = [
        f"{target.target_id}={str(target.current_value)[:80]}" for target in targets[:6]
    ]
    diagnosis = ["上一轮 target/patch 未能完全应用，本轮必须只使用 repair_targets 中的 target_id。"]
    if skipped:
        diagnosis.append("上一轮拒绝原因：" + "；".join(skipped))
    if previous_attempted:
        diagnosis.append("上一轮模型尝试：" + "；".join(previous_attempted))
    if previous_applied:
        diagnosis.append("上一轮已应用：" + "；".join(previous_applied))
    if target_preview:
        diagnosis.append("当前真实目标旧值：" + "；".join(target_preview))
    guidance = {
        "round_number": round_index,
        "max_rounds": _init_coherence_max_repair_rounds(settings),
        "strategy_id": "target_id_repair",
        "new_direction": "只改已锁定 target 的 current_value，不自行推断 JSON Pointer 或旧值。",
        "avoid": "禁止输出 path、expected_old_value、expected_old_hash；禁止复用上一轮被拒绝的旧值。",
        "steps": [
            "逐个读取 repair_targets 的 current_value。",
            "只返回 target_id、value、issue_ids、rationale。",
            "value 必须与 current_value 类型一致，并且只消除本轮冲突。",
        ],
        "diagnosis": diagnosis,
    }
    if previous_attempted:
        guidance["previous_attempted_patches"] = previous_attempted
    if previous_applied:
        guidance["previous_applied_patches"] = previous_applied
    if previous_skipped:
        guidance["previous_skipped_patches"] = previous_skipped
    return guidance


def _try_outline_irreversible_target_fallback(
    *,
    payload: dict[str, Any],
    report: dict[str, Any],
    targets: list[RepairTarget],
    scopes: list[Any],
    max_ops: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not targets or not _report_has_irreversible_outline_issue(report):
        return payload, [], []
    patches: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target.current_value, str):
            continue
        rewritten = _rewrite_outline_irreversible_value(target.current_value)
        if rewritten == target.current_value:
            continue
        patches.append(
            {
                "target_id": target.target_id,
                "value": rewritten,
                "issue_ids": list(target.issue_ids),
                "rationale": "本地兜底：将不可逆完成/最后一次表述降级为阶段性整合进展。",
            }
        )
    if not patches:
        return payload, [], []
    skipped: list[dict[str, Any]] = []
    repaired, applied = apply_targeted_init_patch(
        artifact=OUTLINE_ARTIFACT,
        payload=payload,
        patch_payload={
            "patches": patches[:max_ops],
            "summary": "本地兜底修复 outline 不可逆事件重复声明。",
        },
        target_map={target.target_id: target for target in targets},
        scopes=scopes,
        max_ops=max_ops,
        strict=False,
        skipped=skipped,
    )
    return repaired, applied, skipped


def _report_has_irreversible_outline_issue(report: dict[str, Any]) -> bool:
    for issue in report.get("issues", []) or []:
        if not isinstance(issue, dict):
            continue
        issue_type = str(issue.get("type") or issue.get("issue_type") or "").lower()
        text = " ".join(
            str(issue.get(key) or "") for key in ("description", "summary", "change_intent")
        )
        if "irreversible" in issue_type or "不可逆" in text or "最后一次" in text:
            return True
    return False


def _rewrite_outline_irreversible_value(value: str) -> str:
    rewritten = value
    for old, new in _OUTLINE_IRREVERSIBLE_REPLACEMENTS:
        rewritten = rewritten.replace(old, new)
    return rewritten


def _merge_pruned_fields(
    original: dict[str, Any],
    pruned: dict[str, Any],
    pruned_fields: list[str],
) -> dict[str, Any]:
    """Merge pruned fields back from original into pruned (possibly repaired) payload."""
    if not pruned_fields:
        return pruned
    import copy

    result = copy.deepcopy(pruned)
    for field_path in pruned_fields:
        key = field_path.lstrip("/")
        if key in original:
            result[key] = original[key]
    return result


def _apply_prune_strategy(
    payload: dict[str, Any],
    strategy: Literal["none", "outline", "chapter_contracts", "blueprint"],
) -> tuple[dict[str, Any], list[str]]:
    """Apply field-level pruning to reduce payload size before LLM repair.

    Returns (pruned_payload, pruned_field_paths) where pruned_field_paths
    lists the JSON Pointer paths of fields that were removed/replaced.

    "none": Return original payload unchanged.
    "blueprint": Remove verbose detail fields, preserve structural signatures.

    "outline" / "chapter_contracts": Placeholder — no pruning yet (T20 handles chunking).
    """
    if strategy == "none":
        return payload, []

    if strategy in ("outline", "chapter_contracts"):
        # Placeholder: T20 will handle chunking for these artifacts.
        return payload, []

    # ── blueprint strategy ──────────────────────────────────────────
    import copy

    pruned = copy.deepcopy(payload)
    removed: list[str] = []

    # 1. narrative_hooks → narrative_hooks_count (remove detail, keep count)
    if "narrative_hooks" in pruned:
        hooks = pruned.pop("narrative_hooks")
        if isinstance(hooks, list):
            pruned["narrative_hooks_count"] = len(hooks)
        elif isinstance(hooks, dict):
            pruned["narrative_hooks_count"] = len(hooks)
        else:
            pruned["narrative_hooks_count"] = 1 if hooks else 0
        removed.append("/narrative_hooks")
        _log.debug(
            "prune_blueprint_narrative_hooks | count=%d",
            pruned["narrative_hooks_count"],
        )

    # 2. suspense_schedule → suspense_schedule_summary (remove detail, keep summary)
    if "suspense_schedule" in pruned:
        schedule = pruned.pop("suspense_schedule")
        if isinstance(schedule, list):
            pruned["suspense_schedule_summary"] = f"{len(schedule)} suspense items"
        elif isinstance(schedule, dict):
            pruned["suspense_schedule_summary"] = schedule.get(
                "summary", f"suspense schedule with {len(schedule)} entries"
            )
        elif isinstance(schedule, str):
            pruned["suspense_schedule_summary"] = schedule[:200]
        else:
            pruned["suspense_schedule_summary"] = "suspense schedule (pruned)"
        removed.append("/suspense_schedule")
        _log.debug(
            "prune_blueprint_suspense_schedule | summary=%s",
            str(pruned["suspense_schedule_summary"])[:200],
        )

    # 3. subplot_plan → subplot_plan_summary (remove detail, keep summary)
    if "subplot_plan" in pruned:
        plan = pruned.pop("subplot_plan")
        if isinstance(plan, list):
            names = []
            for item in plan[:10]:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    if name:
                        names.append(str(name))
            summary = f"{len(plan)} subplots"
            if names:
                summary += f": {', '.join(names[:5])}"
                if len(names) > 5:
                    summary += f" ... (+{len(names) - 5} more)"
            pruned["subplot_plan_summary"] = summary
        elif isinstance(plan, dict):
            pruned["subplot_plan_summary"] = plan.get(
                "summary", f"subplot plan with {len(plan)} entries"
            )
        elif isinstance(plan, str):
            pruned["subplot_plan_summary"] = plan[:500]
        else:
            pruned["subplot_plan_summary"] = "subplot plan (pruned)"
        removed.append("/subplot_plan")
        _log.debug(
            "prune_blueprint_subplot_plan | summary=%s",
            str(pruned["subplot_plan_summary"])[:200],
        )

    return pruned, removed


def _compact_init_repair_report(
    report: dict[str, Any],
    *,
    min_severity: str,
) -> dict[str, Any]:
    """Keep only adjudication fields needed by the patch repair prompt."""

    issues = blocking_issues(report, min_severity=min_severity)
    if not issues:
        raw_issues = report.get("issues") or []
        issues = [item for item in raw_issues if isinstance(item, dict)]
    compact = {
        "artifact": report.get("artifact"),
        "verdict": report.get("verdict"),
        "issues": issues,
        "source_refs": _init_coherence_list(report.get("source_refs"))[:32],
        "repair_scope": _init_coherence_list(report.get("repair_scope")),
        "preserve": _init_coherence_list(report.get("preserve"))[:32],
        "change_intent": str(report.get("change_intent") or ""),
        "blocked": bool(report.get("blocked", False)),
        "summary": str(report.get("summary") or ""),
    }
    for key in ("focused_issue_id", "point_repair"):
        if key in report:
            compact[key] = report[key]
    return compact


def _single_issue_init_repair_report(
    report: dict[str, Any],
    issue: dict[str, Any],
) -> dict[str, Any]:
    """Build a focused repair report for one blocking issue."""

    issue_id = init_coherence_issue_id(issue)
    issue_scope = _init_coherence_list(issue.get("repair_scope"))
    report_scope = _init_coherence_list(report.get("repair_scope"))
    description = str(issue.get("description") or issue.get("summary") or "").strip()
    return {
        **report,
        "issues": [issue],
        "repair_scope": issue_scope or report_scope,
        "change_intent": (
            str(issue.get("repair_hint") or issue.get("suggestion") or "").strip()
            or str(report.get("change_intent") or "").strip()
        ),
        "summary": f"点对点修复 {issue_id}" + (f"：{description[:240]}" if description else ""),
        "focused_issue_id": issue_id,
        "point_repair": True,
    }


def _chapter_number_from_scoped_item(item: dict[str, Any]) -> int:
    try:
        return int(item.get("chapter_number") or 0)
    except (TypeError, ValueError):
        return 0


def _build_scoped_init_repair_payload(
    payload: dict[str, Any],
    *,
    artifact: str,
    scopes: list[Any],
) -> dict[str, Any] | None:
    """Build a compact payload that preserves original chapter list indexes.

    The repair prompt only needs fields that the adjudicator allowed the
    patcher to edit, but JSON Patch paths still have to point at the
    original artifact.  Keeping the full chapter list with slim placeholders
    gives the model stable indexes while keeping the prompt small.
    """

    normalized = normalize_artifact_key(artifact)
    if normalized == OUTLINE_ARTIFACT:
        list_key = "chapters"
        base_fields = ("chapter_number", "title", "goal")
        top_fields = ("title", "total_chapters", "structure", "volumes")
    elif normalized == CHAPTER_CONTRACTS_ARTIFACT:
        list_key = "chapter_contracts"
        base_fields = ("chapter_number", "title", "source")
        top_fields = ("coverage", "schema_version")
    else:
        return None

    items = payload.get(list_key)
    if not isinstance(items, list) or not items:
        return None

    fields_by_chapter: dict[int, set[str]] = {}
    for scope in scopes:
        if normalize_artifact_key(getattr(scope, "artifact", "")) != normalized:
            continue
        scope_fields = set(getattr(scope, "fields", set()) or set())
        for chapter in getattr(scope, "chapters", set()) or set():
            try:
                chapter_number = int(chapter)
            except (TypeError, ValueError):
                continue
            fields_by_chapter.setdefault(chapter_number, set()).update(scope_fields)

    if not fields_by_chapter:
        return None

    compact: dict[str, Any] = {
        key: copy.deepcopy(payload[key]) for key in top_fields if key in payload
    }
    compact_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            compact_items.append({})
            continue
        chapter_number = _chapter_number_from_scoped_item(item)
        slim = {key: copy.deepcopy(item[key]) for key in base_fields if key in item}
        for field in sorted(fields_by_chapter.get(chapter_number, set())):
            if field in item:
                slim[field] = copy.deepcopy(item[field])
        compact_items.append(slim)
    compact[list_key] = compact_items
    return compact


def _estimate_init_repair_prompt_tokens(
    *,
    artifact: str,
    artifact_payload: dict[str, Any],
    coherence_report: dict[str, Any],
    repair_scope: list[dict[str, Any]],
    preserve: list[Any],
    change_intent: str,
    max_ops: int,
    repair_targets: list[dict[str, Any]] | None = None,
    repair_attempt_guidance: dict[str, Any] | None = None,
) -> int:
    prompt_text = json.dumps(
        {
            "artifact": artifact,
            "artifact_payload": artifact_payload,
            "coherence_report": coherence_report,
            "repair_scope": repair_scope,
            "preserve": preserve,
            "change_intent": change_intent,
            "max_ops": max_ops,
            "repair_targets": repair_targets or [],
            "repair_attempt_guidance": repair_attempt_guidance or None,
        },
        ensure_ascii=False,
    )
    return max(1, count_text_tokens(prompt_text).tokens)


async def _repair_init_artifact_payload(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    payload: dict[str, Any],
    report: dict[str, Any],
    round_index: int,
    repairs: list[dict[str, Any]],
    prune_strategy: Literal["none", "outline", "chapter_contracts", "blueprint"] = "none",
    split_issues: bool = True,
) -> dict[str, Any]:
    """Build semantic candidates from one baseline and adopt only a verified one."""

    from pathlib import Path
    from tempfile import TemporaryDirectory

    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
    from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
        run_init_coherence_v2_gate,
    )
    from novel_forge.pipeline.long.services.init.init_semantic_repair_cases import (
        record_semantic_candidate_verification,
        semantic_repair_case_ids,
    )

    case_ids = semantic_repair_case_ids(report)
    semantic_inputs = getattr(ctx, "_semantic_gate_inputs", None)
    stage = str(report.get("stage") or "")
    gate_input = semantic_inputs.get(stage) if isinstance(semantic_inputs, dict) else None
    if (
        not case_ids
        or not isinstance(gate_input, dict)
        or bool(getattr(ctx, "_semantic_candidate_verification", False))
    ):
        return await _repair_init_artifact_payload_once(
            ctx,
            artifact=artifact,
            payload=payload,
            report=report,
            round_index=round_index,
            repairs=repairs,
            prune_strategy=prune_strategy,
            split_issues=split_issues,
        )

    # Multiple semantic issues retain the existing one-issue-at-a-time loop.
    # Each individual issue then receives up to three alternatives generated
    # from the same immutable input to avoid candidate-on-candidate drift.
    source_blocking = blocking_issues(
        report,
        min_severity=_init_coherence_min_severity(ctx.settings),
    )
    if split_issues and len(source_blocking) > 1:
        return await _repair_init_artifact_payload_once(
            ctx,
            artifact=artifact,
            payload=payload,
            report=report,
            round_index=round_index,
            repairs=repairs,
            prune_strategy=prune_strategy,
            split_issues=split_issues,
        )

    store = RepairCaseStore(ctx.layout.root)
    cases = [store.load_case(case_id) for case_id in case_ids]
    if any(case is None for case in cases):
        raise InitCoherenceError("语义修复案例证据缺失；未采用未绑定候选。")
    if any(
        case.status in {"manual_required", "deferred", "rejected", "stale", "resolved"}
        for case in cases
        if case is not None
    ):
        raise InitCoherenceError("语义含义尚未由作者确认；不会生成或采用修复候选。")
    cached_verified: list[tuple[RepairCase, RepairCandidate]] = []
    for case in cases:
        if case is None or case.latest_candidate is None:
            continue
        if (
            case.verification is not None
            and case.verification.passed
            and case.latest_candidate.metadata.get("base_compiler_fingerprint")
            == str(report.get("compiler_fingerprint") or case.source_version)
        ):
            cached_verified.append((case, case.latest_candidate))
    if len(cached_verified) == len(cases):
        if any(case.authority == "proposal_required" for case, _ in cached_verified):
            raise InitCoherenceError(
                "已批准来源的语义修复候选已验证；必须先创建并批准 PlanningRevision 提案。"
            )
        payloads = [store.get_blob(candidate.blob_hash) for _, candidate in cached_verified]
        candidate_hashes = {candidate.candidate_hash for _, candidate in cached_verified}
        if (
            len(candidate_hashes) == 1
            and all(isinstance(item, dict) for item in payloads)
            and all(repair_content_hash(item) in candidate_hashes for item in payloads)
        ):
            repairs.append(
                {
                    "artifact": artifact,
                    "round": round_index,
                    "status": "verified_candidate_reused",
                    "repair_type": "semantic_candidate_first",
                    "repair_case_ids": case_ids,
                    "candidate_hash": next(iter(candidate_hashes)),
                }
            )
            return copy.deepcopy(payloads[0])

    baseline = copy.deepcopy(payload)
    seen_candidate_hashes: set[str] = set()
    attempt_records: list[dict[str, Any]] = []
    max_candidates = min(3, max(1, _init_coherence_max_repair_rounds(ctx.settings)))
    for attempt in range(1, max_candidates + 1):
        with TemporaryDirectory(prefix="novel-forge-semantic-candidate-") as temp_dir:
            candidate_storage = FileSystemStorage(Path(temp_dir))
            candidate_layout = ProjectLayout(
                candidate_storage.ensure_project_dir(ctx.layout.root.name)
            )
            candidate_layout.ensure_dirs()
            candidate_ctx = copy.copy(ctx)
            candidate_ctx.storage = candidate_storage
            candidate_ctx.layout = candidate_layout
            candidate_runtime: Any = candidate_ctx
            candidate_runtime._semantic_candidate_verification = True
            candidate_runtime._semantic_gate_inputs = {}
            candidate_payload = await _repair_init_artifact_payload_once(
                candidate_ctx,
                artifact=artifact,
                payload=copy.deepcopy(baseline),
                report=report,
                round_index=attempt,
                repairs=attempt_records,
                prune_strategy=prune_strategy,
                split_issues=False,
            )
            candidate_hash = repair_content_hash(candidate_payload)
            if candidate_hash == repair_content_hash(baseline):
                continue
            if candidate_hash in seen_candidate_hashes:
                break
            seen_candidate_hashes.add(candidate_hash)
            candidate_artifacts = copy.deepcopy(gate_input.get("artifacts") or {})
            candidate_artifacts[artifact] = candidate_payload
            verification_report = await run_init_coherence_v2_gate(
                candidate_ctx,
                stage=stage,
                repair_artifact=artifact,
                profile=copy.deepcopy(gate_input.get("profile") or {}),
                artifacts=candidate_artifacts,
                focus_chapters=None,
            )
        verified_cases = record_semantic_candidate_verification(
            ctx.layout.root,
            case_ids=case_ids,
            artifact=artifact,
            baseline=baseline,
            candidate_payload=candidate_payload,
            verification_report=verification_report,
            attempt=attempt,
            base_compiler_fingerprint=str(report.get("compiler_fingerprint") or ""),
        )
        passed = bool(verified_cases) and all(
            case.verification is not None and case.verification.passed for case in verified_cases
        )
        if not passed:
            continue
        if any(case.authority == "proposal_required" for case in verified_cases):
            raise InitCoherenceError(
                "已批准来源的语义修复候选已验证；必须先创建并批准 PlanningRevision 提案。"
            )
        repairs.append(
            {
                "artifact": artifact,
                "round": round_index,
                "status": "verified_candidate_adopted",
                "repair_type": "semantic_candidate_first",
                "repair_case_ids": case_ids,
                "candidate_hash": candidate_hash,
                "verification_report_hash": repair_content_hash(verification_report),
            }
        )
        return candidate_payload
    raise InitCoherenceError("语义修复候选均未通过完整 claims 抽取与模型裁决；保留原始来源。")


async def _repair_init_artifact_payload_once(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    payload: dict[str, Any],
    report: dict[str, Any],
    round_index: int,
    repairs: list[dict[str, Any]],
    prune_strategy: Literal["none", "outline", "chapter_contracts", "blueprint"] = "none",
    split_issues: bool = True,
) -> dict[str, Any]:
    min_severity = _init_coherence_min_severity(ctx.settings)
    if not has_repair_scope(report, artifact=artifact, min_severity=min_severity):
        raise InitCoherenceError(
            f"初始化裁判发现阻断问题，但没有可定位 repair_scope：{report.get('summary')}"
        )
    source_blocking_issues = blocking_issues(report, min_severity=min_severity)
    source_issue_ids = [
        init_coherence_issue_id(issue)
        for issue in source_blocking_issues
        if isinstance(issue, dict)
    ]
    if (
        split_issues
        and len(source_blocking_issues) > 1
        and bool(getattr(ctx.settings, "init_coherence_point_repair_enabled", True))
    ):
        repaired_payload = payload
        ctx.on_step(
            "repair_init_artifact_point_batch",
            {
                "artifact": artifact,
                "round": round_index,
                "issue_count": len(source_blocking_issues),
                "issue_ids": source_issue_ids,
            },
        )
        for issue_index, issue in enumerate(source_blocking_issues, 1):
            if not isinstance(issue, dict):
                continue
            issue_id = init_coherence_issue_id(issue)
            ctx.on_step(
                "repair_init_artifact_issue_focus",
                {
                    "artifact": artifact,
                    "round": round_index,
                    "issue_index": issue_index,
                    "issue_count": len(source_blocking_issues),
                    "issue_id": issue_id,
                    "issue_type": str(issue.get("issue_type") or issue.get("type") or ""),
                },
            )
            repaired_payload = await _repair_init_artifact_payload(
                ctx,
                artifact=artifact,
                payload=repaired_payload,
                report=_single_issue_init_repair_report(report, issue),
                round_index=round_index,
                repairs=repairs,
                prune_strategy=prune_strategy,
                split_issues=False,
            )
        return repaired_payload
    issue_evidence_by_id = _init_repair_issue_evidence_map(source_blocking_issues)
    # Scope and prompt must be derived from the same severity-filtered report.
    # Previously a non-blocking issue could widen ``scopes`` while being absent
    # from the prompt report.  The first target batch could then contain only
    # unrelated chapters and defer the actual blocking target.
    repair_report = _compact_init_repair_report(report, min_severity=min_severity)
    scopes = collect_repair_scopes(repair_report, default_artifact=artifact)
    max_ops_val = int(getattr(ctx.settings, "init_coherence_patch_max_ops", 40) or 40)
    target_batch_size = _init_repair_target_batch_size(ctx.settings, max_ops_val)
    backup_init_artifact(
        storage=ctx.storage,
        layout=ctx.layout,
        artifact=artifact,
        payload=payload,
        round_index=round_index,
    )

    # ── Prune: reduce payload size before LLM repair (T19) ──
    pruned_payload, pruned_fields = _apply_prune_strategy(payload, prune_strategy)
    if pruned_fields:
        _log.info(
            "init_repair_pruned_fields | artifact=%s | strategy=%s | pruned=%s",
            artifact,
            prune_strategy,
            pruned_fields,
        )
        ctx.on_step(
            "repair_init_artifact_pruned",
            {
                "artifact": artifact,
                "round": round_index,
                "strategy": prune_strategy,
                "pruned_fields": pruned_fields,
            },
        )

    # ── Pre-flight: estimate prompt tokens and select repair path ──
    # Priority: 完整 > 分块 > 降级 (T17 pre-flight estimation)
    repair_scope_payload = [
        {
            "artifact": scope.artifact,
            "chapters": sorted(scope.chapters),
            "fields": sorted(scope.fields),
            "operation": scope.operation,
            "issue_ids": sorted(scope.issue_ids),
        }
        for scope in scopes
    ]
    preserve_payload = _init_coherence_list(repair_report.get("preserve"))
    change_intent = str(repair_report.get("change_intent") or "")
    prompt_payload = pruned_payload
    patch_target_payload = pruned_payload
    repair_type = "patch"
    repair_targets = resolve_init_repair_targets(
        artifact,
        payload,
        repair_report,
        scopes,
    )
    repair_targets_for_call = repair_targets[:target_batch_size]
    repair_targets_payload = _init_target_prompt_payload(repair_targets_for_call)
    repair_attempt_guidance = _build_init_target_repair_guidance(
        ctx.settings,
        artifact=artifact,
        round_index=round_index,
        repairs=repairs,
        source_issue_ids=source_issue_ids,
        targets=repair_targets_for_call,
    )
    if repair_targets_payload:
        prompt_payload = {}
        patch_target_payload = payload
        repair_type = "target_patch"
        ctx.on_step(
            "repair_init_artifact_targets",
            {
                "artifact": artifact,
                "round": round_index,
                "target_count": len(repair_targets),
                "batch_target_count": len(repair_targets_payload),
                "deferred_target_count": max(0, len(repair_targets) - len(repair_targets_payload)),
                "targets": repair_targets_payload[:12],
            },
        )
    estimated_tokens = _estimate_init_repair_prompt_tokens(
        artifact=artifact,
        artifact_payload=prompt_payload,
        coherence_report=repair_report,
        repair_scope=repair_scope_payload,
        preserve=preserve_payload,
        change_intent=change_intent,
        max_ops=max_ops_val,
        repair_targets=repair_targets_payload,
        repair_attempt_guidance=repair_attempt_guidance,
    )

    if estimated_tokens > 10000 and repair_type != "target_patch":
        scoped_payload = _build_scoped_init_repair_payload(
            payload,
            artifact=artifact,
            scopes=scopes,
        )
        if scoped_payload is not None:
            scoped_estimated_tokens = _estimate_init_repair_prompt_tokens(
                artifact=artifact,
                artifact_payload=scoped_payload,
                coherence_report=repair_report,
                repair_scope=repair_scope_payload,
                preserve=preserve_payload,
                change_intent=change_intent,
                max_ops=max_ops_val,
                repair_targets=[],
                repair_attempt_guidance=repair_attempt_guidance,
            )
            if scoped_estimated_tokens < estimated_tokens:
                prompt_payload = scoped_payload
                patch_target_payload = payload
                repair_type = "scoped_patch"
                estimated_tokens = scoped_estimated_tokens
                ctx.on_step(
                    "repair_init_artifact_scoped",
                    {
                        "artifact": artifact,
                        "round": round_index,
                        "estimated_tokens": estimated_tokens,
                        "source": "repair_scope",
                    },
                )

    if estimated_tokens > 50000:
        _log.warning(
            "init_repair_payload_oversized_precise_repair_required | artifact=%s | estimated_tokens=%d",
            artifact,
            estimated_tokens,
        )
        ctx.on_step(
            "repair_init_artifact_precise_required",
            {
                "artifact": artifact,
                "round": round_index,
                "estimated_tokens": estimated_tokens,
                "reason": "oversized_prompt_without_precise_targets",
                "fallback": "keep_original_payload",
            },
        )
        skipped_patches = [
            {
                "reason": (
                    "repair prompt remained oversized and no precise target/scoped patch path "
                    "was available; broad summary fallback is disabled"
                ),
                "repair_type": repair_type,
                "estimated_tokens": estimated_tokens,
            }
        ]
        _record_init_artifact_repair_no_effect(
            ctx,
            artifact=artifact,
            round_index=round_index,
            repairs=repairs,
            source_issue_ids=source_issue_ids,
            source_blocking_issue_count=len(source_blocking_issues),
            skipped_patches=skipped_patches,
            summary="precise repair required; broad summary fallback disabled",
            repair_type="precise_required",
            target_count=len(repair_targets_payload),
            located_count=len(repair_targets_payload),
        )
        return payload

    if estimated_tokens > 10000 and repair_type not in {"scoped_patch", "target_patch"}:
        _log.info(
            "init_repair_chunked_disabled_precise_repair_required | artifact=%s | estimated_tokens=%d",
            artifact,
            estimated_tokens,
        )
        ctx.on_step(
            "repair_init_artifact_precise_required",
            {
                "artifact": artifact,
                "round": round_index,
                "estimated_tokens": estimated_tokens,
                "reason": "chunked_fallback_without_precise_targets",
                "fallback": "keep_original_payload",
            },
        )
        skipped_patches = [
            {
                "reason": (
                    "chunked repair would bypass scoped patch validation; precise target/scoped "
                    "patch is required"
                ),
                "repair_type": repair_type,
                "estimated_tokens": estimated_tokens,
            }
        ]
        _record_init_artifact_repair_no_effect(
            ctx,
            artifact=artifact,
            round_index=round_index,
            repairs=repairs,
            source_issue_ids=source_issue_ids,
            source_blocking_issue_count=len(source_blocking_issues),
            skipped_patches=skipped_patches,
            summary="precise repair required; chunked fallback disabled",
            repair_type="precise_required",
            target_count=len(repair_targets_payload),
            located_count=len(repair_targets_payload),
        )
        return payload

    try:
        patch_payload = await ctx.call_with_retry(
            TaskType.REPAIR_INIT_ARTIFACT_PATCH,
            {
                "artifact": artifact,
                "artifact_payload": prompt_payload,
                "coherence_report": repair_report,
                "repair_scope": repair_scope_payload,
                "preserve": preserve_payload,
                "change_intent": change_intent,
                "max_ops": max_ops_val,
                "repair_targets": repair_targets_payload,
                "repair_attempt_guidance": repair_attempt_guidance,
            },
            max_tokens=calculate_route_aware_max_tokens(
                ctx.router,
                TaskType.REPAIR_INIT_ARTIFACT_PATCH,
                4096,
                prompt_overhead=6000,
                min_tokens=2048,
            ),
            temperature=getattr(ctx.settings, "temp_repair_init_artifact_patch", 0.15),
            required_keys=("patches", "summary"),
            max_retries=3,
        )
        skipped_patches: list[dict[str, Any]] = []
        patches = patch_payload.get("patches")
        patches_list = patches if isinstance(patches, list) else [patches]
        attempted_patch_preview = _init_repair_attempted_patch_preview(patches_list)
        uses_target_ids = any(
            isinstance(patch, dict) and patch.get("target_id") for patch in patches_list
        )
        applied_repair_type = repair_type
        if repair_targets_payload and uses_target_ids:
            repaired_payload, applied = apply_targeted_init_patch(
                artifact=artifact,
                payload=patch_target_payload,
                patch_payload=patch_payload,
                target_map={target.target_id: target for target in repair_targets_for_call},
                scopes=scopes,
                max_ops=int(getattr(ctx.settings, "init_coherence_patch_max_ops", 40) or 40),
                strict=False,
                skipped=skipped_patches,
            )
        else:
            if repair_targets_payload:
                applied_repair_type = "legacy_path_patch"
            repaired_payload, applied = apply_scoped_init_patch(
                artifact=artifact,
                payload=patch_target_payload,
                patch_payload=patch_payload,
                scopes=scopes,
                max_ops=int(getattr(ctx.settings, "init_coherence_patch_max_ops", 40) or 40),
                strict=False,
                skipped=skipped_patches,
                issue_evidence_by_id=issue_evidence_by_id,
            )
        if not applied:
            skipped_summary = "; ".join(
                str(item.get("reason") or "")
                for item in skipped_patches[:3]
                if isinstance(item, dict)
            )
            if artifact == OUTLINE_ARTIFACT and repair_targets:
                fallback_payload, fallback_applied, fallback_skipped = (
                    _try_outline_irreversible_target_fallback(
                        payload=patch_target_payload,
                        report=repair_report,
                        targets=repair_targets_for_call,
                        scopes=scopes,
                        max_ops=max_ops_val,
                    )
                )
                if fallback_applied:
                    repair_record = {
                        "artifact": artifact,
                        "round": round_index,
                        "status": "applied",
                        "repair_type": "deterministic_outline_irreversible",
                        "source_issue_ids": sorted(set(source_issue_ids)),
                        "source_blocking_issue_count": len(source_blocking_issues),
                        "patch_count": len(fallback_applied),
                        "skipped_patch_count": len(fallback_skipped),
                        "patches": fallback_applied,
                        "skipped_patches": fallback_skipped,
                        "attempted_patches": attempted_patch_preview,
                        "target_count": len(repair_targets),
                        "located_count": len(repair_targets_payload),
                        "deferred_target_count": max(
                            0, len(repair_targets) - len(repair_targets_payload)
                        ),
                        "applied_target_count": len(fallback_applied),
                        "skipped_target_count": len(fallback_skipped),
                        "summary": "本地兜底修复 outline 不可逆事件重复声明。",
                    }
                    repairs.append(repair_record)
                    ctx.storage.save_json(
                        ctx.layout.reports_dir / "init_artifact_repair.json",
                        {"repairs": repairs},
                    )
                    _record_init_repair_manifest_round(ctx, repair_record)
                    ctx.on_step("repair_init_artifact_patch", repair_record)
                    return fallback_payload
            _log.warning(
                "init_repair_no_effective_patch | artifact=%s | round=%d | skipped=%d | reasons=%s",
                artifact,
                round_index,
                len(skipped_patches),
                skipped_summary,
            )
            ctx.on_step(
                "repair_init_artifact_patch_degraded",
                {
                    "artifact": artifact,
                    "round": round_index,
                    "skipped_patch_count": len(skipped_patches),
                    "skipped_patches": skipped_patches,
                    "reason": skipped_summary,
                    "fallback": "keep_original_payload",
                },
            )
            _record_init_artifact_repair_no_effect(
                ctx,
                artifact=artifact,
                round_index=round_index,
                repairs=repairs,
                source_issue_ids=source_issue_ids,
                source_blocking_issue_count=len(source_blocking_issues),
                skipped_patches=skipped_patches,
                summary=skipped_summary or "no effective scoped patch",
                repair_type=applied_repair_type,
                target_count=len(repair_targets),
                located_count=len(repair_targets_payload),
                attempted_patches=attempted_patch_preview,
            )
            if patch_target_payload is payload:
                return payload
            return _merge_pruned_fields(payload, pruned_payload, pruned_fields)
    except TaskCircuitOpenError as exc:
        _record_init_artifact_repair_skipped(
            ctx,
            artifact=artifact,
            round_index=round_index,
            repairs=repairs,
            source_issue_ids=source_issue_ids,
            source_blocking_issue_count=len(source_blocking_issues),
            exc=exc,
        )
        if patch_target_payload is payload:
            return payload
        return _merge_pruned_fields(payload, pruned_payload, pruned_fields)
    except Exception as exc:
        _record_init_artifact_repair_failure(
            ctx,
            artifact=artifact,
            round_index=round_index,
            repairs=repairs,
            source_issue_ids=source_issue_ids,
            source_blocking_issue_count=len(source_blocking_issues),
            exc=exc,
        )
        raise
    repair_record = {
        "artifact": artifact,
        "round": round_index,
        "status": "applied",
        "repair_type": applied_repair_type,
        "source_issue_ids": sorted(set(source_issue_ids)),
        "source_blocking_issue_count": len(source_blocking_issues),
        "patch_count": len(applied),
        "skipped_patch_count": len(skipped_patches),
        "patches": applied,
        "skipped_patches": skipped_patches,
        "attempted_patches": attempted_patch_preview,
        "target_count": len(repair_targets),
        "located_count": len(repair_targets_payload),
        "deferred_target_count": max(0, len(repair_targets) - len(repair_targets_payload)),
        "applied_target_count": sum(1 for patch in applied if patch.get("target_id")),
        "skipped_target_count": sum(1 for patch in skipped_patches if patch.get("target_id")),
        "summary": patch_payload.get("summary", ""),
    }
    repairs.append(repair_record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    _record_init_repair_manifest_round(ctx, repair_record)
    ctx.on_step("repair_init_artifact_patch", repair_record)
    if patch_target_payload is payload:
        return repaired_payload
    return _merge_pruned_fields(payload, repaired_payload, pruned_fields)


def _extract_top_level_issues(payload: dict[str, Any]) -> list[str]:
    """Extract top-level structural anomalies from a payload for degraded summary.

    Inspects the payload and returns human-readable issue descriptions
    that hint where repairs may be needed, without carrying full content.
    """
    issues: list[str] = []
    if not isinstance(payload, dict):
        return issues
    for key, value in payload.items():
        if value is None:
            issues.append(f"Field '{key}' is null")
        elif isinstance(value, list) and len(value) == 0:
            issues.append(f"Field '{key}' is an empty list")
        elif isinstance(value, dict) and len(value) == 0:
            issues.append(f"Field '{key}' is an empty dict")
    if not issues:
        issues.append("No structural anomalies detected from summary")
    return issues
