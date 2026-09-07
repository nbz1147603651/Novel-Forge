"""Tests for project-level issue ledger persistence and injection."""

from __future__ import annotations

from novel_forge.core.schemas.issue_ledger import ProjectIssueLedgerEntry
from novel_forge.pipeline.long.services.issue_ledger import IssueLedgerService


def test_issue_ledger_append_dedup_and_resolve(tmp_path) -> None:
    service = IssueLedgerService(tmp_path)

    service.append_entries(
        [
            ProjectIssueLedgerEntry(
                issue_id="ISS-1",
                category="continuity",
                severity="high",
                source="volume_audit",
                chapter_range=[8, 9],
                summary="original summary",
            ),
            ProjectIssueLedgerEntry(
                issue_id="ISS-2",
                category="summary_drift",
                severity="critical",
                source="summary_drift",
                chapter_range=[12],
                summary="critical issue",
            ),
        ]
    )
    service.append_entries(
        [
            ProjectIssueLedgerEntry(
                issue_id="ISS-1",
                category="continuity",
                severity="medium",
                source="volume_audit",
                chapter_range=[9],
                summary="updated summary",
            )
        ]
    )

    entries = service.load_entries()
    assert [entry.issue_id for entry in entries] == ["ISS-1", "ISS-2"]
    assert entries[0].severity == "medium"
    assert entries[0].summary == "updated summary"

    injected = service.load_prev_known_issues(current_chapter=12)
    assert [entry["summary"] for entry in injected] == [
        "critical issue",
        "updated summary",
    ]

    assert service.resolve_entry("ISS-2", resolved_chapter=13) is True
    snapshot = service.load_snapshot()
    assert [entry.issue_id for entry in snapshot.active] == ["ISS-1"]
    assert [entry.issue_id for entry in snapshot.archived] == ["ISS-2"]


def test_issue_ledger_skips_corrupt_lines(tmp_path) -> None:
    service = IssueLedgerService(tmp_path)
    service.path.write_text(
        '{"issue_id":"ISS-1","summary":"valid"}\nnot-json\n',
        encoding="utf-8",
    )

    entries = service.load_entries()
    assert [entry.issue_id for entry in entries] == ["ISS-1"]
