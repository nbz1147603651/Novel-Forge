"""Human-in-the-loop decision provider contracts for high-risk repair actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class HumanDecisionOption:
    """One selectable human decision option."""

    id: str
    label: str
    description: str = ""

    def to_payload(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "description": self.description}


@dataclass(frozen=True)
class HumanDecisionRequest:
    """Request payload for a high-risk repair decision."""

    decision_id: str
    kind: str
    project_id: str
    chapter_number: int
    title: str
    message: str
    options: tuple[HumanDecisionOption, ...]
    default_option: str
    timeout_seconds: int = 300
    risk: str = "medium"
    cost_hint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    requires_explicit_approval: bool = False

    @property
    def approval_required(self) -> bool:
        return self.requires_explicit_approval or self.kind in {
            "init_copilot_gate",
            "init_request_drift_gate",
        }

    @property
    def approval_version(self) -> str:
        payload = [
            self.decision_id,
            self.kind,
            self.project_id,
            self.chapter_number,
            [option.to_payload() for option in self.options],
            self.metadata,
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "kind": self.kind,
            "project_id": self.project_id,
            "chapter_number": self.chapter_number,
            "title": self.title,
            "message": self.message,
            "options": [option.to_payload() for option in self.options],
            "default_option": self.default_option,
            "timeout_seconds": self.timeout_seconds,
            "risk": self.risk,
            "cost_hint": self.cost_hint,
            "metadata": dict(self.metadata),
            "requires_explicit_approval": self.approval_required,
            "approval_version": self.approval_version,
        }


@dataclass(frozen=True)
class HumanDecisionResponse:
    """Resolved human decision."""

    choice: str
    custom_text: str = ""
    timed_out: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "choice": self.choice,
            "custom_text": self.custom_text,
            "timed_out": self.timed_out,
        }


class HumanDecisionProvider(Protocol):
    """Async decision provider used by CLI/API/Desktop flows."""

    async def request_decision(self, request: HumanDecisionRequest) -> HumanDecisionResponse:
        """Return a decision response, possibly after user interaction."""


class AutoDecisionProvider:
    """Non-interactive provider for CLI/API and unattended runs."""

    def __init__(self, default_choice: str = "continue_without_escalation") -> None:
        self._default_choice = default_choice

    async def request_decision(self, request: HumanDecisionRequest) -> HumanDecisionResponse:
        if request.approval_required:
            raise ValueError("此决定需要作者明确批准，默认选项不能代替批准")
        choice = request.default_option or self._default_choice
        return HumanDecisionResponse(choice=choice, timed_out=False)


class CallbackDecisionProvider:
    """Thread-safe async provider that lets a UI resolve decisions by id.

    ``preseeded_decisions`` carries decisions that were already resolved by
    the human before this run started (e.g. a work unit resumed after the
    author confirmed "第 N 章确认后继续" across an app restart).  A request
    Mandatory approvals match both ``decision_id`` and ``approval_version``.
    Legacy non-mandatory decisions retain their kind/chapter fallback.
    """

    def __init__(
        self,
        on_request: Callable[[dict[str, Any]], None],
        *,
        default_choice: str = "continue_without_escalation",
        preseeded_decisions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._on_request = on_request
        self._default_choice = default_choice
        self._pending: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Future[HumanDecisionResponse]]
        ] = {}
        self._requests: dict[str, HumanDecisionRequest] = {}
        self._preseeded: list[dict[str, Any]] = [
            seed for seed in (preseeded_decisions or []) if isinstance(seed, dict)
        ]

    def _match_preseeded(self, request: HumanDecisionRequest) -> dict[str, Any] | None:
        for seed in self._preseeded:
            if str(seed.get("decision_id", "") or "") == request.decision_id:
                if (
                    not request.approval_required
                    or seed.get("approval_version") == request.approval_version
                ) and not seed.get("timed_out"):
                    return (
                        seed
                        if seed.get("choice") in {item.id for item in request.options}
                        else None
                    )
        if request.approval_required:
            return None
        for seed in self._preseeded:
            seed_kind = str(seed.get("kind", "") or "")
            seed_chapter = seed.get("chapter_number")
            if (
                seed_kind
                and seed_kind == request.kind
                and (seed_chapter is None or int(seed_chapter) == request.chapter_number)
            ):
                return seed
        return None

    async def request_decision(self, request: HumanDecisionRequest) -> HumanDecisionResponse:
        seed = self._match_preseeded(request)
        if seed is not None:
            return HumanDecisionResponse(
                choice=str(seed.get("choice", "") or request.default_option),
                custom_text=str(seed.get("custom_text", "") or ""),
            )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[HumanDecisionResponse] = loop.create_future()
        self._pending[request.decision_id] = (loop, future)
        self._requests[request.decision_id] = request
        self._on_request(request.to_payload())
        timeout = max(1, int(request.timeout_seconds or 1))
        try:
            if request.approval_required:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request.decision_id, None)
            return HumanDecisionResponse(
                choice=request.default_option or self._default_choice,
                timed_out=True,
            )
        finally:
            self._pending.pop(request.decision_id, None)
            self._requests.pop(request.decision_id, None)

    def provide_decision(
        self,
        decision_id: str,
        choice: str,
        *,
        custom_text: str = "",
        timed_out: bool = False,
        approval_version: str = "",
    ) -> bool:
        request = self._requests.get(decision_id)
        if request is None or choice not in {option.id for option in request.options}:
            return False
        if request.approval_required and (
            timed_out or approval_version != request.approval_version
        ):
            return False
        pending = self._pending.pop(decision_id, None)
        if pending is None:
            return False
        loop, future = pending
        response = HumanDecisionResponse(
            choice=choice,
            custom_text=custom_text,
            timed_out=timed_out,
        )

        def resolve() -> None:
            if not future.done():
                future.set_result(response)

        loop.call_soon_threadsafe(resolve)
        return True


async def request_human_decision(
    *,
    provider: HumanDecisionProvider | None,
    on_step: Callable[[str, Any], None],
    request: HumanDecisionRequest,
) -> HumanDecisionResponse:
    """Emit request/resolution events around a provider call."""

    on_step("human_decision_requested", request.to_payload())
    active_provider = provider or AutoDecisionProvider(request.default_option)
    response = await active_provider.request_decision(request)
    if response.choice not in {option.id for option in request.options} or (
        request.approval_required and response.timed_out
    ):
        raise ValueError("人工决定无效或已超时；未批准任何变更")
    event = "human_decision_timeout" if response.timed_out else "human_decision_resolved"
    payload = request.to_payload()
    payload.update(response.to_payload())
    on_step(event, payload)
    return response
