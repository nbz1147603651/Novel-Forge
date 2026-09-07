"""QRunnable workers for the desktop workflow page.

All workers inherit :class:`BaseJobWorker`, which provides the asyncio loop
lifecycle, cooperative cancellation, and structured failure emission.
Subclasses define only their business signals and ``_run_async`` body.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from novel_forge.desktop.workers import BaseJobWorker, BaseJobWorkerSignals

if TYPE_CHECKING:
    from novel_forge.core.schemas.outline import StoryOutline
    from novel_forge.workspace.runtime import RuntimeServices

logger = logging.getLogger(__name__)


class OutlinePolishSignals(BaseJobWorkerSignals):
    suggestions_ready = Signal(list)
    polish_done = Signal(object)
    error = Signal(str)


class SemanticResolveSignals(BaseJobWorkerSignals):
    resolved = Signal(list, str)
    error = Signal(str)


class SemanticResolveWorker(BaseJobWorker):
    signals_cls = SemanticResolveSignals
    pool = "aux"

    def __init__(self, hint: str, chapters: list[dict[str, object]]) -> None:
        super().__init__()
        self._hint = hint
        self._chapters = chapters

    async def _run_async(self) -> None:
        from novel_forge.workspace.runtime import create_runtime_services

        runtime = create_runtime_services()
        try:
            await self._resolve_with_runtime(runtime)
        except Exception as exc:  # noqa: BLE001 — business error signal
            self.signals.error.emit(str(exc))
        finally:
            try:
                await runtime.shutdown()
            except Exception as exc:
                logger.warning("SemanticResolveWorker: runtime shutdown failed: %s", exc)

    async def _resolve_with_runtime(self, runtime: RuntimeServices) -> None:
        from novel_forge.common.constants import TaskType
        from novel_forge.gateway.text_retry import call_text_with_retry
        from novel_forge.gateway.types import ModelRequest
        from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

        summaries: list[str] = []
        for ch in self._chapters:
            num = ch.get("chapter_number", 0)
            title = ch.get("title", "未命名")
            goal = str(ch.get("goal", "") or "")[:80]
            summaries.append(f"第{num}章：{title}（{goal}）")

        system_msg = (
            "你是一位精确的章节定位助手。根据用户指令和全书章节列表，返回最匹配的章节编号。"
        )
        chapter_summary = "\n".join(summaries)
        user_msg = (
            f"用户指令：{self._hint}\n\n"
            f"全书章节（共{len(self._chapters)}章）：\n"
            f"{chapter_summary}\n\n"
            '请返回JSON：{"chapters":[1,2,...],"reason":"理由"}\n'
            "若无法确定，返回空数组。只输出JSON。"
        )
        request = ModelRequest(
            task_type=TaskType.ADJUST_OUTLINE,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=calculate_route_aware_max_tokens(
                runtime.router,
                TaskType.ADJUST_OUTLINE,
                max(300, len(self._chapters) * 18),
                prompt_overhead=1200,
                min_tokens=256,
                max_cap=1024,
            ),
            temperature=0.2,
        )
        response = await call_text_with_retry(runtime.router, request)
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        try:
            result = json.loads(content)
            chapters = result.get("chapters", [])
            reason = result.get("reason", "")
            self.signals.resolved.emit(chapters, reason)
        except (json.JSONDecodeError, IndexError):
            self.signals.error.emit(f"无法解析响应：{content[:200]}")


class OutlinePolishWorker(BaseJobWorker):
    signals_cls = OutlinePolishSignals
    pool = "aux"

    def __init__(
        self,
        story_outline: "StoryOutline",
        user_hint: str,
        selected_suggestions: list[str] | None = None,
        focus_fields: list[str] | None = None,
        chapter_range: str | tuple[int, int] | list[int] | None = None,
        analysis_only: bool | None = None,
        project_id: str = "",
    ) -> None:
        super().__init__()
        self._outline = story_outline
        self._hint = user_hint
        self._suggestions = selected_suggestions or []
        self._focus = focus_fields or []
        self._range = chapter_range
        self._analysis_only = not self._suggestions if analysis_only is None else analysis_only
        self._project_id = str(project_id or "").strip()

    async def _run_async(self) -> None:
        from novel_forge.workspace.runtime import create_runtime_services

        runtime = create_runtime_services()
        try:
            await self._polish_with_runtime(runtime)
        except Exception as exc:  # noqa: BLE001 — business error signal
            self.signals.error.emit(str(exc))
        finally:
            try:
                await runtime.shutdown()
            except Exception as exc:
                logger.warning("OutlinePolishWorker: runtime shutdown failed: %s", exc)

    async def _polish_with_runtime(self, runtime: RuntimeServices) -> None:
        from novel_forge.pipeline.steps.polish_outline_step import (
            PolishOutlineInput,
            PolishOutlineResult,
            PolishOutlineStep,
        )

        inp = PolishOutlineInput(
            story_outline=self._outline,
            user_hint=self._hint,
            selected_suggestions=self._suggestions,
            focus_fields=self._focus,
            chapter_range=self._range,
            analysis_only=self._analysis_only,
        )
        step: PolishOutlineStep = PolishOutlineStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
        )
        if self._project_id:
            step._project_id = self._project_id  # noqa: SLF001
        result: PolishOutlineResult = await step.run(inp)
        if result.polish_suggestions and not self._suggestions:
            self.signals.suggestions_ready.emit(result.polish_suggestions)
        self.signals.polish_done.emit(result)
