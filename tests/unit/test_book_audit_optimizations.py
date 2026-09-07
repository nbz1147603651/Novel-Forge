"""Tests for book audit optimizations: cross-chapter, paragraph match, semantic match, score, AuditCoordinator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.pipeline.steps.book_consistency_step import _derive_consistency_score
from novel_forge.workspace.book_ops.execution_book_entry import _lightweight_evidence_verify
from novel_forge.workspace.book_ops.execution_book_repair import (
    _assert_auto_repair_outside_project_lock,
    _book_repair_guard_enabled,
    _evaluate_book_repair_text_guard,
    _repair_guard_reason_text,
)
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.helpers.execution_helpers import (
    _extract_key_entities,
    _group_book_issues_by_chapter,
    _post_repair_evidence_check,
    _select_issue_indices_by_paragraph,
)


class TestCrossChapterPrompt:
    """Verify cross-chapter validation instructions are in the prompt template."""

    def test_prompt_contains_cross_chapter_section(self):
        from pathlib import Path

        template_path = Path(__file__).resolve().parents[2] / "novel_forge" / "prompts" / "prompts" / "checking" / "book_consistency.j2"
        content = template_path.read_text(encoding="utf-8")
        assert "跨章交叉验证指令" in content
        assert "时间线交叉验证" in content
        assert "角色状态交叉验证" in content
        assert "物品/道具追踪" in content
        assert "伏笔回收验证" in content
        assert "因果关系交叉验证" in content

    def test_prompt_contains_chapter_listing_requirement(self):
        from pathlib import Path

        template_path = Path(__file__).resolve().parents[2] / "novel_forge" / "prompts" / "prompts" / "checking" / "book_consistency.j2"
        content = template_path.read_text(encoding="utf-8")
        assert "chapters_involved" in content
        assert "至少 2 个章节" in content

    def test_prompt_contains_issue_output_limit(self):
        from pathlib import Path

        template_path = Path(__file__).resolve().parents[2] / "novel_forge" / "prompts" / "prompts" / "checking" / "book_consistency.j2"
        content = template_path.read_text(encoding="utf-8")
        assert "max_issues_per_chunk" in content
        assert "最多输出" in content


class TestParagraphMatching:
    """Test paragraph-based matching function."""

    def test_exact_paragraph_match(self):
        book_issues = [{"paragraph_index": 10, "description": "test"}]
        report_issues = [SimpleNamespace(location="第10段")]
        result = _select_issue_indices_by_paragraph(
            book_issues=book_issues,
            report_issues=report_issues,
            lane="continuity",
            threshold=2,
        )
        assert result == [0]

    def test_within_threshold_match(self):
        book_issues = [{"paragraph_index": 10, "description": "test"}]
        report_issues = [SimpleNamespace(location="第11段")]
        result = _select_issue_indices_by_paragraph(
            book_issues=book_issues,
            report_issues=report_issues,
            lane="continuity",
            threshold=2,
        )
        assert result == [0]

    def test_outside_threshold_no_match(self):
        book_issues = [{"paragraph_index": 10, "description": "test"}]
        report_issues = [SimpleNamespace(location="第15段")]
        result = _select_issue_indices_by_paragraph(
            book_issues=book_issues,
            report_issues=report_issues,
            lane="continuity",
            threshold=2,
        )
        assert result == []

    def test_no_paragraph_index_no_match(self):
        book_issues = [{"description": "test"}]
        report_issues = [SimpleNamespace(location="第10段")]
        result = _select_issue_indices_by_paragraph(
            book_issues=book_issues,
            report_issues=report_issues,
            lane="continuity",
            threshold=2,
        )
        assert result == []

    def test_multiple_matches(self):
        book_issues = [
            {"paragraph_index": 10, "description": "test1"},
            {"paragraph_index": 20, "description": "test2"},
        ]
        report_issues = [
            SimpleNamespace(location="第10段"),
            SimpleNamespace(location="第20段"),
        ]
        result = _select_issue_indices_by_paragraph(
            book_issues=book_issues,
            report_issues=report_issues,
            lane="continuity",
            threshold=2,
        )
        assert result == [0, 1]


class TestSemanticMatching:
    """Test semantic matching in post-repair evidence check."""

    def test_entity_extraction_time(self):
        text = "天德元年腊月，冬至后第三日"
        entities = _extract_key_entities(text)
        assert len(entities["time"]) > 0

    def test_entity_extraction_location(self):
        text = "他来到了长安城"
        entities = _extract_key_entities(text)
        assert len(entities["location"]) > 0

    def test_entity_extraction_known_unquoted_person(self):
        text = "林远看着窗外，没有再说话。"
        entities = _extract_key_entities(text, known_entities={"person": ["林远"]})
        assert "林远" in entities["person"]

    def test_entity_extraction_action_unquoted_person_candidate(self):
        text = "沈青走进院门，低声问他发生了什么。"
        entities = _extract_key_entities(text, include_unquoted_person_candidates=True)
        assert "沈青" in entities["person"]

    def test_evidence_string_match_still_works(self):
        issues = [{"issue_id": "test1", "evidence": "天德元年腊月"}]
        new_text = "天德元年腊月，冬至后第三日"
        result = _post_repair_evidence_check(new_text, issues)
        assert result["issues_remaining"] == 1
        assert result["issues_closed"] == 0

    def test_evidence_removed_closes_issue(self):
        issues = [{"issue_id": "test1", "evidence": "天德元年腊月"}]
        new_text = "长安三年，冬至后第三日"
        result = _post_repair_evidence_check(new_text, issues)
        assert result["issues_closed"] == 1
        assert result["issues_remaining"] == 0

    def test_empty_evidence_closes_issue(self):
        issues = [{"issue_id": "test1", "evidence": ""}]
        new_text = "some text"
        result = _post_repair_evidence_check(new_text, issues)
        assert result["issues_closed"] == 1

    def test_no_issues_returns_empty(self):
        result = _post_repair_evidence_check("some text", [])
        assert result["issues_checked"] == 0
        assert result["issues_closed"] == 0


class TestScoreFormula:
    """Test the new consistency score formula."""

    def test_no_issues(self):
        assert _derive_consistency_score([]) == 9.5

    def test_three_critical(self):
        issues = [{"severity": "critical"}] * 3
        assert _derive_consistency_score(issues) == 5.0

    def test_one_critical(self):
        issues = [{"severity": "critical"}]
        assert _derive_consistency_score(issues) == 7.0

    def test_ten_warnings(self):
        issues = [{"severity": "warning"}] * 10
        assert _derive_consistency_score(issues) == 7.0

    def test_two_critical(self):
        issues = [{"severity": "critical"}] * 2
        assert _derive_consistency_score(issues) == 6.0

    def test_five_critical_caps_at_5(self):
        issues = [{"severity": "critical"}] * 5
        assert _derive_consistency_score(issues) == 5.0

    def test_mixed_severity_no_critical(self):
        issues = [{"severity": "warning"}] * 5 + [{"severity": "info"}] * 10
        score = _derive_consistency_score(issues)
        assert score == pytest.approx(7.5, rel=0.01)

    def test_score_clamped_to_range(self):
        issues = [{"severity": "critical"}] * 100
        score = _derive_consistency_score(issues)
        assert 0.0 <= score <= 10.0


class TestSummaryModeLightweightVerify:
    def test_weak_warning_issue_becomes_manual_review_not_auto_repair(self, tmp_path):
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(tmp_path)
        layout.chapters_dir.mkdir(parents=True)
        issue = {
            "issue_id": "weak-warning",
            "severity": "warning",
            "primary_chapter": 1,
            "chapters_involved": [1],
            "description": "摘要模式缺少证据。",
        }

        filtered, stats = _lightweight_evidence_verify([issue], [1], layout)

        assert stats["suspected"] == 1
        assert stats["manual_review"] == 1
        assert filtered[0]["verification_status"] == "suspected"
        assert filtered[0]["needs_manual_review"] is True
        assert filtered[0]["auto_repair_eligible"] is False
        assert _group_book_issues_by_chapter(
            filtered,
            min_severity="warning",
            max_chapters=10,
        ) == []

    def test_evidence_match_still_confirms_issue(self, tmp_path):
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(tmp_path)
        layout.chapters_dir.mkdir(parents=True)
        layout.drafts_dir.mkdir(parents=True)
        layout.chapter_path(1).write_text("朱批录仍在案头。", encoding="utf-8")
        issue = {
            "issue_id": "evidence-hit",
            "severity": "warning",
            "primary_chapter": 1,
            "chapters_involved": [1],
            "evidence": "朱批录仍在案头",
        }

        filtered, stats = _lightweight_evidence_verify([issue], [1], layout)

        assert stats["confirmed"] == 1
        assert filtered[0]["verification_status"] == "confirmed"
        assert filtered[0]["confidence"] == 1.0


class TestBookRepairGuard:
    """Regression coverage for whole-book auto-repair pollution guard."""

    def _request(self) -> BookConsistencyRequest:
        return BookConsistencyRequest(
            project_id="long_demo",
            repair_guard_enabled=True,
            repair_guard_max_delta_ratio=0.12,
            repair_guard_max_added_chars=600,
        )

    def _runtime(self) -> SimpleNamespace:
        return SimpleNamespace(
            settings=SimpleNamespace(
                long_book_audit_repair_guard_max_delta_ratio=0.12,
                long_book_audit_repair_guard_max_added_chars=600,
            )
        )

    def test_repeated_sentence_regression_is_blocked(self) -> None:
        original = "第一段里，朱批录确认旧案已封存，众人暂时退到廊下。"
        repeated = "沈照夜的指尖微微发颤，他终于承认旧案仍未结束。"
        repaired = original + "\n\n" + repeated * 4

        result = _evaluate_book_repair_text_guard(
            original,
            repaired,
            request=self._request(),
            runtime=self._runtime(),
        )

        assert result["ok"] is False
        assert "sentence_repetition_regression" in result["reason_codes"]

    def test_prompt_schema_leak_is_blocked(self) -> None:
        original = "朱批录合上册页，确认第七处证词与昨夜灯影相符。"
        repaired = original + '\n\n```json\n{"issue_id":"x1","paragraph_index":7}\n```'

        result = _evaluate_book_repair_text_guard(
            original,
            repaired,
            request=self._request(),
            runtime=self._runtime(),
        )

        assert result["ok"] is False
        assert "schema_or_prompt_leak" in result["reason_codes"]
        assert "检测到疑似提示词" in _repair_guard_reason_text(result)

    def test_markdown_intrusion_is_blocked(self) -> None:
        original = "内卫调度已经归档，朱批录只留下三枚印信。"
        repaired = original + "\n\n**内卫调度：立即收束所有支线。**"

        result = _evaluate_book_repair_text_guard(
            original,
            repaired,
            request=self._request(),
            runtime=self._runtime(),
        )

        assert result["ok"] is False
        assert "markdown_intrusion" in result["reason_codes"]

    def test_small_plaintext_patch_passes(self) -> None:
        original = "朱批录合上册页，确认第七处证词与昨夜灯影相符。"
        repaired = "朱批录合上册页，确认第七处证词与昨夜灯影完全相符。"

        result = _evaluate_book_repair_text_guard(
            original,
            repaired,
            request=self._request(),
            runtime=self._runtime(),
        )

        assert result["ok"] is True
        assert result["reason_codes"] == []

    def test_settings_apply_when_request_does_not_override_guard(self) -> None:
        request = BookConsistencyRequest(project_id="long_demo")
        runtime = SimpleNamespace(
            settings=SimpleNamespace(
                long_book_audit_repair_guard_enabled=False,
                long_book_audit_repair_guard_max_delta_ratio=0.05,
                long_book_audit_repair_guard_max_added_chars=120,
            )
        )

        assert _book_repair_guard_enabled(request=request, runtime=runtime) is False

        result = _evaluate_book_repair_text_guard(
            "朱批录合上册页，确认第七处证词与昨夜灯影相符。" * 20,
            ("朱批录合上册页，确认第七处证词与昨夜灯影相符。" * 20) + ("新增证据。" * 30),
            request=request,
            runtime=runtime,
        )

        assert result["max_delta_ratio"] == 0.05
        assert result["max_added_chars"] == 120

    async def test_auto_repair_asserts_outside_current_project_lock(self) -> None:
        from novel_forge.core.infra.resource_locks import (
            ResourceLockType,
            ResourceName,
            get_resource_lock_manager,
        )

        lock_mgr = get_resource_lock_manager()
        async with lock_mgr.lock(
            ResourceName.CANON,
            ResourceLockType.EXCLUSIVE,
            project_id="long_demo",
        ):
            with pytest.raises(RuntimeError):
                _assert_auto_repair_outside_project_lock("long_demo")

        _assert_auto_repair_outside_project_lock("long_demo")


class TestAuditCoordinatorIntegration:
    """Test AuditCoordinator integration."""

    def test_config_setting_removed(self):
        from novel_forge.core.config import Settings

        settings = Settings()
        assert not hasattr(settings, "long_book_audit_use_memory_enhancement")

    def test_book_consistency_input_has_memory_field(self):
        from novel_forge.pipeline.steps.book_consistency_step import BookConsistencyInput

        input_data = BookConsistencyInput(
            chapter_summaries=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            memory_enhancement_context="test context",
        )
        assert input_data.memory_enhancement_context == "test context"

    def test_graceful_fallback_without_memory(self):
        from novel_forge.pipeline.steps.book_consistency_step import BookConsistencyInput

        input_data = BookConsistencyInput(
            chapter_summaries=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
        )
        assert input_data.memory_enhancement_context == ""
