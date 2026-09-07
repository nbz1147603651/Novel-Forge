"""Desktop adapters for shared pipeline progress metadata."""

from __future__ import annotations

from typing import Any

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord
from novel_forge.desktop.widgets import PipelineStep
from novel_forge.pipeline.progress import (
    INIT_COHERENCE_ARTIFACT_LABELS as _INIT_COHERENCE_ARTIFACT_LABELS,
)
from novel_forge.pipeline.progress import (
    INIT_COHERENCE_STAGE_LABELS as _INIT_COHERENCE_STAGE_LABELS,
)
from novel_forge.pipeline.progress import (
    INIT_COHERENCE_VERDICT_LABELS as _INIT_COHERENCE_VERDICT_LABELS,
)
from novel_forge.pipeline.progress import (
    compute_progress_percent,
    display_step_name,
    is_non_progress_step_event,
    parallel_groups_for,
    resolve_step_key,
    summary_steps,
)

_MEMORY_AWARE_KINDS: frozenset[str] = frozenset(
    {
        "run_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    }
)

# Phase boundaries for resolve_chapter_checkpoint to prevent high-water mark
# from polluting earlier stages with late-stage progress values.
# Maps normalized step keys to their phase index (0-based, sequential).
_RESOLVE_CHECKPOINT_PHASES: dict[str, int] = {
    "plan_checkpoint": 0,
    "resume_from_progress": 0,
    "draft": 1,
    "draft_wave": 1,
    "pre_alignment": 2,
    "pre_alignment_reports": 2,
    "continuity_repair": 3,
    "continuity_repair_recheck": 3,
    "alignment_repair": 4,
    "alignment_repair_edit": 4,
    "alignment_repair_recheck": 4,
    "post_alignment": 5,
    "post_alignment_dedup": 5,
    "post_alignment_causal": 5,
    "post_alignment_rp": 5,
    "post_alignment_rp_final": 5,
    "post_alignment_humanize": 5,
    "guard_checkpoint": 6,
    "post_guard_repair": 7,
    "polish_reextract_canon": 8,
    "persist": 9,
    "evaluate": 10,
    "volume_audit": 11,
    "memory_updated": 12,
}


def ui_steps_for_kind(kind: str) -> list[PipelineStep]:
    """Convert shared step metadata into desktop indicator steps."""
    return [PipelineStep(step.key, step.label, step.is_prefix) for step in summary_steps(kind)]


def ui_parallel_pairs_for(kind: str) -> list[tuple[str, str]]:
    """Return concurrent step-key pairs for desktop step indicators."""
    return parallel_groups_for(kind)


_INIT_LONG_OUTLINE_PREFIXES = (
    "plan_outline_starting",
    "plan_outline_cache_rejected",
    "plan_outline_resume_rejected",
    "plan_outline_batch_",
    "plan_outline_continue_",
    "plan_outline_repair_",
)
_INIT_LONG_OUTLINE_PROGRESS_START = 82
_INIT_LONG_OUTLINE_PROGRESS_SPAN = 9
_INIT_LONG_OUTLINE_PROGRESS_MIN = 82
_INIT_LONG_OUTLINE_PROGRESS_MAX = 91

_INIT_LONG_BLUEPRINT_BLOCKS: tuple[str, ...] = (
    "overview",
    "phases",
    "turning_points",
    "character_arcs",
    "subplots",
    "suspense",
    "ending",
)

_INIT_LONG_BLUEPRINT_BLOCK_TITLES: dict[str, str] = {
    "overview": "全书概述",
    "phases": "叙事阶段",
    "turning_points": "关键转折",
    "character_arcs": "角色弧光",
    "subplots": "支线规划",
    "suspense": "悬念规划",
    "ending": "收束策略",
}

_INIT_COHERENCE_STAGE_PROGRESS_STEPS: dict[str, str] = {
    "blueprint_coherence": "adjudicate_blueprint_coherence",
    "outline_inheritance": "adjudicate_outline_inheritance",
    "contract_coherence": "adjudicate_contract_coherence",
}

_INIT_COHERENCE_STAGE_PROGRESS_BOUNDS: dict[str, tuple[int, int]] = {
    "blueprint_coherence": (79, 80),
    "outline_inheritance": (92, 93),
    "contract_coherence": (95, 96),
}

_INIT_RESUME_ANCHOR_STEP = "init_resume_anchor"
# ``init_resume_fallback`` merely switches from the source-artifact fast path
# to the ordinary per-artifact resume path.  It does not mutate or invalidate
# artifacts, so it must not erase the validated display floor.  A genuine
# invalidation always emits ``init_resume_rollback`` separately.
_INIT_RESUME_PROGRESS_RESET_STEPS = frozenset({"init_resume_rollback"})


def _init_resume_anchor_payload_step(payload: dict[str, Any]) -> str:
    return str(payload.get("step") or payload.get("anchor_step") or "").strip()


def _init_long_resume_anchor_rank(step: str) -> int:
    resolved = resolve_step_key("init_long", step)
    for index, spec in enumerate(summary_steps("init_long")):
        if resolved == spec.key or (spec.is_prefix and resolved.startswith(spec.key)):
            return index
    return -1


def _init_long_events_after_resume_reset(job: DesktopJobRecord) -> list[DesktopJobEvent]:
    """Project only events belonging to the currently accepted resume attempt."""

    start = 0
    for index, event in enumerate(job.events):
        if event.step in _INIT_RESUME_PROGRESS_RESET_STEPS:
            start = index + 1
    return job.events[start:]


def init_long_resume_anchor_step(job: DesktopJobRecord) -> str:
    """Return the highest validated init artifact used as a resume display floor."""
    if job.kind != "init_long":
        return ""
    best_step = ""
    best_progress = -1
    best_rank = -1
    for event in _init_long_events_after_resume_reset(job):
        payload = event.payload if isinstance(event.payload, dict) else {}
        step = ""
        if event.step == _INIT_RESUME_ANCHOR_STEP:
            step = _init_resume_anchor_payload_step(payload)
        elif event.step.endswith("_resumed"):
            step = event.step
        if not step:
            continue
        progress = _compute_step_progress(job.kind, "running", step, payload)
        rank = _init_long_resume_anchor_rank(step)
        if progress > best_progress or (progress == best_progress and rank > best_rank):
            best_progress = progress
            best_rank = rank
            best_step = step
    return best_step


def _init_long_resume_anchor_progress(job: DesktopJobRecord) -> int | None:
    if job.kind != "init_long":
        return None
    best_progress: int | None = None
    for event in _init_long_events_after_resume_reset(job):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.step == _INIT_RESUME_ANCHOR_STEP:
            progress = _compute_step_progress(job.kind, "running", event.step, payload)
        elif event.step.endswith("_resumed"):
            progress = _compute_step_progress(job.kind, "running", event.step, payload)
        else:
            continue
        best_progress = progress if best_progress is None else max(best_progress, progress)
    return best_progress


def _is_init_long_outline_step(step: str) -> bool:
    return any(step.startswith(prefix) for prefix in _INIT_LONG_OUTLINE_PREFIXES)


def _init_long_blueprint_block_key(step: str) -> str:
    step = step.removesuffix("_resumed")
    prefix = "plan_blueprint_"
    if not step.startswith(prefix):
        return ""
    block = step[len(prefix) :]
    return block if block in _INIT_LONG_BLUEPRINT_BLOCKS else ""


def _is_init_long_blueprint_block_step(step: str) -> bool:
    return bool(_init_long_blueprint_block_key(step))


def _latest_payload_for_step(job: DesktopJobRecord, step: str) -> dict[str, Any]:
    if step == str(job.current_step or ""):
        current_payload = getattr(job, "current_step_payload", {})
        if isinstance(current_payload, dict) and current_payload:
            return current_payload
    for event in reversed(job.events):
        if event.step == step:
            return event.payload
    return {}


def _compute_init_long_outline_progress(payload: dict[str, Any]) -> int | None:
    chapters_done = payload.get(
        "safe_chapters_done",
        payload.get("safe_saved_chapter", payload.get("chapters_done")),
    )
    chapters_total = payload.get("chapters_total", payload.get("total_chapters"))
    if not isinstance(chapters_done, (int, float)) or not isinstance(chapters_total, (int, float)):
        return None
    if chapters_total <= 0:
        return None
    ratio = max(0.0, min(float(chapters_done) / float(chapters_total), 1.0))
    progress = _INIT_LONG_OUTLINE_PROGRESS_START + round(ratio * _INIT_LONG_OUTLINE_PROGRESS_SPAN)
    return max(_INIT_LONG_OUTLINE_PROGRESS_MIN, min(progress, _INIT_LONG_OUTLINE_PROGRESS_MAX))


def _compute_init_long_blueprint_block_progress(step: str, payload: dict[str, Any]) -> int | None:
    block_total = payload.get("block_total")
    block_index = payload.get("block_index")
    if (
        isinstance(block_index, (int, float))
        and isinstance(block_total, (int, float))
        and block_total > 0
    ):
        ratio = max(0.0, min(float(block_index) / float(block_total), 1.0))
        return max(72, min(round(72 + ratio * 4), 76))
    block = _init_long_blueprint_block_key(step)
    if not block:
        return None
    index = _INIT_LONG_BLUEPRINT_BLOCKS.index(block) + 1
    ratio = index / len(_INIT_LONG_BLUEPRINT_BLOCKS)
    return max(72, min(round(72 + ratio * 4), 76))


def _init_coherence_progress_bounds(payload: dict[str, Any]) -> tuple[int, int]:
    stage = str(payload.get("stage") or "").strip()
    return _INIT_COHERENCE_STAGE_PROGRESS_BOUNDS.get(stage, (79, 80))


def _init_coherence_batch_progress(payload: dict[str, Any]) -> tuple[object, object]:
    """Return displayed completed batches and total for init-coherence events.

    Claim extraction runs concurrently, so the raw chunk index can complete out
    of order.  ``batches_done`` is the monotonic completed count; legacy events
    fall back to ``batch``.
    """
    return payload.get("batches_done", payload.get("batch")), payload.get("batch_total")


def _compute_init_claims_progress(payload: dict[str, Any]) -> int:
    start, end = _init_coherence_progress_bounds(payload)
    batch, batch_total = _init_coherence_batch_progress(payload)
    if not isinstance(batch, (int, float)) or not isinstance(batch_total, (int, float)):
        return start
    if batch_total <= 0:
        return start
    ratio = max(0.0, min(float(batch) / float(batch_total), 1.0))
    return max(start, min(round(start + ratio * (end - start)), end))


def _compute_init_adjudication_progress(payload: dict[str, Any]) -> int:
    start, end = _init_coherence_progress_bounds(payload)
    batch = payload.get("batch")
    batch_total = payload.get("batch_total")
    if not isinstance(batch, (int, float)) or not isinstance(batch_total, (int, float)):
        return start
    if batch_total <= 0:
        return start
    ratio = max(0.0, min(float(batch) / float(batch_total), 1.0))
    return max(start, min(round(start + ratio * (end - start)), end))


def _compute_reextract_progress(payload: dict[str, Any]) -> int | None:
    """Compute reextract progress percentage from chapter payload (5–95 range)."""
    progress = payload.get("progress")
    total = payload.get("total")
    if not isinstance(progress, (int, float)) or not isinstance(total, (int, float)):
        return None
    if total <= 0:
        return None
    ratio = max(0.0, min(float(progress) / float(total), 1.0))
    return max(5, min(round(5 + ratio * 90), 94))


def _compute_book_consistency_audit_chunk_progress(payload: dict[str, Any]) -> int | None:
    """Compute dynamic progress for whole-book audit chunk phase (0–50% range)."""
    total = payload.get("total")
    current = payload.get("current")
    if not isinstance(total, (int, float)) or not isinstance(current, (int, float)):
        return None
    if total <= 0:
        return None
    ratio = max(0.0, min(float(current) / float(total), 1.0))
    return max(0, min(round(ratio * 50), 50))


def _compute_book_consistency_repair_progress(payload: dict[str, Any]) -> int | None:
    """Compute dynamic progress for whole-book targeted repair phase (75–100% range)."""
    total = payload.get("total", payload.get("targeted"))
    processed = payload.get("processed", payload.get("current", payload.get("applied")))
    if not isinstance(total, (int, float)) or not isinstance(processed, (int, float)):
        return None
    if total <= 0:
        return None
    ratio = max(0.0, min(float(processed) / float(total), 1.0))
    return max(75, min(round(75 + ratio * 25), 100))


def _compute_motif_repair_layer2_progress(payload: dict[str, Any]) -> int | None:
    """Compute dynamic progress for motif history re-extraction."""
    total = payload.get("total")
    processed = payload.get("processed")
    if not isinstance(total, (int, float)) or not isinstance(processed, (int, float)):
        return None
    if total <= 0:
        return None
    ratio = max(0.0, min(float(processed) / float(total), 1.0))
    return max(41, min(round(40 + ratio * 44), 84))


_REPAIR_LOOP_DIMENSIONS: dict[str, tuple[int, int]] = {
    "continuity_repair": (73, 75),
    "causal_repair": (87, 87),
    "reading_power_repair": (88, 89),
}

_RESOLVE_CHECKPOINT_REPAIR_LOOP_DIMENSIONS: dict[str, tuple[int, int]] = {
    "continuity_repair": (48, 52),
    "causal_repair": (87, 87),
    "reading_power_repair": (88, 89),
}

_RESOLVE_RESUME_STAGE_STEPS: dict[str, str] = {
    "draft_done": "pre_alignment",
    "quality_done": "post_alignment",
    "repair_done": "post_alignment",
    "canon_done": "guard_checkpoint",
}

_RESOLVE_RESUME_STAGE_LABELS: dict[str, str] = {
    "draft_done": "初稿成章",
    "quality_done": "质量检查",
    "repair_done": "修复循环",
    "canon_done": "剧情状态提取",
}


def resolve_checkpoint_resume_step(payload: dict[str, Any] | None) -> str:
    """Map a review-progress checkpoint to the next visible resume stage."""
    stage = str((payload or {}).get("completed_stage") or "").strip()
    return _RESOLVE_RESUME_STAGE_STEPS.get(stage, "")


def _compute_repair_loop_progress(
    job: DesktopJobRecord,
    dimension: str,
) -> int | None:
    """Compute dynamic progress for repair loop dimensions.

    Interpolates between start→end pct based on round/max_rounds from the
    latest ``repair_attempt_guidance`` event.  Returns ``None`` when the
    dimension is unknown or the payload is incomplete.
    """
    bounds_by_dimension = (
        _RESOLVE_CHECKPOINT_REPAIR_LOOP_DIMENSIONS
        if job.kind == "resolve_chapter_checkpoint"
        else _REPAIR_LOOP_DIMENSIONS
    )
    bounds = bounds_by_dimension.get(dimension)
    if bounds is None:
        return None
    start, end = bounds
    p = _latest_payload_for_step(job, "repair_attempt_guidance")
    round_num = p.get("round")
    max_rounds = p.get("max_rounds")
    if not isinstance(round_num, (int, float)) or not isinstance(max_rounds, (int, float)):
        return start
    mr = int(max_rounds)
    if mr <= 1:
        return start
    rn = int(round_num)
    ratio = max(0.0, min(float(rn - 1) / float(mr - 1), 1.0))
    return max(start, min(round(start + ratio * (end - start)), end))


def _compute_step_progress(
    kind: str,
    status: str,
    step: str,
    payload: dict[str, Any] | None = None,
) -> int:
    """Compute a step's progress with payload-aware dynamic adapters."""
    if kind == "init_long" and step == _INIT_RESUME_ANCHOR_STEP:
        p = payload or {}
        anchor_step = _init_resume_anchor_payload_step(p)
        if _is_init_long_outline_step(anchor_step):
            dynamic = _compute_init_long_outline_progress(p)
            if dynamic is not None:
                return dynamic
        if anchor_step:
            return compute_progress_percent(kind, status, anchor_step)
    if kind == "init_long" and _is_init_long_outline_step(step):
        dynamic = _compute_init_long_outline_progress(payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "init_long" and _is_init_long_blueprint_block_step(step):
        dynamic = _compute_init_long_blueprint_block_progress(step, payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "init_long" and step.startswith("extract_init_coherence_claims"):
        return _compute_init_claims_progress(payload or {})
    if kind == "init_long" and step.startswith("retrieve_init_conflict_candidates"):
        _, end = _init_coherence_progress_bounds(payload or {})
        return end
    if kind == "init_long" and step.startswith("adjudicate_init_conflict_candidates"):
        return _compute_init_adjudication_progress(payload or {})
    if kind == "init_long" and step == "init_coherence_report_resumed":
        stage_step = _INIT_COHERENCE_STAGE_PROGRESS_STEPS.get(
            str((payload or {}).get("stage") or "")
        )
        if stage_step:
            return compute_progress_percent(kind, status, stage_step)
    if kind == "init_long" and step.startswith("init_repair_reaudit"):
        # A post-repair audit still belongs to the late contract phase. Keep
        # the progress high-water mark stable while the affected gates rerun.
        return compute_progress_percent(
            kind,
            status,
            "adjudicate_contract_coherence",
        )
    if kind == "init_long" and step.startswith(
        ("repair_init_artifact_patch", "repair_init_artifact_targets")
    ):
        p = payload or {}
        stage = str(p.get("stage") or "").strip()
        artifact = str(p.get("artifact") or "").strip()
        stage_step = _INIT_COHERENCE_STAGE_PROGRESS_STEPS.get(stage)
        if not stage_step:
            stage_step = {
                "blueprint": "repair_init_artifact_patch",
                "outline": "adjudicate_outline_inheritance",
                "chapter_contracts": "adjudicate_contract_coherence",
            }.get(artifact)
        if stage_step:
            return compute_progress_percent(kind, status, stage_step)
    if kind == "resolve_chapter_checkpoint" and step == "resume_from_progress":
        resume_step = resolve_checkpoint_resume_step(payload)
        if resume_step:
            return compute_progress_percent(kind, status, resume_step)
    if kind == "reextract_relationships" and step == "reextract_chapter":
        dynamic = _compute_reextract_progress(payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "book_consistency" and step == "book_consistency_chunk_progress":
        dynamic = _compute_book_consistency_audit_chunk_progress(payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "book_consistency" and step == "book_consistency_repair_progress":
        dynamic = _compute_book_consistency_repair_progress(payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "repair_motif_history" and step in (
        "motif_repair_layer2_progress",
        "motif_repair_layer2_scanning",
    ):
        dynamic = _compute_motif_repair_layer2_progress(payload or {})
        if dynamic is not None:
            return dynamic
    if kind == "book_consistency" and step == "book_consistency_verify_progress":
        p = payload or {}
        total = p.get("total")
        processed = p.get("processed", p.get("current"))
        if isinstance(total, int) and total > 0 and isinstance(processed, int):
            ratio = max(0.0, min(float(processed) / float(total), 1.0))
            return max(50, min(round(50 + ratio * 25), 75))
    return compute_progress_percent(kind, status, step)


def _last_non_transient_step(job: DesktopJobRecord) -> str:
    """Walk backwards through events to find the last meaningful (non-transient) step."""
    for event in reversed(job.events):
        if not is_non_progress_step_event(event.step):
            return event.step
    return ""


def _summary_label_for_kind(kind: str, step_key: str) -> str:
    """Return the visible task-flow label for a normalized summary step key."""
    if not step_key:
        return ""
    for spec in summary_steps(kind):
        if spec.key == step_key:
            return spec.label
        if spec.is_prefix and step_key.startswith(spec.key):
            return spec.label
    return ""


def _get_phase_index(kind: str, step_key: str) -> int:
    """Return the phase index for a step within a job kind.

    Returns -1 when phase boundaries are not defined for this kind.
    """
    if kind in {"resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"}:
        return _RESOLVE_CHECKPOINT_PHASES.get(step_key, -1)
    return -1


def _phase_step_for_event(kind: str, raw_step: str, payload: dict[str, Any]) -> str:
    if kind == "resolve_chapter_checkpoint" and raw_step == "resume_from_progress":
        resume_step = resolve_checkpoint_resume_step(payload)
        if resume_step:
            return resume_step
    if kind == "init_long" and _is_init_long_outline_step(raw_step):
        return raw_step
    return resolve_step_key(kind, raw_step)


def _looks_like_raw_step_label(label: str) -> bool:
    """Return True when a display label still looks like an untranslated step key."""
    stripped = label.strip()
    if not stripped:
        return False
    return all(ch.isascii() and (ch.isalnum() or ch in {"_", "-", ":", "."}) for ch in stripped)


def _artifact_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _INIT_COHERENCE_ARTIFACT_LABELS.get(raw, raw)


def _init_coherence_stage_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _INIT_COHERENCE_STAGE_LABELS.get(raw, raw)


def _init_coherence_verdict_label(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return _INIT_COHERENCE_VERDICT_LABELS.get(raw, raw)


def _format_init_long_claims_step(base: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    batch, batch_total = _init_coherence_batch_progress(payload)
    if (
        isinstance(batch, (int, float))
        and isinstance(batch_total, (int, float))
        and batch_total > 0
    ):
        parts.append(f"{int(batch)} / {int(batch_total)}")
    artifact = _artifact_label(payload.get("artifact"))
    if artifact:
        parts.append(f"当前检查：{artifact}")
    claims = payload.get("claims")
    if isinstance(claims, (int, float)):
        claim_label = (
            "本批抽取"
            if str(payload.get("extraction_mode") or "").strip() == "stream"
            else "已抽取"
        )
        parts.append(f"{claim_label} {int(claims)} 条")
    fallback_claims = payload.get("fallback_claims")
    if isinstance(fallback_claims, (int, float)) and fallback_claims > 0:
        parts.append(f"本地兜底 {int(fallback_claims)} 条")
    filtered_claims = payload.get("filtered_claims")
    if isinstance(filtered_claims, (int, float)) and filtered_claims > 0:
        parts.append(f"过滤低信号 {int(filtered_claims)} 条")
    max_parallel = payload.get("max_parallel")
    if isinstance(max_parallel, (int, float)) and max_parallel > 1:
        parts.append(f"并发 {int(max_parallel)}")
    if payload.get("cached"):
        source = str(payload.get("source") or "").strip()
        parts.append("已复用账本" if source == "claim_ledger" else "已复用缓存")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def _format_init_long_candidate_retrieval_step(base: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    stage = _init_coherence_stage_label(payload.get("stage"))
    if stage:
        parts.append(f"当前层：{stage}")
    active_claims = payload.get("active_claims", payload.get("claims"))
    extracted_claims = payload.get("extracted_claims")
    if isinstance(active_claims, (int, float)):
        if isinstance(extracted_claims, (int, float)) and int(extracted_claims) != int(
            active_claims
        ):
            parts.append(f"活跃一致性 Claims {int(active_claims)} / 抽取 {int(extracted_claims)}")
        else:
            parts.append(f"一致性 Claims {int(active_claims)}")
    candidates = payload.get("candidates")
    if isinstance(candidates, (int, float)):
        parts.append(f"候选 {int(candidates)}")
    if payload.get("degraded_memory"):
        parts.append("语义召回降级")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def _format_init_long_candidate_adjudication_step(base: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    stage = _init_coherence_stage_label(payload.get("stage"))
    if stage:
        parts.append(f"当前层：{stage}")
    batch = payload.get("batch")
    batch_total = payload.get("batch_total")
    if (
        isinstance(batch, (int, float))
        and isinstance(batch_total, (int, float))
        and batch_total > 0
    ):
        parts.append(f"批次 {int(batch)} / {int(batch_total)}")
    max_parallel = payload.get("max_parallel")
    if isinstance(max_parallel, (int, float)) and max_parallel > 1:
        parts.append(f"并发 {int(max_parallel)}")
    candidates = payload.get("candidates")
    if isinstance(candidates, (int, float)):
        parts.append(f"候选 {int(candidates)}")
    issues = payload.get("issues", payload.get("issue_count"))
    if isinstance(issues, (int, float)):
        parts.append(f"问题 {int(issues)}")
    high_or_critical = payload.get("high_or_critical")
    if isinstance(high_or_critical, (int, float)) and high_or_critical > 0:
        parts.append(f"高危 {int(high_or_critical)}")
    verdict = _init_coherence_verdict_label(payload.get("verdict"))
    if verdict:
        parts.append(f"判定：{verdict}")
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


def _format_init_long_blueprint_block_step(
    base: str,
    step: str,
    payload: dict[str, Any],
) -> str:
    block = str(payload.get("block") or _init_long_blueprint_block_key(step) or "").strip()
    block_title = str(
        payload.get("block_title") or _INIT_LONG_BLUEPRINT_BLOCK_TITLES.get(block, "")
    ).strip()
    block_index = payload.get("block_index")
    block_total = payload.get("block_total")
    if not isinstance(block_index, (int, float)) or not isinstance(block_total, (int, float)):
        if block in _INIT_LONG_BLUEPRINT_BLOCKS:
            block_index = _INIT_LONG_BLUEPRINT_BLOCKS.index(block) + 1
            block_total = len(_INIT_LONG_BLUEPRINT_BLOCKS)
    parts: list[str] = []
    if (
        isinstance(block_index, (int, float))
        and isinstance(block_total, (int, float))
        and block_total > 0
    ):
        parts.append(f"{int(block_index)} / {int(block_total)}")
    if block_title and block_title not in base:
        parts.append(block_title)
    if not parts:
        return base
    return f"{base}  ·  {'  ·  '.join(parts)}"


_OUTLINE_BATCH_STATUS_LABELS: dict[str, str] = {
    "accepted": "本批已提交",
    "pending": "未提交",
    "incomplete": "未提交",
    "rejected": "未提交",
    "quality_retry": "重试中，未提交",
}


def format_init_long_outline_step_label(base: str, payload: dict[str, Any]) -> str:
    """Return an outline label that distinguishes generated text from safe commits."""
    chapters_total = payload.get("chapters_total", payload.get("total_chapters"))
    safe_saved_chapter = payload.get(
        "safe_saved_chapter",
        payload.get("safe_chapters_done", payload.get("chapters_done")),
    )
    if not isinstance(chapters_total, (int, float)) or int(chapters_total) <= 0:
        return base
    if not isinstance(safe_saved_chapter, (int, float)):
        return base

    total = int(chapters_total)
    safe = max(0, min(int(safe_saved_chapter), total))
    parts: list[str] = []
    if safe > 0:
        parts.append(f"已安全保存到第{safe}章（{safe}/{total}）")
    else:
        parts.append(f"尚无安全保存章节（0/{total}）")

    batch_start = payload.get("batch_start")
    batch_end = payload.get("batch_end")
    status = str(payload.get("current_batch_status") or "").strip()
    status_label = _OUTLINE_BATCH_STATUS_LABELS.get(status)
    if isinstance(batch_start, (int, float)) and isinstance(batch_end, (int, float)):
        batch_label = f"第{int(batch_start)}-{int(batch_end)}章"
        if status == "accepted":
            parts.append(f"{batch_label}已提交")
        elif status_label:
            parts.append(f"{batch_label}{status_label}")
    elif status_label:
        parts.append(status_label)

    return f"{base}  ·  {'  ·  '.join(parts)}"


_MOTIF_REPAIR_STATUS_LABELS: dict[str, str] = {
    "skipped": "已有缓存，跳过",
    "missing": "章节文件缺失",
    "done": "已提取",
    "empty": "无母题返回",
    "error": "提取失败",
}

_BOOK_ANALYSIS_MODE_LABELS: dict[str, str] = {
    "full_text": "全文深审",
    "summary": "摘要审计",
}

_BOOK_REPAIR_STATUS_LABELS: dict[str, str] = {
    "matching": "定位问题",
    "running": "修复中",
    "applied": "已应用",
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "修复失败",
    "blocked": "已阻止",
}


def _positive_int(payload: dict[str, Any], key: str) -> int:
    try:
        value = int(payload.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _book_analysis_mode_label(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    return _BOOK_ANALYSIS_MODE_LABELS.get(normalized, mode)


def _motif_layer2_done_summary(payload: dict[str, Any]) -> str:
    processed = _positive_int(payload, "chapters_processed")
    skipped = _positive_int(payload, "chapters_skipped")
    extracted = _positive_int(payload, "motifs_extracted")
    empty = _positive_int(payload, "chapters_empty")
    missing = _positive_int(payload, "chapters_missing")
    failed = _positive_int(payload, "chapters_failed")
    parts: list[str] = []
    if processed:
        parts.append(f"提取 {processed} 章")
    if skipped:
        parts.append(f"跳过 {skipped} 章")
    if extracted:
        parts.append(f"母题片段 {extracted} 条")
    if empty:
        parts.append(f"空结果 {empty} 章")
    if missing:
        parts.append(f"缺文件 {missing} 章")
    if failed:
        parts.append(f"失败 {failed} 章")
    return "，".join(parts)


def _motif_layer2_running_summary(job: DesktopJobRecord) -> str:
    status_counts: dict[str, int] = {}
    latest_chapter: int | None = None
    latest_processed: int | None = None
    latest_total: int | None = None
    for event in job.events:
        if event.step not in ("motif_repair_layer2_progress", "motif_repair_layer2_scanning"):
            continue
        p = event.payload if isinstance(event.payload, dict) else {}
        status = str(p.get("status", "") or "")
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        ch = p.get("chapter_number")
        if isinstance(ch, int) and ch > 0:
            latest_chapter = ch
        prog = p.get("processed")
        tot = p.get("total")
        if isinstance(prog, int):
            latest_processed = prog
        if isinstance(tot, int):
            latest_total = tot

    parts: list[str] = []
    for key, label in _MOTIF_REPAIR_STATUS_LABELS.items():
        count = status_counts.get(key, 0)
        if count:
            parts.append(f"{label} {count}")
    if not parts:
        return ""

    summary = " · ".join(parts)
    if isinstance(latest_processed, int) and isinstance(latest_total, int) and latest_total > 0:
        summary += f"  ·  {latest_processed} / {latest_total}"
    if isinstance(latest_chapter, int) and latest_chapter > 0:
        summary = f"第 {latest_chapter} 章  ·  {summary}"
    return summary


def compute_job_progress(job: DesktopJobRecord) -> int:
    """Estimate progress for a desktop job record.

    Applies a **phase-scoped** high-water mark: once the progress bar reaches a
    value it will never regress to a lower one *within the same phase*.  Events
    from later phases (e.g. ``persist`` at 96%) cannot pollute the display when
    the current step is in an earlier phase (e.g. ``pre_alignment`` at 40%).
    """
    current_step = job.current_step or (job.events[-1].step if job.events else "")
    if is_non_progress_step_event(current_step) or current_step in {"cancelled", "failed"}:
        current_step = _last_non_transient_step(job)

    # Repair-loop round interpolation: look up round/max_rounds from the
    # repair_attempt_guidance event (not the dimension step itself).
    repair_dynamic = _compute_repair_loop_progress(job, current_step)
    if repair_dynamic is not None:
        return repair_dynamic

    current_payload = _latest_payload_for_step(job, current_step)
    current_phase_step = _phase_step_for_event(job.kind, current_step, current_payload)
    current_phase = _get_phase_index(job.kind, current_phase_step)
    progress = _compute_step_progress(
        job.kind,
        job.status.value,
        current_step,
        current_payload,
    )

    # Phase-scoped high-water mark: only consider events from the current phase
    # or earlier phases.  This prevents a late-stage event (e.g. persist=96%)
    # from a previous run from inflating the progress during an early phase.
    progress_events = (
        _init_long_events_after_resume_reset(job) if job.kind == "init_long" else job.events
    )
    for event in progress_events:
        if is_non_progress_step_event(event.step):
            continue
        # Normalize the event step before computing phase index, so that
        # raw step names (e.g. "chapter_repair") are mapped to their
        # canonical keys (e.g. "draft") which have defined phase boundaries.
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        event_phase_step = _phase_step_for_event(job.kind, event.step, event_payload)
        event_phase = _get_phase_index(job.kind, event_phase_step)
        if event_phase >= 0 and current_phase >= 0 and event_phase > current_phase:
            continue
        p = _compute_step_progress(job.kind, "running", event.step, event_payload)
        if p > progress:
            progress = p
    if job.kind == "init_long":
        anchor_progress = _init_long_resume_anchor_progress(job)
        if anchor_progress is not None:
            progress = max(progress, anchor_progress)
    return progress


def compute_task_flow_progress(job: DesktopJobRecord) -> int:
    """Return the progress percentage aligned with visible task-flow steps.

    The status bar and task card deliberately share the *same* visual-progress
    implementation.  This avoids a second approximation drifting from the
    card's resume floors, checkpoint decisions, and init-long special stages.
    The import is local because the workflow card module already depends on
    this progress module for its lower-level helpers.
    """

    from novel_forge.desktop.pages.workflow.jobs import _compute_card_progress

    return _compute_card_progress(job)


def display_step_name_for_job(job: DesktopJobRecord) -> str:
    """Return an enriched step label: outline batch steps include x/y chapter progress."""
    step = job.current_step or ""
    if is_non_progress_step_event(step):
        step = _last_non_transient_step(job)
    if job.kind == "init_long" and step == _INIT_RESUME_ANCHOR_STEP:
        payload = _latest_payload_for_step(job, step)
        anchor_label = str(payload.get("label") or "").strip()
        anchor_step = _init_resume_anchor_payload_step(payload)
        if not anchor_label and anchor_step:
            anchor_label = display_step_name(anchor_step).removesuffix("生成")
        return f"断点恢复锚点：{anchor_label or '已落盘产物'}"
    if job.kind == "resolve_chapter_checkpoint" and step == "resume_from_progress":
        payload = _latest_payload_for_step(job, step)
        completed_stage = str(payload.get("completed_stage") or "").strip()
        completed_label = _RESOLVE_RESUME_STAGE_LABELS.get(completed_stage, "")
        next_step = resolve_checkpoint_resume_step(payload)
        next_label = _summary_label_for_kind(job.kind, next_step)
        if completed_label and next_label:
            return f"断点恢复 · 已完成{completed_label}，继续{next_label}"
    base = display_step_name(step)
    resolved = resolve_step_key(job.kind, step)
    summary_label = _summary_label_for_kind(job.kind, resolved)
    if summary_label and _looks_like_raw_step_label(base):
        base = summary_label
    if (
        summary_label
        and summary_label != base
        and summary_label not in base
        and not base.startswith(f"{summary_label} · ")
    ):
        base = f"{summary_label} · {base}"
    if job.kind == "init_long" and _is_init_long_blueprint_block_step(step):
        payload = _latest_payload_for_step(job, step)
        return _format_init_long_blueprint_block_step(base, step, payload)
    if job.kind == "init_long" and step == "extract_init_coherence_claims":
        payload = _latest_payload_for_step(job, step)
        return _format_init_long_claims_step(base, payload)
    if job.kind == "init_long" and step == "retrieve_init_conflict_candidates":
        payload = _latest_payload_for_step(job, step)
        return _format_init_long_candidate_retrieval_step(base, payload)
    if job.kind == "init_long" and step.startswith("adjudicate_init_conflict_candidates"):
        payload = _latest_payload_for_step(job, step)
        return _format_init_long_candidate_adjudication_step(base, payload)
    if _is_init_long_outline_step(step):
        payload = _latest_payload_for_step(job, step)
        return format_init_long_outline_step_label(base, payload)
    if job.kind == "reextract_relationships" and step == "reextract_chapter":
        payload = _latest_payload_for_step(job, step)
        progress = payload.get("progress")
        total = payload.get("total")
        chapter = payload.get("chapter")
        if (
            isinstance(total, int)
            and total > 0
            and isinstance(progress, int)
            and isinstance(chapter, int)
        ):
            return f"{base}  ·  第 {chapter} 章  ·  {progress} / {total}"
    if job.kind == "book_consistency" and step == "book_consistency_chunk_progress":
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total")
        current = payload.get("current")
        if isinstance(total, int) and total > 0 and isinstance(current, int):
            return f"{base}  ·  块 {current} / {total}"
    if job.kind == "book_consistency" and step == "book_consistency_verify_progress":
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total")
        processed = payload.get("processed", payload.get("current"))
        chapter = payload.get("chapter_number")
        if isinstance(total, int) and total > 0 and isinstance(processed, int):
            suffix = f"{processed} / {total}"
            if isinstance(chapter, int) and chapter > 0:
                return f"{base}  ·  第 {chapter} 章  ·  {suffix}"
            return f"{base}  ·  {suffix}"
    if job.kind == "book_consistency" and step == "book_consistency_verify_done":
        payload = _latest_payload_for_step(job, step)
        remaining = _positive_int(payload, "remaining")
        rejected = _positive_int(payload, "rejected")
        failed = _positive_int(payload, "verification_failed")
        parts = []
        if remaining:
            parts.append(f"保留 {remaining} 条")
        if rejected:
            parts.append(f"过滤 {rejected} 条")
        if failed:
            parts.append(f"验证失败 {failed} 条")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_start":
        payload = _latest_payload_for_step(job, step)
        count = payload.get("count")
        repair_mode = str(payload.get("repair_mode", "") or "").strip()
        parts = []
        if isinstance(count, int) and count > 0:
            parts.append(f"{count} 章")
        if repair_mode:
            mode_label = "定向修复" if repair_mode == "targeted" else "仅审计"
            parts.append(mode_label)
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_issue_pool_ready":
        payload = _latest_payload_for_step(job, step)
        pool_size = payload.get("issue_pool_size")
        chapters = payload.get("chapters")
        enabled = payload.get("enabled")
        parts = []
        if isinstance(pool_size, int):
            parts.append(f"问题池 {pool_size} 条")
        if isinstance(chapters, int) and chapters > 0:
            parts.append(f"覆盖 {chapters} 章")
        if enabled is False:
            parts.append("未启用问题池")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_two_phase_start":
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total_chapters")
        max_targets = payload.get("max_target_chapters")
        parts = []
        if isinstance(total, int) and total > 0:
            parts.append(f"扫描 {total} 章")
        if isinstance(max_targets, int) and max_targets > 0:
            parts.append(f"最多深审 {max_targets} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step in {
        "book_consistency_two_phase_summary_done",
        "book_consistency_two_phase_targeted_capped",
        "book_consistency_two_phase_targeted_start",
    }:
        payload = _latest_payload_for_step(job, step)
        flagged = payload.get("flagged_count")
        target = payload.get("target_count")
        parts = []
        if isinstance(flagged, int):
            parts.append(f"命中 {flagged} 章")
        if isinstance(target, int):
            parts.append(f"深审 {target} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_two_phase_expanded":
        payload = _latest_payload_for_step(job, step)
        flagged = payload.get("flagged_count")
        target = payload.get("expanded_target_count")
        parts = []
        if isinstance(flagged, int):
            parts.append(f"命中 {flagged} 章")
        if isinstance(target, int):
            parts.append(f"深审 {target} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_two_phase_summary_only":
        payload = _latest_payload_for_step(job, step)
        flagged = payload.get("flagged_count")
        if isinstance(flagged, int):
            return f"{base}  ·  命中 {flagged} 章"
    if job.kind == "book_consistency" and step == "book_consistency":
        payload = _latest_payload_for_step(job, step)
        if isinstance(payload, dict) and str(payload.get("status", "") or "") == "running":
            count = payload.get("count")
            analysis_mode = str(payload.get("analysis_mode", "") or "").strip()
            batch_limit = payload.get("audit_max_chapters_per_batch")
            parts = ["模型审计中"]
            if isinstance(count, int) and count > 0:
                parts.append(f"{count} 章")
            if analysis_mode:
                parts.append(_book_analysis_mode_label(analysis_mode))
            if isinstance(batch_limit, int) and batch_limit > 0:
                parts.append(f"每批≤{batch_limit}章")
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_verify_start":
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total")
        if isinstance(total, int):
            return f"{base}  ·  待验证 {total} 章"
    if job.kind == "book_consistency" and step == "book_consistency_repair_start":
        payload = _latest_payload_for_step(job, step)
        targeted = payload.get("targeted")
        concurrency = payload.get("repair_concurrency")
        parts = []
        if isinstance(targeted, int):
            parts.append(f"目标 {targeted} 章")
        if isinstance(concurrency, int) and concurrency > 1:
            parts.append(f"{concurrency}章并行")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_repair_classification":
        payload = _latest_payload_for_step(job, step)
        concurrent = _positive_int(payload, "concurrent_count")
        serial = _positive_int(payload, "serial_count")
        total = _positive_int(payload, "total")
        parts = []
        if concurrent:
            parts.append(f"文本修复 {concurrent} 章")
        if serial:
            parts.append(f"需顺序修复 {serial} 章")
        if total:
            parts.append(f"共 {total} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_repair_progress":
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total", payload.get("targeted"))
        processed = payload.get("processed", payload.get("current", payload.get("applied")))
        chapter = payload.get("chapter_number", payload.get("chapter"))
        issue_focus = str(payload.get("issue_focus", "") or "").strip()
        order = payload.get("order")  # 1-based queue position (present in "matching" events)
        panel_exp = payload.get("panel_expanded")
        status = str(payload.get("status", "") or "")
        if isinstance(total, int) and total > 0:
            # While matching/starting a chapter use queue position so "0/5" never appears
            if status == "matching" and isinstance(order, int) and order > 0:
                numerator = order
            elif isinstance(processed, int):
                numerator = processed
            else:
                numerator = None
            if numerator is not None:
                suffix = f"{numerator} / {total}"
                status_label = _BOOK_REPAIR_STATUS_LABELS.get(status)
                if status_label:
                    suffix += f"  ·  {status_label}"
                if isinstance(panel_exp, int) and panel_exp > 0:
                    suffix += f"  面板扩展+{panel_exp}"
                if issue_focus:
                    suffix += f"  ·  {issue_focus}"
                if isinstance(chapter, int) and chapter > 0:
                    return f"{base}  ·  第 {chapter} 章  ·  {suffix}"
                return f"{base}  ·  {suffix}"
        elif status == "done" and isinstance(total, int) and total == 0:
            return f"{base}  ·  无需修复"
    if job.kind == "book_consistency" and step == "book_consistency_auto_continue":
        payload = _latest_payload_for_step(job, step)
        batch = payload.get("batch")
        remaining_count = payload.get("remaining")
        parts = []
        if isinstance(batch, int) and batch > 0:
            parts.append(f"第 {batch} 批")
        if isinstance(remaining_count, int) and remaining_count >= 0:
            parts.append(f"剩余 {remaining_count} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "book_consistency" and step == "book_consistency_stuck":
        payload = _latest_payload_for_step(job, step)
        batch = payload.get("batch")
        if isinstance(batch, int) and batch > 0:
            return f"{base}  ·  第 {batch} 批没有新增修复"
    if job.kind == "book_consistency" and step == "book_consistency_auto_continue_done":
        payload = _latest_payload_for_step(job, step)
        batches = payload.get("total_batches")
        remaining_after = payload.get("remaining")
        parts = []
        if isinstance(batches, int) and batches > 0:
            parts.append(f"共 {batches} 批")
        if isinstance(remaining_after, int) and remaining_after >= 0:
            parts.append("全部完成" if remaining_after == 0 else f"剩余 {remaining_after} 章")
        if parts:
            return f"{base}  ·  {' · '.join(parts)}"
    if job.kind == "repair_motif_history" and step == "motif_repair_layer2_start":
        payload = _latest_payload_for_step(job, step)
        concurrency = payload.get("concurrency")
        start_ch = payload.get("start_chapter")
        end_ch = payload.get("end_chapter")
        hints: list[str] = []
        if isinstance(concurrency, int) and concurrency > 1:
            hints.append(f"{concurrency}章并行")
        if isinstance(start_ch, int) and isinstance(end_ch, int):
            hints.append(f"第 {start_ch}-{end_ch} 章")
        if hints:
            return f"{base}  ·  {' · '.join(hints)}"
    if job.kind == "repair_motif_history" and step == "motif_repair_layer2_scanning":
        summary = _motif_layer2_running_summary(job)
        if summary:
            return f"{base}  ·  {summary}"
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total")
        processed = payload.get("processed")
        if isinstance(total, int) and total > 0 and isinstance(processed, int):
            return f"{base}  ·  {processed} / {total}"
    if job.kind == "repair_motif_history" and step == "motif_repair_layer2_progress":
        summary = _motif_layer2_running_summary(job)
        if summary:
            return f"{base}  ·  {summary}"
        payload = _latest_payload_for_step(job, step)
        total = payload.get("total")
        processed = payload.get("processed")
        chapter = payload.get("chapter_number")
        if isinstance(total, int) and total > 0 and isinstance(processed, int):
            suffix = f"{processed} / {total}"
            if isinstance(chapter, int) and chapter > 0:
                return f"{base}  ·  第 {chapter} 章  ·  {suffix}"
            return f"{base}  ·  {suffix}"
    if job.kind == "repair_motif_history" and step == "motif_repair_layer1_done":
        payload = _latest_payload_for_step(job, step)
        touched = _positive_int(payload, "motifs_touched")
        seen = _positive_int(payload, "occurrences_seen")
        created = _positive_int(payload, "created_motifs")
        parts = []
        if touched:
            parts.append(f"母题 {touched} 个")
        if seen:
            parts.append(f"片段 {seen} 条")
        if created:
            parts.append(f"新增 {created} 个")
        if parts:
            return f"{base}  ·  " + "，".join(parts)
    if job.kind == "repair_motif_history" and step == "motif_repair_layer2_done":
        payload = _latest_payload_for_step(job, step)
        summary = _motif_layer2_done_summary(payload)
        if summary:
            return f"{base}  ·  {summary}"
    if step == "draft":
        payload = _latest_payload_for_step(job, step)
        word_count = payload.get("word_count")
        if isinstance(word_count, (int, float)) and word_count > 0:
            return f"{base}  ·  {int(word_count)} 字"
    if step.startswith("edit_"):
        try:
            int(step.split("_", 1)[1])
        except (ValueError, IndexError):
            pass
        else:
            payload = _latest_payload_for_step(job, step)
            iteration = payload.get("iteration")
            if isinstance(iteration, (int, float)) and iteration > 0:
                return f"{base}  ·  第 {int(iteration)} 轮"
    if step in ("continuity_repair", "causal_repair", "reading_power_repair"):
        payload = _latest_payload_for_step(job, "repair_attempt_guidance")
        round_num = payload.get("round")
        max_rounds = payload.get("max_rounds")
        if (
            isinstance(round_num, (int, float))
            and isinstance(max_rounds, (int, float))
            and int(max_rounds) > 0
        ):
            return f"{base}  ·  第 {int(round_num)}/{int(max_rounds)} 轮"
    return base


def memory_stage_status_for_job(job: DesktopJobRecord) -> dict[str, str] | None:
    """Return workflow-friendly memory stage statuses for chapter jobs.

    Keys: ``indexing`` / ``summary`` / ``motif``; values are one of
    ``pending`` | ``running`` | ``done`` | ``failed``.
    """
    if job.kind not in _MEMORY_AWARE_KINDS:
        return None

    stage_state: dict[str, str] = {
        "indexing": "pending",
        "summary": "pending",
        "motif": "pending",
    }
    seen_memory_event = False
    recognized_steps = {
        "memory_indexing_started",
        "memory_indexing_complete",
        "memory_episodic_done",
        "memory_summary_scheduled",
        "memory_summary_generated",
        "memory_motifs_extracted",
        "memory_motifs_scheduled",
        "memory_motifs_completed",
        "memory_concurrent_tasks_started",
        "memory_concurrent_tasks_done",
        "memory_updated",
        # P0-1: 记忆补偿和失败事件
        "memory_gap_compensated",
        "memory_update_failed",
    }

    for event in job.events:
        step = str(getattr(event, "step", "") or "")
        payload = event.payload if isinstance(event.payload, dict) else {}
        if step not in recognized_steps:
            continue
        seen_memory_event = True

        success = bool(payload.get("success", True))
        if step == "memory_indexing_started":
            if stage_state["indexing"] == "pending":
                stage_state["indexing"] = "running"
            continue
        if step == "memory_concurrent_tasks_started":
            tasks_payload = payload.get("tasks")
            task_names = (
                {str(item) for item in tasks_payload} if isinstance(tasks_payload, list) else set()
            )
            if "episodic" in task_names and stage_state["indexing"] == "pending":
                stage_state["indexing"] = "running"
            if "summary" in task_names and stage_state["summary"] == "pending":
                stage_state["summary"] = "running"
            if "motifs" in task_names and stage_state["motif"] == "pending":
                stage_state["motif"] = "running"
            continue
        if step == "memory_concurrent_tasks_done":
            tasks_ok_payload = payload.get("tasks_ok")
            tasks_failed_payload = payload.get("tasks_failed")
            ok = (
                {str(item) for item in tasks_ok_payload}
                if isinstance(tasks_ok_payload, list)
                else set()
            )
            failed = (
                {str(item) for item in tasks_failed_payload}
                if isinstance(tasks_failed_payload, list)
                else set()
            )
            if "episodic" in ok:
                stage_state["indexing"] = "done"
            elif "episodic" in failed:
                stage_state["indexing"] = "failed"
            if "summary" in ok:
                stage_state["summary"] = "done"
            elif "summary" in failed:
                stage_state["summary"] = "failed"
            if "motifs" in ok:
                stage_state["motif"] = "done"
            elif "motifs" in failed:
                stage_state["motif"] = "failed"
            continue
        if step == "memory_indexing_complete":
            stage_state["indexing"] = "done" if success else "failed"
            continue
        if step == "memory_episodic_done":
            if not success:
                stage_state["indexing"] = "failed"
            elif stage_state["indexing"] == "pending":
                stage_state["indexing"] = "running"
            continue
        if step == "memory_summary_scheduled":
            if stage_state["summary"] == "pending":
                stage_state["summary"] = "running" if success else "failed"
            continue
        if step == "memory_summary_generated":
            stage_state["summary"] = "done" if success else "failed"
            continue
        if step in {"memory_motifs_extracted", "memory_motifs_scheduled"}:
            if stage_state["motif"] == "pending":
                stage_state["motif"] = "running" if success else "failed"
            continue
        if step == "memory_motifs_completed":
            stage_state["motif"] = "done" if success else "failed"
            continue
        if step == "memory_updated":
            if stage_state["indexing"] == "pending":
                stage_state["indexing"] = "done"

            _ms = payload.get("memory_module_status")
            module_status = _ms if isinstance(_ms, dict) else {}
            chapter = int(payload.get("chapter", 0) or 0)
            last_indexed = int(payload.get("last_indexed_chapter", 0) or 0)
            reached_chapter = chapter > 0 and last_indexed >= chapter

            if (
                stage_state["summary"] == "pending"
                and module_status.get("summary_enabled", True)
                and reached_chapter
            ):
                stage_state["summary"] = "done"

            if (
                stage_state["motif"] == "pending"
                and module_status.get("motif_enabled", True)
                and reached_chapter
            ):
                stage_state["motif"] = "done"

        # P0-1: 处理记忆补偿成功事件
        if step == "memory_gap_compensated":
            chapter = int(payload.get("chapter", 0) or 0)
            stats = payload.get("stats", {}) if isinstance(payload.get("stats"), dict) else {}
            if chapter > 0 and stats.get("saved"):
                stage_state["indexing"] = "done"
            continue

        # P0-1: 处理记忆更新失败事件
        if step == "memory_update_failed":
            chapter = int(payload.get("chapter", 0) or 0)
            if chapter > 0:
                stage_state["indexing"] = "failed"
            continue

    if not seen_memory_event:
        return None
    return stage_state


__all__ = [
    "compute_job_progress",
    "compute_task_flow_progress",
    "display_step_name",
    "display_step_name_for_job",
    "memory_stage_status_for_job",
    "resolve_checkpoint_resume_step",
    "resolve_step_key",
    "ui_steps_for_kind",
]
