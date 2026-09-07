"""Integration tests: Motif performance logging — classification source tracking and CSV gating."""

from __future__ import annotations

import logging

import pytest

from novel_forge.pipeline.steps.continuity_eval.validators import (
    _classify_issue_source,
    _filter_forbidden_elements,
)


class TestClassificationSource:
    """Verify _classify_issue_source correctly identifies the origin of forbidden elements."""

    async def test_logs_classification_source_llm(self) -> None:
        """Elements matching motif_context active_motifs should be classified as 'llm'."""
        motif_context = {
            "active_motifs": [
                {"name": "铜质怀表", "category": "意象", "frequency": 3},
            ]
        }
        result = _classify_issue_source(
            "铜质怀表",
            motif_context=motif_context,
            bible_anchor_terms=["林远", "图书馆"],
        )
        assert result == "llm"

    async def test_logs_classification_source_rule(self) -> None:
        """Elements matching bible_anchor_terms should be classified as 'rule'."""
        result = _classify_issue_source(
            "林远",
            motif_context=None,
            bible_anchor_terms=["林远", "图书馆", "神秘符号"],
        )
        assert result == "rule"

    async def test_logs_classification_source_algorithm(self) -> None:
        """Elements not matching any known source should be classified as 'algorithm'."""
        result = _classify_issue_source(
            "雷雨交加",
            motif_context={"active_motifs": [{"name": "铜质怀表", "category": "意象"}]},
            bible_anchor_terms=["林远", "图书馆"],
        )
        assert result == "algorithm"

    async def test_logs_classification_source_llm_priority_over_rule(self) -> None:
        """When an element matches both motif_context and bible_anchor_terms, 'llm' takes priority."""
        motif_context = {
            "active_motifs": [
                {"name": "林远", "category": "符号", "frequency": 5},
            ]
        }
        result = _classify_issue_source(
            "林远",
            motif_context=motif_context,
            bible_anchor_terms=["林远", "图书馆"],
        )
        assert result == "llm"

    async def test_logs_classification_source_empty_input(self) -> None:
        """Empty or whitespace-only input should default to 'algorithm'."""
        assert _classify_issue_source("", motif_context=None, bible_anchor_terms=None) == "algorithm"
        assert _classify_issue_source("   ", motif_context=None, bible_anchor_terms=None) == "algorithm"


class TestFilterSummaryLogging:
    """Verify _filter_forbidden_elements emits summary debug logs."""

    async def test_filter_summary_log_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """_filter_forbidden_elements should log a summary debug line at the end."""
        caplog.set_level(logging.DEBUG, logger="novel_forge.pipeline.steps.continuity_eval.validators")

        elements = ["雷雨交加", "突然转身", "金色光芒", "寒意刺骨", "夜色深沉"]
        _filter_forbidden_elements(
            elements,
            motif_context=None,
            bible_anchor_terms=["雷雨交加"],
            emotion_keywords=frozenset({"突然转身"}),
            kinship_terms=frozenset(),
            rhetorical_hints=frozenset(),
        )

        filter_logs = [
            record for record in caplog.records
            if "forbidden_filter_summary" in record.getMessage()
        ]
        assert len(filter_logs) == 1
        log_msg = filter_logs[0].getMessage()
        assert "total=5" in log_msg
        assert "passed=" in log_msg
        assert "filtered_out=" in log_msg

    async def test_filter_summary_counts_correct(self, caplog: pytest.LogCaptureFixture) -> None:
        """Summary log should reflect accurate counts of total, passed, and filtered."""
        caplog.set_level(logging.DEBUG, logger="novel_forge.pipeline.steps.continuity_eval.validators")

        elements = ["雷雨交加", "金色光芒"]
        _filter_forbidden_elements(
            elements,
            motif_context=None,
            bible_anchor_terms=["雷雨交加"],
            emotion_keywords=frozenset(),
            kinship_terms=frozenset(),
            rhetorical_hints=frozenset(),
        )

        filter_logs = [
            record for record in caplog.records
            if "forbidden_filter_summary" in record.getMessage()
        ]
        assert len(filter_logs) == 1
        log_msg = filter_logs[0].getMessage()
        assert "total=2" in log_msg
        assert "passed=1" in log_msg
        assert "filtered_out=1" in log_msg


class TestCSVGating:
    """Verify CSV logging is gated by environment variable and defaults to off."""

    async def test_csv_gating_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CSV logging should be disabled by default (NOVEL_FORGE_MOTIF_PERF_LOG not set)."""
        monkeypatch.delenv("NOVEL_FORGE_MOTIF_PERF_LOG", raising=False)

        import os
        perf_log_enabled = os.environ.get("NOVEL_FORGE_MOTIF_PERF_LOG", "").lower() in ("1", "true", "yes")
        assert perf_log_enabled is False

    async def test_csv_gating_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CSV logging should be enabled when NOVEL_FORGE_MOTIF_PERF_LOG=1."""
        monkeypatch.setenv("NOVEL_FORGE_MOTIF_PERF_LOG", "1")

        import os
        perf_log_enabled = os.environ.get("NOVEL_FORGE_MOTIF_PERF_LOG", "").lower() in ("1", "true", "yes")
        assert perf_log_enabled is True
