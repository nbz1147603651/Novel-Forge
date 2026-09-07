"""Order-preserving QSS fragment: part_01_tooltip_popups."""

from __future__ import annotations

CONTENT = """/* ── Frameless tooltip popups (replace native QToolTip delivery where needed) ──
 *
 * The translucent top-level shell is painted by ``PaintedFloatingSurface``.
 * Do not add top-level QFrame rules for the character or blueprint popup
 * object names here: even non-background declarations can make Qt enable
 * styled background handling on top-level QFrame popups. Only style child
 * labels.
 */
QLabel#characterTooltipLabel,
QLabel#blueprintTooltipLabel {
    background: transparent;
    color: {{text.body.warm}};
    font-family: "Songti SC", "STSong", "SimSun", "Times New Roman", Arial;
    font-size: 10pt;
    line-height: 1.45;
}

QLabel#sectionTitle {
    color: {{text.heading.warm}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 19pt;
    font-weight: 600;
}

QLabel#metricTitle {
    color: {{text.muted.strong}};
    font-size: 12pt;
    font-weight: 600;
}

QLabel#metricTitle[compact="true"] {
    font-size: 11pt;
}

QLabel#metricValue {
    color: {{accent.muted}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 28pt;
    font-weight: 700;
}

QLabel#metricValue[compact="true"] {
    font-size: 21pt;
}

QLabel#metricDetail,
QLabel#cardMeta,
QLabel#fieldHint {
    color: {{text.muted.soft}};
    font-size: 12pt;
}

QLabel#metricDetail[compact="true"],
QLabel#cardHint[compact="true"] {
    font-size: 11pt;
}

QLabel#cardTitleLarge {
    color: {{text.heading.deep}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 22pt;
    font-weight: 600;
}

QLabel#cardTitle {
    color: {{text.heading}};
    font-size: 15pt;
    font-weight: 700;
}

QLabel#cardTitle[compact="true"] {
    font-size: 13pt;
}

QLabel#providerGroupTitle {
    color: {{text.body}};
    font-size: 13pt;
    font-weight: 600;
}

QLabel#cardBody {
    color: {{text.body}};
    font-size: 13pt;
}

QLabel#cardBody[compact="true"] {
    font-size: 12pt;
}

QLabel#cardHint {
    color: {{text.muted}};
    font-size: 12pt;
}

QLabel#emptyTitle {
    color: {{text.empty}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 17pt;
    font-weight: 600;
}

QLabel#emptyMessage {
    color: {{text.empty.soft}};
    font-size: 13pt;
}

QLabel#dangerText {
    color: {{status.danger.alt}};
    font-size: 12pt;
}

QTextEdit#taskFlowErrorLogText {
    background: rgba({{bg.input.soft}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 10px;
    color: {{text.body}};
    font-size: 12pt;
    padding: 10px;
}

QLabel#badge {
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 11pt;
    font-weight: 700;
}

QLabel#badge[tone="default"] {
    background: rgba({{border.default}}, 0.12);
    color: {{text.badge.default}};
}

QLabel#badge[tone="warning"] {
    background: rgba({{accent.primary}}, 0.14);
    color: {{accent.badge}};
}

QLabel#badge[tone="success"] {
    background: rgba({{status.success.warm}}, 0.14);
    color: {{status.success.warm}};
}

QLabel#badge[tone="danger"] {
    background: rgba({{status.danger.alt}}, 0.14);
    color: {{status.danger.deep}};
}

QLabel#badge[tone="muted"] {
    background: rgba({{text.muted}}, 0.12);
    color: {{text.muted.badge}};
}

QLabel#badge[tone="quiet"] {
    background: rgba({{text.muted}}, 0.08);
    color: {{text.muted.quiet}};
}

QLabel#badge[tone="primary"] {
    background: rgba({{accent.primary}}, 0.18);
    color: {{accent.primary}};
}

QLabel#badge[tone="info"] {
    background: rgba({{status.info}}, 0.14);
    color: {{status.info}};
}

QProgressBar#projectProgress {
    background: rgba({{bg.control.hover}}, 0.6);
    border-radius: 4px;
    border: none;
    min-height: 7px;
    max-height: 7px;
}

QProgressBar#projectProgress::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.light}},
        stop: 1 {{accent.dark}}
    );
    border-radius: 4px;
}

"""
