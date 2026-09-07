from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ContextLengthError
from novel_forge.pipeline.long.services.attention_budget import apply_stage_context_manifest
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    _compact_memory_prompt_context,
)
from novel_forge.pipeline.long.services.generation.llm_service import LLMService
from novel_forge.pipeline.steps.prompt_diagnostics import (
    build_prompt_pressure_diagnostics,
    log_prompt_diagnostics,
)


def test_prompt_pressure_records_stage_card_breakdown() -> None:
    request = SimpleNamespace(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "正文" * 200},
        ],
        max_tokens=2048,
    )
    context = {
        "stage_cards": {
            "memory": {"summary_context": "记忆" * 100},
            "style": {"summary": "风格" * 30},
            "contract": {"hard_facts": ["事实"]},
            "plan": {"scene_intents": [{"summary": "场景"}]},
            "source": {"source_artifact_id": "chapter_source_slice:007"},
            "retrieval_evidence": {
                "selection": {
                    "evidence_token_budget": 2400,
                    "estimated_evidence_tokens": 1800,
                    "has_more_evidence": True,
                }
            },
            "context_budget": {"advisory_overages": {"memory.relevant_history": 2}},
        }
    }

    diagnostics = build_prompt_pressure_diagnostics(
        request,
        context,
        context_window=1_000,
        info_ratio=0.01,
        warn_ratio=0.05,
    )

    assert diagnostics.estimated_prompt_tokens > 0
    assert diagnostics.stage_cards_estimated_tokens > 0
    assert diagnostics.stage_cards_token_ratio > 0
    assert diagnostics.card_token_counts["memory"] > diagnostics.card_token_counts["style"]
    assert diagnostics.status == "overflow"
    payload = diagnostics.to_event_payload()
    assert payload["card_token_counts"]["contract"] > 0
    assert payload["advisory_overages"] == {"memory.relevant_history": 2}
    assert payload["retrieval_selection"]["has_more_evidence"] is True
    assert payload["source_artifact_id"] == "chapter_source_slice:007"
    assert payload["fits_context"] is False
    assert payload["hard_truncation_allowed"] is False
    assert payload["overflow_action"] == ("route_larger_context_or_partition_complete_coverage")


def test_prompt_diagnostics_emits_structured_step_event() -> None:
    request = SimpleNamespace(
        messages=[{"role": "user", "content": "正文" * 40}],
        max_tokens=2048,
    )
    events: list[tuple[str, dict[str, object]]] = []

    result = log_prompt_diagnostics(
        MagicMock(),
        event="plan_prompt_diagnostics",
        request=request,
        context={"stage_cards": {"context_budget": {"advisory_overages": {}}}},
        settings=SimpleNamespace(enabled=True, warn=32000),
        enabled_attr="enabled",
        warn_attr="warn",
        chapter=7,
        on_event=lambda event, payload: events.append((event, payload)),
    )

    assert result is not None
    assert events[0][0] == "plan_prompt_diagnostics"
    assert events[0][1]["chapter"] == 7
    assert events[0][1]["estimated_prompt_tokens"] == result.estimated_prompt_tokens


def test_attention_budget_reports_pressure_without_mutating_source_text() -> None:
    long_sentence = "甲" * 1_000
    summary = ("关键事实" * 120) + "。" + long_sentence
    cards = {
        "stage": "draft",
        "memory": {"summary_context": summary},
    }

    result = apply_stage_context_manifest(cards, stage="draft")

    assert result["memory"]["summary_context"] == summary
    assert result["context_budget"]["advisory_overages"]["memory.summary_context"] > 0
    assert result["context_budget"]["overflow_policy"]["hard_truncation_allowed"] is False


def test_stage_memory_auxiliary_projection_does_not_retruncate_text() -> None:
    summary = ("核心记忆" * 50) + "。" + ("后续细节" * 100)

    compacted = _compact_memory_prompt_context(
        {"summary_context": summary, "foreshadow_due": [{"description": summary}]}
    )

    assert compacted["foreshadow_due"][0]["description"] == summary


def test_prompt_pressure_payload_keeps_legacy_preflight_alias_shape() -> None:
    request = SimpleNamespace(
        messages=[{"role": "user", "content": "测试" * 40}],
        max_tokens=512,
        task_type=TaskType.DRAFT_CHAPTER,
    )
    diagnostics = build_prompt_pressure_diagnostics(
        request,
        {"stage_cards": {"memory": {"summary_context": "记忆" * 20}}},
        context_window=128_000,
    )
    payload = diagnostics.to_event_payload()

    assert payload["estimated_prompt_tokens"] == diagnostics.estimated_prompt_tokens
    assert payload["stage_cards_token_ratio"] >= 0


def test_llm_service_blocks_known_preflight_overflow() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    router = SimpleNamespace(
        task_circuit_breaker=None,
        resolve_model_id_for_task=lambda *_args, **_kwargs: "gpt-4o-mini",
    )
    service = LLMService(
        router=router,
        builder=MagicMock(),
        on_step=lambda event, payload: events.append((event, payload)),
        settings=SimpleNamespace(long_prompt_preflight_block_oversized=True),
    )
    request = SimpleNamespace(
        messages=[{"role": "user", "content": "正文" * 300000}],
        max_tokens=8192,
        model_id="gpt-4o-mini",
    )

    with pytest.raises(ContextLengthError, match="pre-flight context overflow"):
        service._emit_prompt_pressure_preflight(
            task_type=TaskType.GROUND_OUTLINE_RESEARCH,
            request=request,
            context={},
            provider="openai",
            model_id="gpt-4o-mini",
            attempt=1,
            chapter="",
        )

    assert events[-1][1]["preflight_oversized"] is True
