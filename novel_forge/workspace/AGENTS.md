# Workspace Layer AGENTS.md

## Role

Thin orchestration bridging CLI/API/Desktop to pipeline. Shared entry point for all three interfaces.

## Workflow Reading and Synchronization

Before changing execution, sessions, planning, revision or exports, read the [current workflow](../../docs/novel-workflow-current.md) and [author-control design](../../docs/novel-authoring-control-design.md). Update their affected N/S steps and D status in the same change, with cross-frontend effects and validation.

Pay special attention to N10–N12: direct `execute_run_chapter` and prepare/resolve/autorun now share `execution_post_archive.complete_chapter_archive`, after the required finalization barrier. B3 will move planning into durable independent jobs. A hard planning waterline is not human approval. Reuse existing finalization manifests, planning revisions and job/autorun recovery; do not build a second scheduler or treat recommended/default decisions as author authorization. Changes to effective scope, defaults, progress or stop behavior must propagate through API/contracts and both affected desktop consumers without silently widening saved permissions.

## Key Files

| File | Role |
|------|------|
| `execution.py` | FACADE - re-exports from execution_* submodules. Stable API, implementation evolves separately. |
| `helpers/execution_runners.py` | `execute_run_chapter`, `execute_init_long`, etc. - async orchestration functions |
| `helpers/execution_state.py` | Execution state management |
| `helpers/execution_io.py` | File I/O operations |
| `helpers/execution_helpers.py` | Helper functions; dataclasses.replace pattern for frozen types |
| `execution_polish.py` | Polish execution logic |
| `execution_repair.py` | Repair execution logic |
| `execution_export.py` | Export execution logic |
| `book_ops/execution_book_entry.py` | Compatibility entry for whole-book consistency audit + global repair queue; preserves legacy import/patch paths. |
| `book_ops/execution_book_audit_runner.py` | Active read-only whole-book consistency audit runner. |
| `book_ops/execution_book_audit_report.py` | Versioned/canonical whole-book audit report persistence helpers. |
| `book_ops/execution_book_audit_helpers.py` | Shared whole-book audit pure helpers and payload normalization. |
| `book_ops/execution_book_audit_post_repair.py` | Post-repair evidence checks, targeted audit, and staleness marker refresh. |
| `book_ops/execution_book_audit_legacy.py` | Thin compatibility adapter: delegates the retired composite entry to the active read-only audit and repair-queue workflows. |
| `book_audit_checkpoint_store.py` | Central access wrapper for existing audit/verify/repair checkpoint files and recovery snapshots. |
| `book_ops/execution_book_consistency.py` | Deprecated compatibility facade for old imports. |
| `regression_detector.py` | **RegressionDetector** — pure rule-based regression detection after chapter repairs (entity/state/narrative regressions). NO LLM calls. |
| `propagation_validator.py` | **PropagationValidator** — pure rule-based propagation validation ensuring repair changes reflect in subsequent chapters. NO LLM calls. |
| `audit_quality_metrics.py` | **AuditQualityMetrics** — frozen dataclass encapsulating audit quality metrics (coverage, miss rate, dimension scores). |
| `runtime.py` | RuntimeServices SINGLETON - dependency injection container |
| `projects.py` | Project views and overviews |
| `sessions/chapter_sessions.py` | Chapter session main entry |
| `sessions/chapter_session_handlers.py` | Checkpoint handlers - uses dataclasses.replace for PendingChapterReviewState |
| `sessions/chapter_session_state.py` | Session state; PendingChapterReviewState is frozen |
| `sessions/chapter_session_results.py` | Result handling |
| `execution_planning_horizon.py`, `planning_horizon.py` | Entry preflight, bounded progressive hardening, isolated strict synchronization and publication |
| `execution_future_planning.py`, `execution_extend_outline.py` | Future candidates versus completing/extending the book target; keep these semantics separate |
| `memory_contracts.py` | Memory system contracts |
| `result_payloads.py` | Result payload definitions |
| `contracts.py` | Request/response contract definitions |

## Critical Patterns

**FACADE pattern**: `execution.py` imports and re-exports from `execution_*` submodules. Never modify facade directly - update implementation in submodules.

**SINGLETON**: `RuntimeServices` is the DI container providing:
- `settings` - configuration
- `model_router` - ModelRouter instance
- `prompt_builder` - PromptBuilder instance
- `storage` - FileSystemStorage instance
- Runner factories

**FROZEN DATACLASSES**: `PendingChapterReviewState` is `@dataclass(frozen=True)`.
```python
# WRONG - raises FrozenInstanceError
state.field = new_value
# RIGHT
import dataclasses
state = dataclasses.replace(state, field=new_value)
```

## Entry Point Flow

```
CLI/API/Desktop → workspace/ → pipeline/steps/
```

All three interfaces route through this layer before reaching pipeline.

## Anti-Patterns

- **DON'T** modify `execution.py` directly - it's a re-export shim
- **DON'T** bypass RuntimeServices - it's the single source of truth for services
- **DON'T** mutate frozen dataclasses - use `dataclasses.replace()`
- **DON'T** add business logic here - it belongs in `pipeline/`

## Async Conventions

Execution functions are async and handle retry/tracing/logging at workspace level. 

## Repair Quality Assurance Modules

Three modules work together to ensure repair operations don't introduce new problems:

### RegressionDetector (`regression_detector.py`)

Pure rule-based detector that compares original vs repaired chapter text to find:
- **Entity regression**: new entities (character/item names in quotes) not in canon state
- **State regression**: character state contradictions (death, relationship, location) with canon
- **Narrative regression**: unresolved foreshadowing or new conflicts introduced without later resolution

**Key patterns:**
- Uses Chinese regex patterns for foreshadowing (`伏笔`, `暗示`, `总有一天`), conflict (`矛盾`, `背叛`, `阴谋`), death (`死`, `亡`, `牺牲`), relationships (`父子`, `兄弟`, `师徒`)
- Extracts entities via quoted name patterns (2-6 Chinese characters in `「」""''『』`)
- Cross-chapter checks: scans subsequent chapters for narrative element resolution
- Output: `list[RegressionIssue]` with type, severity, evidence, affected chapters

**Trigger:** Called in `execution_repair.py` immediately after a chapter repair completes.

### PropagationValidator (`propagation_validator.py`)

Pure rule-based validator that ensures repair changes propagate correctly to subsequent chapters:
- **Entity state propagation**: entity state changed in repaired chapter but not reflected in later chapters
- **Reference broken**: references to repaired content now invalid (e.g., character name removed but later chapters still reference it)
- **Timeline shift**: temporal markers inconsistent between repaired and subsequent chapters

**Key patterns:**
- Extracts entities via same quoted-name regex as RegressionDetector
- Extracts temporal markers (`次日`, `第X天`, `清晨`, `之后`, `已经`, `刚刚`)
- Compares entity sets between original and repaired text, then checks subsequent chapters
- Output: `list[PropagationIssue]` with type, severity, source chapter, affected chapters, suggested action

**Trigger:** Called after RegressionDetector in the repair flow, forming a double-check pipeline.

### AuditQualityMetrics (`audit_quality_metrics.py`)

Frozen dataclass (`@dataclass(frozen=True)`) that encapsulates quality metrics from book consistency audits:

```python
@dataclass(frozen=True)
class AuditQualityMetrics:
    coverage_ratio: float = 0.0          # 0.0-1.0
    estimated_miss_rate: float = 0.0     # 0.0-1.0
    dimension_scores: dict[str, float]   # naming, timeline, worldbuilding, character_state, plot_thread, narrative_drift
    audit_depth: str = "quick"           # quick | full | detailed
    total_issues_found: int = 0
    critical_issues_found: int = 0
```

**Usage:** Returned as `BookConsistencyResult.quality_metrics` field. Downstream systems use `coverage_ratio` and `audit_depth` to decide if deeper audit is needed.

### Anti-Patterns for Repair QA

- **DON'T** add LLM calls to RegressionDetector or PropagationValidator — they are intentionally rule-based for speed and determinism
- **DON'T** mutate AuditQualityMetrics — it's frozen, use constructor or `dataclasses.replace()`
- **DON'T** skip propagation checks after regression detection — both are needed for complete repair validation. Pipeline steps receive pre-processed inputs.
