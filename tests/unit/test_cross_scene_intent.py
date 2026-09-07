"""Tests for the CrossSceneIntent schema (PLAN -> WAVE contract).

The schema has two fields by design lock:
  * ``cross_scene_references``: list[CrossSceneRef]
  * ``pacing_curve``:           list[int]

Both fields default to empty, ``extra="forbid"`` on both classes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.chapter import CrossSceneIntent, CrossSceneRef


def _ref(
    from_scene: str = "scene_01",
    to_scene: str = "scene_02",
    ref_type: str = "callback",
    description: str = "林远穿过雾霭街道看到图书馆",
) -> CrossSceneRef:
    return CrossSceneRef(
        from_scene=from_scene,
        to_scene=to_scene,
        ref_type=ref_type,
        description=description,
    )


def test_cross_scene_intent_requires_both_fields() -> None:
    """Both fields must be present, even when empty."""
    intent = CrossSceneIntent(cross_scene_references=[], pacing_curve=[])
    assert intent.cross_scene_references == []
    assert intent.pacing_curve == []


def test_cross_scene_intent_extra_fields_forbidden() -> None:
    """extra='forbid' rejects unknown keys on both CrossSceneIntent and
    CrossSceneRef."""
    with pytest.raises(ValidationError):
        CrossSceneIntent(
            cross_scene_references=[],
            pacing_curve=[],
            surprise_field="boom",
        )

    with pytest.raises(ValidationError):
        CrossSceneRef(
            from_scene="scene_01",
            to_scene="scene_02",
            ref_type="callback",
            description="...",
            bogus_extra="nope",
        )


def test_cross_scene_intent_pacing_curve_accepts_int_list() -> None:
    """pacing_curve is a flat list[int] (1-5 per scene)."""
    intent = CrossSceneIntent(
        cross_scene_references=[_ref()],
        pacing_curve=[1, 2, 3, 4, 5],
    )
    assert intent.pacing_curve == [1, 2, 3, 4, 5]


def test_cross_scene_intent_ref_type_must_be_literal() -> None:
    """CrossSceneRef.ref_type is a closed literal — anything else fails."""
    with pytest.raises(ValidationError):
        CrossSceneRef(
            from_scene="scene_01",
            to_scene="scene_02",
            ref_type="totally_made_up",
            description="...",
        )


def test_cross_scene_intent_round_trip_dump_load() -> None:
    """The schema round-trips through model_dump / model_validate."""
    intent = CrossSceneIntent(
        cross_scene_references=[_ref()],
        pacing_curve=[3, 4],
    )
    dumped = intent.model_dump(mode="json")
    reloaded = CrossSceneIntent.model_validate(dumped)
    assert reloaded == intent
    # pacing_curve survives as plain ints (not coerced to str)
    assert reloaded.pacing_curve == [3, 4]
