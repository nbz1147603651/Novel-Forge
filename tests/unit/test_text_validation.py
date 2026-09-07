"""Unit tests for novel_forge.core.utils.text_validation."""

from novel_forge.core.utils.text_validation import (
    assess_word_count,
    check_revelation_density,
    check_word_count,
    count_chapter_words,
    display_word_count,
)


class TestCheckWordCount:
    """Tests for check_word_count."""

    def test_exact_match(self):
        result = check_word_count("你好世界", 4)
        assert result["actual_count"] == 4
        assert result["target"] == 4
        assert result["deviation_pct"] == 0.0
        assert result["within_tolerance"] is True
        assert result["status"] == "pass"

    def test_within_tolerance(self):
        result = check_word_count("你好世界测试", 10, 0.5)
        assert result["actual_count"] == 6
        assert result["deviation_pct"] == 40.0
        assert result["within_tolerance"] is True
        assert result["status"] == "pass"

    def test_warning_level(self):
        result = check_word_count("你好", 10, 0.15)
        assert result["actual_count"] == 2
        assert result["deviation_pct"] == 80.0
        assert result["within_tolerance"] is False
        assert result["status"] == "fail"

    def test_warning_level_boundary(self):
        result = check_word_count("你好世界", 5, 0.15)
        assert result["actual_count"] == 4
        assert result["deviation_pct"] == 20.0
        assert result["within_tolerance"] is False
        assert result["status"] == "warning"

    def test_fail_level(self):
        result = check_word_count("你好", 100, 0.15)
        assert result["actual_count"] == 2
        assert result["deviation_pct"] == 98.0
        assert result["within_tolerance"] is False
        assert result["status"] == "fail"

    def test_excludes_whitespace(self):
        result = check_word_count("你 好 世 界", 4)
        assert result["actual_count"] == 4
        assert result["status"] == "pass"

    def test_excludes_punctuation(self):
        result = check_word_count("你好，世界！", 4)
        assert result["actual_count"] == 4
        assert result["status"] == "pass"

    def test_excludes_latin(self):
        result = check_word_count("你好hello世界", 4)
        assert result["actual_count"] == 4
        assert result["status"] == "pass"

    def test_empty_string(self):
        result = check_word_count("", 10)
        assert result["actual_count"] == 0
        assert result["deviation_pct"] == 100.0
        assert result["status"] == "fail"

    def test_none_text(self):
        result = check_word_count(None, 10)
        assert result["actual_count"] == 0

    def test_zero_target(self):
        result = check_word_count("你好", 0)
        assert result["actual_count"] == 2
        assert result["deviation_pct"] == 100.0

    def test_custom_tolerance(self):
        result = check_word_count("你好世界", 5, tolerance=0.25)
        assert result["actual_count"] == 4
        assert result["deviation_pct"] == 20.0
        assert result["within_tolerance"] is True
        assert result["status"] == "pass"

    def test_mixed_cjk_and_non_cjk(self):
        result = check_word_count("中文123abc测试", 6)
        assert result["actual_count"] == 4
        assert result["target"] == 6
        assert result["deviation_pct"] == 33.33

    def test_count_chapter_words_falls_back_for_non_cjk(self):
        assert count_chapter_words("hello world, again") == 3

    def test_count_chapter_words_ignores_non_prose_marks_for_cjk(self):
        assert count_chapter_words("你好，world 123！\n“世界”") == 4

    def test_display_word_count_ignores_chinese_punctuation(self):
        assert display_word_count("你好，世界！“再见”。") == 6

    def test_display_word_count_scrubs_prompt_artifacts(self):
        text = "正文。\nscene_intent: 这里是规划提示，不应进入展示字数。"
        assert display_word_count(text) == 2

    def test_display_word_count_ignores_markdown_headings(self):
        assert display_word_count("# 第 1 章 · 标题\n正文。") == 2

    def test_long_chinese_text(self):
        text = "这是一段很长的中文文本，用于测试字数统计功能是否正常工作。" * 10
        result = check_word_count(text, 300)
        assert result["actual_count"] > 0
        assert isinstance(result["deviation_pct"], float)


class TestAssessWordCount:
    """Tests for the long-form archive word-count policy."""

    def test_ideal_band_accepts(self):
        result = assess_word_count("字" * 4500, 4500)
        assert result.band == "ideal"
        assert result.action == "accept"
        assert result.within_acceptable is True

    def test_acceptable_band_accepts(self):
        result = assess_word_count("字" * 5175, 4500)
        assert result.band == "acceptable"
        assert result.action == "accept"

    def test_buffer_band_requests_light_adjust(self):
        result = assess_word_count("字" * 5300, 4500)
        assert result.band == "buffer"
        assert result.action == "light_adjust"

    def test_structural_band_requests_restructure(self):
        result = assess_word_count("字" * 5500, 4500)
        assert result.band == "structural"
        assert result.action == "structural_restructure"

    def test_hard_reject_requests_restructure_before_archive(self):
        result = assess_word_count("字" * 5700, 4500)
        assert result.band == "hard_reject"
        assert result.action == "structural_restructure"


class TestCheckRevelationDensity:
    """Tests for check_revelation_density."""

    def test_no_revelations(self):
        result = check_revelation_density("他走进了房间，坐下来喝茶。")
        assert result["revelation_count"] == 0
        assert result["max_allowed"] == 2
        assert result["within_budget"] is True
        assert result["status"] == "pass"

    def test_single_revelation(self):
        result = check_revelation_density("他原来是个好人", 2)
        assert result["revelation_count"] == 1
        assert result["within_budget"] is True
        assert result["status"] == "pass"

    def test_multiple_markers(self):
        result = check_revelation_density("原来他竟然才发现真相", 2)
        assert result["revelation_count"] == 4
        assert result["within_budget"] is False
        assert result["status"] == "warning"

    def test_within_budget(self):
        result = check_revelation_density("原来他竟然是卧底", 2)
        assert result["revelation_count"] == 2
        assert result["within_budget"] is True
        assert result["status"] == "pass"

    def test_warning_level(self):
        result = check_revelation_density("原来他竟然揭露了真相", 2)
        assert result["revelation_count"] == 4
        assert result["within_budget"] is False
        assert result["status"] == "warning"

    def test_fail_level_excessive(self):
        text = "原来他竟然才发现真相揭露揭示意外发现出乎意料始料未及"
        result = check_revelation_density(text, 2)
        assert result["revelation_count"] == 9
        assert result["within_budget"] is False
        assert result["status"] == "fail"

    def test_empty_string(self):
        result = check_revelation_density("")
        assert result["revelation_count"] == 0
        assert result["status"] == "pass"

    def test_none_text(self):
        result = check_revelation_density(None)
        assert result["revelation_count"] == 0

    def test_custom_max_revelations(self):
        result = check_revelation_density("原来他竟然", max_revelations=5)
        assert result["revelation_count"] == 2
        assert result["within_budget"] is True
        assert result["status"] == "pass"

    def test_all_markers(self):
        text = "原来他竟然才发现真相揭露揭示意外发现出乎意料始料未及"
        result = check_revelation_density(text)
        assert result["revelation_count"] == 9

    def test_marker_in_context(self):
        text = (
            "他走进房间，意外发现桌上有一封信。"
            "原来这封信竟然是写给他的。"
            "他这才发现真相远比他想象的复杂。"
        )
        result = check_revelation_density(text, max_revelations=3)
        assert result["revelation_count"] == 5
        assert result["within_budget"] is False

    def test_no_false_positives(self):
        text = "他原本的计划是好的，然而事情发展出乎预期。"
        result = check_revelation_density(text)
        assert result["revelation_count"] == 0
