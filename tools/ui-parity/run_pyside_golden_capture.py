#!/usr/bin/env python3
"""Run the PySide golden capture through a stable macOS/offscreen boundary.

Run this launcher from the repository root.  It preserves the primary
capture script's command-line options and writes exactly the same artifacts.
"""

from __future__ import annotations

import runpy
import sys

from novel_forge.desktop.window import NovelForgeDesktopWindow

sys.argv = ["capture_pyside_goldens.py", *sys.argv[1:]]
module = runpy.run_path("tools/ui-parity/capture_pyside_goldens.py")
runtime_globals = module["main"].__globals__
original_load_window = runtime_globals["_load_window"]
original_capture_pages = runtime_globals["capture_pages"]
original_init = NovelForgeDesktopWindow.__init__


def traced_init(instance, *args, **kwargs):  # noqa: ANN001, ANN202
    print("ui-parity capture: initializing PySide shell", flush=True)
    original_init(instance, *args, **kwargs)


NovelForgeDesktopWindow.__init__ = traced_init


def traced_load_window(*args, **kwargs):  # noqa: ANN001, ANN202
    print("ui-parity capture: constructing source window", flush=True)
    return original_load_window(*args, **kwargs)


runtime_globals["_load_window"] = traced_load_window


def traced_capture_pages(*args, **kwargs):  # noqa: ANN001, ANN202
    print("ui-parity capture: binding deterministic fixture", flush=True)
    return original_capture_pages(*args, **kwargs)


runtime_globals["capture_pages"] = traced_capture_pages
exit_code = module["main"]()
raise SystemExit(exit_code)
