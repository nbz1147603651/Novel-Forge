# Memory Layer AGENTS.md

## Role

Episodic memory, motif tracking, multi-granularity summaries, adaptive compression, and independent validation (CriticAgent). Provides semantic retrieval and state-aware context for the pipeline.

## Key Files

| File | Role |
|------|------|
| `critic.py` | **CriticAgent** — independent validation agent for single-chapter quality audits. Uses LLM calls with EpisodicMemory + MotifTracker access. Outputs `CritiqueReport`. |
| `episodic.py` | Episodic memory with vector-backed semantic retrieval |
| `motif.py` | Motif tracker for recurring themes/symbols |
| `summary.py` | Multi-granularity summaries (scene/chapter/volume/arc) |
| `compression.py` | Adaptive compression for context window management |
| `compression_enricher.py` | Enriches compressed context with key details |
| `audit_coordinator.py` | Coordinates audit operations across memory subsystems |
| `base.py` | Base classes and interfaces |
| `vector_store.py` | Vector store backend factory for semantic search |
| `zvec_store.py` | Production vector store implementation |
| `integration.py` | Integration utilities |
| `migration.py` | Memory migration utilities |

## CriticAgent vs BookConsistencyStep — Responsibility Boundary

**This is a common source of confusion. Read carefully.**

| Aspect | CriticAgent (`memory/critic.py`) | BookConsistencyStep (`pipeline/steps/book_consistency_step.py`) |
|--------|----------------------------------|----------------------------------------------------------------|
| **Granularity** | Single chapter | Entire book (all chapters) |
| **Trigger** | Automatic, after each chapter generation | Manual/CLI, or post-repair verification |
| **Location** | `memory/` layer — has EpisodicMemory + MotifTracker access | `pipeline/steps/` — inherits `PipelineStep` |
| **Method** | LLM-based multi-task audit (5 concurrent checks) | LLM-based batch audit with chapter grouping |
| **Output** | `CritiqueReport` (issues, score, strengths, warnings) | `BookConsistencyResult` (issues, repair plan, quality metrics) |
| **Memory Access** | YES — semantic retrieval via EpisodicMemory | NO — operates on provided summaries/snapshots |
| **Caching** | LRU cache (default 24 entries) | No caching |
| **Pipeline Stage** | `quality_checks` stage in long-form loop | Standalone step, invoked from workspace layer |
| **Issue Pool** | Produces issues that feed into `chapter_issue_pool` | Consumes `chapter_issue_pool` as input context |

**Key rule:** CriticAgent is the **per-chapter quality gate**. BookConsistencyStep is the **global consistency auditor**. They are complementary, not redundant. CriticAgent catches issues early (per chapter); BookConsistencyStep catches cross-chapter patterns that single-chapter analysis misses.

## Critical Patterns

**CRITICAGENT CACHING:** CriticAgent uses an LRU cache keyed by chapter text hash. Cache is automatically invalidated when chapter text changes.

**CONCURRENT CHECKS:** CriticAgent runs 5 checks concurrently via `asyncio.gather`:
1. Continuity check (cross-chapter)
2. Character consistency check
3. Causal chain check
4. Thematic coherence check
5. Alignment check (optional, requires `chapter_plan`)

**EPISODIC MEMORY:** Provides semantic retrieval of past events. CriticAgent uses this to find relevant context for continuity checks.

**MOTIF TRACKER:** Tracks recurring themes/symbols across chapters. CriticAgent uses this for thematic coherence checks.

**CONTEXT OWNERSHIP:** Runtime chapter prompts use fixed structured facts from
StoryKernel/source slices, one L1 macro-summary route, and purpose-specific Zvec
evidence. Do not re-inject episodic, motif, character, or summary copies through
multiple memory layers. Retrieval `top_k` and token budgets belong at the Zvec
evidence boundary; consumers must not apply a second `[:N]` cut.

**LOSSLESS COMPRESSION COVERAGE:** Adaptive compression processes overlong input
as complete provider-safe chunks, lets the LLM compress each chunk, hierarchically
merges the results, and verifies every source chunk. Gateway failure preserves the
source block; local code must not choose story facts or fall back to prefix
truncation.

## Anti-Patterns

- **DON'T** use CriticAgent for book-wide audits — it's single-chapter only
- **DON'T** use BookConsistencyStep for per-chapter quality gates — it's designed for batch analysis
- **DON'T** bypass EpisodicMemory when calling CriticAgent — it needs memory context for accurate continuity checks
- **DON'T** confuse CriticAgent with RegressionDetector — CriticAgent uses LLM calls for deep analysis; RegressionDetector is pure rule-based for speed

## Async Conventions

All memory operations are async. CriticAgent's `critique_chapter()` is the main entry point and handles its own concurrency internally.

## Zvec Vector Store

**Current Version**: v0.5.0 (upgraded 2026-06-15)

| Component | File | Purpose |
|-----------|------|---------|
| ZvecVectorStore | `zvec_store.py` | Vector store adapter backed by Zvec (HNSW/IVF/FLAT/RabitQ/DiskANN indexes) |
| StoryKernelZVec | `../story_kernel/zvec_layer.py` | Optional semantic enhancement for StoryKernel entities/knowledge/motifs |
| Migration | `migration.py` | Data migration from JSON to Zvec collection |

**Configuration**:
- `NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND` — `zvec` (default/production) / `in_memory` (tests/mock only)
- `NOVEL_FORGE_MEMORY_ZVEC_INDEX_TYPE` — `hnsw` (default) / `ivf` / `flat` / `hnsw_rabitq` / `diskann`
- `NOVEL_FORGE_MEMORY_ZVEC_MEMORY_LIMIT_MB` — Soft memory cap (default: 512)

**Key behavior**: Real episodic memory requires Zvec and fails fast when the dependency or project-scoped collection path is unavailable. Mock embeddings force `in_memory` so tests do not need a durable vector collection.

**Version notes**: v0.5.0 adds native FTS, hybrid retrieval, DiskANN, and output-field selection for fetch. The adapter is zvec 0.5+ only: it uses the unified `Query` API, stores a lightweight `content` FTS field, and exposes `text_search()` / `hybrid_search()` alongside the historical vector `search()`.

## HumanizeLibrary (拟人化库)

Global cross-project SQLite + FTS5 + Zvec pattern library for AI-writing-style detection. Stores builtin patterns plus user-defined and imported entries. Used by the Humanize layer in the long-form chapter pipeline to召回 AI 写作痕迹并定向改写。

| File | Role |
|------|------|
| `humanize_library_store.py` | SQLite + FTS5 + fcntl lock + migration + export/import + vector rebuild |
| `humanize_retrieval.py` | Sentence splitting, BM25 scoring, vector search, union fuse, LRU embedding cache |
| `../core/humanize_rule_catalog.py` | Shared executable builtin catalog used by novel, dubbing, and persisted library metadata |
| `../core/schemas/humanize_library.py` | `HumanizeLibraryEntry` Pydantic schema, `LIBRARY_BUILTIN_ENTRIES` (30 entries), `LIBRARY_SCHEMA_VERSION = "2.0"` |
| `../pipeline/long/stages/humanize_layer.py` | Pipeline integration — calls retriever, falls back to 21 hard rules on failure |
| `../cli/commands/humanize_library.py` | CLI: list / add / remove / enable / disable / find-duplicates / merge / export / import / stats |

### Public API

- `HumanizeLibrary.from_default_path()` / `from_path(path)` / `in_memory()`
- CRUD: `list_all()`, `get(pid)`, `add(entry)`, `update(pid, ...)`, `remove(pid)`, `enable(pid)`, `disable(pid)`
- Stats: `bump_hit(pid, chapter, score, source)`, `bump_hits_from_report(report, chapter)`, `stats()`, `is_healthy()`
- Cross-machine: `export() -> bytes`, `import_archive(blob, merge_strategy)` — merge_strategy: `skip` / `overwrite` / `merge`
- Vector: `vec_search(vec, top_k)`, `rebuild_vectors(embedder, *, resumable=True, batch_size=32)`
- Schema: `run_migrations() -> MigrationReport`
- Retrieval: `HumanizeLibraryRetriever.retrieve(library, chapter_text, top_k)` — deterministic matches are exhaustive with exact spans; `top_k` applies per window only to semantic candidates
- Seeding: `seed_builtin_patterns(lib) -> int` — idempotent, returns count added

### Exceptions

| Exception | When |
|-----------|------|
| `LibraryError` | Base exception |
| `LibraryUnavailableError` | DB cannot be opened |
| `LibraryDegradedError` | Subsystem (e.g. Zvec) failed but library is usable |
| `LibraryReadOnlyError` | Attempting to mutate a builtin entry |
| `LibraryDuplicateError` | Adding entry with existing pattern_id |
| `LibraryLockTimeoutError` | fcntl lock not acquired within timeout (default 30s) |
| `LibrarySchemaVersionMismatchError` | On-disk schema version newer than code supports |

### Embedding Signature

`EmbeddingSignature.compute(provider, model, dimension)` returns `sha256(f"{provider}:{model}:{dimension}")` hex digest. Used to detect when the embedding model has changed and vectors need rebuilding. Entries with mismatched signatures are marked `vector_stale=True`.

### Retrieval Flow

`HumanizeLibraryRetriever.retrieve()` first scans every enabled executable regex,
example phrase, and configured keyword conjunction across all paragraphs. It then
runs paragraph-window BM25/Zvec recall for semantic patterns, fuses scores, and
applies `top_k` per window. Deterministic occurrences are never truncated.
The Zvec index is lazily attached/rebuilt by provider/model/dimension signature;
without an attached index, the retriever does not waste embedding calls.

### Degradation Behavior

All failures are caught and degraded gracefully — the library never blocks chapter generation:
- DB unavailable → `LibraryUnavailableError` → Humanize layer falls back to 21 hard-coded rules
- Zvec down → `vec_search()` returns empty → BM25-only retrieval
- Embedder error → batch marked stale, continues with next batch
- Retrieval exception → returns empty list
- Lock timeout → `LibraryLockTimeoutError` after 30s

### Configuration

5 env vars via `Settings`: `humanize_library_enabled` (bool), `humanize_library_path` (str, default empty = `{storage_root}/_global/humanize_library`), `humanize_library_top_k` (int, 1-200, default 30), `humanize_library_sim_threshold` (float, 0.0-1.0, default 0.6), `humanize_library_seed_builtin` (bool, default True).

### Critical Patterns

**BUILTIN IMMUTABILITY**: Entries with `source="builtin"` cannot be updated or removed via `update()` / `remove()`. They can only be enabled/disabled.

**IDEMPOTENT SEEDING**: `seed_builtin_patterns()` checks each pattern_id before inserting. Safe to call multiple times.

**fcntl LOCK SCOPE**: Write operations (`add`, `update`, `remove`, `enable`, `disable`, `bump_hit`, `export`, `import_archive`, `rebuild_vectors`) acquire an exclusive fcntl lock on `library.lock`. Read operations (`list_all`, `get`, `stats`, `fts_search`, `vec_search`) do not lock.

**VECTOR REBUILD CHECKPOINT**: `rebuild_vectors()` writes progress to `rebuild_progress.json` between batches. On restart, already-embedded entries are skipped. Checkpoint is deleted on full success.

### Anti-Patterns

- **DON'T** modify builtin entries — they are read-only by design
- **DON'T** share a `HumanizeLibrary` instance across threads — each thread should open its own connection
- **DON'T** call `rebuild_vectors()` without an embedder — it requires an `EmbedderProtocol` implementation
- **DON'T** assume vector search is always available — Zvec is optional, always check `vec_search()` return for empty
