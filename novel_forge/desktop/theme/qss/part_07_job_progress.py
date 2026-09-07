"""Order-preserving QSS fragment: part_07_job_progress."""

from __future__ import annotations

CONTENT = """/* ── Job progress bar ──────────────────────────────────────────────── */

QProgressBar#jobProgress {
    background: rgba({{bg.control.hover}}, 0.55);
    border-radius: 4px;
    border: none;
    min-height: 7px;
    max-height: 7px;
}

QProgressBar#jobProgress::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.light}},
        stop: 1 {{accent.dark}}
    );
    border-radius: 4px;
}

QLabel#jobProgressPct {
    color: {{text.muted.strong}};
    font-size: 12pt;
    font-weight: 700;
}

"""
