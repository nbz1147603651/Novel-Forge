"""Tests for gateway adapter modules."""

from __future__ import annotations

import json

from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.gateway.adapters.deepseek import DeepSeekAdapter
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.adapters.tongyi import TongyiAdapter
from novel_forge.gateway.types import ModelRequest


class TestMockAdapter:
    def test_complete_returns_response(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        assert resp.content is not None
        assert len(resp.content) > 0

    def test_complete_beats_task(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.BEATS,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        assert resp.content is not None

    def test_complete_edit_task(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.EDIT_CHAPTER,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        assert resp.content is not None

    def test_complete_evaluate_task(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        assert resp.content is not None

    def test_complete_unknown_task_fallback(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.SUMMARIZE_SCENE,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        assert resp.content is not None

    def test_health_check(self) -> None:
        adapter = MockAdapter()
        import asyncio

        result = asyncio.run(adapter.health_check())
        assert result is True

    def test_shutdown_no_op(self) -> None:
        adapter = MockAdapter()
        import asyncio

        asyncio.run(adapter.shutdown())

    def test_aclose_no_op(self) -> None:
        adapter = MockAdapter()
        import asyncio

        asyncio.run(adapter.aclose())

    def test_init_conflict_adjudication_response_matches_contract(self) -> None:
        adapter = MockAdapter()
        req = ModelRequest(
            task_type=TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
            messages=[{"role": "user", "content": "test"}],
        )
        import asyncio

        resp = asyncio.run(adapter.complete(req))
        payload = json.loads(resp.content)

        validate_json_output_contract(
            TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
            payload,
        )
        assert payload["schema_version"] == "audit_v2"
        assert payload["dimension"] == "init_coherence"


class TestDeepSeekAdapter:
    def test_provider_name(self) -> None:
        adapter = DeepSeekAdapter(api_key="sk-test")
        assert adapter.provider_name == "deepseek"

    def test_default_model(self) -> None:
        adapter = DeepSeekAdapter(api_key="sk-test")
        assert adapter.default_model == "deepseek-v4-flash"


class TestTongyiAdapter:
    def test_provider_name(self) -> None:
        adapter = TongyiAdapter(api_key="sk-test")
        assert adapter.provider_name == "tongyi"

    def test_default_model(self) -> None:
        adapter = TongyiAdapter(api_key="sk-test")
        assert adapter.default_model is not None
