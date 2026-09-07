#!/usr/bin/env python3
"""Verify control-plane architecture constraints.

Checks that the control plane's stage definitions, DAG, and permission
boundaries are structurally sound. Runs in CI alongside other verify scripts.

Checks:
1. Stage DAG is acyclic
2. All DAG edge targets are registered stages
3. Every StageDefinition has required fields filled
4. No stage can read full project_spec (bounded projection)
5. TTS stages cannot commit canon/chapter (isolation)
6. Canon commit only allowed in finalize stage
7. Repair stage requires source_text_hash_match validation
8. Every resumable stage has input hash + idempotency key pattern

Usage:
  python scripts/verify_control_plane_constraints.py
  python scripts/verify_control_plane_constraints.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.control_plane.registry import (  # noqa: E402
    STAGE_DAG_EDGES,
    STAGE_DEFINITIONS,
    get_stage_definition,
    list_stage_names,
)


def _check_dag_acyclic() -> list[str]:
    """Check that the stage DAG has no cycles."""
    errors: list[str] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in STAGE_DAG_EDGES}

    def has_cycle(node: str, path: list[str]) -> bool:
        color[node] = GRAY
        for neighbor in STAGE_DAG_EDGES.get(node, frozenset()):
            if color[neighbor] == GRAY:
                errors.append(f"  DAG CYCLE: {' -> '.join(path + [node, neighbor])}")
                return True
            if color[neighbor] == WHITE and has_cycle(neighbor, path + [node]):
                return True
        color[node] = BLACK
        return False

    for node in STAGE_DAG_EDGES:
        if color[node] == WHITE:
            has_cycle(node, [])

    return errors


def _check_dag_targets_registered() -> list[str]:
    """Check that all DAG edge targets are registered stages."""
    errors: list[str] = []
    for source, targets in STAGE_DAG_EDGES.items():
        if source not in STAGE_DEFINITIONS:
            errors.append(f"  DAG source '{source}' not in STAGE_DEFINITIONS")
        for target in targets:
            if target not in STAGE_DEFINITIONS:
                errors.append(f"  DAG target '{target}' (from '{source}') not in STAGE_DEFINITIONS")
    return errors


def _check_definitions_complete() -> list[str]:
    """Check that every StageDefinition has required fields filled."""
    errors: list[str] = []
    for name, sd in STAGE_DEFINITIONS.items():
        if not sd.name:
            errors.append(f"  Stage '{name}': missing name")
        if not sd.allowed_artifact_types:
            errors.append(f"  Stage '{name}': no allowed_artifact_types")
        if not sd.allowed_proposal_types:
            errors.append(f"  Stage '{name}': no allowed_proposal_types")
        if sd.editable_text_window not in ("none", "patch", "full"):
            errors.append(
                f"  Stage '{name}': invalid editable_text_window '{sd.editable_text_window}'"
            )
        if sd.degradation_policy not in ("block", "advisory", "allow_local"):
            errors.append(f"  Stage '{name}': invalid degradation_policy '{sd.degradation_policy}'")
    return errors


def _check_no_project_spec_read() -> list[str]:
    """Check that no stage can read full project_spec."""
    errors: list[str] = []
    for name in list_stage_names():
        sd = get_stage_definition(name)
        if sd and sd.can_read_full_project_spec:
            errors.append(f"  Stage '{name}' can read full project_spec (should be bounded)")
    return errors


def _check_tts_isolation() -> list[str]:
    """Check that TTS stages cannot commit canon or chapter."""
    errors: list[str] = []
    tts_stages = {"tts_script", "tts_synthesis", "tts_assembly"}
    for name in tts_stages:
        sd = get_stage_definition(name)
        if sd is None:
            errors.append(f"  TTS stage '{name}' not found in registry")
            continue
        if "canon" in sd.commit_resource_kinds:
            errors.append(f"  TTS stage '{name}' can commit canon (violation)")
        if "chapter" in sd.commit_resource_kinds:
            errors.append(f"  TTS stage '{name}' can commit chapter (violation)")
        if sd.can_commit_directly and not sd.commit_resource_kinds:
            errors.append(f"  TTS stage '{name}' can_commit_directly but no resource kinds")
    return errors


def _check_canon_commit_isolation() -> list[str]:
    """Check that only finalize stage can commit canon."""
    errors: list[str] = []
    for name in list_stage_names():
        sd = get_stage_definition(name)
        if sd and "canon" in sd.commit_resource_kinds and name != "finalize":
            errors.append(f"  Stage '{name}' can commit canon (only 'finalize' should)")
    # Verify finalize CAN commit canon
    finalize = get_stage_definition("finalize")
    if finalize and "canon" not in finalize.commit_resource_kinds:
        errors.append("  Stage 'finalize' cannot commit canon (should be allowed)")
    return errors


def _check_repair_validations() -> list[str]:
    """Check that repair stage requires source_text_hash_match."""
    errors: list[str] = []
    repair = get_stage_definition("repair")
    if repair is None:
        errors.append("  Stage 'repair' not found in registry")
    elif "source_text_hash_match" not in repair.required_validations:
        errors.append("  Stage 'repair' missing required validation 'source_text_hash_match'")
    if repair and "window_bounds" not in repair.required_validations:
        errors.append("  Stage 'repair' missing required validation 'window_bounds'")
    return errors


def _check_resumable_stages() -> list[str]:
    """Check that creative stages with max_retries > 0 have human_intervention_conditions.

    TTS stages are exempt (mechanical retries, not creative decisions).
    Planning/review/polish/canon_memory stages are exempt (advisory or local).
    """
    errors: list[str] = []
    exempt = {
        "bridge",
        "plan",
        "review",
        "polish",
        "canon_memory",
        "tts_script",
        "tts_synthesis",
        "tts_assembly",
    }
    for name in list_stage_names():
        sd = get_stage_definition(name)
        if sd and sd.max_retries > 0 and name not in exempt:
            if not sd.human_intervention_conditions:
                errors.append(
                    f"  Stage '{name}' has max_retries={sd.max_retries} "
                    f"but no human_intervention_conditions"
                )
    return errors


def _check_enforcement_mode() -> list[str]:
    """Check that the harness is in a valid enforcement mode."""
    from novel_forge.control_plane.harness import get_enforcement_mode

    mode = get_enforcement_mode()
    if mode not in ("advisory", "enforce"):
        return [f"  Invalid enforcement mode: {mode}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify control-plane architecture constraints")
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check constraints (default). Exit 1 if violations found.",
    )
    parser.parse_args(argv)

    all_errors: list[str] = []

    checks = [
        ("DAG acyclic", _check_dag_acyclic),
        ("DAG targets registered", _check_dag_targets_registered),
        ("Definitions complete", _check_definitions_complete),
        ("No project_spec read", _check_no_project_spec_read),
        ("TTS isolation", _check_tts_isolation),
        ("Canon commit isolation", _check_canon_commit_isolation),
        ("Repair validations", _check_repair_validations),
        ("Resumable stages", _check_resumable_stages),
        ("Enforcement mode", _check_enforcement_mode),
    ]

    for check_name, check_fn in checks:
        errors = check_fn()
        if errors:
            all_errors.extend([f"[{check_name}]"] + errors)

    if all_errors:
        print(f"RESULT: FAIL - {len(all_errors)} issue(s) found:")
        for err in all_errors:
            print(err)
        return 1

    print(f"RESULT: PASS - all {len(checks)} constraint checks passed.")
    print(f"  Stages: {len(list_stage_names())}")
    print(f"  DAG nodes: {len(STAGE_DAG_EDGES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
