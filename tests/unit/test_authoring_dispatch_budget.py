from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.authoring import AuthoringPolicy
from novel_forge.core.authoring_context import authoring_dispatch_guard
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.router import ModelRouter, TaskRouteOverride
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.persistence.authoring_budget import AuthoringBudget
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)


@pytest.fixture
def authority(tmp_path):
    root = tmp_path / "budget-book"
    root.mkdir()
    store = AuthoringStore(root)
    policy = store.set_policy(
        AuthoringPolicy(mode="authorized_auto", budget_usd=1, end_chapter=5), expected_version=0
    )
    policy = store.start(expected_version=policy.version, input_version=story_input_version(root))

    def check():
        store.require("generate", 1, expected_policy_version=policy.version)

    return store, AuthoringBudget(root, check), check


def test_budget_reservations_survive_pause_restart_and_policy_changes(authority):
    store, budget, _ = authority
    budget.reserve("a", 0.6, {})
    with pytest.raises(AuthoringDeniedError, match="预算"):
        budget.reserve("b", 0.6, {})
    budget.settle("a", 0.2)
    budget.settle("a", 0.0)  # duplicate settlement cannot erase paid cost
    budget.reserve("b", 0.6, {})
    budget.settle("b", None)  # failed/unknown request retains its reservation
    store.stop()
    restored = AuthoringBudget(store.root)
    assert restored.totals() == {
        "spent_usd": 0.2,
        "reserved_usd": 0.6,
        "unknown_calls": 0,
        "call_count": 2,
    }
    policy = store.policy()
    store.start(expected_version=policy.version, input_version=story_input_version(store.root))
    with pytest.raises(AuthoringDeniedError):
        restored.reserve("c", 0.3, {})
    assert restored.totals()["call_count"] == 2


def test_unknown_pricing_does_not_become_free_budget(authority):
    store, budget, _ = authority
    with pytest.raises(AuthoringDeniedError, match="未知费用"):
        budget.reserve("unknown", None, {})
    assert budget.totals()["call_count"] == 0


class Adapter(ProviderAdapter):
    def __init__(self, callback):
        self.callback = callback
        self.calls = 0

    @property
    def provider_name(self):
        return "test"

    async def complete(self, request):
        self.calls += 1
        return self.callback(request)


def request():
    return ModelRequest(
        task_type=TaskType.DRAFT_CHAPTER,
        model_id="gpt-4o",
        max_tokens=1000,
        messages=[{"role": "user", "content": "保留作者声音"}],
    )


def router_for(first, backup=None):
    return ModelRouter(
        adapters={"test": first, "backup": backup or first},
        default_provider="test",
        task_fallbacks={
            TaskType.DRAFT_CHAPTER: [TaskRouteOverride(provider="backup", model_id="gpt-4o")]
        },
        request_timeout_s=0,
        workflow_timeout_s=0,
    )


async def test_pause_blocks_retry_and_fallback_not_a_provider_error(authority, monkeypatch):
    store, budget, check = authority

    def fail(_request):
        store.stop()
        raise ModelGatewayError("retryable provider error", is_transient=True)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("novel_forge.gateway.router.asyncio.sleep", no_delay)
    first = Adapter(fail)
    backup = Adapter(lambda r: ModelResponse(content="not authorized", model_id=r.model_id))
    with (
        authoring_dispatch_guard(check, reserve=budget.reserve, settle=budget.settle),
        pytest.raises(AuthoringDeniedError),
    ):
        await router_for(first, backup).route(request())
    assert first.calls == 1
    assert backup.calls == 0
    assert budget.totals()["reserved_usd"] > 0


async def test_paid_response_after_pause_still_records_cost(authority):
    store, budget, check = authority

    def finish(r):
        store.stop()
        return ModelResponse(content="候选", model_id=r.model_id, cost_usd=0.04)

    first = Adapter(finish)
    with authoring_dispatch_guard(check, reserve=budget.reserve, settle=budget.settle):
        await router_for(first).route(request())
    assert budget.totals()["spent_usd"] == 0.04
    assert budget.totals()["reserved_usd"] == 0
    with pytest.raises(AuthoringDeniedError):
        check()


async def test_length_retry_cannot_exceed_remaining_budget(authority):
    store, _, _ = authority
    policy = store.policy()
    policy = store.set_policy(
        policy.model_copy(update={"budget_usd": 0.02}), expected_version=policy.version
    )
    policy = store.start(
        expected_version=policy.version, input_version=story_input_version(store.root)
    )

    def check():
        store.require("generate", 1, expected_policy_version=policy.version)

    budget = AuthoringBudget(store.root, check)
    first = Adapter(
        lambda r: ModelResponse(
            content="未完整", model_id=r.model_id, finish_reason="length", cost_usd=0.015
        )
    )
    with (
        authoring_dispatch_guard(check, reserve=budget.reserve, settle=budget.settle),
        pytest.raises(AuthoringDeniedError, match="预算"),
    ):
        await router_for(first).route(request())
    assert first.calls == 1
    assert budget.totals()["spent_usd"] == 0.015


async def test_stream_rechecks_authority_after_rate_limit_wait(authority):
    store, budget, check = authority

    class Limiter:
        async def acquire(self, _tokens):
            store.stop()

        async def report_usage(self, *_args):
            pass

    adapter = Adapter(lambda r: ModelResponse(content="should not dispatch", model_id=r.model_id))
    router = router_for(adapter)
    router._rate_limiter = SimpleNamespace(get=lambda _provider: Limiter())
    with (
        authoring_dispatch_guard(check, reserve=budget.reserve, settle=budget.settle),
        pytest.raises(AuthoringDeniedError),
    ):
        await router.stream_route(request())
    assert adapter.calls == 0
    assert budget.totals()["call_count"] == 0


def test_pending_required_decision_is_durable_before_worker_finishes(tmp_path):
    from novel_forge.app_service.contracts import JobKind, JobRecord, JobState
    from novel_forge.app_service.engine_views import project_job_view
    from novel_forge.app_service.job_service import JobService

    service = JobService(storage_root=tmp_path, load_persisted_history=False)
    record = JobRecord(
        job_id="approval-job",
        kind=JobKind.RUN_CHAPTER,
        label="版本确认",
        project_id="book",
        status=JobState.RUNNING,
    )
    service._jobs[record.job_id] = record
    service._handle_decision_required(
        record.job_id,
        {
            "decision_id": "d",
            "requires_explicit_approval": True,
            "approval_version": "exact",
            "options": [{"id": "accept", "label": "接受"}],
        },
    )
    restored = JobService(storage_root=tmp_path)
    loaded = restored.get(record.job_id)
    assert loaded.status == JobState.PAUSED
    assert loaded.pending_decision["approval_version"] == "exact"
    assert project_job_view(loaded).decisions[0]["approval_version"] == "exact"
    with pytest.raises(ValueError, match="批准版本"):
        restored.provide_decision(record.job_id, {"decision_id": "d", "choice": "accept"})
    service.shutdown(wait_s=0)
    restored.shutdown(wait_s=0)


def test_cancelled_worker_keeps_capacity_until_safe_exit(tmp_path):
    from novel_forge.app_service.contracts import JobKind, JobRecord
    from novel_forge.app_service.job_service import JobService

    service = JobService(storage_root=tmp_path, load_persisted_history=False, max_concurrent_jobs=1)
    record = JobRecord(job_id="old", kind=JobKind.RUN_CHAPTER, label="停止中", project_id="book")
    service._stopping_workers["old"] = SimpleNamespace(record=record)
    other = JobRecord(job_id="next", kind=JobKind.RUN_CHAPTER, label="下一任务", project_id="book")
    assert not service._can_start_locked(other)
    service._stopping_workers.clear()
    assert service._can_start_locked(other)
    service.shutdown(wait_s=0)
