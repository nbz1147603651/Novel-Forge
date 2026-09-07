"""Character identity helpers shared by pipeline and UI code."""

from __future__ import annotations

import re
from collections.abc import Iterable
from hashlib import sha1
from typing import Any

_BRACKETED_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)(?P<suffix>(?:[（(【\[][^）)\]】]{1,24}[）)\]】]){1,3})$"
)
_ROLE_SPLIT_RE = re.compile(r"[+＋/／,，;；|｜]")


def clean_character_name(value: Any) -> str:
    """Return a compact display-safe character name."""
    text = str(value or "").strip().strip("“”\"'「」《》")
    return re.sub(r"\s+", " ", text).strip()


def stable_character_id(name: Any, *, prefix: str = "char") -> str:
    """Return a deterministic storage id for a character name."""
    cleaned = clean_character_name(name)
    raw = cleaned or str(name or "")
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def parenthetical_base_name(value: Any) -> str | None:
    """Return ``周芷若`` for stage labels such as ``周芷若（狱中）``."""
    text = clean_character_name(value)
    if not text:
        return None
    match = _BRACKETED_SUFFIX_RE.match(text)
    if not match:
        return None
    base = clean_character_name(match.group("base"))
    return base if base and base != text else None


def resolve_existing_character_name(
    name: Any,
    existing_names: Iterable[str],
) -> str | None:
    """Resolve a possibly-qualified name to an already-known canonical name."""
    lookup = {clean_character_name(existing): existing for existing in existing_names}
    cleaned = clean_character_name(name)
    if not cleaned:
        return None
    if cleaned in lookup:
        return lookup[cleaned]
    base = parenthetical_base_name(cleaned)
    if base and base in lookup:
        return lookup[base]
    return None


def dedupe_candidate_character_names(
    candidates: Iterable[Any],
    *,
    existing_names: Iterable[str],
) -> list[str]:
    """Return new candidate names after filtering existing/stage-alias duplicates."""
    existing = [clean_character_name(name) for name in existing_names if clean_character_name(name)]
    candidate_names = sorted(
        {
            clean_character_name(name)
            for name in candidates
            if clean_character_name(name)
        }
    )
    seen: set[str] = set()
    new_names: list[str] = []

    for name in candidate_names:
        if resolve_existing_character_name(name, existing):
            continue
        base = parenthetical_base_name(name)
        normalized = base or name
        if normalized in seen or resolve_existing_character_name(normalized, existing):
            continue
        seen.add(normalized)
        new_names.append(normalized)
    return new_names


def normalize_character_role(value: Any, *, default: str = "supporting") -> str:
    """Collapse model-written role prose into the canonical role buckets."""
    text = str(value or "").strip()
    if not text:
        return default
    normalized = text.lower().replace("-", "_")
    first_token = _ROLE_SPLIT_RE.split(normalized, maxsplit=1)[0].strip()
    for candidate in (normalized, first_token):
        if candidate in {"protagonist", "lead", "主角", "女主", "男主"}:
            return "protagonist"
        if candidate in {"deuteragonist", "co_protagonist", "second_lead", "双主角"}:
            return "deuteragonist"
        if candidate in {"antagonist", "villain", "反派", "敌手"}:
            return "antagonist"
        if candidate in {"supporting", "support", "major", "主要角色", "配角"}:
            return "supporting"
        if candidate in {
            "minor",
            "cameo",
            "mentioned",
            "reference",
            "background",
            "小角色",
            "临时角色",
            "提及",
            "仅提及",
            "背景人物",
        }:
            return "minor"

    if any(token in normalized for token in ("反派", "宿敌", "villain", "antagonist")):
        return "antagonist"
    if any(
        token in normalized
        for token in (
            "minor",
            "mentioned",
            "reference",
            "background",
            "小角色",
            "路人",
            "临时",
            "提及",
            "背景人物",
        )
    ):
        return "minor"
    if any(token in normalized for token in ("deuteragonist", "second_lead", "双主角")):
        return "deuteragonist"
    if any(token in normalized for token in ("protagonist", "主角", "女主", "男主")):
        return "protagonist"
    if any(token in normalized for token in ("supporting", "support", "major", "配角", "主要")):
        return "supporting"
    return default
