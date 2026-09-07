"""Durable, dependency-aware execution state machine for film graphs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .schemas import utc_now_iso
from .workflow_models import (
    FilmGraphEvent,
    FilmGraphNode,
    FilmGraphRun,
    FilmGraphRunStatus,
    FilmNodeAttempt,
)
from .workflow_repository import FilmWorkflowRepository


@dataclass(frozen=True)
class FilmNodeExecutionResult:
    artifact_uris: tuple[str, ...] = ()
    provider_task_id: str = ""
    cost_usd: float = 0


FilmNodeRunner = Callable[
    [FilmGraphNode, dict[str, tuple[str, ...]]],
    Awaitable[FilmNodeExecutionResult],
]


class FilmGraphExecutor:
    """Execute ready nodes without coupling the graph contract to a provider.

    Registered runners are the trust boundary: graph authors can arrange only
    catalog nodes, while application code controls what each node may execute.
    """

    def __init__(
        self,
        repository: FilmWorkflowRepository,
        runners: dict[str, FilmNodeRunner],
        *,
        provider_concurrency: int = 2,
    ) -> None:
        self.repository = repository
        self.runners = dict(runners)
        self.provider_concurrency = max(1, provider_concurrency)
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}

    async def execute(
        self,
        run_id: str,
        *,
        approved_checkpoint_node_ids: set[str] | None = None,
    ) -> FilmGraphRun:
        run = self._required_run(run_id)
        if run.status in {
            FilmGraphRunStatus.BLOCKED,
            FilmGraphRunStatus.CANCELLED,
            FilmGraphRunStatus.FAILED,
            FilmGraphRunStatus.SUCCEEDED,
            FilmGraphRunStatus.WAITING_CONFIRMATION,
            FilmGraphRunStatus.WAITING_HUMAN,
        }:
            return run
        approved = approved_checkpoint_node_ids or set()
        graph = self.repository.get_graph(run.graph_revision)
        by_id = {node.node_id: node for node in graph.nodes}
        parents: dict[str, set[str]] = {attempt.node_id: set() for attempt in run.attempts}
        for edge in graph.edges:
            if edge.target_node_id in parents and edge.source_node_id in parents:
                parents[edge.target_node_id].add(edge.source_node_id)

        run = self._save_status(run, FilmGraphRunStatus.RUNNING, "run_started", "开始执行工作流")
        while True:
            latest = self._required_run(run_id)
            if latest.status == FilmGraphRunStatus.CANCELLED:
                return latest
            attempts = {attempt.node_id: attempt for attempt in latest.attempts}
            completed = {
                node_id
                for node_id, attempt in attempts.items()
                if attempt.status in {FilmGraphRunStatus.SUCCEEDED, FilmGraphRunStatus.SKIPPED}
            }
            pending = [
                attempt
                for attempt in latest.attempts
                if attempt.status == FilmGraphRunStatus.QUEUED
            ]
            ready = [attempt for attempt in pending if parents[attempt.node_id] <= completed]
            checkpoint = next(
                (
                    attempt
                    for attempt in ready
                    if by_id[attempt.node_id].human_checkpoint and attempt.node_id not in approved
                ),
                None,
            )
            if checkpoint is not None:
                updated_attempts = [
                    item.model_copy(update={"status": FilmGraphRunStatus.WAITING_HUMAN})
                    if item.node_id == checkpoint.node_id
                    else item
                    for item in latest.attempts
                ]
                waiting = latest.model_copy(
                    update={
                        "status": FilmGraphRunStatus.WAITING_HUMAN,
                        "attempts": updated_attempts,
                    }
                )
                waiting = self.repository.save_run(waiting)
                self._event(waiting, "checkpoint_waiting", "等待人工检查点", checkpoint.node_id)
                return waiting
            if not ready:
                return self._finish_or_block(latest, parents)
            results = await asyncio.gather(
                *(self._execute_attempt(by_id[attempt.node_id], attempt) for attempt in ready)
            )
            replacements = {attempt.node_id: attempt for attempt in results}
            run = self.repository.save_run(
                latest.model_copy(
                    update={
                        "attempts": [
                            replacements.get(item.node_id, item) for item in latest.attempts
                        ]
                    }
                )
            )

    def approve_checkpoint(self, run_id: str, node_id: str) -> FilmGraphRun:
        run = self._required_run(run_id)
        attempts = [
            attempt.model_copy(update={"status": FilmGraphRunStatus.QUEUED})
            if attempt.node_id == node_id and attempt.status == FilmGraphRunStatus.WAITING_HUMAN
            else attempt
            for attempt in run.attempts
        ]
        resumed = self.repository.save_run(
            run.model_copy(update={"status": FilmGraphRunStatus.QUEUED, "attempts": attempts})
        )
        self._event(resumed, "checkpoint_approved", "人工检查点已确认", node_id)
        return resumed

    async def _execute_attempt(
        self,
        node: FilmGraphNode,
        attempt: FilmNodeAttempt,
    ) -> FilmNodeAttempt:
        runner = self.runners.get(node.type_id)
        started_at = utc_now_iso()
        if runner is None:
            return attempt.model_copy(
                update={
                    "status": FilmGraphRunStatus.FAILED,
                    "error_code": "executor_not_registered",
                    "error_message": f"节点执行器未注册：{node.type_id}",
                    "started_at": started_at,
                    "finished_at": utc_now_iso(),
                }
            )
        upstream = self._upstream_artifacts(attempt.run_id, node.node_id)
        provider_key = node.provider_id or "local"
        semaphore = self._provider_semaphores.setdefault(
            provider_key,
            asyncio.Semaphore(self.provider_concurrency),
        )
        try:
            async with semaphore:
                result = await runner(node, upstream)
        except (OSError, RuntimeError, ValueError) as exc:
            return attempt.model_copy(
                update={
                    "status": FilmGraphRunStatus.FAILED,
                    "error_code": type(exc).__name__,
                    "error_message": str(exc),
                    "started_at": started_at,
                    "finished_at": utc_now_iso(),
                }
            )
        return attempt.model_copy(
            update={
                "status": FilmGraphRunStatus.SUCCEEDED,
                "artifact_uris": list(result.artifact_uris),
                "provider_task_id": result.provider_task_id,
                "cost_usd": result.cost_usd,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
            }
        )

    def _upstream_artifacts(self, run_id: str, node_id: str) -> dict[str, tuple[str, ...]]:
        run = self._required_run(run_id)
        graph = self.repository.get_graph(run.graph_revision)
        source_ids = {edge.source_node_id for edge in graph.edges if edge.target_node_id == node_id}
        return {
            attempt.node_id: tuple(attempt.artifact_uris)
            for attempt in run.attempts
            if attempt.node_id in source_ids
        }

    def _finish_or_block(
        self,
        run: FilmGraphRun,
        parents: dict[str, set[str]],
    ) -> FilmGraphRun:
        statuses = {attempt.status for attempt in run.attempts}
        if statuses <= {FilmGraphRunStatus.SUCCEEDED, FilmGraphRunStatus.SKIPPED}:
            return self._save_status(
                run,
                FilmGraphRunStatus.SUCCEEDED,
                "run_succeeded",
                "工作流执行完成",
            )
        failed = {
            attempt.node_id
            for attempt in run.attempts
            if attempt.status == FilmGraphRunStatus.FAILED
        }
        attempts = [
            attempt.model_copy(update={"status": FilmGraphRunStatus.BLOCKED})
            if attempt.status == FilmGraphRunStatus.QUEUED and parents[attempt.node_id] & failed
            else attempt
            for attempt in run.attempts
        ]
        failed_run = self.repository.save_run(
            run.model_copy(update={"status": FilmGraphRunStatus.FAILED, "attempts": attempts})
        )
        self._event(failed_run, "run_failed", "工作流包含失败或阻塞节点")
        return failed_run

    def _save_status(
        self,
        run: FilmGraphRun,
        status: FilmGraphRunStatus,
        kind: str,
        message: str,
    ) -> FilmGraphRun:
        saved = self.repository.save_run(run.model_copy(update={"status": status}))
        self._event(saved, kind, message)
        return saved

    def _event(self, run: FilmGraphRun, kind: str, message: str, node_id: str = "") -> None:
        self.repository.append_event(
            FilmGraphEvent(
                run_id=run.run_id,
                sequence=self.repository.next_event_sequence(run.run_id),
                kind=kind,
                node_id=node_id,
                message=message,
            )
        )

    def _required_run(self, run_id: str) -> FilmGraphRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown workflow run: {run_id}")
        return run


__all__ = ["FilmGraphExecutor", "FilmNodeExecutionResult", "FilmNodeRunner"]
