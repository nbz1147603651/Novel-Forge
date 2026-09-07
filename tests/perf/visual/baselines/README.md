# Visual Regression Baselines

This directory stores baseline PNG screenshots used by the visual regression
tests under ``tests/perf/visual/``.

Each test has a corresponding ``<page>.png`` file captured in offscreen mode
with synthetic, fully deterministic input data.  When a test runs:

* If the baseline is **missing**, the screenshot is captured and saved here;
  the test is then **skipped** with a message asking the developer to
  re-run the suite.
* If the baseline **exists**, the captured screenshot is diffed against it
  pixel-by-pixel; differences above ``VISUAL_DIFF_THRESHOLD`` (default 1 %)
  fail the test.

To regenerate baselines after an intentional UI change::

    pytest -m visual --update-visual

Baselines are committed to the repository so that CI can detect visual
regressions without needing to regenerate baselines as part of the run.