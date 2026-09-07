"""Render-gate regressions for every prompt in the chapter generation flow."""

from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ValidationError as NovelForgeValidationError
from novel_forge.core.schemas.beats import Beat, StoryBeats
from novel_forge.core.schemas.continuity import CausalLink
from novel_forge.pipeline.long.services.chapter_position import build_chapter_position
from novel_forge.pipeline.steps.evaluate_context import project_evaluate_context
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.context_types import CHAPTER_FLOW_PROMPT_TASKS
from novel_forge.prompts.registry import PromptRegistry


class _InvalidModelDump:
    def model_dump(self, *, mode: str) -> list[object]:
        del mode
        return []


_REQUIRED_MINIMUMS: dict[TaskType, dict[str, object]] = {
    TaskType.CONTEXT_COMPRESS: {"blocks": []},
    TaskType.ADAPTIVE_COMPRESS: {"blocks": []},
    TaskType.VERIFY_COMPRESSION: {"original": "original", "compressed": "short"},
    TaskType.VALIDATE_SCENE_PLAN: {
        "scene_plan": {},
        "bridge_card": {},
        "chapter_card": {},
    },
    TaskType.DRAFT_SCENE: {"current_scene": {}},
    TaskType.WAVE_CHAPTER: {"draft_text": "text"},
    TaskType.EDIT_CHAPTER: {"draft_text": "text"},
    TaskType.EVALUATE: {"draft_text": "text"},
    TaskType.CHECK_ALIGNMENT: {"chapter_outline": {}, "chapter_plan": {}},
    TaskType.CHECK_EDITORIAL: {"editorial_contract": {}},
    TaskType.ELEMENT_PROGRESS_ARBITER: {"element": {}, "rule_eval": {}, "context": {}},
    TaskType.REPAIR_SEMANTIC_VERIFY: {
        "issue_description": "issue",
        "repaired_text": "text",
    },
    TaskType.EXTRACT_MOTIFS: {"text": "text"},
    TaskType.GUARD_CONSTRAINT_CHECK: {"constraint": "rule"},
    TaskType.HUMANIZE_PARAGRAPH_REWRITE: {"paragraphs": [], "pattern_hits": []},
}

_TIGHTENED_DICT_FIELD_CASES: tuple[tuple[TaskType, str, object], ...] = (
    (TaskType.CHECK_CONTINUITY, "chapter_state_packet", {"k": "v"}),
    (TaskType.CHECK_CONTINUITY, "chapter_bridge", {"bridge_summary": "b"}),
    (TaskType.CHECK_CONTINUITY, "chapter_plan", {"opening_contract": "oc"}),
    (TaskType.REPAIR_READING_POWER, "expected_hook", {"hook_type": "cliff"}),
    (TaskType.EVALUATE, "blueprint", {"synopsis": "s"}),
    (TaskType.EVALUATE, "anchor", {"time_frame": "t"}),
)


def _minimum_context(task_type: TaskType, *, output_language: str) -> dict[str, object]:
    context: dict[str, object] = {
        "chapter_number": 1,
        "chapter_text": "text",
        "stage_cards": {},
        "output_language": output_language,
        **_REQUIRED_MINIMUMS.get(task_type, {}),
    }
    if task_type == TaskType.PATCH_CHAPTER:
        context["issues_with_windows"] = [
            {
                "summary": "issue",
                "window_text": "text",
                "repair_directive": {"repair_strategy": "replace"},
            }
        ]
    return context


@pytest.mark.parametrize(
    ("locale", "output_language"),
    [("zh", "Chinese"), ("en", "English")],
)
def test_every_chapter_flow_prompt_passes_typed_render_gate(
    locale: str,
    output_language: str,
) -> None:
    builder = PromptBuilder(PromptRegistry(locale=locale, allow_pack_fallback=True))

    for task_type in sorted(CHAPTER_FLOW_PROMPT_TASKS, key=lambda item: item.value):
        rendered = builder.render(
            task_type,
            _minimum_context(task_type, output_language=output_language),
        )
        assert rendered


def test_wave_prompt_accepts_production_chapter_position_mapping() -> None:
    chapter_position = build_chapter_position(
        {"total_chapters": 60, "chapters": []},
        6,
    )
    rendered = PromptBuilder().render(
        TaskType.WAVE_CHAPTER,
        {
            "chapter_number": 6,
            "draft_text": "测试草稿",
            "stage_cards": {},
            "chapter_position": chapter_position,
        },
    )

    assert "第 6 / 60 章" in rendered
    assert "is_last_chapter=False" in rendered


@pytest.mark.parametrize("invalid_position", ["6/60", {"chapter_number": 6}])
def test_chapter_position_rejects_incomplete_or_scalar_contract_drift(
    invalid_position: object,
) -> None:
    with pytest.raises(NovelForgeValidationError, match="chapter_position"):
        PromptBuilder().render(
            TaskType.WAVE_CHAPTER,
            {
                "chapter_number": 6,
                "draft_text": "测试草稿",
                "stage_cards": {},
                "chapter_position": invalid_position,
            },
        )


def test_chapter_flow_registry_covers_all_six_phases_and_recovery_paths() -> None:
    expected = {
        # Planning
        TaskType.CONTEXT_COMPRESS,
        TaskType.ADAPTIVE_COMPRESS,
        TaskType.VERIFY_COMPRESSION,
        TaskType.BRIDGE_CHAPTER,
        TaskType.PLAN_CHAPTER,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.VALIDATE_SCENE_PLAN,
        # Generate
        TaskType.DRAFT_CHAPTER,
        TaskType.DRAFT_SCENE,
        TaskType.WAVE_CHAPTER,
        # Review and bounded repair
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_CONTINUITY,
        TaskType.VALIDATE_CAUSAL,
        TaskType.CHECK_ALIGNMENT,
        TaskType.PATCH_CHAPTER,
        TaskType.REPAIR_CONTINUITY,
        TaskType.REPAIR_CAUSAL,
        TaskType.EVALUATE_READING_POWER,
        TaskType.REPAIR_READING_POWER,
        TaskType.REPAIR_STRATEGY_DIAGNOSE,
        TaskType.REPAIR_SEMANTIC_VERIFY,
        # Polish and humanize
        TaskType.POLISH_CHAPTER,
        TaskType.HUMANIZE_SCAN,
        TaskType.HUMANIZE_PARAGRAPH_REWRITE,
        # Finalize
        TaskType.GUARD_CONSTRAINT_CHECK,
        TaskType.MACRO_GUARD_AUDIT,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_FINAL_STATE,
        TaskType.EXTRACT_MOTIFS,
        TaskType.SUMMARIZE_CHAPTER,
    }

    assert expected <= CHAPTER_FLOW_PROMPT_TASKS


@pytest.mark.parametrize(
    ("task_type", "context"),
    [
        (TaskType.DRAFT_SCENE, {}),
        (TaskType.GUARD_CONSTRAINT_CHECK, {}),
        (TaskType.CONTEXT_COMPRESS, {}),
    ],
)
def test_missing_required_chapter_context_fails_before_jinja(
    task_type: TaskType,
    context: dict[str, object],
) -> None:
    with pytest.raises(NovelForgeValidationError, match="Prompt context validation failed"):
        PromptBuilder().render(task_type, context)


def test_patch_prompt_materializes_sparse_nested_recovery_contexts() -> None:
    rendered = PromptBuilder().render(
        TaskType.PATCH_CHAPTER,
        {
            "chapter_number": 6,
            "issues_with_windows": [
                {
                    "summary": "world-rule conflict",
                    "window_text": "watch text",
                    "repair_directive": {"repair_strategy": "replace"},
                }
            ],
            "causal_link": {
                "previous_event": "entered the dream",
                "memory_guidance": {
                    "strategy_recommendation": {"preferred_strategy": "patch"},
                    "total_attempts": 2,
                },
            },
            "continuity_context": {
                "previous_exit_state": "watch remains worn",
                "memory_guidance": {"strategy_recommendation": {"reason": "local edit"}},
            },
            "boundary_context": {"focus": "opening"},
        },
    )

    assert "world-rule conflict" in rendered
    assert "watch text" in rendered


def test_patch_prompt_renders_canonical_carry_forward_text() -> None:
    rendered = PromptBuilder().render(
        TaskType.PATCH_CHAPTER,
        {
            "chapter_number": 6,
            "issues_with_windows": [
                {
                    "summary": "carry-forward missing",
                    "window_text": "chapter text",
                    "repair_directive": {"repair_strategy": "replace"},
                }
            ],
            "continuity_context": {"must_carry_forward": ["the watch remains on the left wrist"]},
        },
    )

    assert "the watch remains on the left wrist" in rendered


def test_evaluate_context_projector_converts_domain_models_to_canonical_dto() -> None:
    projected = project_evaluate_context(
        {
            "beats": StoryBeats(beats=[Beat(sequence=1, summary="the bracelet starts ticking")]),
            "causal_link": CausalLink(
                previous_event="Shen An touched the silver bracelet",
                causal_mechanism="the pocket watch gears started moving",
            ),
        }
    )

    assert projected["beats"][0]["summary"] == "the bracelet starts ticking"
    assert projected["causal_link"]["previous_event"] == "Shen An touched the silver bracelet"


def test_evaluate_prompt_accepts_only_canonical_flat_beat_dto() -> None:
    rendered = PromptBuilder().render(
        TaskType.EVALUATE,
        {
            "draft_text": "chapter text",
            "beats": [
                {
                    "sequence": 1,
                    "beat_type": "rising",
                    "summary": "the bracelet starts ticking",
                }
            ],
        },
    )

    assert "the bracelet starts ticking" in rendered


@pytest.mark.parametrize(
    "noncanonical_beats",
    [
        StoryBeats(beats=[Beat(sequence=1, summary="domain model")]),
        {"beats": [{"sequence": 1, "summary": "wrapped list"}]},
    ],
)
def test_evaluate_prompt_rejects_noncanonical_beat_containers(
    noncanonical_beats: object,
) -> None:
    with pytest.raises(NovelForgeValidationError, match="beats"):
        PromptBuilder().render(
            TaskType.EVALUATE,
            {"draft_text": "chapter text", "beats": noncanonical_beats},
        )


def test_motif_prompt_accepts_only_structured_context_shape() -> None:
    builder = PromptBuilder()
    structured = builder.render(
        TaskType.EXTRACT_MOTIFS,
        {
            "text": "the bell rang",
            "element_focus": ["sound", "symbol"],
            "motif_category_distribution": {"sound": {"count": 2, "percentage": 100}},
        },
    )

    assert "sound、symbol" in structured

    with pytest.raises(NovelForgeValidationError, match="element_focus"):
        builder.render(
            TaskType.EXTRACT_MOTIFS,
            {
                "text": "the bell rang",
                "element_focus": "sound, symbol",
                "motif_category_distribution": "sound: 2 (100%)",
            },
        )


@pytest.mark.parametrize(
    "task_type",
    [TaskType.REPAIR_CAUSAL, TaskType.REPAIR_READING_POWER],
)
def test_fulltext_repair_prompts_materialize_sparse_issue_and_history_fields(
    task_type: TaskType,
) -> None:
    rendered = PromptBuilder().render(
        task_type,
        {
            "chapter_number": 6,
            "chapter_text": "text",
            "stage_cards": {},
            "typed_issues": {"world_rule_conflict": [{"summary": "keep watch worn"}]},
            "memory_guidance": {
                "strategy_recommendation": {"preferred_strategy": "patch"},
                "matched_issues": [{"summary": "prior repair"}],
                "total_attempts": 2,
            },
        },
    )

    assert "keep watch worn" in rendered


@pytest.mark.parametrize(
    ("locale", "output_language"),
    [("zh", "Chinese"), ("en", "English")],
)
def test_every_chapter_flow_task_renders_with_production_chapter_position(
    locale: str,
    output_language: str,
) -> None:
    """Every chapter-flow task must accept the production shape of chapter_position.

    Guards against the historical ``WavePromptContext.chapter_position: str`` drift:
    if the field is mis-declared as str, the production dict from
    ``build_chapter_position`` is rejected at validation time (before model routing).
    """
    builder = PromptBuilder(PromptRegistry(locale=locale, allow_pack_fallback=True))
    chapter_position = build_chapter_position(
        {"total_chapters": 60, "chapters": []},
        6,
    )
    bogus_position = "第 0 / 0 章" if locale == "zh" else "Chapter 0 / 0"
    expected_position = "第 6 / 60 章" if locale == "zh" else "Chapter 6 / 60"
    for task_type in sorted(CHAPTER_FLOW_PROMPT_TASKS, key=lambda item: item.value):
        context = {
            **_minimum_context(task_type, output_language=output_language),
            "chapter_position": chapter_position,
        }
        rendered = builder.render(task_type, context)
        assert rendered
        assert bogus_position not in rendered
        if task_type == TaskType.WAVE_CHAPTER:
            assert expected_position in rendered


@pytest.mark.parametrize(
    "invalid_position",
    ["6/60", 42, ["chapter", 6], {"chapter_number": 6}],
)
def test_chapter_flow_tasks_reject_chapter_position_type_drift(
    invalid_position: object,
) -> None:
    """All chapter-flow tasks inheriting ChapterFlowPromptContext must reject
    chapter_position type drift.

    Covers str / int / list / partial-dict shapes. The original bug let a ``str``
    declaration slip through because tests omitted the field; this test forces
    every inheriting task to reject non-ChapterPositionPromptContext inputs
    explicitly. PATCH_CHAPTER is excluded because it uses an independent
    PatchChapterContext that does not declare chapter_position.
    """
    from novel_forge.prompts.context_types.chapter_flow import (
        CHAPTER_FLOW_PROMPT_CONTEXT_MODELS,
        ChapterFlowPromptContext,
    )

    for task_type in sorted(CHAPTER_FLOW_PROMPT_TASKS, key=lambda item: item.value):
        model = CHAPTER_FLOW_PROMPT_CONTEXT_MODELS[task_type]
        if not issubclass(model, ChapterFlowPromptContext):
            continue
        with pytest.raises(NovelForgeValidationError, match="chapter_position"):
            PromptBuilder().render(
                task_type,
                {
                    **_minimum_context(task_type, output_language="Chinese"),
                    "chapter_position": invalid_position,
                },
            )


@pytest.mark.parametrize(
    ("task_type", "field_name", "valid_value"),
    _TIGHTENED_DICT_FIELD_CASES,
)
@pytest.mark.parametrize(
    "invalid_value",
    ["a string", "", 42, [1, 2, 3], [], _InvalidModelDump()],
)
def test_tightened_fields_reject_scalar_type_drift(
    task_type: TaskType,
    field_name: str,
    valid_value: object,
    invalid_value: object,
) -> None:
    """Tightened fields reject scalars, lists, and invalid model-like outputs.

    Validates the P1 hardening: ContinuityCheck / RepairPromptContext /
    EvaluateDraft fields that were previously ``Any`` now reject invalid shapes
    that would otherwise mask producer contract drift.
    """
    from pydantic import ValidationError as PydanticValidationError

    from novel_forge.prompts.context_types.chapter_flow import (
        CHAPTER_FLOW_PROMPT_CONTEXT_MODELS,
    )

    model = CHAPTER_FLOW_PROMPT_CONTEXT_MODELS[task_type]
    base_ctx: dict[str, object] = {
        **_minimum_context(task_type, output_language="Chinese"),
        field_name: valid_value,
    }
    # Sanity: the valid dict payload must construct cleanly.
    model.model_validate(base_ctx)
    # Drifted payloads must be rejected at the model layer.
    with pytest.raises(PydanticValidationError, match=field_name):
        model.model_validate({**base_ctx, field_name: invalid_value})


@pytest.mark.parametrize(
    ("task_type", "field_name"),
    [(task_type, field_name) for task_type, field_name, _ in _TIGHTENED_DICT_FIELD_CASES],
)
def test_tightened_fields_normalize_empty_mapping_to_none(
    task_type: TaskType,
    field_name: str,
) -> None:
    from novel_forge.prompts.context_types.chapter_flow import (
        CHAPTER_FLOW_PROMPT_CONTEXT_MODELS,
    )

    model = CHAPTER_FLOW_PROMPT_CONTEXT_MODELS[task_type]
    validated = model.model_validate(
        {
            **_minimum_context(task_type, output_language="Chinese"),
            field_name: {},
        }
    )

    assert getattr(validated, field_name) is None
