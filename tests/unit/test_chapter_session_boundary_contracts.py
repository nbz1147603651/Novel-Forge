"""Regression tests for chapter session boundary contracts.

This module ensures that all checkpoint result construction sites maintain
consistent field extraction patterns and metadata schemas. It guards against
the class of bugs where container/inner-field confusion leads to incorrect
data being surfaced in checkpoint results.

Key contracts verified:
1. All `chapter_exit_summary` fields in chapter session handlers use
   `summarize_exit_state(outcome)`; the handler must not unwrap
   `ChapterOutcome.chapter_exit_state` itself.
2. All `continuity_issue_count` fields use `_open_issue_count()` to count only
   open issues, not all issues.
3. All block checkpoint metadata includes required score/warning fields.
4. All `bridge_summary` fields are non-empty when bridge data is available.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
HANDLERS = ROOT / "novel_forge" / "workspace" / "sessions" / "chapter_session_handlers.py"
STATE = ROOT / "novel_forge" / "workspace" / "sessions" / "chapter_session_state.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_checkpoint_results_use_open_issue_count() -> None:
    """Verify that all checkpoint result constructions use _open_issue_count
    for continuity_issue_count, not len().

    This guards against the bug where len(issues) was used instead of
    _open_issue_count(issues), causing UI to show resolved issues as open.
    """
    content = _read(HANDLERS)

    assert "continuity_issue_count=_open_issue_count(" in content
    assert "continuity_issue_count=len(" not in content
    assert "len(pending.continuity_report.issues)" not in content


def test_all_checkpoint_results_use_correct_exit_summary_function() -> None:
    """Verify that chapter_exit_summary uses the correct function based on input type.

    - For `pending.outcome` / `review.outcome` / `retry_pending.outcome`: use `summarize_exit_state()`
    - No handler branch may call `summarize_chapter_exit_state()` directly.
    """
    content = _read(HANDLERS)

    assert "summarize_chapter_exit_state" not in content
    assert "chapter_exit_summary=summarize_exit_state(pending.outcome)" in content
    assert "chapter_exit_summary=summarize_exit_state(review.outcome)" in content
    assert "result.chapter_exit_state" not in content
    assert "pending.outcome.chapter_exit_state" not in content


def test_block_checkpoint_metadata_includes_scores() -> None:
    """Verify that block checkpoint metadata includes alignment_score, causal_score,
    causal_issue_count, and warnings fields.

    This guards against the bug where block checkpoints had empty metadata,
    causing UI to show blank score panels.
    """
    content = _read(HANDLERS)

    for field in (
        '"alignment_score"',
        '"causal_score"',
        '"causal_issue_count"',
        '"warnings"',
    ):
        assert field in content

    assert "def _guard_checkpoint_metadata(" in content
    assert "_guard_checkpoint_metadata(" in content
    assert "block_key: True" in content
    assert '"state_adjudication_block"' in content
    assert '"contract_execution_audit_unrepairable"' in content
    assert '"contract_execution_repair_checkpoint"' in content


def test_bridge_summary_not_hardcoded_empty() -> None:
    """Verify that bridge_summary is not hardcoded to empty string when bridge data is available.

    This guards against the bug where _build_guard_alignment_retry_checkpoint_result
    had bridge_summary="" hardcoded, causing UI to miss bridge承接摘要.
    """
    content = _read(HANDLERS)

    assert 'bridge_summary=""' not in content
    assert "ChapterBridge.model_validate" in content
    assert "bundle.layout.chapter_bridge_path(chapter_number)" in content
    assert "_load_chapter_bridge_summary(" in content


def test_helper_function_exists() -> None:
    """Verify that the _build_guard_checkpoint_result helper function exists.

    This ensures the structural deduplication from Phase 4 is in place.
    """
    content = _read(HANDLERS)

    assert "def _build_guard_checkpoint_result(" in content, (
        "Helper function _build_guard_checkpoint_result should exist"
    )
    assert "def _build_block_checkpoint_result(" in content, (
        "Helper function _build_block_checkpoint_result should exist"
    )
    assert "def _persist_guard_checkpoint_and_build_result(" in content, (
        "Helper function _persist_guard_checkpoint_and_build_result should exist"
    )


def test_helper_function_used_for_guard_checkpoints() -> None:
    """Verify that _build_guard_checkpoint_result is used for guard checkpoint results.

    This ensures the helper is actually being used, not just defined.
    """
    content = _read(HANDLERS)

    # Count calls to the helper
    helper_calls = content.count("_build_guard_checkpoint_result(")

    assert helper_calls >= 2, (
        f"Expected _build_guard_checkpoint_result definition plus wrapper usage, got {helper_calls}"
    )
    assert content.count("_build_block_checkpoint_result(") >= 4
    assert content.count("_persist_guard_checkpoint_and_build_result(") >= 4


def test_type_strictness_restored() -> None:
    """Verify that summarize_chapter_exit_state has strict type signature.

    This ensures the type contract from Phase 1 is in place:
    - summarize_chapter_exit_state accepts only ChapterExitState | None
    - summarize_exit_state accepts ChapterOutcome | None and unwraps
    """
    content = _read(STATE)

    assert (
        "def summarize_chapter_exit_state(exit_state: ChapterExitState | None) -> str:" in content
    )
    assert "def summarize_exit_state(outcome: ChapterOutcome | None) -> str:" in content
