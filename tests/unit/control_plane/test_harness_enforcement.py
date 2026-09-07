"""Tests for StageDefinition and StageHarness permission enforcement."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.harness import (
    HarnessViolationError,
    StageHarness,
    get_enforcement_mode,
    set_enforcement_mode,
)
from novel_forge.control_plane.registry import (
    STAGE_DAG_EDGES,
    STAGE_DEFINITIONS,
    get_stage_definition,
    list_stage_names,
)
from novel_forge.control_plane.stage_definitions import StageDefinition

# ---------------------------------------------------------------------------
# StageDefinition
# ---------------------------------------------------------------------------


def test_stage_definition_frozen() -> None:
    """StageDefinition is a frozen dataclass."""
    sd = StageDefinition(name="test")
    with pytest.raises(AttributeError):
        sd.name = "other"  # type: ignore[misc]


def test_can_read_artifact() -> None:
    sd = StageDefinition(
        name="draft",
        allowed_artifact_types=frozenset({"plan", "source_slice"}),
    )
    assert sd.can_read_artifact("plan")
    assert sd.can_read_artifact("source_slice")
    assert not sd.can_read_artifact("project_spec")


def test_can_generate_proposal() -> None:
    sd = StageDefinition(
        name="repair",
        allowed_proposal_types=frozenset({"patch", "repair"}),
    )
    assert sd.can_generate_proposal("patch")
    assert not sd.can_generate_proposal("final")


def test_can_commit_resource() -> None:
    sd = StageDefinition(
        name="finalize",
        can_commit_directly=True,
        commit_resource_kinds=frozenset({"chapter", "canon"}),
    )
    assert sd.can_commit_resource("chapter")
    assert sd.can_commit_resource("canon")
    assert not sd.can_commit_resource("tts_audio")

    # can_commit_directly=False -> never allowed
    sd2 = StageDefinition(
        name="repair",
        can_commit_directly=False,
        commit_resource_kinds=frozenset({"chapter"}),
    )
    assert not sd2.can_commit_resource("chapter")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_has_all_stages() -> None:
    expected = {
        "bridge",
        "plan",
        "draft",
        "wave",
        "review",
        "repair",
        "polish",
        "humanize",
        "finalize",
        "canon_memory",
        "tts_script",
        "tts_synthesis",
        "tts_assembly",
    }
    assert set(STAGE_DEFINITIONS.keys()) == expected


def test_get_stage_definition() -> None:
    sd = get_stage_definition("finalize")
    assert sd is not None
    assert sd.name == "finalize"
    assert "canon" in sd.commit_resource_kinds

    assert get_stage_definition("nonexistent") is None


def test_list_stage_names() -> None:
    names = list_stage_names()
    assert "finalize" in names
    assert "tts_synthesis" in names
    assert len(names) == 13


# ---------------------------------------------------------------------------
# Stage DAG (acyclic check)
# ---------------------------------------------------------------------------


def test_stage_dag_acyclic() -> None:
    """The stage DAG must be acyclic (no cycles)."""
    # Simple cycle detection via DFS
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


def test_stage_dag_edges_exist() -> None:
    """All DAG edge targets must be registered stages."""
    for source, targets in STAGE_DAG_EDGES.items():
        assert source in STAGE_DEFINITIONS, f"DAG source '{source}' not in definitions"
        for target in targets:
            assert target in STAGE_DEFINITIONS, f"DAG target '{target}' not in definitions"


# ---------------------------------------------------------------------------
# StageHarness - advisory mode (default)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_enforcement_mode():
    """Reset to advisory mode after each test."""
    set_enforcement_mode("advisory")
    yield
    set_enforcement_mode("advisory")


def test_harness_advisory_mode_default() -> None:
    assert get_enforcement_mode() == "advisory"


def test_harness_check_input_artifact_allowed() -> None:
    harness = StageHarness("draft")
    result = harness.check_input_artifact("plan")
    assert result.passed


def test_harness_check_input_artifact_denied_advisory() -> None:
    """In advisory mode, denied checks log but don't raise."""
    harness = StageHarness("draft")
    result = harness.check_input_artifact("project_spec")
    assert not result.passed
    assert result.violation == "unauthorized_input_artifact"


def test_harness_check_read_project_spec() -> None:
    """No stage should be able to read full project_spec (by default)."""
    for name in list_stage_names():
        harness = StageHarness(name)
        result = harness.check_read_project_spec()
        # All stages have can_read_full_project_spec=False by default
        assert not result.passed, f"Stage '{name}' should not read project_spec"


def test_harness_check_commit_allowed() -> None:
    harness = StageHarness("finalize")
    result = harness.check_commit("chapter")
    assert result.passed


def test_harness_check_commit_denied() -> None:
    """Repair stage cannot commit directly."""
    harness = StageHarness("repair")
    result = harness.check_commit("chapter")
    assert not result.passed
    assert result.violation == "unauthorized_commit"


def test_harness_check_commit_wrong_resource() -> None:
    """Draft can commit chapter but not canon."""
    harness = StageHarness("draft")
    assert harness.check_commit("chapter").passed
    assert not harness.check_commit("canon").passed


def test_harness_check_canon_commit_hard_gate_failed() -> None:
    """Canon commit should fail if hard gates not passed."""
    harness = StageHarness("finalize")
    result = harness.check_canon_commit(hard_gates_passed=False, watermark_match=True)
    assert not result.passed
    assert result.violation == "canon_hard_gate_failed"


def test_harness_check_canon_commit_watermark_mismatch() -> None:
    harness = StageHarness("finalize")
    result = harness.check_canon_commit(hard_gates_passed=True, watermark_match=False)
    assert not result.passed
    assert result.violation == "canon_watermark_mismatch"


def test_harness_check_canon_commit_success() -> None:
    harness = StageHarness("finalize")
    result = harness.check_canon_commit(hard_gates_passed=True, watermark_match=True)
    assert result.passed


def test_harness_check_canon_commit_wrong_stage() -> None:
    """Non-finalize stages cannot commit canon."""
    harness = StageHarness("draft")
    result = harness.check_canon_commit(hard_gates_passed=True, watermark_match=True)
    assert not result.passed
    assert result.violation == "unauthorized_canon_commit"


# ---------------------------------------------------------------------------
# Text modification checks
# ---------------------------------------------------------------------------


def test_harness_check_text_modification_full_allowed() -> None:
    harness = StageHarness("draft")
    result = harness.check_text_modification("full")
    assert result.passed


def test_harness_check_text_modification_full_denied_for_patch_only() -> None:
    harness = StageHarness("repair")
    result = harness.check_text_modification("full")
    assert not result.passed
    assert result.violation == "unauthorized_full_rewrite"


def test_harness_check_text_modification_patch_allowed_for_full_stage() -> None:
    harness = StageHarness("draft")
    result = harness.check_text_modification("patch")
    assert result.passed


def test_harness_check_text_modification_denied_for_readonly() -> None:
    harness = StageHarness("review")
    result = harness.check_text_modification("full")
    assert not result.passed
    assert result.violation == "unauthorized_text_modification"


# ---------------------------------------------------------------------------
# Required validations
# ---------------------------------------------------------------------------


def test_harness_check_required_validations_pass() -> None:
    harness = StageHarness("finalize")
    result = harness.check_required_validations(
        {
            "hard_gates_passed",
            "canon_watermark_match",
            "report_hash_consistency",
        }
    )
    assert result.passed


def test_harness_check_required_validations_missing() -> None:
    harness = StageHarness("finalize")
    result = harness.check_required_validations({"hard_gates_passed"})
    assert not result.passed
    assert result.violation == "missing_required_validations"
    assert "canon_watermark_match" in result.detail


def test_harness_check_required_validations_repair() -> None:
    """Repair stage requires source_text_hash_match and window_bounds."""
    harness = StageHarness("repair")
    assert not harness.check_required_validations(set()).passed
    assert harness.check_required_validations(
        {
            "source_text_hash_match",
            "window_bounds",
        }
    ).passed


# ---------------------------------------------------------------------------
# Enforce mode
# ---------------------------------------------------------------------------


def test_harness_enforce_mode_raises() -> None:
    """In enforce mode, violations raise HarnessViolationError."""
    set_enforcement_mode("enforce")
    harness = StageHarness("draft")
    with pytest.raises(HarnessViolationError) as exc_info:
        harness.check_input_artifact("project_spec")
    assert exc_info.value.stage_name == "draft"
    assert exc_info.value.violation == "unauthorized_input_artifact"


def test_harness_enforce_mode_allows_valid() -> None:
    """In enforce mode, valid checks don't raise."""
    set_enforcement_mode("enforce")
    harness = StageHarness("draft")
    result = harness.check_input_artifact("plan")
    assert result.passed


def test_harness_enforce_mode_canon_commit() -> None:
    set_enforcement_mode("enforce")
    harness = StageHarness("finalize")
    with pytest.raises(HarnessViolationError):
        harness.check_canon_commit(hard_gates_passed=False, watermark_match=True)


# ---------------------------------------------------------------------------
# TTS isolation
# ---------------------------------------------------------------------------


def test_tts_cannot_commit_canon() -> None:
    """TTS stages must never be able to commit canon or chapter."""
    for tts_stage in ["tts_script", "tts_synthesis", "tts_assembly"]:
        harness = StageHarness(tts_stage)
        assert not harness.check_commit("canon").passed
        assert not harness.check_commit("chapter").passed


def test_tts_can_commit_audio() -> None:
    harness = StageHarness("tts_synthesis")
    assert harness.check_commit("tts_audio").passed


def test_tts_script_can_only_read_committed() -> None:
    """TTS script stage can only read 'final' (committed chapter)."""
    harness = StageHarness("tts_script")
    assert harness.check_input_artifact("final").passed
    assert not harness.check_input_artifact("draft").passed
    assert not harness.check_input_artifact("wave").passed


# ---------------------------------------------------------------------------
# Unknown stage (no definition)
# ---------------------------------------------------------------------------


def test_harness_unknown_stage_allows_all() -> None:
    """Stages without a definition pass all checks (backward compat)."""
    harness = StageHarness("unknown_stage")
    assert harness.check_input_artifact("anything").passed
    assert harness.check_commit("canon").passed
    assert harness.check_text_modification("full").passed
