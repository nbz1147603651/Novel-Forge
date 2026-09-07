"""Cognitive conflict helpers for init coherence v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from novel_forge.core.schemas.init_coherence import CoherenceClaim


@dataclass(frozen=True)
class CognitiveFilterDeps:
    """Callbacks and constants owned by ``init_coherence_v2``."""

    claim_start_chapter: Callable[[CoherenceClaim], int]
    claim_subject_key: Callable[[CoherenceClaim], str]
    cognitive_level_order: tuple[str, ...]
    normalize_key: Callable[[Any], str]


def semantic_candidate_passes_cognitive_post_filter(
    left: CoherenceClaim,
    right: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> bool:
    if left.subject_ids or right.subject_ids or left.cognitive_subjects or right.cognitive_subjects:
        overlap = cognitive_subject_overlap(left, right)
        if not overlap:
            return False

    if cognitive_candidate_kinds(left, right, deps=deps):
        return True

    if left.claim_type != right.claim_type:
        return False

    if left.action_level == right.action_level == "none":
        return True
    return True


def cognitive_subject_overlap(left: CoherenceClaim, right: CoherenceClaim) -> bool:
    left_subjects = {
        subject for subject in (left.cognitive_subjects or left.subject_ids) if subject
    }
    right_subjects = {
        subject for subject in (right.cognitive_subjects or right.subject_ids) if subject
    }
    return bool(left_subjects & right_subjects)


def claims_have_cognitive_regression(
    a: CoherenceClaim,
    b: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> bool:
    if not cognitive_subject_overlap(a, b):
        return False
    if claim_cognitive_object_key(a, deps=deps) != claim_cognitive_object_key(b, deps=deps):
        return False
    a_chapter = claim_effective_cognitive_chapter(a, deps=deps)
    b_chapter = claim_effective_cognitive_chapter(b, deps=deps)
    if not (a_chapter and b_chapter):
        return False
    if abs(a_chapter - b_chapter) > 12:
        return False

    if a_chapter <= b_chapter:
        first = a
        second = b
    else:
        first = b
        second = a

    first_level = (
        deps.cognitive_level_order.index(first.cognitive_level)
        if first.cognitive_level in deps.cognitive_level_order
        else 0
    )
    second_level = (
        deps.cognitive_level_order.index(second.cognitive_level)
        if second.cognitive_level in deps.cognitive_level_order
        else 0
    )
    return first_level > second_level


def cognitive_group_keys(
    claim: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> list[tuple[str, str]]:
    object_key = claim_cognitive_object_key(claim, deps=deps)
    if not object_key:
        return []
    subjects = [
        deps.normalize_key(subject)
        for subject in (claim.cognitive_subjects or claim.subject_ids)
        if deps.normalize_key(subject)
    ]
    if not subjects:
        subjects = [deps.claim_subject_key(claim)]
    result: list[tuple[str, str]] = []
    for subject in subjects:
        if subject:
            result.append((subject, object_key))
    return list(dict.fromkeys(result))


def claim_cognitive_object_key(
    claim: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> str:
    return deps.normalize_key(
        claim.cognitive_object
        or claim.axis
        or claim.event_type
        or claim.payoff_id
        or claim.payoff_kind
        or claim.claim_text[:80]
    )


def cognitive_candidate_kinds(
    a: CoherenceClaim,
    b: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> list[tuple[str, str, str]]:
    if not cognitive_subject_overlap(a, b):
        return []
    if claim_cognitive_object_key(a, deps=deps) != claim_cognitive_object_key(b, deps=deps):
        return []

    kinds: list[tuple[str, str, str]] = []
    if claims_have_cognitive_regression(a, b, deps=deps):
        kinds.append(
            (
                "cognitive_regression",
                "同一认知主体/对象的认知层级出现倒退，需校验是否为误判、回忆误导或真实矛盾。",
                "high",
            )
        )
    if claims_have_premature_public_reveal(a, b, deps=deps):
        kinds.append(
            (
                "premature_public_reveal",
                "某认知在计划公开章节前被公开说破或正式承认，需校验是否提前揭露。",
                "high",
            )
        )
    if claims_have_awareness_conflict(a, b, deps=deps):
        kinds.append(
            (
                "awareness_conflict",
                "同一章节/认知对象的信息差记录互相冲突，需校验读者或角色知情状态。",
                "medium",
            )
        )
    if claim_has_foreshadow_after_reveal(a) or claim_has_foreshadow_after_reveal(b):
        kinds.append(
            (
                "foreshadow_after_reveal",
                "伏笔章节晚于正式公开章节，需校验时间锚点是否写反。",
                "medium",
            )
        )
    if claims_have_action_level_conflict(a, b, deps=deps):
        kinds.append(
            (
                "action_level_conflict",
                "同一认知对象同时要求内心知晓和公开揭露，需校验行动层边界。",
                "high",
            )
        )
    return kinds


def claims_have_premature_public_reveal(
    a: CoherenceClaim,
    b: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> bool:
    for planned, other in ((a, b), (b, a)):
        public_chapter = planned.public_reveal_chapter
        if not public_chapter:
            continue
        other_start = claim_effective_cognitive_chapter(other, deps=deps)
        if not other_start or other_start >= public_chapter:
            continue
        if other.action_level == "revealed" or other.cognitive_level == "acknowledged":
            return True
    return False


def claims_have_awareness_conflict(
    a: CoherenceClaim,
    b: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> bool:
    a_chapter = claim_effective_cognitive_chapter(a, deps=deps)
    b_chapter = claim_effective_cognitive_chapter(b, deps=deps)
    if not a_chapter or a_chapter != b_chapter:
        return False
    if (
        a.reader_awareness != "unknown"
        and b.reader_awareness != "unknown"
        and a.reader_awareness != b.reader_awareness
    ):
        return True
    shared_characters = set(a.character_knowledge_coverage) & set(b.character_knowledge_coverage)
    for character in shared_characters:
        left = a.character_knowledge_coverage.get(character, "unknown")
        right = b.character_knowledge_coverage.get(character, "unknown")
        if left != "unknown" and right != "unknown" and left != right:
            return True
    return False


def claim_has_foreshadow_after_reveal(claim: CoherenceClaim) -> bool:
    public_chapter = claim.public_reveal_chapter
    return bool(
        public_chapter and any(chapter > public_chapter for chapter in claim.foreshadow_chapters)
    )


def claims_have_action_level_conflict(
    a: CoherenceClaim,
    b: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> bool:
    internal, public = internal_and_public_claim(a, b)
    if internal is None or public is None:
        return False
    public_chapter = claim_effective_cognitive_chapter(public, deps=deps)
    if not public_chapter:
        return True
    allowed_public_chapter = internal.public_reveal_chapter
    if allowed_public_chapter and public_chapter < allowed_public_chapter:
        return True
    internal_chapter = claim_effective_cognitive_chapter(internal, deps=deps)
    return bool(internal_chapter and public_chapter == internal_chapter)


def internal_and_public_claim(
    a: CoherenceClaim,
    b: CoherenceClaim,
) -> tuple[CoherenceClaim | None, CoherenceClaim | None]:
    if a.action_level == "internal" and b.action_level == "revealed":
        return a, b
    if b.action_level == "internal" and a.action_level == "revealed":
        return b, a
    if a.action_level == "internal" and b.cognitive_level == "acknowledged":
        return a, b
    if b.action_level == "internal" and a.cognitive_level == "acknowledged":
        return b, a
    return None, None


def claim_effective_cognitive_chapter(
    claim: CoherenceClaim,
    *,
    deps: CognitiveFilterDeps,
) -> int:
    return claim.cognitive_chapter or deps.claim_start_chapter(claim)
