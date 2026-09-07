"""Tests for evaluation_metrics — 3-dimension read-only chapter quality metrics.

Covers the 3 named score functions and the ``evaluate_chapter``
aggregator. Pure read-only: no LLM, no state writes, no async. All
test fixtures are inline (no shared state) so tests can run in
parallel via ``pytest -n auto``.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.memory.evaluation_metrics import (
    EvaluationReport,
    character_drift_score,
    evaluate_chapter,
    foreshadow_recovery_rate,
    timeline_consistency,
)

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_bible(
    characters: list[tuple[str, str]],
) -> CharacterBible:
    """Build a CharacterBible from a list of (name, role) tuples."""
    profiles = [CharacterProfile(name=name, role=role) for name, role in characters]
    return CharacterBible(characters=profiles)


class _StubForeshadowReminder:
    """Duck-typed ForeshadowReminder for foreshadow_recovery_rate tests.

    Mirrors the expected shape from the parallel Task 8 implementation:
    ``get_due(volume)`` and ``get_paid(volume)`` returning lists.
    """

    def __init__(
        self,
        *,
        pending: list[Any] | None = None,
        paid: list[Any] | None = None,
    ) -> None:
        self._pending = list(pending or [])
        self._paid = list(paid or [])

    def get_due(self, volume: int) -> list[Any]:
        return list(self._pending)

    def get_paid(self, volume: int) -> list[Any]:
        return list(self._paid)


# ─────────────────────────────────────────────────────────────────────
# 1. character_drift_score
# ─────────────────────────────────────────────────────────────────────


def test_character_drift_score_perfect_match() -> None:
    """All active characters mentioned ≥ expected → 1.0."""
    bible = _make_bible(
        [
            ("林渊", "protagonist"),
            ("沈墨", "antagonist"),
        ]
    )
    text = (
        "林渊走入夜色，雨打湿他的衣袍。"
        "沈墨在远处冷笑，手持短剑。"
        "林渊握紧剑柄，向前迈出一步。"
        "林渊沉声问道——沈墨，你想怎样？"
    )
    score = character_drift_score(chapter=1, chapter_text=text, character_bible=bible)
    assert score == 1.0, f"expected 1.0, got {score}"


def test_character_drift_score_no_match() -> None:
    """No characters mentioned at all → ~0.0 (active role penalty)."""
    bible = _make_bible(
        [
            ("林渊", "protagonist"),
            ("沈墨", "antagonist"),
        ]
    )
    text = "城外的风刮了一整夜，河水涨了三寸。"
    score = character_drift_score(chapter=2, chapter_text=text, character_bible=bible)
    # No active characters mentioned → 0.0 (active_ratio = 0, density = 0).
    assert score < 0.05, f"expected ~0.0, got {score}"


def test_character_drift_score_partial_match() -> None:
    """Only 1 of 2 active characters mentioned → score between 0 and 1."""
    bible = _make_bible(
        [
            ("林渊", "protagonist"),
            ("沈墨", "antagonist"),
        ]
    )
    text = "林渊独自走在街上，四下无人。林渊抬头望天，林渊继续前行。"
    score = character_drift_score(chapter=3, chapter_text=text, character_bible=bible)
    # Active matched: 1/2 = 0.5 (林渊 matched, 沈墨 missing).
    # Density for active only: 林渊 has 4 mentions, expected 3 → density 1.0.
    # 0.7 * 0.5 * 1.0 = 0.35.
    assert 0.0 < score < 1.0, f"expected partial score, got {score}"


def test_character_drift_score_no_bible_returns_neutral() -> None:
    """No bible provided → 0.5 (neutral)."""
    score = character_drift_score(chapter=1, chapter_text="任何文本", character_bible=None)
    assert score == 0.5


def test_character_drift_score_only_minor_characters() -> None:
    """Bible with only minor characters → 0.5 (nothing to judge)."""
    bible = _make_bible(
        [
            ("路人甲", "minor"),
            ("小贩", "minor"),
        ]
    )
    text = "路人甲走过，小贩叫卖。"
    score = character_drift_score(chapter=1, chapter_text=text, character_bible=bible)
    # No active or supporting characters → return 0.5 (nothing to judge).
    assert score == 0.5


def test_character_drift_score_supporting_character_miss() -> None:
    """Missing supporting character reduces score (not to 0)."""
    bible = _make_bible(
        [
            ("林渊", "protagonist"),
            ("沈墨", "supporting"),
        ]
    )
    text = "林渊走进客栈，林渊点了一壶酒。林渊坐到角落，林渊望着窗外的雨。"
    score = character_drift_score(chapter=1, chapter_text=text, character_bible=bible)
    # Active matched (林渊, 4 ≥ 3) = 1.0, supporting missed (沈墨 0 ≥ 1 fail) = 0.0.
    # 0.7 * 1.0 + 0.3 * 0.0 = 0.7 (but multiplied by density_factor).
    # Density: active mentions (4) + supporting mentions (0) = 4
    # Expected: 3 + 1 = 4
    # density_factor = min(4/4, 1.0) = 1.0
    # Final = 0.7 * 1.0 = 0.7
    assert 0.5 < score <= 0.7, f"expected ~0.7, got {score}"


def test_character_drift_score_uses_aliases() -> None:
    """Alias mentions count toward character presence."""
    # Build a profile with aliases via the ``notes`` field (the only
    # place CharacterProfile stores alias-like info out of the box).
    profile = CharacterProfile(
        name="林渊",
        role="protagonist",
        notes="别名：渊、剑痴",
    )
    bible = CharacterBible(characters=[profile])
    text = "渊走入夜色。渊握紧剑柄。渊低声自语。"
    score = character_drift_score(chapter=1, chapter_text=text, character_bible=bible)
    # Alias "渊" appears 3 times → matched (≥ 3 expected).
    assert score >= 0.7, f"alias should count toward score, got {score}"


# ─────────────────────────────────────────────────────────────────────
# 2. foreshadow_recovery_rate
# ─────────────────────────────────────────────────────────────────────


def test_foreshadow_recovery_rate_calculation() -> None:
    """2 paid out of 4 total → 0.5."""
    reminder = _StubForeshadowReminder(
        pending=["伏笔1", "伏笔2"],
        paid=["回收1", "回收2"],
    )
    rate = foreshadow_recovery_rate(volume=1, foreshadow_reminder=reminder)
    assert rate == 0.5, f"expected 0.5, got {rate}"


def test_foreshadow_recovery_rate_all_paid() -> None:
    """All paid → 1.0."""
    reminder = _StubForeshadowReminder(
        pending=[],
        paid=["回收1", "回收2", "回收3"],
    )
    rate = foreshadow_recovery_rate(volume=2, foreshadow_reminder=reminder)
    assert rate == 1.0


def test_foreshadow_recovery_rate_none_pending() -> None:
    """All pending → 0.0 (none recovered yet)."""
    reminder = _StubForeshadowReminder(
        pending=["伏笔1", "伏笔2", "伏笔3"],
        paid=[],
    )
    rate = foreshadow_recovery_rate(volume=1, foreshadow_reminder=reminder)
    assert rate == 0.0


def test_foreshadow_recovery_rate_no_reminder_returns_one() -> None:
    """No reminder → 1.0 (vacuous: nothing overdue)."""
    rate = foreshadow_recovery_rate(volume=1, foreshadow_reminder=None)
    assert rate == 1.0


def test_foreshadow_recovery_rate_empty_state() -> None:
    """Reminder with no pending and no paid → 1.0 (vacuous)."""
    reminder = _StubForeshadowReminder(pending=[], paid=[])
    rate = foreshadow_recovery_rate(volume=1, foreshadow_reminder=reminder)
    assert rate == 1.0


def test_foreshadow_recovery_rate_chapter_range_ignored() -> None:
    """chapter_range is recorded-only; does not affect the score."""
    reminder = _StubForeshadowReminder(pending=["p"], paid=["a", "b"])
    rate = foreshadow_recovery_rate(
        volume=1,
        foreshadow_reminder=reminder,
        chapter_range=(1, 10),
    )
    # 2/3 ≈ 0.6667
    assert abs(rate - 2 / 3) < 1e-6, f"expected ~0.667, got {rate}"


def test_foreshadow_recovery_rate_async_only_method_returns_neutral() -> None:
    """Reminder with async-only ``get_due`` returns neutral 0.5 + warning."""

    class _AsyncOnlyReminder:
        """Mirrors the real ``ForeshadowReminder`` shape: async get_due."""

        async def get_due(self, volume: int) -> list[Any]:
            return []

    import warnings

    reminder = _AsyncOnlyReminder()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rate = foreshadow_recovery_rate(volume=1, foreshadow_reminder=reminder)

    assert rate == 0.5, f"expected neutral 0.5 for async-only reminder, got {rate}"
    assert any("async-only" in str(w.message) for w in caught), (
        f"expected async-only warning, got: {[str(w.message) for w in caught]}"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. timeline_consistency
# ─────────────────────────────────────────────────────────────────────


def test_timeline_consistency_no_contradictions() -> None:
    """Pure time markers, no conflicting pairs → 1.0."""
    text = "清晨，林渊出发。三日后，他抵达山脚。黄昏时扎营。"
    score = timeline_consistency(chapter=1, chapter_text=text)
    assert score == 1.0, f"expected 1.0, got {score}"


def test_timeline_consistency_day_night_contradiction() -> None:
    """Day marker + night marker in same chapter → contradiction."""
    text = "清晨，阳光洒落。深夜，月光如霜。"
    score = timeline_consistency(chapter=1, chapter_text=text)
    # 4 markers, 1 contradiction → 1 - 1/4 = 0.75.
    assert score < 1.0, f"expected < 1.0 for contradiction, got {score}"
    assert 0.0 < score <= 0.8, f"expected 0.75-ish, got {score}"


def test_timeline_consistency_same_day_vs_next_day() -> None:
    """当天 + 次日 in same chapter → contradiction."""
    text = "当天，议事完毕。次日清晨，新的风波又起。"
    score = timeline_consistency(chapter=1, chapter_text=text)
    # "当天" and "次日" are both flagged as same-day vs next-day
    # contradiction (one of the 4 markers triggers the pair).
    # 4 unique markers, 1 contradiction pair → score = 1 - 1/4 = 0.75.
    assert score < 1.0, f"expected < 1.0 for same-day/next-day conflict, got {score}"


def test_timeline_consistency_no_markers() -> None:
    """No time markers in text → 1.0 (vacuous)."""
    text = "他走进客栈，点了一壶酒，坐到角落开始读书。"
    score = timeline_consistency(chapter=1, chapter_text=text)
    assert score == 1.0


def test_timeline_consistency_empty_text() -> None:
    """Empty text → 1.0 (vacuous)."""
    score = timeline_consistency(chapter=1, chapter_text="")
    assert score == 1.0


def test_timeline_consistency_cross_chapter_break() -> None:
    """Previous chapter ends with '次日' and current opens with '当天' → break."""
    prev = "林渊做出决定。次日，他启程出发。"
    cur = "当天，客栈里人声鼎沸。林渊坐在角落。"
    score = timeline_consistency(
        chapter=2,
        chapter_text=cur,
        previous_chapter_text=prev,
    )
    # Cross-chapter: "次日" (prev) + "当天" (cur) is a contradiction.
    # 1 unique marker in current ("当天"), 1 cross-chapter contradiction
    # pair → score = 1 - 1/1 = 0.0. But "林渊" doesn't add a time marker
    # — let's see. Actually "当天" is the only current-side marker, and
    # the cross-chapter check adds the (prev_nxt, cur_same) pair.
    # 1 marker in current + 1 contradiction → 0.0.
    assert score < 1.0, f"expected cross-chapter break to lower score, got {score}"


def test_timeline_consistency_cross_chapter_clean() -> None:
    """Previous ends with day, current opens with day → no break."""
    prev = "清晨，众人启程。"
    cur = "清晨，他们抵达山脚。"
    score = timeline_consistency(
        chapter=2,
        chapter_text=cur,
        previous_chapter_text=prev,
    )
    # 1 unique current marker, 0 contradictions → 1.0.
    assert score == 1.0, f"expected 1.0 for clean cross-chapter, got {score}"


# ─────────────────────────────────────────────────────────────────────
# 4. evaluate_chapter (aggregator)
# ─────────────────────────────────────────────────────────────────────


def test_evaluate_chapter_aggregates_all_three() -> None:
    """Aggregator returns an EvaluationReport with all 3 scores + details."""
    bible = _make_bible(
        [
            ("林渊", "protagonist"),
            ("沈墨", "antagonist"),
        ]
    )
    reminder = _StubForeshadowReminder(pending=["伏笔1"], paid=["回收1"])
    text = (
        "清晨，林渊出门。林渊手持长剑。"
        "林渊在街角遇见沈墨。沈墨拔剑。"
        "林渊格挡，林渊反击。林渊一剑封喉。"
    )
    report = evaluate_chapter(
        chapter=5,
        chapter_text=text,
        character_bible=bible,
        foreshadow_reminder=reminder,
        volume=1,
    )
    assert isinstance(report, EvaluationReport)
    assert report.chapter == 5
    assert 0.0 <= report.character_drift_score <= 1.0
    assert 0.0 <= report.foreshadow_recovery_rate <= 1.0
    assert 0.0 <= report.timeline_consistency <= 1.0
    # Details should include intermediate data.
    assert "character_mentions" in report.details
    assert "time_markers" in report.details
    assert "time_contradictions" in report.details
    assert report.details["has_character_bible"] is True
    assert report.details["has_foreshadow_reminder"] is True
    assert report.details["has_previous_chapter"] is False
    assert report.details["volume"] == 1


def test_evaluate_chapter_none_services_uses_defaults() -> None:
    """All services None → scores are neutral defaults (0.5, 1.0, 1.0)."""
    text = "林渊在清晨出门，走了三日后抵达山脚。"
    report = evaluate_chapter(
        chapter=1,
        chapter_text=text,
        character_bible=None,
        foreshadow_reminder=None,
        entity_knowledge=None,
    )
    assert report.character_drift_score == 0.5  # No bible → neutral
    assert report.foreshadow_recovery_rate == 1.0  # No reminder → vacuous
    # Timeline: "清晨" and "三日后" are both day/large-gap markers, no
    # contradiction → 1.0.
    assert report.timeline_consistency == 1.0


def test_evaluate_chapter_to_dict_serializable() -> None:
    """to_dict() yields JSON-friendly payload (no custom objects)."""
    report = evaluate_chapter(chapter=1, chapter_text="清晨。")
    payload = report.to_dict()
    assert payload["chapter"] == 1
    assert "character_drift_score" in payload
    assert "details" in payload
    # Details should be plain dict (frozen dataclass fields only).
    assert isinstance(payload["details"], dict)


# ─────────────────────────────────────────────────────────────────────
# 5. Pure-function invariants
# ─────────────────────────────────────────────────────────────────────


def test_pure_function_no_input_mutation() -> None:
    """Calling evaluate_chapter twice with the same inputs yields the same report."""
    bible = _make_bible([("林渊", "protagonist")])
    text = "清晨，林渊出门。"

    snapshot_text = text
    snapshot_bible_dump = bible.model_dump()

    report1 = evaluate_chapter(chapter=1, chapter_text=text, character_bible=bible)
    report2 = evaluate_chapter(chapter=1, chapter_text=text, character_bible=bible)

    # Identical input → identical output (determinism).
    assert report1.character_drift_score == report2.character_drift_score
    assert report1.foreshadow_recovery_rate == report2.foreshadow_recovery_rate
    assert report1.timeline_consistency == report2.timeline_consistency

    # Input objects were not mutated by the function call.
    assert text == snapshot_text
    assert bible.model_dump() == snapshot_bible_dump


def test_evaluation_report_is_frozen() -> None:
    """EvaluationReport is @dataclass(frozen=True) — assignment should raise."""
    from dataclasses import FrozenInstanceError

    report = evaluate_chapter(chapter=1, chapter_text="")
    raised: Exception | None = None
    try:
        report.character_drift_score = 0.0  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None, "frozen dataclass accepted assignment"
    assert isinstance(raised, FrozenInstanceError), (
        f"expected FrozenInstanceError, got {type(raised).__name__}: {raised}"
    )
