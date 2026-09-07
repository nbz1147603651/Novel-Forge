# desktop/window.py — Refactor Boundary Design

## Status: SPRINT 5 STABILIZED

## Module Stats

- File: `novel_forge/desktop/window.py`
- Current lines: 4597
- Original Sprint 1 baseline: 4921 lines
- Primary class: `NovelForgeDesktopWindow`
- Compatibility alias: `MainWindow`
- Refactor style: extracted functions/classes use the `owner` pattern and keep Qt signal ownership in the window shell.

## Stable Public Interface

| Member | Entry point for |
| --- | --- |
| `NovelForgeDesktopWindow.__init__(*, ollama_sidecar, ollama_sidecar_result)` | `desktop/main.py::launch_desktop` |
| `MainWindow` | external imports and tests |
| `PAGE_META` | page metadata access |
| `_PAGE_SECTION_MAP` | page binding and snapshot tests |
| `_PREWARM_PAGE_IDS` | prewarming config |
| `_COLD_INSTANT_PAGE_IDS` | cold-start page policy |
| `switch_page(page_id)` | navigation |
| `closeEvent(event)` | Qt close lifecycle |
| `_pre_close_cleanup()` | shutdown protocol |

## Extracted Siblings

| File | Responsibility |
| --- | --- |
| `window_navigation.py` | page ensure/switch/connect behavior |
| `window_jobs.py` | job binding, chapter number, active write job, task decisions |
| `window_runnables.py` | QRunnable worker classes |
| `window_widgets.py` | Page metadata and navigation widgets |
| `registry.py` | page registration and metadata |

## Registered Pages

The five-page shell remains a stable contract:

| page_id | eyebrow | title |
| --- | --- | --- |
| `dashboard` | 案头 | 先定手头所重 |
| `projects` | 卷帙 | 卷帙总览 |
| `workflow` | 机杼 | 把任务调度清楚 |
| `chapter_studio` | 章台 | 把章节工作放到一张台面上 |
| `settings` | 火候 | 读懂当前运行环境 |

## Remaining Extraction Candidates

| Area | Risk | Suggested target |
| --- | --- | --- |
| UI session save/load | Low-medium, mostly file I/O | `window_session.py` |
| Top actions and status bar | Medium, scattered signal usage | `window_actions.py` |
| Workspace refresh | Medium, touches snapshots and page refresh | `window_workspace.py` |
| Close cleanup | Medium-high, timer and worker ordering matters | `window_cleanup.py` |
| Page routing leftovers | Medium, keep `owner` pattern | extend `window_navigation.py` or create `window_routing.py` |

## Required Gates

Run these after every desktop extraction:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/test_window_behavior_snapshot.py -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/ -q --timeout=120
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/desktop/*_visual.py -q --timeout=180
.venv/bin/python -m ruff check novel_forge/desktop tests/desktop/test_window_behavior_snapshot.py
```

## Refactor Rule

Do not update Qt widgets directly from workers. Extracted modules must preserve signal/slot routing and page shutdown behavior described in `novel_forge/desktop/pages/AGENTS.md`.
