# Desktop Pages — AGENTS.md (v4)

Pages layer of the PySide6 desktop UI. Each top-level page (sidebar item) maps to one subdirectory.

## Novel Controls: Required Boundaries

Read [workflow facts](../../../docs/novel-workflow-current.md) and [the author-control design](../../../docs/novel-authoring-control-design.md) before changing workflow/chapter/project controls. Update the same-change step/status record and include matching backend/API/NIMO behavior in validation.

Display total target, planned range, executable detail and archived state distinctly; do not label machine hardening as human approval. Mode changes, stop requests and “continue” must reflect the backend's accepted scope and state, not only a widget flag. Preserve manual edits, saved choices and the existing interaction design; no visual redesign or expanded autonomy as a side effect of a targeted fix.

## Layout

```
pages/
├── __init__.py            # pkgutil auto-discovery of <group>/ subdirs
├── AGENTS.md              # this file
├── _page_utils.py         # cross-cutting helpers
├── chapter_studio/        # 18 files — chapter generation/inspection page
├── workflow/              # 12 files — workflow page
├── document_renderer/     # package from Phase M3 (splits already done) + a few additional files
├── settings/              # 9 files — settings page
└── standalone/            # 19 files — single-purpose pages (projects, dashboard, character_*, ...)
```

## Per-subdir conventions

Each subdirectory SHOULD have:
- `__init__.py` — docstring + `from __future__ import annotations`. **Eager imports of `*Page` classes go into `page_registrations.py`, not here** (avoids circular imports between chapter_studio ↔ workflow ↔ standalone ↔ document_renderer).
- `page.py` — the *Page class (QWidget-based)
- `state.py / actions.py / workers.py / widgets.py / components.py` — optional conventions

## Registration

Registration happens in `novel_forge/desktop/page_registrations.py` (not in each subdir's `__init__.py` — see above for circular-import reasons).

## Imports

- **Within a subdir**: use relative imports (`from .page import ChapterStudioPage`).
- **Across subdirs**: use absolute imports (`from novel_forge.desktop.pages.workflow.artifacts import StepArtifactDialog`). The old flat layout used bare relative imports across the boundary (`from .memory_panel import X`) — these were rewritten during M4 to absolute.
- **Tests**: continue to import from `novel_forge.desktop.pages.<group>.<module>` (e.g. `novel_forge.desktop.pages.standalone.projects`).

## Backward-compat shims at pages/ top level

After M4, these top-level files are kept as **shims** for any external import path that existed pre-M4. They re-export from the new subdir:

- `document_renderers.py` (shim for `pages/document_renderer/__init__.py`)
- `document_renderer_reports.py` (shim for `pages/document_renderer/reports/__init__.py`)
- `document_renderer_story_artifacts.py` (shim for `pages/document_renderer/story_artifacts/__init__.py`)
- `settings_page_parameters.py` (shim for `pages/settings/parameters/__init__.py`)

If you find a `pages/<name>.py` not listed above, that file is independent — leave alone.

## Tests

Pages are exercised by:
- `tests/desktop/test_page_registry.py` — page registration
- `tests/desktop/test_pages_autodiscovery.py` — subdir discovery via pkgutil
- `tests/desktop/test_window_pages.py` — page-switch integration
- Various `tests/desktop/test_<feature>*.py` files
