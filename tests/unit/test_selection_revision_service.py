from __future__ import annotations

import json

import pytest

from novel_forge.app_service.selection_revision import (
    MAX_SELECTION_REVISION_CHARS,
    SelectionRevisionError,
    SelectionRevisionInput,
    generate_selection_revision_candidate,
)
from novel_forge.gateway.types import ModelRequest, ModelResponse


class _CapturingRouter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, finish_reason="stop", model_id="")


async def test_selection_revision_uses_project_context_and_cleans_text_response(tmp_path) -> None:
    (tmp_path / "spec.json").write_text(
        json.dumps({"title": "雨城", "theme": "克制"}, ensure_ascii=False),
        encoding="utf-8",
    )
    router = _CapturingRouter("```markdown\n改写后：她并无立刻回头。\n```")

    revised = await generate_selection_revision_candidate(
        router,
        project_dir=tmp_path,
        request=SelectionRevisionInput(
            project_id="rain-city",
            chapter_number=4,
            chapter_title="高架桥下",
            selected_text="她没有立刻回头。",
            before_context="雨声没有停。",
            after_context="周砚仍在等她。",
            instruction="更凝练",
        ),
        temperature=0.3,
    )

    assert revised == "她并无立刻回头。"
    assert router.requests[0].task_type.value == "polish_chapter"
    prompt = str(router.requests[0].messages[1]["content"])
    assert "雨城" in prompt
    assert "更凝练" in prompt
    assert "雨声没有停。" in prompt


async def test_selection_revision_rejects_empty_and_oversized_selection(tmp_path) -> None:
    router = _CapturingRouter("不会调用")
    with pytest.raises(SelectionRevisionError, match="选区为空"):
        await generate_selection_revision_candidate(
            router,
            project_dir=tmp_path,
            request=SelectionRevisionInput(
                project_id="demo",
                chapter_number=1,
                chapter_title="",
                selected_text=" ",
            ),
            temperature=0.3,
        )
    with pytest.raises(SelectionRevisionError, match="超过"):
        await generate_selection_revision_candidate(
            router,
            project_dir=tmp_path,
            request=SelectionRevisionInput(
                project_id="demo",
                chapter_number=1,
                chapter_title="",
                selected_text="甲" * (MAX_SELECTION_REVISION_CHARS + 1),
            ),
            temperature=0.3,
        )
    assert router.requests == []
