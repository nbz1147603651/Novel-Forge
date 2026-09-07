# Observability Layer — AGENTS.md

## Project Logger

`project_logger.py` provides `ProjectRunLogger` for per-run structured logging.
Each run logs to `logs/<run_id>/` with events, model calls, summaries, and two
ordinary-Python-log projections:

- `application.jsonl`: INFO+ JSON Lines with correlation fields
  (`run_id`, `project_id`, `command`, `request_id`, plus step/task when known).
- `python.log`: human-readable WARNING+ logs for quick inspection.

Entry points must wrap a workflow in `with run_logger.activate():`. This both
binds context for normal `logging` calls and makes `get_project_logger()` safe
under concurrent API/desktop runs. Never use a process-global logger pointer
for per-run events.

### Module-level access

Lower layers (memory, narrative_state) can emit structured events without
holding a reference to a `ProjectRunLogger`:

```python
from novel_forge.obs.project_logger import get_project_logger, set_project_logger

# Preferred: scopes the logger and automatically restores parent context.
with run_logger.activate():
    # Emit from anywhere in this async task:
    logger = get_project_logger()
    if logger is not None:
        logger.log_event("my.event", {"key": "value"})

# `set_project_logger()` remains available for compatibility. If it is used
# directly, keep its returned ContextVar token and reset it in `finally`.
```

The helper `_safe_log_event()` in `memory/humanize_library_store.py` wraps
this pattern with try/except — never raises.

## Humanize Library Events

All `humanize_library.*` events are emitted via `_safe_log_event()` in
`memory/humanize_library_store.py` and `memory/humanize_retrieval.py`.

| # | Event name | Payload | Emitter | When |
|---|------------|---------|---------|------|
| 1 | `humanize_library.initialized` | `{path, total_entries, regex_count, llm_only_count, schema_version}` | `HumanizeLibrary.from_path()` / `in_memory()` | After successful DB open + schema init |
| 2 | `humanize_library.unavailable` | `{path, error_type, error_msg}` | `HumanizeLibrary.from_path()` | On DB open failure (before re-raise) |
| 3 | `humanize_library.degraded` | `{path, reason, fallback_mode}` | `HumanizeLibrary.vec_search()` | Zvec search raises / unavailable |
| 4 | `humanize_library.seed_completed` | `{added, skipped, total}` | `seed_builtin_patterns()` | After builtin patterns seeded |
| 5 | `humanize_library.migration_applied` | `{from_version, to_version, entries_migrated}` | `HumanizeLibrary.run_migrations()` | After migration steps applied |
| 6 | `humanize_library.lock_acquired` | `{operation, duration_ms}` | `HumanizeLibrary.library_lock()` | After fcntl lock acquired |
| 7 | `humanize_library.lock_timeout` | `{operation, waited_ms}` | `HumanizeLibrary.library_lock()` | Lock timeout exceeded (before raise) |
| 8 | `humanize_library.entry_added` | `{pattern_id, source, project_id}` | `HumanizeLibrary.add()` | After successful entry insert |
| 9 | `humanize_library.entry_bumped` | `{chapter, count, pattern_ids}` | `HumanizeLibrary.bump_hits_from_report()` | Aggregate after bulk bump |
| 10 | `humanize_library.retrieval.invoked` | `{chapter, query_sentences, top_k}` | `HumanizeLibraryRetriever.retrieve()` | Before retrieval starts |
| 11 | `humanize_library.retrieval.completed` | `{chapter, fts_hits, vec_hits, union_hits, duration_ms, normalization_method}` | `HumanizeLibraryRetriever.retrieve()` | After successful retrieval |
| 12 | `humanize_library.retrieval.failed` | `{chapter, error_type, duration_ms, fallback}` | `HumanizeLibraryRetriever.retrieve()` | Exception during retrieval |
| 13 | `humanize_library.rebuild_progress` | `{processed, total, signature_mismatched, checkpoint_path}` | `HumanizeLibrary.rebuild_vectors()` | After each batch checkpoint |

### Safety guarantees

- All events are wrapped in try/except — emission failure never blocks the caller
- No chapter_text or example_phrases in payloads (summaries + counts only)
- Bulk operations emit one aggregate event, not one per item
- Events are synchronous (no asyncio needed)
