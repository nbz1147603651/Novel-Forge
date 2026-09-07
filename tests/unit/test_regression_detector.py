"""Unit tests for RegressionDetector."""

from __future__ import annotations

import asyncio

from novel_forge.workspace.regression_detector import (
    ENTITY_REGRESSION_NEW,
    NARRATIVE_REGRESSION_UNRESOLVED_FORESHADOW,
    STATE_REGRESSION_DEATH,
    STATE_REGRESSION_RELATIONSHIP,
    RegressionDetector,
)


class TestRegressionDetectorInstantiation:
    """Test suite for RegressionDetector instantiation."""

    def test_instantiation(self) -> None:
        """RegressionDetector instantiates with default parameters."""
        detector = RegressionDetector()
        assert detector is not None
        assert detector._entity_confidence_threshold == 0.8
        assert detector._context_window_chars == 200

    def test_instantiation_custom_params(self) -> None:
        """RegressionDetector instantiates with custom parameters."""
        detector = RegressionDetector(
            entity_confidence_threshold=0.9,
            context_window_chars=300,
        )
        assert detector._entity_confidence_threshold == 0.9
        assert detector._context_window_chars == 300


class TestEntityRegression:
    """Test suite for entity regression detection."""

    def test_entity_regression_new_character(self) -> None:
        """Detects new character name not in canon."""
        detector = RegressionDetector()

        original_text = "林远君走在雾霭小镇的街道上。"
        repaired_text = "林远君和张三郎走在雾霭小镇的街道上。"

        canon_state = {
            "characters": {
                "林远君": {"name": "林远君", "alive": True},
            },
        }

        regressions = detector._detect_entity_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert len(regressions) >= 1
        assert any(
            r.issue_type == ENTITY_REGRESSION_NEW
            and r.severity == "warning"
            for r in regressions
        )

    def test_entity_regression_new_unquoted_action_character(self) -> None:
        """Detects a new bare Chinese name when it appears in character-action context."""
        detector = RegressionDetector()

        original_text = "林远走在雾霭小镇的街道上。"
        repaired_text = "林远走在雾霭小镇的街道上。沈青走进巷口，低声问他要去哪。"

        canon_state = {
            "characters": {
                "林远": {"name": "林远", "alive": True},
            },
        }

        regressions = detector._detect_entity_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert any(
            r.issue_type == ENTITY_REGRESSION_NEW
            and r.evidence == "沈青"
            for r in regressions
        )

    def test_entity_regression_new_location(self) -> None:
        """Detects new location not in canon."""
        detector = RegressionDetector()

        original_text = "林远走在雾霭小镇的街道上。"
        repaired_text = "林远走进古城堡，这里阴森恐怖。"

        canon_state = {
            "characters": {
                "林远": {"name": "林远", "alive": True},
            },
            "worldbuilding": {
                "雾霭小镇": {},
            },
        }

        regressions = detector._detect_entity_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert len(regressions) >= 1
        assert any(
            r.issue_type == ENTITY_REGRESSION_NEW
            and r.severity == "info"
            for r in regressions
        )


class TestStateRegression:
    """Test suite for state regression detection."""

    def test_state_regression_character_death(self) -> None:
        """Detects character death state contradiction."""
        detector = RegressionDetector()

        original_text = "林远君站在墓地中。"
        repaired_text = "林远君活着离开了墓地。"

        canon_state = {
            "characters": {
                "林远君": {
                    "name": "林远君",
                    "alive": False,
                },
            },
        }

        regressions = detector._detect_state_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert len(regressions) >= 1
        assert any(
            r.issue_type == STATE_REGRESSION_DEATH
            and r.severity == "critical"
            for r in regressions
        )

    def test_state_regression_relationship_change(self) -> None:
        """Detects relationship change contradiction."""
        detector = RegressionDetector()

        original_text = "林远君和张三郎是仇敌，他们互相警惕。"
        repaired_text = "林远君和张三郎成为了好友，彼此信任。"

        canon_state = {
            "characters": {
                "林远君": {
                    "name": "林远君",
                    "alive": True,
                    "relationships": {
                        "张三郎": "仇敌，世代对立",
                    },
                },
                "张三郎": {
                    "name": "张三郎",
                    "alive": True,
                },
            },
        }

        regressions = detector._detect_state_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert len(regressions) >= 1
        assert any(
            r.issue_type == STATE_REGRESSION_RELATIONSHIP
            for r in regressions
        )

    def test_death_keyword_for_other_entity_does_not_mark_all_alive_characters(self) -> None:
        """Death words elsewhere in the chapter should not taint unrelated character mentions."""
        detector = RegressionDetector()

        original_text = "陆云峥走进店里。林远君递给他一封信。"
        repaired_text = (
            "陆云峥走进店里，低声询问怀表的来历。"
            "手机上弹出陈伯庸去世的讣闻，林远君把屏幕扣在桌面。"
        )

        canon_state = {
            "characters": {
                "陆云峥": {"name": "陆云峥", "alive": True},
                "林远君": {"name": "林远君", "alive": True},
                "陈伯庸": {"name": "陈伯庸", "alive": False},
            },
        }

        regressions = detector._detect_state_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        assert not any(
            r.issue_type == STATE_REGRESSION_DEATH
            and ("陆云峥" in r.description or "林远君" in r.description)
            for r in regressions
        )


class TestNarrativeRegression:
    """Test suite for narrative regression detection."""

    def test_narrative_regression_new_foreshadowing(self) -> None:
        """Detects new unresolved foreshadowing."""
        detector = RegressionDetector()

        original_text = "林远走在街上，一切平静。"
        repaired_text = "林远感到不安，注定将有大事发生。"

        all_chapter_texts = {
            1: "林远走在街上，一切平静。",
            2: "又是新的一天。",
        }

        regressions = detector._detect_narrative_regressions(
            original_text,
            repaired_text,
            all_chapter_texts,
            chapter_number=1,
            repair_context="test_repair",
        )

        assert len(regressions) >= 1
        assert any(
            r.issue_type == NARRATIVE_REGRESSION_UNRESOLVED_FORESHADOW
            for r in regressions
        )


class TestNoRegression:
    """Test suite for clean repair scenarios."""

    def test_no_regression_clean_repair(self) -> None:
        """Returns empty list when no regression."""
        detector = RegressionDetector()

        original_text = "林远走在雾霭小镇的街道上，微风拂过。"
        repaired_text = "林远缓步走在雾霭小镇的街道上，微风轻拂。"

        canon_state = {
            "characters": {
                "林远": {"name": "林远", "alive": True},
            },
            "worldbuilding": {
                "雾霭小镇": {},
            },
        }

        regressions = detector._detect_entity_regressions(
            original_text,
            repaired_text,
            canon_state,
            "minor_style_fix",
        )

        assert regressions == []

    def test_no_regression_identical_texts(self) -> None:
        """Returns empty when original and repaired are identical."""
        detector = RegressionDetector()

        original_text = "林远走在雾霭小镇的街道上。"
        repaired_text = "林远走在雾霭小镇的街道上。"

        regressions = detector._texts_differ_significantly(original_text, repaired_text)
        assert regressions is False


class TestMultipleRegressions:
    """Test suite for multiple regression detection."""

    def test_multiple_regressions(self) -> None:
        """Detects multiple regressions at once."""
        detector = RegressionDetector()

        original_text = "林远君走在雾霭小镇的街道上。"
        repaired_text = "张三郎和李四郎一起来到古城堡，他们注定将有大事发生。"

        canon_state = {
            "characters": {
                "林远君": {"name": "林远君", "alive": True},
            },
            "worldbuilding": {
                "雾霭小镇": {},
            },
        }

        entity_regressions = detector._detect_entity_regressions(
            original_text,
            repaired_text,
            canon_state,
            "test_repair",
        )

        all_chapter_texts = {1: repaired_text, 2: "平静的一天。"}
        narrative_regressions = detector._detect_narrative_regressions(
            original_text,
            repaired_text,
            all_chapter_texts,
            chapter_number=1,
            repair_context="test_repair",
        )

        total_regressions = len(entity_regressions) + len(narrative_regressions)
        assert total_regressions >= 2

    def test_full_detect_regressions_integration(self) -> None:
        """Full detect_regressions method with multiple issues."""
        detector = RegressionDetector()

        original_text = "林远君走在雾霭小镇的街道上。"
        repaired_text = "张三郎来到古城堡，注定将有大事发生。"

        chapter_issues = [{"issue_id": "issue_1", "description": "missing character"}]
        all_chapter_texts = {
            1: repaired_text,
            2: "平静的一天。",
        }
        canon_state = {
            "characters": {
                "林远君": {"name": "林远君", "alive": True},
            },
            "worldbuilding": {
                "雾霭小镇": {},
            },
        }

        regressions = asyncio.run(
            detector.detect_regressions(
                original_text,
                repaired_text,
                chapter_issues,
                all_chapter_texts,
                canon_state,
                chapter_number=1,
            )
        )

        assert len(regressions) >= 1
        issue_types = {r.issue_type for r in regressions}
        assert ENTITY_REGRESSION_NEW in issue_types
