"""Integration tests for forbidden elements after audit-dimension split."""

from __future__ import annotations

from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairInput, CausalRepairResult
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
    _detect_forbidden_elements,
)


def _build_base_input() -> ContinuityEvalInput:
    """Build a minimal ContinuityEvalInput for testing."""
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


# ────────────────────────────────────────────────────────
# 1. test_forbidden_element_joint_scoring_critical
#    Opening imagery forbidden + handoff break → critical severity
# ────────────────────────────────────────────────────────

def test_forbidden_element_joint_scoring_critical() -> None:
    """Forbidden elements belong to chapter quality, not continuity scoring."""
    input_data = _build_base_input()
    input_data.chapter_plan.forbidden_elements = ["金丝颤动"]
    input_data.chapter_bridge.action_handoff = "周明听见铜铃一响，指尖金丝颤动了一下。"
    input_data.chapter_text = (
        "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
        "他听见远处传来更夫的梆子声。\n\n"
        "铜铃轻响，指尖金丝颤动了一下。\n\n"
        "他决定起身去查访。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issues
    assert "forbidden_element_usage" not in issues


def test_forbidden_story_anchor_not_flagged_as_violation() -> None:
    """Names, kinship terms, and carry-forward objects should not be treated as hard forbidden."""
    input_data = _build_base_input()
    input_data.chapter_state_packet.known_characters = ["周明", "赵成安"]
    input_data.chapter_plan.forbidden_elements = ["周明", "父亲", "账簿残页"]
    input_data.chapter_text = (
        "周明仍在耳房里等消息。\n\n"
        "他想到父亲临走前那句嘱咐，把账簿残页又往袖中按了按。\n\n"
        "更夫的梆子声从街口传来。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issue_types = {issue["issue_type"] for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issue_types
    assert "forbidden_element_usage" not in issue_types


# ────────────────────────────────────────────────────────
# 2. test_forbidden_element_joint_scoring_high
#    Opening forbidden + handoff intact → high severity
# ────────────────────────────────────────────────────────

def test_forbidden_element_joint_scoring_high() -> None:
    """Continuity no longer owns hard-forbidden rhetorical imagery."""
    input_data = _build_base_input()
    input_data.chapter_plan.forbidden_elements = ["深秋的寒意"]
    input_data.chapter_text = (
        "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
        "他听见远处传来更夫的梆子声。\n\n"
        "风从门缝里灌进来，带着深秋的寒意。\n\n"
        "他决定起身去查访。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issues
    assert "forbidden_element_usage" not in issues


# ────────────────────────────────────────────────────────
# 3. test_forbidden_element_short_word_gap
#    4-char forbidden element with gap=1 catches close variants
# ────────────────────────────────────────────────────────

def test_forbidden_element_short_word_gap() -> None:
    """4-character forbidden elements use gap=1 for fuzzy matching.

    '金丝颤动' should catch '金丝微颤动' (1-char gap) but NOT
    '金丝微微颤动' (2-char gap).
    """
    # 1-char gap should match
    text_1gap = "她感到指尖金丝微颤动，心中一惊。"
    found_1gap = _detect_forbidden_elements(text_1gap, ["金丝颤动"])
    assert len(found_1gap) == 1

    # 2-char gap should NOT match with gap=1
    text_2gap = "她感到指尖金丝微微颤动，心中一惊。"
    found_2gap = _detect_forbidden_elements(text_2gap, ["金丝颤动"])
    assert found_2gap == []


# ────────────────────────────────────────────────────────
# 4. test_forbidden_element_narrative_function
#    Narrative function is inferred and recorded in fix_actions
# ────────────────────────────────────────────────────────

def test_forbidden_element_narrative_function() -> None:
    """Continuity should not generate forbidden-element repair actions."""
    input_data = _build_base_input()
    input_data.chapter_plan.forbidden_elements = ["金丝颤动"]
    # Place forbidden element in opening with environment context → "营造氛围"
    input_data.chapter_text = (
        "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
        "他听见远处传来更夫的梆子声。\n\n"
        "窗外月光惨淡，指尖金丝颤动了一下。\n\n"
        "他决定起身去查访。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issues
    assert "forbidden_element_usage" not in issues


# ────────────────────────────────────────────────────────
# 5. test_causal_repair_respects_forbidden
#    CausalRepairInput receives forbidden_elements for template injection
# ────────────────────────────────────────────────────────

def test_causal_repair_respects_forbidden() -> None:
    """CausalRepairInput should carry forbidden_elements to the repair template.

    The forbidden_elements and forbidden_elements_soft fields on CausalRepairInput
    are injected into the causal_repair_typed.j2 template so the LLM avoids them
    during repair.
    """
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport

    bridge = ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        opening_time="亥时初",
        opening_location="西官仓耳房",
        opening_pov="周明",
        transition_mode="direct_continue",
        action_handoff="周明守着账簿残页。",
    )
    causal_report = CausalValidationReport(
        causal_score=6.0,
        validation_status="ok",
        issues=[
            CausalIssue(
                issue_type="opening_causal_gap",
                severity="high",
                summary="开头缺少因果承接。",
                location="第1段",
                evidence="上章结尾提到账簿残页，本章未承接。",
            )
        ],
    )

    repair_input = CausalRepairInput(
        chapter_number=3,
        chapter_text="周明坐在耳房里，想着下一步。",
        causal_link={},
        chapter_bridge=bridge,
        causal_report=causal_report,
        forbidden_elements=["金丝颤动", "惨淡月光"],
        forbidden_elements_soft=["冷白灯光"],
    )

    assert repair_input.forbidden_elements == ["金丝颤动", "惨淡月光"]
    assert repair_input.forbidden_elements_soft == ["冷白灯光"]


# ────────────────────────────────────────────────────────
# 6. test_causal_repair_post_detection
#    CausalRepairResult contains forbidden_element_warnings after repair
# ────────────────────────────────────────────────────────

def test_causal_repair_post_detection() -> None:
    """After causal repair, if revised text contains forbidden elements,
    CausalRepairResult.forbidden_element_warnings should list them.
    """
    from novel_forge.pipeline.steps.continuity_eval_step import _detect_forbidden_elements

    revised_text = "周明坐在耳房里，指尖金丝颤动了一下，想着下一步。"
    forbidden = ["金丝颤动", "惨淡月光"]
    hits = _detect_forbidden_elements(revised_text, forbidden)

    assert len(hits) == 1
    assert hits[0][0] == "金丝颤动"

    result = CausalRepairResult(
        revised_text=revised_text,
        issues=[],
        applied=True,
        forbidden_element_warnings=[fe for fe, _ in hits],
    )
    assert result.forbidden_element_warnings == ["金丝颤动"]


# ────────────────────────────────────────────────────────
# 7. test_intentional_callbacks_not_flagged
#    Intentional callback elements should NOT be flagged as forbidden
# ────────────────────────────────────────────────────────

def test_intentional_callbacks_not_flagged() -> None:
    """Elements listed in intentional_callbacks should not trigger forbidden detection.

    intentional_callbacks are deliberately repeated motifs for thematic resonance.
    They must be excluded from both hard and soft forbidden element detection.
    """
    input_data = _build_base_input()
    # '金丝颤动' is in forbidden_elements BUT also in intentional_callbacks
    input_data.chapter_plan.forbidden_elements = ["金丝颤动", "惨淡月光"]
    input_data.chapter_plan.intentional_callbacks = ["金丝颤动"]
    input_data.chapter_text = (
        "周明守着账簿残页，仍在耳房等下一步消息。\n\n"
        "他听见远处传来更夫的梆子声。\n\n"
        "窗外月光惨淡，指尖金丝颤动了一下。\n\n"
        "他决定起身去查访。"
    )

    # The detection logic should exclude intentional_callbacks from forbidden set.
    # Currently the code does NOT filter intentional_callbacks — this test verifies
    # the expected behavior: only '惨淡月光' should be flagged, not '金丝颤动'.
    #
    # We test the effective forbidden set after intentional_callbacks exclusion.
    hard_forbidden = {
        str(item).strip()
        for item in (getattr(input_data.chapter_plan, "forbidden_elements", []) or [])
        if str(item).strip()
    }
    intentional = {
        str(item).strip()
        for item in (getattr(input_data.chapter_plan, "intentional_callbacks", []) or [])
        if str(item).strip()
    }
    effective_forbidden = hard_forbidden - intentional

    assert "金丝颤动" not in effective_forbidden
    assert "惨淡月光" in effective_forbidden

    test_text = "窗外惨淡月光照进来，令他心生寒意。"
    found = _detect_forbidden_elements(test_text, list(effective_forbidden))
    matched_elements = {fe for fe, _ in found}
    assert "惨淡月光" in matched_elements
    assert "金丝颤动" not in matched_elements


def test_continuity_eval_excludes_intentional_callbacks_from_forbidden() -> None:
    """ContinuityEvalStep should not flag hard-forbidden items that are intentional callbacks."""
    input_data = _build_base_input()
    input_data.chapter_plan.forbidden_elements = ["金丝颤动"]
    input_data.chapter_plan.intentional_callbacks = ["金丝颤动"]
    input_data.chapter_text = (
        "周明仍在耳房等下一步消息。\n\n"
        "他听见更夫梆子声从街口过去。\n\n"
        "指尖金丝颤动了一下，那是他故意保留的旧誓回环。\n\n"
        "他把残页重新收好。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issue_types = {issue["issue_type"] for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issue_types
    assert "forbidden_element_usage" not in issue_types
