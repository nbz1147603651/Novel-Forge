"""Unit tests for style_metrics service module."""

from __future__ import annotations

from novel_forge.pipeline.long.services.quality.style_metrics import (
    compute_style_metrics,
    style_metrics_to_quality_check,
)

# ---------------------------------------------------------------------------
# compute_style_metrics — dialogue ratio
# ---------------------------------------------------------------------------


class TestDialogueRatio:
    def test_empty_text(self):
        report = compute_style_metrics("", None)
        assert report.dialogue_ratio_pct == 0.0
        assert report.total_char_count == 0

    def test_no_dialogue(self):
        text = "这是一段完全没有对话的叙述文本，描述了远处的山峦和天空。" * 10
        report = compute_style_metrics(text, None)
        assert report.dialogue_ratio_pct == 0.0
        assert report.dialogue_char_count == 0

    def test_chinese_quoted_dialogue(self):
        # 50 chars of dialogue out of ~100 total
        text = "她说了一些话。" + "\u201c" + "这是一段对话内容，描述了角色的想法和感受。" + "\u201d"
        report = compute_style_metrics(text, None)
        assert report.dialogue_ratio_pct > 0.0
        assert report.dialogue_char_count > 0

    def test_ascii_quoted_dialogue(self):
        text = "She spoke. " + '"' + "This is dialogue content with some words." + '"'
        report = compute_style_metrics(text, None)
        assert report.dialogue_ratio_pct > 0.0

    def test_mixed_dialogue_and_narration(self):
        dialogue = "\u201c" + "你好，我是张三，今天来这里是为了那件事。" + "\u201d"
        narration = "他站在门口，看着远处的山峦，心中涌起一股莫名的感慨。" * 5
        text = dialogue + narration
        report = compute_style_metrics(text, None)
        assert 0.0 < report.dialogue_ratio_pct < 50.0

    def test_high_dialogue_ratio(self):
        lines = []
        for i in range(20):
            lines.append(f"\u201c这是第{i}句对话，内容比较丰富。\u201d")
        text = "".join(lines)
        report = compute_style_metrics(text, None)
        # Almost all text is in quotes
        assert report.dialogue_ratio_pct > 70.0

    def test_character_silence_uses_non_protagonist_ratio(self):
        text = (
            "陈屿说：“" + ("配角承担对话。" * 20) + "”"
            "沈鹿溪说：“嗯。”"
        )
        report = compute_style_metrics(
            text,
            {"global_style": {"dialogue_ratio": "high"}},
            protagonist_names=["沈鹿溪"],
            character_silence=True,
        )
        assert report.character_silence_mode is True
        assert report.total_dialogue_ratio_pct > report.dialogue_ratio_pct
        assert report.dialogue_ratio_pct > 50.0
        assert report.protagonist_dialogue_char_count > 0
        assert report.non_protagonist_dialogue_char_count > 0


# ---------------------------------------------------------------------------
# compute_style_metrics — target level
# ---------------------------------------------------------------------------


class TestDialogueTarget:
    def test_no_style_profile(self):
        report = compute_style_metrics("正文", None)
        assert report.dialogue_target_level == ""
        assert report.dialogue_target_range == (0, 0)

    def test_high_target(self):
        profile = {"global_style": {"dialogue_ratio": "high"}}
        report = compute_style_metrics("正文", profile)
        assert report.dialogue_target_level == "high"
        assert report.dialogue_target_range == (50, 70)

    def test_medium_target(self):
        profile = {"global_style": {"dialogue_ratio": "medium"}}
        report = compute_style_metrics("正文", profile)
        assert report.dialogue_target_level == "medium"
        assert report.dialogue_target_range == (30, 45)

    def test_invalid_level_defaults_to_empty(self):
        profile = {"global_style": {"dialogue_ratio": "invalid"}}
        report = compute_style_metrics("正文", profile)
        assert report.dialogue_target_level == ""


# ---------------------------------------------------------------------------
# compute_style_metrics — repeated phrases
# ---------------------------------------------------------------------------


class TestRepeatedPhrases:
    def test_no_repetition(self):
        text = "这是一段独特的文本，每个短语都只出现一次。" * 2
        report = compute_style_metrics(text, None)
        # Even with repetition of short text, n-gram should find repeats
        # but the text is very short, so might not meet threshold
        assert isinstance(report.repeated_image_phrases, list)

    def test_repeated_phrase_detected(self):
        target = "远处的山峦像是一幅画卷"
        text = f"第一段：{target}。第二段：{target}。第三段：{target}。第四段：{target}。"
        report = compute_style_metrics(text, None)
        found_phrases = [r["phrase"] for r in report.repeated_image_phrases]
        # The phrase or a substring of it should be detected
        assert any(target in found or found in target for found in found_phrases)

    def test_short_text_no_repetition(self):
        report = compute_style_metrics("短文本", None)
        assert report.repeated_image_phrases == []


# ---------------------------------------------------------------------------
# compute_style_metrics — banned phrases
# ---------------------------------------------------------------------------


class TestBannedPhrases:
    def test_no_banned_phrases_configured(self):
        report = compute_style_metrics("一些文本", None)
        assert report.banned_phrase_hits == []

    def test_banned_phrase_detected(self):
        profile = {"global_style": {"banned_phrases": ["像是又像"]}}
        text = "远处的雾气像是又像是远方的记忆，像是又像是昨日的梦境。"
        report = compute_style_metrics(text, profile)
        assert len(report.banned_phrase_hits) == 1
        assert report.banned_phrase_hits[0]["phrase"] == "像是又像"
        assert report.banned_phrase_hits[0]["count"] == 2

    def test_banned_phrase_not_in_text(self):
        profile = {"global_style": {"banned_phrases": ["不存在的短语"]}}
        text = "这是一段完全不包含禁用短语的正常文本。"
        report = compute_style_metrics(text, profile)
        assert report.banned_phrase_hits == []

    def test_empty_banned_phrases_list(self):
        profile = {"global_style": {"banned_phrases": []}}
        report = compute_style_metrics("一些文本", profile)
        assert report.banned_phrase_hits == []


# ---------------------------------------------------------------------------
# style_metrics_to_quality_check
# ---------------------------------------------------------------------------


class TestStyleMetricsToQualityCheck:
    def test_warn_mode_always_passes(self):
        report = compute_style_metrics("几乎没有对话的文本" * 50, {"global_style": {"dialogue_ratio": "high"}})
        check = style_metrics_to_quality_check(report, gate_mode="warn")
        assert check.passed is True
        assert check.dimension == "style_dialogue_ratio"

    def test_block_mode_fails_below_threshold(self):
        report = compute_style_metrics("几乎没有对话的文本" * 50, {"global_style": {"dialogue_ratio": "high"}})
        check = style_metrics_to_quality_check(report, gate_mode="block", repair_threshold_pct=25.0)
        assert check.passed is False

    def test_above_target_passes(self):
        # Create high-dialogue text
        lines = [f"\u201c这是第{i}句对话内容，比较长。\u201d" for i in range(30)]
        text = "".join(lines)
        report = compute_style_metrics(text, {"global_style": {"dialogue_ratio": "high"}})
        check = style_metrics_to_quality_check(report, gate_mode="block", repair_threshold_pct=25.0)
        # High dialogue ratio should pass
        assert check.passed is True

    def test_message_includes_metrics(self):
        profile = {"global_style": {"banned_phrases": ["禁用短语"], "dialogue_ratio": "medium"}}
        text = "一些文本中包含禁用短语。" * 10
        report = compute_style_metrics(text, profile)
        check = style_metrics_to_quality_check(report, gate_mode="warn")
        assert "对话比例" in check.message

    def test_details_populated(self):
        report = compute_style_metrics("测试文本" * 10, None)
        check = style_metrics_to_quality_check(report, gate_mode="warn")
        assert "dialogue_ratio_pct" in check.details
        assert "total_dialogue_ratio_pct" in check.details
        assert "character_silence_mode" in check.details
        assert "gate_mode" in check.details

    def test_silence_mode_message_names_effective_ratio(self):
        text = "沈鹿溪说：“" + ("主角一直说话。" * 40) + "”"
        report = compute_style_metrics(
            text,
            {"global_style": {"dialogue_ratio": "high"}},
            protagonist_names=["沈鹿溪"],
            character_silence=True,
        )
        check = style_metrics_to_quality_check(
            report,
            gate_mode="block",
            repair_threshold_pct=25.0,
        )
        assert check.passed is False
        assert check.score == 0.0
        assert "沉默模式" in check.message
