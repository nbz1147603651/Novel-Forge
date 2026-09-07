"""Helpers for turning POV hints into explicit narrative-person rules."""

from __future__ import annotations

from typing import Any

_FIRST_PERSON_MARKERS = (
    "第一人称",
    "一人称",
    "第一视角",
    "我视角",
    "自述",
    "自传式",
)
_FIRST_PERSON_BLOCKERS = (
    "不采用第一人称",
    "不用第一人称",
    "禁止第一人称",
    "禁用第一人称",
    "不得使用第一人称",
    "不得采用第一人称",
    "非第一人称",
)
_THIRD_PERSON_DEFAULT_RULE = (
    "默认叙事人称：第三人称限知。非对话正文禁止使用“我/我们/咱们”作为叙述主体；"
    "角色内心必须写成“角色名/他/她觉得、意识到、想”，不得写成第一人称自述。"
)
# When a pov_hint exceeds this many characters, only the first segment
# (person declaration + POV allocation summary) is kept. Detailed character
# interaction rules belong in character_bible/outline, not in the per-chapter
# narration rule.
_POV_HINT_DISTILL_LIMIT = 200


def clean_pov_hint(value: Any) -> str:
    """Normalize a free-text POV hint for prompt injection."""
    return str(value or "").strip()


def pov_allows_first_person(pov_hint: Any) -> bool:
    """Return whether the hint explicitly permits first-person narration."""
    text = clean_pov_hint(pov_hint)
    if not text:
        return False
    if any(marker in text for marker in _FIRST_PERSON_BLOCKERS):
        return False
    return any(marker in text for marker in _FIRST_PERSON_MARKERS)


def _distill_pov_hint(hint: str) -> str:
    """Compress a long POV hint to its core person + allocation rules.

    Detailed character interaction notes (e.g. "前期清漪只听闻玄昱有个不爱
    露面的小书童…") belong in character_bible/outline and should not be
    re-injected into every chapter's narration rule. This helper keeps the
    first paragraph (person declaration + POV allocation) and drops the rest
    when the hint exceeds ``_POV_HINT_DISTILL_LIMIT``.
    """
    if len(hint) <= _POV_HINT_DISTILL_LIMIT:
        return hint
    # Keep up to the first sentence-ending punctuation after the limit,
    # or the first paragraph break — whichever comes first.
    first_paragraph = hint.split("\n")[0]
    if len(first_paragraph) <= _POV_HINT_DISTILL_LIMIT:
        return first_paragraph
    # Hard truncation at the last sentence-ending punctuation within limit.
    truncated = hint[:_POV_HINT_DISTILL_LIMIT]
    for sep in ("。", "；", "！", "？"):
        idx = truncated.rfind(sep)
        if idx > _POV_HINT_DISTILL_LIMIT // 2:
            return truncated[: idx + 1]
    return truncated


def build_narrative_person_rule(pov_hint: Any) -> str:
    """Build an explicit grammar-person rule from the user/init POV hint.

    The system defaults ambiguous POV hints to third-person limited narration.
    Users can still opt into first person by saying so explicitly.
    """
    hint = clean_pov_hint(pov_hint)
    if "叙事人称硬约束" in hint:
        return hint
    if pov_allows_first_person(hint):
        distilled = _distill_pov_hint(hint)
        return (
            f"{distilled}\n"
            "叙事人称硬约束：采用第一人称时，只能由当前 POV 角色自述；"
            "视角切换后必须更换叙述主体，不得混入非 POV 角色内心。"
        )
    if not hint:
        return _THIRD_PERSON_DEFAULT_RULE
    distilled = _distill_pov_hint(hint)
    return (
        f"{distilled}\n"
        "叙事人称硬约束：采用第三人称叙述；非对话正文禁止使用“我/我们/咱们”作为叙述主体；"
        "需要呈现内心时写成“角色名/他/她觉得、意识到、想”，不得改写成第一人称自述。"
    )


def build_narrative_person_context(pov_hint: Any) -> dict[str, Any]:
    """Return prompt context fields shared by planning/drafting/editing."""
    hint = clean_pov_hint(pov_hint)
    return {
        "pov_hint": hint,
        "narrative_person_rule": build_narrative_person_rule(hint),
        "narrative_person_allows_first_person": pov_allows_first_person(hint),
    }
