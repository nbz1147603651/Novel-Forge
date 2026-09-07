"""Coauthor responses are typed suggestions, never executable file operations."""

from __future__ import annotations

from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.authoring import AuthoringReply
from novel_forge.pipeline.steps.base import PipelineStep


class AuthoringChatStep(PipelineStep[dict[str, Any], AuthoringReply]):
    @property
    def step_name(self) -> str:
        return "authoring_chat"

    async def _execute(self, input_data: dict[str, Any]) -> AuthoringReply:
        response = await self._call_with_retry(
            TaskType.AUTHORING_CHAT,
            {"input_payload": input_data},
            max_tokens=8192,
            temperature=0.5,
            required_keys=("reply", "proposals", "actions"),
        )
        return AuthoringReply.model_validate(response)
