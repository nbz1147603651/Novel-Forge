"""Unit tests for extraction_quality post-extraction validation."""

from __future__ import annotations

from novel_forge.pipeline.long.services.quality.extraction_quality import (
    clean_placeholder_deltas,
    is_placeholder_plot_thread_delta,
    is_placeholder_relationship_delta,
    validate_creative_report_payload,
)

_LONG_TEXT = "正文" * 2000  # >2000 chars to trigger "long chapter"


# ---------------------------------------------------------------------------
# is_placeholder_relationship_delta
# ---------------------------------------------------------------------------


class TestIsPlaceholderRelationshipDelta:
    def test_empty_change_summary_default_trust_tension(self):
        delta = {
            "pair_id": "A--B",
            "change_summary": "",
            "relationship": {
                "pair_id": "A--B",
                "characters": ["A", "B"],
                "trust": 0.5,
                "tension": 0.5,
            },
        }
        assert is_placeholder_relationship_delta(delta) is True

    def test_nonempty_change_summary_not_placeholder(self):
        delta = {
            "pair_id": "A--B",
            "change_summary": "Trust increased after shared danger",
            "relationship": {
                "pair_id": "A--B",
                "characters": ["A", "B"],
                "trust": 0.7,
                "tension": 0.3,
            },
        }
        assert is_placeholder_relationship_delta(delta) is False

    def test_non_change_summary_is_placeholder(self):
        delta = {
            "pair_id": "母亲--儿子",
            "change_summary": "本章正文未直接出现母子互动",
            "relationship": {
                "pair_id": "母亲--儿子",
                "characters": ["母亲", "儿子"],
                "trust": 0.5,
                "tension": 0.5,
            },
        }
        assert is_placeholder_relationship_delta(delta) is True

    def test_empty_change_summary_non_default_trust_not_placeholder(self):
        delta = {
            "pair_id": "A--B",
            "change_summary": "",
            "relationship": {
                "pair_id": "A--B",
                "characters": ["A", "B"],
                "trust": 0.8,
                "tension": 0.5,
            },
        }
        assert is_placeholder_relationship_delta(delta) is False

    def test_not_a_dict(self):
        assert is_placeholder_relationship_delta("not a dict") is False
        assert is_placeholder_relationship_delta(None) is False

    def test_missing_relationship_key(self):
        delta = {"pair_id": "A--B", "change_summary": ""}
        assert is_placeholder_relationship_delta(delta) is False


# ---------------------------------------------------------------------------
# is_placeholder_plot_thread_delta
# ---------------------------------------------------------------------------


class TestIsPlaceholderPlotThreadDelta:
    def test_empty_change_summary_default_thread(self):
        delta = {
            "thread_id": "t1",
            "change_summary": "",
            "thread": {
                "thread_id": "t1",
                "title": "Thread 1",
                "status": "active",
                "summary": "",
                "last_touched_chapter": 0,
            },
        }
        assert is_placeholder_plot_thread_delta(delta) is True

    def test_nonempty_change_summary_not_placeholder(self):
        delta = {
            "thread_id": "t1",
            "change_summary": "Thread advanced via discovery",
            "thread": {
                "thread_id": "t1",
                "title": "Thread 1",
                "status": "active",
                "summary": "",
                "last_touched_chapter": 0,
            },
        }
        assert is_placeholder_plot_thread_delta(delta) is False

    def test_non_default_status_not_placeholder(self):
        delta = {
            "thread_id": "t1",
            "change_summary": "",
            "thread": {
                "thread_id": "t1",
                "title": "Thread 1",
                "status": "resolved",
                "summary": "",
                "last_touched_chapter": 0,
            },
        }
        assert is_placeholder_plot_thread_delta(delta) is False

    def test_non_default_last_touched_not_placeholder(self):
        delta = {
            "thread_id": "t1",
            "change_summary": "",
            "thread": {
                "thread_id": "t1",
                "title": "Thread 1",
                "status": "active",
                "summary": "",
                "last_touched_chapter": 5,
            },
        }
        assert is_placeholder_plot_thread_delta(delta) is False


# ---------------------------------------------------------------------------
# clean_placeholder_deltas
# ---------------------------------------------------------------------------


class TestCleanPlaceholderDeltas:
    def test_removes_placeholder_relationship_deltas(self):
        payload = {
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "Real change",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.7,
                        "tension": 0.3,
                    },
                },
                {
                    "pair_id": "C--D",
                    "change_summary": "",
                    "relationship": {
                        "pair_id": "C--D",
                        "characters": ["C", "D"],
                        "trust": 0.5,
                        "tension": 0.5,
                    },
                },
            ],
            "plot_thread_deltas": [],
        }
        cleaned = clean_placeholder_deltas(payload)
        assert len(cleaned["relationship_deltas"]) == 1
        assert cleaned["relationship_deltas"][0]["pair_id"] == "A--B"

    def test_removes_placeholder_plot_thread_deltas(self):
        payload = {
            "relationship_deltas": [],
            "plot_thread_deltas": [
                {
                    "thread_id": "t1",
                    "change_summary": "",
                    "thread": {
                        "thread_id": "t1",
                        "title": "T1",
                        "status": "active",
                        "summary": "",
                        "last_touched_chapter": 0,
                    },
                },
            ],
        }
        cleaned = clean_placeholder_deltas(payload)
        assert len(cleaned["plot_thread_deltas"]) == 0

    def test_preserves_valid_deltas(self):
        payload = {
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "Real change",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.7,
                        "tension": 0.3,
                    },
                },
            ],
            "plot_thread_deltas": [
                {
                    "thread_id": "t1",
                    "change_summary": "Thread progressed",
                    "thread": {
                        "thread_id": "t1",
                        "title": "T1",
                        "status": "active",
                        "summary": "",
                        "last_touched_chapter": 5,
                    },
                },
            ],
        }
        cleaned = clean_placeholder_deltas(payload)
        assert len(cleaned["relationship_deltas"]) == 1
        assert len(cleaned["plot_thread_deltas"]) == 1

    def test_removes_non_change_relationship_summaries(self):
        payload = {
            "relationship_deltas": [
                {
                    "pair_id": "母亲--儿子",
                    "change_summary": "本章正文未直接出现母子互动",
                    "relationship": {
                        "pair_id": "母亲--儿子",
                        "characters": ["母亲", "儿子"],
                        "trust": 0.5,
                        "tension": 0.5,
                    },
                },
            ],
        }
        cleaned = clean_placeholder_deltas(payload)
        assert cleaned["relationship_deltas"] == []

    def test_does_not_mutate_original(self):
        payload = {
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.5,
                        "tension": 0.5,
                    },
                },
            ],
        }
        original_len = len(payload["relationship_deltas"])
        clean_placeholder_deltas(payload)
        assert len(payload["relationship_deltas"]) == original_len


# ---------------------------------------------------------------------------
# validate_creative_report_payload
# ---------------------------------------------------------------------------


class TestValidateCreativeReportPayload:
    def test_hollow_long_chapter_triggers_retry(self):
        payload = {
            "creative_report": {
                "structured_summary": "",
                "must_carry_forward": [],
                "suggestions_for_next_chapter": "",
                "bridge_hints": [],
            },
            "relationship_deltas": [],
            "plot_thread_deltas": [],
        }
        report = validate_creative_report_payload(payload, _LONG_TEXT, 7)
        assert report.is_hollow is True
        assert report.retry_recommended is True
        assert report.severity in ("warn", "hard_fail")

    def test_hollow_short_chapter_no_retry(self):
        payload = {
            "creative_report": {
                "structured_summary": "",
                "must_carry_forward": [],
                "suggestions_for_next_chapter": "",
                "bridge_hints": [],
            },
        }
        report = validate_creative_report_payload(payload, "短章", 1)
        assert report.is_hollow is True
        assert report.retry_recommended is False
        assert report.severity == "warn"

    def test_substantive_report_passes(self):
        payload = {
            "creative_report": {
                "structured_summary": "本章描述了朝堂辩论的激烈场面",
                "must_carry_forward": ["皇帝态度转变"],
                "suggestions_for_next_chapter": "关注暗线发展",
                "bridge_hints": ["注意铁幕裂缝的后续影响"],
            },
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "Trust increased",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.7,
                        "tension": 0.3,
                    },
                },
            ],
        }
        report = validate_creative_report_payload(payload, _LONG_TEXT, 4)
        assert report.is_hollow is False
        assert report.severity == "pass"
        assert report.retry_recommended is False

    def test_detects_placeholder_deltas(self):
        payload = {
            "creative_report": {
                "structured_summary": "有内容",
                "must_carry_forward": [],
                "suggestions_for_next_chapter": "",
                "bridge_hints": [],
            },
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.5,
                        "tension": 0.5,
                    },
                },
            ],
            "plot_thread_deltas": [
                {
                    "thread_id": "t1",
                    "change_summary": "",
                    "thread": {
                        "thread_id": "t1",
                        "title": "T1",
                        "status": "active",
                        "summary": "",
                        "last_touched_chapter": 0,
                    },
                },
            ],
        }
        report = validate_creative_report_payload(payload, _LONG_TEXT, 7)
        assert len(report.placeholder_relationship_deltas) == 1
        assert len(report.placeholder_plot_thread_deltas) == 1

    def test_hard_fail_long_chapter_no_deltas(self):
        payload = {
            "creative_report": {
                "structured_summary": "",
                "must_carry_forward": [],
                "suggestions_for_next_chapter": "",
                "bridge_hints": [],
            },
            "relationship_deltas": [],
            "plot_thread_deltas": [],
            "character_state_deltas": [],
        }
        report = validate_creative_report_payload(payload, _LONG_TEXT, 7)
        assert report.severity == "hard_fail"

    def test_missing_creative_report_key(self):
        payload = {}
        report = validate_creative_report_payload(payload, _LONG_TEXT, 7)
        assert report.is_hollow is True

    def test_quiet_chapter_with_valid_deltas_not_hard_fail(self):
        """A quiet chapter with valid deltas should not be hard-failed."""
        payload = {
            "creative_report": {
                "structured_summary": "过渡章节，主要描写旅途风景",
                "must_carry_forward": [],
                "suggestions_for_next_chapter": "",
                "bridge_hints": [],
            },
            "relationship_deltas": [
                {
                    "pair_id": "A--B",
                    "change_summary": "旅途中的默契",
                    "relationship": {
                        "pair_id": "A--B",
                        "characters": ["A", "B"],
                        "trust": 0.6,
                        "tension": 0.4,
                    },
                },
            ],
        }
        report = validate_creative_report_payload(payload, _LONG_TEXT, 3)
        assert report.is_hollow is False
        assert report.severity == "pass"
