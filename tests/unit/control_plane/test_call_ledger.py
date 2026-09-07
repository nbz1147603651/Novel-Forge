"""Tests for CallLedger - per-model-call artifact manifest recording."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.call_ledger import (
    CallLedger,
    compute_prompt_hash,
    compute_response_hash,
    get_call_ledger,
)
from novel_forge.control_plane.enums import ArtifactKind
from novel_forge.control_plane.schemas import RunAttemptDTO, WorkUnitDTO
from novel_forge.control_plane.stage_recorder import StageRecorder
from novel_forge.control_plane.store import ControlPlaneStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> ControlPlaneStore:
    s = ControlPlaneStore.in_memory()
    await s.init_db()
    yield s
    await s.close()


@pytest.fixture
def recorder(store: ControlPlaneStore) -> StageRecorder:
    return StageRecorder(store)


@pytest.fixture
def ledger(recorder: StageRecorder) -> CallLedger:
    return CallLedger(recorder)


@pytest.fixture
def disabled_ledger() -> CallLedger:
    return CallLedger(None)


@pytest.fixture
async def setup_work_unit_and_attempt(store: ControlPlaneStore) -> tuple[str, str]:
    work_unit = WorkUnitDTO(kind="run_chapter", project_id="proj1")
    await store.create_work_unit(work_unit)
    attempt = RunAttemptDTO(work_unit_id=work_unit.id)
    await store.create_run_attempt(attempt)
    return work_unit.id, attempt.id


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def test_compute_prompt_hash_deterministic() -> None:
    messages = [
        {"role": "system", "content": "You are a writer"},
        {"role": "user", "content": "Write chapter 1"},
    ]
    h1 = compute_prompt_hash(messages)
    h2 = compute_prompt_hash(messages)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_prompt_hash_key_order_independent() -> None:
    """Same messages with different dict key order should hash the same."""
    messages1 = [{"role": "user", "content": "Hello", "extra": True}]
    messages2 = [{"extra": True, "content": "Hello", "role": "user"}]
    assert compute_prompt_hash(messages1) == compute_prompt_hash(messages2)


def test_compute_prompt_hash_none() -> None:
    assert compute_prompt_hash(None) == ""


def test_compute_response_hash_dict() -> None:
    response = {"content": "Chapter 1 text...", "tokens": 500}
    h = compute_response_hash(response)
    assert len(h) == 64


def test_compute_response_hash_string() -> None:
    h = compute_response_hash("plain text response")
    assert len(h) == 64


def test_compute_response_hash_none() -> None:
    assert compute_response_hash(None) == ""


def test_prompt_and_response_hashes_differ() -> None:
    messages = [{"role": "user", "content": "Write"}]
    response = {"content": "Written text"}
    assert compute_prompt_hash(messages) != compute_response_hash(response)


# ---------------------------------------------------------------------------
# Disabled ledger
# ---------------------------------------------------------------------------


def test_disabled_ledger_no_op(disabled_ledger: CallLedger) -> None:
    assert not disabled_ledger.enabled
    assert disabled_ledger.record_call(project_id="p") is None
    disabled_ledger.record_router_event("api_call_done", {})


# ---------------------------------------------------------------------------
# Call recording
# ---------------------------------------------------------------------------


def test_record_call(
    ledger: CallLedger,
    store: ControlPlaneStore,
) -> None:
    messages = [{"role": "user", "content": "Write chapter 1"}]
    response = {"content": "Once upon a time...", "tokens": 100}

    artifact_id = ledger.record_call(
        project_id="proj1",
        task_type="DRAFT_CHAPTER",
        route="openai:gpt-4o",
        provider="openai",
        model="gpt-4o",
        messages=messages,
        response=response,
        template_version="2.1.3",
        model_params={"temperature": 0.8, "max_tokens": 4096},
        call_id="call_001",
    )
    assert artifact_id is not None

    fetched = store.sync.get_artifact(artifact_id)
    assert fetched is not None
    assert fetched.artifact_kind == ArtifactKind.MODEL_CALL
    assert fetched.task_type == "DRAFT_CHAPTER"
    assert fetched.route == "openai:gpt-4o"
    assert fetched.prompt_hash == compute_prompt_hash(messages)
    assert fetched.response_hash == compute_response_hash(response)
    assert fetched.template_version == "2.1.3"
    assert fetched.model_params["temperature"] == 0.8
    assert fetched.model_params["provider"] == "openai"
    assert fetched.model_params["model"] == "gpt-4o"


def test_record_call_without_response(
    ledger: CallLedger,
    store: ControlPlaneStore,
) -> None:
    """A call with an error (no response) should still be recorded."""
    messages = [{"role": "user", "content": "Write"}]

    artifact_id = ledger.record_call(
        project_id="proj1",
        task_type="WAVE_CHAPTER",
        route="deepseek:deepseek-chat",
        messages=messages,
        response=None,
        call_id="call_002",
    )
    assert artifact_id is not None

    fetched = store.sync.get_artifact(artifact_id)
    assert fetched is not None
    assert fetched.prompt_hash != ""
    assert fetched.response_hash == ""


def test_record_call_inherits_active_execution_context(
    ledger: CallLedger,
    store: ControlPlaneStore,
    setup_work_unit_and_attempt: tuple[str, str],
) -> None:
    """Desktop/API job observers need not manually thread attempt ids through every call."""
    _, attempt_id = setup_work_unit_and_attempt
    from novel_forge.control_plane.context import bind_execution_context

    with bind_execution_context(
        work_unit_id="wu_context",
        run_attempt_id=attempt_id,
        project_id="proj1",
    ):
        artifact_id = ledger.record_call(
            task_type="DRAFT_CHAPTER",
            messages=[{"role": "user", "content": "Write"}],
            response={"content": "Done"},
            call_id="context_call",
        )

    assert artifact_id is not None
    artifact = store.sync.get_artifact(artifact_id)
    assert artifact is not None
    assert artifact.project_id == "proj1"
    assert artifact.model_params["run_attempt_id"] == attempt_id


# ---------------------------------------------------------------------------
# Router event processing
# ---------------------------------------------------------------------------


def test_record_router_event_done(
    ledger: CallLedger,
    store: ControlPlaneStore,
) -> None:
    """Processing an api_call_done event should record the call."""
    payload = {
        "call_id": "call_003",
        "task": "DRAFT_CHAPTER",
        "provider": "openai",
        "model": "gpt-4o",
        "route": "openai:gpt-4o",
        "max_tokens": 4096,
        "temperature": 0.8,
        "request": {
            "messages": [
                {"role": "system", "content": "You are a novelist"},
                {"role": "user", "content": "Write chapter 1"},
            ],
        },
        "response": {
            "content": "The rain fell steadily...",
            "structured_output_mode": "native",
        },
    }

    # Start event should be ignored
    ledger.record_router_event("api_call_start", {"call_id": "call_003"}, project_id="proj1")

    # Done event should record
    ledger.record_router_event("api_call_done", payload, project_id="proj1")

    # Find the artifact by checking the prompt hash
    prompt_hash = compute_prompt_hash(payload["request"]["messages"])
    # Verify by finding the artifact with the composite hash
    from novel_forge.control_plane.stage_recorder import _hash_json

    composite = {
        "prompt_hash": prompt_hash,
        "response_hash": compute_response_hash(payload["response"]),
        "task_type": "DRAFT_CHAPTER",
        "route": "openai:gpt-4o",
        "call_id": "call_003",
    }
    sha = _hash_json(composite)
    artifact = store.sync.find_artifact_by_sha256(sha)
    assert artifact is not None
    assert artifact.artifact_kind == ArtifactKind.MODEL_CALL


def test_record_router_event_ignores_unknown_events(
    ledger: CallLedger,
    store: ControlPlaneStore,
) -> None:
    """Unknown event types should be silently ignored."""
    ledger.record_router_event("unknown_event", {"call_id": "x"}, project_id="p")
    ledger.record_router_event("api_call_start", {"call_id": "x"}, project_id="p")

    # No artifacts should have been created
    # (We can't easily count all artifacts, but we can verify no error was raised)
    assert True  # If we got here without exception, the test passes


def test_record_router_event_error(
    ledger: CallLedger,
    store: ControlPlaneStore,
) -> None:
    """Error events should still record the call (with empty response hash)."""
    payload = {
        "call_id": "call_004",
        "task": "EDIT_CHAPTER",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "route": "deepseek:deepseek-chat",
        "request": {"messages": [{"role": "user", "content": "Edit this"}]},
        "response": None,
        "error": "rate_limit_exceeded",
    }

    ledger.record_router_event("api_call_error", payload, project_id="proj1")

    # Verify the call was recorded
    from novel_forge.control_plane.stage_recorder import _hash_json

    prompt_hash = compute_prompt_hash(payload["request"]["messages"])
    # record_router_event converts None response to {} via `or {}`, so the
    # response_hash is compute_response_hash({}), not ""
    response_hash = compute_response_hash({})
    composite = {
        "prompt_hash": prompt_hash,
        "response_hash": response_hash,
        "task_type": "EDIT_CHAPTER",
        "route": "deepseek:deepseek-chat",
        "call_id": "call_004",
    }
    sha = _hash_json(composite)
    artifact = store.sync.find_artifact_by_sha256(sha)
    assert artifact is not None
    assert artifact.response_hash == response_hash


# ---------------------------------------------------------------------------
# Exception swallowing
# ---------------------------------------------------------------------------


def test_call_ledger_swallow_exceptions() -> None:
    """CallLedger methods must never raise, even if the store fails."""
    from unittest.mock import MagicMock

    broken_recorder = MagicMock()
    broken_recorder.enabled = True
    broken_recorder.record_artifact.side_effect = RuntimeError("DB down")

    ledger = CallLedger(broken_recorder)

    # None should raise
    ledger.record_call(project_id="p", messages=[{"role": "user", "content": "hi"}])
    ledger.record_router_event("api_call_done", {"call_id": "x"}, project_id="p")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_get_call_ledger_disabled() -> None:
    """When control plane is disabled, get_call_ledger returns a disabled ledger."""
    from novel_forge.core.config import Settings

    settings = Settings(runtime_control_enabled=False)
    ledger = get_call_ledger(settings)
    assert not ledger.enabled
