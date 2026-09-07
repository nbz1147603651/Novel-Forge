"""Lightweight executable DAG for film production nodes.

Absorbs ComfyUI's node-graph execution idea without its torch/caching stack:
nodes carry dependencies and artifact I/O, the runner exposes ready nodes in
topological order, and a failed node plus its transitive dependents revert to
pending — so re-running repairs only the affected subgraph (the frontend's
"只重跑失败节点" becomes factual instead of a caption).

Pure state transformations over :class:`RunPlanNode`; fully unit-testable
without providers or filesystem access.
"""

from __future__ import annotations

from .schemas import FilmStageStatus, RunPlanNode


def topological_order(nodes: list[RunPlanNode]) -> list[RunPlanNode]:
    """Return nodes sorted so every dependency precedes its dependent.

    Cyclic dependencies are rejected because an executable production graph
    must be a DAG.  Validation surfaces a deterministic error instead of
    silently returning an invalid order.
    """
    by_id = {node.node_id: node for node in nodes}
    ordered: list[RunPlanNode] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"Cyclic film workflow dependency at node: {node_id}")
        node = by_id.get(node_id)
        if node is None:
            return
        visiting.add(node_id)
        for dep in node.depends_on:
            if dep in by_id:
                visit(dep)
        visiting.remove(node_id)
        visited.add(node_id)
        ordered.append(node)

    for node in nodes:
        visit(node.node_id)
    return ordered


def ready_nodes(nodes: list[RunPlanNode]) -> list[RunPlanNode]:
    """Pending nodes whose dependencies are all completed (executable now)."""
    completed = {node.node_id for node in nodes if node.status == FilmStageStatus.COMPLETED}
    return [
        node
        for node in nodes
        if node.status == FilmStageStatus.PENDING
        and all(dep in completed for dep in node.depends_on)
    ]


def invalidate_downstream(nodes: list[RunPlanNode], failed_ids: set[str]) -> list[RunPlanNode]:
    """Revert failed nodes and every transitive dependent to pending.

    The affected set grows until closure over ``depends_on``; unrelated
    completed nodes stay untouched so a single failed node only forces the
    re-execution of its own subgraph.
    """
    by_id = {node.node_id: node for node in nodes}
    affected = set(failed_ids)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.node_id in affected:
                continue
            if any(dep in affected for dep in node.depends_on if dep in by_id):
                affected.add(node.node_id)
                changed = True
    return [
        node.model_copy(update={"status": FilmStageStatus.PENDING})
        if node.node_id in affected and node.status != FilmStageStatus.PENDING
        else node
        for node in nodes
    ]


def completion_ratio(nodes: list[RunPlanNode]) -> float:
    """Fraction of nodes that actually completed successfully."""
    if not nodes:
        return 0.0
    done = sum(1 for node in nodes if node.status == FilmStageStatus.COMPLETED)
    return round(done / len(nodes), 4)


__all__ = [
    "completion_ratio",
    "invalidate_downstream",
    "ready_nodes",
    "topological_order",
]
