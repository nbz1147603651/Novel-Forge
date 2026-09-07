"""Engine-owned generation for final-draft selection revision candidates.

The candidate is intentionally ephemeral until the existing manual-revision
command persists the edited chapter.  Keeping model routing and prompt context
here ensures PySide and Nimo receive the same rewrite semantics rather than
each manufacturing a local substitute.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.common.constants import TaskType
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.gateway.text_retry import call_text_with_retry
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter

MAX_SELECTION_REVISION_CHARS = 12_000
_STYLE_CONTEXT_CHARS = 3_600


class SelectionRevisionError(ValueError):
    """The requested local revision candidate cannot be generated safely."""


@dataclass(frozen=True)
class SelectionRevisionInput:
    project_id: str
    chapter_number: int
    chapter_title: str
    selected_text: str
    before_context: str = ""
    after_context: str = ""
    instruction: str = ""


def _compact_json_payload(data: Any, *, limit: int) -> str:
    if not data:
        return ""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(data)
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}\n…"


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - optional source artifacts are advisory context
        return None


def _chapter_outline_entry(outline: Any, chapter_number: int) -> Any:
    if chapter_number <= 0 or not isinstance(outline, dict):
        return None
    chapters = outline.get("chapters")
    if not isinstance(chapters, list):
        return None
    for item in chapters:
        if isinstance(item, dict) and int(item.get("chapter_number") or item.get("number") or 0) == chapter_number:
            return item
    return None


def project_selection_revision_context(project_dir: Path, chapter_number: int) -> str:
    """Load bounded project context used by both desktop clients' candidates."""

    packets: list[str] = []
    for label, filename in (
        ("创作规格", "spec.json"),
        ("世界与主题", "story_bible.json"),
        ("写作风格", "style_profile.json"),
        ("角色语气", "character_bible.json"),
    ):
        compact = _compact_json_payload(_load_json_file(project_dir / filename), limit=900)
        if compact:
            packets.append(f"【{label}】\n{compact}")
    outline = _load_json_file(project_dir / "outline.json")
    chapter = _chapter_outline_entry(outline, chapter_number)
    compact_chapter = _compact_json_payload(chapter, limit=900)
    if compact_chapter:
        packets.append(f"【当前章节大纲】\n{compact_chapter}")
    joined = "\n\n".join(packets).strip()
    return joined if len(joined) <= _STYLE_CONTEXT_CHARS else f"{joined[:_STYLE_CONTEXT_CHARS].rstrip()}\n…"


def clean_selection_revision_response(text: str) -> str:
    """Remove formatting wrappers the free-text candidate endpoint forbids."""

    cleaned = extract_text_content(text or "").strip()
    cleaned = re.sub(r"^```(?:text|markdown|md|json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    cleaned = re.sub(r"^(?:改写后|润色后|修改后|建议文本)\s*[:：]\s*", "", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] in {'"', "'", "“", "「"} and cleaned[-1] in {'"', "'", "”", "」"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


async def generate_selection_revision_candidate(
    router: ModelRouter,
    *,
    project_dir: Path,
    request: SelectionRevisionInput,
    temperature: float,
) -> str:
    """Return one LLM rewrite for a bounded selected passage without writing it."""

    selected = request.selected_text.strip()
    if not selected:
        raise SelectionRevisionError("选区为空，请选择包含正文的片段。")
    if len(selected) > MAX_SELECTION_REVISION_CHARS:
        raise SelectionRevisionError(
            f"选区超过 {MAX_SELECTION_REVISION_CHARS:,} 个字符，请缩小后再精修。"
        )

    chapter_label = f"第 {request.chapter_number} 章" if request.chapter_number > 0 else "短篇正文"
    style_context = project_selection_revision_context(project_dir, request.chapter_number)
    system = (
        "你是 Novel Forge 的终稿选段精修编辑。只改写用户选中的中文正文片段。"
        "必须保持事实、人物、视角、事件顺序、时间线、称谓与叙事口吻；"
        "让修改贴合项目既有主题、文风、节奏与人物语气。"
        "不输出解释、标题或列表。"
    )
    user = f"""请按要求润色/修改选中片段。

## 项目
- project_id: {request.project_id}
- 章节: {chapter_label}
- 标题: {request.chapter_title or "未命名"}

## 修改要求
{request.instruction.strip() or "在不改变事实与剧情的前提下，让文字更自然、更贴合本书文风。"}

## 风格与主题参考
{style_context or "暂无结构化风格资料，请严格贴合选区与前后文。"}

## 选区前文（只读上下文）
{request.before_context or "无"}

## 需要改写的选中文本
{selected}

## 选区后文（只读上下文）
{request.after_context or "无"}

## 输出
只返回改写后的选中文本。不得返回 JSON、解释、项目符号、引号包装或“改写后：”等前缀。
"""
    target_output_chars = max(len(selected), 512)
    min_tokens = max(768, int(len(selected) / 1.8) + 512)
    response = await call_text_with_retry(
        router,
        ModelRequest(
            task_type=TaskType.POLISH_CHAPTER,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=calculate_route_aware_max_tokens(
                router,
                TaskType.POLISH_CHAPTER,
                target_output_chars,
                prompt_overhead=5_000,
                min_tokens=min_tokens,
            ),
            temperature=temperature,
        ),
    )
    revised = clean_selection_revision_response(response.content)
    if not revised:
        raise SelectionRevisionError("模型没有返回可用文本。")
    return revised
