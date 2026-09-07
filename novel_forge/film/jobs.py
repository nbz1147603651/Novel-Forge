"""Governed job ledger for 映界 production work.

Paid media generation must never be double-submitted by a retried UI click,
and in-flight provider tasks must survive process restarts.  This module keeps
a small, durable job ledger inside :class:`FilmStudioState` and provides the
state transitions the pipeline and API share:

- **idempotent acquire** — same (kind, target, provider, model) key returns the
  active job instead of submitting again;
- **attempt accounting** — failures bump ``attempts`` with capped retries;
- **cancellation** — queued/running jobs can be cancelled from the API;
- **recovery** — jobs left ``RUNNING`` by a crash are re-queued while budget
  remains, otherwise marked failed.

The ledger is pure state manipulation (Pydantic ``model_copy``), so it is fully
unit-testable without any provider or filesystem access.
"""

from __future__ import annotations

from .schemas import (
    FilmJob,
    FilmJobKind,
    FilmJobState,
    FilmStudioState,
    utc_now_iso,
)

_ACTIVE_STATES = {FilmJobState.QUEUED, FilmJobState.RUNNING}


class FilmJobManager:
    """Stateless governance over ``state.jobs``; every method returns new state."""

    @staticmethod
    def idempotency_key(
        kind: FilmJobKind,
        target_id: str,
        provider_id: str = "",
        model_id: str = "",
    ) -> str:
        return f"{kind.value}:{target_id}:{provider_id}:{model_id}"

    def acquire(
        self,
        state: FilmStudioState,
        *,
        kind: FilmJobKind,
        target_id: str,
        provider_id: str = "",
        model_id: str = "",
        max_attempts: int = 3,
        estimated_cost_usd: float = 0.0,
    ) -> tuple[FilmStudioState, FilmJob, bool]:
        """Return ``(state, job, created)``.

        ``created=False`` means an active job with the same idempotency key
        already exists; callers must not submit paid work again.
        """

        key = self.idempotency_key(kind, target_id, provider_id, model_id)
        existing = next(
            (
                job
                for job in state.jobs
                if job.idempotency_key == key and job.state in _ACTIVE_STATES
            ),
            None,
        )
        if existing is not None:
            return state, existing, False
        job = FilmJob(
            idempotency_key=key,
            kind=kind,
            target_id=target_id,
            provider_id=provider_id,
            model_id=model_id,
            max_attempts=max_attempts,
            estimated_cost_usd=max(0.0, estimated_cost_usd),
        )
        return _append_job(state, job), job, True

    def mark_running(self, state: FilmStudioState, job: FilmJob) -> FilmStudioState:
        started = job.model_copy(
            update={
                "state": FilmJobState.RUNNING,
                "attempts": job.attempts + 1,
                "updated_at": utc_now_iso(),
            }
        )
        return _replace_job(state, started)

    def mark_succeeded(self, state: FilmStudioState, job: FilmJob) -> FilmStudioState:
        done = job.model_copy(
            update={
                "state": FilmJobState.SUCCEEDED,
                "error_message": "",
                "updated_at": utc_now_iso(),
            }
        )
        return _replace_job(state, done)

    def mark_failed(
        self, state: FilmStudioState, job: FilmJob, *, error: str = ""
    ) -> FilmStudioState:
        failed = job.model_copy(
            update={
                "state": FilmJobState.FAILED,
                "error_message": error[:500],
                "updated_at": utc_now_iso(),
            }
        )
        return _replace_job(state, failed)

    def cancel(self, state: FilmStudioState, job_id: str) -> tuple[FilmStudioState, bool]:
        job = next((item for item in state.jobs if item.job_id == job_id), None)
        if job is None or job.state not in _ACTIVE_STATES:
            return state, False
        cancelled = job.model_copy(
            update={"state": FilmJobState.CANCELLED, "updated_at": utc_now_iso()}
        )
        return _replace_job(state, cancelled), True

    def recover(self, state: FilmStudioState) -> tuple[FilmStudioState, list[FilmJob]]:
        """Re-queue jobs stuck in RUNNING after a crash while retry budget
        remains; otherwise mark them failed.  Returns resumable jobs."""

        resumable: list[FilmJob] = []
        for job in state.jobs:
            if job.state != FilmJobState.RUNNING:
                continue
            if job.attempts < job.max_attempts:
                reset = job.model_copy(
                    update={"state": FilmJobState.QUEUED, "updated_at": utc_now_iso()}
                )
                state = _replace_job(state, reset)
                resumable.append(reset)
            else:
                state = _replace_job(
                    state,
                    job.model_copy(
                        update={
                            "state": FilmJobState.FAILED,
                            "error_message": "重试预算耗尽，任务在中断后未能恢复",
                            "updated_at": utc_now_iso(),
                        }
                    ),
                )
        return state, resumable

    def list_jobs(self, state: FilmStudioState, *, limit: int = 50) -> list[FilmJob]:
        ordered = sorted(state.jobs, key=lambda job: job.updated_at, reverse=True)
        return ordered[: max(0, limit)]

    def retry_backoff_s(self, job: FilmJob) -> float:
        """Exponential backoff: base * 2^(attempts-1), capped at 60s."""

        exponent = max(0, job.attempts - 1)
        return min(60.0, job.retry_backoff_s * (2**exponent))


def _append_job(state: FilmStudioState, job: FilmJob) -> FilmStudioState:
    return state.model_copy(update={"jobs": [*state.jobs, job]})


def _replace_job(state: FilmStudioState, job: FilmJob) -> FilmStudioState:
    jobs = [job if item.job_id == job.job_id else item for item in state.jobs]
    return state.model_copy(update={"jobs": jobs})
