"""Targeted review rechecks used by workspace repair commands."""

from __future__ import annotations

from typing import Any, Literal, cast

from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.stages.causal_repair import recheck_alignment
from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
from novel_forge.pipeline.steps.causal_validation_step import (
    CausalValidationInput,
    CausalValidationStep,
)
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
)
from novel_forge.workspace.execution_result import StepCallback


def _service_attr(source: Any, public_name: str, private_name: str) -> Any:
    if hasattr(source, public_name):
        return getattr(source, public_name)
    return getattr(source, private_name)


def _service_step_callback(source: Any) -> Any | None:
    return getattr(source, "on_step", None) or getattr(source, "_on_step", None)


def _issue_payload(issue: Any) -> dict[str, Any]:
    if hasattr(issue, "model_dump"):
        return issue.model_dump(mode="json")
    if isinstance(issue, dict):
        return issue
    return {}


def _report_payload(report: Any) -> dict[str, Any]:
    return report.model_dump(mode="json") if hasattr(report, "model_dump") else dict(report)


def _issue_text(issue: Any, field_name: str) -> str:
    if isinstance(issue, dict):
        return str(issue.get(field_name, "") or "")
    return str(getattr(issue, field_name, "") or "")


def _must_resolve_summaries(issues: list[Any]) -> list[str]:
    return [summary for issue in issues if (summary := _issue_text(issue, "summary").strip())]


def _recheck_payload(report: Any, *, issues: list[Any], current_text: str) -> dict[str, Any]:
    payload = _report_payload(report)
    payload["issues"] = [_issue_payload(issue) for issue in issues]
    payload["source_text_hash"] = source_text_hash(current_text)
    return payload


def build_continuity_recheck_payload(
    report: Any,
    *,
    issues: list[Any],
    current_text: str,
) -> dict[str, Any]:
    """Return persisted continuity report payload with current text freshness."""

    return _recheck_payload(report, issues=issues, current_text=current_text)


def build_causal_recheck_payload(
    report: Any,
    *,
    issues: list[Any],
    current_text: str,
) -> dict[str, Any]:
    """Return persisted causal report payload with current text freshness."""

    return _recheck_payload(report, issues=issues, current_text=current_text)


async def run_alignment_recheck_and_save(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
) -> Any:
    """Run the pipeline alignment recheck helper that also persists its report."""

    return await recheck_alignment(
        runner,
        bundle,
        packet,
        plan,
        current_text,
        chapter_number,
        trace,
    )


async def run_alignment_check(
    *,
    services: Any,
    chapter_outline: Any,
    chapter_plan: Any,
    current_text: str,
    trace: Any,
) -> Any:
    """Run an alignment check without persisting the candidate result."""

    step = AlignmentStep(
        _service_attr(services, "router", "_router"),
        _service_attr(services, "builder", "_builder"),
        settings=_service_attr(services, "settings", "_settings"),
        trace=trace,
    )
    return await step.run(
        AlignmentInput(
            chapter_outline=chapter_outline,
            chapter_plan=chapter_plan,
            chapter_text=current_text,
        )
    )


async def run_continuity_recheck(
    *,
    services: Any,
    chapter_number: int,
    current_text: str,
    packet: Any,
    bridge: Any,
    plan: Any,
    trace: Any,
    prior_issues: list[dict[str, Any]] | None = None,
    must_resolve_summaries: list[str] | None = None,
    pov_switch: bool = False,
    recheck_mode: bool = False,
    patch_only_repair: bool = False,
    repaired_issue_types: list[str] | None = None,
    project_path: Any | None = None,
    chapter_source_slice: Any | None = None,
    start_step_name: str | None = None,
    on_step_progress: StepCallback = None,
) -> Any:
    """Run a continuity check/recheck without persisting the candidate result."""

    if start_step_name and on_step_progress:
        on_step_progress(start_step_name, {"chapter_number": chapter_number})
    step = ContinuityEvalStep(
        _service_attr(services, "router", "_router"),
        _service_attr(services, "builder", "_builder"),
        settings=_service_attr(services, "settings", "_settings"),
        trace=trace,
    )
    return await step.run(
        ContinuityEvalInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_state_packet=packet,
            chapter_bridge=bridge,
            chapter_plan=plan,
            prior_issues=list(prior_issues or []),
            must_resolve_summaries=list(must_resolve_summaries or []),
            pov_switch=pov_switch,
            recheck_mode=recheck_mode,
            patch_only_repair=patch_only_repair,
            repaired_issue_types=list(repaired_issue_types or []),
            project_path=project_path,
            chapter_source_slice=chapter_source_slice,
        )
    )


async def run_causal_recheck(
    *,
    services: Any,
    chapter_number: int,
    current_text: str,
    bridge: Any,
    causal_link: dict[str, Any],
    trace: Any,
    must_resolve_summaries: list[str] | None = None,
    character_notes: str = "",
    previous_chapter_ending: str = "",
    recheck_strategy: Literal["strict_targeted", "targeted_with_global_guard"] = (
        "targeted_with_global_guard"
    ),
    prior_issues: list[dict[str, Any]] | None = None,
    repaired_issue_types: list[str] | None = None,
    patch_only_repair: bool = False,
    strict_review: bool = False,
    start_step_name: str | None = None,
    on_step_progress: StepCallback = None,
) -> Any:
    """Run a causal validation recheck without persisting the candidate result."""

    if start_step_name and on_step_progress:
        on_step_progress(start_step_name, {"chapter_number": chapter_number})
    step = CausalValidationStep(
        _service_attr(services, "router", "_router"),
        _service_attr(services, "builder", "_builder"),
        settings=_service_attr(services, "settings", "_settings"),
        trace=trace,
        on_step=_service_step_callback(services),
    )
    return await step.run(
        CausalValidationInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            chapter_bridge=bridge,
            causal_link=causal_link,
            must_resolve_summaries=list(must_resolve_summaries or []),
            character_notes=character_notes,
            previous_chapter_ending=previous_chapter_ending,
            recheck_mode=True,
            recheck_strategy=recheck_strategy,
            prior_issues=list(prior_issues or []),
            repaired_issue_types=list(repaired_issue_types or []),
            patch_only_repair=patch_only_repair,
            strict_review=strict_review,
        )
    )


async def run_targeted_continuity_recheck(
    *,
    runtime: Any,
    layout: Any,
    chapter_number: int,
    current_text: str,
    packet: Any,
    bridge: Any,
    plan: Any,
    prior_issues: list[dict[str, Any]],
    must_fix_issues: list[Any],
    repair_result: Any,
    chapter_source_slice: Any | None = None,
    start_step_name: str | None = "continuity_eval_after_repair_start",
    on_step_progress: StepCallback = None,
) -> Any:
    """Run the continuity verifier in targeted repair recheck mode."""

    return await run_continuity_recheck(
        services=runtime,
        chapter_number=chapter_number,
        current_text=current_text,
        packet=packet,
        bridge=bridge,
        plan=plan,
        trace=PipelineTrace(),
        prior_issues=[dict(item) for item in prior_issues],
        must_resolve_summaries=_must_resolve_summaries(must_fix_issues),
        recheck_mode=True,
        patch_only_repair=getattr(repair_result, "patch_only", False),
        repaired_issue_types=getattr(repair_result, "repaired_issue_types", []),
        project_path=layout.root,
        chapter_source_slice=chapter_source_slice,
        start_step_name=start_step_name,
        on_step_progress=on_step_progress,
    )


async def run_targeted_causal_recheck(
    *,
    runtime: Any,
    cache: Any,
    chapter_number: int,
    current_text: str,
    bridge: Any,
    causal_link: dict[str, Any],
    must_fix_issues: list[Any],
    repair_result: Any,
    boundary_prev_tail_paragraphs: int,
    on_step_progress: StepCallback = None,
) -> Any:
    """Run the causal verifier in targeted repair recheck mode."""

    prior_issues = [
        {
            "issue_type": _issue_text(issue, "issue_type").lower(),
            "severity": (_issue_text(issue, "severity") or "medium").lower(),
            "location": _issue_text(issue, "location"),
            "summary": _issue_text(issue, "summary"),
        }
        for issue in must_fix_issues
    ]
    repaired_types = sorted(
        {
            str(item).strip().lower()
            for item in (
                getattr(repair_result, "repaired_issue_types", None)
                or [
                    _issue_text(issue, "issue_type").lower()
                    for issue in must_fix_issues
                    if _issue_text(issue, "issue_type").strip()
                ]
            )
            if str(item).strip()
        }
    )
    patch_only_types = set(CausalRepairStep.patch_only_issue_types())
    recheck_strategy = cast(
        Literal["strict_targeted", "targeted_with_global_guard"],
        getattr(runtime.settings, "recheck_strategy", "targeted_with_global_guard"),
    )
    return await run_causal_recheck(
        services=runtime,
        chapter_number=chapter_number,
        current_text=current_text,
        bridge=bridge,
        causal_link=causal_link,
        trace=PipelineTrace(),
        must_resolve_summaries=_must_resolve_summaries(must_fix_issues),
        character_notes=cache.get_character_notes(),
        previous_chapter_ending=cache.get_previous_chapter_ending(
            chapter_number,
            paragraphs=boundary_prev_tail_paragraphs,
        ),
        recheck_strategy=recheck_strategy,
        prior_issues=prior_issues,
        repaired_issue_types=repaired_types,
        patch_only_repair=bool(repaired_types)
        and all(issue_type in patch_only_types for issue_type in repaired_types),
        strict_review=True,
        start_step_name="causal_eval_after_repair_start",
        on_step_progress=on_step_progress,
    )


__all__ = [
    "build_causal_recheck_payload",
    "build_continuity_recheck_payload",
    "run_alignment_check",
    "run_alignment_recheck_and_save",
    "run_causal_recheck",
    "run_continuity_recheck",
    "run_targeted_causal_recheck",
    "run_targeted_continuity_recheck",
]
