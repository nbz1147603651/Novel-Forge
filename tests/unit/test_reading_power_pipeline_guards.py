from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from novel_forge.core.schemas.reading_power_window_config import ReadingPowerWindowConfig
from novel_forge.pipeline.long.services.quality.reading_power_timeline_window_manager import (
    ReadingPowerTimelineWindowManager,
)
from novel_forge.pipeline.long.stages.quality_checks import (
    _build_reading_power_excerpt,
    _build_reading_power_input,
)
from novel_forge.pipeline.steps.reading_power_eval_step import ReadingPowerEvalStep
from novel_forge.pipeline.style_profile_helpers import (
    build_reading_power_window_config_from_settings,
    coerce_reading_power_window_config,
    get_reading_power_eval_config,
    merge_style_profile_overrides,
)


class _MemoryStorage:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def exists(self, path: Path) -> bool:
        return str(path) in self.data

    def load_json(self, path: Path) -> dict[str, Any]:
        return self.data[str(path)]

    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        self.data[str(path)] = data


def _make_manager(tmp_path: Path) -> ReadingPowerTimelineWindowManager:
    layout = SimpleNamespace(plans_dir=tmp_path)
    return ReadingPowerTimelineWindowManager(
        blueprint=SimpleNamespace(),
        storage=_MemoryStorage(),
        layout=layout,
        config=ReadingPowerWindowConfig(),
    )


def test_reading_power_excerpt_keeps_context_and_closing_hook() -> None:
    opening = "开头信息" * 300
    middle = "中段兑现" * 2500
    ending = "章尾钩子：门外传来她不该听见的脚步声。"
    text = opening + middle + ending

    excerpt, strategy = _build_reading_power_excerpt(text, limit=8000)

    assert len(excerpt) <= 8000
    assert "开头信息" in excerpt
    assert "中段兑现" in excerpt
    assert ending in excerpt
    assert strategy == "head_middle_tail_excerpt_8000_chars"


def test_reading_power_eval_config_accepts_dict_style_profile() -> None:
    style_profile = {
        "micro_payoff_config": {"min_per_chapter": 2},
        "hook_score_config": {
            "hook_score_strong": 4.5,
            "hook_score_medium": 2.5,
            "hook_score_weak": 1.0,
            "payoff_cap": 4,
            "transition_penalty": 1.0,
        },
    }

    min_payoffs, hook_score_config = get_reading_power_eval_config(style_profile)

    assert min_payoffs == 2
    assert hook_score_config is not None
    assert hook_score_config["hook_score_strong"] == 4.5
    assert hook_score_config["payoff_cap"] == 4


def test_reading_power_window_config_accepts_dict_style_profile() -> None:
    style_profile = {
        "reading_power_window_config": {
            "enabled": True,
            "window_size": 7,
            "critical_score_threshold": 2.5,
            "warning_score_threshold": 5.5,
        },
    }

    config = coerce_reading_power_window_config(style_profile)

    assert isinstance(config, ReadingPowerWindowConfig)
    assert config.enabled is True
    assert config.window_size == 7
    assert config.critical_score_threshold == 2.5
    assert config.warning_score_threshold == 5.5


def test_style_profile_overrides_merge_for_nested_sections() -> None:
    merged = merge_style_profile_overrides(
        {
            "global_style": {
                "pace_mode": "moderate",
                "dialogue_ratio": "medium",
            },
            "overrides": {
                "global_style": {"pace_mode": "fast"},
                "summary": "覆盖后的风格摘要",
            },
        }
    )

    assert merged is not None
    assert merged["global_style"]["pace_mode"] == "fast"
    assert merged["global_style"]["dialogue_ratio"] == "medium"
    assert merged["summary"] == "覆盖后的风格摘要"


def test_build_reading_power_window_config_respects_disabled_setting() -> None:
    settings = SimpleNamespace(
        reading_power_enabled=False,
        reading_power_window_size=7,
        reading_power_window_left_offset=0,
        reading_power_window_right_offset=1,
        reading_power_suspense_delay_threshold=4,
        reading_power_force_resolve_threshold=6,
        reading_power_hook_alternation_threshold=3,
        reading_power_tension_deviation_tolerance=2.0,
        reading_power_max_consecutive_same_hook=4,
        reading_power_tension_recovery_factor=0.4,
        reading_power_payoff_cap=4,
        reading_power_critical_score_threshold=2.5,
        reading_power_warning_score_threshold=5.5,
    )

    config = build_reading_power_window_config_from_settings(settings)

    assert config.enabled is False
    assert config.window_size == 7
    assert config.force_resolve_threshold == 6
    assert config.max_consecutive_same_hook == 4
    assert config.tension_recovery_factor == 0.4
    assert config.payoff_cap == 4
    assert config.critical_score_threshold == 2.5
    assert config.warning_score_threshold == 5.5


def test_build_reading_power_input_uses_style_profile_constraints(tmp_path: Path) -> None:
    storage = _MemoryStorage()
    runner = SimpleNamespace(_storage=storage)
    bundle = SimpleNamespace(
        layout=SimpleNamespace(
            reading_power_report_path=lambda chapter: (
                tmp_path / f"reading_power_report_ch{chapter}.json"
            ),
        ),
        style_profile={
            "micro_payoff_config": {
                "min_per_chapter": 2,
                "preferred_types": ["relationship", "emotion", "clue"],
            },
            "hook_score_config": {
                "hook_score_strong": 4.0,
                "hook_score_medium": 2.5,
                "hook_score_weak": 1.0,
                "payoff_cap": 3,
                "transition_penalty": 1.0,
            },
            "global_style": {"dialogue_ratio": "high"},
        },
        story_bible=SimpleNamespace(genre="都市轮回言情"),
        chapter_outline=SimpleNamespace(
            pov_character="沈念卿",
            involved_characters=["沈念卿", "陆云峥"],
            main_plot_points=["怀表触发旧时空线索"],
            expected_hook={
                "hook_type": "emotion",
                "hook_strength": "medium",
                "hook_description": "他认出她腕上的金镯",
            },
            expected_payoffs=[{"payoff_type": "clue", "description": "确认怀表与旧案相关"}],
        ),
        blueprint=SimpleNamespace(
            narrative_phases=[
                {
                    "chapter_start": 1,
                    "chapter_end": 5,
                    "phase_name": "重逢",
                    "tension_level": "medium",
                }
            ],
            suspense_schedule=[
                {
                    "suspense_id": "s_watch",
                    "suspense_description": "怀表为何会触发记忆",
                    "setup_chapter": 1,
                    "planned_resolution_chapter": 4,
                }
            ],
        ),
    )

    rp_input, _ = _build_reading_power_input(
        runner=runner,
        bundle=bundle,
        current_text="她触到怀表，听见旧上海的钟声。",
        chapter_number=2,
    )

    assert rp_input.min_payoffs == 2
    assert rp_input.preferred_payoff_types == ["relationship", "emotion", "clue"]
    assert rp_input.pacing_dialogue_ratio == "high"
    assert rp_input.main_plot_points == ["怀表触发旧时空线索"]
    assert rp_input.narrative_phase_context["phase_name"] == "重逢"
    assert rp_input.suspense_schedule[0]["suspense_id"] == "s_watch"
    assert rp_input.hook_score_config["payoff_cap"] == 3


def test_fallback_report_does_not_pollute_window_history(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.update_window(
        current_chapter=1,
        chapter_outline=None,
        reading_power_report={
            "chapter": 1,
            "hook_type": "crisis",
            "hook_strength": "strong",
            "overall_score": 7.0,
            "micro_payoffs": [{"payoff_type": "information", "description": "揭示线索"}],
        },
    )

    fallback = ReadingPowerEvalStep._default_report(2, reason="unit_test")
    report = manager.update_window(
        current_chapter=2,
        chapter_outline=None,
        reading_power_report=fallback,
    )

    assert len(manager._hook_type_history) == 1
    assert len(manager._reading_power_history) == 1
    assert report.alerts
    assert "未纳入" in report.alerts[0]


def test_trend_uses_real_scores_and_skips_fallbacks(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager._reading_power_history = [
        {"chapter": 1, "score": 6.0},
        {"chapter": 2, "overall_score": 0.0, "score": 0.0, "is_fallback": True},
        {"chapter": 3, "overall_score": 7.0, "score": 7.0},
    ]

    trend = manager.compute_reading_power_trend()

    assert trend["total_chapters_evaluated"] == 2
    assert trend["mean"] == 6.5
    assert trend["recent_scores"] == [
        {"chapter": 1, "score": 6.0},
        {"chapter": 3, "score": 7.0},
    ]


def test_planning_hint_ignores_previous_fallback_suggestions(tmp_path: Path) -> None:
    storage = _MemoryStorage()
    prev_path = tmp_path / "reading_power_report_ch1.json"
    prev_path.write_text("{}", encoding="utf-8")
    storage.data[str(prev_path)] = ReadingPowerEvalStep._default_report(
        1,
        reason="unit_test",
    ).model_dump(mode="json")
    layout = SimpleNamespace(
        plans_dir=tmp_path,
        reading_power_report_path=lambda chapter: (
            tmp_path / f"reading_power_report_ch{chapter}.json"
        ),
    )
    manager = ReadingPowerTimelineWindowManager(
        blueprint=SimpleNamespace(),
        storage=storage,
        layout=layout,
        config=ReadingPowerWindowConfig(),
    )

    hint = manager.build_reading_power_hint(current_chapter=2, chapter_outline=None)

    assert hint["prev_chapter_suggestions"] == []
    assert hint["prev_chapter_overall_score"] is None


def test_window_manager_derives_suspense_and_backfills_outline_expectations(
    tmp_path: Path,
) -> None:
    storage = _MemoryStorage()
    outline_path = tmp_path / "outline.json"
    storage.data[str(outline_path)] = {
        "chapters": [
            {
                "chapter_number": 7,
                "expected_hook": {
                    "hook_type": "suspense",
                    "hook_strength": "strong",
                    "hook_description": "朱批录里缺失的一页究竟去了哪里",
                },
                "expected_payoffs": [{"payoff_type": "clue", "description": "确认缺页与旧案有关"}],
            }
        ]
    }
    layout = SimpleNamespace(
        plans_dir=tmp_path,
        outline_path=outline_path,
        reading_power_report_path=lambda chapter: (
            tmp_path / f"reading_power_report_ch{chapter}.json"
        ),
    )
    blueprint = {
        "narrative_phases": [
            {
                "chapter_start": 16,
                "chapter_end": 25,
                "phase_name": "追查",
                "tension_level": "high",
            }
        ],
        "key_turning_points": [{"chapter_number": 19, "description": "真相撕开"}],
    }
    manager = ReadingPowerTimelineWindowManager(
        blueprint=blueprint,
        storage=storage,
        layout=layout,
        config=ReadingPowerWindowConfig(),
    )

    entries = manager.build_eval_suspense_entries(8)
    assert entries[0]["suspense_id"] == "s_ch7_mystery"
    assert entries[0]["suspense_type"] == "mystery"

    chapter_outline = {
        "chapter_number": 16,
        "goal": "追查朱批录缺页的去向",
        "main_plot_points": ["发现缺页上的旧案线索"],
        "subplot_points": ["沈承夜与崔令仪的信任出现裂痕"],
    }
    hint = manager.build_reading_power_hint(16, chapter_outline)

    assert hint["outline_expected_hook"]["source"] == "auto_synthesised_from_outline_blueprint"
    assert hint["outline_expected_hook"]["hook_type"] == "mystery"
    assert hint["outline_expected_payoffs"]


def test_next_chapter_constraints_can_use_last_window_report(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.update_window(
        current_chapter=3,
        chapter_outline=None,
        reading_power_report={
            "chapter": 3,
            "hook_type": "mystery",
            "hook_strength": "medium",
            "overall_score": 6.0,
            "micro_payoffs": [{"payoff_type": "information", "description": "揭示线索"}],
        },
    )

    constraints = manager.get_next_chapter_constraints()

    assert constraints.recommended_hook_type
    assert constraints.tension_target >= 0
