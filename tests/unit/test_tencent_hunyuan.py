"""Tests for Tencent Hunyuan adapter."""

from __future__ import annotations

import sys
import types

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.adapters.tencent_hunyuan import (
    TencentHunyuanAdapter,
    normalize_tencent_model_id,
    optimize_tencent_model_id,
)
from novel_forge.gateway.types import ModelRequest


class TestTencentHunyuanAdapter:
    def test_provider_name(self):
        adapter = TencentHunyuanAdapter("test-key")
        assert adapter.provider_name == "tencent"

    def test_default_model(self):
        adapter = TencentHunyuanAdapter("test-key")
        assert adapter.default_model == "hy3"

    def test_custom_default_model(self):
        adapter = TencentHunyuanAdapter("test-key", "hunyuan-pro")
        assert adapter.default_model == "hunyuan-pro"

    def test_last_health_error_initial(self):
        adapter = TencentHunyuanAdapter("test-key")
        assert adapter.last_health_error == ""

    def test_normalize_legacy_model_aliases(self):
        assert normalize_tencent_model_id("hunyuan-2.0-think") == "hy3"
        assert normalize_tencent_model_id("hunyuan-2.0-instruct") == "hy3"
        assert normalize_tencent_model_id("hunyuan-turbos-latest") == "hy3"

    def test_optimize_tencent_model_id_prefers_instruct_when_thinking_not_requested(self):
        assert optimize_tencent_model_id("hunyuan-2.0-think", thinking=False) == "hy3"
        assert optimize_tencent_model_id("hunyuan-2.0-think", thinking=True) == "hy3"

    @pytest.mark.asyncio
    async def test_health_check_uses_single_v1_suffix(self, monkeypatch: pytest.MonkeyPatch):
        captured: dict[str, str] = {}

        class _AsyncOpenAI:
            def __init__(
                self,
                *,
                api_key: str,
                base_url: str,
                timeout: object,
                max_retries: int,
            ):
                captured["api_key"] = api_key
                captured["base_url"] = base_url
                captured["timeout"] = str(timeout)
                captured["max_retries"] = str(max_retries)
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                captured["model"] = str(kwargs["model"])
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="ok"),
                            finish_reason="stop",
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=1,
                        completion_tokens=1,
                        total_tokens=2,
                    ),
                )

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = TencentHunyuanAdapter("test-key", "hunyuan-2.0-instruct")
        ok = await adapter.health_check()

        assert ok is True
        assert captured["api_key"] == "test-key"
        assert captured["base_url"] == "https://api.hunyuan.cloud.tencent.com/v1"
        assert captured["max_retries"] == "0"
        assert captured["model"] == "hy3"

    @pytest.mark.asyncio
    async def test_complete_normalizes_legacy_model_alias_before_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        captured: dict[str, str] = {}

        class _AsyncOpenAI:
            def __init__(self, *, api_key: str, base_url: str, timeout: float, **_: object):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                captured["model"] = str(kwargs["model"])
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="ok"),
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=3,
                        completion_tokens=2,
                        total_tokens=5,
                    ),
                )

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = TencentHunyuanAdapter("test-key", "hunyuan-2.0-think")
        response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.DRAFT,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=8,
                temperature=0.0,
            )
        )

        assert captured["model"] == "hy3"
        assert response.model_id == "hy3"

    @pytest.mark.asyncio
    async def test_complete_keeps_thinking_model_when_thinking_requested(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        captured: dict[str, object] = {}

        class _AsyncOpenAI:
            def __init__(self, *, api_key: str, base_url: str, timeout: float, **_: object):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                captured["model"] = str(kwargs["model"])
                captured["extra_body"] = kwargs.get("extra_body")
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="<think>internal</think>ok"),
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=3,
                        completion_tokens=2,
                        total_tokens=5,
                    ),
                )

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = TencentHunyuanAdapter("test-key", "hunyuan-2.0-think")
        response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.DRAFT,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=8,
                temperature=0.0,
                thinking=True,
            )
        )

        assert captured["model"] == "hy3"
        assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
        assert response.model_id == "hy3"
        assert response.content == "ok"
        assert response.thinking_content == "internal"
