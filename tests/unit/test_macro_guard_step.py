from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.config import Settings
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.macro_guard_step import MacroGuardInput, MacroGuardStep


def _make_settings(**overrides: object) -> SimpleNamespace:
    base = {
        "long_macro_guard_drift_threshold_warning": 0.3,
        "long_macro_guard_drift_threshold_alert": 0.5,
        "long_macro_guard_drift_threshold_critical": 0.7,
        "long_macro_guard_max_adjustments_per_book": 3,
        "long_macro_guard_min_chapters_between_adjustments": 8,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_select_outline_window_matches_audited_chapters() -> None:
    outline = SimpleNamespace(
        chapters=[
            SimpleNamespace(chapter_number=2, title="c2", goal="g2", beats_summary=["b2"]),
            SimpleNamespace(chapter_number=3, title="c3", goal="g3", beats_summary=["b3"]),
            SimpleNamespace(chapter_number=4, title="c4", goal="g4", beats_summary=["b4"]),
            SimpleNamespace(chapter_number=5, title="c5", goal="g5", beats_summary=["b5"]),
        ]
    )

    selected = MacroGuardStep._select_outline_window(outline, [4, 3, 99, 5])

    assert [item["chapter_number"] for item in selected] == [4, 3, 5]


def test_build_report_uses_threshold_floor_instead_of_llm_pass() -> None:
    step = MacroGuardStep.__new__(MacroGuardStep)
    settings = _make_settings()
    input_data = MacroGuardInput(
        chapter_number=12,
        audit_entries=[{"chapter_number": 10}, {"chapter_number": 11}, {"chapter_number": 12}],
        outline=SimpleNamespace(),
        canon_state={},
        settings=settings,
        adjustment_state={"applied_adjustments": 0, "last_adjustment_chapter": 0},
    )
    data = {
        "dimensions": {
            "outline_alignment": 0.4,
            "character_arc_consistency": 0.4,
            "pacing_curve": 0.4,
            "foreshadowing_recovery": 0.4,
            "thematic_cohesion": 0.4,
        },
        "recommended_action": "pass",
        "confidence": 0.8,
        "reasoning": "llm says pass",
    }

    report = step._build_report(data, input_data)

    # Drift = 0.6 should at least trigger alert_plan by thresholds.
    assert report.recommended_action == "alert_plan"


def test_build_report_downgrades_alert_when_adjustments_blocked() -> None:
    step = MacroGuardStep.__new__(MacroGuardStep)
    settings = _make_settings(long_macro_guard_max_adjustments_per_book=0)
    input_data = MacroGuardInput(
        chapter_number=12,
        audit_entries=[{"chapter_number": 10}, {"chapter_number": 11}, {"chapter_number": 12}],
        outline=SimpleNamespace(),
        canon_state={},
        settings=settings,
        adjustment_state={"applied_adjustments": 0, "last_adjustment_chapter": 0},
    )
    data = {
        "dimensions": {
            "outline_alignment": 0.45,
            "character_arc_consistency": 0.4,
            "pacing_curve": 0.4,
            "foreshadowing_recovery": 0.4,
            "thematic_cohesion": 0.4,
        },
        "recommended_action": "alert_plan",
        "adjustment_plan": {
            "window_size": 2,
            "strategy": "accelerate_plot",
            "target_outline_v": "v1",
            "adjusted_chapter_goals": [{"chapter_number": 13, "goal": "g"}],
            "reasoning": "adjust",
        },
    }

    report = step._build_report(data, input_data)

    assert report.recommended_action == "warning_hint"
    assert report.adjustment_plan is None
    assert "downgraded" in report.reasoning


class _MacroBuilder:
    def build(
        self,
        task_type,
        context,
        *,
        max_tokens: int,
        temperature: float,
        prior_messages=None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "macro"}],
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _MacroRouter:
    def __init__(self, content: str) -> None:
        self.content = content

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=self.content,
            model_id=request.model_id or "mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        )


async def test_macro_guard_falls_back_when_dimensions_missing() -> None:
    step = MacroGuardStep(
        _MacroRouter('{"recommended_action":"alert_plan","confidence":0.8}'),
        _MacroBuilder(),
        settings=Settings(_env_file=None),
    )
    input_data = MacroGuardInput(
        chapter_number=12,
        audit_entries=[{"chapter_number": 10}, {"chapter_number": 11}, {"chapter_number": 12}],
        outline=SimpleNamespace(synopsis="", total_chapters=12, chapters=[]),
        canon_state={},
        settings=_make_settings(),
        adjustment_state={"applied_adjustments": 0, "last_adjustment_chapter": 0},
    )

    report = await step.run(input_data)

    assert report.recommended_action == "pass"
    assert report.confidence == 0.0
    assert "failed to parse" in report.reasoning
