from __future__ import annotations

from novel_forge.workspace.book_ops.execution_book_editorial import (
    _build_editorial_revision_queue,
)


def test_editorial_revision_queue_preserves_domain_and_source_lineage() -> None:
    payload = {
        "findings": [
            {
                "issue_type": "voice_convergence",
                "chapter_number": 2,
                "summary": "人物声音趋同",
                "evidence": ["两人的对白句式完全一致"],
            }
        ],
        "metrics": {
            "structured_revision_plan": {
                "actions": [
                    {
                        "action_type": "rewrite_language",
                        "priority": "high",
                        "chapter_range": [2],
                        "target": "voice_convergence",
                        "rationale": "人物声音趋同",
                        "instruction": "保持事件不变，只调整人物措辞与节奏。",
                    }
                ]
            }
        },
    }

    queue = _build_editorial_revision_queue(
        payload=payload,
        chapter_hashes={"1": "hash-1", "2": "hash-2"},
    )

    assert len(queue) == 1
    item = queue[0]
    assert item["audit_domain"] == "editorial"
    assert item["status"] == "manual_review"
    assert item["target_chapters"] == [2]
    assert item["source_text_hashes"] == {"2": "hash-2"}
    assert item["impact_scope"]["requires_state_replay"] is True
    assert item["evidence"] == ["两人的对白句式完全一致"]
