# memory/integration.py — Refactor Boundary Design

## Status: SPRINT 5 STABILIZED

## Module Stats

- File: `novel_forge/memory/integration.py`
- Current lines: 3351
- Original Sprint 1 baseline: 3935 lines
- Primary class: `MemoryContext`
- Refactor style: thin delegating methods on `MemoryContext`, extracted helpers accept `ctx: MemoryContext`

## Stable Public Interface

Do not change these signatures during extraction. They are exercised by the memory behavior snapshot tests and by workspace/runtime callers.

| Method | Entry point for |
| --- | --- |
| `create_from_settings(cls, router, builder, settings, project_id, storage)` | RuntimeServices, workspace |
| `finalize_chapter_memory(chapter_number, text, creative_report_text, *, chapter_result, chapter_plan)` | chapter finalize stage |
| `finalize_volume_memory(volume_number, ...)` | volume boundary handler |
| `get_memory_context_for_prompt(current_chapter, ...)` | sync prompt assembly |
| `aget_memory_context_for_prompt(current_chapter, ...)` | async prompt assembly |
| `index_chapter(chapter_number, text, creative_report_text, *, async_summarize, async_motifs)` | chapter memory indexing |
| `search_relevant_history(query, current_chapter, top_k)` | async retrieval |
| `search_relevant_history_sync(query, ...)` | sync retrieval |
| `warm_start_from_init_artifacts()` | init_long memory warmup |
| `get_memory_status_for_ui()` | Desktop status |
| `get_character_context(character_id, ...)` | character-scoped context |
| `shutdown()` / `aclose()` | lifecycle cleanup |
| `save_to_disk()` / `load_from_disk()` | persistence |
| `flush_pending_tasks(timeout_s)` | pending task draining |
| `set_outline(outline)` | outline binding |
| `set_progress_callback(callback)` | progress reporting |
| `on_volume_boundary(volume_number)` | volume boundary hook |
| `purge_volume_caches(volume_number)` | cache cleanup |

## Extracted Siblings

| File | Responsibility |
| --- | --- |
| `integration_io.py` | persistence save/load and path helpers |
| `integration_prompt_context.py` | prompt context helpers |
| `integration_serializers.py` | artifact serialization helpers |
| `integration_expression.py` | expression observations and profile loading |
| `integration_finalize.py` | episodic finalize indexing |
| `integration_canon.py` | canon state helpers |
| `integration_motif.py` | motif extraction and scheduling |
| `integration_summary.py` | async chapter summary generation |
| `integration_config.py` | configuration helpers |
| `integration_utils.py` | shared utility helpers |

## Remaining Extraction Candidates

| Area | Notes | Suggested target |
| --- | --- | --- |
| Retrieval orchestration | Keep public retrieval methods as facade delegates | `integration_retrieval.py` |
| Compression/adaptive context | Extract only after retrieval facade is stable | `integration_compression.py` |
| Critique context helpers | Preserve prompt text and artifact hashes exactly | `integration_critique_context.py` |
| Warm-start artifact indexing | High impact on init_long baseline | `integration_warm_start.py` |

## Required Gates

Run these after every memory extraction:

```bash
.venv/bin/python -m pytest tests/regression/test_memory_context_behavior_snapshot.py -q
.venv/bin/python -m pytest tests/unit/test_contract_models.py tests/unit/test_llm_helpers_defaults.py -q
.venv/bin/python -m ruff check novel_forge/memory tests/regression/test_memory_context_behavior_snapshot.py
```

For larger changes, also run:

```bash
.venv/bin/python -m pytest tests/unit/ -q --timeout=60
```

## Refactor Rule

`integration.py` remains the compatibility facade. Prefer the established `ctx` parameter pattern over moving state into new classes unless there is a concrete lifecycle reason.
