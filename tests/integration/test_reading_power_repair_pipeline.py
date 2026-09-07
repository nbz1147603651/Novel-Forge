"""Integration tests for reading power repair pipeline-level verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.reading_power import (
    MicroPayoff,
    MicroPayoffType,
    ReadingPowerReport,
)
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.prompts.builder import PromptBuilder


def _make_rp_report(
    chapter: int = 1,
    *,
    hook_type: str = "mystery",
    hook_strength: str = "strong",
    hook_description: str = "章尾悬念钩子",
    prev_hook_fulfilled: bool = True,
    micro_payoffs: list[MicroPayoff] | None = None,
    is_transition: bool = False,
    overall_score: float | None = None,
) -> ReadingPowerReport:
    payoffs = micro_payoffs or [
        MicroPayoff(payoff_type=MicroPayoffType.INFORMATION, description="线索揭示", strength="strong"),
        MicroPayoff(payoff_type=MicroPayoffType.RELATIONSHIP, description="关系推进", strength="medium"),
    ]
    report = ReadingPowerReport(
        chapter=chapter,
        hook_type=hook_type,
        hook_strength=hook_strength,
        hook_description=hook_description,
        prev_hook_fulfilled=prev_hook_fulfilled,
        micro_payoffs=payoffs,
        is_transition=is_transition,
    )
    if overall_score is not None:
        report.overall_score = overall_score
    else:
        report.compute_score(min_payoffs=1)
    return report


class TestReadingPowerRepairPipeline:
    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def router(self) -> ModelRouter:
        return ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        return PromptBuilder()

    @pytest.fixture
    def runner(
        self,
        storage: FileSystemStorage,
        router: ModelRouter,
        builder: PromptBuilder,
        runtime_settings: Settings,
    ) -> ChapterRunner:
        return ChapterRunner(
            router,
            builder,
            storage,
            config=ChapterRunnerConfig(),
            settings=runtime_settings,
        )

    async def test_reading_power_repair_in_pipeline(
        self,
        runner: ChapterRunner,
        storage: FileSystemStorage,
    ) -> None:
        project_id = "rp_repair_pipeline"
        await runner.init_long(
            premise="一个关于时间回环的悬疑故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        result = await runner.run_chapter(project_id, chapter_number=1)

        assert result.eval_report is not None
        assert result.causal_report is not None
        assert result.continuity_report is not None

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        rp_report_path = layout.reading_power_report_path(1)
        assert storage.exists(rp_report_path), (
            f"reading_power_report_path should exist at {rp_report_path}"
        )

        rp_data = storage.load_json(rp_report_path)
        assert rp_data is not None
        assert "pipeline_stage" in rp_data, "Report must contain pipeline_stage field"

        pipeline_stage = rp_data.get("pipeline_stage", "")
        assert pipeline_stage in ("pre_repair", "post_repair", "final_review_text"), (
            f"pipeline_stage should be pre_repair, post_repair, or final_review_text, got: {pipeline_stage}"
        )

    async def test_reading_power_repair_skipped_when_score_ok(
        self,
        runner: ChapterRunner,
        storage: FileSystemStorage,
    ) -> None:
        project_id = "rp_skip_repair"
        await runner.init_long(
            premise="一个关于时间回环的悬疑故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        good_report = _make_rp_report(
            chapter=1,
            hook_type="crisis",
            hook_strength="strong",
            hook_description="强烈的危机悬念",
            prev_hook_fulfilled=True,
            overall_score=8.5,
        )

        call_log = []

        async def mock_rp_eval_side_effect(**kwargs):
            call_log.append(kwargs)
            chapter_number = kwargs.get("chapter_number", 1)
            runner_obj = kwargs.get("runner")
            bundle = kwargs.get("bundle")
            current_text = kwargs.get("current_text", "")
            pipeline_stage = kwargs.get("pipeline_stage", "mocked")

            from novel_forge.core.utils.text_hash import source_text_hash

            payload = good_report.model_dump(mode="json")
            payload["source_text_hash"] = source_text_hash(current_text)
            payload["pipeline_stage"] = pipeline_stage

            if runner_obj and bundle:
                rp_path = bundle.layout.reading_power_report_path(chapter_number)
                runner_obj._storage.save_json(rp_path, payload)

            return good_report

        with patch(
            "novel_forge.pipeline.long.stages.reading_power_repair.evaluate_and_record_reading_power",
            side_effect=mock_rp_eval_side_effect,
        ):
            with patch(
                "novel_forge.pipeline.long.chapter_flow.evaluate_and_record_reading_power",
                side_effect=mock_rp_eval_side_effect,
            ):
                with patch(
                    "novel_forge.pipeline.long.stages.report_refresh."
                    "evaluate_and_record_reading_power",
                    side_effect=mock_rp_eval_side_effect,
                ):
                    result = await runner.run_chapter(project_id, chapter_number=1)

        assert result.eval_report is not None
        assert result.text is not None
        assert len(result.text) > 0

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        rp_report_path = layout.reading_power_report_path(1)
        assert storage.exists(rp_report_path), (
            f"reading_power_report should exist at {rp_report_path}"
        )

        rp_data = storage.load_json(rp_report_path)
        assert rp_data is not None

        pipeline_stage = rp_data.get("pipeline_stage", "")
        assert pipeline_stage == "final_review_text", (
            f"Final pipeline_stage should be final_review_text, got: {pipeline_stage}"
        )

        assert rp_data.get("overall_score", 0) >= 8.0, (
            f"Score should remain high (>=8.0), got: {rp_data.get('overall_score', 0)}"
        )

        assert len(call_log) >= 1, (
            f"evaluate_and_record_reading_power should have been called at least once, got: {len(call_log)}"
        )

    async def test_reading_power_repair_preserves_causal_continuity(
        self,
        runner: ChapterRunner,
        storage: FileSystemStorage,
    ) -> None:
        project_id = "rp_preserve_causal"
        await runner.init_long(
            premise="一个关于时间回环的悬疑故事",
            project_id=project_id,
            genre="mystery",
            tone="suspenseful",
            total_chapters=3,
            words_per_chapter=1800,
        )

        result = await runner.run_chapter(project_id, chapter_number=1)

        assert result.causal_report is not None, "Causal report must exist"
        assert result.continuity_report is not None, "Continuity report must exist"

        causal_score = result.causal_report.causal_score
        continuity_score = result.continuity_report.continuity_score

        assert causal_score >= 7.0, (
            f"Causal score {causal_score} should be >= 7.0 after pipeline"
        )
        assert continuity_score >= 7.0, (
            f"Continuity score {continuity_score} should be >= 7.0 after pipeline"
        )

        layout = ProjectLayout(storage.existing_project_dir(project_id))
        rp_report_path = layout.reading_power_report_path(1)
        assert storage.exists(rp_report_path)

        rp_data = storage.load_json(rp_report_path)
        assert rp_data is not None
        assert "pipeline_stage" in rp_data

        chapter_text = storage.load_text(layout.chapter_path(1))
        assert chapter_text is not None
        assert len(chapter_text) > 0, "Chapter text should not be empty"

        assert result.eval_report is not None
        assert result.eval_report.overall_score >= 6.0, (
            f"Overall eval score {result.eval_report.overall_score} should be >= 6.0"
        )
