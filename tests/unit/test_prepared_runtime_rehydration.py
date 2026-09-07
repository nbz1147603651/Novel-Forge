"""Runtime-only context must be equivalent after plan confirmation or resume."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import novel_forge.pipeline.long.chapter_flow_orchestrate as orchestrate
from novel_forge.pipeline.long.execution_models import PreparedChapterArtifacts


async def test_rehydrate_prepared_runtime_restores_memory_and_reading_power(
    monkeypatch: Any,
) -> None:
    manager = SimpleNamespace(
        build_reading_power_hint=lambda **_kwargs: {
            "chapter_hook": "让读者追问密钥为何失效",
            "tension_target": 8,
        }
    )
    config = SimpleNamespace(enabled=True)
    monkeypatch.setattr(
        orchestrate,
        "_initialize_reading_power_runtime",
        lambda *_args, **_kwargs: (manager, config),
    )

    async def _collect_memory(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"memory_unified_guidance": ["保留密钥与旧承诺的因果联系"]}

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.context.stage_memory_builder.collect_planning_memory_hints",
        _collect_memory,
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(chapter_number=7))
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    context = SimpleNamespace(on_step=lambda event, payload: events.append((event, payload)))

    restored = await orchestrate.rehydrate_prepared_runtime_context(
        context,
        prepared=prepared,
        chapter_number=7,
    )

    assert restored is not prepared
    assert restored.window_manager is manager
    assert restored.window_config is config
    assert restored.memory_hints == {
        "memory_unified_guidance": ["保留密钥与旧承诺的因果联系"]
    }
    assert restored.reading_power_hint == {
        "chapter_hook": "让读者追问密钥为何失效",
        "tension_target": 8,
    }
    assert events == []
