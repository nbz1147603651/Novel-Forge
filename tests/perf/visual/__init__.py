"""Visual regression tests for desktop pages.

Tests under this package are gated behind the ``visual`` pytest marker and are
NOT part of the default CI suite.  Run them explicitly with::

    pytest -m visual
    pytest -m visual --update-visual    # regenerate baselines

Each test renders a single page in offscreen mode, captures the resulting
``QPixmap`` via ``QWidget.grab()``, and compares it against a PNG baseline
checked into ``tests/perf/visual/baselines/``.  On the first run (no baseline
present) the screenshot is written as the new baseline and the test is
skipped; subsequent runs assert that the pixel-level diff is below the
configured threshold (default 1%).
"""