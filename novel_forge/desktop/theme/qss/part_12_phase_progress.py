"""QSS fragment for phase progress bar component.

Migrates inline ``setStyleSheet()`` calls from:
- ``components/phase_progress.py``

Hardcoded colours are replaced with design tokens where exact matches exist.
"""

from __future__ import annotations

_CONTENT = """

/* ═══════════════  Phase Progress  ═════════════════════════════════ */

QLabel#phaseProgressSegment {
    background: rgba({{border.default}}, 0.08);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 5px;
    color: {{text.muted.badge}};
    font-size: 12px;
    font-weight: 600;
    padding: 3px 6px;
}

QLabel#phaseProgressSegment[state='complete'] {
    background: rgba({{status.success.job}}, 0.16);
    border-color: rgba({{status.success.job}}, 0.24);
    color: {{status.success.job}};
}

QLabel#phaseProgressSegment[state='complete']:hover {
    background: rgba({{status.success.job}}, 0.22);
    border-color: rgba({{status.success.job}}, 0.36);
}

QLabel#phaseProgressSegment[state='current'] {
    background: rgba({{accent.primary}}, 0.14);
    border-color: rgba({{accent.primary}}, 0.34);
    color: {{accent.primary}};
}

QLabel#phaseProgressSegment[state='cancelled'] {
    background: rgba({{border.default}}, 0.08);
    border: 1px dashed rgba({{border.default}}, 0.28);
    color: {{text.disabled}};
}

"""

CONTENT = _CONTENT
