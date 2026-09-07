from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.gateway import profiles
from novel_forge.pipeline.token_budget import validate_prompt_fits_context


class _Router:
    @staticmethod
    def resolve_model_id_for_task(*_args, **_kwargs) -> str:
        return "resolved-test-model"

    @staticmethod
    def output_limit_for_task(*_args, **_kwargs) -> int:
        return 20


def test_context_validation_uses_resolved_model_and_never_authorizes_truncation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(profiles, "get_model_context_window", lambda _model_id: 100)

    result = validate_prompt_fits_context(
        "必要事实" * 30,
        _Router(),
        TaskType.DRAFT_CHAPTER,
        safety_margin=0.9,
    )

    assert result.model_id == "resolved-test-model"
    assert result.fits is False
    assert result.overshoot_tokens > 0
    assert result.token_count_method == "unicode_heuristic"
    assert result.overflow_action == ("route_larger_context_or_partition_complete_coverage")
    assert result.hard_truncation_allowed is False
