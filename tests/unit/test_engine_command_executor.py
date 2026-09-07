"""Executor-level tests validating Engine command payloads against real WorkspaceCommandExecutor.

These tests ensure that the thin API command handlers in
``novel_forge/api/routes/engine.py`` produce payloads that the production
``WorkspaceCommandExecutor.prepare()`` can successfully transform into typed
workspace request models.
"""

from __future__ import annotations

import pytest

from novel_forge.app_service.contracts import JobCommand, JobKind
from novel_forge.app_service.engine_views import engine_capabilities
from novel_forge.app_service.workspace_commands import WorkspaceCommandExecutor
from novel_forge.core.schemas.repair import RepairCaseJobRequest
from novel_forge.workspace.contracts import (
    BookEditorialAuditRequest,
    ExportBookRequest,
    GlobalRepairQueueRequest,
    PrepareChapterRequest,
    SemanticConsistencyRefreshRequest,
    TTSExportAudiobookRequest,
    TTSExportAudioRequest,
    TTSSynthesizeRequest,
)


@pytest.fixture
def executor() -> WorkspaceCommandExecutor:
    return WorkspaceCommandExecutor()


# ── prepare_chapter ───────────────────────────────────────────────────────────


def test_prepare_chapter_payload_produces_valid_request(executor: WorkspaceCommandExecutor) -> None:
    """The Engine API prepare-chapter payload must satisfy PrepareChapterRequest."""
    command = JobCommand(
        kind="prepare_chapter",
        project_id="demo",
        payload={
            "project_id": "demo",
            "chapter_number": 3,
            "notes": "测试备注",
        },
    )

    prepared = executor.prepare(command)

    assert prepared.kind == JobKind.PREPARE_CHAPTER
    assert prepared.project_id == "demo"
    assert isinstance(prepared.request, PrepareChapterRequest)
    assert prepared.request.project_id == "demo"
    assert prepared.request.chapter_number == 3
    assert prepared.request.notes == "测试备注"


def test_prepare_chapter_without_project_id_in_payload_fails(
    executor: WorkspaceCommandExecutor,
) -> None:
    """Omitting project_id from the payload must raise a validation error."""
    command = JobCommand(
        kind="prepare_chapter",
        project_id="demo",
        payload={
            "chapter_number": 3,
            "notes": "",
        },
    )

    with pytest.raises(Exception):  # noqa: B017 - Pydantic ValidationError
        executor.prepare(command)


def test_repair_case_job_is_path_free_and_version_bound(
    executor: WorkspaceCommandExecutor,
) -> None:
    prepared = executor.prepare(
        JobCommand(
            kind=JobKind.REPAIR_CASE,
            project_id="demo",
            payload={
                "project_id": "demo",
                "case_id": "case-1",
                "operation": "verify",
                "case_version": 4,
                "candidate_version": 2,
            },
        )
    )

    assert prepared.kind == JobKind.REPAIR_CASE
    assert isinstance(prepared.request, RepairCaseJobRequest)
    assert prepared.request.case_version == 4
    assert prepared.request.candidate_version == 2

    with pytest.raises(Exception):  # noqa: B017 - extra paths are forbidden
        executor.prepare(
            JobCommand(
                kind=JobKind.REPAIR_CASE,
                project_id="demo",
                payload={
                    "project_id": "demo",
                    "case_id": "case-1",
                    "operation": "prepare",
                    "case_version": 4,
                    "candidate_version": 0,
                    "path": "/tmp/official.md",
                },
            )
        )

    with pytest.raises(Exception):  # noqa: B017 - verify must bind a candidate
        executor.prepare(
            JobCommand(
                kind=JobKind.REPAIR_CASE,
                project_id="demo",
                payload={
                    "project_id": "demo",
                    "case_id": "case-1",
                    "operation": "verify",
                    "case_version": 4,
                    "candidate_version": 0,
                },
            )
        )


def test_semantic_consistency_job_is_path_free_and_version_bound(
    executor: WorkspaceCommandExecutor,
) -> None:
    prepared = executor.prepare(
        JobCommand(
            kind=JobKind.SEMANTIC_CONSISTENCY,
            project_id="demo",
            payload={
                "project_id": "demo",
                "expected_story_version": "story-v3",
                "expected_policy_version": 7,
            },
        )
    )

    assert prepared.kind == JobKind.SEMANTIC_CONSISTENCY
    assert isinstance(prepared.request, SemanticConsistencyRefreshRequest)
    assert prepared.request.expected_story_version == "story-v3"
    assert prepared.request.expected_policy_version == 7
    assert prepared.label == "刷新语义一致性"

    with pytest.raises(Exception):  # noqa: B017 - clients cannot submit source paths
        executor.prepare(
            JobCommand(
                kind=JobKind.SEMANTIC_CONSISTENCY,
                project_id="demo",
                payload={
                    "project_id": "demo",
                    "expected_story_version": "story-v3",
                    "expected_policy_version": 7,
                    "source_path": "/tmp/outline.json",
                },
            )
        )


# ── tts_synthesize ────────────────────────────────────────────────────────────


def test_synthesize_voice_payload_produces_valid_request(
    executor: WorkspaceCommandExecutor,
) -> None:
    """The Engine API synthesize-voice payload must satisfy TTSSynthesizeRequest."""
    command = JobCommand(
        kind="tts_synthesize",
        project_id="demo",
        payload={
            "project_id": "demo",
            "chapter_number": 6,
        },
    )

    prepared = executor.prepare(command)

    assert prepared.kind == JobKind.TTS_SYNTHESIZE
    assert isinstance(prepared.request, TTSSynthesizeRequest)
    assert prepared.request.project_id == "demo"
    assert prepared.request.chapter_number == 6


def test_synthesize_voice_without_chapter_number_fails(
    executor: WorkspaceCommandExecutor,
) -> None:
    """TTSSynthesizeRequest requires chapter_number >= 1."""
    command = JobCommand(
        kind="tts_synthesize",
        project_id="demo",
        payload={
            "project_id": "demo",
        },
    )

    with pytest.raises(Exception):  # noqa: B017
        executor.prepare(command)


# ── export_book ───────────────────────────────────────────────────────────────


def test_export_book_payload_produces_valid_request(
    executor: WorkspaceCommandExecutor,
) -> None:
    """The Engine API export-audio (book scope) payload must satisfy ExportBookRequest."""
    command = JobCommand(
        kind="export_book",
        project_id="demo",
        payload={
            "project_id": "demo",
            "format": "markdown",
        },
    )

    prepared = executor.prepare(command)

    assert prepared.kind == JobKind.EXPORT_BOOK
    assert isinstance(prepared.request, ExportBookRequest)
    assert prepared.request.project_id == "demo"


def test_global_repair_queue_payload_preserves_commercial_safety_gates(
    executor: WorkspaceCommandExecutor,
) -> None:
    prepared = executor.prepare(
        JobCommand(
            kind="global_repair_queue",
            project_id="demo",
            payload={
                "project_id": "demo",
                "statuses": ["ready"],
                "max_items": 25,
                "verify_before_apply": True,
                "rollback_on_failure": True,
                "concurrency": 2,
            },
        )
    )

    assert prepared.kind == JobKind.GLOBAL_REPAIR_QUEUE
    assert isinstance(prepared.request, GlobalRepairQueueRequest)
    assert prepared.request.statuses == ["ready"]
    assert prepared.request.max_items == 25
    assert prepared.request.verify_before_apply is True
    assert prepared.request.rollback_on_failure is True
    assert prepared.request.concurrency == 2


def test_book_editorial_audit_payload_produces_durable_request(
    executor: WorkspaceCommandExecutor,
) -> None:
    prepared = executor.prepare(
        JobCommand(
            kind="book_editorial_audit",
            project_id="demo",
            payload={
                "project_id": "demo",
                "chapter_range": [1, 2, 3],
                "prompt_hint": "优先检查中段节奏",
                "max_tokens": 12000,
                "temperature": 0.15,
                "batch_size": 3,
            },
        )
    )

    assert prepared.kind == JobKind.BOOK_EDITORIAL_AUDIT
    assert isinstance(prepared.request, BookEditorialAuditRequest)
    assert prepared.request.chapter_range == [1, 2, 3]
    assert prepared.request.batch_size == 3


def test_voice_delivery_payloads_produce_durable_export_requests(
    executor: WorkspaceCommandExecutor,
) -> None:
    audio = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIO,
            project_id="demo",
            payload={
                "project_id": "demo",
                "scope": "chapter",
                "chapter_number": 6,
                "format": "mp3",
            },
        )
    )
    audiobook = executor.prepare(
        JobCommand(
            kind=JobKind.TTS_EXPORT_AUDIOBOOK,
            project_id="demo",
            payload={"project_id": "demo", "chapter_numbers": [1, 2]},
        )
    )

    assert isinstance(audio.request, TTSExportAudioRequest)
    assert audio.request.chapter_number == 6
    assert isinstance(audiobook.request, TTSExportAudiobookRequest)
    assert audiobook.request.chapter_numbers == [1, 2]


# ── start_workflow (run_short / run_chapter / init_long) ──────────────────────


def test_start_workflow_short_produces_valid_kind(executor: WorkspaceCommandExecutor) -> None:
    command = JobCommand(
        kind="run_short",
        project_id="demo",
        payload={"project_id": "demo", "theme": "测试主题"},
    )

    prepared = executor.prepare(command)
    assert prepared.kind == JobKind.RUN_SHORT


def test_start_workflow_long_chapter_produces_valid_kind(
    executor: WorkspaceCommandExecutor,
) -> None:
    command = JobCommand(
        kind="run_chapter",
        project_id="demo",
        payload={"project_id": "demo", "chapter_number": 1},
    )

    prepared = executor.prepare(command)
    assert prepared.kind == JobKind.RUN_CHAPTER


# ── capabilities consistency ──────────────────────────────────────────────────


def test_capabilities_commands_match_registry() -> None:
    """Every command declared available in capabilities must have a registered JobKind."""
    from novel_forge.app_service.workspace_commands import COMMAND_REGISTRY

    view = engine_capabilities()
    kind_mapping = {
        "prepare_chapter": JobKind.PREPARE_CHAPTER,
        "cancel_job": None,  # cancel is handled by JobService.cancel, not COMMAND_REGISTRY
        "start_workflow": JobKind.RUN_CHAPTER,  # representative; all 3 kinds exist
        "audit_book": JobKind.BOOK_CONSISTENCY,
        "audit_book_editorial": JobKind.BOOK_EDITORIAL_AUDIT,
        "execute_global_repair_queue": JobKind.GLOBAL_REPAIR_QUEUE,
        "synthesize_voice": JobKind.TTS_SYNTHESIZE,
        "export_audio": JobKind.TTS_EXPORT_AUDIO,
        "export_audiobook": JobKind.TTS_EXPORT_AUDIOBOOK,
    }

    for command_name, enabled in view.commands.items():
        if not enabled:
            continue
        kind = kind_mapping.get(command_name)
        if kind is None:
            # cancel_job is a JobService-level operation, not a COMMAND_REGISTRY entry
            continue
        assert kind in COMMAND_REGISTRY, (
            f"capabilities declares '{command_name}' as available "
            f"but {kind.value} is not in COMMAND_REGISTRY"
        )


def test_capabilities_export_commands_are_backed_by_durable_job_kinds() -> None:
    view = engine_capabilities()
    assert view.commands["export_audio"] is True
    assert view.commands["export_audiobook"] is True


def test_unsupported_job_kind_is_rejected_at_command_construction() -> None:
    """JobCommand.kind is a strict enum; invalid kinds fail at construction."""
    with pytest.raises(Exception):  # noqa: B017 - Pydantic ValidationError
        JobCommand(
            kind="export_chapter_audio",  # type: ignore[arg-type]
            project_id="demo",
            payload={},
        )
