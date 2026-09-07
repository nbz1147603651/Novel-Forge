"""Tests for extended ModelCallTrace / PipelineTrace fields."""

from __future__ import annotations

from novel_forge.obs.tracer import ModelCallTrace, PipelineTrace, StepTrace


class TestModelCallTraceExtended:
    def test_defaults_are_empty(self):
        mct = ModelCallTrace(task="t", provider="p", model="m")
        assert mct.model_call_id == ""
        assert mct.chapter_run_id == ""
        assert mct.attempt_id == ""
        assert mct.chapter_number == 0
        assert mct.lane == ""
        assert mct.repair_round == -1
        assert mct.mutation_id == ""
        assert mct.cost_source == "reported"

    def test_as_summary_backward_compatible(self):
        mct = ModelCallTrace(task="t", provider="p", model="m")
        summary = mct.as_summary()
        # as_summary() must not include new fields — keeps Desktop stable.
        assert "model_call_id" not in summary
        assert "chapter_run_id" not in summary
        assert "task" in summary

    def test_extended_fields_settable(self):
        mct = ModelCallTrace(
            task="REPAIR_CONTINUITY",
            provider="openai",
            model="gpt-4o",
            model_call_id="mc-123",
            chapter_run_id="cr-456",
            attempt_id="att-789",
            chapter_number=12,
            lane="continuity",
            repair_round=0,
            mutation_id="mut-abc",
            cost_source="estimated",
        )
        assert mct.model_call_id == "mc-123"
        assert mct.chapter_number == 12
        assert mct.cost_source == "estimated"


class TestPipelineTraceExtended:
    def test_defaults_are_empty(self):
        pt = PipelineTrace()
        assert pt.chapter_run_id == ""
        assert pt.attempt_id == ""
        assert pt.chapter_number == 0
        assert pt.status == ""

    def test_extended_fields_settable(self):
        pt = PipelineTrace(
            chapter_run_id="cr-001",
            attempt_id="att-002",
            chapter_number=5,
            status="replanned",
        )
        assert pt.chapter_run_id == "cr-001"
        assert pt.status == "replanned"

    def test_existing_properties_unaffected(self):
        pt = PipelineTrace()
        step = StepTrace(step_name="draft")
        step.prompt_tokens = 100
        step.completion_tokens = 50
        step.tokens_used = 150
        step.cost_usd = 0.01
        step.ended_at = step.started_at + 1.0
        pt.add(step)
        assert pt.total_tokens == 150
        assert pt.total_cost == 0.01
