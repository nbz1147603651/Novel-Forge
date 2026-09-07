"""Workspace entrypoint for whole-book editorial audits."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.book_audit_runner import BookAuditChapter
from novel_forge.pipeline.steps.book_editorial_audit_step import (
    BookEditorialAuditInput,
    BookEditorialAuditStep,
)
from novel_forge.workspace.contracts import BookEditorialAuditRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.runtime import RuntimeServices


async def execute_book_editorial_audit(
    runtime: RuntimeServices,
    request: BookEditorialAuditRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """Run a whole-book publication-level editorial audit."""

    from novel_forge.workspace.authoring_control import authoring_review

    with authoring_review(runtime, request):
        return await _execute_book_editorial_audit(
            runtime, request, on_step_progress=on_step_progress
        )


async def _execute_book_editorial_audit(
    runtime: RuntimeServices,
    request: BookEditorialAuditRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:

    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    async with _project_lock(runtime, request.project_id):
        contract = EditorialContract.model_validate(
            runtime.storage.load_json(layout.editorial_contract_path)
        )
        chapters = _load_audit_chapters(runtime, layout, request.chapter_range)
        if not chapters:
            raise ValueError("没有可审计的已完成章节。")
        if on_step_progress:
            on_step_progress(
                "book_editorial_audit_start",
                {"chapters": [chapter.chapter_number for chapter in chapters]},
            )
        step = BookEditorialAuditStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=PipelineTrace(),
        )
        report = await step.run(
            BookEditorialAuditInput(
                chapters=chapters,
                editorial_contract=contract,
                prompt_hint=request.prompt_hint,
                batch_size=request.batch_size,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                checkpoint_path=layout.states_dir / "book_editorial_audit_checkpoint.json",
                batch_timeout_s=request.batch_timeout_s,
            )
        )
        payload = report.model_dump(mode="json")
        payload["report_type"] = "book_editorial_audit"
        payload["audit_domain"] = "editorial"
        payload["read_only"] = True
        payload["chapter_range"] = [chapter.chapter_number for chapter in chapters]
        chapter_hashes = {
            str(chapter.chapter_number): source_text_hash(chapter.text) for chapter in chapters
        }
        payload["input_manifest"] = {
            "workflow_version": "book.editorial.audit.v2",
            "chapter_hashes": chapter_hashes,
            "contract_hash": hashlib.sha256(
                json.dumps(
                    contract.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "prompt_hint_hash": hashlib.sha256(request.prompt_hint.encode("utf-8")).hexdigest(),
        }
        payload["revision_queue"] = _build_editorial_revision_queue(
            payload=payload,
            chapter_hashes=chapter_hashes,
        )
        report_path = layout.reports_dir / "book_editorial_audit.json"
        runtime.storage.save_json(report_path, payload)
        if on_step_progress:
            on_step_progress("book_editorial_audit", payload)
            on_step_progress("book_editorial_audit_report_written", {"path": str(report_path)})
        return ExecutionResult(project_id=request.project_id, result=payload)


def _build_editorial_revision_queue(
    *,
    payload: dict[str, Any],
    chapter_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    """Project structured editorial actions into an explicit, non-mutating queue."""

    raw_metrics = payload.get("metrics")
    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
    raw_structured = metrics.get("structured_revision_plan")
    structured = raw_structured if isinstance(raw_structured, dict) else {}
    actions = structured.get("actions")
    findings = [item for item in payload.get("findings") or [] if isinstance(item, dict)]
    queue: list[dict[str, Any]] = []
    for index, action in enumerate(actions or [], start=1):
        if not isinstance(action, dict):
            continue
        chapters = sorted(
            {
                int(chapter)
                for chapter in action.get("chapter_range") or []
                if str(chapter).isdigit() and int(chapter) > 0
            }
        )
        relevant_findings = [
            finding
            for finding in findings
            if not chapters or int(finding.get("chapter_number", 0) or 0) in chapters
        ]
        identity = {
            "index": index,
            "action_type": action.get("action_type") or "editorial_revision",
            "target": action.get("target") or "",
            "chapters": chapters,
            "instruction": action.get("instruction") or "",
            "source_text_hashes": {
                str(chapter): chapter_hashes.get(str(chapter), "legacy_unknown")
                for chapter in chapters
            },
        }
        queue.append(
            {
                "queue_item_id": "editorial_"
                + hashlib.sha256(
                    json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16],
                "audit_domain": "editorial",
                "status": "manual_review",
                "target_chapters": chapters,
                "affected_dimensions": [str(action.get("action_type") or "editorial_revision")],
                "evidence": [
                    evidence
                    for finding in relevant_findings
                    for evidence in finding.get("evidence") or []
                    if str(evidence).strip()
                ][:12],
                "impact_scope": {
                    "from_chapter": min(chapters) if chapters else None,
                    "to_chapter": max(chapters) if chapters else None,
                    "requires_preview": True,
                    "requires_state_replay": bool(chapters),
                },
                "repair_boundary": {
                    "rationale": str(action.get("rationale") or ""),
                    "instruction": str(action.get("instruction") or ""),
                    "target": str(action.get("target") or ""),
                },
                "source_text_hashes": identity["source_text_hashes"],
                "action": dict(action),
            }
        )
    return queue


def _load_audit_chapters(
    runtime: RuntimeServices,
    layout: ProjectLayout,
    requested_range: list[int],
) -> list[BookAuditChapter]:
    outline_by_number: dict[int, Any] = {}
    if layout.outline_path.exists():
        try:
            outline_payload = runtime.storage.load_json(layout.outline_path)
            for item in outline_payload.get("chapters", []) or []:
                if isinstance(item, dict):
                    outline_by_number[int(item.get("chapter_number", 0) or 0)] = item
        except Exception:
            outline_by_number = {}
    numbers = sorted(requested_range) if requested_range else _completed_chapter_numbers(layout)
    chapters: list[BookAuditChapter] = []
    for number in numbers:
        path = layout.chapter_path(number)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        outline = outline_by_number.get(number, {})
        summary, key_events = _load_chapter_summary(runtime, layout, number)
        chapters.append(
            BookAuditChapter(
                chapter_number=number,
                title=str(outline.get("title", "") or ""),
                summary=summary,
                text=text,
                key_events=key_events,
            )
        )
    return chapters


def _completed_chapter_numbers(layout: ProjectLayout) -> list[int]:
    numbers: list[int] = []
    for path in sorted(layout.chapters_dir.glob("chapter_*.md")):
        try:
            numbers.append(int(path.stem.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return numbers


def _load_chapter_summary(
    runtime: RuntimeServices,
    layout: ProjectLayout,
    chapter_number: int,
) -> tuple[str, list[str]]:
    report_path = layout.creative_report_path(chapter_number)
    if not report_path.exists():
        return "", []
    try:
        payload = runtime.storage.load_json(report_path)
    except Exception:
        return "", []
    structured = payload.get("structured_summary", "")
    if isinstance(structured, dict):
        return (
            str(structured.get("one_line_summary", "") or ""),
            [str(item) for item in structured.get("key_events", []) or []],
        )
    return (
        str(structured or ""),
        [
            str(item)
            for item in (
                payload.get("key_moments")
                or payload.get("key_revelations")
                or payload.get("must_carry_forward")
                or []
            )
        ],
    )
