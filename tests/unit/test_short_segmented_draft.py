"""Tests for segmented short-story drafting."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.draft import Draft
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.gateway.types import ModelResponse
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.draft_step import DraftInput, DraftStep


class _CaptureRouter:
    def __init__(self) -> None:
        self.last_request = None

    async def route(self, request):
        self.last_request = request
        return ModelResponse(content="片段正文。", model_id="mock-model")

    async def stream_route(self, request, on_chunk=None):
        self.last_request = request
        if on_chunk is not None:
            on_chunk("片段正文。")
        return ModelResponse(content="片段正文。", model_id="mock-model")

    def resolve_model_id_for_task(self, *args: object, **kwargs: object) -> str:
        return "mock-model"


def _sample_spec(*, length_target: int = 4200) -> StorySpec:
    return StorySpec(
        title="旧港回声",
        genre="mystery",
        theme="真相与归还",
        tone="克制",
        length_target=length_target,
        language="zh",
        conflict_hint="主角必须在风暴前追回一封会改变命运的信",
        ending_style="余韵式收束",
    )


def _sample_beats() -> StoryBeats:
    return StoryBeats.model_validate(
        {
            "beats": [
                {
                    "sequence": 1,
                    "beat_type": "opening",
                    "summary": "沈青在旧港的暴雨前夜收到一封来源不明的来信。",
                    "tension_level": 3,
                    "characters_involved": ["沈青"],
                    "setting": "旧港码头",
                },
                {
                    "sequence": 2,
                    "beat_type": "rising",
                    "summary": "她沿着信上的线索追到废弃仓库，发现失踪者留下的新证据。",
                    "tension_level": 6,
                    "characters_involved": ["沈青", "阿策"],
                    "setting": "废弃仓库",
                },
                {
                    "sequence": 3,
                    "beat_type": "climax",
                    "summary": "风暴抵岸时，沈青与阿策当面对质，真相和谎言一起被掀开。",
                    "tension_level": 9,
                    "characters_involved": ["沈青", "阿策"],
                    "setting": "仓库天台",
                },
                {
                    "sequence": 4,
                    "beat_type": "resolution",
                    "summary": "沈青决定交出那封信，也接受自己必须承担的代价。",
                    "tension_level": 5,
                    "characters_involved": ["沈青"],
                    "setting": "海堤尽头",
                },
            ],
            "total_estimated_words": 4200,
        }
    )


async def test_draft_step_forwards_prior_messages_and_multi_turn(builder, runtime_settings) -> None:
    router = _CaptureRouter()
    step = DraftStep(router, builder, settings=runtime_settings)

    await step.run(
        DraftInput(
            task_type=TaskType.DRAFT,
            context={
                "target_word_count": 1200,
                "spec": _sample_spec(length_target=1200),
                "beats": _sample_beats(),
            },
            prior_messages=[
                {"role": "user", "content": "上一段任务摘要"},
                {"role": "assistant", "content": "上一段正文摘要"},
            ],
            multi_turn=True,
        )
    )

    assert router.last_request is not None
    assert router.last_request.multi_turn is True
    assert len(router.last_request.messages) == 4
    assert router.last_request.messages[1]["content"] == "上一段任务摘要"
    assert router.last_request.messages[2]["content"] == "上一段正文摘要"


def test_build_short_segments_covers_all_beats(router, builder, tmp_storage) -> None:
    settings = Settings(
        _env_file=None,
        short_segment_mode="on",
        short_segment_target_words=1400,
        short_segment_max_count=3,
    )
    runner = ShortStoryRunner(router, builder, tmp_storage, settings=settings, max_edit_rounds=1)
    spec = _sample_spec()
    beats = _sample_beats()

    execution_plan = runner._build_short_execution_plan(spec, beats, None)
    segments = runner._build_short_segments(
        spec,
        beats,
        execution_plan,
        segment_target_words=1400,
        segment_max_count=3,
    )

    assert len(segments) == 3
    assert segments[0]["is_first"] is True
    assert segments[-1]["is_final"] is True
    assert [(item["start_index"], item["end_index"]) for item in segments] == [
        (0, 0),
        (1, 1),
        (2, 3),
    ]
    assert segments[-1]["structural_roles"][-1] == "resolution"


def test_short_writing_mode_maps_to_segmented_mode(router, builder, tmp_storage) -> None:
    settings = Settings(_env_file=None)
    scene_runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=settings,
        max_edit_rounds=1,
        writing_mode="scene_level",
    )
    whole_runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=settings,
        max_edit_rounds=1,
        writing_mode="whole_chapter",
    )
    auto_runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=settings,
        max_edit_rounds=1,
        writing_mode="auto",
    )
    factory_runner = ShortStoryRunner.from_settings(
        router,
        builder,
        tmp_storage,
        settings,
        writing_mode="scene_level",
    )

    assert scene_runner._effective_segmented_mode("off") == "on"
    assert whole_runner._effective_segmented_mode("on") == "off"
    assert auto_runner._effective_segmented_mode(None) is None
    assert factory_runner._effective_segmented_mode("off") == "on"


async def test_run_initial_draft_segments_and_reuses_history(
    monkeypatch,
    router,
    builder,
    tmp_storage,
) -> None:
    settings = Settings(
        _env_file=None,
        short_segment_mode="on",
        short_segment_target_words=1400,
        short_segment_max_count=3,
        short_draft_multi_turn=True,
        short_draft_multi_turn_providers="mock",
    )
    runner = ShortStoryRunner(router, builder, tmp_storage, settings=settings, max_edit_rounds=1)
    spec = _sample_spec()
    beats = _sample_beats()
    layout = ProjectLayout(tmp_storage.ensure_project_dir("segmented_short"))
    layout.ensure_dirs()
    execution_plan = runner._build_short_execution_plan(spec, beats, None)

    captured_calls: list[dict[str, object]] = []

    async def _fake_run(self, input_data: DraftInput) -> Draft:
        segment = input_data.context["segment_plan"]
        captured_calls.append(
            {
                "segment_index": segment["segment_index"],
                "prior_messages": list(input_data.prior_messages or []),
                "multi_turn": input_data.multi_turn,
                "is_final": segment["is_final"],
            }
        )
        text = f"第{segment['segment_index']}段正文。"
        if segment["is_final"]:
            text += "故事在这里完成收束。"
        return Draft(text=text, iteration=1)

    monkeypatch.setattr(DraftStep, "run", _fake_run)

    merged_text = await runner._run_initial_draft(
        layout,
        spec,
        beats,
        None,
        execution_plan,
        segmented_mode="on",
        segment_target_words=1400,
        segment_max_count=3,
    )

    assert len(captured_calls) == 3
    assert captured_calls[0]["multi_turn"] is True
    assert captured_calls[0]["prior_messages"] == []
    assert len(captured_calls[1]["prior_messages"]) >= 2
    assert len(captured_calls[2]["prior_messages"]) >= 2
    assert "第1段正文。" in merged_text
    assert "故事在这里完成收束。" in merged_text
    assert layout.short_segment_plan_path().exists()
    assert layout.short_segment_draft_path(1).exists()
    assert layout.short_segment_bridge_path(3).exists()
    assert layout.short_draft_path(0).exists()


def test_segment_context_budget_scales_with_segment_target(router, builder, tmp_storage) -> None:
    runner = ShortStoryRunner(
        router, builder, tmp_storage, settings=Settings(_env_file=None), max_edit_rounds=1
    )

    small = runner._build_segment_context_budget(
        {"target_words": 900, "total_segments": 3, "is_first": True},
        story_target_words=3600,
    )
    large = runner._build_segment_context_budget(
        {"target_words": 2400, "total_segments": 3, "is_final": True},
        story_target_words=7200,
    )

    assert small["bridge_chars"] < large["bridge_chars"]
    assert small["history_chars"] < large["history_chars"]
    assert small["history_rounds"] <= large["history_rounds"]


def test_apply_segment_history_budget_trims_rounds_and_chars(router, builder, tmp_storage) -> None:
    runner = ShortStoryRunner(
        router, builder, tmp_storage, settings=Settings(_env_file=None), max_edit_rounds=1
    )
    history = [
        {"role": "user", "content": "上一段目标是推进冲突并保持雨夜场景一致。"},
        {"role": "assistant", "content": "这是第一段较长正文摘要。" * 40},
        {"role": "user", "content": "第二段继续承接，注意人物情绪变化。"},
        {"role": "assistant", "content": "这是第二段较长正文摘要。" * 40},
    ]

    trimmed = runner._apply_segment_history_budget(history, budget_chars=220, max_rounds=1)

    assert len(trimmed) <= 2
    assert sum(len(item["content"]) for item in trimmed) <= 220
