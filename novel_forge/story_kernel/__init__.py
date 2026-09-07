"""StoryKernel — unified relational field pool for narrative state.

This package provides both the ORM layer (SQLite-backed relational schema)
and the Pydantic validation schemas for the unified field pool.

ORM layer::

    from novel_forge.story_kernel import Base, create_sqlite_engine

    engine = create_sqlite_engine(":memory:")
    Base.metadata.create_all(engine)

Pydantic schemas::

    from novel_forge.story_kernel import StoryKernel, Entity, WorldRule

    kernel = StoryKernel(project_id="my-project")
"""

from __future__ import annotations

from novel_forge.story_kernel.composer import (
    ContextComposer,
    StoryKernelLoader,
    StoryKernelSnapshotStore,
)
from novel_forge.story_kernel.engine import create_sqlite_engine
from novel_forge.story_kernel.field_types import (
    EntityType,
    LedgerVisibility,
    RelationType,
)
from novel_forge.story_kernel.gates import GateResult, InitTruthGate, PreArchiveTruthGate
from novel_forge.story_kernel.init_adapter import InitAdapter, build_kernel_from_init
from novel_forge.story_kernel.merger import (
    StoryKernelMerger,
    merge_character_state_preserving_identity,
)
from novel_forge.story_kernel.orm import (
    AccessLedger as ORMAccessLedger,
)
from novel_forge.story_kernel.orm import (
    Base,
)
from novel_forge.story_kernel.orm import (
    BusinessDependency as ORMBusinessDependency,
)
from novel_forge.story_kernel.orm import (
    Entity as ORMEntity,
)
from novel_forge.story_kernel.orm import (
    KnowledgeLedger as ORMKnowledgeLedger,
)
from novel_forge.story_kernel.orm import (
    MotifProtocol as ORMMotifProtocol,
)
from novel_forge.story_kernel.orm import (
    ObjectLedger as ORMObjectLedger,
)
from novel_forge.story_kernel.orm import (
    PromiseLedger as ORMPromiseLedger,
)
from novel_forge.story_kernel.orm import (
    Relationship as ORMRelationship,
)
from novel_forge.story_kernel.orm import (
    TimelineAnchor as ORMTimelineAnchor,
)
from novel_forge.story_kernel.orm import (
    WorldRule as ORMWorldRule,
)
from novel_forge.story_kernel.schemas import (
    AccessLedger,
    BusinessDependency,
    Entity,
    KnowledgeLedger,
    MotifProtocol,
    ObjectLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    StoryKernelStructuredWarning,
    TimelineAnchor,
    WorldRule,
)
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import CanonStore, StoryKernelStore

__all__ = [
    "AccessLedger",
    "Base",
    "BusinessDependency",
    "CanonStore",
    "ContextComposer",
    "Entity",
    "EntityType",
    "GateResult",
    "InitAdapter",
    "InitTruthGate",
    "KnowledgeLedger",
    "LedgerVisibility",
    "MotifProtocol",
    "ObjectLedger",
    "ORMAccessLedger",
    "ORMBusinessDependency",
    "ORMEntity",
    "ORMKnowledgeLedger",
    "ORMMotifProtocol",
    "ORMObjectLedger",
    "ORMPromiseLedger",
    "ORMRelationship",
    "ORMTimelineAnchor",
    "ORMWorldRule",
    "PromiseLedger",
    "PreArchiveTruthGate",
    "RelationType",
    "Relationship",
    "StoryKernel",
    "StoryKernelLoader",
    "StoryKernelMerger",
    "StoryKernelSnapshotStore",
    "StoryKernelStore",
    "StoryKernelStateWriter",
    "StoryKernelStructuredWarning",
    "TimelineAnchor",
    "WorldRule",
    "build_kernel_from_init",
    "create_sqlite_engine",
    "merge_character_state_preserving_identity",
]
