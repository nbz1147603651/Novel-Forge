"""Workspace adapter for opt-in, validated future planning after chapter acceptance."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from novel_forge.core.infra.resource_locks import ResourceName
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.user_intent import build_persisted_user_intent_card
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.pipeline.long.services.future_planning import (
    FutureOutlineReviewStep,
    exploration_has_budget,
    explore_future_candidate,
    planning_boundary,
    protected_chapters,
    validate_candidate_boundaries,
    validate_protected_contracts,
)
from novel_forge.story_kernel.store import StoryKernelStore
from novel_forge.workspace.execution_outline_polish import prepare_outline_revision
from novel_forge.workspace.helpers.execution_runners import _project_lock


def _load(storage: Any, path: Any) -> dict[str, Any]:
    return storage.load_json(path) if path.exists() else {}


async def _accepted_kernel(layout: ProjectLayout, project_id: str) -> dict[str, Any]:
    if not layout.story_kernel_db_path.exists():
        raise ValueError("Accepted kernel is required for future exploration")
    store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        kernel = await store.load_kernel(project_id)
        # Archived facts still bind future planning. If the complete authority
        # exceeds the model's context/budget, exploration fails safely; do not
        # silently remove old facts to make a candidate appear consistent.
        return kernel.model_dump(mode="json")
    finally:
        await store.close()


async def execute_future_planning(
    runtime: Any,
    *,
    project_id: str,
    completed_chapter: int,
    on_step_progress: Any = None,
) -> dict[str, Any]:
    storage = runtime.storage
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    try:
        policy = _load(storage, layout.plans_dir / "planning_policy.json")
    except (ValueError, OSError) as exc:
        return {"status": "invalid_policy", "reason": str(exc)}
    mode = policy.get("mode", getattr(runtime.settings, "future_planning_mode", "fixed"))
    if mode == "fixed":
        return {"status": "fixed"}
    if mode not in {"proposal", "adaptive"}:
        return {"status": "invalid_policy"}
    report_path = layout.reports_dir / f"chapter_{completed_chapter:03d}_future_planning.json"
    report: dict[str, Any] = {"status": "kept_original", "mode": mode}
    try:
        async with _project_lock(runtime, project_id, ResourceName.CANON):
            outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
            trigger = _load(storage, layout.reports_dir / "future_planning_trigger.json")
            if not planning_boundary(
                outline, completed_chapter, _load(storage, layout.blueprint_path)
            ) and (trigger.get("chapter_number") != completed_chapter):
                report["status"] = "not_at_boundary"
                return report
            if not exploration_has_budget(runtime.settings, runtime.router):
                report["status"] = "budget_skipped"
                return report
            protected = protected_chapters(layout.root, policy)
            if completed_chapter not in protected:
                report["status"] = "chapter_not_archived"
                return report
            kernel = await _accepted_kernel(layout, project_id)
            if int(kernel.get("current_chapter", 0)) != completed_chapter:
                report["status"] = "kernel_not_at_boundary"
                return report
            kernel_hash = hashlib.sha256(
                json.dumps(kernel, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            previous = _load(storage, report_path)
            if (
                previous.get("source_kernel_hash") == kernel_hash
                and previous.get("mode") == mode
                and previous.get("status")
                in {"published", "proposal", "kept_original", "requires_confirmation"}
            ):
                report = previous
                return report
            report["source_kernel_hash"] = kernel_hash
            revision = PlanningRevision(layout.root, project_id)
            accepted = {
                "kernel": kernel,
                "exit_state": _load(storage, layout.chapter_exit_state_path(completed_chapter)),
                "user_intent": build_persisted_user_intent_card(
                    _load(storage, layout.init_request_meta_path), _load(storage, layout.spec_path)
                ),
                "narrative_contract": _load(storage, layout.narrative_contract_path),
                "locked_chapters": sorted(protected),
            }
            old_contracts = _load(storage, layout.plans_dir / "chapter_contracts.json")
        candidate = await explore_future_candidate(
            router=runtime.router,
            builder=runtime.builder,
            settings=runtime.settings,
            outline=outline,
            chapter_number=completed_chapter,
            accepted_context=accepted,
            protected=protected,
            on_step=on_step_progress,
        )
        if candidate is None:
            return report
        report["revision_id"] = revision.revision_id
        report["candidate_path"] = str(revision.project)
        revision.storage.save_json(
            revision.project / "outline.json", candidate.model_dump(mode="json")
        )
        width = int(getattr(runtime.settings, "future_planning_window", 4))
        try:
            changed = validate_candidate_boundaries(
                outline,
                candidate,
                protected=protected,
                allowed=set(range(completed_chapter + 1, completed_chapter + width + 1)),
            )
        except ValueError as exc:
            report.update({"status": "requires_confirmation", "reason": str(exc)})
            return report
        if not changed:
            return report
        if not exploration_has_budget(runtime.settings, runtime.router):
            report["status"] = "budget_skipped"
            return report
        await prepare_outline_revision(
            runtime,
            project_id=project_id,
            revision=revision,
            changed_chapters=changed,
            previous_hash=hashlib.sha256(outline.model_dump_json().encode()).hexdigest(),
            on_step_progress=on_step_progress,
        )
        new_contracts = revision.storage.load_json(
            revision.project / "plans" / "chapter_contracts.json"
        )
        try:
            validate_protected_contracts(old_contracts, new_contracts, protected)
        except ValueError as exc:
            report.update({"status": "requires_confirmation", "reason": str(exc)})
            return report
        if not exploration_has_budget(runtime.settings, runtime.router):
            report["status"] = "budget_skipped"
            return report
        review = FutureOutlineReviewStep(
            runtime.router, runtime.builder, settings=runtime.settings, on_step=on_step_progress
        )
        decision = await review.run(
            {
                "original": outline.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
                "accepted_context": accepted,
                "candidate_contracts": new_contracts,
            }
        )
        report.update({"decision": decision.model_dump(mode="json"), "changed_chapters": changed})
        # Keep the review beside the immutable candidate, including rejected paths.
        revision.storage.save_json(revision.directory / "decision.json", report)
        if not decision.permits_publication:
            return report
        if mode == "proposal":
            report.update({"status": "proposal", "candidate_path": str(revision.project)})
            return report
        async with _project_lock(runtime, project_id, ResourceName.CANON):
            if kernel != await _accepted_kernel(layout, project_id):
                raise ValueError("Accepted facts changed while evaluating candidate")
            if protected != protected_chapters(layout.root, policy):
                raise ValueError("Archived chapters changed while evaluating candidate")
            revision.publish()
        report["status"] = "published"
        return report
    except Exception as exc:
        report.update({"status": "kept_original", "reason": str(exc)})
        return report
    finally:
        # This report is not part of the candidate CAS and is written after it.
        try:
            storage.save_json(report_path, report)
            if on_step_progress is not None:
                on_step_progress("future_planning", report)
        except Exception:
            logging.getLogger(__name__).exception("future_planning_report_failed")
