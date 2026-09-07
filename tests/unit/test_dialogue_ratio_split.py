"""Unit tests for dialogue_ratio split (protagonist vs non-protagonist).

Added in M4 — see docs/ai_flavor_quality.md.

Resolves the structural conflict between:
- spec.dialogue_ratio = "high"  → 50-70% of chapter is dialogue
- character_silence = True      → protagonist barely speaks

When character_silence=True, the effective dialogue_ratio requirement
becomes "non_protagonist dialogue >= 50%", not "all dialogue >= 50%".
"""

from __future__ import annotations

from novel_forge.pipeline.long.services.quality.style_metrics import (
    DialogueSpeakerSplit,
    extract_dialogue_speakers,
    split_dialogue_ratio,
)

# ---------------------------------------------------------------------------
# DialogueSpeakerSplit dataclass
# ---------------------------------------------------------------------------


class TestDialogueSpeakerSplitDataclass:
    def test_default_zeros(self) -> None:
        s = DialogueSpeakerSplit()
        assert s.total_dialogue_chars == 0
        assert s.protagonist_dialogue_chars == 0
        assert s.non_protagonist_dialogue_chars == 0

    def test_total_equals_sum_of_parts(self) -> None:
        s = DialogueSpeakerSplit(
            protagonist_dialogue_chars=100,
            non_protagonist_dialogue_chars=300,
        )
        assert s.total_dialogue_chars == 400


# ---------------------------------------------------------------------------
# extract_dialogue_speakers — pure regex parser
# ---------------------------------------------------------------------------


class TestExtractDialogueSpeakers:
    def test_empty_text_returns_empty(self) -> None:
        assert extract_dialogue_speakers("") == []

    def test_extracts_speaker_name_for_attributed_dialogue(self) -> None:
        """extract_dialogue_speakers returns raw speaker_name from attribution;
        role assignment is the aggregator's responsibility."""
        text = '沈鹿溪说：「你好。」'
        speakers = extract_dialogue_speakers(text)
        assert len(speakers) >= 1
        assert speakers[0]["speaker_name"] == "沈鹿溪"
        assert "你好" in speakers[0]["text"]

    def test_extracts_named_non_protagonist(self) -> None:
        text = '陈屿说：「你好。」'
        speakers = extract_dialogue_speakers(text)
        assert speakers[0]["speaker_name"] == "陈屿"

    def test_extracts_low_voice_attribution(self) -> None:
        text = '苏皖低声道：「我去拿。」'
        speakers = extract_dialogue_speakers(text)
        assert speakers[0]["speaker_name"] == "苏皖"

    def test_unknown_speaker_when_no_attribution(self) -> None:
        """Pure dialogue with no surrounding narration has speaker_name=''."""
        text = '"你好。"她说'
        speakers = extract_dialogue_speakers(text)
        # No name attribution was found — speaker_name defaults to empty.
        if speakers:
            assert speakers[0]["speaker_name"] == ""

    def test_multiple_dialogue_segments_have_speaker_names(self) -> None:
        text = (
            '沈鹿溪说：「我先看看。」\n\n'
            '陈屿说：「我帮你。」\n\n'
            '沈鹿溪说：「不用。」'
        )
        speakers = extract_dialogue_speakers(text)
        names = [s["speaker_name"] for s in speakers]
        assert names.count("沈鹿溪") == 2
        assert names.count("陈屿") == 1


# ---------------------------------------------------------------------------
# split_dialogue_ratio — aggregates speakers into a report
# ---------------------------------------------------------------------------


class TestSplitDialogueRatio:
    def test_empty_text(self) -> None:
        split = split_dialogue_ratio("", protagonist_names=["沈鹿溪"])
        assert split.total_dialogue_chars == 0
        assert split.non_protagonist_dialogue_chars == 0

    def test_with_protagonist_names(self) -> None:
        text = (
            '沈鹿溪说：「我先看看。」\n\n'
            '陈屿说：「那你要看什么？」\n\n'
            '周婶说：「要不喝点茶？」'
        )
        split = split_dialogue_ratio(
            text,
            protagonist_names=["沈鹿溪"],
        )
        assert split.protagonist_dialogue_chars > 0
        assert split.non_protagonist_dialogue_chars > 0
        assert split.total_dialogue_chars == (
            split.protagonist_dialogue_chars + split.non_protagonist_dialogue_chars
        )

    def test_all_dialogue_protagonist(self) -> None:
        text = (
            '沈鹿溪说：「第一段。」\n\n'
            '沈鹿溪说：「第二段。」'
        )
        split = split_dialogue_ratio(text, protagonist_names=["沈鹿溪"])
        assert split.non_protagonist_dialogue_chars == 0
        assert split.protagonist_dialogue_chars > 0


# ---------------------------------------------------------------------------
# silence-aware effective ratio (the headline feature)
# ---------------------------------------------------------------------------


class TestSilenceAwareRatio:
    def test_effective_ratio_uses_non_protagonist_when_silence(self) -> None:
        """When character_silence=True, non_protagonist ratio is the headline metric."""
        from novel_forge.pipeline.long.services.quality.style_metrics import (
            compute_silence_aware_dialogue_ratio,
        )
        text = (
            '陈屿说：「那你要看什么？」\n\n'
            '周婶说：「要不喝点茶？」\n\n'
            '沈鹿溪说：「嗯。」'  # protagonist says 1 char
        )
        # Overall: ~ (12 + 8 + 1) chars out of ~50 chars prose ~ 42%
        # Non-protagonist: ~ 20 chars out of 50 prose chars ~ 40%
        result = compute_silence_aware_dialogue_ratio(
            text,
            protagonist_names=["沈鹿溪"],
            character_silence=True,
        )
        assert "non_protagonist_ratio" in result
        assert "protagonist_ratio" in result
        assert "effective_ratio" in result
        # Effective ratio should equal non_protagonist ratio when silence=True
        assert result["effective_ratio"] == result["non_protagonist_ratio"]
        # Protagonist ratio should be tiny (just "嗯")
        assert result["protagonist_ratio"] < result["non_protagonist_ratio"]

    def test_effective_ratio_uses_total_when_not_silence(self) -> None:
        from novel_forge.pipeline.long.services.quality.style_metrics import (
            compute_silence_aware_dialogue_ratio,
        )
        text = (
            '沈鹿溪说：「第一段。」\n\n'
            '陈屿说：「第二段。」'
        )
        result = compute_silence_aware_dialogue_ratio(
            text,
            protagonist_names=["沈鹿溪"],
            character_silence=False,
        )
        # When not in silence mode, effective == total
        assert result["effective_ratio"] == result["total_ratio"]