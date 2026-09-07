# Editorial Contract Domain AGENTS.md

## Role

Publication-grade editorial quality controls for the novel generation pipeline. Defines the **EditorialContract** (single source of truth for editorial constraints), provides deterministic local quality metrics, contract validation, expression-signal detection, and structured revision planning.

This module does NOT execute LLM calls. It defines contracts and evaluates prose locally. LLM-mediated editorial audit is handled by `pipeline/steps/book_editorial_audit_step.py`.

## Key Files

| File | Role |
|------|------|
| `schemas.py` | 14 Pydantic schemas: `EditorialContract`, `EditorialFinding`, `EditorialAuditReport`, `CharacterVoiceProfile`, `ClimaxMarker`, `DenouementBudget`, `SymbolPolicy`, `SceneResistanceRule`, `RevelationStep`, `TitlePolicy`, `EditorialRevisionAction`, `EditorialElementDirective`, `EditorialExpressionChannelProfile`, plus `normalize_expression_channel_profiles()` helper. Contains extensive Chinese-English alias maps for LLM output normalization. |
| `cards.py` | `build_editorial_card()` — projects `EditorialContract` into compact prompt cards per pipeline stage. DRAFT stage strips `character_voices` (voices are WAVE's responsibility). |
| `metrics.py` | `chapter_editorial_findings()`, `editorial_metrics_payload()` — deterministic local metrics using regex patterns (no LLM). Detects explanation density, premature resolution, and other prose quality signals. |
| `signals.py` | Expression-signal detection: body language, emotional channels. Uses project-contract-driven profiles (no built-in phrase table). Imports `ExpressionChannelRecord` from `narrative_state.schemas`. |
| `validators.py` | `validate_editorial_contract()` — pre-generation contract structure and budget validation. Enforces structural budgets (denouement ratio, climax placement, confirmation scene caps). |
| `revision_planner.py` | `build_structured_revision_plan()` — converts `EditorialFinding` objects into categorized `EditorialRevisionAction` objects (chapter_merge_plan, element_directive_actions, language_actions, title_actions, paragraph_actions, time_bridge_actions). |

## Critical Patterns

### Stage-Gated Card Projection

`build_editorial_card()` strips `character_voices` for DRAFT stage. Full voice profiles are WAVE's responsibility. Passing full voices to DRAFT causes the model to attempt voice-matching on raw prose before scene weaving, producing worse results.

### Deterministic Local Metrics

`metrics.py` uses regex patterns for local findings:
- Explanation density: `意识到|明白|知道...`
- Premature resolution: `终于|圆满...`

These are intentionally LLM-free for speed and determinism. Do not add LLM calls to metrics. Upgrade to LLM-mediated checking only in the pipeline step layer.

### Contract Validation Budgets

`validators.py` enforces structural budgets:
- Denouement must not exceed 22% of total chapters
- Climax must not be on the final chapter
- Confirmation scenes budget <= 2

### LLM Alias Normalization

`schemas.py` contains extensive Chinese-English alias maps for climax types, explanation policies, expression channels, and editorial severities. These normalize messy LLM output into canonical enum values. Always add new aliases to the existing maps rather than creating new normalization paths.

### Expression Channel System

`signals.py` uses project-contract-driven expression profiles defined in `EditorialContract.expression_channel_budget`. There is no built-in phrase table. Expression channels are validated against `narrative_state.schemas.ExpressionChannelRecord`. The editorial module depends on `narrative_state` schemas but not on the narrative_state store.

## Cross-Module Boundaries

| Module | Relationship |
|--------|-------------|
| `pipeline/steps/book_editorial_audit_step.py` | Consumes `EditorialContract` and `EditorialAuditReport` for LLM-mediated audit |
| `pipeline/long/services/constraint_router.py` | Uses `build_editorial_card()` for stage-gated prompt construction |
| `narrative_state/schemas.py` | `signals.py` imports `ExpressionChannelRecord` |
| `workspace/execution_book_editorial.py` | Entry point that drives editorial audit workflow |
| `editorial/__init__.py` | Re-exports 14 types + 1 helper function for external consumption |

## Anti-Patterns

- Do NOT add LLM calls to `metrics.py` or `signals.py`. These must stay deterministic.
- Do NOT bypass the DRAFT-stage `character_voices` stripping in `build_editorial_card()`.
- Do NOT add new normalization maps outside `schemas.py`. Centralize all alias handling there.
- Do NOT import pipeline step classes from editorial. This module is schema and logic only.
- Do NOT import `narrative_state.store` or `narrative_state.engine` from editorial. Only schemas are used.
