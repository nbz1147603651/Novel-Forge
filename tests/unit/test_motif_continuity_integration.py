"""Regression tests for motif-aware continuity boundary behavior.

Continuity evaluation no longer owns rhetorical forbidden-element scoring;
these cases must stay out of continuity so they can be handled by chapter
quality / reading-power checks.
"""

from __future__ import annotations

from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.continuity_eval_step import ContinuityEvalInput, ContinuityEvalStep


def _build_base_input() -> ContinuityEvalInput:
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="夜访",
            goal="探查账簿线索",
            pov_character="周明",
            setting="西官仓耳房",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="亥时初",
            location="西官仓耳房",
            pov="周明",
            must_carry_forward=["账簿残页线索"],
        ),
        previous_chapter_ending="周明把残页压在青砖下，仍留在耳房等更夫报时。",
        must_carry_forward=["账簿残页线索"],
        known_characters=["周明", "赵成安"],
    )
    bridge = ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        opening_time="亥时初",
        opening_location="西官仓耳房",
        opening_pov="周明",
        transition_mode="direct_continue",
        action_handoff="周明守着账簿残页，等下一步消息。",
    )
    plan = ChapterPlan(
        opening_contract="承接耳房里的等待状态。",
        closing_contract="留下下一章可直接承接的证据交接。",
    )
    return ContinuityEvalInput(
        chapter_number=3,
        chapter_text="",
        chapter_state_packet=packet,
        chapter_bridge=bridge,
        chapter_plan=plan,
    )


class TestMotifContextEndToEnd:
    def test_rhetorical_motif_kept_as_forbidden(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["月光", "寒意"]
        input_data.chapter_text = (
            "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
            "窗外月光惨淡，他感到一阵寒意。"
        )
        input_data.motif_context = {
            "active_motifs": [
                {"name": "月光", "category": "颜色"},
                {"name": "寒意", "category": "感官"},
            ]
        }

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"]: issue for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_thematic_motif_filtered_out(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["宿命", "金丝"]
        input_data.chapter_text = (
            "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
            "他感到这是宿命，指尖掠过金丝。"
        )
        input_data.motif_context = {
            "active_motifs": [
                {"name": "宿命", "category": "主题"},
                {"name": "金丝", "category": "符号"},
            ]
        }

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_motif_rhetorical_still_subject_to_legacy_filter(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["月光"]
        input_data.chapter_text = "窗外月光惨淡，照在账簿残页上。"
        input_data.motif_context = {
            "active_motifs": [
                {"name": "月光", "category": "颜色"},
            ]
        }
        input_data.bible_anchor_terms = ["月光"]

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types


class TestBibleAnchorTermsEndToEnd:
    def test_bible_term_whitelists_known_anchor(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["镇北将军", "六品翰林"]
        input_data.chapter_text = (
            "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
            "镇北将军派人传话，六品翰林也在查这案子。"
        )
        input_data.bible_anchor_terms = ["镇北将军", "六品翰林"]

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_bible_term_does_not_affect_rhetorical_imagery(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["月光"]
        input_data.chapter_text = "窗外月光惨淡，照在账簿残页上。"
        input_data.bible_anchor_terms = ["镇北将军"]

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"]: issue for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types


class TestColdStartFallback:
    def test_empty_motif_context_falls_back_to_static(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["父亲"]
        input_data.chapter_text = "周明想起父亲临走前那句嘱咐。"
        input_data.motif_context = {}
        input_data.bible_anchor_terms = []

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_empty_bible_terms_falls_back_to_static_kinship(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["殿下"]
        input_data.chapter_text = "殿下站在廊下，神色警觉。"
        input_data.motif_context = {}
        input_data.bible_anchor_terms = []

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_static_rhetorical_imagery_still_flagged_when_dynamic_empty(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["月光"]
        input_data.chapter_text = "窗外月光惨淡，照在账簿残页上。"
        input_data.motif_context = {}
        input_data.bible_anchor_terms = []

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"]: issue for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_no_dynamic_sources_matches_legacy_behavior(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["周明", "父亲", "月光"]
        input_data.chapter_text = (
            "周明仍在耳房里等消息。\n\n"
            "他想到父亲临走前那句嘱咐。\n\n"
            "窗外月光惨淡，照在青砖上。"
        )
        input_data.motif_context = {}
        input_data.bible_anchor_terms = []

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issue_types = {issue["issue_type"] for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issue_types
        assert "forbidden_element_usage" not in issue_types

    def test_mixed_dynamic_and_static_sources(self):
        input_data = _build_base_input()
        input_data.chapter_plan.forbidden_elements = ["月光", "宿命", "镇北将军"]
        input_data.chapter_text = (
            "镇北将军站在廊下，望着窗外月光。\n\n"
            "他感到这是宿命。"
        )
        input_data.motif_context = {
            "active_motifs": [
                {"name": "月光", "category": "颜色"},
                {"name": "宿命", "category": "主题"},
            ]
        }
        input_data.bible_anchor_terms = ["镇北将军"]

        normalized = ContinuityEvalStep._normalize_report({}, input_data)
        issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

        assert "forbidden_element_violation" not in issues
        assert "forbidden_element_usage" not in issues
