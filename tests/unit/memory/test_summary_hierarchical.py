from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.memory.summary import (
    MultiGranularitySummaryService,
    SummaryConfig,
)


class _CaptureBuilder:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def build(self, task_type, context, **kwargs):
        self.contexts.append(dict(context))
        return SimpleNamespace(
            task_type=task_type,
            messages=[{"role": "user", "content": str(context.get("text", ""))}],
            max_tokens=int(kwargs.get("max_tokens", 4096)),
        )


class _SummaryRouter:
    def __init__(self, *, invalid_json: bool = False) -> None:
        self.calls = 0
        self.invalid_json = invalid_json

    async def route(self, request):
        self.calls += 1
        if self.invalid_json:
            return SimpleNamespace(content="not-json")
        return SimpleNamespace(
            content=json.dumps(
                {
                    "summary": f"模型摘要-{self.calls}",
                    "key_events": [f"事件-{self.calls}"],
                    "character_state_changes": [f"人物变化-{self.calls}"],
                    "unresolved_questions": [f"未决-{self.calls}"],
                    "new_facts": [f"事实-{self.calls}"],
                },
                ensure_ascii=False,
            )
        )


async def test_hierarchical_summary_covers_every_source_chunk() -> None:
    builder = _CaptureBuilder()
    router = _SummaryRouter()
    service = MultiGranularitySummaryService(
        router=router,  # type: ignore[arg-type]
        builder=builder,  # type: ignore[arg-type]
        config=SummaryConfig(input_token_budget=512, chapter_target_words=200),
    )
    markers = [f"【证据-{index}】" for index in range(12)]
    text = "\n".join(f"{marker}{'正文' * 90}" for marker in markers)

    payload = await service._llm_generate_summary_payload(
        text=text,
        target_words=200,
        task_type=TaskType.SUMMARIZE_CHAPTER,
    )

    chunk_sources = "\n".join(
        str(context["text"])
        for context in builder.contexts
        if context.get("summary_pass") == "evidence_chunk"
    )
    assert all(marker in chunk_sources for marker in markers)
    assert payload["_summary_strategy"] == "hierarchical_map_reduce"
    assert payload["_chunk_count"] >= 2
    assert len(payload["key_events"]) >= 2
    assert router.calls == len(builder.contexts)


async def test_invalid_summary_json_never_falls_back_to_source_prefix() -> None:
    service = MultiGranularitySummaryService(
        router=_SummaryRouter(invalid_json=True),  # type: ignore[arg-type]
        builder=_CaptureBuilder(),  # type: ignore[arg-type]
    )

    with pytest.raises(json.JSONDecodeError):
        await service._llm_generate_summary(
            text="完整正文" * 100,
            target_words=200,
            task_type=TaskType.SUMMARIZE_CHAPTER,
        )


async def test_short_chapter_is_preserved_complete_without_local_prefix_cut() -> None:
    router = _SummaryRouter()
    service = MultiGranularitySummaryService(
        router=router,  # type: ignore[arg-type]
        builder=_CaptureBuilder(),  # type: ignore[arg-type]
        config=SummaryConfig(chapter_target_words=200),
    )
    text = "开端。" + "发展。" * 20 + "结尾事实必须保留。"

    result = await service.generate_chapter_summary(1, text)

    assert result.text == text
    assert result.text.endswith("结尾事实必须保留。")
    assert router.calls == 0


async def test_volume_summary_rejects_missing_chapter_evidence() -> None:
    service = MultiGranularitySummaryService(
        router=_SummaryRouter(),  # type: ignore[arg-type]
        builder=_CaptureBuilder(),  # type: ignore[arg-type]
    )
    volume = VolumeOutline(
        volume_number=1,
        title="第一卷",
        start_chapter=1,
        end_chapter=3,
        arc_goal="建立主线",
        milestone_targets=[],
        main_conflicts=[],
        climax_hint="",
        resolution_hint="",
        notes="",
    )

    with pytest.raises(ValueError, match="缺少章节摘要"):
        await service.generate_volume_summary(
            volume,
            {1: "第一章摘要", 3: "第三章摘要"},
        )
