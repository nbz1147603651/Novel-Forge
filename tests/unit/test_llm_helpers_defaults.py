from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.pipeline.long.services.generation.llm_helpers import (
    apply_task_response_defaults,
    compute_retry_backoff,
    compute_transient_retry_backoff,
    ensure_required_response_keys,
)


def test_transient_retry_waits_for_earliest_open_provider_recovery() -> None:
    error = ModelGatewayError(
        "all routes open",
        is_transient=True,
        failure_categories=["circuit_open", "circuit_open"],
        provider_health={
            "provider:a": {"state": "open", "retry_after_s": 17.8},
            "provider:b": {"state": "open", "retry_after_s": 29.0},
            "embedding:c": {"state": "closed", "retry_after_s": 0.0},
        },
    )

    assert compute_transient_retry_backoff(error, 1) == 17.8


def test_transient_retry_keeps_normal_backoff_for_mixed_failures() -> None:
    error = ModelGatewayError(
        "mixed transient failures",
        is_transient=True,
        failure_categories=["circuit_open", "rate_limit"],
        provider_health={"provider:a": {"state": "open", "retry_after_s": 30.0}},
    )

    assert compute_transient_retry_backoff(error, 2) == compute_retry_backoff(2)


def test_transient_network_retry_uses_recovery_window() -> None:
    error = ModelGatewayError(
        "dns unavailable",
        is_transient=True,
        failure_categories=["timeout", "network_error"],
    )

    assert compute_transient_retry_backoff(error, 1) == compute_retry_backoff(1) * 5


def test_init_coherence_profile_defaults_lift_nested_required_fields() -> None:
    data = {
        "genre_tags": ["现代都市言情"],
        "project_ontology": {
            "domains": ["relationship"],
            "state_axes": ["relationship_status", "knowledge"],
            "relationship_axes": ["trust"],
            "payoff_types": ["information"],
            "terminology": {"信任墙": "情感防御机制"},
            "narrative_modes": ["双线推进"],
            "conflict_lens": ["信任契约冲突"],
            "extraction_guidance": ["关注信任墙状态变化"],
            "summary": "治愈系都市言情的一致性画像。",
        },
    }

    apply_task_response_defaults(data, TaskType.REFINE_INIT_COHERENCE_PROFILE)
    ensure_required_response_keys(
        data,
        (
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
    )

    assert data["narrative_modes"] == ["双线推进"]
    assert data["extraction_guidance"] == ["关注信任墙状态变化"]
    assert data["summary"] == "治愈系都市言情的一致性画像。"
    assert data["conflict_lens"] == ["信任契约冲突"]
    assert "narrative_modes" not in data["project_ontology"]
    assert "extraction_guidance" not in data["project_ontology"]


def test_init_coherence_profile_defaults_do_not_derive_missing_guidance() -> None:
    data = {
        "genre_tags": ["现代都市言情"],
        "narrative_modes": ["线性推进"],
        "conflict_lens": ["状态轴冲突"],
        "project_ontology": {
            "state_axes": ["relationship_status", "knowledge"],
            "relationship_axes": ["trust"],
            "payoff_types": ["information"],
            "terminology": {"信任墙": "情感防御机制"},
        },
    }

    apply_task_response_defaults(data, TaskType.DERIVE_INIT_COHERENCE_PROFILE)
    assert "extraction_guidance" not in data
    assert "summary" not in data
    with pytest.raises(KeyError):
        ensure_required_response_keys(data, ("extraction_guidance", "summary"))


def test_bridge_defaults_do_not_backfill_narrative_fields() -> None:
    data = {
        "opening_time": "清晨",
        "opening_location": "旧图书馆",
        "opening_pov": "林远",
        "transition_mode": "direct_continue",
        "action_handoff": "林远仍握着怀表",
        "causal_link": {},
    }

    apply_task_response_defaults(data, TaskType.BRIDGE_CHAPTER)
    for key in (
        "emotional_carryover",
        "pending_questions",
        "forbidden_repetition",
        "opening_acceptance_criteria",
    ):
        assert key not in data
    with pytest.raises(KeyError):
        ensure_required_response_keys(data, ("emotional_carryover",))


def test_plan_chapter_defaults_do_not_synthesize_cross_scene_intent() -> None:
    data = {
        "scene_intents": [],
        "opening_contract": "承接上一章。",
        "closing_contract": "留下下一章入口。",
        "required_state_transitions": [],
        "chapter_type": "transition",
        "emotional_arc": "平稳推进",
        "relationship_evolution": [],
        "forbidden_elements": [],
        "forbidden_elements_soft": [],
        "forbidden_elements_quota": [],
        "intentional_callbacks": [],
        "foreshadowing_plan": [],
        "key_revelations": [],
    }

    apply_task_response_defaults(data, TaskType.PLAN_CHAPTER)

    assert "cross_scene_intent" not in data
    with pytest.raises(KeyError):
        ensure_required_response_keys(
            data,
            (
                "scene_intents",
                "opening_contract",
                "closing_contract",
                "required_state_transitions",
                "chapter_type",
                "emotional_arc",
                "relationship_evolution",
                "forbidden_elements",
                "forbidden_elements_soft",
                "forbidden_elements_quota",
                "intentional_callbacks",
                "foreshadowing_plan",
                "key_revelations",
                "cross_scene_intent",
            ),
        )


def test_init_narrative_contract_defaults_do_not_backfill_prompt_fields() -> None:
    data = {
        "world_rules": [],
        "character_arcs": [],
        "plot_threads": [],
    }

    apply_task_response_defaults(data, TaskType.INIT_NARRATIVE_CONTRACT)
    assert "promise_plan" not in data
    assert "notes" not in data
    with pytest.raises(KeyError):
        ensure_required_response_keys(data, ("promise_plan", "notes"))


def test_init_adjudication_defaults_only_add_transport_identity() -> None:
    data = {
        "verdict": "needs_repair",
        "issues": [{"severity": "info", "description": "同一事实重复。"}],
    }

    apply_task_response_defaults(data, TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES)
    assert data["schema_version"] == "audit_v2"
    assert data["dimension"] == "init_coherence"
    for key in (
        "score",
        "metadata",
        "source_refs",
        "repair_scope",
        "preserve",
        "change_intent",
        "blocked",
        "summary",
    ):
        assert key not in data


def test_adjudicate_contract_coherence_defaults_preserve_null_for_retry() -> None:
    data = {
        "verdict": "accept",
        "issues": [],
        "summary": "无冲突。",
        "change_intent": None,
    }

    apply_task_response_defaults(data, TaskType.ADJUDICATE_CONTRACT_COHERENCE)

    assert data["change_intent"] is None
    assert "schema_version" not in data
    assert "blocked" not in data
    assert "score" not in data


def test_adjudicate_contract_coherence_defaults_keep_missing_fields_visible() -> None:
    data = {
        "verdict": "accept",
        "issues": [],
    }

    apply_task_response_defaults(data, TaskType.ADJUDICATE_CONTRACT_COHERENCE)

    assert "change_intent" not in data
    assert "summary" not in data


def test_init_adjudication_defaults_backfill_repair_keys_on_accept_verdict() -> None:
    # Models legitimately omit repair-side keys for accept/defer/ambiguous
    # verdicts; synthesizing empty defaults prevents a required-key retry storm
    # on a benign response (observed: KeyError retries for missing source_refs /
    # repair_scope / preserve / change_intent / blocked on accept batches).
    for verdict in ("accept", "defer", "ambiguous"):
        data = {"verdict": verdict, "issues": []}
        apply_task_response_defaults(data, TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES)
        assert data["schema_version"] == "audit_v2"
        assert data["dimension"] == "init_coherence"
        assert data["source_refs"] == []
        assert data["repair_scope"] == []
        assert data["preserve"] == []
        assert data["change_intent"] == ""
        assert data["blocked"] is False


def test_init_adjudication_defaults_keep_repair_keys_missing_on_needs_repair() -> None:
    # When the verdict actually asks for repair, missing repair keys must stay
    # missing so format validation can request a corrected model response.
    data = {"verdict": "needs_repair", "issues": [{"severity": "high"}]}
    apply_task_response_defaults(data, TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES)
    assert "repair_scope" not in data
    assert "source_refs" not in data
    assert "blocked" not in data


def test_init_adjudication_defaults_do_not_overwrite_model_provided_values() -> None:
    data = {
        "verdict": "accept",
        "issues": [],
        "source_refs": [{"source": "outline_1"}],
        "change_intent": "模型给出的意图",
    }
    apply_task_response_defaults(data, TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES)
    assert data["source_refs"] == [{"source": "outline_1"}]
    assert data["change_intent"] == "模型给出的意图"
    assert data["repair_scope"] == []
    assert data["blocked"] is False
