"""Order-preserving QSS fragment: part_02_collapsible."""

from __future__ import annotations

CONTENT = """/* ── Collapsible Section ─────────────────────────── */

QPushButton#collapseToggle {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __COMPACT_CARD_RADIUS__px;
    color: {{text.heading}};
    font-size: 14pt;
    font-weight: 700;
    padding: 10px 16px;
    text-align: left;
}

QPushButton#collapseToggle:hover {
    background: rgba({{bg.hover.accent}}, 1);
    border-color: rgba({{border.default}}, 0.22);
}

QPushButton#collapseToggle:checked {
    border-radius: __COMPACT_CARD_RADIUS__px;
    border-bottom-color: rgba({{border.default}}, 0.14);
}

QPushButton#collapseToggle[nested="true"] {
    background: rgba({{bg.surface}}, 0.72);
    border-color: rgba({{border.default}}, 0.12);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.collapse.nested}};
    font-size: 13pt;
    padding: 8px 12px;
}

QPushButton#collapseToggle[nested="true"]:checked {
    border-radius: __CHIP_RADIUS__px;
}

QFrame#collapseBody {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-top: none;
    border-radius: 0 0 __COMPACT_CARD_RADIUS__px __COMPACT_CARD_RADIUS__px;
}

QFrame#collapseBody[nested="true"] {
    background: rgba({{bg.surface}}, 0.72);
    border-color: rgba({{border.default}}, 0.12);
    border-radius: 0 0 __CHIP_RADIUS__px __CHIP_RADIUS__px;
}

QSlider#blueprintWeightSlider::groove:horizontal {
    height: 5px;
    border-radius: 3px;
    background: rgba({{bg.control.hover}}, 0.62);
}

QSlider#blueprintWeightSlider::sub-page:horizontal {
    border-radius: 3px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.slider.warm}},
        stop: 1 {{accent.light}}
    );
}

QSlider#blueprintWeightSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
    border: 1px solid rgba({{border.default}}, 0.24);
    background: {{bg.surface.elevated}};
}

QSlider#blueprintWeightSlider::handle:horizontal:hover {
    border-color: rgba({{accent.primary}}, 0.45);
}

QDialog#docViewerDialog QLabel#docViewerTitle,
QWidget#projectsPage QLabel#viewerTitle {
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 18pt;
    font-weight: 700;
}

QWidget#projectsPage QLabel#viewerTitle[readerHeader="true"] {
    font-size: 16pt;
}

QDialog#docViewerDialog QLabel#docViewerEmpty,
QWidget#projectsPage QLabel#viewerHint {
    color: {{text.muted}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 13pt;
    padding: 30px 16px;
}

QDialog#docViewerDialog QFrame#docViewerSeparator,
QWidget#projectsPage QFrame#viewerSep {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 transparent,
        stop: 0.15 rgba({{border.warm}}, 0.28),
        stop: 0.85 rgba({{border.warm}}, 0.28),
        stop: 1 transparent
    );
    border: none;
}

QWidget#projectsPage QLabel#viewerFilterLabel {
    color: {{text.body.alt}};
    font-size: 12pt;
    font-weight: 600;
}

QWidget#projectsPage QComboBox#projectSelector {
    background: rgba({{bg.input}}, 0.9);
    border: 1px solid rgba({{border.default}}, 0.22);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 14pt;
    font-weight: 600;
    min-height: 40px;
    min-width: 320px;
    padding: 0 14px;
}

QWidget#projectsPage QComboBox#projectSelector[readerHeader="true"] {
    border-radius: 7px;
    font-size: 13pt;
    min-height: 32px;
    max-height: 32px;
    min-width: 300px;
    padding: 0 12px;
}

QWidget#projectsPage QComboBox#projectSelector::drop-down {
    border-left: 1px solid rgba({{border.default}}, 0.15);
    width: 32px;
}

QWidget#projectsPage QComboBox#projectSelector QAbstractItemView {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.2);
    color: {{text.heading}};
    selection-background-color: rgba({{accent.primary}}, 0.12);
}

QSplitter::handle {
    background: rgba({{border.default}}, 0.12);
}

QSplitter::handle:hover {
    background: rgba({{accent.primary}}, 0.28);
}

QSplitter::handle:pressed {
    background: rgba({{accent.primary}}, 0.45);
}

QSplitter#docSplitter::handle {
    background: rgba({{border.default}}, 0.18);
    border-radius: 3px;
    margin: 4px 2px;
}

QSplitter#docSplitter::handle:hover {
    background: rgba({{accent.primary}}, 0.32);
}

QSplitter#docSplitter::handle:pressed {
    background: rgba({{accent.primary}}, 0.48);
}

QTextEdit#docViewerContent {
    background: rgba({{bg.input}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.primary}};
    font-family: "Menlo", "Consolas", "Courier New";
    font-size: 12pt;
    padding: 12px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}

QTextEdit#docViewerProse {
    background: rgba({{bg.input}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.primary}};
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 15pt;
    padding: 20px 24px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}

QTextBrowser#chapterProseBrowser {
    background: rgba({{bg.input}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    padding: 8px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}

"""
