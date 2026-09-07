from __future__ import annotations

import pytest

from novel_forge.pipeline.context_governance import (
    CompletePartitionError,
    partition_complete,
    select_ranked_evidence,
)


def test_complete_partition_preserves_order_and_coverage() -> None:
    items = list(range(1, 11))
    partitions = partition_complete(
        items,
        build_payload=lambda batch: {"items": batch, "padding": "x" * 20},
        char_budget=60,
        max_items=3,
    )

    assert [item for partition in partitions for item in partition.items] == items
    assert all(len(partition.items) <= 3 for partition in partitions)


def test_complete_partition_rejects_single_oversized_authority_item() -> None:
    with pytest.raises(CompletePartitionError, match="single chapter"):
        partition_complete(
            ["x" * 200],
            build_payload=lambda batch: {"chapters": batch},
            char_budget=50,
            max_items=4,
            item_label="chapter",
        )


def test_ranked_evidence_uses_one_shared_budget_after_mandatory_items() -> None:
    selection = select_ranked_evidence(
        ["history-1", "history-2", "history-3"],
        mandatory_items=["entity-1"],
        token_budget=5,
        estimate_tokens=lambda _item: 2,
        identity=str,
    )

    assert selection.selected == ("entity-1", "history-1")
    assert selection.omitted_count == 2
    assert selection.used_tokens == 4
