"""Tests for improved dedup logic using MD5 hash of description."""

from __future__ import annotations

import hashlib
import time


def _make_parsed(issues: list[dict]) -> dict:
    """Helper to create a parsed_items entry."""
    return {"issues": issues, "repair_plan": [], "summary": "", "consistency_score": 0.9}


def _dedup_issues(parsed_items: list[dict]) -> list[dict]:
    """Reproduce the dedup logic from book_consistency_step.py for testing."""
    from novel_forge.pipeline.steps.book_consistency_step import _coerce_int

    issues: list[dict] = []
    seen_issue_keys: set[tuple[str, str, int, int, str]] = set()
    for parsed in parsed_items:
        for issue in parsed.get("issues", []) or []:
            if not isinstance(issue, dict):
                continue
            description = str(issue.get("description", "") or "").strip()
            desc_md5 = hashlib.md5(description.encode()).hexdigest()[:16]
            key = (
                str(issue.get("issue_id", "") or "").strip(),
                str(issue.get("category", "") or "").strip(),
                _coerce_int(issue.get("primary_chapter"), default=0),
                _coerce_int(issue.get("paragraph_index"), default=0),
                desc_md5,
            )
            if key in seen_issue_keys:
                continue
            seen_issue_keys.add(key)
            issues.append(issue)
    return issues


class TestImprovedDedup:
    """Test the improved dedup logic with MD5 hash."""

    def test_improved_dedup(self) -> None:
        """Two issues with same first 120 chars but different content should NOT be deduped."""
        # Create a common prefix of 120+ chars
        prefix = "这是一个很长的问题描述，" * 10  # 240 chars
        desc_a = prefix + "结尾A有额外的内容来区分"
        desc_b = prefix + "结尾B有不同的内容来区分"

        # Verify they share the same first 120 chars
        assert desc_a[:120] == desc_b[:120]
        # But have different MD5 hashes
        assert hashlib.md5(desc_a.encode()).hexdigest() != hashlib.md5(desc_b.encode()).hexdigest()

        issues_a = [{"issue_id": "ISS001", "description": desc_a, "category": "continuity", "primary_chapter": 1, "paragraph_index": 0}]
        issues_b = [{"issue_id": "ISS001", "description": desc_b, "category": "continuity", "primary_chapter": 1, "paragraph_index": 0}]

        result = _dedup_issues([_make_parsed(issues_a), _make_parsed(issues_b)])

        # Both should be kept (not deduped) because MD5 hashes differ
        assert len(result) == 2

    def test_same_issue_deduped(self) -> None:
        """Two identical issues should be deduped to one."""
        desc = "这是一个完全相同的问题描述"
        issue = {"issue_id": "ISS002", "description": desc, "category": "causal", "primary_chapter": 3, "paragraph_index": 5}

        result = _dedup_issues([_make_parsed([issue]), _make_parsed([dict(issue)])])

        assert len(result) == 1

    def test_different_category_not_deduped(self) -> None:
        """Same description, different category should NOT be deduped."""
        desc = "这是一个相同描述但类别不同的问题"
        issue_a = {"issue_id": "ISS003", "description": desc, "category": "continuity", "primary_chapter": 2, "paragraph_index": 1}
        issue_b = {"issue_id": "ISS003", "description": desc, "category": "causal", "primary_chapter": 2, "paragraph_index": 1}

        result = _dedup_issues([_make_parsed([issue_a]), _make_parsed([issue_b])])

        assert len(result) == 2

    def test_performance(self) -> None:
        """Dedup 1000 issues in <10ms."""
        issues_list = []
        for i in range(1000):
            issues_list.append({
                "issue_id": f"ISS{i:04d}",
                "description": f"问题描述内容第{i}条" + "x" * 50,
                "category": "continuity" if i % 2 == 0 else "causal",
                "primary_chapter": (i % 50) + 1,
                "paragraph_index": i % 10,
            })

        parsed = [_make_parsed(issues_list)]

        start = time.perf_counter()
        result = _dedup_issues(parsed)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(result) == 1000  # All unique
        assert elapsed_ms < 10, f"Dedup took {elapsed_ms:.2f}ms, expected <10ms"
