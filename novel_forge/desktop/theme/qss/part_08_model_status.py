"""Order-preserving QSS fragment: part_08_model_status."""

from __future__ import annotations

CONTENT = """/* ── Model status dots ─────────────────────────────────────────────── */

QLabel#statusDotGreen {
    background: {{status.success.light}};
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}

QLabel#statusDotRed {
    background: {{status.dot.red}};
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}

QLabel#statusDotYellow {
    background: {{status.dot.yellow}};
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}

QLabel#modelNameLabel {
    font-family: "Helvetica Neue", "Arial";
    font-size: 13pt;
    font-weight: 600;
    color: {{text.heading.deep}};
}

QLabel#statusMeta {
    color: {{text.empty.soft}};
    font-size: 12pt;
}

"""
