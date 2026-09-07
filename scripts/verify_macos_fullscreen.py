"""Verify macOS fullscreen doesn't exit on page switch.

Local-only diagnostic (requires macOS GUI session). Not run in CI.

Strategy: launch the desktop window, programmatically enter fullscreen
state, trigger page switches 50 times via the navigation button
signals, and assert that the window's fullscreen state is preserved.

Uses PySide6 QTest where possible; PyObjC only if Qt introspection is
insufficient. PyObjC is optional and only loaded inside the macOS
guard to avoid hard import dependency on other platforms.
"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip GUI; just verify the script runs without errors.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("[skip] only runs on macOS; sys.platform =", sys.platform)
        sys.exit(0)

    if args.dry_run:
        print("[dry-run] macOS detected; would launch window + simulate 50 page switches.")
        sys.exit(0)

    # Real run: requires GUI session.
    from PySide6.QtWidgets import QApplication

    from novel_forge.desktop.main import launch_desktop

    app = QApplication.instance() or QApplication(sys.argv)
    window = launch_desktop(app)
    if window is None:
        print("[!] launch_desktop returned None — desktop failed to start")
        sys.exit(1)

    # Manually enter fullscreen state
    window.showFullScreen()

    # Programmatic page switches
    from PySide6.QtCore import QTimer
    page_ids = ["dashboard", "projects", "workflow", "settings", "chapter_studio"]
    state = {"i": 0, "fs_drops": 0}

    def _tick():
        if state["i"] >= 50:
            window.close()
            app.quit()
            return
        pid = page_ids[state["i"] % len(page_ids)]
        window.switch_page(pid)
        # After switch, re-enter fullscreen if it got dropped
        QTimer.singleShot(50, _check)
        state["i"] += 1

    def _check():
        if not window.isFullScreen():
            state["fs_drops"] += 1
            window.showFullScreen()
        _tick()

    QTimer.singleShot(500, _tick)

    app.exec()
    print(f"[result] page switches: 50, fullscreen drops: {state['fs_drops']}")
    sys.exit(0 if state["fs_drops"] == 0 else 2)


if __name__ == "__main__":
    main()
