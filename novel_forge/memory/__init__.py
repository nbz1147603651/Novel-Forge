"""Memory module for Novel Forge.

Provides episodic, semantic, and archival memory systems for enhanced
context coherence and narrative consistency.
"""

from novel_forge.memory.audit_coordinator import AuditContext, AuditCoordinator
from novel_forge.memory.audit_evidence import build_book_audit_evidence_context
from novel_forge.memory.compression import AdaptiveCompressionService, CompressionResult
from novel_forge.memory.critic import CriticAgent, CritiqueIssue, CritiqueReport
from novel_forge.memory.episodic import EpisodicMemory, EpisodicResult
from novel_forge.memory.expression_channel import ExpressionChannelMemory
from novel_forge.memory.humanize_library_store import (
    EmbedderProtocol,
    EmbeddingSignature,
    HumanizeLibrary,
    ImportReport,
    LibraryDegradedError,
    LibraryDuplicateError,
    LibraryError,
    LibraryLockTimeoutError,
    LibraryReadOnlyError,
    LibrarySchemaVersionMismatchError,
    LibraryStats,
    LibraryUnavailableError,
    MigrationReport,
    seed_builtin_patterns,
)
from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever
from novel_forge.memory.integration import MemoryContext, MemoryIntegrationConfig
from novel_forge.memory.migration import (
    MemoryVectorMigrationResult,
    migrate_project_memory_vectors_to_zvec,
)
from novel_forge.memory.motif import (
    Motif,
    MotifOccurrence,
    MotifSuggestion,
    MotifTracker,
    RepetitionWarning,
)
from novel_forge.memory.narrative_evidence import (
    NarrativeEvidenceIndex,
    NarrativeEvidenceService,
)
from novel_forge.memory.summary import MultiGranularitySummaryService, SummaryAtGranularity

__all__ = [
    # Episodic memory
    "EpisodicMemory",
    "EpisodicResult",
    "NarrativeEvidenceIndex",
    "NarrativeEvidenceService",
    "ExpressionChannelMemory",
    # Summary service
    "MultiGranularitySummaryService",
    "SummaryAtGranularity",
    # Compression
    "AdaptiveCompressionService",
    "CompressionResult",
    # Motif tracking
    "MotifTracker",
    "Motif",
    "MotifOccurrence",
    "MotifSuggestion",
    "RepetitionWarning",
    # Critic
    "CriticAgent",
    "CritiqueReport",
    "CritiqueIssue",
    # Integration
    "MemoryContext",
    "MemoryIntegrationConfig",
    "MemoryVectorMigrationResult",
    "migrate_project_memory_vectors_to_zvec",
    # Audit coordinator
    "AuditCoordinator",
    "AuditContext",
    "build_book_audit_evidence_context",
    # Humanize library
    "HumanizeLibrary",
    "HumanizeLibraryRetriever",
    "EmbedderProtocol",
    "EmbeddingSignature",
    "LibraryStats",
    "ImportReport",
    "MigrationReport",
    "LibraryError",
    "LibraryUnavailableError",
    "LibraryDegradedError",
    "LibraryReadOnlyError",
    "LibraryDuplicateError",
    "LibraryLockTimeoutError",
    "LibrarySchemaVersionMismatchError",
    "seed_builtin_patterns",
]
