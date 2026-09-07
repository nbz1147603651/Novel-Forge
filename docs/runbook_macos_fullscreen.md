# macOS Fullscreen Runbook

## Symptom

Page-switch while the desktop window is in **macOS system fullscreen** (green-button) drops the window out of the Spaces fullscreen state. AppKit interprets the page-switch main-thread widget ops as a window-state change and exits the Space.

## Reproduction

1. Launch the PySide6 fallback explicitly with `nimo-p`.
2. Click green-button (or View → Enter Full Screen) to enter macOS system fullscreen.
3. Click any sidebar item (e.g. projects → workflow → settings → chapter_studio → back to projects).
4. Window drops out of fullscreen and returns to its desktop Space.

## Root cause

Phase M6: `_on_workspace_refreshed` was triggering heavy main-thread widget ops at the moment of page switch. Phase M6 fixed this by wrapping the body in a fullscreen-aware guard that defers via `QTimer.singleShot(0, ...)` when `_skip_page_motion_for_window_state()` returns True.

## Verification

Run the local-only script:

```bash
python3 scripts/verify_macos_fullscreen.py
```

Exit code 0 = fullscreen preserved across 50 page switches.

If `verify_macos_fullscreen.py` reports drops > 0, escalate: the M6 guard may need to be extended to additional defer points. Candidates:
- `_apply_workspace_refresh` callback
- `_on_section_changed` callback

## Workaround (during development)

Do NOT use macOS system fullscreen while developing. Use **Window → Maximize** instead, or just expand the window.

## CI

CI's desktop-tests job runs with `QT_QPA_PLATFORM=offscreen` which does NOT exercise macOS native fullscreen. The Python script above is **local-only**.
