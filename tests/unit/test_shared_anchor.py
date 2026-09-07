from __future__ import annotations

from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor


def test_shared_evidence_anchor_has_stable_hash_for_same_sources() -> None:
    left = build_shared_evidence_anchor(
        "demo",
        {"premise": "同一前提", "ignored": "A"},
        source_keys=("premise",),
    )
    right = build_shared_evidence_anchor(
        "demo",
        {"premise": "同一前提", "ignored": "B"},
        source_keys=("premise",),
    )

    assert left["anchor_hash"] == right["anchor_hash"]
    assert left["shared_source_keys"] == ["premise"]


def test_shared_evidence_anchor_compacts_long_values() -> None:
    anchor = build_shared_evidence_anchor(
        "demo",
        {"chapter_text": "字" * 2000},
        max_string_chars=20,
    )

    assert anchor["evidence_excerpt"]["chapter_text"].endswith("...")
