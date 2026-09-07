"""Order-preserving QSS fragment: part_09_capability_dots."""

from __future__ import annotations

CONTENT = """/* ── Capability indicator dots (small, for status cards) ───────────── */

QLabel#capDotGreen {
    background: {{status.success.light}};
    border-radius: 4px;
    min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}

QLabel#capDotRed {
    background: {{status.dot.red}};
    border-radius: 4px;
    min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}

QLabel#capDotPending {
    background: rgba({{bg.control.hover}}, 0.35);
    border-radius: 4px;
    min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}

QLabel#capLabel {
    color: {{text.empty.soft}};
    font-size: 11pt;
    font-weight: 600;
}

"""
