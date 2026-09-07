"""Tests for issue pool content-hash dual-gated with TTL filtering.

These tests verify the content-hash (chapter_text_hash + canon_state_hash)
AND-gated filter that runs alongside the existing TTL filter in the issue
pool loader.  Race-condition semantics: content-hash is a snapshot taken at
persist time; if chapter text changes during audit the hash mismatches and
the pool entry is correctly treated as stale.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class _FakeStorage:
    """Minimal storage double matching FileSystemStorage's load_json/exists."""

    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads

    def exists(self, path: str) -> bool:
        return path in self._payloads

    def load_json(self, path: str) -> dict[str, Any]:
        return self._payloads[path]


class _FakeLayout:
    """Minimal layout double returning predictable paths."""

    def continuity_report_path(self, chapter_number: int) -> str:
        return f"continuity-{chapter_number}.json"

    def chapter_causal_report_path(self, chapter_number: int) -> str:
        return f"causal-{chapter_number}.json"


# Shared test data
_NOW = datetime.now(timezone.utc)
_TTL_100H = (int(_NOW.timestamp()) + 1000000)  # effectively infinite
_H1 = "aaa"  # chapter-text hash for report
_H1_PRIME = "bbb"  # different chapter-text hash (chapter changed)
_H2 = "ccc"  # canon-state hash for report
_H2_PRIME = "ddd"  # different canon-state hash (canon changed)


def _report_payload(
    *,
    chapter_text_hash: str | None = "aaa",
    canon_state_hash: str | None = "ccc",
    created_at: str | None = None,
    issues: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a continuity/causal report dict with optional hash metadata."""
    if created_at is None:
        created_at = _NOW.isoformat()
    payload: dict[str, Any] = {
        "created_at": created_at,
        "issues": issues or [
            {
                "issue_type": "continuity",
                "severity": "warning",
                "summary": "Test issue",
                "location": "para 12",
                "evidence": "Details here",
            }
        ],
    }
    if chapter_text_hash is not None:
        payload["source_text_hash"] = chapter_text_hash
    if canon_state_hash is not None:
        payload["canon_state_hash"] = canon_state_hash
    return payload


# ============================================================================
# Test 1: chapter text unchanged + canon state unchanged + within TTL → reuse
# ============================================================================

class TestContentHashAndTtlMatch:
    """When both content-hashes match AND TTL passes → entry reused."""

    def test_reuse_when_both_hashes_match_and_within_ttl(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=_H1,
                canon_state_hash=_H2,
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,
            chapter_text_hash_by_chapter={1: _H1},
            canon_state_hash=_H2,
            legacy_grace_hours=0,
        )
        assert len(pool) == 1
        assert pool[0]["chapter_number"] == 1


# ============================================================================
# Test 2: chapter text changed + within TTL → filtered (content-hash mismatch)
# ============================================================================

class TestContentHashMismatchChapter:
    """Chapter-text hash mismatch filters even when within TTL."""

    def test_filtered_when_chapter_text_changed_and_within_ttl(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=_H1,  # stored hash
                canon_state_hash=_H2,
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,  # within TTL
            chapter_text_hash_by_chapter={1: _H1_PRIME},  # current text has different hash
            canon_state_hash=_H2,
            legacy_grace_hours=0,
        )
        assert pool == []


# ============================================================================
# Test 3: TTL expired + content-hash match → filtered (TTL gate)
# ============================================================================

class TestTtlExpiryWithHashMatch:
    """TTL expiry filters even when content-hash matches."""

    def test_filtered_when_ttl_expired_and_hash_matches(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        old_time = (_NOW - timedelta(hours=100)).isoformat()
        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=_H1,
                canon_state_hash=_H2,
                created_at=old_time,
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=24,  # report is 100h old, TTL is 24h
            chapter_text_hash_by_chapter={1: _H1},  # text hash matches
            canon_state_hash=_H2,  # canon hash matches
            legacy_grace_hours=0,
        )
        assert pool == []


# ============================================================================
# Test 4: Legacy report missing hash fields + within grace period → reuse
# ============================================================================

class TestLegacyReportGracePeriod:
    """Reports created before content-hash was stored, within grace → reused."""

    def test_reuse_legacy_report_within_grace_period(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        # Report with NO hash metadata, but created recently (within grace)
        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=None,   # legacy: no hash
                canon_state_hash=None,    # legacy: no hash
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,
            chapter_text_hash_by_chapter={1: _H1},
            canon_state_hash=_H2,
            legacy_grace_hours=24,  # report is 0h old, grace is 24h
        )
        # Legacy report within grace period should be reused
        assert len(pool) == 1
        assert pool[0]["chapter_number"] == 1

    def test_filter_legacy_report_outside_grace_period(self) -> None:
        """Legacy report without hash metadata, outside grace → filtered."""
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        old_time = (_NOW - timedelta(hours=48)).isoformat()
        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=None,
                canon_state_hash=None,
                created_at=old_time,
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,  # within TTL
            chapter_text_hash_by_chapter={1: _H1},
            canon_state_hash=_H2,
            legacy_grace_hours=24,  # report is 48h old, grace is 24h → outside grace
        )
        # Legacy report outside grace period should be filtered
        assert pool == []


# ============================================================================
# Test 5: canon state changed + chapter text unchanged + within TTL → filtered
# ============================================================================

class TestCanonStateHashMismatch:
    """Canon-state-hash mismatch filters even when chapter-text hash matches."""

    def test_filtered_when_canon_state_changed_and_within_ttl(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        storage = _FakeStorage({
            "continuity-1.json": _report_payload(
                chapter_text_hash=_H1,
                canon_state_hash=_H2,  # stored canon hash
            ),
        })
        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,  # within TTL
            chapter_text_hash_by_chapter={1: _H1},  # text hash matches
            canon_state_hash=_H2_PRIME,  # current canon state hash differs
            legacy_grace_hours=0,
        )
        assert pool == []
