# Status After Sprint 5

## Summary

Sprint 5 was changed from a new-capability sprint to a stabilization and evidence sprint. The goal is to make the Sprint 1-4 refactor track maintainable and testable before adding Docker, PyInstaller, or broad structured-output dependencies.

## Completed Stabilization

| Area | Result |
| --- | --- |
| init coherence e2e blocker | Cleared by aligning `MockAdapter`, response schema, and defaults for `ADJUDICATE_INIT_CONFLICT_CANDIDATES` |
| init coherence snapshot | `tests/regression/test_init_coherence_v2_behavior_snapshot.py` now runs the mock init_long e2e path |
| contract-to-model spike | Added isolated `novel_forge/core/contract_models.py` |
| structured-output pilot tests | Added coverage for `ADJUDICATE_INIT_CONFLICT_CANDIDATES`, `REPAIR_INIT_ARTIFACT_PATCH`, and `REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS` |
| boundary docs | Updated memory, desktop, and init coherence boundary docs to actual post-refactor state |
| safe refactor runbook | Added `docs/refactor/how_to_refactor_safely.md` |

## Current Large Module State

| Module | Current lines | Baseline lines | Status |
| --- | ---: | ---: | --- |
| `novel_forge/memory/integration.py` | 3351 | 3935 | Partially extracted, facade preserved |
| `novel_forge/desktop/window.py` | 4597 | 4921 | First-round extraction complete, shell still large |
| `novel_forge/pipeline/long/services/init_coherence_v2.py` | 4828 | 4828 | Safety net stabilized, extraction still pending |

## Extracted Modules

Memory siblings:

- `integration_io.py`
- `integration_prompt_context.py`
- `integration_serializers.py`
- `integration_expression.py`
- `integration_finalize.py`
- `integration_canon.py`
- `integration_motif.py`
- `integration_summary.py`
- `integration_config.py`
- `integration_utils.py`

Desktop siblings:

- `window_navigation.py`
- `window_jobs.py`
- `window_runnables.py`
- `window_widgets.py`
- `registry.py`

Init coherence siblings:

- `init_coherence_entrypoints.py`
- `init_coherence_claim_cache.py`
- `init_coherence_auto_repair.py`
- `init_coherence_profile.py`
- `init_coherence_report_merge.py`

## Stable Interfaces

Do not change these without a separate migration plan:

- `MemoryContext` public methods listed in `docs/refactor/memory_integration_boundary.md`
- `NovelForgeDesktopWindow`, `MainWindow`, `switch_page`, `closeEvent`, `_pre_close_cleanup`
- `run_init_coherence_v2_gate(...)`
- `retrieve_init_conflict_candidates(...)`
- `TaskFormatContract` as the source of truth for prompt/runtime format contracts
- `gateway/router.py` as the model-call entry path

## Remaining High-Risk Work

| Area | Risk | Next step |
| --- | --- | --- |
| `init_coherence_v2.py` claim extraction | Medium-high | Extract only after e2e snapshot is green |
| semantic candidate retrieval | High | Preserve zvec fallback and skipped reason fields |
| cognitive post-filters | High | Keep report counters stable |
| adjudication | High | Preserve audit_v2 payload and defaults |
| chunked focus recheck | High | Preserve focus chapter report merge behavior |
| `desktop/window.py` cleanup | Medium | Extract session/actions/cleanup one block at a time |
| memory retrieval/compression | Medium-high | Extract retrieval facade before compression |

## Required Gates

Minimum Sprint 6 start gate:

```bash
.venv/bin/python -m pytest tests/regression/test_memory_context_behavior_snapshot.py -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/test_window_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/regression/test_init_coherence_v2_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/unit/test_contract_models.py tests/unit/test_gateway_adapters.py tests/unit/test_llm_helpers_defaults.py tests/unit/test_format_contracts.py::test_init_coherence_llm_contracts_match_response_schemas -q
```

## Structured Output Recommendation

Keep `contract_to_model()` as an internal experiment until it proves value across more tasks. The next iteration should test whether generated models can feed adapter-level schema hints while still preserving:

- existing router policy
- response repair
- cost tracking
- provider-specific behavior
- domestic model compatibility

Instructor remains optional adapter-layer experimentation, not a main-path dependency.

## Explicit Non-Goals

Do not spend the next stabilization cycle on:

- Docker Compose full-stack packaging
- PyInstaller three-platform artifacts
- full Instructor migration
- replacing `gateway/router.py`
- replacing `TaskFormatContract`
- rewriting the long pipeline in LangGraph or Temporal
