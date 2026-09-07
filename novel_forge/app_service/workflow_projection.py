"""Qt-free workflow projection shared by API and desktop clients.

The pipeline event stream is the source of truth.  This module turns that
stream, durable review checkpoints, and the signed artifact manifest into one
read model.  UI clients must not infer completion or fabricate checkpoints.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from novel_forge.app_service.init_manual_repair import (
    ManualInitRepairError,
    load_manual_init_repair,
)
from novel_forge.persistence.filesystem import FileSystemStorage, resolve_project_path
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import MANIFEST_FILENAME, ArtifactRecord
from novel_forge.pipeline.progress import (
    StepVisualSpec,
    compute_progress_percent,
    display_step_name,
    format_step_label,
    is_non_progress_step_event,
    resolve_step_key,
    summary_steps,
)

WORKFLOW_PROJECTION_VERSION = "novel-production.v2"

RunInsightStatus = Literal[
    "inactive",
    "pending",
    "running",
    "success",
    "warning",
    "blocked",
    "skipped",
    "rolled_back",
]

WorkflowStageState = Literal[
    "completed",
    "active",
    "pending",
    "failed",
    "skipped",
    "blocked",
    "rolled_back",
]

_ACTIVE_STATES = frozenset({"queued", "running", "paused"})
_FAILED_STATES = frozenset({"failed", "cancelled", "timeout"})
_CHAPTER_KINDS = frozenset(
    {
        "run_chapter",
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "polish_chapter",
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
    }
)

# Diagnostic/parallel init tasks remain visible in the task observation log,
# but are intentionally folded into their owning semantic milestone here.
_HIDDEN_STEP_KEYS: dict[str, frozenset[str]] = {
    "init_long": frozenset(
        {
            "init_character_system",
            "init_entity_registry",
            "init_entity_graph",
            "creative_director_packet",
            "derive_init_coherence_profile",
            "refine_init_coherence_profile",
            "build_init_coherence_profile",
            "extract_init_coherence_claims",
            "retrieve_init_conflict_candidates",
            "adjudicate_init_conflict_candidates",
            "adjudicate_blueprint_coherence",
            "repair_init_artifact_patch",
            "plan_blueprint_fragments",
            "plan_blueprint_validated",
            "plan_blueprint_repaired",
            "adjudicate_outline_inheritance",
            "adjudicate_contract_coherence",
            "init_source_artifacts",
            "init_source_artifacts_resume",
            "init_source_artifacts_repair",
            "init_source_artifacts_repaired",
            "init_source_artifacts_repair_gate",
            "source_artifacts_repair_targeted",
            "init_repair_reaudit_started",
            "init_repair_reaudit_stage",
            "init_repair_reaudit_passed",
            "init_repair_reaudit_failed",
            "init_knowledge_boundaries",
        }
    )
}

_INIT_COHERENCE_STAGE_STEP = {
    "blueprint_coherence": "plan_blueprint",
    "outline_inheritance": "plan_outline",
    "contract_coherence": "plan_chapter_contracts",
}
_INIT_COHERENCE_ARTIFACT_STEP = {
    "blueprint": "plan_blueprint",
    "outline": "plan_outline",
    "chapter_contracts": "plan_chapter_contracts",
}
_INIT_HIDDEN_STEP_ANCHOR = {
    "creative_director_packet": "plan_blueprint",
    "init_knowledge_boundaries": "profile_style",
}
_INIT_COHERENCE_PREFIXES = (
    "init_coherence_recheck_",
    "init_claim_entity_adjudication_",
    "init_coherence_report_resumed",
    "extract_init_coherence_claims",
    "retrieve_init_conflict_candidates",
    "adjudicate_init_conflict_candidates",
    "adjudicate_blueprint_coherence",
    "adjudicate_outline_inheritance",
    "adjudicate_contract_coherence",
    "repair_init_artifact_patch",
    "repair_init_artifact_targets",
    "init_repair_reaudit",
)
_RESUME_STAGE_STEP = {
    "draft_done": "alignment",
    "quality_done": "causal_repair",
    "causal_repair_done": "reading_power_repair",
    "repair_done": "polish",
    "canon_done": "persist",
    "refinement_done": "persist",
    "final_verify_done": "persist",
}
_HIGH_WATER_RESET_STEPS = frozenset(
    {
        "init_resume_rollback",
        "short_resume_rollback",
        "consistency_replan",
        "regenerate_plan",
        "regenerate_plan_with_notes",
        "edit_plan_and_write",
        "adjust_outline_and_finalize",
    }
)
_HIGH_WATER_RESET_ACTIONS = frozenset({"rollback", "reset", "replan", "invalidate"})
_OPTIONAL_STAGE_OUTCOMES = {
    "profile_style": ("profile_style", "completed"),
    "profile_style_resumed": ("profile_style", "completed"),
    "profile_style_failed": ("profile_style", "failed"),
    "profile_style_skipped": ("profile_style", "skipped"),
    "chapter_research_cache_hit": ("chapter_research", "completed"),
    "chapter_research_ready": ("chapter_research", "completed"),
    "chapter_research_skipped": ("chapter_research", "skipped"),
    "init_web_research_skipped": ("init_web_research", "skipped"),
    "init_web_research_failed": ("init_web_research", "failed"),
    "polish_auto_trigger_skipped": ("polish", "skipped"),
    "humanize_skipped": ("humanize", "skipped"),
    "humanize_rolled_back": ("humanize", "rolled_back"),
    "causal_repair_skipped_high_score": ("causal_repair", "skipped"),
    "reading_power_repair_skipped": ("reading_power_repair", "skipped"),
    "short_adaptive_revision_rollback": ("edit_", "rolled_back"),
}
_OPTIONAL_CHAPTER_STAGES = frozenset(
    {
        "chapter_research",
        "opening_guard",
        "continuity_repair",
        "alignment_repair",
        "causal_repair",
        "reading_power_repair",
        "polish",
        "humanize",
    }
)
_CHAPTER_CHECKPOINT_STAGE = {
    "state_packet": "plan_checkpoint",
    "chapter_research": "plan_checkpoint",
    "bridge": "plan_checkpoint",
    "plan": "plan_checkpoint",
    "draft": "draft",
    "wave": "draft",
    "opening_guard": "pre_alignment",
    "alignment": "pre_alignment",
    "continuity_repair": "continuity_repair",
    "alignment_repair": "alignment_repair",
    "guard_review": "post_alignment",
    "causal_repair": "post_alignment",
    "reading_power_repair": "post_alignment",
    "polish": "post_alignment",
    "humanize": "post_alignment",
    "extract_canon": "post_alignment",
    "persist": "guard_checkpoint",
    "memory_updated": "guard_checkpoint",
}


def _is_progress_observation(step: str, payload: dict[str, Any]) -> bool:
    """Health/telemetry describe prior work; they do not move the execution cursor."""
    if is_non_progress_step_event(step) or step in {"failed", "cancelled"}:
        return True
    if step == "init_upstream_health":
        return str(payload.get("status") or "") not in {"failed", "blocked"}
    return step in {"init_narrative_evidence_sync", "init_entity_graph_sensory_rules_synced"}


@dataclass(frozen=True)
class WorkflowCheckpointProjection:
    exists: bool = False
    completed_stage: str = ""
    source_text_hash: str = ""
    input_signature: str = "legacy_unknown"
    path: str = ""


@dataclass(frozen=True)
class WorkflowLineageProjection:
    workflow_version: str = "legacy_unknown"
    input_signature: str = "legacy_unknown"
    output_version: int = 0
    quality_status: str = "legacy_unknown"
    derivation_status: str = "legacy_unknown"
    degradation_reason: str = ""
    parent_artifact_versions: tuple[tuple[str, int], ...] = ()
    template_version: str = "legacy_unknown"
    config_fingerprint: str = "legacy_unknown"
    model_fingerprint: str = "legacy_unknown"


@dataclass
class WorkflowProjectionContext:
    """Per-response cache for small durable projection files.

    The workflow page polls while work is active and often contains many runs
    from the same project.  Reading the same manifest once per card would turn
    a cheap refresh into avoidable filesystem churn.
    """

    storage_root: Path | None = None
    _json_cache: dict[Path, dict[str, Any]] = field(default_factory=dict)

    def read_json(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve(strict=False)
        cached = self._json_cache.get(resolved)
        if cached is not None:
            return cached
        payload = _read_json_uncached(resolved)
        self._json_cache[resolved] = payload
        return payload


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _events(record: Any) -> list[Any]:
    value = getattr(record, "events", [])
    return list(value) if isinstance(value, (list, tuple)) else []


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _count_values(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    return sum(max(0, int(_number(item))) for item in value.values())


def _event_step(event: Any) -> str:
    return str(getattr(event, "step", "") or "").strip()


def _event_payload(event: Any) -> dict[str, Any]:
    return _mapping(getattr(event, "payload", {}))


def _kind(record: Any) -> str:
    value = _enum_value(getattr(record, "kind", ""))
    return "init_long" if value == "init_repair_retry" else value


def _latest_event(record: Any, steps: frozenset[str]) -> tuple[str, dict[str, Any]]:
    for event in reversed(_events(record)):
        step = _event_step(event)
        if step in steps:
            return step, _event_payload(event)
    return "", {}


def _performance_metrics(record: Any) -> dict[str, Any]:
    step, payload = _latest_event(record, frozenset({"performance_metrics"}))
    if step:
        return payload
    result = _mapping(getattr(record, "result", {}))
    return _mapping(result.get("performance_metrics"))


def _insight(
    insight_id: str,
    *,
    status: RunInsightStatus,
    label: str,
    summary: str,
    detail: str = "",
    count: int = 0,
    artifact_step_key: str = "",
) -> dict[str, Any]:
    return {
        "id": insight_id,
        "status": status,
        "label": label,
        "summary": summary,
        "detail": detail,
        "count": max(0, count),
        "artifact_step_key": artifact_step_key,
    }


def project_run_transparency(record: Any) -> dict[str, Any]:
    """Project UI-safe chapter governance and efficiency facts from events.

    The projector deliberately exposes bounded counters and explanations only.
    Raw prompts, evidence excerpts, private paths, and full text hashes remain
    behind the Engine boundary.
    """

    kind = _kind(record)
    chapter_kinds = _CHAPTER_KINDS | frozenset({"run_short"})
    metrics = _performance_metrics(record)
    tokens = _mapping(metrics.get("tokens"))
    calls = _mapping(metrics.get("llm_calls"))
    research = _mapping(metrics.get("research"))
    semantic_mutations = _mapping(metrics.get("semantic_mutations"))
    report_refreshes = _mapping(metrics.get("report_refreshes"))
    intent_conflicts = _mapping(metrics.get("intent_conflicts"))
    rollback_steps = {
        "short_adaptive_revision_rollback",
        "repair_rollback",
        "quality_gate_rollback_applied",
        "causal_repair_rollback",
        "continuity_repair_rollback",
        "reading_power_repair_rollback",
        "word_count_polish_continuity_rollback",
        "alignment_repair_candidate_rolled_back",
    }
    efficiency = {
        "llm_calls": max(
            int(_number(calls.get("started"))),
            int(_number(calls.get("succeeded"))) + int(_number(calls.get("failed"))),
        ),
        "prompt_tokens": max(0, int(_number(tokens.get("prompt")))),
        "completion_tokens": max(0, int(_number(tokens.get("completion")))),
        "total_tokens": max(
            0,
            int(_number(tokens.get("total")) or int(getattr(record, "cumulative_tokens", 0) or 0)),
        ),
        "cost_usd": max(
            0.0,
            _number(metrics.get("cost_usd"))
            or float(getattr(record, "cumulative_cost_usd", 0.0) or 0.0),
        ),
        "research_queries": max(0, int(_number(research.get("queries")))),
        "research_cache_hits": max(0, int(_number(research.get("cache_hits")))),
        "semantic_mutations": _count_values(semantic_mutations),
        "report_refreshes": _count_values(report_refreshes),
        "repair_rounds": max(0, int(_number(metrics.get("repair_rounds")))),
        "short_revision_rounds": max(0, int(_number(metrics.get("short_revision_rounds")))),
        "rollbacks": sum(1 for event in _events(record) if _event_step(event) in rollback_steps),
        "final_hash_verifications": max(0, int(_number(metrics.get("final_hash_verifications")))),
    }
    if kind not in chapter_kinds:
        return {"run_insights": [], "efficiency": efficiency}

    state = _status(record)
    active = state in _ACTIVE_STATES
    succeeded = state == "succeeded"

    conflict_count = _count_values(intent_conflicts)
    if not conflict_count:
        for event in _events(record):
            conflicts = _event_payload(event).get("intent_conflicts")
            if isinstance(conflicts, list):
                conflict_count += len(conflicts)
    if conflict_count:
        intent = _insight(
            "intent",
            status="blocked" if state == "failed" else "warning",
            label="意图保护",
            summary=f"检测到 {conflict_count} 项主观意图冲突",
            detail="自动修复不会改写用户设定；冲突文本将被拒绝或回滚。",
            count=conflict_count,
        )
    elif succeeded:
        intent = _insight(
            "intent",
            status="success",
            label="意图保护",
            summary="用户意图约束已通过",
            detail="人物、关系、POV、结局与锁定要素未被静默覆盖。",
        )
    else:
        intent = _insight(
            "intent",
            status="pending" if active else "inactive",
            label="意图保护",
            summary="运行中持续检查" if active else "本次尚无意图检查结果",
        )

    research_step, research_payload = _latest_event(
        record,
        frozenset(
            {
                "chapter_research_start",
                "chapter_research_cache_hit",
                "chapter_research_ready",
                "chapter_research_skipped",
            }
        ),
    )
    research_count = int(_number(research_payload.get("queries")))
    if research_step == "chapter_research_ready":
        cards = int(_number(research_payload.get("cards")))
        inspiration = int(_number(research_payload.get("inspiration_cards")))
        research_insight = _insight(
            "research",
            status="success",
            label="研究与证据",
            summary=f"已准备 {cards} 张章节证据卡",
            detail=f"查询 {research_count} 次，其中抽象灵感 {inspiration} 张。",
            count=cards,
            artifact_step_key="chapter_research",
        )
    elif research_step == "chapter_research_cache_hit":
        research_insight = _insight(
            "research",
            status="success",
            label="研究与证据",
            summary="已复用章节证据缓存",
            detail=f"避免重复执行 {research_count} 个查询。",
            count=research_count,
            artifact_step_key="chapter_research",
        )
    elif research_step == "chapter_research_start":
        research_insight = _insight(
            "research",
            status="running",
            label="研究与证据",
            summary=f"正在准备 {research_count} 个有界查询",
            count=research_count,
        )
    elif research_step == "chapter_research_skipped":
        reason = str(research_payload.get("reason_label") or "本章无需新增外部资料")
        research_insight = _insight(
            "research",
            status="skipped",
            label="研究与证据",
            summary=reason,
            detail="未发起模型或 MCP 调用。",
        )
    else:
        research_insight = _insight(
            "research",
            status="inactive",
            label="研究与证据",
            summary="本次运行未触发章节研究",
        )

    rollback_step, rollback_payload = _latest_event(
        record,
        frozenset(
            {
                "short_adaptive_revision_rollback",
                "repair_rollback",
                "quality_gate_regression_detected",
                "cross_dimension_regression",
            }
        ),
    )
    revision_rounds = max(
        int(efficiency["short_revision_rounds"]),
        int(efficiency["repair_rounds"]),
        int(efficiency["semantic_mutations"]),
    )
    if rollback_step:
        revision = _insight(
            "revision",
            status="rolled_back",
            label="修订与回滚",
            summary="修订出现回归，已保留上一个安全版本",
            detail=str(rollback_payload.get("reason") or "未验证文本不会成为最终稿。"),
            count=max(1, revision_rounds),
        )
    elif revision_rounds:
        revision = _insight(
            "revision",
            status="success" if succeeded else "running" if active else "warning",
            label="修订与回滚",
            summary=f"已执行 {revision_rounds} 轮定向修订或修复",
            detail="所有语义修改均受全局预算和回归检查约束。",
            count=revision_rounds,
        )
    else:
        revision = _insight(
            "revision",
            status="skipped" if succeeded else "pending" if active else "inactive",
            label="修订与回滚",
            summary="质量门禁已通过，无需改文" if succeeded else "尚未进入修订阶段",
        )

    final_step, final_payload = _latest_event(record, frozenset({"final_text_hash_verified"}))
    if final_step:
        final_verify = _insight(
            "final_verify",
            status="success",
            label="最终验证",
            summary="终稿文本指纹已验证",
            detail=(
                "验证后禁止继续发生语义修改。"
                if final_payload.get("semantic_mutation_allowed_after") is False
                else "最终验证已完成。"
            ),
            count=1,
        )
    elif kind == "run_short" and succeeded:
        final_verify = _insight(
            "final_verify",
            status="success",
            label="最终验证",
            summary="短篇终检与创意分析已完成",
            count=1,
        )
    elif state == "failed":
        final_verify = _insight(
            "final_verify",
            status="blocked",
            label="最终验证",
            summary="任务在最终验证前被阻断",
        )
    else:
        final_verify = _insight(
            "final_verify",
            status="pending" if active else "inactive",
            label="最终验证",
            summary="等待最终文本" if active else "尚无最终验证结果",
        )

    return {
        "run_insights": [intent, research_insight, revision, final_verify],
        "efficiency": efficiency,
    }


def _status(record: Any) -> str:
    return _enum_value(getattr(record, "status", "")) or "failed"


def _latest_semantic_step(record: Any) -> str:
    current = str(getattr(record, "current_step", "") or "").strip()
    if current and not _is_progress_observation(current, _latest_payload(record, current)):
        return current
    for event in reversed(_events(record)):
        step = _event_step(event)
        if step and not _is_progress_observation(step, _event_payload(event)):
            return step
    return ""


def _latest_payload(record: Any, step: str) -> dict[str, Any]:
    if step == str(getattr(record, "current_step", "") or ""):
        payload = _mapping(getattr(record, "current_step_payload", {}))
        if payload:
            return payload
    for event in reversed(_events(record)):
        if _event_step(event) == step:
            return _event_payload(event)
    return {}


def visible_workflow_steps(kind: str) -> list[StepVisualSpec]:
    """Return the engine-owned visible milestone sequence for one job kind."""

    hidden = _HIDDEN_STEP_KEYS.get(str(kind), frozenset())
    return [step for step in summary_steps(str(kind)) if step.key not in hidden]


def _step_index(steps: list[StepVisualSpec], key: str) -> int | None:
    for index, step in enumerate(steps):
        if key == step.key:
            return index
    matches = [
        (len(step.key), index)
        for index, step in enumerate(steps)
        if step.is_prefix and key.startswith(step.key)
    ]
    return max(matches)[1] if matches else None


def _nearest_visible_key(
    kind: str,
    resolved: str,
    steps: list[StepVisualSpec],
) -> str:
    keys = {step.key for step in steps}
    if resolved in keys:
        return resolved
    if kind == "init_long":
        anchor = _INIT_HIDDEN_STEP_ANCHOR.get(resolved, "")
        if anchor in keys:
            return anchor
    all_steps = summary_steps(kind)
    matched = _step_index(all_steps, resolved)
    if matched is None:
        return resolved
    for step in reversed(all_steps[:matched]):
        if step.key in keys:
            return step.key
    for step in all_steps[matched + 1 :]:
        if step.key in keys:
            return step.key
    return resolved


def _resolve_visible_key(
    kind: str,
    raw_step: str,
    payload: dict[str, Any],
    steps: list[StepVisualSpec],
) -> str:
    if kind in _CHAPTER_KINDS and raw_step == "input_integrity_check":
        raw_step = {
            "extract_input": "extract_canon_start",
            "draft_input": "draft",
            "wave_input": "wave",
            "plan_input": "plan",
        }.get(str(payload.get("stage") or ""), raw_step)
    if kind in _CHAPTER_KINDS and raw_step == "resume_from_progress":
        resume = _RESUME_STAGE_STEP.get(str(payload.get("completed_stage") or ""), "")
        if resume:
            if kind == "resolve_chapter_checkpoint":
                resume = {
                    "alignment": "pre_alignment",
                    "causal_repair": "post_alignment",
                    "reading_power_repair": "post_alignment",
                    "polish": "post_alignment",
                    "persist": "guard_checkpoint",
                }[resume]
            return _nearest_visible_key(kind, resolve_step_key(kind, resume), steps)
    if kind == "init_long":
        if raw_step.startswith("init_knowledge_boundaries"):
            return "profile_style"
        if raw_step == "init_upstream_health":
            artifact = str(payload.get("artifact") or "")
            return {
                "story_bible": "init_story_bible",
                "character_bible": "init_character_bible",
                "character_system": "init_character_bible",
                "entity_graph": "profile_style",
            }.get(artifact, "")
        if raw_step == "init_resume_anchor":
            anchor = str(payload.get("step") or payload.get("anchor_step") or "").strip()
            if anchor:
                return _nearest_visible_key(kind, resolve_step_key(kind, anchor), steps)
        if raw_step.startswith(_INIT_COHERENCE_PREFIXES):
            staged = _INIT_COHERENCE_STAGE_STEP.get(str(payload.get("stage") or ""), "")
            staged = staged or _INIT_COHERENCE_ARTIFACT_STEP.get(
                str(payload.get("artifact") or ""), ""
            )
            if staged:
                return staged
    resolved = resolve_step_key(kind, raw_step)
    if kind == "resolve_chapter_checkpoint" and _step_index(steps, resolved) is None:
        resolved = _CHAPTER_CHECKPOINT_STAGE.get(
            resolve_step_key("run_chapter", raw_step), resolved
        )
    return _nearest_visible_key(kind, resolved, steps)


def _checkpoint_decision_step(record: Any) -> str:
    result = _mapping(getattr(record, "result", {}))
    checkpoint = _mapping(result.get("checkpoint"))
    value = str(checkpoint.get("checkpoint_type") or "").strip()
    if value in {"plan_checkpoint", "guard_checkpoint"}:
        return value
    current = _latest_semantic_step(record)
    if current in {"plan_checkpoint", "guard_checkpoint"}:
        return current
    return "guard_checkpoint"


def _needs_decision(record: Any) -> bool:
    result = _mapping(getattr(record, "result", {}))
    return str(result.get("status") or "").strip().lower() == "needs_decision"


def project_workflow_stages(record: Any) -> list[dict[str, str]]:
    """Replay one run into an explicit, lossless stage-state sequence."""

    kind = _kind(record)
    steps = visible_workflow_steps(kind)
    if not steps:
        return []

    current_raw = _latest_semantic_step(record)
    current_key = _resolve_visible_key(
        kind, current_raw, _latest_payload(record, current_raw), steps
    )
    current_index = _step_index(steps, current_key)
    frontier: int | None = None
    resume_floor: int | None = None
    optional: dict[str, WorkflowStageState] = {}
    seen_raw_steps: set[str] = set()
    observed_keys: set[str] = set()
    status = _status(record)

    events = [(_event_step(event), _event_payload(event)) for event in _events(record)]
    if current_raw and not any(raw == current_raw for raw, _ in events):
        events.append((current_raw, _latest_payload(record, current_raw)))
    for raw, payload in events:
        if _is_progress_observation(raw, payload):
            continue
        seen_raw_steps.add(raw)
        key = _resolve_visible_key(kind, raw, payload, steps)
        index = _step_index(steps, key)
        reset = raw in _HIGH_WATER_RESET_STEPS or str(payload.get("action") or "").lower() in (
            _HIGH_WATER_RESET_ACTIONS
        )
        if reset:
            frontier = index
            resume_floor = None
            # Invalidated work must not leave skipped/failed/completed state in
            # a new attempt. Keep only evidence before the rollback boundary.
            boundary = index if index is not None else current_index or 0
            retained = {step.key for step in steps[:boundary]}
            optional = {key: value for key, value in optional.items() if key in retained}
            observed_keys.intersection_update(retained)
            seen_raw_steps = {raw}
        elif index is not None and (
            kind == "init_long" or current_index is None or index <= current_index
        ):
            frontier = index if frontier is None else max(frontier, index)
        if raw in {"init_resume_anchor", "resume_from_progress"} and index is not None:
            resume_floor = index if resume_floor is None else max(resume_floor, index)
        if index is not None:
            observed_keys.add(steps[index].key)
        outcome = _OPTIONAL_STAGE_OUTCOMES.get(raw)
        if raw == "continuity_repair":
            plan = _mapping(payload.get("repair_plan"))
            reason = str(payload.get("failure_reason") or "")
            if plan.get("no_op") is True and reason.startswith("continuity_score "):
                # The score gate emits a normal repair result even when it
                # never invokes the repair model. Preserve that distinction.
                outcome = ("continuity_repair", "skipped")
            elif payload.get("applied") is True:
                optional.pop("continuity_repair", None)
        if outcome is not None:
            optional[outcome[0]] = outcome[1]  # type: ignore[assignment]
        elif key in optional and (
            raw.endswith(("_start", "_starting"))
            or raw in {"polish_triggered", "polish", "humanize"}
        ):
            # A later retry/re-entry supersedes the previous optional outcome.
            optional.pop(key, None)

    if _needs_decision(record):
        current_index = _step_index(
            steps,
            _nearest_visible_key(kind, _checkpoint_decision_step(record), steps),
        )
        status = "paused"
    candidates = [index for index in (frontier, current_index, resume_floor) if index is not None]
    active_index = max(candidates) if candidates else (0 if status in _ACTIVE_STATES else None)
    if status in _FAILED_STATES and current_index is not None:
        active_index = current_index
    if status == "queued" and not current_raw:
        active_index = None

    research_index = _step_index(steps, "chapter_research")
    research_observed = any(step.startswith("chapter_research_") for step in seen_raw_steps)
    if (
        research_index is not None
        and not research_observed
        and (status == "succeeded" or (active_index is not None and active_index > research_index))
    ):
        # The research milestone is optional.  Absence of a research event means
        # the project/request gate was closed; it must never look like a completed
        # network call merely because a later writing stage advanced the frontier.
        optional["chapter_research"] = "skipped"

    if kind == "run_chapter":
        for index, step in enumerate(steps):
            if (
                step.key in _OPTIONAL_CHAPTER_STAGES
                and step.key not in observed_keys
                and resume_floor is None
                and (status == "succeeded" or (active_index is not None and index < active_index))
            ):
                optional[step.key] = "skipped"

    states: list[WorkflowStageState] = ["pending"] * len(steps)
    if status == "succeeded" and not _needs_decision(record):
        states = ["completed"] * len(steps)
    elif active_index is not None:
        for index in range(active_index):
            states[index] = "completed"
        if status in _FAILED_STATES:
            states[active_index] = "failed"
        elif _needs_decision(record):
            states[active_index] = "blocked"
        else:
            states[active_index] = "active"

    indexes = {step.key: index for index, step in enumerate(steps)}
    for key, state in optional.items():
        index = indexes.get(key)
        if index is not None:
            if (
                kind == "init_long"
                and key == "profile_style"
                and index == active_index
                and status in _ACTIVE_STATES
            ):
                # This visible milestone owns the whole parallel foundation
                # group, not just its style-profile branch.
                continue
            states[index] = state

    if kind == "init_long" and active_index in {2, 3} and status != "succeeded":
        # Both branches are launched together. A completed element selection
        # does not prove that the still-running StoryBible has finished.
        for key in ("init_story_bible", "plan_blueprint_elements"):
            index = indexes[key]
            finished = bool({key, f"{key}_resumed"} & seen_raw_steps)
            if states[index] != "failed":
                states[index] = (
                    "completed" if finished else "active" if status in _ACTIVE_STATES else "pending"
                )

    return [
        {"id": step.key, "label": step.label, "state": states[index]}
        for index, step in enumerate(steps)
    ]


def project_workflow_progress(record: Any, stages: list[dict[str, str]]) -> int:
    """Project progress against the same milestone sequence rendered by clients."""

    status = _status(record)
    if status == "succeeded" and not _needs_decision(record):
        return 100
    if status == "queued" and not _latest_semantic_step(record):
        return 0
    steps = visible_workflow_steps(_kind(record))
    raw = _latest_semantic_step(record)
    has_cursor = (
        _step_index(
            steps, _resolve_visible_key(_kind(record), raw, _latest_payload(record, raw), steps)
        )
        is not None
    )
    has_history = any(
        not _is_progress_observation(_event_step(event), _event_payload(event))
        and _step_index(
            steps,
            _resolve_visible_key(_kind(record), _event_step(event), _event_payload(event), steps),
        )
        is not None
        for event in _events(record)
    )
    if not has_cursor and not has_history:
        # Preserve explicit progress from legacy/extension jobs for which no
        # semantic cursor exists; do not turn their percentage into a fake 3%.
        for source in (getattr(record, "current_step_payload", {}), getattr(record, "result", {})):
            for name in ("progress_percent", "progress", "percent"):
                try:
                    value = float(_mapping(source)[name])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    value = value * 100 if 0 <= value <= 1 else value
                    return max(0, min(99, round(value)))
    if stages:
        settled = sum(stage["state"] in {"completed", "skipped", "rolled_back"} for stage in stages)
        in_flight = sum(stage["state"] in {"active", "failed", "blocked"} for stage in stages)
        return max(0, min(99, round(((settled + 0.5 * in_flight) / len(stages)) * 100)))
    return compute_progress_percent(_kind(record), status, _latest_semantic_step(record))


def project_workflow_current_stage(
    record: Any,
    stages: list[dict[str, str]],
) -> dict[str, str] | None:
    """Select the cursor, not an earlier non-blocking branch failure."""
    raw = _latest_semantic_step(record)
    key = _resolve_visible_key(
        _kind(record), raw, _latest_payload(record, raw), visible_workflow_steps(_kind(record))
    )
    priorities = (
        ("blocked", "active", "failed")
        if _needs_decision(record)
        else ("active", "blocked", "failed")
    )
    for state in priorities:
        candidates = [stage for stage in stages if stage["state"] == state]
        if candidates:
            return next((stage for stage in candidates if stage["id"] == key), candidates[0])
    return next((stage for stage in reversed(stages) if stage["state"] != "pending"), None)


def project_workflow_step_label(record: Any, stages: list[dict[str, str]]) -> str:
    """A completed parallel branch must not replace the still-running cursor."""
    raw = _latest_semantic_step(record)
    payload = _latest_payload(record, raw)
    current = project_workflow_current_stage(record, stages)
    steps = visible_workflow_steps(_kind(record))
    owner = _step_index(steps, _resolve_visible_key(_kind(record), raw, payload, steps))
    if current is not None and owner is not None and current["id"] != steps[owner].key:
        return current["label"]
    return format_step_label(_kind(record), raw, payload)


def _chapter_number(record: Any) -> int:
    sources = [
        _mapping(getattr(record, "current_step_payload", {})),
        _mapping(getattr(record, "result", {})),
    ]
    sources.extend(_event_payload(event) for event in reversed(_events(record)))
    for source in sources:
        for key in ("chapter_number", "chapter"):
            try:
                value = int(source.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return 0


def _read_json_uncached(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json(
    path: Path,
    *,
    context: WorkflowProjectionContext | None = None,
) -> dict[str, Any]:
    return context.read_json(path) if context is not None else _read_json_uncached(path)


def project_workflow_project_label(
    record: Any,
    *,
    storage_root: Path | None = None,
    context: WorkflowProjectionContext | None = None,
) -> str:
    """Resolve the author-facing project name without exposing its storage key.

    New Engine jobs persist ``project_label`` at submission time, before the
    initialization worker writes any artifacts.  For historical jobs we recover
    the same value from durable project metadata in the canonical workspace
    precedence order: Story Bible, story specification, then init request.
    """

    saved_label = str(getattr(record, "project_label", "") or "").strip()
    if saved_label:
        return saved_label

    storage_root = context.storage_root if context is not None else storage_root
    project_id = str(getattr(record, "project_id", "") or "").strip()
    if storage_root is None or not project_id:
        return project_id
    try:
        layout = ProjectLayout(resolve_project_path(storage_root, project_id))
    except ValueError:
        return project_id

    story_bible = _read_json(layout.bible_path, context=context)
    title = str(story_bible.get("title") or "").strip()
    if title:
        return title
    spec = _read_json(layout.spec_path, context=context)
    title = str(spec.get("title") or "").strip()
    if title:
        return title
    init_meta = _read_json(layout.init_request_meta_path, context=context)
    request = _mapping(init_meta.get("request"))
    title = str(request.get("title") or init_meta.get("title") or "").strip()
    return title or project_id


def project_workflow_checkpoint(
    record: Any,
    *,
    storage_root: Path | None = None,
    context: WorkflowProjectionContext | None = None,
) -> WorkflowCheckpointProjection:
    """Read the durable engine checkpoint; never infer one from UI state."""

    storage_root = context.storage_root if context is not None else storage_root
    project_id = str(getattr(record, "project_id", "") or "").strip()
    chapter = _chapter_number(record)
    if (
        storage_root is None
        or not project_id
        or chapter <= 0
        or _kind(record) not in _CHAPTER_KINDS
    ):
        return WorkflowCheckpointProjection()
    try:
        project_path = resolve_project_path(storage_root, project_id)
    except ValueError:
        return WorkflowCheckpointProjection()
    path = ProjectLayout(project_path).chapter_review_progress_path(chapter)
    if not path.is_file():
        return WorkflowCheckpointProjection()
    payload = _read_json(path, context=context)
    return WorkflowCheckpointProjection(
        exists=True,
        completed_stage=str(payload.get("completed_stage") or ""),
        source_text_hash=str(
            payload.get("source_text_hash")
            or payload.get("text_hash")
            or payload.get("draft_hash")
            or ""
        ),
        input_signature=str(payload.get("input_signature") or "legacy_unknown"),
        path=str(path),
    )


def project_workflow_lineage(
    record: Any,
    *,
    storage_root: Path | None = None,
    context: WorkflowProjectionContext | None = None,
) -> WorkflowLineageProjection:
    """Load signed job-level lineage from the project artifact manifest."""

    storage_root = context.storage_root if context is not None else storage_root
    project_id = str(getattr(record, "project_id", "") or "").strip()
    if storage_root is None or not project_id:
        return WorkflowLineageProjection()
    try:
        project_path = resolve_project_path(storage_root, project_id)
    except ValueError:
        return WorkflowLineageProjection()
    manifest = _read_json(
        ProjectLayout(project_path).states_dir / MANIFEST_FILENAME,
        context=context,
    )
    artifacts = _mapping(manifest.get("artifacts"))
    raw = artifacts.get(f"workflow:{_kind(record)}:result")
    parsed = ArtifactRecord.from_payload(f"workflow:{_kind(record)}:result", raw)
    if parsed is None:
        return WorkflowLineageProjection()
    return WorkflowLineageProjection(
        workflow_version=parsed.workflow_version,
        input_signature=parsed.input_signature,
        output_version=parsed.output_version,
        quality_status=parsed.quality_status,
        derivation_status=parsed.derivation_status,
        degradation_reason=parsed.degradation_reason,
        parent_artifact_versions=tuple(sorted(parsed.parent_artifact_versions.items())),
        template_version=str(parsed.metadata.get("template_version") or "legacy_unknown"),
        config_fingerprint=str(parsed.metadata.get("config_fingerprint") or "legacy_unknown"),
        model_fingerprint=str(parsed.metadata.get("model_fingerprint") or "legacy_unknown"),
    )


def _first_text(sources: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _runtime_quality(record: Any, lineage: WorkflowLineageProjection) -> tuple[str, str]:
    current = _mapping(getattr(record, "current_step_payload", {}))
    result = _mapping(getattr(record, "result", {}))
    # A failed check followed by a successful recheck is history, not a current
    # blocker. Keep the latest observation per check/dimension, without letting
    # a passing check erase a different, still unresolved quality gate.
    recent: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for event in reversed(_events(record)):
        payload = _event_payload(event)
        key = (
            _event_step(event),
            *(
                str(payload.get(field) or "")
                for field in ("stage", "dimension", "chapter", "chapter_number")
            ),
        )
        if key not in seen:
            seen.add(key)
            recent.append(payload)
    sources = [result, current, *recent]
    reason = lineage.degradation_reason or _first_text(
        sources,
        ("degradation_reason", "degraded_reason", "fallback_reason", "blocked_reason"),
    )
    if _status(record) == "failed":
        return "blocked", reason or str(getattr(record, "error", "") or "任务失败")
    if result.get("blocked") is True:
        return "blocked", _first_text([result], ("blocked_reason", "summary")) or reason
    # A result's explicit quality describes the delivered artifact. Interim
    # repair/fallback events must not override it (including explicit blockers).
    result_quality = _first_text(
        [result], ("execution_quality_status", "quality_status", "evaluation_status")
    ).lower()
    if result_quality in {"actual", "degraded", "fallback", "blocked"}:
        return result_quality, _first_text(
            [result], ("degradation_reason", "degraded_reason", "fallback_reason", "blocked_reason")
        )
    for source in sources:
        if source.get("blocked") is True:
            return "blocked", _first_text([source], ("blocked_reason", "summary")) or reason
    explicit = _first_text(
        sources,
        ("execution_quality_status", "quality_status", "evaluation_status"),
    ).lower()
    if explicit in {"actual", "degraded", "fallback", "blocked"}:
        return explicit, reason
    if any(
        source.get("fallback") is True or source.get("is_fallback") is True for source in sources
    ):
        return "fallback", reason
    if reason or any(source.get("degraded") is True for source in sources):
        return "degraded", reason
    if lineage.quality_status in {"actual", "degraded", "fallback", "blocked"}:
        return lineage.quality_status, reason
    return "actual", ""


def _stale_dependencies(record: Any, lineage: WorkflowLineageProjection) -> list[str]:
    values: list[str] = []
    for source in (
        _mapping(getattr(record, "current_step_payload", {})),
        _mapping(getattr(record, "result", {})),
    ):
        for key in ("stale_dependencies", "blocking_reasons", "lineage_conflicts"):
            raw = source.get(key)
            if isinstance(raw, list):
                values.extend(str(item).strip() for item in raw if str(item).strip())
    if lineage.derivation_status in {"stale", "conflict", "blocked"}:
        values.append(f"产物血缘状态：{lineage.derivation_status}")
    return list(dict.fromkeys(values))


def _recovery_actions(
    record: Any,
    checkpoint: WorkflowCheckpointProjection,
    *,
    storage_root: Path | None,
) -> list[dict[str, Any]]:
    status = _status(record)
    actions: list[dict[str, Any]] = []
    if status in {"queued", "running"}:
        actions.append({"id": "cancel", "kind": "cancel", "label": "停止", "enabled": True})
    if status == "paused":
        actions.append({"id": "resume", "kind": "resume", "label": "恢复", "enabled": True})
    if status == "failed" and checkpoint.exists:
        actions.append(
            {
                "id": "resume_checkpoint",
                "kind": "resume_checkpoint",
                "label": "断点续写",
                "enabled": True,
            }
        )
    summary = _mapping(getattr(record, "error_summary", {}))
    retryable = bool(summary.get("retryable", False))
    if status == "failed" and retryable and not checkpoint.exists:
        actions.append({"id": "retry", "kind": "retry", "label": "安全重试", "enabled": True})
    if status == "failed" and _kind(record) == "init_long" and storage_root is not None:
        project_id = str(getattr(record, "project_id", "") or "").strip()
        try:
            init_meta: Path | None = ProjectLayout(
                resolve_project_path(storage_root, project_id)
            ).init_request_meta_path
        except ValueError:
            init_meta = None
        if project_id and init_meta is not None and init_meta.is_file():
            actions.append(
                {
                    "id": "retry_init_repair",
                    "kind": "retry_init_repair",
                    "label": "AI修复",
                    "enabled": True,
                }
            )
        if project_id:
            try:
                manual_repair = load_manual_init_repair(
                    FileSystemStorage(storage_root),
                    project_id,
                )
            except (ManualInitRepairError, OSError):
                manual_repair = None
            if manual_repair is not None and manual_repair.available:
                actions.append(
                    {
                        "id": "manual_init_repair",
                        "kind": "manual_init_repair",
                        "label": "人工修复",
                        "enabled": True,
                    }
                )
    raw_actions = summary.get("recovery_actions")
    if isinstance(raw_actions, list):
        for index, raw in enumerate(raw_actions):
            if isinstance(raw, dict):
                action = str(raw.get("action") or raw.get("kind") or f"recovery_{index}")
                label = str(raw.get("label") or raw.get("title") or action)
            else:
                action = f"guidance_{index}"
                label = str(raw)
            if label.strip():
                actions.append(
                    {"id": action, "kind": action, "label": label.strip(), "enabled": False}
                )
    return actions


def project_workflow_run(
    record: Any,
    *,
    storage_root: Path | None = None,
    context: WorkflowProjectionContext | None = None,
) -> dict[str, Any]:
    """Build the complete engine-owned workflow run projection."""

    stages = project_workflow_stages(record)
    active = project_workflow_current_stage(record, stages)
    current_raw = _latest_semantic_step(record)
    storage_root = context.storage_root if context is not None else storage_root
    checkpoint = project_workflow_checkpoint(
        record,
        storage_root=storage_root,
        context=context,
    )
    lineage = project_workflow_lineage(
        record,
        storage_root=storage_root,
        context=context,
    )
    quality_status, degradation_reason = _runtime_quality(record, lineage)
    actions = _recovery_actions(record, checkpoint, storage_root=storage_root)
    state = _status(record)
    stage_label = str((active or {}).get("label") or display_step_name(current_raw))
    stale_dependencies = _stale_dependencies(record, lineage)
    transparency = project_run_transparency(record)
    parent_versions = {key: value for key, value in lineage.parent_artifact_versions}
    return {
        "workflow_projection_version": WORKFLOW_PROJECTION_VERSION,
        "workflow_version": lineage.workflow_version,
        "stage_id": str((active or {}).get("id") or resolve_step_key(_kind(record), current_raw)),
        "actual_state": state,
        "input_signature": lineage.input_signature,
        "output_version": lineage.output_version,
        "parent_artifact_versions": parent_versions,
        "template_version": lineage.template_version,
        "config_fingerprint": lineage.config_fingerprint,
        "model_fingerprint": lineage.model_fingerprint,
        "quality_status": quality_status,
        "degradation_reason": degradation_reason,
        "derivation_status": lineage.derivation_status,
        "retryable": any(action["enabled"] for action in actions if action["kind"] != "cancel"),
        "recovery_actions": actions,
        "checkpoint": {
            "exists": checkpoint.exists,
            "completed_stage": checkpoint.completed_stage,
            "source_text_hash": checkpoint.source_text_hash,
            "input_signature": checkpoint.input_signature,
        },
        "stale_dependencies": stale_dependencies,
        "cumulative_tokens": max(0, int(getattr(record, "cumulative_tokens", 0) or 0)),
        "cumulative_cost_usd": max(0.0, float(getattr(record, "cumulative_cost_usd", 0.0) or 0.0)),
        "progress_percent": project_workflow_progress(record, stages),
        "current_stage_label": stage_label,
        "step_label": project_workflow_step_label(record, stages),
        "activity_label": stage_label,
        "stages": stages,
        **transparency,
    }


__all__ = [
    "WORKFLOW_PROJECTION_VERSION",
    "WorkflowCheckpointProjection",
    "WorkflowLineageProjection",
    "WorkflowProjectionContext",
    "project_workflow_checkpoint",
    "project_workflow_lineage",
    "project_workflow_project_label",
    "project_workflow_progress",
    "project_workflow_run",
    "project_run_transparency",
    "project_workflow_stages",
    "visible_workflow_steps",
]
