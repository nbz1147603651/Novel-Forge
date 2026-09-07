"""QSS fragment for stream detail components.

Migrates inline ``setStyleSheet()`` calls from:
- ``components/stream_detail.py``

Includes styles for:
- SegmentWidget reasoning header/body and content body
- StreamHeaderBar
- ModelCallCard
- Error cards
- StreamDetailWidget container
- Truncation hint
- Dark mode overrides (via ``darkMode`` dynamic property)
"""

from __future__ import annotations

_CONTENT = """

/* ═══════════════  Stream Detail  ═════════════════════════════════ */

/* ── Segment reasoning header ───────────────────────────────────── */

QFrame#reasoningHeader {
    background: rgba({{text.muted}}, 0.12);
    border-left: 3px solid rgba({{text.muted}}, 0.35);
    border-radius: 0 6px 0 0;
    padding: 4px 10px;
}

QLabel#reasoningToggleLabel {
    color: {{text.muted.badge}};
    font-size: 12px;
    font-weight: 700;
}

/* ── Segment reasoning body ─────────────────────────────────────── */

QLabel#reasoningBody {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 8px 12px;
    color: {{text.body.alt}};
    font-size: 12px;
}

QScrollArea#reasoningBodyScroll {
    background: rgba({{text.muted}}, 0.06);
    border: none;
    border-left: 3px solid rgba({{text.muted}}, 0.35);
    border-radius: 0 0 8px 0;
}

QScrollArea#reasoningBodyScroll QScrollBar:vertical {
    background: rgba({{border.default}}, 0.08);
    width: 10px;
    margin: 3px 2px 3px 0;
    border-radius: 5px;
}

QScrollArea#reasoningBodyScroll QScrollBar::handle:vertical {
    background: rgba({{accent.primary}}, 0.48);
    min-height: 28px;
    border-radius: 4px;
}

QScrollArea#reasoningBodyScroll QScrollBar::handle:vertical:hover {
    background: rgba({{accent.primary}}, 0.72);
}

QScrollArea#reasoningBodyScroll QScrollBar::add-line:vertical,
QScrollArea#reasoningBodyScroll QScrollBar::sub-line:vertical {
    height: 0;
}

/* ── Segment content body ───────────────────────────────────────── */

QLabel#contentBody {
    background: transparent;
    padding: 6px 4px 8px 4px;
    color: {{text.primary}};
    font-family: "Noto Serif SC", "Songti SC", "STSong", serif;
    font-size: 14px;
    line-height: 1.58;
}

/* ── Stream header bar ──────────────────────────────────────────── */

QFrame#streamHeaderBar {
    background: rgba({{bg.hero.mid}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 7px;
}

QLabel#streamHeaderText {
    color: {{text.body.alt}};
    font-size: 12px;
    font-weight: 600;
}

/* ── Model call card ────────────────────────────────────────────── */

QFrame#modelCallCard {
    background: rgba({{bg.surface.elevated}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
}

QFrame#modelCallCard[tone='error'] {
    background: rgba({{status.danger}}, 0.08);
    border-color: rgba({{status.danger}}, 0.28);
}

QLabel#modelCallTitle {
    color: {{text.primary}};
    font-size: 14px;
    font-weight: 700;
}

QLabel#modelCallMeta {
    color: {{text.mode.sub}};
    font-size: 12px;
}

QLabel#modelCallPreview {
    background: rgba({{bg.input}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 6px;
    padding: 9px 10px;
    color: {{text.heading}};
    font-size: 13px;
    line-height: 1.6;
}

QPushButton#modelCallToggle {
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 6px;
    padding: 4px 10px;
    background: rgba({{bg.input}}, 0.76);
    color: {{text.body.alt}};
}

QLabel#modelCallStats {
    color: {{text.muted.soft}};
    font-size: 12px;
    padding: 4px 0 0 0;
}

/* ── Error card ─────────────────────────────────────────────────── */

QLabel#streamErrorCard {
    background: rgba({{status.danger}}, 0.08);
    border: 1px solid rgba({{status.danger}}, 0.28);
    border-left: 4px solid rgba({{status.danger}}, 0.78);
    border-radius: 7px;
    padding: 9px 11px;
    color: {{status.danger.deep}};
    font-size: 13px;
    font-weight: 600;
}

/* ── Structured-output validation status ────────────────────────── */

QLabel#streamValidationHint {
    border: 1px solid rgba({{status.warning}}, 0.28);
    border-radius: 7px;
    padding: 8px 10px;
    background: rgba({{status.warning}}, 0.08);
    color: {{status.warning.alt}};
    font-size: 12px;
    font-weight: 600;
}

QLabel#streamValidationHint[tone="success"] {
    border-color: rgba({{status.success.warm}}, 0.28);
    background: rgba({{status.success.warm}}, 0.08);
    color: {{status.success.warm}};
}

QLabel#streamValidationHint[tone="error"] {
    border-color: rgba({{status.danger}}, 0.28);
    background: rgba({{status.danger}}, 0.08);
    color: {{status.danger.deep}};
}

/* ── Stream detail container ────────────────────────────────────── */

QScrollArea#streamDetailScroll {
    background: {{bg.surface.elevated}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 8px;
}

QWidget#streamDetailHost {
    background: {{bg.surface.elevated}};
}

/* ── Truncation hint ────────────────────────────────────────────── */

QLabel#streamTruncationHint {
    background: rgba({{text.muted}}, 0.08);
    border: 1px solid rgba({{text.muted}}, 0.18);
    border-radius: 6px;
    padding: 7px 10px;
    color: {{text.mode.sub}};
    font-size: 12px;
}

/* ── Dark mode overrides (activated via darkMode property) ──────── */

QScrollArea#streamDetailScroll[darkMode="true"] {
    background: {{bg.sidebar.start}};
    border-color: rgba({{separator}}, 0.16);
}

QWidget#streamDetailHost[darkMode="true"] {
    background: {{bg.sidebar.start}};
}

QLabel#contentBody[darkMode="true"] {
    color: {{fade.bg}};
}

QLabel#reasoningBody[darkMode="true"] {
    color: {{text.brand.gold}};
    background: transparent;
}

QScrollArea#reasoningBodyScroll[darkMode="true"] {
    background: rgba({{separator}}, 0.07);
    border-left-color: rgba({{separator}}, 0.28);
}

QFrame#streamHeaderBar[darkMode="true"],
QFrame#modelCallCard[darkMode="true"] {
    background: rgba({{bg.sidebar.end}}, 0.96);
    border-color: rgba({{separator}}, 0.14);
}

QLabel#streamHeaderText[darkMode="true"],
QLabel#modelCallMeta[darkMode="true"] {
    color: {{text.brand.gold}};
}

QLabel#modelCallTitle[darkMode="true"] {
    color: {{fade.bg}};
}

"""

CONTENT = _CONTENT
