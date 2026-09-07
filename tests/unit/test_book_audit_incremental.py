from __future__ import annotations

from novel_forge.workspace.book_ops.book_audit_incremental import (
    build_book_audit_input_manifest,
    plan_book_audit_reuse,
)


def _slices() -> list[dict[str, object]]:
    return [
        {
            "slice_id": "whole_book",
            "slice_kind": "whole_book",
            "chapters": [1, 2, 3, 4],
            "boundary_chapters": [],
            "focus_dimensions": ["timeline_arc"],
            "source_refs": [],
        },
        {
            "slice_id": "volume_1",
            "slice_kind": "volume",
            "chapters": [1, 2],
            "boundary_chapters": [],
            "focus_dimensions": ["character_arc"],
            "source_refs": [{"source": "outline.volumes", "index": 1}],
        },
        {
            "slice_id": "volume_2",
            "slice_kind": "volume",
            "chapters": [3, 4],
            "boundary_chapters": [],
            "focus_dimensions": ["character_arc"],
            "source_refs": [{"source": "outline.volumes", "index": 2}],
        },
    ]


def _manifest(hashes: dict[int, str]) -> dict[str, object]:
    return build_book_audit_input_manifest(
        project_id="book",
        chapter_hashes=hashes,
        audit_slices=_slices(),
        analysis_options={"analysis_mode": "full_text", "temperature": 0.2},
        context_hashes={"outline": "same", "kernel_context": "same"},
    )


def test_incremental_audit_reuses_only_unchanged_dimension_slices() -> None:
    previous_manifest = _manifest({1: "a", 2: "b", 3: "c", 4: "d"})
    previous = {
        "input_manifest": previous_manifest,
        "dimension_results": [
            {"dimension": "timeline_arc", "slice_id": "whole_book", "findings": []},
            {"dimension": "character_arc", "slice_id": "volume_1", "findings": []},
            {"dimension": "character_arc", "slice_id": "volume_2", "findings": []},
        ],
    }

    plan = plan_book_audit_reuse(
        previous_payload=previous,
        current_manifest=_manifest({1: "changed", 2: "b", 3: "c", 4: "d"}),
        current_slices=_slices(),
    )

    assert plan.status == "incremental"
    assert plan.changed_chapters == [1]
    assert plan.reusable_slice_ids == ["volume_2"]
    assert set(plan.invalidated_slice_ids) == {"whole_book", "volume_1"}
    assert [item["slice_id"] for item in plan.seeded_dimension_results["character_arc"]] == [
        "volume_2"
    ]


def test_incremental_audit_rejects_reuse_when_analysis_context_changes() -> None:
    previous = {
        "input_manifest": _manifest({1: "a", 2: "b", 3: "c", 4: "d"}),
        "dimension_results": [],
    }
    current = build_book_audit_input_manifest(
        project_id="book",
        chapter_hashes={1: "a", 2: "b", 3: "c", 4: "d"},
        audit_slices=_slices(),
        analysis_options={"analysis_mode": "full_text", "temperature": 0.9},
        context_hashes={"outline": "same", "kernel_context": "same"},
    )

    plan = plan_book_audit_reuse(
        previous_payload=previous,
        current_manifest=current,
        current_slices=_slices(),
    )

    assert plan.status == "full_refresh"
    assert plan.seeded_dimension_results == {}
    assert plan.reason == "analysis_context_changed"


def test_incremental_audit_treats_unsigned_history_as_legacy_unknown() -> None:
    plan = plan_book_audit_reuse(
        previous_payload={"dimension_results": []},
        current_manifest=_manifest({1: "a", 2: "b", 3: "c", 4: "d"}),
        current_slices=_slices(),
    )

    assert plan.status == "legacy_unknown"
    assert plan.reason == "previous_signature_missing"
