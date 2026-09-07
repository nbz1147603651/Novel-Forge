"""Versioned film graph, validation, estimate and repository tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.film.node_catalog import default_film_graph
from novel_forge.film.workflow_executor import FilmGraphExecutor, FilmNodeExecutionResult
from novel_forge.film.workflow_graph import build_run, estimate_run, validate_graph
from novel_forge.film.workflow_models import (
    FilmGraphEdge,
    FilmGraphEvent,
    FilmGraphNode,
    FilmGraphPosition,
    FilmGraphRunScope,
    FilmGraphRunStatus,
)
from novel_forge.film.workflow_repository import (
    FilmWorkflowRepository,
    FilmWorkflowRevisionConflict,
)


def test_default_graph_is_valid_and_targets_only_ancestor_closure() -> None:
    graph = default_film_graph("film-demo")

    assert validate_graph(graph) == []
    estimate = estimate_run(
        graph,
        scope=FilmGraphRunScope.TO_NODE,
        target_node_ids=["minimax-h3"],
    )

    assert "creative-brief" in estimate.execution_node_ids
    assert "minimax-h3" in estimate.execution_node_ids
    assert "film-qc" not in estimate.execution_node_ids
    assert "delivery" not in estimate.execution_node_ids
    assert estimate.estimated_cost_usd > 0
    assert estimate.requires_confirmation is True


def test_validation_rejects_incompatible_port_and_cycle() -> None:
    graph = default_film_graph("film-demo")
    forged_nodes = [
        node.model_copy(
            update={
                "output_ports": [
                    port.model_copy(update={"artifact_type": "CreativeBrief"})
                    for port in node.output_ports
                ]
            }
        )
        if node.node_id == "delivery"
        else node
        for node in graph.nodes
    ]
    bad_edge = FilmGraphEdge(
        edge_id="bad-type",
        source_node_id="style-lock",
        source_port_id="style",
        target_node_id="film-qc",
        target_port_id="video",
    )
    cycle_edge = FilmGraphEdge(
        edge_id="cycle",
        source_node_id="delivery",
        source_port_id="package",
        target_node_id="style-lock",
        target_port_id="brief",
    )
    invalid = graph.model_copy(
        update={"nodes": forged_nodes, "edges": [*graph.edges, bad_edge, cycle_edge]}
    )

    codes = {issue.code for issue in validate_graph(invalid)}

    assert "incompatible_ports" in codes
    assert "node_port_schema_mismatch" in codes
    assert "stage_regression" in codes
    assert "cycle" in codes


def test_validation_rejects_duplicate_port_connections() -> None:
    graph = default_film_graph("film-demo")
    original = graph.edges[0]
    duplicate = original.model_copy(update={"edge_id": "duplicate-port-pair"})

    codes = {issue.code for issue in validate_graph(graph.model_copy(update={"edges": [*graph.edges, duplicate]}))}

    assert "duplicate_connection" in codes


def test_validation_rejects_unregistered_video_provider_and_model() -> None:
    graph = default_film_graph("provider-validation")
    invalid = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "provider_id": "unknown-platform",
                        "model_id": "invented-video",
                    }
                )
                if node.type_id == "minimax_h3_video"
                else node
                for node in graph.nodes
            ]
        }
    )

    codes = {issue.code for issue in validate_graph(invalid)}
    invalid_model = invalid.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"provider_id": "bailian"})
                if node.type_id == "minimax_h3_video"
                else node
                for node in invalid.nodes
            ]
        }
    )
    model_codes = {issue.code for issue in validate_graph(invalid_model)}

    assert "provider_not_supported" in codes
    assert "model_not_supported" in model_codes


def test_validation_accepts_bailian_video_route() -> None:
    graph = default_film_graph("provider-validation")
    routed = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={"provider_id": "bailian", "model_id": "wan2.7-r2v"}
                )
                if node.type_id == "minimax_h3_video"
                else node
                for node in graph.nodes
            ]
        }
    )

    codes = {issue.code for issue in validate_graph(routed)}

    assert "provider_not_supported" not in codes
    assert "model_not_supported" not in codes


def test_paid_run_waits_for_confirmation() -> None:
    graph = default_film_graph("film-demo")
    estimate = estimate_run(
        graph,
        scope=FilmGraphRunScope.TO_NODE,
        target_node_ids=["minimax-h3"],
    )

    waiting = build_run(graph, estimate, confirmed_cost=False)
    queued = build_run(graph, estimate, confirmed_cost=True)

    assert waiting.status == FilmGraphRunStatus.WAITING_CONFIRMATION
    assert queued.status == FilmGraphRunStatus.QUEUED
    assert {attempt.run_id for attempt in queued.attempts} == {queued.run_id}


def test_estimate_blocks_when_project_budget_is_exceeded() -> None:
    graph = default_film_graph("film-demo")
    estimate = estimate_run(
        graph,
        scope=FilmGraphRunScope.TO_NODE,
        target_node_ids=["minimax-h3"],
        budget_usd=1,
    )

    assert any(issue.code == "budget_exceeded" for issue in estimate.validation_issues)
    assert build_run(graph, estimate, confirmed_cost=True).status == FilmGraphRunStatus.BLOCKED


def test_successful_attempt_with_artifact_is_cached() -> None:
    graph = default_film_graph("film-demo")
    first_estimate = estimate_run(
        graph,
        scope=FilmGraphRunScope.SELECTED,
        target_node_ids=["creative-brief"],
    )
    first_run = build_run(graph, first_estimate, confirmed_cost=True)
    succeeded_attempt = first_run.attempts[0].model_copy(
        update={
            "status": FilmGraphRunStatus.SUCCEEDED,
            "artifact_uris": ["artifact://creative-brief/v1"],
        }
    )
    first_run = first_run.model_copy(update={"attempts": [succeeded_attempt]})

    repeated = estimate_run(
        graph,
        scope=FilmGraphRunScope.SELECTED,
        target_node_ids=["creative-brief"],
        prior_runs=[first_run],
    )

    assert repeated.cached_node_ids == ["creative-brief"]
    assert repeated.estimated_cost_usd == 0


def test_repository_persists_revisions_runs_and_event_cursor(tmp_path: Path) -> None:
    repository = FilmWorkflowRepository(tmp_path / "film", "film-demo")
    graph = repository.get_graph()
    moved = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"position": FilmGraphPosition(x=420, y=180)})
                if node.node_id == "creative-brief"
                else node
                for node in graph.nodes
            ]
        }
    )

    saved = repository.save_graph(moved, expected_revision=graph.revision)
    assert saved.revision == graph.revision + 1
    assert repository.get_graph().nodes[0].position.x == 420
    with pytest.raises(FilmWorkflowRevisionConflict):
        repository.save_graph(moved, expected_revision=graph.revision)

    estimate = estimate_run(
        saved,
        scope=FilmGraphRunScope.SELECTED,
        target_node_ids=["creative-brief"],
    )
    run = repository.save_run(build_run(saved, estimate, confirmed_cost=True))
    repository.append_event(FilmGraphEvent(run_id=run.run_id, sequence=1, kind="run_created"))
    repository.append_event(FilmGraphEvent(run_id=run.run_id, sequence=2, kind="run_started"))

    assert repository.get_run(run.run_id) is not None
    assert [event.sequence for event in repository.list_events(run.run_id, after_sequence=1)] == [2]


async def test_executor_persists_success_and_artifacts(tmp_path: Path) -> None:
    repository = FilmWorkflowRepository(tmp_path / "film", "film-demo")
    graph = repository.get_graph()
    estimate = estimate_run(
        graph,
        scope=FilmGraphRunScope.SELECTED,
        target_node_ids=["creative-brief"],
    )
    run = repository.save_run(build_run(graph, estimate, confirmed_cost=True))

    async def execute_brief(
        _node: FilmGraphNode,
        _upstream: dict[str, tuple[str, ...]],
    ) -> FilmNodeExecutionResult:
        return FilmNodeExecutionResult(artifact_uris=("artifact://creative-brief/v1",))

    executor = FilmGraphExecutor(repository, {"creative_brief": execute_brief})
    completed = await executor.execute(run.run_id)

    assert completed.status == FilmGraphRunStatus.SUCCEEDED
    assert completed.attempts[0].artifact_uris == ["artifact://creative-brief/v1"]
    assert [event.kind for event in repository.list_events(run.run_id)] == [
        "run_started",
        "run_succeeded",
    ]
