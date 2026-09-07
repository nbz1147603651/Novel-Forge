"""Tests for PropagationValidator."""

from __future__ import annotations

import pytest

from novel_forge.workspace.propagation_validator import (
    IssueType,
    PropagationIssue,
    PropagationValidator,
    Severity,
)


class TestPropagationValidator:
    """Test suite for PropagationValidator."""

    def test_instantiation(self):
        validator = PropagationValidator()
        assert validator is not None
        assert isinstance(validator, PropagationValidator)

    @pytest.mark.asyncio
    async def test_entity_state_propagation_death(self):
        validator = PropagationValidator()

        original_text = "「张伟」走进房间，脸上带着微笑。"
        repaired_text = "「张伟」倒在血泊中，已经断气。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "「张伟」那天还和我说过话，没想到会发生这样的事。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_type == IssueType.ENTITY_STATE_PROPAGATION
        assert issue.severity == Severity.CRITICAL
        assert "张伟" in issue.description
        assert issue.source_chapter == 2
        assert 3 in issue.affected_chapters

    @pytest.mark.asyncio
    async def test_entity_state_propagation_unquoted_death(self):
        validator = PropagationValidator()

        original_text = "张伟走进房间，脸上带着微笑。"
        repaired_text = "张伟倒下，已经断气。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "张伟说过，等天亮以后还要去城门口。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert any(
            issue.issue_type == IssueType.ENTITY_STATE_PROPAGATION
            and issue.severity == Severity.CRITICAL
            for issue in issues
        )

    @pytest.mark.asyncio
    async def test_entity_state_propagation_death_removed_inconsistency(self):
        validator = PropagationValidator()

        original_text = "「张伟」已经去世，尸体被安放在灵堂。"
        repaired_text = "「张伟」静静地躺在房间里。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "「张伟」的遗体被抬出房间，亲友们悲痛不已。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert len(issues) >= 1
        issue_types = {issue.issue_type for issue in issues}
        assert IssueType.ENTITY_STATE_PROPAGATION in issue_types

    @pytest.mark.asyncio
    async def test_reference_broken_deleted_entity(self):
        validator = PropagationValidator()

        original_text = "「王明」是我的老朋友，我们经常一起喝酒。"
        repaired_text = "他是我多年的老朋友，我们经常一起喝酒。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "「王明」上次说要去出差，不知道回来了没有。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_type == IssueType.REFERENCE_BROKEN
        assert issue.severity == Severity.WARNING
        assert "王明" in issue.description
        assert issue.source_chapter == 2
        assert 3 in issue.affected_chapters

    @pytest.mark.asyncio
    async def test_reference_broken_removed_item(self):
        validator = PropagationValidator()

        original_text = "「古剑」放在书架上，已经尘封多年。"
        repaired_text = "书架上空空如也，什么都没有。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "那把「古剑」究竟去了哪里？"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_type == IssueType.REFERENCE_BROKEN

    @pytest.mark.asyncio
    async def test_timeline_shift_date_change(self):
        validator = PropagationValidator()

        original_text = "第一天，他们抵达了京城。"
        repaired_text = "他们终于来到了繁华的京城。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "第一天的行程结束后，他们决定在客栈休息。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert len(issues) >= 1
        issue_types = {issue.issue_type for issue in issues}
        assert IssueType.TIMELINE_SHIFT in issue_types

    @pytest.mark.asyncio
    async def test_timeline_shift_conflicting_markers(self):
        validator = PropagationValidator()

        original_text = "次日清晨，他们抵达了小镇。"
        repaired_text = "当日傍晚，他们才到达小镇。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "次日晚上，他们在客栈休息。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        timeline_issues = [
            issue for issue in issues
            if issue.issue_type == IssueType.TIMELINE_SHIFT
        ]
        assert len(timeline_issues) >= 1

    @pytest.mark.asyncio
    async def test_no_propagation_clean_repair(self):
        validator = PropagationValidator()

        original_text = "「李娜」在家中看书。"
        repaired_text = "「李娜」在家中安静地看书。"
        subsequent_chapters = [3, 4]
        all_chapter_texts = {
            3: "「李娜」在房间里读书。",
            4: "我去找「李娜」，她还在家里。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        assert issues == []

    @pytest.mark.asyncio
    async def test_multiple_issues_detected(self):
        validator = PropagationValidator()

        original_text = "「张伟」和「王明」在「京城」相遇。"
        repaired_text = "「张伟」已经去世，只有「王明」独自一人。"
        subsequent_chapters = [3]
        all_chapter_texts = {
            3: "「张伟」那天还和「王明」在「京城」喝酒。"
        }

        issues = await validator.validate_propagation(
            repaired_chapter=2,
            original_text=original_text,
            repaired_text=repaired_text,
            subsequent_chapters=subsequent_chapters,
            all_chapter_texts=all_chapter_texts,
        )

        issue_types = {issue.issue_type for issue in issues}
        assert len(issues) >= 2
        assert IssueType.ENTITY_STATE_PROPAGATION in issue_types
        assert IssueType.REFERENCE_BROKEN in issue_types

    def test_propagation_issue_model_dump(self):
        issue = PropagationIssue(
            issue_type=IssueType.ENTITY_STATE_PROPAGATION,
            severity=Severity.CRITICAL,
            description="Test description",
            source_chapter=1,
            affected_chapters=[2, 3],
            suggested_action="Fix it",
        )

        result = issue.model_dump()

        assert result["issue_type"] == IssueType.ENTITY_STATE_PROPAGATION
        assert result["severity"] == Severity.CRITICAL
        assert result["description"] == "Test description"
        assert result["source_chapter"] == 1
        assert result["affected_chapters"] == [2, 3]
        assert result["suggested_action"] == "Fix it"

    def test_entity_referenced_as_alive_helper(self):
        validator = PropagationValidator()

        text_with_alive = "「李娜」还活着，她每天都会来花园散步。"
        assert validator._entity_referenced_as_alive("李娜", text_with_alive) is True

        text_with_death = "「李娜」已经去世，她的遗物还留在房间里。"
        assert validator._entity_referenced_as_alive("李娜", text_with_death) is False

        text_with_memory = "我想起「李娜」曾经在这里种过花。"
        assert validator._entity_referenced_as_alive("李娜", text_with_memory) is False

    def test_markers_are_synonyms(self):
        validator = PropagationValidator()

        assert validator._markers_are_synonyms("当日", "当天") is True
        assert validator._markers_are_synonyms("次日", "翌日") is True
        assert validator._markers_are_synonyms("当日", "次日") is False
        assert validator._markers_are_synonyms("清晨", "深夜") is False

    def test_extract_entities(self):
        validator = PropagationValidator()

        text = "「张伟」和「李娜」一起去「京城」旅行。"
        entities = validator._extract_entities(text)

        assert "张伟" in entities
        assert "李娜" in entities
        assert "京城" in entities

    def test_extract_temporal_markers(self):
        validator = PropagationValidator()

        text = "第一天，他们抵达京城。当日深夜才到达。"
        markers = validator._extract_temporal_markers(text)

        assert "第一天" in markers
        assert "当日" in markers
        assert "深夜" in markers

    def test_entity_has_death_context(self):
        validator = PropagationValidator()

        text_with_death = "「张伟」已经断气，尸体躺在地上。"
        assert validator._entity_has_death_context("张伟", text_with_death) is True

        text_without_death = "「张伟」走进房间，坐下来喝茶。"
        assert validator._entity_has_death_context("张伟", text_without_death) is False
