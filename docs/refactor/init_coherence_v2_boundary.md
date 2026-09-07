# init_coherence_v2.py — Refactor Boundary Design

## Status: SPRINT 6 THIRD EXTRACTION COMPLETE

## Module Stats

- File: `novel_forge/pipeline/long/services/init_coherence_v2.py`
- Current lines: 4392
- Primary entry: `run_init_coherence_v2_gate(ctx, *, stage, repair_artifact, profile, artifacts, focus_chapters=None, allow_focus_chunking=True)`
- Candidate retrieval entry: `retrieve_init_conflict_candidates(ctx, claims, *, stage="", profile=None)`
- Caller: `init_orchestrator.py` init_long path
- Highest-risk property: artifact-producing init_long core logic with claim ledger, conflict candidates, adjudication reports, and repair coupling.

## Sibling Files

| File | Role | Status |
| --- | --- | --- |
| `init_coherence.py` | v1 API and repair helpers | Parallel system, not a rollback anchor |
| `init_coherence_entrypoints.py` | entrypoint re-exports | Extracted |
| `init_coherence_claim_cache.py` | claim batch caching | Extracted |
| `init_coherence_cognitive_filters.py` | cognitive conflict filters and candidate-kind rules | Extracted in Sprint 6 |
| `init_coherence_auto_repair.py` | auto repair logic | Extracted |
| `init_coherence_extract.py` | claim extraction orchestration | Extracted in Sprint 6 |
| `init_coherence_profile.py` | profile utilities | Extracted |
| `init_coherence_report_merge.py` | report merge helpers | Extracted |
| `init_coherence_retrieval_exact.py` | exact conflict candidate retrieval | Extracted in Sprint 6 |

## Stable Public Interface

| Function | Signature |
| --- | --- |
| `run_init_coherence_v2_gate` | `(ctx, *, stage, repair_artifact, profile, artifacts, focus_chapters=None, allow_focus_chunking=True) -> dict` |
| `retrieve_init_conflict_candidates` | `(ctx, claims, *, stage="", profile=None) -> tuple[list, bool, dict]` |

## Report Payload Keys

These keys must remain stable after refactoring:

- `schema_version`, `stage`, `artifact`
- `extracted_claims_count`, `active_claims_count`, `ledger_active_claims_count`
- `retrieval_scope`, `exact_candidate_count`, `semantic_candidate_count`
- `post_filter_drop_count`, `fully_pushed_down_ratio`
- `zvec_backend`, `semantic_skipped_reason`
- `claim_ledger_path`, `artifact_hashes`, `focus_chapters`
- `candidate_count`, `adjudicated_candidate_count`
- `verdict`, `blocked`, `issues`, `summary`

## Sprint 5 Stabilization Result

The Sprint 4 blocker is cleared:

- `MockAdapter` now returns `audit_v2` fields for `ADJUDICATE_INIT_CONFLICT_CANDIDATES`.
- `_ContractCoherenceResponse` includes `schema_version`, `dimension`, `score`, and `metadata`.
- `apply_task_response_defaults()` backfills safe audit defaults for init coherence adjudication tasks.
- The mock init_long e2e snapshot is enabled and passes.

Current baseline command:

```bash
.venv/bin/python -m pytest tests/regression/test_init_coherence_v2_behavior_snapshot.py -q
```

Expected current result: `14 passed`.

## Sprint 6 Extraction Result

Claim extraction moved behind a sibling module without changing private wrapper names:

- `init_coherence_extract.py` owns `extract_stage_claims()` and `extract_claims()`.
- `init_coherence_v2.py` keeps `_extract_stage_claims()` and `_extract_claims()` as compatibility wrappers.
- Dependencies that still live in `init_coherence_v2.py` are passed through `ClaimExtractionDeps`.
- Existing tests that import the private wrappers continue to pass.

Exact candidate retrieval was then moved behind the same wrapper pattern:

- `init_coherence_retrieval_exact.py` owns `retrieve_exact_candidates()`.
- `init_coherence_v2.py` keeps `_retrieve_exact_candidates()` as the compatibility wrapper.
- Cognitive/key helper ownership remains in `init_coherence_v2.py` and is passed through `ExactRetrievalDeps`.

Cognitive filtering and candidate-kind rules now use the same wrapper pattern:

- `init_coherence_cognitive_filters.py` owns semantic cognitive post-filtering, cognitive regression, awareness conflict, action-level conflict, and foreshadow/reveal checks.
- `init_coherence_v2.py` keeps the original private helper names as wrappers.
- Key normalization and chapter lookup ownership remains in `init_coherence_v2.py` through `CognitiveFilterDeps`.

## Remaining Extraction Candidates

| Responsibility | Functions | Suggested target |
| --- | --- | --- |
| Semantic candidate retrieval | `_retrieve_semantic_candidates` | `init_coherence_retrieval_semantic.py` |
| Adjudication | `_adjudicate_candidates` | `init_coherence_adjudicate.py` |
| Chunked focus recheck | `_split_focus_chapters_for_recheck`, `_run_chunked_focus_recheck` | `init_coherence_chunked_recheck.py` |

## Required Gates

Run these after every init coherence extraction:

```bash
.venv/bin/python -m pytest tests/regression/test_init_coherence_v2_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/unit/test_gateway_adapters.py tests/unit/test_llm_helpers_defaults.py tests/unit/test_format_contracts.py::test_init_coherence_llm_contracts_match_response_schemas tests/unit/test_contract_models.py -q
.venv/bin/python -m ruff check novel_forge/pipeline/long/services/init_coherence_v2.py novel_forge/pipeline/long/services/init_coherence_*.py tests/regression/test_init_coherence_v2_behavior_snapshot.py
```

For extraction that changes adjudication or repair payloads, also run:

```bash
.venv/bin/python -m pytest tests/unit/test_init_coherence_patch.py -q --timeout=120
```

## Refactor Rule

Extract one functional group per PR. Preserve report payload keys and artifact paths before optimizing internals. Do not use old `init_coherence.py` as behavior-equivalence evidence; use mock init e2e artifacts, schema tests, and artifact hashes.
