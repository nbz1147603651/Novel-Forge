"""Tests for RuntimeControlPlane unified facade and CI constraint verification."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.enums import Priority
from novel_forge.control_plane.harness import (
    StageHarness,
    get_enforcement_mode,
    set_enforcement_mode,
)
from novel_forge.control_plane.plane import RuntimeControlPlane
from novel_forge.control_plane.store import ControlPlaneStore

# ---------------------------------------------------------------------------
# RuntimeControlPlane
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> ControlPlaneStore:
    s = ControlPlaneStore.in_memory()
    await s.init_db()
    yield s
    await s.close()


def test_plane_from_store(store: ControlPlaneStore) -> None:
    plane = RuntimeControlPlane(store=store)
    assert plane.enabled
    assert plane.store is store
    assert plane.health is not None
    assert plane.capacity is not None
    assert plane.shadow is not None
    assert plane.recorder is not None


def test_plane_disabled_when_store_none() -> None:
    plane = RuntimeControlPlane(store=None)
    assert not plane.enabled
    assert plane.store is None
    # Subsystems still work (just disabled)
    assert plane.shadow.enabled is False
    assert plane.recorder.enabled is False


def test_plane_health_and_capacity(store: ControlPlaneStore) -> None:
    plane = RuntimeControlPlane(store=store)
    plane.health.record_success("openai", "DRAFT_CHAPTER")
    decision = plane.capacity.can_admit(Priority.P1, "DRAFT_CHAPTER")
    assert decision.admitted


def test_plane_get_harness(store: ControlPlaneStore) -> None:
    plane = RuntimeControlPlane(store=store)
    harness = plane.get_harness("draft")
    assert isinstance(harness, StageHarness)
    assert harness.stage_name == "draft"


def test_plane_from_settings_disabled() -> None:
    from novel_forge.core.config import Settings

    settings = Settings(runtime_control_enabled=False)
    plane = RuntimeControlPlane.from_settings(settings)
    assert plane is None


def test_plane_from_settings_applies_enforcement_mode(tmp_path) -> None:
    """The configured mode, not a constructor default, controls live checks."""
    from novel_forge.control_plane.factory import reset_control_plane_store
    from novel_forge.core.config import Settings

    reset_control_plane_store()
    try:
        plane = RuntimeControlPlane.from_settings(
            Settings(
                storage_root=str(tmp_path),
                runtime_control_enabled=True,
                runtime_control_enforcement_mode="enforce",
            )
        )
        assert plane is not None
        assert get_enforcement_mode() == "enforce"
    finally:
        reset_control_plane_store()
        set_enforcement_mode("advisory")


def test_plane_sets_enforcement_mode(store: ControlPlaneStore) -> None:
    """Plane should set the enforcement mode on creation."""
    set_enforcement_mode("enforce")
    assert get_enforcement_mode() == "enforce"

    RuntimeControlPlane(store=store, enforcement_mode="advisory")
    assert get_enforcement_mode() == "advisory"

    # Reset for other tests
    set_enforcement_mode("advisory")


# ---------------------------------------------------------------------------
# CI Constraint Verification (inline checks mirroring the script)
# ---------------------------------------------------------------------------


def test_dag_acyclic() -> None:
    """The stage DAG must be acyclic."""
    from novel_forge.control_plane.registry import STAGE_DAG_EDGES

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in STAGE_DAG_EDGES}

    def has_cycle(node: str) -> bool:
        color[node] = GRAY
        for neighbor in STAGE_DAG_EDGES.get(node, frozenset()):
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and has_cycle(neighbor):
                return True
        color[node] = BLACK
        return False

    for node in STAGE_DAG_EDGES:
        if color[node] == WHITE:
            assert not has_cycle(node), f"Cycle detected starting from {node}"


def test_all_dag_targets_registered() -> None:
    from novel_forge.control_plane.registry import STAGE_DAG_EDGES, STAGE_DEFINITIONS

    for source, targets in STAGE_DAG_EDGES.items():
        assert source in STAGE_DEFINITIONS
        for target in targets:
            assert target in STAGE_DEFINITIONS


def test_no_stage_reads_project_spec() -> None:
    from novel_forge.control_plane.registry import get_stage_definition, list_stage_names

    for name in list_stage_names():
        sd = get_stage_definition(name)
        assert sd is not None
        assert not sd.can_read_full_project_spec, f"Stage '{name}' can read project_spec"


def test_tts_isolation() -> None:
    from novel_forge.control_plane.registry import get_stage_definition

    for tts_stage in ["tts_script", "tts_synthesis", "tts_assembly"]:
        sd = get_stage_definition(tts_stage)
        assert sd is not None
        assert "canon" not in sd.commit_resource_kinds
        assert "chapter" not in sd.commit_resource_kinds


def test_only_finalize_commits_canon() -> None:
    from novel_forge.control_plane.registry import get_stage_definition, list_stage_names

    for name in list_stage_names():
        sd = get_stage_definition(name)
        assert sd is not None
        if name != "finalize":
            assert "canon" not in sd.commit_resource_kinds, f"Stage '{name}' can commit canon"
    # Finalize MUST be able to commit canon
    finalize = get_stage_definition("finalize")
    assert "canon" in finalize.commit_resource_kinds


def test_repair_requires_hash_validation() -> None:
    from novel_forge.control_plane.registry import get_stage_definition

    repair = get_stage_definition("repair")
    assert repair is not None
    assert "source_text_hash_match" in repair.required_validations
    assert "window_bounds" in repair.required_validations


def test_all_definitions_have_valid_fields() -> None:
    from novel_forge.control_plane.registry import STAGE_DEFINITIONS

    for name, sd in STAGE_DEFINITIONS.items():
        assert sd.name, f"Stage '{name}' has no name"
        assert sd.allowed_artifact_types, f"Stage '{name}' has no allowed_artifact_types"
        assert sd.allowed_proposal_types, f"Stage '{name}' has no allowed_proposal_types"
        assert sd.editable_text_window in ("none", "patch", "full")
        assert sd.degradation_policy in ("block", "advisory", "allow_local")


# ---------------------------------------------------------------------------
# Script execution test
# ---------------------------------------------------------------------------


def test_verify_script_passes() -> None:
    """The verify_control_plane_constraints.py script should pass."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/verify_control_plane_constraints.py"],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
    )
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"
    assert "RESULT: PASS" in result.stdout
