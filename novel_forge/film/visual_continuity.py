"""Visual continuity bridges from narrative state and motif memory into the film bible.

P1-5: accepted StateDeltas (character_state / item / relationship) become a
cross-volume visual state timeline injected into each character's
``ScreenIdentityLock.visual_state_timeline`` so shot prompts can carry
appearance/state continuity (伤疤、服装变更、道具易手) across chapters.

P1-6: recurring motifs become visual symbol anchors on ``FilmStyleLock``
(``visual_motifs``) so asset boards and shots keep the recurring imagery
consistent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout

from .schemas import ProductionBible

_VISUAL_DELTA_TYPES = {"character_state", "item", "relationship", "event"}
_TIMELINE_PER_CHARACTER_CAP = 8
_VISUAL_MOTIF_CAP = 12
_VISUAL_MOTIF_MIN_OCCURRENCES = 2


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeError):
                continue
            if isinstance(value, dict):
                entries.append(value)
    except OSError:
        return []
    return entries


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_accepted_ledger_entries(narrative_dir: Path) -> list[dict[str, Any]]:
    """Return accepted, still-active state ledger entries ordered by chapter."""
    entries = _load_jsonl(narrative_dir / "state_ledger.jsonl")
    accepted: list[dict[str, Any]] = []
    for entry in entries:
        decision = entry.get("decision")
        verdict = decision.get("verdict") if isinstance(decision, dict) else ""
        if verdict != "accept":
            continue
        if str(entry.get("evidence_status") or "active") != "active":
            continue
        if str(entry.get("delta_type") or "") not in _VISUAL_DELTA_TYPES:
            continue
        accepted.append(entry)
    accepted.sort(key=lambda item: int(item.get("chapter_number") or 0))
    return accepted


def build_visual_state_timeline(
    entries: list[dict[str, Any]],
    character_names: list[str],
) -> dict[str, list[str]]:
    """Map accepted deltas to per-character visual continuity lines.

    A delta is attributed to every character whose name appears in the summary
    or state_update payload; item/relationship deltas that mention multiple
    characters are attached to all of them.
    """
    timeline: dict[str, list[str]] = {name: [] for name in character_names}
    for entry in entries:
        summary = str(entry.get("summary") or "").strip()
        if not summary:
            continue
        chapter = int(entry.get("chapter_number") or 0)
        state_update = entry.get("state_update")
        payload_text = summary + json.dumps(state_update, ensure_ascii=False) if isinstance(
            state_update, dict
        ) else summary
        matched = [name for name in character_names if name and name in payload_text]
        line = f"第{chapter}章 · {summary}" if chapter else summary
        for name in matched:
            bucket = timeline[name]
            if line not in bucket:
                bucket.append(line)
    return {
        name: lines[-_TIMELINE_PER_CHARACTER_CAP:]
        for name, lines in timeline.items()
        if lines
    }


def extract_visual_motifs(motif_state: dict[str, Any]) -> list[str]:
    """Turn recurring motifs into visual symbol anchor strings.

    Only non-retired motifs with at least ``_VISUAL_MOTIF_MIN_OCCURRENCES``
    occurrences are kept, ordered by occurrence count (most recurring first).
    """
    raw_motifs = motif_state.get("motifs")
    if not isinstance(raw_motifs, dict):
        return []
    candidates: list[tuple[int, str, str]] = []
    for data in raw_motifs.values():
        if not isinstance(data, dict) or data.get("retired"):
            continue
        occurrences = int(data.get("occurrence_count") or 0)
        if occurrences < _VISUAL_MOTIF_MIN_OCCURRENCES:
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        meaning = str(data.get("thematic_meaning") or "").strip()
        candidates.append((occurrences, name, meaning))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        f"{name}（{meaning}）" if meaning else name
        for _count, name, meaning in candidates[:_VISUAL_MOTIF_CAP]
    ]


def enrich_production_bible(bible: ProductionBible, layout: ProjectLayout) -> ProductionBible:
    """Apply visual timeline + motif anchors to an already-built production bible."""
    character_names = [character.name for character in bible.characters if character.name]
    entries = load_accepted_ledger_entries(layout.narrative_state_dir)
    timeline = build_visual_state_timeline(entries, character_names)
    for character in bible.characters:
        lines = timeline.get(character.name)
        if lines:
            existing = list(character.screen_identity.visual_state_timeline)
            merged = existing + [line for line in lines if line not in existing]
            character.screen_identity.visual_state_timeline = merged[
                -_TIMELINE_PER_CHARACTER_CAP:
            ]
    motif_state = _load_json(layout.memory_dir / "motif_state.json")
    motifs = extract_visual_motifs(motif_state)
    if motifs:
        bible.style.visual_motifs = motifs
    return bible
