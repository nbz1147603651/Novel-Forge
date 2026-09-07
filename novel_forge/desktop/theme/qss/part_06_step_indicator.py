"""Order-preserving QSS fragment: part_06_step_indicator."""

from __future__ import annotations

CONTENT = """/* ── Step Indicator ─────────────────────────────────────── */

QLabel#stepDot {
    border-radius: __CHIP_RADIUS__px;
}

QLabel#stepDot[state="pending"] {
    background: rgba({{bg.control.hover}}, 0.28);
    color: rgba({{text.muted}}, 0.55);
    border: 1.5px solid rgba({{border.warm}}, 0.22);
}

QLabel#stepDot[state="active"] {
    background: rgba({{accent.primary}}, 0.18);
    color: {{accent.primary}};
    border: 2px solid {{accent.primary}};
}

QLabel#stepDot[state="done"] {
    background: rgba({{status.success.warm}}, 0.18);
    color: {{status.success.warm}};
    border: 2px solid rgba({{status.success.warm}}, 0.85);
}

QLabel#stepDot[state="failed"] {
    background: rgba({{status.danger.alt}}, 0.18);
    color: {{status.danger.alt}};
    border: 2px solid rgba({{status.danger.alt}}, 0.55);
}

QLabel#stepDot[state="skipped"] {
    background: rgba({{bg.control.hover}}, 0.15);
    color: rgba({{text.muted}}, 0.35);
    border: 1.5px dashed rgba({{border.warm}}, 0.30);
}

QLabel#stepLabel {
    color: {{text.muted}};
    font-size: 10pt;
    font-weight: 600;
}

QLabel#stepLabel[state="active"] {
    color: {{accent.primary}};
    font-weight: 700;
}

QLabel#stepLabel[state="done"] {
    color: {{text.muted.strong}};
}

QLabel#stepLabel[state="failed"] {
    color: {{status.danger.alt}};
    font-weight: 700;
}

QLabel#stepLabel[state="skipped"] {
    color: {{text.disabled}};
}

QLabel#stepConnector {
    background: rgba({{border.warm}}, 0.22);
    border: none;
}

QLabel#progressDetail {
    color: {{text.muted.strong}};
    font-size: 11pt;
}

QLabel#progressScore {
    color: {{status.success.warm}};
    font-size: 14pt;
    font-weight: 700;
}

QLabel#progressScoreFail {
    color: {{status.danger.deep}};
    font-size: 14pt;
    font-weight: 700;
}

QLabel#stepPhaseLabel {
    color: {{text.muted}};
    font-size: 10pt;
    font-weight: 600;
}

QFrame#stepPhase {
    border-radius: 10px;
    padding: 3px 8px;
    border: 1.5px solid rgba({{border.warm}}, 0.22);
    background: rgba({{bg.control.hover}}, 0.14);
}

QFrame#stepPhase[state="active"] {
    background: rgba({{accent.primary}}, 0.14);
    border: 2px solid {{accent.primary}};
}

QFrame#stepPhase[state="done"] {
    background: rgba({{status.success.warm}}, 0.14);
    border: 2px solid rgba({{status.success.warm}}, 0.45);
}

QFrame#stepPhase[state="failed"] {
    background: rgba({{status.danger.alt}}, 0.14);
    border: 2px solid rgba({{status.danger.alt}}, 0.45);
}

"""
