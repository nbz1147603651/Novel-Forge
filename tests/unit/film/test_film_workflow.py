"""Executable run-plan DAG tests (Batch D: ComfyUI node-graph absorption)."""

from __future__ import annotations

import pytest

from novel_forge.film.schemas import FilmStage, FilmStageStatus, RunPlanNode
from novel_forge.film.workflow import (
    completion_ratio,
    invalidate_downstream,
    ready_nodes,
    topological_order,
)


def _node(
    node_id: str,
    depends: list[str] | None = None,
    status: FilmStageStatus = FilmStageStatus.PENDING,
) -> RunPlanNode:
    return RunPlanNode(
        node_id=node_id,
        label=node_id,
        stage=FilmStage.SHOT_PRODUCTION,
        depends_on=list(depends or []),
        status=status,
    )


def test_topological_order_puts_dependencies_first() -> None:
    nodes = [
        _node("shot-b", depends=["asset-a"]),
        _node("asset-a"),
        _node("shot-c", depends=["shot-b"]),
    ]
    ordered = topological_order(nodes)
    ids = [node.node_id for node in ordered]
    assert ids.index("asset-a") < ids.index("shot-b") < ids.index("shot-c")


def test_topological_order_rejects_cycles() -> None:
    nodes = [_node("a", depends=["b"]), _node("b", depends=["a"])]
    with pytest.raises(ValueError, match="Cyclic film workflow"):
        topological_order(nodes)


def test_ready_nodes_only_when_dependencies_completed() -> None:
    nodes = [
        _node("asset-a", status=FilmStageStatus.COMPLETED),
        _node("shot-b", depends=["asset-a"]),
        _node("shot-c", depends=["missing-dep"]),
    ]
    ready = [node.node_id for node in ready_nodes(nodes)]
    assert ready == ["shot-b"]


def test_invalidate_downstream_reverts_subgraph_only() -> None:
    nodes = [
        _node("asset-a", status=FilmStageStatus.COMPLETED),
        _node("shot-b", depends=["asset-a"], status=FilmStageStatus.COMPLETED),
        _node("shot-c", depends=["shot-b"], status=FilmStageStatus.COMPLETED),
        _node("shot-d", status=FilmStageStatus.COMPLETED),  # unrelated
    ]
    updated = invalidate_downstream(nodes, {"shot-b"})

    by_id = {node.node_id: node for node in updated}
    assert by_id["asset-a"].status == FilmStageStatus.COMPLETED  # upstream untouched
    assert by_id["shot-b"].status == FilmStageStatus.PENDING  # failed node
    assert by_id["shot-c"].status == FilmStageStatus.PENDING  # transitive dependent
    assert by_id["shot-d"].status == FilmStageStatus.COMPLETED  # unrelated untouched


def test_completion_ratio_does_not_count_blocked() -> None:
    nodes = [
        _node("a", status=FilmStageStatus.COMPLETED),
        _node("b", status=FilmStageStatus.BLOCKED),
        _node("c"),
        _node("d"),
    ]
    assert completion_ratio(nodes) == 0.25


def test_sync_run_plan_appends_executable_nodes(tmp_path: object) -> None:
    from tests.unit.film.test_film_pipeline import _project

    _layout, pipeline = _project(tmp_path)  # type: ignore[arg-type]
    state = pipeline.get_or_bootstrap()

    synced = pipeline.sync_run_plan(state)
    node_ids = {node.node_id for node in synced.run_plan}
    assert any(node_id.startswith("generate_asset:") for node_id in node_ids)
    assert any(node_id.startswith("generate:") for node_id in node_ids)

    # Idempotent: a second sync adds nothing.
    twice = pipeline.sync_run_plan(synced)
    assert len(twice.run_plan) == len(synced.run_plan)


def test_generate_nodes_depend_on_identity_assets(tmp_path: object) -> None:
    from tests.unit.film.test_film_pipeline import _project

    _layout, pipeline = _project(tmp_path)  # type: ignore[arg-type]
    state = pipeline.get_or_bootstrap()
    synced = pipeline.sync_run_plan(state)

    shot_nodes = [node for node in synced.run_plan if node.node_id.startswith("generate:")]
    assert shot_nodes
    asset_ids = {
        node.node_id for node in synced.run_plan if node.node_id.startswith("generate_asset:")
    }
    assert all(
        dep in asset_ids
        for node in shot_nodes
        for dep in node.depends_on
        if dep.startswith("generate_asset:")
    )


def test_get_or_bootstrap_populates_run_plan_nodes(tmp_path: object) -> None:
    """Loading a project must auto-materialize the executable node graph so the
    frontend run-plan view and '只重跑失败节点' are never empty."""

    from tests.unit.film.test_film_pipeline import _project

    _layout, pipeline = _project(tmp_path)  # type: ignore[arg-type]
    state = pipeline.get_or_bootstrap()

    node_ids = {node.node_id for node in state.run_plan}
    assert any(node_id.startswith("generate_asset:") for node_id in node_ids)
    assert any(node_id.startswith("generate:") for node_id in node_ids)
    # Persisted on disk, not just in memory.
    reloaded = pipeline.store.load()
    assert reloaded is not None
    assert any(node.node_id.startswith("generate:") for node in reloaded.run_plan)


def test_rerun_node_reverts_subgraph_and_persists(tmp_path: object) -> None:
    from tests.unit.film.test_film_pipeline import _project

    _layout, pipeline = _project(tmp_path)  # type: ignore[arg-type]
    state = pipeline.get_or_bootstrap()
    state = pipeline.sync_run_plan(state)
    generate_node = next(node for node in state.run_plan if node.node_id.startswith("generate:"))
    node_id = generate_node.node_id

    # Complete the node then rerun it.
    completed = [
        node.model_copy(update={"status": FilmStageStatus.COMPLETED})
        if node.node_id == node_id
        else node
        for node in state.run_plan
    ]
    pipeline.store.save(state.model_copy(update={"run_plan": completed}))

    rerun = pipeline.rerun_node(node_id)
    node_after = next(node for node in rerun.run_plan if node.node_id == node_id)
    assert node_after.status == FilmStageStatus.PENDING

    # Persisted: a fresh pipeline load sees the pending node.
    fresh = pipeline.get_or_bootstrap()
    node_fresh = next(node for node in fresh.run_plan if node.node_id == node_id)
    assert node_fresh.status == FilmStageStatus.PENDING
