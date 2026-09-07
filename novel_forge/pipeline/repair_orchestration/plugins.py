"""Candidate-first repair plugin and publication contracts.

The contracts keep diagnosis/editing separate from publication.  A plugin is
accepted by the new registry only when it explicitly declares that audit,
location, proposal, and verification are candidate-safe and never mutate
official project artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairArtifactSnapshot,
    RepairAuthority,
    RepairCandidate,
    RepairCandidateOrigin,
    RepairCase,
    RepairPatchRecord,
    RepairPublishReceipt,
    RepairVerificationBundle,
)


class UnsafeRepairPluginError(ValueError):
    """Raised when a write-capable legacy handler enters the candidate path."""


class RepairPublicationError(RuntimeError):
    """Raised when publication authority or receipt validation fails."""


@dataclass(frozen=True, slots=True)
class RepairCandidateDraft:
    """In-memory candidate produced from an immutable artifact snapshot."""

    payload: Any
    origin: RepairCandidateOrigin
    patches: tuple[RepairPatchRecord, ...] = ()
    change_ratio: float = 0.0
    protected_items: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepairCandidateMaterial:
    """Candidate identity plus its isolated content for independent verification."""

    candidate: RepairCandidate
    payload: Any


@dataclass(frozen=True, slots=True)
class RepairAuthorityContext:
    """Transport-neutral authority and CAS facts checked before publication."""

    authority: RepairAuthority
    target: str
    current_hash: str
    source_version: str = ""
    input_version: str = ""
    policy_version: int | None = None
    approval_id: str = ""
    proposal_id: str = ""
    receipt_id: str = ""
    publish_allowed: bool = False
    stopped: bool = False
    disabled_reason: str = ""


class RepairPlugin(Protocol):
    """Public extension point for one content type.

    ``audit`` and ``verify`` are intentionally separate so a proposal model
    cannot grade its own result without the original validators running again.
    """

    name: str
    content_types: Sequence[str]
    candidate_safe: Literal[True]

    async def audit(self, snapshot: RepairArtifactSnapshot) -> list[AuditIssueV2]: ...

    def locate(
        self,
        issue: AuditIssueV2,
        snapshot: RepairArtifactSnapshot,
    ) -> list[ResolvedRepairTarget]: ...

    async def propose(
        self,
        issue: AuditIssueV2,
        targets: Sequence[ResolvedRepairTarget],
        snapshot: RepairArtifactSnapshot,
    ) -> RepairCandidateDraft: ...

    async def verify(
        self,
        candidate: RepairCandidateMaterial,
        issues: Sequence[AuditIssueV2],
    ) -> RepairVerificationBundle: ...


class RepairPublisher(Protocol):
    """Only component allowed to move verified candidate content into a target."""

    name: str
    content_types: Sequence[str]

    async def publish(
        self,
        case: RepairCase,
        candidate: RepairCandidateMaterial,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt: ...

    async def recover(
        self,
        receipt: RepairPublishReceipt,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt: ...


class RepairPluginRegistry:
    """Resolve candidate-safe plugins without depending on domain loops."""

    def __init__(self) -> None:
        self._plugins: dict[str, RepairPlugin] = {}

    def register(self, plugin: RepairPlugin) -> None:
        if getattr(plugin, "candidate_safe", False) is not True:
            raise UnsafeRepairPluginError(
                f"repair plugin {getattr(plugin, 'name', type(plugin).__name__)} "
                "does not guarantee candidate-only writes"
            )
        if isinstance(plugin.content_types, str):
            raise ValueError("repair plugin content_types must be a sequence, not a string")
        content_types = tuple(str(item).strip() for item in plugin.content_types)
        if not content_types or any(not item for item in content_types):
            raise ValueError("repair plugin must declare at least one content type")
        for content_type in content_types:
            existing = self._plugins.get(content_type)
            if existing is not None and existing is not plugin:
                raise ValueError(f"repair plugin already registered: {content_type}")
            self._plugins[content_type] = plugin

    def resolve(self, content_type: str) -> RepairPlugin | None:
        return self._plugins.get(content_type) or self._plugins.get("*")


class RepairPublisherRegistry:
    """Resolve guarded publishers independently from repair plugins."""

    def __init__(self) -> None:
        self._publishers: dict[str, RepairPublisher] = {}

    def register(self, publisher: RepairPublisher) -> None:
        if isinstance(publisher.content_types, str):
            raise ValueError("repair publisher content_types must be a sequence, not a string")
        content_types = tuple(str(item).strip() for item in publisher.content_types)
        if not content_types or any(not item for item in content_types):
            raise ValueError("repair publisher must declare at least one content type")
        for content_type in content_types:
            existing = self._publishers.get(content_type)
            if existing is not None and existing is not publisher:
                raise ValueError(f"repair publisher already registered: {content_type}")
            self._publishers[content_type] = publisher

    def resolve(self, content_type: str) -> RepairPublisher | None:
        return self._publishers.get(content_type) or self._publishers.get("*")


class LegacyRepairHandlerAdapter:
    """Explicit marker for old execute-first handlers.

    This adapter is documentation and a safety tripwire, not a bridge into the
    candidate-first registry.  Legacy handlers remain callable only through the
    existing ``RepairOrchestrator.run`` compatibility path.
    """

    candidate_safe: Literal[False] = False

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.name = f"legacy:{getattr(handler, 'name', type(handler).__name__)}"
        self.content_types = ("legacy",)


__all__ = [
    "LegacyRepairHandlerAdapter",
    "RepairAuthorityContext",
    "RepairCandidateDraft",
    "RepairCandidateMaterial",
    "RepairPlugin",
    "RepairPluginRegistry",
    "RepairPublicationError",
    "RepairPublisher",
    "RepairPublisherRegistry",
    "UnsafeRepairPluginError",
]
