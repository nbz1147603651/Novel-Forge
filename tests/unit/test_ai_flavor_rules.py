"""Unit tests for the five new AI-flavor morphological detectors added to
HumanizeScanStep._HUMANIZE_RULES.

These detectors target *form-level* AI patterns rather than banned content phrases:
- weak_verb_stacking: 感到/觉得/意识到/似乎/仿佛/不禁/不由得/情不自禁 chains
- tautology_marker: 「某种……」 three-peat within a short span
- binary_judgment_closing: 「X 不是 Y，而是 Z」 closing structures
- pronoun_disappearance_run: 「她没有……她没有……」 subject-collapse runs
- precise_timestamp_overuse: 「三分二十七秒」 over-precise timestamps

They were added in response to quality issues surfaced in 山风与归人2, where the
existing 24 humanize rules failed to catch form-level AI fingerprints.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep


def _hit_ids_for(text: str, *, filter_dialogue: bool = True) -> list[str]:
    """Return list of pattern_id values surfaced by prescreen for the given text."""
    payload = HumanizeScanStep.prescreen_text(text, filter_dialogue=filter_dialogue)
    return [str(hit["pattern_id"]) for hit in payload]


# ---------------------------------------------------------------------------
# weak_verb_stacking
# ---------------------------------------------------------------------------


class TestWeakVerbStacking:
    """Detect 感到/觉得/意识到/似乎/仿佛 chains that mark weak interiority."""

    def test_detects_basic_chain(self) -> None:
        text = "她感到一种难以言喻的失落涌上心头，又觉得屋里太静。"
        ids = _hit_ids_for(text)
        assert "weak_verb_stacking" in ids

    def test_detects_意识到(self) -> None:
        text = "他忽然意识到，自己已经很久没有这样站在窗前了。"
        ids = _hit_ids_for(text)
        assert "weak_verb_stacking" in ids

    def test_detects_仿佛(self) -> None:
        text = "风铃摇晃，仿佛有人在门外低声说话。"
        ids = _hit_ids_for(text)
        assert "weak_verb_stacking" in ids

    def test_does_not_trigger_on_concrete_sensory(self) -> None:
        # Concrete verbs (摸/握/走) should not trigger.
        text = "她摸了摸风铃的金属片，把摄像机挂在肩上，走了出去。"
        ids = _hit_ids_for(text)
        assert "weak_verb_stacking" not in ids

    def test_severity_is_high(self) -> None:
        text = "她感到空气里有某种凉意，似乎在慢慢靠近。"
        hits = HumanizeScanStep.prescreen_text(text)
        match = [h for h in hits if h["pattern_id"] == "weak_verb_stacking"]
        assert match
        assert match[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# tautology_marker
# ---------------------------------------------------------------------------


class TestTautologyMarker:
    """Detect 「某种……某种……某种」 three-peat within a short span."""

    def test_detects_three_peat_in_one_sentence(self) -> None:
        text = (
            "她注意到某种年长者的欲言又止，某种无声的语言，"
            "和那个比任何语言都更清晰地表明了某种意义的动作。"
        )
        ids = _hit_ids_for(text)
        assert "tautology_marker" in ids

    def test_does_not_trigger_single_某种(self) -> None:
        text = "风从穿堂深处涌出来，带着某种难以形容的凉意。"
        ids = _hit_ids_for(text)
        assert "tautology_marker" not in ids

    def test_does_not_trigger_two_only(self) -> None:
        text = "她停在门槛边，看着某种光落在某种旧木纹上。"
        ids = _hit_ids_for(text)
        # Two instances are below the three-peat threshold.
        assert "tautology_marker" not in ids

    def test_three_peat_across_paragraph_boundary_still_detected(self) -> None:
        text = (
            "这是某种犹豫不决的姿态。\n\n"
            "她站在那里，保持着某种距离。\n\n"
            "这种沉默本身，就是某种信号。"
        )
        ids = _hit_ids_for(text)
        assert "tautology_marker" in ids


# ---------------------------------------------------------------------------
# binary_judgment_closing
# ---------------------------------------------------------------------------


class TestBinaryJudgmentClosing:
    """Detect 「X 不是 Y，而是 Z」 closing structures."""

    def test_detects_not_but_closing(self) -> None:
        text = "风声不是她逃避的方式，而是她正在学着去抓住的东西。"
        ids = _hit_ids_for(text)
        assert "binary_judgment_closing" in ids

    def test_detects_不是_正是_closing(self) -> None:
        text = "这不是她的错，正是她从未学会说话的原因。"
        ids = _hit_ids_for(text)
        assert "binary_judgment_closing" in ids

    def test_does_not_trigger_on_plain_negative(self) -> None:
        text = "她没有回答，把摄像机从胸前取下来放进兜里。"
        ids = _hit_ids_for(text)
        assert "binary_judgment_closing" not in ids


# ---------------------------------------------------------------------------
# pronoun_disappearance_run
# ---------------------------------------------------------------------------


class TestPronounDisappearanceRun:
    """Detect 「她没有……她没有……」 subject-collapse runs."""

    def test_detects_run_of_three(self) -> None:
        text = (
            "她没有说话。她没有回头。她没有打开摄像机。"
        )
        ids = _hit_ids_for(text)
        assert "pronoun_disappearance_run" in ids

    def test_does_not_trigger_single_instance(self) -> None:
        text = "她没有回答，把摄像机从胸前取下来放进兜里。"
        ids = _hit_ids_for(text)
        assert "pronoun_disappearance_run" not in ids


# ---------------------------------------------------------------------------
# precise_timestamp_overuse
# ---------------------------------------------------------------------------


class TestPreciseTimestampOveruse:
    """Detect 「三分二十七秒」 over-precise timestamps."""

    def test_detects_minute_second_format(self) -> None:
        text = "她录了三分二十七秒的风声，又录了四分十一秒的水声。"
        ids = _hit_ids_for(text)
        assert "precise_timestamp_overuse" in ids

    def test_detects_hms_format(self) -> None:
        text = "视频时长 00:03:27，她把进度条拖回开头。"
        ids = _hit_ids_for(text)
        assert "precise_timestamp_overuse" in ids

    def test_does_not_trigger_on_round_duration(self) -> None:
        text = "她录了大约三分钟的风声，把摄像机收进包里。"
        ids = _hit_ids_for(text)
        assert "precise_timestamp_overuse" not in ids


# ---------------------------------------------------------------------------
# Integration: 5 rules fire together in 山风与归人2 Ch21 sample
# ---------------------------------------------------------------------------


class TestShanfengCh21Sample:
    """The exact problems observed in 山风与归人2 Ch21 should all be detected."""

    def test_ch21_fires_multiple_rules(self) -> None:
        text = (
            "她第一次意识到，风声不是她逃避的方式，"
            "是她正在学着去抓住的东西。"
            "\n\n"
            "她注意到某种年长者的欲言又止，某种无声的语言，"
            "和那个比任何语言都更清晰地表明了某种意义的动作。"
        )
        ids = _hit_ids_for(text)
        # Ch21 L157-159 problem:
        assert "weak_verb_stacking" in ids
        assert "binary_judgment_closing" in ids
        # Ch21 L31/L121/L133 problem:
        assert "tautology_marker" in ids


# ---------------------------------------------------------------------------
# Pattern ID registration
# ---------------------------------------------------------------------------


def test_all_five_pattern_ids_are_registered_in_schema() -> None:
    """The HUMANIZE_PATTERN_IDS tuple must include all five new pattern IDs
    so they are accepted by HumanizePatternHit validators downstream."""
    from novel_forge.core.schemas.humanize import HUMANIZE_PATTERN_IDS

    required = {
        "weak_verb_stacking",
        "tautology_marker",
        "binary_judgment_closing",
        "pronoun_disappearance_run",
        "precise_timestamp_overuse",
    }
    missing = required - set(HUMANIZE_PATTERN_IDS)
    assert not missing, f"Pattern IDs missing from HUMANIZE_PATTERN_IDS: {missing}"


def test_pattern_id_normalization_accepts_new_strings() -> None:
    """HumanizePatternHit.field_validator must accept the new pattern IDs as
    stable string ids without aliasing."""
    from novel_forge.core.schemas.humanize import HumanizePatternHit

    for pid in (
        "weak_verb_stacking",
        "tautology_marker",
        "binary_judgment_closing",
        "pronoun_disappearance_run",
        "precise_timestamp_overuse",
    ):
        hit = HumanizePatternHit(
            pattern_id=pid,
            pattern_name="test",
            category="test",
            severity="medium",
            evidence_quote="x",
        )
        assert hit.pattern_id == pid
