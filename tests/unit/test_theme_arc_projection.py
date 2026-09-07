"""Unit tests for lightweight theme + character arc projection."""

from __future__ import annotations

from novel_forge.pipeline.long.services.theme_arc_projection import (
    build_theme_arc_context,
    derive_theme_focus,
    format_theme_arc_alignment_question,
    select_arc_milestones,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeMilestone:
    def __init__(self, chapter_start: int, chapter_end: int, description: str = ""):
        self.chapter_start = chapter_start
        self.chapter_end = chapter_end
        self.description = description


class _FakeArc:
    def __init__(self, character: str, arc_summary: str = "", milestones=None):
        self.character = character
        self.arc_summary = arc_summary
        self.milestones = milestones or []


class _FakePhase:
    def __init__(self, chapter_start: int, chapter_end: int, phase_name: str = "", description: str = ""):
        self.chapter_start = chapter_start
        self.chapter_end = chapter_end
        self.phase_name = phase_name
        self.description = description


# ────────────────────────────────────────────────────────────────────────────
# derive_theme_focus
# ────────────────────────────────────────────────────────────────────────────


class TestDeriveThemeFocus:
    def test_empty_themes_returns_empty(self):
        result = derive_theme_focus([], chapter_number=5)
        assert result["primary_theme"] == ""
        assert result["theme_list"] == []

    def test_single_theme_is_primary(self):
        result = derive_theme_focus(["identity"], chapter_number=3, total_chapters=10)
        assert result["primary_theme"] == "identity"
        assert result["theme_list"] == ["identity"]

    def test_multiple_themes_rotate_by_position(self):
        themes = ["identity", "trust", "freedom"]
        early = derive_theme_focus(themes, chapter_number=2, total_chapters=10)
        mid = derive_theme_focus(themes, chapter_number=5, total_chapters=10)
        late = derive_theme_focus(themes, chapter_number=9, total_chapters=10)
        assert early["primary_theme"] == "identity"
        assert mid["primary_theme"] == "trust"
        assert late["primary_theme"] == "freedom"

    def test_position_label_early(self):
        result = derive_theme_focus(["x"], chapter_number=2, total_chapters=10)
        assert result["position_label"] == "early"

    def test_position_label_mid(self):
        result = derive_theme_focus(["x"], chapter_number=5, total_chapters=10)
        assert result["position_label"] == "mid"

    def test_position_label_late(self):
        result = derive_theme_focus(["x"], chapter_number=9, total_chapters=10)
        assert result["position_label"] == "late"

    def test_position_label_empty_when_no_total(self):
        result = derive_theme_focus(["x"], chapter_number=3, total_chapters=0)
        assert result["position_label"] == ""

    def test_phase_context_from_narrative_phases(self):
        phases = [
            _FakePhase(1, 8, phase_name="铺垫", description="建立世界与角色"),
            _FakePhase(9, 16, phase_name="冲突", description="主要矛盾升级"),
        ]
        result = derive_theme_focus(
            ["identity"], chapter_number=5, total_chapters=16, narrative_phases=phases
        )
        assert "铺垫" in result["phase_context"]
        assert "建立世界与角色" in result["phase_context"]

    def test_phase_context_empty_when_no_match(self):
        phases = [_FakePhase(1, 3, phase_name="early")]
        result = derive_theme_focus(
            ["identity"], chapter_number=10, total_chapters=12, narrative_phases=phases
        )
        assert result["phase_context"] == ""


# ────────────────────────────────────────────────────────────────────────────
# select_arc_milestones
# ────────────────────────────────────────────────────────────────────────────


class TestSelectArcMilestones:
    def test_empty_arcs_returns_empty(self):
        assert select_arc_milestones([], chapter_number=5) == []

    def test_milestone_within_range_selected(self):
        arcs = [
            _FakeArc(
                "沈昭",
                "从迷茫到觉醒",
                [_FakeMilestone(3, 7, "面对内心恐惧")],
            )
        ]
        result = select_arc_milestones(arcs, chapter_number=5)
        assert len(result) == 1
        assert result[0]["character"] == "沈昭"
        assert result[0]["milestone_description"] == "面对内心恐惧"
        assert result[0]["chapter_range"] == (3, 7)

    def test_milestone_outside_range_excluded(self):
        arcs = [
            _FakeArc(
                "沈昭",
                "",
                [_FakeMilestone(10, 15, "最终觉醒")],
            )
        ]
        result = select_arc_milestones(arcs, chapter_number=5)
        assert result == []

    def test_multiple_arcs_multiple_milestones(self):
        arcs = [
            _FakeArc("沈昭", "", [_FakeMilestone(1, 5, "初始困惑"), _FakeMilestone(6, 10, "突破")]),
            _FakeArc("公孙烈", "", [_FakeMilestone(3, 8, "暗中布局")]),
        ]
        result = select_arc_milestones(arcs, chapter_number=5)
        assert len(result) == 2
        chars = {m["character"] for m in result}
        assert "沈昭" in chars
        assert "公孙烈" in chars

    def test_zero_chapter_number_returns_empty(self):
        arcs = [_FakeArc("沈昭", "", [_FakeMilestone(1, 5, "test")])]
        assert select_arc_milestones(arcs, chapter_number=0) == []

    def test_arc_summary_passed_through(self):
        arcs = [_FakeArc("沈昭", "从迷茫到觉醒", [_FakeMilestone(1, 5, "test")])]
        result = select_arc_milestones(arcs, chapter_number=3)
        assert result[0]["arc_summary"] == "从迷茫到觉醒"


# ────────────────────────────────────────────────────────────────────────────
# build_theme_arc_context
# ────────────────────────────────────────────────────────────────────────────


class TestBuildThemeArcContext:
    def test_empty_inputs_return_empty(self):
        result = build_theme_arc_context(
            themes=[], character_arcs=[], chapter_number=5, total_chapters=10
        )
        assert result["theme_focus"]["primary_theme"] == ""
        assert result["active_arc_milestones"] == []
        assert result["injection_text"] == ""

    def test_full_context_assembled(self):
        themes = ["identity", "trust"]
        arcs = [
            _FakeArc("沈昭", "成长", [_FakeMilestone(3, 7, "面对恐惧")])
        ]
        phases = [_FakePhase(1, 8, "铺垫")]

        result = build_theme_arc_context(
            themes=themes,
            character_arcs=arcs,
            chapter_number=5,
            total_chapters=10,
            narrative_phases=phases,
        )
        assert result["theme_focus"]["primary_theme"] != ""
        assert len(result["active_arc_milestones"]) == 1
        assert result["injection_text"] != ""

    def test_injection_text_contains_theme_and_arc(self):
        themes = ["identity"]
        arcs = [_FakeArc("沈昭", "", [_FakeMilestone(3, 7, "突破心障")])]
        result = build_theme_arc_context(
            themes=themes, character_arcs=arcs, chapter_number=5, total_chapters=10
        )
        text = result["injection_text"]
        assert "identity" in text
        assert "沈昭" in text
        assert "突破心障" in text


# ────────────────────────────────────────────────────────────────────────────
# format_theme_arc_alignment_question
# ────────────────────────────────────────────────────────────────────────────


class TestFormatThemeArcAlignmentQuestion:
    def test_none_context_returns_empty(self):
        assert format_theme_arc_alignment_question(None) == ""

    def test_empty_context_returns_empty(self):
        assert format_theme_arc_alignment_question({}) == ""

    def test_produces_question_with_theme(self):
        ctx = {
            "theme_focus": {"primary_theme": "identity"},
            "active_arc_milestones": [],
        }
        q = format_theme_arc_alignment_question(ctx)
        assert "identity" in q
        assert "主题" in q or "职责" in q

    def test_produces_question_with_arc(self):
        ctx = {
            "theme_focus": {"primary_theme": ""},
            "active_arc_milestones": [
                {"character": "沈昭", "milestone_description": "觉醒"},
            ],
        }
        q = format_theme_arc_alignment_question(ctx)
        assert "沈昭" in q
        assert "觉醒" in q

    def test_full_context_produces_rich_question(self):
        ctx = {
            "theme_focus": {"primary_theme": "identity"},
            "active_arc_milestones": [
                {"character": "沈昭", "milestone_description": "面对恐惧"},
                {"character": "公孙烈", "milestone_description": "暗中布局"},
            ],
        }
        q = format_theme_arc_alignment_question(ctx)
        assert "identity" in q
        assert "沈昭" in q
        assert "公孙烈" in q
