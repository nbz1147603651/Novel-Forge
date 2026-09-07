"""Bounded adaptive 2+1 creative-direction exploration for long initialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.init_v2 import (
    CreativeDirectionCandidate,
    CreativeDirectionCandidateBatch,
    CreativeDirectionDecision,
    CreativeDirectorPacket,
)
from novel_forge.pipeline.long.services.init.init_user_intent import (
    candidate_intent_conflicts,
)
from novel_forge.pipeline.long.services.init.init_v2 import StructuredTaskRunner, hash_payload


@dataclass(frozen=True)
class AdaptiveCreativeExplorationResult:
    """Selected seed plus enough detail for lineage and an optional human gate."""

    packet: CreativeDirectorPacket
    candidates: tuple[CreativeDirectionCandidate, ...]
    decision: CreativeDirectionDecision | None
    selected_candidate_id: str = ""
    model_calls: int = 0
    used_fallback: bool = False
    fallback_reason: str = ""


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def merge_creative_director_packet(
    *, seed: CreativeDirectorPacket, deterministic: CreativeDirectorPacket
) -> CreativeDirectorPacket:
    """Use the selected direction as a seed, then add accepted deterministic context."""

    return CreativeDirectorPacket(
        emotional_engine=_dedupe([*seed.emotional_engine, *deterministic.emotional_engine])[:8],
        thematic_promises=_dedupe(
            [*seed.thematic_promises, *deterministic.thematic_promises]
        )[:8],
        signature_motifs=_dedupe([*seed.signature_motifs, *deterministic.signature_motifs])[:10],
        relationship_tensions=_dedupe(
            [*seed.relationship_tensions, *deterministic.relationship_tensions]
        )[:18],
        anti_cliche_rules=_dedupe(
            [*seed.anti_cliche_rules, *deterministic.anti_cliche_rules]
        )[:10],
        scene_potential=_dedupe([*seed.scene_potential, *deterministic.scene_potential])[:10],
        notes="；".join(_dedupe([seed.notes, deterministic.notes])),
    )


def _validated_candidates(
    batch: CreativeDirectionCandidateBatch,
    *,
    user_intent: dict[str, Any],
) -> list[CreativeDirectionCandidate]:
    result: list[CreativeDirectionCandidate] = []
    for candidate in batch.candidates:
        conflicts = candidate_intent_conflicts(
            user_intent=user_intent,
            preserved_intent_ids=candidate.preserved_intent_ids,
            declared_conflicts=candidate.intent_conflicts,
            scene_potential=candidate.packet.scene_potential,
            candidate_notes=candidate.packet.notes,
        )
        result.append(candidate.model_copy(update={"intent_conflicts": conflicts}))
    return result


def _operational_fallback(
    *,
    deterministic_packet: CreativeDirectorPacket,
    candidates: list[CreativeDirectionCandidate],
    decision: CreativeDirectionDecision | None,
    model_calls: int,
    phase: str,
    error: Exception,
    on_step: Any,
) -> AdaptiveCreativeExplorationResult:
    """Keep optional concept exploration from blocking deterministic initialization."""

    reason = f"{phase}:{type(error).__name__}"
    on_step(
        "init_creative_exploration_fallback",
        {
            "error_kind": "optional_creative_exploration_failed",
            "error_type": type(error).__name__,
            "phase": phase,
            "model_calls": model_calls,
            "error": str(error),
            "action": "use_deterministic_creative_director_packet",
        },
    )
    return AdaptiveCreativeExplorationResult(
        packet=deterministic_packet,
        candidates=tuple(candidates),
        decision=decision,
        model_calls=model_calls,
        used_fallback=True,
        fallback_reason=reason,
    )


async def run_adaptive_creative_exploration(
    *,
    runner: StructuredTaskRunner,
    user_intent: dict[str, Any],
    creative_context: dict[str, Any],
    deterministic_packet: CreativeDirectorPacket,
    upstream_hashes: dict[str, str],
    on_step: Any,
    candidate_temperature: float = 0.75,
    selection_temperature: float = 0.15,
) -> AdaptiveCreativeExplorationResult:
    """Run at most four calls: two candidates, select, optional third, reselect."""

    try:
        batch = cast(
            CreativeDirectionCandidateBatch,
            await runner.run_json_block(
                block_key="creative_direction_candidates",
                task_type=TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
                context={
                    **creative_context,
                    "user_intent": user_intent,
                    "creative_context": creative_context,
                    "candidate_count": 2,
                    "candidate_seed": [],
                },
                model_type=CreativeDirectionCandidateBatch,
                upstream_hashes=upstream_hashes,
                max_tokens=5000,
                temperature=candidate_temperature,
            ),
        )
    except Exception as exc:
        return _operational_fallback(
            deterministic_packet=deterministic_packet,
            candidates=[],
            decision=None,
            model_calls=1,
            phase="candidate_generation",
            error=exc,
            on_step=on_step,
        )
    candidates = _validated_candidates(batch, user_intent=user_intent)
    calls = 1
    valid = [candidate for candidate in candidates if not candidate.intent_conflicts]
    on_step(
        "init_creative_candidates",
        {"total": len(candidates), "valid": len(valid), "model_calls": calls},
    )
    if not valid:
        return AdaptiveCreativeExplorationResult(
            packet=deterministic_packet,
            candidates=tuple(candidates),
            decision=None,
            model_calls=calls,
            used_fallback=True,
            fallback_reason="all_candidates_conflicted",
        )

    try:
        decision = cast(
            CreativeDirectionDecision,
            await runner.run_json_block(
                block_key="creative_direction_select",
                task_type=TaskType.INIT_CREATIVE_DIRECTION_SELECT,
                context={
                    **creative_context,
                    "user_intent": user_intent,
                    "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                },
                model_type=CreativeDirectionDecision,
                upstream_hashes={**upstream_hashes, "candidates": hash_payload(candidates)},
                max_tokens=1800,
                temperature=selection_temperature,
            ),
        )
    except Exception as exc:
        return _operational_fallback(
            deterministic_packet=deterministic_packet,
            candidates=candidates,
            decision=None,
            model_calls=2,
            phase="candidate_selection",
            error=exc,
            on_step=on_step,
        )
    calls += 1
    needs_third = bool(
        decision.need_third_candidate
        or decision.diversity_score < 0.55
        or decision.confidence < 0.60
        or decision.selected_candidate_id not in {item.candidate_id for item in valid}
    )

    if needs_third:
        try:
            third_batch = cast(
                CreativeDirectionCandidateBatch,
                await runner.run_json_block(
                    block_key="creative_direction_candidate_third",
                    task_type=TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
                    context={
                        **creative_context,
                        "user_intent": user_intent,
                        "creative_context": creative_context,
                        "candidate_count": 1,
                        "candidate_seed": [
                            candidate.model_dump(mode="json") for candidate in candidates
                        ],
                    },
                    model_type=CreativeDirectionCandidateBatch,
                    upstream_hashes={
                        **upstream_hashes,
                        "first_candidates": hash_payload(candidates),
                    },
                    max_tokens=3200,
                    temperature=candidate_temperature,
                ),
            )
        except Exception as exc:
            return _operational_fallback(
                deterministic_packet=deterministic_packet,
                candidates=candidates,
                decision=decision,
                model_calls=3,
                phase="third_candidate_generation",
                error=exc,
                on_step=on_step,
            )
        calls += 1
        candidates.extend(_validated_candidates(third_batch, user_intent=user_intent))
        valid = [candidate for candidate in candidates if not candidate.intent_conflicts]
        if valid:
            try:
                decision = cast(
                    CreativeDirectionDecision,
                    await runner.run_json_block(
                        block_key="creative_direction_select_final",
                        task_type=TaskType.INIT_CREATIVE_DIRECTION_SELECT,
                        context={
                            **creative_context,
                            "user_intent": user_intent,
                            "candidates": [
                                candidate.model_dump(mode="json") for candidate in candidates
                            ],
                        },
                        model_type=CreativeDirectionDecision,
                        upstream_hashes={
                            **upstream_hashes,
                            "all_candidates": hash_payload(candidates),
                        },
                        max_tokens=1800,
                        temperature=selection_temperature,
                    ),
                )
            except Exception as exc:
                return _operational_fallback(
                    deterministic_packet=deterministic_packet,
                    candidates=candidates,
                    decision=decision,
                    model_calls=4,
                    phase="final_candidate_selection",
                    error=exc,
                    on_step=on_step,
                )
            calls += 1

    selected = next(
        (
            candidate
            for candidate in valid
            if candidate.candidate_id == decision.selected_candidate_id
        ),
        None,
    )
    if selected is None:
        return AdaptiveCreativeExplorationResult(
            packet=deterministic_packet,
            candidates=tuple(candidates),
            decision=decision,
            model_calls=calls,
            used_fallback=True,
            fallback_reason="selected_candidate_unavailable",
        )
    return AdaptiveCreativeExplorationResult(
        packet=merge_creative_director_packet(
            seed=selected.packet,
            deterministic=deterministic_packet,
        ),
        candidates=tuple(candidates),
        decision=decision,
        selected_candidate_id=selected.candidate_id,
        model_calls=calls,
        used_fallback=False,
    )


__all__ = (
    "AdaptiveCreativeExplorationResult",
    "merge_creative_director_packet",
    "run_adaptive_creative_exploration",
)
