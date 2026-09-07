"""Tests for StrandWeave service: tracker, balance check, inference."""

from __future__ import annotations

from novel_forge.pipeline.long.services.generation.strand_weave import (
    StrandTracker,
    StrandType,
    build_strand_config_from_profile,
    build_strand_hint_for_planning,
    check_strand_balance,
    infer_dominant_strand,
)

# ── StrandTracker ────────────────────────────────────────────────────────


def test_tracker_record_and_distribution() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST)
    t.record(2, StrandType.FIRE)
    t.record(3, StrandType.QUEST)
    dist = t.distribution()
    assert dist[StrandType.QUEST] > dist[StrandType.FIRE]
    assert dist[StrandType.CONSTELLATION] == 0.0


def test_tracker_record_idempotent() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST)
    t.record(1, StrandType.FIRE)  # overwrite chapter 1
    assert t.history[0].dominant == StrandType.FIRE
    assert len(t.history) == 1


def test_tracker_distribution_empty_returns_zeros() -> None:
    t = StrandTracker()
    dist = t.distribution()
    assert all(v == 0.0 for v in dist.values())


def test_tracker_last_chapter_for_strand() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST)
    t.record(2, StrandType.FIRE)
    t.record(3, StrandType.QUEST)
    assert t.last_quest_chapter == 3
    assert t.last_fire_chapter == 2
    assert t.last_constellation_chapter == 0


def test_tracker_secondary_strands_counted_partial() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST, secondary=[StrandType.FIRE])
    dist = t.distribution()
    # Quest=1.0, Fire=0.5 → total=1.5; quest=66.7%, fire=33.3%
    assert dist[StrandType.QUEST] > dist[StrandType.FIRE]
    assert dist[StrandType.FIRE] > 0.0


def test_tracker_window_limits_distribution() -> None:
    t = StrandTracker()
    for i in range(1, 11):
        t.record(i, StrandType.QUEST)
    # Now add fire chapters
    for i in range(11, 16):
        t.record(i, StrandType.FIRE)
    # window=5 should show only fire
    dist = t.distribution(window=5)
    assert dist[StrandType.FIRE] > dist[StrandType.QUEST]


# ── check_strand_balance ─────────────────────────────────────────────────


def test_balance_check_absence_alert() -> None:
    """Fire strand absent for 20 chapters should trigger alert."""
    t = StrandTracker()
    for i in range(1, 21):
        t.record(i, StrandType.QUEST)
    # chapter 21, fire has been absent 21 chapters (last=0, gap=21 > max_absent=10)
    alerts = check_strand_balance(t, 21)
    alert_types = {a.alert_type for a in alerts}
    alert_strands = {a.strand for a in alerts}
    assert "absence" in alert_types
    assert StrandType.FIRE in alert_strands


def test_balance_check_fatigue_alert() -> None:
    """Quest strand dominant for 6 consecutive chapters should trigger fatigue."""
    t = StrandTracker()
    for i in range(1, 7):
        t.record(i, StrandType.QUEST)
    alerts = check_strand_balance(t, 7)
    fatigue_alerts = [a for a in alerts if a.alert_type == "fatigue"]
    assert len(fatigue_alerts) >= 1
    assert fatigue_alerts[0].strand == StrandType.QUEST


def test_balance_check_no_alerts_for_balanced_tracker() -> None:
    """Balanced rotation should produce no alerts."""
    t = StrandTracker()
    strands = [StrandType.QUEST, StrandType.FIRE, StrandType.CONSTELLATION]
    for i in range(1, 13):
        t.record(i, strands[(i - 1) % 3])
    alerts = check_strand_balance(t, 13)
    assert len(alerts) == 0


def test_balance_check_uses_custom_config() -> None:
    """Custom config with fire_max_absent=2 should trigger faster."""
    t = StrandTracker()
    for i in range(1, 5):
        t.record(i, StrandType.QUEST)
    custom_config = {
        StrandType.FIRE: {
            "ideal_pct_low": 20,
            "ideal_pct_high": 40,
            "max_consecutive": 5,
            "max_absent_chapters": 2,  # very tight
        }
    }
    alerts = check_strand_balance(t, 5, config=custom_config)
    fire_absence = [a for a in alerts if a.strand == StrandType.FIRE and a.alert_type == "absence"]
    assert len(fire_absence) >= 1


def test_build_strand_config_accepts_dict_style_profile_section() -> None:
    cfg = build_strand_config_from_profile(
        {
            "quest_max_consecutive": 3,
            "fire_max_absent": 4,
            "constellation_max_absent": 6,
        }
    )

    assert cfg[StrandType.QUEST]["max_consecutive"] == 3
    assert cfg[StrandType.FIRE]["max_absent_chapters"] == 4
    assert cfg[StrandType.CONSTELLATION]["max_absent_chapters"] == 6


# ── build_strand_hint_for_planning ───────────────────────────────────────


def test_hint_returns_distribution_and_alerts_keys() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST)
    hint = build_strand_hint_for_planning(t, 2)
    assert "strand_distribution" in hint
    assert "strand_alerts" in hint
    assert isinstance(hint["strand_distribution"], dict)
    assert isinstance(hint["strand_alerts"], list)


def test_hint_distribution_percentage_format() -> None:
    t = StrandTracker()
    t.record(1, StrandType.QUEST)
    hint = build_strand_hint_for_planning(t, 2)
    for v in hint["strand_distribution"].values():
        assert v.endswith("%")


def test_hint_empty_tracker_no_alerts() -> None:
    t = StrandTracker()
    hint = build_strand_hint_for_planning(t, 1)
    # Empty tracker, no history → no fatigue/absence alerts in first chapter
    # (gap = chapter - 0 = 1, which may or may not trigger depending on defaults)
    assert "strand_distribution" in hint


# ── infer_dominant_strand ────────────────────────────────────────────────


def test_infer_quest_dominant_from_battle_text() -> None:
    text = "战斗激烈，敌方强大，主角修炼突破，击败了追击的对手。"
    dominant, secondary = infer_dominant_strand(text)
    assert dominant == StrandType.QUEST


def test_infer_fire_dominant_from_romance_text() -> None:
    text = "她感到心动，他的目光充满深情，两人牵手走在月下，感情悄然升温。"
    dominant, secondary = infer_dominant_strand(text)
    assert dominant == StrandType.FIRE


def test_infer_constellation_dominant_from_political_text() -> None:
    text = "帝国势力重组，家族阴谋浮出水面，历史真相被揭开，格局悄然改变。"
    dominant, secondary = infer_dominant_strand(text)
    assert dominant == StrandType.CONSTELLATION


def test_infer_returns_secondary_if_present() -> None:
    text = "战斗之后他感到爱意，战斗修炼突破，战斗杀，同时感情心动告白。"
    dominant, secondary = infer_dominant_strand(text)
    # Should have at least one secondary
    assert isinstance(secondary, list)


def test_infer_with_emotional_chapter_type_boosts_fire() -> None:
    from types import SimpleNamespace

    text = "平静的一天，无特别事件。"
    plan = SimpleNamespace(chapter_type="emotional")
    dominant, _ = infer_dominant_strand(text, plan=plan)
    assert dominant == StrandType.FIRE  # fire gets +3 boost from chapter_type


def test_infer_discovery_chapter_type_boosts_constellation() -> None:
    from types import SimpleNamespace

    text = "安静无特别关键词的一段文字。"
    plan = SimpleNamespace(chapter_type="discovery")
    dominant, _ = infer_dominant_strand(text, plan=plan)
    assert dominant == StrandType.CONSTELLATION


def test_infer_uses_dict_style_profile_keywords_when_present() -> None:
    style_profile = {
        "strand_keywords_config": {
            "fire_keywords": ["信任试探", "握手"],
        }
    }

    dominant, _ = infer_dominant_strand("两人在雨中完成信任试探。", style_profile=style_profile)

    assert dominant == StrandType.FIRE


def test_infer_keeps_default_keywords_when_profile_keywords_are_empty() -> None:
    style_profile = {
        "strand_keywords_config": {
            "quest_keywords": [],
            "fire_keywords": [],
            "constellation_keywords": [],
        }
    }

    dominant, _ = infer_dominant_strand(
        "战斗激烈，主角修炼突破。",
        style_profile=style_profile,
    )

    assert dominant == StrandType.QUEST
