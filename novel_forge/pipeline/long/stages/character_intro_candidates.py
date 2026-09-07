"""Character-introduction candidate collection helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.schemas.outline import NarrativeBlueprint

if TYPE_CHECKING:
    from novel_forge.pipeline.long.preflight import LongProjectBundle


_CHARACTER_NAME_FIELDS = frozenset(
    {
        "character",
        "characters",
        "character_name",
        "character_names",
        "characters_involved",
        "required_characters",
        "source_character",
        "target_character",
        "pov_character",
        "involved_characters",
    }
)
_OPERATION_CHARACTER_NAME_FIELDS = _CHARACTER_NAME_FIELDS | frozenset(
    {
        "actor",
        "from",
        "to",
        "subject",
        "object",
        "owner",
        "owners",
        "source_char",
        "target_char",
        "source_name",
        "target_name",
    }
)
_RELATIONSHIP_OPERATION_CHARACTER_NAME_FIELDS = _OPERATION_CHARACTER_NAME_FIELDS
_STATE_OPERATION_CHARACTER_NAME_FIELDS = _CHARACTER_NAME_FIELDS | frozenset(
    {
        "actor",
        "owner",
        "owners",
        "source_char",
        "target_char",
        "source_character",
        "target_character",
    }
)
_KNOWLEDGE_OPERATION_CHARACTER_NAME_FIELDS = _STATE_OPERATION_CHARACTER_NAME_FIELDS
_RELATIONSHIP_OPERATION_FIELDS = frozenset({"relationship_ops"})
_STATE_OPERATION_FIELDS = frozenset(
    {
        "character_ops",
        "character_state_ops",
        "character_updates",
        "state_ops",
    }
)
_KNOWLEDGE_OPERATION_FIELDS = frozenset({"knowledge_ops", "promise_ops"})
_NEW_CHARACTER_CANDIDATE_FIELDS = frozenset(
    {
        "new_character_candidates",
        "candidate_new_characters",
        "possible_new_characters",
    }
)
_NEW_CHARACTER_CANDIDATE_NAME_FIELDS = frozenset(
    {
        "name",
        "candidate_name",
        "canonical_name",
        "character",
        "character_name",
    }
)
_ENTITY_ID_RE = re.compile(r"^(?:char|loc|item|org|event|thread)_[A-Za-z0-9_]+$")
_COMPOUND_NAME_SEPARATOR_RE = re.compile(r"(?:__|[、,，/／&＋+]|与|和)")
_FUNCTIONAL_LABEL_TERMS = frozenset(
    {
        "黄门侍郎",
        "中书侍郎",
        "门下侍郎",
        "侍郎",
        "尚书",
        "御史",
        "御史中丞",
        "太守",
        "刺史",
        "县令",
        "县丞",
        "主簿",
        "都尉",
        "校尉",
        "司马",
        "郎中",
        "中郎将",
        "太监",
        "宦官",
        "内侍",
        "宫女",
        "侍卫",
        "护卫",
        "守卫",
        "官员",
        "大臣",
        "使者",
        "信使",
        "衙役",
        "捕快",
        "狱卒",
        "店员",
        "伙计",
        "掌柜",
        "老板",
        "门房",
        "仆人",
        "仆役",
        "侍女",
        "丫鬟",
        "车夫",
        "船夫",
        "士兵",
        "军士",
        "哨兵",
        "护院",
    }
)
_FUNCTIONAL_LABEL_PREFIXES = (
    "某",
    "路人",
    "无名",
    "一名",
    "一位",
    "那名",
    "那位",
    "这名",
    "这位",
    "年轻",
    "年老",
    "街边",
    "门口",
    "店里",
    "茶馆",
    "客栈",
    "酒楼",
    "咖啡店",
    "衙门",
    "宫中",
    "宫门",
    "府中",
    "牢中",
    "狱中",
    "蜀国",
    "魏国",
    "水运",
    "商行",
)


def _looks_like_character_name(value: str) -> bool:
    text = str(value or "").strip().strip("“”\"'「」《》()（）[]【】")
    if not text:
        return False
    if is_system_artifact_name(text):
        return False
    if _ENTITY_ID_RE.match(text):
        return False
    if len(text) > 24:
        return False
    if re.search(r"[。！？!?；;：:\n\r]", text):
        return False
    return True


def _is_functional_character_label(value: Any) -> bool:
    """Return True for pure title/occupation labels that should not become profiles."""
    text = clean_character_name(value).strip("“”\"'「」《》()（）[]【】")
    if not text:
        return False
    if text in _FUNCTIONAL_LABEL_TERMS:
        return True
    for term in _FUNCTIONAL_LABEL_TERMS:
        if not text.endswith(term):
            continue
        prefix = text[: -len(term)]
        if prefix in _FUNCTIONAL_LABEL_PREFIXES:
            return True
    return False


def _add_candidate_signal(
    signals: dict[str, set[str]],
    name: Any,
    source: str,
) -> None:
    cleaned = clean_character_name(name)
    if cleaned and _looks_like_character_name(cleaned):
        signals.setdefault(cleaned, set()).add(source)


def _add_candidate_signals(
    signals: dict[str, set[str]],
    names: set[str],
    source: str,
) -> None:
    for name in names:
        _add_candidate_signal(signals, name, source)


def _select_auto_intro_candidates(signals: dict[str, set[str]]) -> set[str]:
    """Return all structurally valid candidates.

    Semantic decisions are intentionally delegated to
    ``ADJUDICATE_CHARACTER_INTRODUCTION``.  The local layer only preserves
    source signals so the model can judge aliases, labels, and importance.
    """
    return set(signals)


def _split_compound_character_names(value: str) -> list[str]:
    text = str(value or "").strip()
    if len(text) > 24 or not _COMPOUND_NAME_SEPARATOR_RE.search(text):
        return []
    if re.search(r"[。！？!?；;：:\n\r]", text):
        return []
    parts = [part.strip() for part in _COMPOUND_NAME_SEPARATOR_RE.split(text) if part.strip()]
    if len(parts) < 2:
        return []
    if any(len(part) > 8 for part in parts):
        return []
    return [part for part in parts if _looks_like_character_name(part)]


def _add_name(candidates: set[str], value: Any) -> None:
    if isinstance(value, str):
        name = value.strip()
        parts = _split_compound_character_names(name)
        if parts:
            candidates.update(parts)
        elif _looks_like_character_name(name):
            candidates.add(name)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _add_name(candidates, item)


def _collect_character_operation_names(
    source: Any,
    *,
    accepted_name_fields: frozenset[str],
    tuple_mode: str,
) -> set[str]:
    """Collect names from explicit character/relationship operation payloads.

    Chapter contracts have existed in a few shapes over time: modern dict
    payloads (``subject``/``object`` or ``from``/``to``), plus older tuple-like
    rows such as ``["char_a", "char_b", "描述", "甲与乙"]``.  This helper is
    intentionally narrower than free-form prose parsing: it only looks inside
    operation containers and only accepts compact name-like labels.
    """
    candidates: set[str] = set()
    if source is None:
        return candidates
    if hasattr(source, "model_dump"):
        source = source.model_dump(mode="json")
    if isinstance(source, dict):
        for key, value in source.items():
            if key in accepted_name_fields:
                _add_name(candidates, value)
            elif isinstance(value, (dict, list, tuple)):
                candidates.update(
                    _collect_character_operation_names(
                        value,
                        accepted_name_fields=accepted_name_fields,
                        tuple_mode=tuple_mode,
                    )
                )
        return candidates
    if isinstance(source, (list, tuple, set)):
        for item in source:
            if isinstance(item, dict):
                candidates.update(
                    _collect_character_operation_names(
                        item,
                        accepted_name_fields=accepted_name_fields,
                        tuple_mode=tuple_mode,
                    )
                )
            elif isinstance(item, (list, tuple, set)):
                for index, part in enumerate(item):
                    if isinstance(part, str):
                        if tuple_mode == "relationship" and (
                            index < 2 or _split_compound_character_names(part)
                        ):
                            _add_name(candidates, part)
                        elif tuple_mode == "actor_tail" and index >= 2:
                            _add_name(candidates, part)
                    elif isinstance(part, dict):
                        candidates.update(
                            _collect_character_operation_names(
                                part,
                                accepted_name_fields=accepted_name_fields,
                                tuple_mode=tuple_mode,
                            )
                        )
            elif isinstance(item, str):
                if tuple_mode == "relationship" and _split_compound_character_names(item):
                    _add_name(candidates, item)
    return candidates


def _collect_structured_character_names(source: Any) -> set[str]:
    """Collect character names only from explicit character-name fields.

    The local layer deliberately avoids parsing free-form event prose.  It only
    routes names that upstream artifacts already exposed as structured fields.
    """
    candidates: set[str] = set()
    if source is None:
        return candidates
    if hasattr(source, "model_dump"):
        source = source.model_dump(mode="json")
    if isinstance(source, dict):
        for key, value in source.items():
            if key in _CHARACTER_NAME_FIELDS:
                _add_name(candidates, value)
            elif key in _NEW_CHARACTER_CANDIDATE_FIELDS:
                candidates.update(_collect_new_character_candidate_names(value))
            elif key in _RELATIONSHIP_OPERATION_FIELDS:
                candidates.update(
                    _collect_character_operation_names(
                        value,
                        accepted_name_fields=_RELATIONSHIP_OPERATION_CHARACTER_NAME_FIELDS,
                        tuple_mode="relationship",
                    )
                )
            elif key in _STATE_OPERATION_FIELDS:
                candidates.update(
                    _collect_character_operation_names(
                        value,
                        accepted_name_fields=_STATE_OPERATION_CHARACTER_NAME_FIELDS,
                        tuple_mode="actor_tail",
                    )
                )
            elif key in _KNOWLEDGE_OPERATION_FIELDS:
                candidates.update(
                    _collect_character_operation_names(
                        value,
                        accepted_name_fields=_KNOWLEDGE_OPERATION_CHARACTER_NAME_FIELDS,
                        tuple_mode="actor_tail",
                    )
                )
            elif isinstance(value, (dict, list, tuple)):
                candidates.update(_collect_structured_character_names(value))
    elif isinstance(source, (list, tuple, set)):
        for item in source:
            candidates.update(_collect_structured_character_names(item))
    return candidates


def _collect_new_character_candidate_names(source: Any) -> set[str]:
    candidates: set[str] = set()
    if source is None:
        return candidates
    if hasattr(source, "model_dump"):
        source = source.model_dump(mode="json")
    if isinstance(source, dict):
        for key, value in source.items():
            if key in _NEW_CHARACTER_CANDIDATE_NAME_FIELDS:
                _add_name(candidates, value)
            elif isinstance(value, (dict, list, tuple)):
                candidates.update(_collect_new_character_candidate_names(value))
    elif isinstance(source, (list, tuple, set)):
        for item in source:
            candidates.update(_collect_new_character_candidate_names(item))
    elif isinstance(source, str):
        _add_name(candidates, source)
    return candidates


def _collect_chapter_character_candidate_signals(
    bundle: "LongProjectBundle",
    blueprint: NarrativeBlueprint | None,
    chapter_number: int,
    chapter_contract: dict[str, Any] | None = None,
) -> dict[str, set[str]]:
    """Collect structurally named character candidates and their source signals.

    This function does not decide whether a candidate should become a profile.
    It only surfaces structured names from upstream artifacts; the LLM
    adjudication step decides whether they are new, important, aliases, or
    temporary labels.
    """
    signals: dict[str, set[str]] = {}
    weak_outline_names: set[str] = set()

    # 1. POV character from chapter outline is always plot-relevant enough.
    pov = (bundle.chapter_outline.pov_character or "").strip()
    if pov:
        _add_candidate_signal(signals, pov, "pov")

    # 2. Outline involved/required character fields are weak signals only.
    weak_outline_names.update(_collect_structured_character_names(bundle.chapter_outline))

    # 3. Explicit chapter-contract relationship/character operation fields are
    # strong signals because they describe required plot-state movement.
    _add_candidate_signals(
        signals,
        _collect_structured_character_names(chapter_contract or {}),
        "chapter_contract",
    )

    if blueprint is None:
        _add_candidate_signals(signals, weak_outline_names, "outline")
        return signals

    # 4. Key characters in narrative phases covering this chapter.
    for phase in blueprint.narrative_phases:
        if phase.chapter_start <= chapter_number <= phase.chapter_end:
            for name in phase.key_characters:
                _add_candidate_signal(signals, name, "phase")

    # 5. Characters involved in turning points at this chapter
    for tp in blueprint.key_turning_points:
        if tp.chapter_number == chapter_number:
            for name in tp.characters_involved:
                _add_candidate_signal(signals, name, "turning_point")

    # 6. Character arc holders with a milestone active in this chapter.  This is
    # a planning corroboration signal, not enough by itself to create a profile.
    for arc in blueprint.character_arcs:
        name = (arc.character or "").strip()
        if not name:
            continue
        for milestone in arc.milestones:
            if milestone.chapter_start <= chapter_number <= milestone.chapter_end:
                _add_candidate_signal(signals, name, "arc")
                break

    _add_candidate_signals(signals, weak_outline_names, "outline")
    return signals


def _collect_chapter_character_candidates(
    bundle: "LongProjectBundle",
    blueprint: NarrativeBlueprint | None,
    chapter_number: int,
    chapter_contract: dict[str, Any] | None = None,
) -> set[str]:
    """Return candidate names before LLM adjudication.

    Kept for tests and diagnostics; production selection uses the richer signal
    payload from ``_collect_chapter_character_candidate_signals``.
    """
    return _select_auto_intro_candidates(
        _collect_chapter_character_candidate_signals(
            bundle,
            blueprint,
            chapter_number,
            chapter_contract=chapter_contract,
        )
    )
