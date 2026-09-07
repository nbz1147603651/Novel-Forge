"""Shared utility functions for the novel_forge package."""

from __future__ import annotations

from typing import Any

_GENDER_ALIASES: dict[str, str] = {
    "男": "男",
    "male": "男",
    "man": "男",
    "m": "男",
    "女": "女",
    "female": "女",
    "woman": "女",
    "f": "女",
}


def normalize_gender_value(value: object) -> str:
    """Normalize approved gender aliases to canonical 男/女 or an empty value.

    Story-kernel identity fields accept only the two canonical values used by
    the CharacterBible.  Treat an unknown model-produced value as missing so
    it cannot become a persistent canonical fact.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _GENDER_ALIASES.get(text.lower(), "")


# ---------------------------------------------------------------------------
# Text helpers shared by long and short pipelines
# (canonical home; re-exported by pipeline.long.services.llm_helpers for
# backward compatibility)
# ---------------------------------------------------------------------------

_INSTRUCTION_KEYWORDS = (
    "请",
    "必须",
    "禁止",
    "不要",
    "应该",
    "需要",
    "要求",
    "根据",
    "按照",
    "参考",
    "确保",
    "注意",
    "关键",
    "segment_",
    "beat",
    "execution",
    "plan",
    "约束",
    "目标字数",
    "基调",
    "体裁",
    "章节",
    "场景",
)

_META_PATTERNS = (
    "以下是",
    "根据您的要求",
    "我已经",
    "让我来",
    "好的",
    "好的，我",
    "明白了",
)


def clean_conversation_history(
    messages: list[dict[str, str]],
    *,
    max_rounds: int = 2,
) -> list[dict[str, str]]:
    """Clean conversation history to remove instructional content that could pollute context.

    Keeps: assistant responses (actual generated text) and minimal factual summaries.
    Removes: full user prompts, planning terminology, meta-comments.
    """
    if not messages:
        return []

    cleaned: list[dict[str, str]] = []
    round_size = 2
    start_idx = max(0, len(messages) - max_rounds * round_size)

    i = start_idx
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "user":
            factual_parts = []
            for line in content.split("。"):
                line = line.strip()
                if not line:
                    continue
                is_instruction = any(kw in line for kw in _INSTRUCTION_KEYWORDS)
                if not is_instruction and len(line) < 80:
                    factual_parts.append(line)

            if factual_parts:
                cleaned.append({"role": "user", "content": "；".join(factual_parts[:3])})
            else:
                cleaned.append({"role": "user", "content": "[已清理指令内容]"})

            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                assistant_content = messages[i + 1].get("content", "") or ""
                clean_content = (
                    assistant_content[:600] if len(assistant_content) > 600 else assistant_content
                )
                for pattern in _META_PATTERNS:
                    if clean_content.startswith(pattern):
                        clean_content = clean_content[len(pattern) :].lstrip("：: ")
                if clean_content.strip():
                    cleaned.append({"role": "assistant", "content": clean_content})
                i += 2
                continue
        i += 1

    return cleaned


def excerpt_text(value: Any, *, limit: int = 220) -> str:
    """Truncate text to ~limit chars with ellipsis in the middle."""
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    head = max(80, int(limit * 0.55))
    tail = max(60, limit - head - 3)
    return f"{text[:head]}...{text[-tail:]}"


def unique_texts(values: list[str]) -> list[str]:
    """Deduplicate strings while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = " ".join(str(value or "").split()).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
