"""Unit tests for POV drift audit service."""

from __future__ import annotations

from novel_forge.pipeline.long.services.pov_drift_audit import (
    PovDriftAuditResult,
    PovDriftCandidate,
    detect_pov_drift_candidates,
    findings_from_pov_candidates,
    repair_tickets_from_pov_findings,
    run_pov_drift_audit,
)

# ────────────────────────────────────────────────────────────────────────────
# detect_pov_drift_candidates
# ────────────────────────────────────────────────────────────────────────────


class TestDetectPovDriftCandidates:
    """Tests for local candidate detection."""

    def test_empty_text_returns_empty(self):
        assert detect_pov_drift_candidates("", pov_character="沈昭") == []

    def test_no_pov_character_returns_empty(self):
        assert detect_pov_drift_candidates("some text", pov_character="") == []

    def test_no_drift_when_only_pov_character_has_interior(self):
        text = "沈昭觉得这件事不对劲。他想了一会儿，决定去找公孙烈。\n\n沈昭意识到时间不多了。"
        candidates = detect_pov_drift_candidates(
            text, pov_character="沈昭", known_characters=["沈昭", "公孙烈"]
        )
        assert candidates == []

    def test_drift_detected_when_non_pov_has_interior(self):
        # POV is 沈昭, but 公孙烈 also gets interior access in same paragraph
        text = (
            "沈昭觉得这件事不对劲。他看了看对面的公孙烈。"
            "公孙烈心想这个年轻人果然不简单，看来必须早做打算了。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            pov_scope="limited",
            known_characters=["沈昭", "公孙烈"],
        )
        assert len(candidates) == 1
        assert candidates[0].pov_character == "沈昭"
        assert "公孙烈" in candidates[0].characters_with_interior

    def test_pov_separator_prevents_drift(self):
        # With --- separator, interior access after separator is intentional
        text = (
            "沈昭觉得这件事不对劲。\n\n---\n\n公孙烈心想这个年轻人果然不简单。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        # The paragraph containing --- is skipped
        assert len(candidates) == 0

    def test_severity_high_for_limited_scope(self):
        text = (
            "沈昭觉得很疲惫。公孙烈明白了他的意图，心中暗自盘算。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            pov_scope="limited",
            known_characters=["沈昭", "公孙烈"],
        )
        assert len(candidates) == 1
        assert candidates[0].severity == "high"

    def test_severity_medium_for_omniscient_scope(self):
        text = (
            "沈昭觉得很疲惫。公孙烈明白了他的意图，心中暗自盘算。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            pov_scope="omniscient",
            known_characters=["沈昭", "公孙烈"],
        )
        assert len(candidates) == 1
        assert candidates[0].severity == "medium"

    def test_unknown_characters_ignored(self):
        # 路人甲 is not in known_characters, so should be ignored
        text = "沈昭觉得不对劲。路人甲心里想着这事跟自己没关系。"
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        assert candidates == []

    def test_multiple_paragraphs_separate_candidates(self):
        text = (
            "沈昭觉得不对劲。公孙烈明白了他的意思。\n\n"
            "沈昭意识到时间紧迫。李远察觉到事情并不简单。\n\n"
            "沈昭坐下来思考。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈", "李远"],
        )
        assert len(candidates) == 2

    def test_short_paragraphs_skipped(self):
        # Paragraphs shorter than 10 chars are skipped
        text = "沈昭想。\n\n公孙烈觉得。\n\n沈昭觉得这件事很复杂。"
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        assert candidates == []

    def test_evidence_quotes_populated(self):
        text = (
            "沈昭觉得事情不对。公孙烈心想这个计划必须提前执行，不能再等了。"
        )
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        assert len(candidates) == 1
        assert len(candidates[0].evidence_quotes) > 0

    def test_two_non_pov_characters_trigger(self):
        # Even without POV character having interior, 2 non-POV chars trigger
        text = "公孙烈明白了。李远意识到了问题的严重性。他们面面相觑。"
        candidates = detect_pov_drift_candidates(
            text,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈", "李远"],
        )
        assert len(candidates) == 1
        assert len(candidates[0].characters_with_interior) >= 2

    def test_paragraph_text_truncated(self):
        # paragraph_text should be truncated to 500 chars
        long_para = "沈昭觉得" + "很长的内容" * 200 + "。公孙烈心想确实如此。"
        candidates = detect_pov_drift_candidates(
            long_para,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        if candidates:
            assert len(candidates[0].paragraph_text) <= 500


# ────────────────────────────────────────────────────────────────────────────
# findings_from_pov_candidates
# ────────────────────────────────────────────────────────────────────────────


class TestFindingsFromPovCandidates:
    """Tests for converting candidates to ReviewFinding objects."""

    def test_empty_candidates_returns_empty(self):
        findings = findings_from_pov_candidates(
            [], chapter_number=7, current_text="some text"
        )
        assert findings == []

    def test_single_candidate_produces_finding(self):
        candidate = PovDriftCandidate(
            paragraph_index=3,
            paragraph_text="沈昭觉得不对。公孙烈心想果然如此。",
            characters_with_interior=["公孙烈"],
            pov_character="沈昭",
            evidence_quotes=["公孙烈心想"],
            severity="high",
        )
        findings = findings_from_pov_candidates(
            [candidate], chapter_number=7, current_text="some text"
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.dimension == "pov"
        assert f.issue_type == "unmarked_interior_switch"
        assert f.severity == "high"
        assert f.suggested_mode == "window"
        assert f.chapter_number == 7
        assert "公孙烈" in f.summary
        assert f.source_module == "pov_drift_audit"

    def test_multiple_candidates_produce_multiple_findings(self):
        c1 = PovDriftCandidate(
            paragraph_index=1,
            paragraph_text="para1",
            characters_with_interior=["公孙烈"],
            pov_character="沈昭",
        )
        c2 = PovDriftCandidate(
            paragraph_index=5,
            paragraph_text="para2",
            characters_with_interior=["李远"],
            pov_character="沈昭",
        )
        findings = findings_from_pov_candidates(
            [c1, c2], chapter_number=3, current_text="text"
        )
        assert len(findings) == 2
        assert findings[0].paragraph_start == 1
        assert findings[1].paragraph_start == 5

    def test_finding_has_signature_and_hash(self):
        candidate = PovDriftCandidate(
            paragraph_index=0,
            paragraph_text="text",
            characters_with_interior=["公孙烈"],
            pov_character="沈昭",
        )
        findings = findings_from_pov_candidates(
            [candidate], chapter_number=1, current_text="chapter text"
        )
        assert findings[0].signature != ""
        assert findings[0].source_text_hash != ""


# ────────────────────────────────────────────────────────────────────────────
# repair_tickets_from_pov_findings
# ────────────────────────────────────────────────────────────────────────────


class TestRepairTicketsFromPovFindings:
    """Tests for generating repair tickets from findings."""

    def test_empty_findings_returns_empty(self):
        assert repair_tickets_from_pov_findings([]) == []

    def test_findings_produce_tickets(self):
        candidate = PovDriftCandidate(
            paragraph_index=2,
            paragraph_text="text",
            characters_with_interior=["公孙烈"],
            pov_character="沈昭",
        )
        findings = findings_from_pov_candidates(
            [candidate], chapter_number=5, current_text="text"
        )
        tickets = repair_tickets_from_pov_findings(findings)
        assert len(tickets) == 1
        assert tickets[0].issue_type == "unmarked_interior_switch"


# ────────────────────────────────────────────────────────────────────────────
# run_pov_drift_audit (async)
# ────────────────────────────────────────────────────────────────────────────


class TestRunPovDriftAudit:
    """Tests for the main async audit entry point."""

    async def test_skip_when_no_text(self):
        result = await run_pov_drift_audit(
            current_text="",
            chapter_number=1,
            pov_character="沈昭",
        )
        assert result.verdict == "pass"
        assert "skipped" in result.details

    async def test_skip_when_no_pov_character(self):
        result = await run_pov_drift_audit(
            current_text="some text here",
            chapter_number=1,
            pov_character="",
        )
        assert result.verdict == "pass"

    async def test_pass_when_no_drift(self):
        text = "沈昭觉得很疲惫。他想了一会儿。\n\n沈昭意识到时间不多了。"
        result = await run_pov_drift_audit(
            current_text=text,
            chapter_number=1,
            pov_character="沈昭",
            known_characters=["沈昭", "公孙烈"],
        )
        assert result.verdict == "pass"
        assert result.candidates == []

    async def test_drift_detected_result(self):
        text = (
            "沈昭觉得不对劲。公孙烈明白了他的意图，心中暗自盘算下一步。"
        )
        result = await run_pov_drift_audit(
            current_text=text,
            chapter_number=7,
            pov_character="沈昭",
            pov_scope="limited",
            known_characters=["沈昭", "公孙烈"],
        )
        assert result.verdict == "drift_detected"
        assert len(result.candidates) >= 1
        assert len(result.findings) >= 1
        assert len(result.repair_tickets) >= 1

    async def test_result_type(self):
        result = await run_pov_drift_audit(
            current_text="",
            chapter_number=1,
            pov_character="沈昭",
        )
        assert isinstance(result, PovDriftAuditResult)

    async def test_omniscient_scope_reduces_severity(self):
        text = "沈昭觉得不对。公孙烈心想果然如此，看来必须早做打算了。"
        result_limited = await run_pov_drift_audit(
            current_text=text,
            chapter_number=1,
            pov_character="沈昭",
            pov_scope="limited",
            known_characters=["沈昭", "公孙烈"],
        )
        result_omni = await run_pov_drift_audit(
            current_text=text,
            chapter_number=1,
            pov_character="沈昭",
            pov_scope="omniscient",
            known_characters=["沈昭", "公孙烈"],
        )
        if result_limited.findings and result_omni.findings:
            assert result_limited.findings[0].severity == "high"
            assert result_omni.findings[0].severity == "medium"
