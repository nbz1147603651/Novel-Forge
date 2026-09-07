"""Shared runner for strict JSON artifact fragments."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.persistence.filesystem import atomic_write_json


@dataclass(frozen=True)
class SplitJsonFragment:
    """One independently generated JSON fragment."""

    name: str
    task_type: TaskType
    context: dict[str, Any]
    required_keys: tuple[str, ...]
    max_tokens: int
    temperature: float = 0.0
    max_retries: int = 3
    depends_on: tuple[str, ...] = ()
    cache_version: str = ""


SplitJsonCall = Callable[
    [
        TaskType,
        dict[str, Any],
        int,
        float,
        tuple[str, ...],
        int,
    ],
    Awaitable[dict[str, Any]],
]


@dataclass
class SplitArtifactResult:
    """Collected fragment payloads keyed by fragment name."""

    fragments: dict[str, dict[str, Any]] = field(default_factory=dict)
    from_checkpoint: set[str] = field(default_factory=set)


class SplitArtifactRunner:
    """Run JSON fragments with bounded parallelism and optional checkpointing.

    The runner deliberately knows nothing about StoryBible, Canon, or audit
    semantics. Domain steps provide fragment specs, merge the resulting dicts,
    then validate the materialized artifact with their own Pydantic model.
    """

    def __init__(
        self,
        *,
        call_json: SplitJsonCall,
        max_parallel: int = 4,
        checkpoint_path: Path | None = None,
        artifact_name: str = "split_artifact",
    ) -> None:
        self._call_json = call_json
        self._max_parallel = max(1, int(max_parallel or 1))
        self._checkpoint_path = checkpoint_path
        self._artifact_name = artifact_name
        self._checkpoint_lock = asyncio.Lock()

    async def run(self, fragments: list[SplitJsonFragment]) -> SplitArtifactResult:
        """Run all fragments whose dependencies are already satisfied."""
        if not fragments:
            return SplitArtifactResult()

        signature = self._signature(fragments)
        checkpoint = self._load_checkpoint(signature)
        checkpoint_fragments = checkpoint.get("fragments") if isinstance(checkpoint, dict) else None
        checkpoint_payloads = checkpoint_fragments if isinstance(checkpoint_fragments, dict) else {}
        completed: dict[str, dict[str, Any]] = {}
        checkpoint_changed = False
        for fragment in fragments:
            raw_payload = checkpoint_payloads.get(fragment.name)
            if not isinstance(raw_payload, dict):
                continue
            normalized_payload = self._normalize_fragment_payload(
                fragment,
                raw_payload,
                from_checkpoint=True,
            )
            if normalized_payload is None:
                checkpoint_changed = True
                continue
            completed[fragment.name] = normalized_payload
            if normalized_payload != raw_payload:
                checkpoint_changed = True

        result = SplitArtifactResult(
            fragments=dict(completed),
            from_checkpoint=set(completed),
        )
        if checkpoint_changed and completed:
            await self._save_checkpoint(signature, result.fragments)
        pending_by_name = {fragment.name: fragment for fragment in fragments}

        while pending_by_name:
            ready = [
                fragment
                for fragment in pending_by_name.values()
                if all(dep in result.fragments for dep in fragment.depends_on)
            ]
            if not ready:
                missing = sorted(pending_by_name)
                raise RuntimeError(
                    f"{self._artifact_name} split fragments have unresolved dependencies: {missing}"
                )

            ready = [fragment for fragment in ready if fragment.name not in result.fragments]
            if not ready:
                for name in list(pending_by_name):
                    if name in result.fragments:
                        pending_by_name.pop(name, None)
                continue

            sem = asyncio.Semaphore(self._max_parallel)

            async def _run_one(
                fragment: SplitJsonFragment,
                _sem: asyncio.Semaphore = sem,
            ) -> tuple[str, dict[str, Any]]:
                async with _sem:
                    payload = await self._call_json(
                        fragment.task_type,
                        fragment.context,
                        fragment.max_tokens,
                        fragment.temperature,
                        fragment.required_keys,
                        fragment.max_retries,
                    )
                    payload = self._normalize_fragment_payload(
                        fragment,
                        payload,
                        from_checkpoint=False,
                    )
                    if payload is None:
                        raise ValueError(
                            f"{self._artifact_name} fragment {fragment.name} failed validation"
                        )
                    return fragment.name, payload

            for name, payload in await asyncio.gather(*[_run_one(fragment) for fragment in ready]):
                result.fragments[name] = payload
                pending_by_name.pop(name, None)
                await self._save_checkpoint(signature, result.fragments)

        return result

    def _signature(self, fragments: list[SplitJsonFragment]) -> str:
        payload = [
            {
                "name": fragment.name,
                "task_type": fragment.task_type.value,
                "context": fragment.context,
                "required_keys": list(fragment.required_keys),
                "cache_version": fragment.cache_version,
            }
            for fragment in fragments
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_checkpoint(self, signature: str) -> dict[str, Any]:
        if self._checkpoint_path is None or not self._checkpoint_path.exists():
            return {}
        try:
            payload = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if payload.get("signature") != signature:
            return {}
        return payload

    def _normalize_fragment_payload(
        self,
        fragment: SplitJsonFragment,
        payload: dict[str, Any],
        *,
        from_checkpoint: bool,
    ) -> dict[str, Any] | None:
        """Re-apply task adapters and contracts before accepting a fragment."""

        normalized = deepcopy(payload)
        try:
            from novel_forge.core.format_contracts import validate_json_output_contract
            from novel_forge.core.parsing.response_schemas import validate_response_schema
            from novel_forge.pipeline.long.services.generation.llm_helpers import (
                apply_task_response_defaults,
            )
            from novel_forge.pipeline.long.services.task_output_adapters import (
                apply_task_output_adapter,
            )

            apply_task_output_adapter(
                normalized,
                fragment.task_type,
                context=fragment.context,
            )
            apply_task_response_defaults(normalized, fragment.task_type)
            validate_response_schema(normalized, fragment.task_type)
            validate_json_output_contract(
                fragment.task_type,
                normalized,
                context=fragment.context,
                explicit_required_keys=fragment.required_keys,
            )
        except Exception:
            if from_checkpoint:
                return None
            raise
        return normalized

    async def _save_checkpoint(
        self,
        signature: str,
        fragments: dict[str, dict[str, Any]],
    ) -> None:
        if self._checkpoint_path is None:
            return
        async with self._checkpoint_lock:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                self._checkpoint_path,
                {
                    "schema_version": 2,
                    "artifact_name": self._artifact_name,
                    "signature": signature,
                    "fragments": fragments,
                },
            )
