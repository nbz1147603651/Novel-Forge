"""Tests for artifact diff helper canonicalization."""

from __future__ import annotations

from tests.helpers.artifact_diff import canonical_json_bytes


def test_canonical_json_preserves_business_ids() -> None:
    payload = {
        "project_id": "project-a",
        "character_id": "char-1",
        "claim_id": "claim-1",
        "entity_id": "entity-1",
        "run_id": "run-volatile",
        "trace_id": "trace-volatile",
    }

    canonical = canonical_json_bytes(payload).decode("utf-8")

    assert "project_id" in canonical
    assert "character_id" in canonical
    assert "claim_id" in canonical
    assert "entity_id" in canonical
    assert "run_id" not in canonical
    assert "trace_id" not in canonical


def test_canonical_json_normalizes_paths_and_uuid_values() -> None:
    payload = {
        "message": "written to /private/tmp/demo/abc.json",
        "stable_ref": "550e8400-e29b-41d4-a716-446655440000",
    }

    canonical = canonical_json_bytes(payload).decode("utf-8")

    assert "/private/tmp/demo" not in canonical
    assert "<PATH>" in canonical
    assert "550e8400" not in canonical
    assert "<UUID>" in canonical
