"""Locations field-level completeness checks and targeted backfill merging.

Implements the Early-Constraint hardening for ``StoryBible.locations``:
cross-media core fields are mandatory, and any missing field triggers a
targeted ``LOCATIONS_FIELD_BACKFILL`` task instead of re-running the whole
story-bible initialization.  All helpers are deterministic and LLM-free.
"""

from __future__ import annotations

from typing import Any

# Cross-media core fields that every location must carry (Early-Constraint
# hardening).  ``name`` is implicitly required by the schema itself.
REQUIRED_LOCATION_FIELDS: tuple[str, ...] = (
    "location_id",
    "dramatic_function",
    "spatial_layout",
    "materials",
    "practical_lights",
    "ambient_sound",
    "continuity_rules",
)

# Fields whose emptiness is tolerated when the story genuinely has no use
# for them (kept explicit so future hardening can move them into the
# required set without touching call sites).
_OPTIONAL_LOCATION_FIELDS: frozenset[str] = frozenset(
    {"geography", "era", "weather_states", "recurring_props"}
)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def find_missing_location_fields(story_bible: Any) -> list[dict[str, Any]]:
    """Return a per-location report of missing cross-media core fields.

    Accepts a ``StoryBible`` model or a raw mapping with a ``locations`` key.
    Each report entry carries ``name``, ``location_id`` and the ``missing``
    field list so the backfill prompt can target exactly those gaps.
    """

    locations: list[Any] = []
    if isinstance(story_bible, dict):
        raw = story_bible.get("locations") or []
        if isinstance(raw, list):
            locations = raw
    else:
        raw = getattr(story_bible, "locations", None) or []
        locations = list(raw)

    report: list[dict[str, Any]] = []
    for item in locations:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            location_id = str(item.get("location_id") or "")
            missing = [
                field
                for field in REQUIRED_LOCATION_FIELDS
                if _is_blank(item.get(field))
            ]
        else:
            name = str(getattr(item, "name", "") or "")
            location_id = str(getattr(item, "location_id", "") or "")
            missing = [
                field
                for field in REQUIRED_LOCATION_FIELDS
                if _is_blank(getattr(item, field, None))
            ]
        if missing:
            report.append(
                {
                    "name": name,
                    "location_id": location_id,
                    "missing": missing,
                }
            )
    return report


def merge_location_backfill(
    story_bible: dict[str, Any], backfill_locations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge backfilled fields into an existing story-bible mapping.

    Matching is by ``location_id`` first, then ``name``.  Only blank fields
    are filled; existing content is never overwritten, preserving the
    "polish but never clobber" rule of the Early-Constraint principle.
    Returns the mutated mapping for convenience.
    """

    targets = story_bible.get("locations")
    if not isinstance(targets, list):
        return story_bible

    for patch in backfill_locations:
        if not isinstance(patch, dict):
            continue
        patch_id = str(patch.get("location_id") or "")
        patch_name = str(patch.get("name") or "")
        target = next(
            (
                item
                for item in targets
                if isinstance(item, dict)
                and (
                    (patch_id and str(item.get("location_id") or "") == patch_id)
                    or (patch_name and str(item.get("name") or "") == patch_name)
                )
            ),
            None,
        )
        if target is None:
            continue
        for key, value in patch.items():
            if key in ("name", "location_id"):
                continue
            if key in _OPTIONAL_LOCATION_FIELDS or key in REQUIRED_LOCATION_FIELDS:
                if _is_blank(target.get(key)) and not _is_blank(value):
                    target[key] = value
    return story_bible
