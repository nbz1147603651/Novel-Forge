"""Tests for issue pool TTL cleanup mechanism."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class _FakeStorage:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads

    def exists(self, path: str) -> bool:
        return path in self._payloads

    def load_json(self, path: str) -> dict[str, Any]:
        return self._payloads[path]


class _FakeLayout:
    def continuity_report_path(self, chapter_number: int) -> str:
        return f"continuity-{chapter_number}.json"

    def chapter_causal_report_path(self, chapter_number: int) -> str:
        return f"causal-{chapter_number}.json"


class TestIsEntryWithinTtl:
    """Test _is_entry_within_ttl function."""

    def _is_entry_within_ttl(self, entry: dict, cutoff: datetime) -> bool:
        """Reproduce TTL check logic for testing."""
        created_at_str = entry.get("created_at", "")
        if not created_at_str:
            return True
        try:
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return created_at >= cutoff
        except (ValueError, TypeError):
            return True

    def test_entry_within_ttl_is_kept(self) -> None:
        """Entry with created_at within TTL should be kept."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        entry = {"created_at": now.isoformat()}
        assert self._is_entry_within_ttl(entry, cutoff) is True

    def test_entry_outside_ttl_is_filtered(self) -> None:
        """Entry with created_at older than TTL should be filtered."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        old_time = now - timedelta(hours=100)
        entry = {"created_at": old_time.isoformat()}
        assert self._is_entry_within_ttl(entry, cutoff) is False

    def test_entry_without_created_at_is_kept(self) -> None:
        """Entry without created_at (backward compat) should be kept."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        entry: dict = {}
        assert self._is_entry_within_ttl(entry, cutoff) is True

    def test_entry_with_empty_created_at_is_kept(self) -> None:
        """Entry with empty created_at should be kept (backward compat)."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        entry = {"created_at": ""}
        assert self._is_entry_within_ttl(entry, cutoff) is True

    def test_entry_with_invalid_created_at_is_kept(self) -> None:
        """Entry with invalid created_at format should be kept for safety."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        entry = {"created_at": "not-a-valid-timestamp"}
        assert self._is_entry_within_ttl(entry, cutoff) is True

    def test_entry_with_naive_datetime_is_kept(self) -> None:
        """Entry with timezone-naive created_at should be treated as UTC."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        # Create naive datetime (no timezone info)
        recent_naive = (now - timedelta(hours=10)).replace(tzinfo=None)
        entry = {"created_at": recent_naive.isoformat()}
        assert self._is_entry_within_ttl(entry, cutoff) is True

    def test_entry_at_exact_cutoff_is_kept(self) -> None:
        """Entry at exact cutoff time should be kept."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)
        entry = {"created_at": cutoff.isoformat()}
        assert self._is_entry_within_ttl(entry, cutoff) is True


class TestCompactIssuePoolEntry:
    """Test _compact_issue_pool_entry with created_at."""

    def test_compact_entry_includes_created_at(self) -> None:
        """Compact entry should include created_at field with ISO timestamp."""
        from novel_forge.workspace.book_ops.execution_book_common import _compact_issue_pool_entry

        issue = {
            "issue_type": "naming",
            "severity": "warning",
            "summary": "Test issue",
            "location": "ch1",
            "evidence": "evidence",
            "fix_actions": ["fix1"],
        }
        entry = _compact_issue_pool_entry(
            chapter_number=1,
            lane="continuity",
            index=0,
            issue=issue,
        )
        assert entry is not None
        assert "created_at" in entry
        # Verify it's a valid ISO format timestamp
        created_at = entry["created_at"]
        datetime.fromisoformat(created_at)  # Should not raise

    def test_compact_entry_inherits_report_created_at(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import _compact_issue_pool_entry

        created_at = "2026-05-30T12:00:00Z"
        entry = _compact_issue_pool_entry(
            chapter_number=1,
            lane="causal",
            index=0,
            issue={"issue_type": "timeline", "severity": "warning"},
            created_at=created_at,
        )

        assert entry is not None
        assert entry["created_at"] == "2026-05-30T12:00:00+00:00"


class TestIssuePoolTtlFiltering:
    """Test issue pool TTL filtering integration."""

    def test_ttl_hours_parameter_default(self) -> None:
        """Default TTL should be 72 hours."""
        import inspect

        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        sig = inspect.signature(_load_issue_panel_pool_for_chapters)
        ttl_param = sig.parameters["ttl_hours"]
        assert ttl_param.default == 72

    def test_created_at_old_entries_filtered(self) -> None:
        """Entries older than TTL should be filtered from pool."""
        from novel_forge.workspace.book_ops.execution_book_common import _is_entry_within_ttl

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)

        # Create an entry that's 100 hours old
        old_entry = {
            "created_at": (now - timedelta(hours=100)).isoformat(),
            "chapter_number": 1,
            "lane": "continuity",
        }
        assert _is_entry_within_ttl(old_entry, cutoff) is False

    def test_created_at_recent_entries_kept(self) -> None:
        """Entries within TTL should be kept in pool."""
        from novel_forge.workspace.book_ops.execution_book_common import _is_entry_within_ttl

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)

        # Create an entry that's 10 hours old
        recent_entry = {
            "created_at": (now - timedelta(hours=10)).isoformat(),
            "chapter_number": 1,
            "lane": "continuity",
        }
        assert _is_entry_within_ttl(recent_entry, cutoff) is True

    def test_missing_created_at_backward_compat(self) -> None:
        """Entries without created_at should be kept (backward compatibility)."""
        from novel_forge.workspace.book_ops.execution_book_common import _is_entry_within_ttl

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=72)

        # Entry without created_at should be kept
        no_timestamp_entry: dict = {
            "chapter_number": 1,
            "lane": "continuity",
        }
        assert _is_entry_within_ttl(no_timestamp_entry, cutoff) is True

    def test_custom_ttl_hours(self) -> None:
        """Custom TTL hours should be respected."""
        from novel_forge.workspace.book_ops.execution_book_common import _is_entry_within_ttl

        now = datetime.now(timezone.utc)
        # Use 24 hour TTL
        cutoff_24h = now - timedelta(hours=24)

        # Entry that's 48 hours old
        entry_48h_old = {
            "created_at": (now - timedelta(hours=48)).isoformat(),
            "chapter_number": 1,
            "lane": "continuity",
        }
        # Should be filtered with 24h TTL
        assert _is_entry_within_ttl(entry_48h_old, cutoff_24h) is False
        # But kept with 72h TTL
        cutoff_72h = now - timedelta(hours=72)
        assert _is_entry_within_ttl(entry_48h_old, cutoff_72h) is True

    def test_load_issue_panel_pool_filters_report_created_at_by_ttl(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        now = datetime.now(timezone.utc)
        old_report_time = (now - timedelta(hours=100)).isoformat()
        storage = _FakeStorage(
            {
                "continuity-1.json": {
                    "created_at": old_report_time,
                    "issues": [
                        {
                            "issue_type": "naming",
                            "severity": "warning",
                            "summary": "old issue",
                        }
                    ],
                }
            }
        )

        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=72,
        )

        assert pool == []

    def test_load_issue_panel_pool_zero_ttl_disables_filtering(self) -> None:
        from novel_forge.workspace.book_ops.execution_book_common import (
            _load_issue_panel_pool_for_chapters,
        )

        now = datetime.now(timezone.utc)
        old_report_time = (now - timedelta(hours=100)).isoformat()
        storage = _FakeStorage(
            {
                "causal-1.json": {
                    "created_at": old_report_time,
                    "issues": [
                        {
                            "issue_type": "timeline",
                            "severity": "warning",
                            "summary": "old issue",
                        }
                    ],
                }
            }
        )

        pool = _load_issue_panel_pool_for_chapters(
            storage=storage,
            layout=_FakeLayout(),
            chapter_numbers=[1],
            ttl_hours=0,
        )

        assert len(pool) == 1
        assert pool[0]["summary"] == "old issue"
