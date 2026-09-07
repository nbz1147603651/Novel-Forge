"""Exact candidate retrieval for init coherence v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from novel_forge.core.schemas.init_coherence import (
    ClaimChapterRange,
    CoherenceClaim,
    ConflictCandidate,
)


@dataclass(frozen=True)
class ExactRetrievalDeps:
    """Callbacks owned by ``init_coherence_v2`` and used by exact retrieval."""

    chapter_span_for_claims: Callable[[list[CoherenceClaim]], ClaimChapterRange | None]
    claim_has_different_state_after: Callable[[CoherenceClaim, CoherenceClaim], bool]
    claim_has_foreshadow_after_reveal: Callable[[CoherenceClaim], bool]
    claim_subject_key: Callable[[CoherenceClaim], str]
    claim_sort_key: Callable[[CoherenceClaim], tuple[Any, ...]]
    claims_have_cognitive_regression: Callable[[CoherenceClaim, CoherenceClaim], bool]
    cognitive_candidate_kinds: Callable[[CoherenceClaim, CoherenceClaim], list[tuple[str, str, str]]]
    cognitive_candidate_types: frozenset[str]
    cognitive_group_keys: Callable[[CoherenceClaim], list[tuple[str, str]]]
    normalize_key: Callable[[Any], str]
    same_artifact_path: Callable[[CoherenceClaim, CoherenceClaim], bool]


def retrieve_exact_candidates(
    claims: list[CoherenceClaim],
    *,
    max_candidates: int,
    deps: ExactRetrievalDeps,
) -> list[ConflictCandidate]:
    candidates: list[ConflictCandidate] = []
    seen_pairs: set[tuple[str, ...]] = set()

    def add_pair(
        claim_a: CoherenceClaim,
        claim_b: CoherenceClaim,
        *,
        candidate_type: str,
        reason: str,
        retrieval_source: str,
        severity_hint: str = "medium",
        complete_coverage: bool = False,
    ) -> None:
        if not complete_coverage and len(candidates) >= max_candidates:
            return
        # One model decision owns one claim pair.  Retrieval hints may discover
        # the same pair through several indexes, but must not spend a second
        # adjudication call on an already covered pair.
        pair_key = tuple(sorted([claim_a.claim_id, claim_b.claim_id]))
        if pair_key in seen_pairs or claim_a.claim_id == claim_b.claim_id:
            return
        seen_pairs.add(pair_key)
        claim_items = sorted([claim_a, claim_b], key=deps.claim_sort_key)
        candidates.append(
            ConflictCandidate(
                candidate_id=f"cand_{len(candidates) + 1:04d}",
                candidate_type=candidate_type,
                reason=reason,
                claim_ids=[claim.claim_id for claim in claim_items],
                claims=claim_items,
                retrieval_sources=[retrieval_source],
                severity_hint=severity_hint,
                confidence=min(claim_a.confidence, claim_b.confidence),
                chapter_span=deps.chapter_span_for_claims(claim_items),
            )
        )

    # Local code selects pairs only by source structure.  It does not infer
    # whether two facts mean the same thing: every pair whose chapter scopes
    # overlap (plus project-wide claims) reaches the model adjudicator once.
    # These coverage pairs intentionally bypass the optional candidate cap;
    # downstream adjudication already performs bounded batching.
    ordered_claims = sorted(claims, key=deps.claim_sort_key)
    for index, claim_a in enumerate(ordered_claims):
        for claim_b in ordered_claims[index + 1 :]:
            if not _claims_share_structural_scope(claim_a, claim_b):
                continue
            add_pair(
                claim_a,
                claim_b,
                candidate_type="structural_overlap",
                reason="来源章节范围重叠，由模型判断两条事实能否同时成立。",
                retrieval_source="exact:structural_scope",
                severity_hint="medium",
                complete_coverage=True,
            )

    grouped: dict[tuple[str, str], list[CoherenceClaim]] = {}
    for claim in claims:
        subject_key = deps.claim_subject_key(claim)
        axis_key = deps.normalize_key(claim.axis)
        if subject_key and axis_key:
            grouped.setdefault((subject_key, axis_key), []).append(claim)
    for (_subject, _axis), group in grouped.items():
        ordered = sorted(group, key=deps.claim_sort_key)
        for idx, claim_a in enumerate(ordered):
            for claim_b in ordered[idx + 1 : idx + 5]:
                if deps.same_artifact_path(claim_a, claim_b):
                    continue
                if deps.claims_have_cognitive_regression(claim_a, claim_b):
                    add_pair(
                        claim_a,
                        claim_b,
                        candidate_type="cognitive_regression",
                        reason="同一主体/状态轴认知层级出现倒退，需校验是否为误判或叙事回忆误导。",
                        retrieval_source="exact:cognitive_regression",
                        severity_hint="high",
                    )
                add_pair(
                    claim_a,
                    claim_b,
                    candidate_type="same_subject_axis",
                    reason="同一主体与状态轴在不同来源中出现，需判断状态顺序是否互斥。",
                    retrieval_source="exact:subject_axis",
                    severity_hint=(
                        "high"
                        if deps.claim_has_different_state_after(claim_a, claim_b)
                        else "medium"
                    ),
                )

    cognitive_groups: dict[tuple[str, str], list[CoherenceClaim]] = {}
    for claim in claims:
        if deps.claim_has_foreshadow_after_reveal(claim):
            pair_key = (claim.claim_id, "foreshadow_after_reveal")
            if len(candidates) < max_candidates and pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                candidates.append(
                    ConflictCandidate(
                        candidate_id=f"cand_{len(candidates) + 1:04d}",
                        candidate_type="foreshadow_after_reveal",
                        reason="伏笔章节晚于正式公开章节，需校验时间锚点是否写反。",
                        claim_ids=[claim.claim_id],
                        claims=[claim],
                        retrieval_sources=["exact:foreshadow_after_reveal"],
                        severity_hint="medium",
                        confidence=claim.confidence,
                        chapter_span=deps.chapter_span_for_claims([claim]),
                    )
                )
        for group_key in deps.cognitive_group_keys(claim):
            cognitive_groups.setdefault(group_key, []).append(claim)
    for _group_key, group in cognitive_groups.items():
        ordered = sorted(group, key=deps.claim_sort_key)
        for idx, claim_a in enumerate(ordered):
            for claim_b in ordered[idx + 1 : idx + 8]:
                if deps.same_artifact_path(claim_a, claim_b):
                    continue
                for candidate_type, reason, severity_hint in deps.cognitive_candidate_kinds(
                    claim_a,
                    claim_b,
                ):
                    add_pair(
                        claim_a,
                        claim_b,
                        candidate_type=candidate_type,
                        reason=reason,
                        retrieval_source=f"exact:{candidate_type}",
                        severity_hint=severity_hint,
                    )

    payoff_groups: dict[str, list[CoherenceClaim]] = {}
    for claim in claims:
        payoff_key = deps.normalize_key(claim.payoff_id or claim.payoff_kind)
        if payoff_key:
            payoff_groups.setdefault(payoff_key, []).append(claim)
    for _payoff_key, group in payoff_groups.items():
        ordered = sorted(group, key=deps.claim_sort_key)
        for idx, claim_a in enumerate(ordered):
            for claim_b in ordered[idx + 1 : idx + 4]:
                add_pair(
                    claim_a,
                    claim_b,
                    candidate_type="same_payoff",
                    reason="同一 payoff 标识或兑现类型重复出现，需判断是否重复消费。",
                    retrieval_source="exact:payoff",
                    severity_hint="high",
                )

    irreversible_groups: dict[tuple[str, str], list[CoherenceClaim]] = {}
    for claim in claims:
        if not claim.irreversible:
            continue
        key = (deps.claim_subject_key(claim), deps.normalize_key(claim.event_type or claim.axis))
        if key[0] and key[1]:
            irreversible_groups.setdefault(key, []).append(claim)
    for _key, group in irreversible_groups.items():
        ordered = sorted(group, key=deps.claim_sort_key)
        for idx, claim_a in enumerate(ordered):
            for claim_b in ordered[idx + 1 : idx + 4]:
                add_pair(
                    claim_a,
                    claim_b,
                    candidate_type="irreversible_repeat",
                    reason="同一主体的不可逆事件多次出现，需判断是否被重复当作首次完成。",
                    retrieval_source="exact:irreversible",
                    severity_hint="high",
                )

    return candidates


def structural_pair_keys(claims: list[CoherenceClaim]) -> set[tuple[str, str]]:
    """Return every claim pair required by structural chapter coverage."""

    ordered = sorted(claims, key=lambda claim: claim.claim_id)
    return {
        (min(claim_a.claim_id, claim_b.claim_id), max(claim_a.claim_id, claim_b.claim_id))
        for index, claim_a in enumerate(ordered)
        for claim_b in ordered[index + 1 :]
        if _claims_share_structural_scope(claim_a, claim_b)
        and claim_a.claim_id != claim_b.claim_id
    }


def _claim_scope(claim: CoherenceClaim) -> tuple[int, int] | None:
    if claim.chapter_range and claim.chapter_range.start and claim.chapter_range.end:
        return claim.chapter_range.start, claim.chapter_range.end
    if claim.chapter_numbers:
        return min(claim.chapter_numbers), max(claim.chapter_numbers)
    return None


def _claims_share_structural_scope(
    claim_a: CoherenceClaim,
    claim_b: CoherenceClaim,
) -> bool:
    scope_a = _claim_scope(claim_a)
    scope_b = _claim_scope(claim_b)
    if scope_a is None or scope_b is None:
        return True
    return not (scope_a[1] < scope_b[0] or scope_b[1] < scope_a[0])
