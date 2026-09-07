"""Anchor-term extraction for continuity / forbidden-element filtering."""

from __future__ import annotations

import re
from typing import Any


def _extract_anchor_terms_from_bible(bible_data: Any, canon_data: Any) -> list[str]:
    """Extract anchor terms that should not be treated as forbidden elements.

    Sources:
    - bible_data.characters[].relationships.keys() → relationship types
    - canon_data.characters[].social_status → titles / ranks
    - canon_data.relationships[].public_status → key address terms
      (simple tokenisation, conservative: keep chunks ≥2 chars)

    Empty bible/canon returns an empty list; no exceptions are raised.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: Any) -> None:
        text = str(term).strip() if term is not None else ""
        if text and text not in seen:
            seen.add(text)
            terms.append(text)

    if bible_data is not None:
        characters: Any = []
        if hasattr(bible_data, "characters"):
            characters = bible_data.characters
        elif isinstance(bible_data, dict):
            characters = bible_data.get("characters", [])

        for char in characters or []:
            if char is None:
                continue
            rels: Any = {}
            if hasattr(char, "relationships"):
                rels = char.relationships
            elif isinstance(char, dict):
                rels = char.get("relationships", {})
            if isinstance(rels, dict):
                # Extract relationship *descriptions* (values) rather than
                # character names (keys) so terms like "师父" actually surface.
                for desc in rels.values():
                    _add(desc)

    if canon_data is not None:
        canon_chars: Any = {}
        if hasattr(canon_data, "characters"):
            canon_chars = canon_data.characters
        elif isinstance(canon_data, dict):
            canon_chars = canon_data.get("characters", {})

        _iter_character_states(canon_chars, _add)

        canon_rels: Any = {}
        if hasattr(canon_data, "relationships"):
            canon_rels = canon_data.relationships
        elif isinstance(canon_data, dict):
            canon_rels = canon_data.get("relationships", {})
            if not canon_rels:
                canon_rels = canon_data.get("active_relationships", [])

        if isinstance(canon_rels, dict):
            for rel in canon_rels.values():
                _extract_public_status_terms(rel, _add)
        elif isinstance(canon_rels, list):
            for rel in canon_rels:
                _extract_public_status_terms(rel, _add)

    return terms


def _iter_character_states(characters: Any, add_fn: Any) -> None:
    if isinstance(characters, dict):
        for state in characters.values():
            _extract_social_status(state, add_fn)
    elif isinstance(characters, list):
        for state in characters:
            _extract_social_status(state, add_fn)


def _extract_social_status(state: Any, add_fn: Any) -> None:
    if state is None:
        return
    status = ""
    if hasattr(state, "social_status"):
        status = state.social_status
    elif isinstance(state, dict):
        status = state.get("social_status", "")
    add_fn(status)


def _extract_public_status_terms(rel: Any, add_fn: Any) -> None:
    if rel is None:
        return
    public_status = ""
    if hasattr(rel, "public_status"):
        public_status = rel.public_status
    elif isinstance(rel, dict):
        public_status = rel.get("public_status", "")

    public_status = str(public_status).strip()
    if not public_status:
        return

    for part in re.split(r"[，。！？；：、\s]+", public_status):
        part = part.strip()
        if len(part) >= 2:
            add_fn(part)
