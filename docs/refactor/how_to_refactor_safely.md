# How to Refactor Safely

This runbook is the refactor gate for the large-module cleanup track. Use it before splitting `memory/integration.py`, `desktop/window.py`, or `pipeline/long/services/init_coherence_v2.py`.

## Rules

1. Keep the public facade import path stable.
2. Move one functional group at a time.
3. Preserve method and function signatures listed in the boundary docs.
4. Prefer existing patterns:
   - memory modules: `ctx: MemoryContext`
   - desktop modules: `owner: NovelForgeDesktopWindow`
   - workspace-style compatibility: thin re-export or thin facade
5. Do not rewrite behavior while extracting. Behavior changes need a separate PR after the extraction baseline is green.
6. If a golden snapshot changes, classify the drift before accepting it:
   - artifact path drift
   - report key drift
   - hash drift
   - text/content drift
   - ordering-only drift
7. Treat pre-existing failures as scoped debt. Do not hide them by weakening unrelated tests.

## Memory Extraction Gate

```bash
.venv/bin/python -m pytest tests/regression/test_memory_context_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/unit/test_contract_models.py tests/unit/test_llm_helpers_defaults.py -q
.venv/bin/python -m ruff check novel_forge/memory tests/regression/test_memory_context_behavior_snapshot.py
```

Escalate to the broader unit suite when retrieval, compression, prompt assembly, or persistence changes:

```bash
.venv/bin/python -m pytest tests/unit/ -q --timeout=60
```

## Desktop Extraction Gate

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/test_window_behavior_snapshot.py -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/ -q --timeout=120
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/*_visual.py -q --timeout=180
.venv/bin/python -m ruff check novel_forge/desktop tests/desktop/test_window_behavior_snapshot.py
```

Before touching page state or worker wiring, read:

```text
novel_forge/desktop/pages/AGENTS.md
```

## Init Coherence Extraction Gate

```bash
.venv/bin/python -m pytest tests/regression/test_init_coherence_v2_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/unit/test_gateway_adapters.py tests/unit/test_llm_helpers_defaults.py tests/unit/test_format_contracts.py::test_init_coherence_llm_contracts_match_response_schemas tests/unit/test_contract_models.py -q
.venv/bin/python -m ruff check novel_forge/pipeline/long/services/init_coherence_v2.py novel_forge/pipeline/long/services/init_coherence_*.py tests/regression/test_init_coherence_v2_behavior_snapshot.py
```

Run patch-specific tests when adjudication, repair scope, or artifact patch payloads change:

```bash
.venv/bin/python -m pytest tests/unit/test_init_coherence_patch.py -q --timeout=120
```

## Structured Output Spike Gate

The Sprint 5 structured-output spike is intentionally isolated in `novel_forge/core/contract_models.py`.

Allowed:

- Build Pydantic models from `TaskFormatContract`.
- Reuse registered response schemas.
- Validate pilot tasks in tests.
- Experiment behind adapter/router boundaries.

Not allowed in this refactor track:

- Bypassing `gateway/router.py`.
- Replacing `validate_json_output_contract()`.
- Making Instructor a hard runtime dependency.
- Mutating `TaskFormatContract` at runtime.

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_contract_models.py tests/unit/test_format_contracts.py::test_init_coherence_llm_contracts_match_response_schemas -q
```

## Commit Checklist

Before committing:

```bash
git diff --check
git status --short
```

The commit should contain one coherent extraction or one coherent stabilization change. Avoid mixing refactors, behavior fixes, and documentation unless the docs describe the exact change.
