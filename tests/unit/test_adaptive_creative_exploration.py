from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.init_v2 import (
    CreativeDirectionCandidate,
    CreativeDirectionCandidateBatch,
    CreativeDirectionDecision,
    CreativeDirectorPacket,
)
from novel_forge.pipeline.long.services.init.init_creative_exploration import (
    run_adaptive_creative_exploration,
)


class _QueuedRunner:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def run_json_block(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _FailingRunner:
    async def run_json_block(self, **_kwargs: Any) -> Any:
        raise ValueError("candidate packet missing")


def _packet(label: str) -> CreativeDirectorPacket:
    return CreativeDirectorPacket(
        emotional_engine=[f"情感-{label}"],
        thematic_promises=[f"主题-{label}"],
        scene_potential=[f"场景-{label}"],
    )


def _candidate(candidate_id: str, intent_ids: list[str]) -> CreativeDirectionCandidate:
    return CreativeDirectionCandidate(
        candidate_id=candidate_id,
        packet=_packet(candidate_id),
        preserved_intent_ids=intent_ids,
    )


async def test_adaptive_base_path_uses_exactly_two_calls() -> None:
    intent = {"immutable_intent_ids": ["user:ending_style"]}
    batch = CreativeDirectionCandidateBatch(
        candidates=[_candidate("a", intent["immutable_intent_ids"]), _candidate("b", intent["immutable_intent_ids"])]
    )
    decision = CreativeDirectionDecision(
        selected_candidate_id="b",
        diversity_score=0.8,
        confidence=0.9,
        selection_reason="b 更可执行",
    )
    runner = _QueuedRunner(batch, decision)

    result = await run_adaptive_creative_exploration(
        runner=runner,  # type: ignore[arg-type]
        user_intent=intent,
        creative_context={},
        deterministic_packet=_packet("base"),
        upstream_hashes={"spec": "hash"},
        on_step=lambda *_args: None,
    )

    assert result.model_calls == 2
    assert result.selected_candidate_id == "b"
    assert not result.used_fallback
    assert [call["temperature"] for call in runner.calls] == [0.75, 0.15]


async def test_low_diversity_adds_one_candidate_and_reselects_once() -> None:
    intent = {"immutable_intent_ids": ["user:pov_hint"]}
    first = CreativeDirectionCandidateBatch(
        candidates=[_candidate("a", intent["immutable_intent_ids"]), _candidate("b", intent["immutable_intent_ids"])]
    )
    low = CreativeDirectionDecision(
        selected_candidate_id="a",
        diversity_score=0.4,
        confidence=0.8,
        need_third_candidate=True,
        selection_reason="需补充",
    )
    third = CreativeDirectionCandidateBatch(
        candidates=[_candidate("c", intent["immutable_intent_ids"])]
    )
    final = CreativeDirectionDecision(
        selected_candidate_id="c",
        diversity_score=0.75,
        confidence=0.85,
        selection_reason="c 更佳",
    )
    runner = _QueuedRunner(first, low, third, final)

    result = await run_adaptive_creative_exploration(
        runner=runner,  # type: ignore[arg-type]
        user_intent=intent,
        creative_context={},
        deterministic_packet=_packet("base"),
        upstream_hashes={"spec": "hash"},
        on_step=lambda *_args: None,
    )

    assert result.model_calls == 4
    assert result.selected_candidate_id == "c"
    assert len(runner.calls) == 4


async def test_all_conflicting_candidates_fall_back_without_rewriting_intent() -> None:
    intent = {"immutable_intent_ids": ["user:ending_style"]}
    conflict = CreativeDirectionCandidate(
        candidate_id="bad",
        packet=_packet("bad"),
        preserved_intent_ids=intent["immutable_intent_ids"],
        intent_conflicts=["与用户 HE 结局冲突"],
    )
    runner = _QueuedRunner(CreativeDirectionCandidateBatch(candidates=[conflict]))
    deterministic = _packet("deterministic")

    result = await run_adaptive_creative_exploration(
        runner=runner,  # type: ignore[arg-type]
        user_intent=intent,
        creative_context={},
        deterministic_packet=deterministic,
        upstream_hashes={"spec": "hash"},
        on_step=lambda *_args: None,
    )

    assert result.used_fallback
    assert result.packet == deterministic
    assert result.model_calls == 1
    assert result.fallback_reason == "all_candidates_conflicted"


async def test_unknown_selected_candidate_records_semantic_fallback_reason() -> None:
    intent = {"immutable_intent_ids": ["user:premise"]}
    batch = CreativeDirectionCandidateBatch(
        candidates=[_candidate("a", intent["immutable_intent_ids"])]
    )
    decision = CreativeDirectionDecision(
        selected_candidate_id="missing",
        diversity_score=0.8,
        confidence=0.9,
        selection_reason="返回了不存在的候选",
    )
    third = CreativeDirectionCandidateBatch(
        candidates=[_candidate("b", intent["immutable_intent_ids"])]
    )
    final_decision = decision.model_copy(update={"need_third_candidate": False})
    runner = _QueuedRunner(batch, decision, third, final_decision)
    deterministic = _packet("deterministic")

    result = await run_adaptive_creative_exploration(
        runner=runner,  # type: ignore[arg-type]
        user_intent=intent,
        creative_context={},
        deterministic_packet=deterministic,
        upstream_hashes={"spec": "hash"},
        on_step=lambda *_args: None,
    )

    assert result.packet == deterministic
    assert result.used_fallback
    assert result.fallback_reason == "selected_candidate_unavailable"


async def test_candidate_generation_failure_uses_deterministic_packet() -> None:
    deterministic = _packet("deterministic")
    events: list[tuple[str, dict[str, Any]]] = []

    result = await run_adaptive_creative_exploration(
        runner=_FailingRunner(),  # type: ignore[arg-type]
        user_intent={"immutable_intent_ids": ["user:premise"]},
        creative_context={},
        deterministic_packet=deterministic,
        upstream_hashes={"spec": "hash"},
        on_step=lambda name, payload: events.append((name, payload)),
    )

    assert result.packet == deterministic
    assert result.candidates == ()
    assert result.used_fallback is True
    assert result.fallback_reason == "candidate_generation:ValueError"
    assert result.model_calls == 1
    assert events == [
        (
            "init_creative_exploration_fallback",
            {
                "error_kind": "optional_creative_exploration_failed",
                "error_type": "ValueError",
                "phase": "candidate_generation",
                "model_calls": 1,
                "error": "candidate packet missing",
                "action": "use_deterministic_creative_director_packet",
            },
        )
    ]
