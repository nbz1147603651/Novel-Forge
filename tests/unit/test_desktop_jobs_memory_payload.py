"""Tests for desktop job memory payload compaction."""

from __future__ import annotations

from novel_forge.desktop.jobs import _compact_payload


def test_compact_payload_keeps_memory_updated_fields() -> None:
    payload = _compact_payload(
        "memory_updated",
        {
            "chapter": 3,
            "indexed_chapters": 3,
            "last_indexed_chapter": 3,
            "cached_summaries": 1,
            "save_success": True,
            "memory_module_status": {"episodic_enabled": True},
            "outline_stats": {"total_outline_entries": 5},
            "motifs": [
                {
                    "motif_id": "m1",
                    "category": "symbol",
                    "description": "怀表",
                    "occurrence_count": 4,
                    "last_chapter": 3,
                    "ignored": "x",
                }
            ],
            "motif_suggestions": [{"motif_id": "m1", "suggestion": "继续强化", "priority": "high"}],
            "repetition_warnings": [{"motif_id": "m1", "warning_type": "repeat", "message": "重复", "severity": "medium"}],
            "chapter_motifs": {"3": ["m1"], "bad": ["ignored"]},
            "unresolved_questions": ["胡杨信背后的密令是什么？"],
        },
    )

    assert payload["chapter"] == 3
    assert payload["indexed_chapters"] == 3
    assert payload["save_success"] is True
    assert payload["motifs"][0]["motif_id"] == "m1"
    assert payload["chapter_motifs"] == {"3": ["m1"]}
    assert payload["unresolved_questions"] == ["胡杨信背后的密令是什么？"]
    assert "ignored" not in payload["motifs"][0]


def test_compact_payload_keeps_memory_stage_status_snapshot() -> None:
    payload = _compact_payload(
        "memory_indexing_complete",
        {
            "chapter": 5,
            "success": True,
            "last_indexed": 5,
            "memory_status": {
                "chapter": 5,
                "indexed_chapters": 5,
                "motifs": [],
                "motif_suggestions": [],
                "repetition_warnings": [],
            },
        },
    )

    assert payload["chapter"] == 5
    assert payload["success"] is True
    assert payload["memory_status"]["indexed_chapters"] == 5


def test_compact_payload_keeps_standardized_chapter_postprocess_fields() -> None:
    payload = _compact_payload(
        "chapter_postprocess",
        {
            "project_id": "long_demo",
            "chapter_number": 7,
            "source": "repair_causal",
            "status": "completed",
            "task": "postprocess",
            "message": "done",
            "text_changed": True,
            "extra_ignored": "x",
        },
    )

    assert payload == {
        "project_id": "long_demo",
        "chapter_number": 7,
        "source": "repair_causal",
        "status": "completed",
        "task": "postprocess",
        "message": "done",
        "text_changed": 1,
    }


def test_compact_payload_keeps_stage_memory_context_snapshot() -> None:
    payload = _compact_payload(
        "memory_draft_context",
        {
            "stage": "draft",
            "chapter_number": 8,
            "requested_layers": [
                "L0_identity",
                "L1_core_memory",
                "L2_on_demand",
                "L3_deep_search",
            ],
            "resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
            "counts": {
                "relevant_history": 2,
                "previous_chapter_events": 1,
                "motif_suggestions": 3,
                "ignored": 99,
            },
            "flags": {
                "has_critique_context": True,
                "has_outline_context": False,
                "ignored": True,
            },
            "sources": {
                "relevant_history": "prefetched",
                "motif_suggestions": "generated",
                "ignored": "x",
            },
            "history_reused": True,
        },
    )

    assert payload["stage"] == "draft"
    assert payload["chapter_number"] == 8
    assert payload["resolved_layers"] == ["L0_identity", "L1_core_memory", "L2_on_demand"]
    assert payload["counts"] == {
        "relevant_history": 2,
        "previous_chapter_events": 1,
        "motif_suggestions": 3,
    }
    assert payload["flags"] == {
        "has_critique_context": True,
        "has_outline_context": False,
    }
    assert payload["sources"] == {
        "relevant_history": "prefetched",
        "motif_suggestions": "generated",
    }
    assert payload["history_reused"] is True
