# Novel Forge — Pipeline Long-Form Orchestration

Long-form chapter responsibilities: `StatePacket → Bridge → Plan(absorb opening bridge) → DRAFT → optional pre-wave repair → WAVE → Review/bounded repair → optional refinement → final-version verification → archive/required state`. Extraction, evaluation and reports must be refreshed after semantic text changes, not treated as an unconditional post-persist check.

## Workflow Reading and Author Intent

Read [current workflow N06–N12](../../../docs/novel-workflow-current.md) and [author-control design D03–D08](../../../docs/novel-authoring-control-design.md) before changing planning, quality gates, repair or finalization; update the affected entries and validation in the same change. These documents separate existing branches from planned unified authorization.

AI scores and suggestions do not authorize overriding explicit intent, human edits/locks, voice or intentional pacing/repetition. Keep fact/obligation failures distinct from subjective preferences and uncertain interpretations; do not silently weaken existing hard gates either. Preserve bounded repair and last-validated-text rollback. Any changed completion/decision semantics must reach workspace, API/contracts and UI consumers, including checkpoint/autorun recovery paths.

## Architecture

```
loop.py                     # Entry orchestrator (run_long_chapter)
chapter_flow.py             # Compatibility facade
chapter_flow_orchestrate.py # Prepare and coordinate chapter execution
chapter_flow_generate.py    # DRAFT + WAVE handoff
chapter_flow_review.py      # Review and repair orchestration
chapter_flow_finalize.py    # Final-text gates and archive/state handoff
  stages/
    planning.py      # Bridge + plan generation
    draft.py         # DRAFT context + raw prose handoff (v0_draft.md)
    wave.py          # WAVE single-pass scene weaving (v1_wave.md)
    quality_checks.py # Facade; implementation split across quality_checks_* helpers
    finalize.py      # Facade; checks/persist/report/common implementation helpers
    character_intro.py # Auto-introduce new characters
  services/          # Init/context/quality/memory and other focused services
decisions.py         # Pure function decision logic
```

## Six-Phase Pipeline

| Stage | Files | Purpose |
|-------|-------|---------|
| Planning | `chapter_flow_orchestrate.py` + `stages/planning.py` | Build context, bridge, absorb opening anchors into plan, validate inputs |
| Generate | `chapter_flow_generate.py` + `stages/draft.py`, `stages/wave.py` | DRAFT writes raw prose, WAVE produces the reviewable handoff |
| Review | `chapter_flow_review.py` + `stages/quality_checks*` + domain repair | Quality checks and bounded repair loops |
| Polish | Review/finalize orchestration and stage helpers | Optional post-repair polish and refreshed audits |
| Humanize | Final refinement orchestration + `stages/humanize_layer.py` | Configured AI-flavor cleanup, repair and final verification |
| Finalize | `chapter_flow_finalize.py` + `stages/finalize_*` | Same-version gates, reports, persistence and required state |

## Key Files

- `loop.py`: Orchestrator entry, memory gap compensation, style trend tracking, budget emission
- `chapter_flow.py`: Facade; sequence, integrity and rollback live in `chapter_flow_{orchestrate,generate,review,finalize}.py`
- `decisions.py`: Pure functions — `RepairDecision`, `QualityGateDecision`, `RollbackDecision`
- `repair_dimensions.py`: Declarative per-dimension coordination policies (`RepairDimensionPolicy`) for the chapter repair chain — total-cap gating + outcome absorption; add new dimensions as policy constants, not inline scaffolding
- `execution_models.py`: `ChapterExecutionContext`, `PreparedChapterArtifacts`, `ChapterReviewArtifacts`
- `context.py`: `StoryMemoryManager` — assembles `ChapterStatePacket` from canon/reports
- `preflight.py`: `LongProjectBundle` loading, schema validation, stale chapter detection
- `repair.py` / `repair_causal.py`: Thin wrappers delegating to pipeline steps

## Critical Conventions

- **No business logic in loop.py** — orchestrator only, delegates to chapter_flow
- **decisions.py must stay pure** — no side effects, injectable thresholds, unit-testable
- **Services call services indirectly** — go through orchestrator, not each other
- **Frozen dataclasses** — use `dataclasses.replace()`, never direct assignment

## Decision Functions (decisions.py)

| Decision | Returns | Governs |
|----------|---------|---------|
| `decide_repair_continuation` | `RepairVerdict` | Continue/exhaust/rollback/rollback_continue |
| `decide_repair_strategy` | `StrategyRecommendation` | patch/fulltext/rewrite |
| `decide_post_repair_action` | `PostRepairAction` | Skip recheck tiers |
| `decide_quality_gate_action` | `QualityGateAction` | Accept/block_for_replan |
| `decide_causal_regression_action` | `CausalRegressionAction` | Skip/skip_chapter_repair/full_recheck |
| `decide_rollback` | `RollbackVerdict` | Keep/rollback_regression/rollback_drift |
| `decide_best_effort_accept` | `BestEffortVerdict` | Accept best-effort after exhaustion |
| `decide_patch_routing` | `bool` | Whether an issue qualifies for patch (vs fulltext) routing |
| `decide_cross_dimension_action` | `CrossDimensionAction` | Cross-dimension coordination across repair loops |
| `derive_dynamic_repair_policy` | `DynamicRepairPolicy` | Per-round budget/threshold derived from issue mix + rollback history |
| `build_repair_thresholds` | `RepairThresholds` | Static + dynamic threshold bundle for a repair run |

## Unique Patterns

- **Memory gap compensation**: Failed memory updates write marker files; next chapter detects and retries
- **Element progress tracking** (`element_progress.py`): hit/weak/miss per chapter, rule-based + optional LLM arbitration
- **Style trend detection**: Monitors style scores across last 4 chapters, warns if decline > 1.5 points
- **Opening guard patch**: Pre-quality high-confidence开场 continuity pre-screening
- **Cross-dimension regression**: Causal repair checked for continuity degradation > 1.5 points

## Bridge / Plan Ownership

Bridge is a lineage and audit artifact for chapter opening continuity. Planning owns executable writing constraints: `absorb_bridge_into_plan()` copies Bridge opening fields into `ChapterPlan.opening_bridge`, reinforces `scene_01`, and persists the absorbed Plan. DRAFT and DRAFT_SCENE must use `stage_cards.plan.opening_bridge`; do not reintroduce `stage_cards.bridge` as a writing-stage execution source.

## Context Ownership and Budgeting

- Fixed current facts and writable state slots come from StoryKernel and the
  chapter source slice. They remain structured and complete; they are not stored
  again as vector memories.
- Dynamic historical evidence is selected once at the Zvec retrieval boundary by
  purpose, chapter waterline, entity/type filters, and token budget. Downstream
  steps consume that ranked result without secondary `[:N]` truncation.
- `StageMemoryBuilder` supplies only L1 macro memory to the layered-memory path.
  L0 fixed cards, L2 episodic history, and L3 semantic evidence already have
  authoritative routes and must not be duplicated there.
- Explicit batch/window controls are valid when they guarantee complete coverage
  across calls (for example whole-book audit batches). Prefix truncation inside a
  selected batch is not valid. On overflow, split or hierarchically summarize;
  never let local code decide which narrative facts are expendable.
- Narrative-state adjudication receives complete current writable slots, current
  relationships/threads, the previous exit state, and unresolved pending items.
  Accepted ledger history and Canon snapshots are not injected again because they
  are already materialized into current state and searchable evidence.

## Services

Initialization implementation lives under `services/init/` (`init_orchestrator`, `init_cache`, `init_context`, `init_contract`, `init_outline_helpers`, `init_outline_batch`); `init_service` remains an entry wrapper. Use the current workflow source index and CodeGraph for relocated helper implementations rather than assuming all services are flat.
LLM: `llm_service`, `llm_helpers`, `bridge_service`
Context: `context_helpers`, `compaction_service`, `outline_helpers`
Quality: `validation_service`, `element_progress`, `reading_power_timeline_window_manager`
Special: `strand_weave`, `style_profile_recovery`, `weave_validation`, `time_validation`, `anchor_terms`, `blueprint_validation`, `motif_prompt_format`, `stage_memory_builder`

## Anti-Patterns

- Do NOT add if/elif decision logic to loop.py or chapter_flow.py — use decisions.py
- Do NOT call service from service — go through orchestrator
- Do NOT modify frozen dataclasses directly — use `dataclasses.replace()`
- Do NOT route raw BridgeCard back into DRAFT/DRAFT_SCENE; use `ChapterPlan.opening_bridge`
