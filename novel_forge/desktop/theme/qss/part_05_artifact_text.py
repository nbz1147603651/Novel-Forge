"""Order-preserving QSS fragment: part_05_artifact_text."""

from __future__ import annotations

CONTENT = """/* ── Artifact text viewer ───────────────────────────────── */

QTextEdit#artifactContent {
    background: rgba({{bg.input.soft}}, 0.96);
    border: none;
    border-radius: 0;
    color: {{text.artifact}};
    font-family: "Menlo", "Monaco", "Courier New";
    font-size: 12pt;
    padding: 10px 14px;
}

QLabel#panelDescription {
    color: {{text.secondary}};
    font-size: 12pt;
}

"""
