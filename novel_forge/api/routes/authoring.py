"""Additive authoring API; no arbitrary file paths or executable model commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from novel_forge.api.deps import (
    _peek_cached_job_service,
    get_job_service,
    get_runtime_services,
    get_storage,
)
from novel_forge.app_service.authoring_commands import AuthoringCommands, require_authoring_rollout
from novel_forge.app_service.job_service import JobService
from novel_forge.app_service.repair_commands import RepairCommands
from novel_forge.core.authoring import (
    AuthoringMessageRequest,
    AuthoringMessageView,
    AuthoringModel,
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
    AuthoringProposalView,
    AuthoringSessionView,
)
from novel_forge.core.schemas.repair import (
    RepairApprovalRequest,
    RepairCandidateEditRequest,
    RepairCase,
    RepairCaseDecisionRequest,
    RepairCaseDetailView,
    RepairManualAnnotationRequest,
    RepairPublishRequest,
    RepairRecoveryRequest,
    RepairSourceView,
    RepairVerificationRequest,
)
from novel_forge.persistence.authoring_conversation import message_views, save_message
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter(prefix="/authoring")
StorageDep = Annotated[FileSystemStorage, Depends(get_storage)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


def _repair_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, str(exc))
    return HTTPException(409, str(exc))


def _require_rollout() -> None:
    try:
        require_authoring_rollout()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def session_view(
    root: Path, project_id: str, chapter: int = 1, service: JobService | None = None
) -> AuthoringSessionView:
    service = service or _peek_cached_job_service()
    if (
        service is not None
        and service.storage_root is not None
        and service.storage_root.resolve() != root.parent.resolve()
    ):
        service = None
    return AuthoringCommands(FileSystemStorage(root.parent), service).view(project_id, chapter)


@router.get("/{project_id}/messages", response_model=list[AuthoringMessageView])
def get_messages(project_id: str, storage: StorageDep) -> list[AuthoringMessageView]:
    return message_views(storage.existing_project_dir(project_id))


@router.post("/{project_id}/messages", response_model=AuthoringMessageView)
def send_message(
    project_id: str, body: AuthoringMessageRequest, storage: StorageDep, service: JobServiceDep
) -> AuthoringMessageView:
    _require_rollout()
    from novel_forge.app_service.contracts import JobCommand, JobKind

    root = storage.existing_project_dir(project_id)
    store = AuthoringStore(root)
    policy = store.policy()
    if policy is None:
        raise HTTPException(409, "请先明确选择共创授权")
    try:
        store.require("discuss", body.chapter_number, explicit=True)
        message_id = uuid4().hex
        view = AuthoringMessageView(
            id=message_id,
            project_id=project_id,
            message=body.message,
            context=body.context,
            chapter_number=body.chapter_number,
            input_version=story_input_version(root),
            policy_version=policy.version,
            task_id=f"chat-{message_id}",
        )
        data = {"request": body.model_dump(mode="json"), "view": view.model_dump(mode="json")}
        save_message(root, data)
        try:
            service.submit(
                JobCommand(
                    job_id=view.task_id,
                    kind=JobKind.AUTHORING_CHAT,
                    project_id=project_id,
                    payload={"project_id": project_id, "message_id": message_id},
                )
            )
        except Exception as exc:
            view.status = "failed"
            view.error = str(exc)
            data["view"] = view.model_dump(mode="json")
            save_message(root, data)
            raise
        return view
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


class PolicyChange(AuthoringModel):
    policy: AuthoringPolicy
    expected_version: int = Field(ge=0)


class SessionStart(AuthoringModel):
    expected_version: int = Field(ge=1)
    input_version: str


@router.post("/{project_id}/policy", response_model=AuthoringSessionView)
def set_policy(project_id: str, body: PolicyChange, storage: StorageDep) -> AuthoringSessionView:
    _require_rollout()
    try:
        root = storage.existing_project_dir(project_id)
        AuthoringCommands(storage, _peek_cached_job_service()).set_policy(
            project_id, body.policy, body.expected_version
        )
        return session_view(root, project_id, body.policy.start_chapter)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{project_id}/start", response_model=AuthoringSessionView)
def start_session(project_id: str, body: SessionStart, storage: StorageDep) -> AuthoringSessionView:
    _require_rollout()
    try:
        root = storage.existing_project_dir(project_id)
        AuthoringCommands(storage, _peek_cached_job_service()).start(
            project_id, body.expected_version, body.input_version
        )
        return session_view(root, project_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{project_id}/pause", response_model=AuthoringSessionView)
def pause_session(
    project_id: str, storage: StorageDep, service: JobServiceDep
) -> AuthoringSessionView:
    root = storage.existing_project_dir(project_id)
    service.pause_authoring(project_id)
    return session_view(root, project_id, service=service)


class AvailabilityChange(AuthoringModel):
    enabled: bool
    expected_version: int = Field(default=0, ge=0)
    input_version: str = ""


class SemanticRefresh(AuthoringModel):
    expected_story_version: str
    expected_policy_version: int = Field(ge=1)


@router.post("/{project_id}/availability", response_model=AuthoringSessionView)
def set_availability(
    project_id: str, body: AvailabilityChange, storage: StorageDep, service: JobServiceDep
) -> AuthoringSessionView:
    root = storage.existing_project_dir(project_id)
    try:
        if body.enabled:
            _require_rollout()
            service.enable_authoring(
                project_id, expected_version=body.expected_version, input_version=body.input_version
            )
        else:
            service.pause_authoring(project_id, disable=True)
        return session_view(root, project_id, service=service)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{project_id}/semantic-consistency/refresh")
def refresh_semantic_consistency(
    project_id: str,
    body: SemanticRefresh,
    storage: StorageDep,
    service: JobServiceDep,
) -> dict[str, Any]:
    try:
        record = AuthoringCommands(storage, service).refresh_semantic_consistency(
            project_id,
            expected_story_version=body.expected_story_version,
            expected_policy_version=body.expected_policy_version,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "accepted",
        "project_id": project_id,
        "task_id": record.job_id,
        "message": "已复用或提交语义一致性刷新任务",
    }


@router.get("/{project_id}/proposals", response_model=list[AuthoringProposalView])
def get_proposals(project_id: str, storage: StorageDep) -> list[AuthoringProposalView]:
    return ProposalStore(storage.existing_project_dir(project_id)).views()


@router.post("/{project_id}/proposals", response_model=AuthoringProposalView)
async def propose(
    project_id: str, body: AuthoringProposalRequest, runtime: RuntimeDep
) -> AuthoringProposalView:
    _require_rollout()
    try:
        return await AuthoringCommands(runtime.storage).propose(project_id, body)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{project_id}/proposals/{proposal_id}/decision", response_model=AuthoringProposalView)
def decide(
    project_id: str, proposal_id: str, body: AuthoringProposalDecision, storage: StorageDep
) -> AuthoringProposalView:
    if body.decision not in {"reject", "defer"}:
        _require_rollout()
    try:
        return AuthoringCommands(storage).decide(project_id, proposal_id, body)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{project_id}/proposals/{proposal_id}/apply", response_model=AuthoringProposalView)
async def apply(
    project_id: str, proposal_id: str, runtime: RuntimeDep, service: JobServiceDep
) -> AuthoringProposalView:
    try:
        return await AuthoringCommands(runtime.storage, service).apply(project_id, proposal_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{project_id}/repairs/source", response_model=RepairSourceView)
def get_repair_source(
    project_id: str,
    storage: StorageDep,
    chapter: Annotated[int, Query(ge=1)],
) -> RepairSourceView:
    """Return the server-selected chapter source; clients never submit a path."""

    try:
        return RepairCommands(storage).source(project_id, chapter)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.get("/{project_id}/repairs", response_model=list[RepairCase])
def get_repair_cases(
    project_id: str,
    storage: StorageDep,
    content_type: str = "",
    status: str = "",
    source: str = "",
    chapter: Annotated[int | None, Query(ge=1)] = None,
    severity: str = "",
) -> list[RepairCase]:
    """List compact repair projections with optional workbench filters."""

    try:
        return RepairCommands(storage).list_cases(
            project_id,
            content_type=content_type,
            status=status,
            source=source,
            chapter_number=chapter,
            severity=severity,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.get("/{project_id}/repairs/{case_id}", response_model=RepairCaseDetailView)
def get_repair_case(
    project_id: str,
    case_id: str,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Read one case, its exact evidence blobs and append-only event trail."""

    try:
        return RepairCommands(storage).detail(project_id, case_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post("/{project_id}/repairs/annotations", response_model=RepairCaseDetailView)
def create_repair_annotation(
    project_id: str,
    body: RepairManualAnnotationRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Create a path-free issue from an exact author-selected character span."""

    try:
        return RepairCommands(storage).annotate(project_id, body)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/candidate",
    response_model=RepairCaseDetailView,
)
def save_repair_candidate(
    project_id: str,
    case_id: str,
    body: RepairCandidateEditRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Save an isolated exact-span replacement and invalidate old verification."""

    try:
        return RepairCommands(storage).save_edit(project_id, case_id, body)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/verify",
    response_model=RepairCaseDetailView,
)
async def verify_repair_candidate(
    project_id: str,
    case_id: str,
    body: RepairVerificationRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Re-run the registered original verifier against the exact candidate."""

    try:
        return await RepairCommands(storage).verify(
            project_id,
            case_id,
            case_version=body.case_version,
            candidate_version=body.candidate_version,
        )
    except (FileNotFoundError, LookupError, OSError, RuntimeError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/decision",
    response_model=RepairCaseDetailView,
)
def decide_repair_case(
    project_id: str,
    case_id: str,
    body: RepairCaseDecisionRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Reject or defer a case even when execution capability is closed."""

    try:
        return RepairCommands(storage).decide(project_id, case_id, body)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/approval",
    response_model=RepairCaseDetailView,
)
async def request_repair_approval(
    project_id: str,
    case_id: str,
    body: RepairApprovalRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Bind a verified official chapter candidate to an existing revision proposal."""

    try:
        return await RepairCommands(storage).request_approval(project_id, case_id, body)
    except (FileNotFoundError, LookupError, OSError, RuntimeError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/publish",
    response_model=RepairCaseDetailView,
)
async def publish_repair_case(
    project_id: str,
    case_id: str,
    body: RepairPublishRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Apply one approved candidate through its existing domain transaction."""

    try:
        return await RepairCommands(storage).publish(project_id, case_id, body)
    except (FileNotFoundError, LookupError, OSError, RuntimeError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


@router.post(
    "/{project_id}/repairs/{case_id}/recover",
    response_model=RepairCaseDetailView,
)
async def recover_repair_receipt(
    project_id: str,
    case_id: str,
    body: RepairRecoveryRequest,
    storage: StorageDep,
) -> RepairCaseDetailView:
    """Reconcile a prepared receipt without replaying its candidate content."""

    try:
        return await RepairCommands(storage).recover(project_id, case_id, body)
    except (FileNotFoundError, LookupError, OSError, RuntimeError, ValueError) as exc:
        raise _repair_http_error(exc) from exc


class PlanningRetry(AuthoringModel):
    current_chapter: int = Field(ge=1)


@router.post("/{project_id}/planning/retry")
async def retry_planning(
    project_id: str, body: PlanningRetry, runtime: RuntimeDep, service: JobServiceDep
) -> dict[str, Any]:
    from novel_forge.workspace.planning_jobs import request_planning_horizon

    try:
        if AuthoringStore(runtime.storage.existing_project_dir(project_id)).policy() is not None:
            _require_rollout()
        state = await request_planning_horizon(
            runtime,
            project_id=project_id,
            current_chapter=body.current_chapter,
            retry=True,
            explicit=True,
            job_service=service,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "accepted",
        "project_id": project_id,
        "task_id": state["job_id"] if state else "",
        "message": "已复用或提交规划任务" if state else "规划已齐备，无需补纲",
    }


@router.get("/{project_id}", response_model=AuthoringSessionView)
def get_authoring_session(
    project_id: str, storage: StorageDep, chapter: Annotated[int, Query(ge=1)] = 1
) -> AuthoringSessionView:
    try:
        root = storage.existing_project_dir(project_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "作品不存在") from exc
    return session_view(root, project_id, chapter)
