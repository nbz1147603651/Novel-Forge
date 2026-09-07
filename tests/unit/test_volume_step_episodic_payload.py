"""Tests for episodic_context inclusion in VolumeAuditStep._execute payload."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep


@pytest.fixture
def mock_router():
    return MagicMock()


@pytest.fixture
def mock_builder():
    return MagicMock()


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.temp_volume_audit = 0.7
    return settings


@pytest.fixture
def step(mock_router, mock_builder, mock_settings):
    return VolumeAuditStep(
        router=mock_router,
        builder=mock_builder,
        settings=mock_settings,
    )


@pytest.fixture
def base_input():
    return VolumeAuditInput(
        volume=VolumeOutline(
            volume_number=1,
            title="Test Volume",
            start_chapter=1,
            end_chapter=5,
        ),
        story_synopsis="Test synopsis",
        chapter_summaries=[{"chapter": 1, "summary": "Chapter 1 summary"}],
        timeline_events=[{"event": "test event"}],
        active_characters=[{"name": "Alice"}],
        active_foreshadowing=[{"id": "foreshadow_1", "text": "It will rain"}],
        world_fact_keys=["key1", "key2"],
        blueprint_phases=[{"phase": 1}],
        blueprint_arc_milestones=[{"milestone": "m1"}],
    )


@pytest.mark.asyncio
async def test_episodic_context_included_in_payload(step, base_input):
    episodic_context = {"similar_volumes": [{"volume_id": "v1", "summary": "Volume 1"}]}

    captured_payload = None

    async def capture_call(task_type, payload, **kwargs):
        nonlocal captured_payload
        captured_payload = payload
        return {"volume_number": 1}

    base_input.episodic_context = episodic_context

    with patch.object(step, "_call_with_retry", side_effect=capture_call):
        await step._execute(base_input)

    assert captured_payload is not None
    assert "episodic_context" in captured_payload
    assert captured_payload["episodic_context"] is episodic_context
    assert "similar_volumes" in captured_payload["episodic_context"]


@pytest.mark.asyncio
async def test_episodic_context_none_not_in_payload(step, base_input):
    captured_payload = None

    async def capture_call(task_type, payload, **kwargs):
        nonlocal captured_payload
        captured_payload = payload
        return {"volume_number": 1}

    base_input.episodic_context = None

    with patch.object(step, "_call_with_retry", side_effect=capture_call):
        await step._execute(base_input)

    assert captured_payload is not None
    assert "episodic_context" not in captured_payload


@pytest.mark.asyncio
async def test_episodic_context_structure(step, base_input):
    episodic_context = {
        "similar_volumes": [
            {"volume_id": "v1", "summary": "Volume 1 summary"},
            {"volume_id": "v2", "summary": "Volume 2 summary"},
        ]
    }

    captured_payload = None

    async def capture_call(task_type, payload, **kwargs):
        nonlocal captured_payload
        captured_payload = payload
        return {"volume_number": 1}

    base_input.episodic_context = episodic_context

    with patch.object(step, "_call_with_retry", side_effect=capture_call):
        await step._execute(base_input)

    assert captured_payload is not None
    assert "episodic_context" in captured_payload
    ec = captured_payload["episodic_context"]
    assert "similar_volumes" in ec
    assert len(ec["similar_volumes"]) == 2
    assert ec["similar_volumes"][0]["volume_id"] == "v1"