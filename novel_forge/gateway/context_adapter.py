"""Cross-provider context format adapter (Pi-inspired handoff).

Enables seamless model switching mid-conversation by converting context
formats between providers:
- OpenAI → Anthropic: thinking blocks → <thinking> tagged text
- Anthropic → OpenAI: thinking → reasoning_content field
- Tool calls and text content are preserved unchanged.

Quality constraints:
- Conversion never loses tool_calls or text content.
- Conversion failure degrades to "no context" mode (never blocks repair).
- format_contracts.enforce_required_keys is not affected by conversion.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Provider Identifiers ──────────────────────────────────────────────────────

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_GOOGLE = "google"
PROVIDER_UNKNOWN = "unknown"


# ── ContextFormatAdapter ──────────────────────────────────────────────────────


class ContextFormatAdapter:
    """Converts conversation context between provider formats.

    Usage::

        adapter = ContextFormatAdapter()
        converted = adapter.convert(
            messages=previous_context,
            from_provider="anthropic",
            to_provider="openai",
        )
    """

    def convert(
        self,
        messages: list[dict[str, Any]],
        *,
        from_provider: str,
        to_provider: str,
    ) -> list[dict[str, Any]]:
        """Convert messages from one provider format to another.

        Args:
            messages: The conversation context from the previous provider.
            from_provider: Source provider identifier.
            to_provider: Target provider identifier.

        Returns:
            Converted messages compatible with the target provider.
            On failure, returns messages with thinking content stripped.
        """
        if from_provider == to_provider:
            return messages

        try:
            if from_provider == PROVIDER_ANTHROPIC and to_provider in (
                PROVIDER_OPENAI,
                PROVIDER_DEEPSEEK,
            ):
                return self._anthropic_to_openai(messages)
            if from_provider in (PROVIDER_OPENAI, PROVIDER_DEEPSEEK) and to_provider == PROVIDER_ANTHROPIC:
                return self._openai_to_anthropic(messages)
            # Generic fallback: strip provider-specific fields
            return self._generic_normalize(messages)
        except Exception:
            logger.warning(
                "ContextFormatAdapter: conversion %s→%s failed, stripping thinking",
                from_provider,
                to_provider,
                exc_info=True,
            )
            return self._strip_thinking(messages)

    # ── Anthropic → OpenAI ────────────────────────────────────────────────

    def _anthropic_to_openai(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic format to OpenAI format.

        - thinking blocks → reasoning_content field
        - tool_use blocks → tool_calls array
        - tool_result role → tool role
        """
        result = []
        for msg in messages:
            converted = dict(msg)
            content = msg.get("content", "")

            # Handle content blocks (Anthropic uses list of blocks)
            if isinstance(content, list):
                text_parts = []
                thinking_parts = []
                tool_calls = []

                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "text":
                            text_parts.append(block.get("text", ""))
                        elif block_type == "thinking":
                            thinking_parts.append(block.get("thinking", ""))
                        elif block_type == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": block.get("input", {}),
                                },
                            })

                converted["content"] = "\n".join(text_parts)
                if thinking_parts:
                    converted["reasoning_content"] = "\n".join(thinking_parts)
                if tool_calls:
                    converted["tool_calls"] = tool_calls

            # Handle tool_result role
            if msg.get("role") == "user" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        converted["role"] = "tool"
                        converted["tool_call_id"] = block.get("tool_use_id", "")
                        converted["content"] = block.get("content", "")
                        break

            result.append(converted)
        return result

    # ── OpenAI → Anthropic ────────────────────────────────────────────────

    def _openai_to_anthropic(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI format to Anthropic format.

        - reasoning_content → thinking block
        - tool_calls → tool_use blocks
        - tool role → user role with tool_result block
        """
        result = []
        for msg in messages:
            converted = dict(msg)

            # Convert reasoning_content to thinking block
            reasoning = msg.get("reasoning_content", "")
            content = msg.get("content", "")

            if reasoning:
                blocks: list[dict[str, Any]] = []
                blocks.append({"type": "thinking", "thinking": reasoning})
                if content:
                    blocks.append({"type": "text", "text": content})
                converted["content"] = blocks
                converted.pop("reasoning_content", None)

            # Convert tool_calls to tool_use blocks
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                if not isinstance(converted.get("content"), list):
                    blocks = [{"type": "text", "text": str(converted.get("content", ""))}]
                else:
                    blocks = converted["content"]
                for tc in tool_calls:
                    func = tc.get("function", {})
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": func.get("arguments", {}),
                    })
                converted["content"] = blocks
                converted.pop("tool_calls", None)

            # Convert tool role to user with tool_result
            if msg.get("role") == "tool":
                converted["role"] = "user"
                converted["content"] = [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(msg.get("content", "")),
                }]

            result.append(converted)
        return result

    # ── Generic Normalization ─────────────────────────────────────────────

    def _generic_normalize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip provider-specific fields, keep text and tool_calls."""
        result = []
        for msg in messages:
            normalized = {
                "role": msg.get("role", "user"),
                "content": self._extract_text_content(msg.get("content", "")),
            }
            if msg.get("tool_calls"):
                normalized["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                normalized["tool_call_id"] = msg["tool_call_id"]
            result.append(normalized)
        return result

    def _strip_thinking(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Emergency fallback: strip all thinking/reasoning content."""
        result = []
        for msg in messages:
            cleaned = dict(msg)
            cleaned.pop("reasoning_content", None)
            content = cleaned.get("content", "")
            if isinstance(content, list):
                cleaned["content"] = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") != "thinking"
                ]
            result.append(cleaned)
        return result

    @staticmethod
    def _extract_text_content(content: Any) -> str:
        """Extract plain text from various content formats."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)
