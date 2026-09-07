"""Global widget styles: QWidget, scrollbars, status bar, page containers."""

from __future__ import annotations

from novel_forge.desktop.tokens.radius import DIALOG_RADIUS

_CONTENT_TEMPLATE = """

/* ═══════════════  Global  ════════════════════════════════════════ */

QWidget {
    color: {{text.primary}};
    font-family: "PingFang SC", "Helvetica Neue", "Arial";
    font-size: 13pt;
}

QPushButton {
    outline: none;
}

QPushButton:focus {
    outline: none;
}

QWidget#workspaceRoot {
    background: transparent;
}

QFrame#sideRail {
    background: transparent;
    border-right: 1px solid rgba({{text.brand.light}}, 0.08);
}

QWidget#sideRailHeader {
    background: transparent;
}

QFrame#brandLogoFrame {
    background: transparent;
    border: none;
    border-radius: 0px;
}

QFrame#brandLogoFrame:hover {
    background: transparent;
    border: none;
}

QFrame#brandLogoFrame[density="compact"] {
    border-radius: 0px;
}

QLabel#brandLogo {
    color: {{text.brand.light}};
    background: transparent;
    border: none;
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 30pt;
    font-weight: 700;
    letter-spacing: 0px;
    padding: 0px;
}

QLabel#brandMark {
    color: {{text.brand.light}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 30pt;
    font-weight: 700;
}

QLabel#brandMark[density="compact"] {
    font-size: 30pt;
}

QLabel#brandTitle {
    color: {{text.brand.gold}};
    font-family: "STKaiti", "Kaiti SC", "KaiTi", "Songti SC", "Georgia";
    font-size: 24pt;
    font-weight: 600;
    letter-spacing: 6px;
}

QLabel#brandTitle[density="compact"] {
    font-size: 22pt;
    letter-spacing: 4px;
}

QLabel#brandSubtitle {
    color: rgba({{text.brand.light}}, 0.55);
    font-size: 11pt;
    line-height: 1.6;
}

/* 装饰分隔线 */
QFrame#railSep {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 transparent,
        stop: 0.15 rgba({{text.brand.gold}}, 0.12),
        stop: 0.35 rgba({{text.brand.gold}}, 0.36),
        stop: 0.5 rgba({{brand.logo.accent}}, 0.42),
        stop: 0.65 rgba({{text.brand.gold}}, 0.36),
        stop: 0.85 rgba({{text.brand.gold}}, 0.12),
        stop: 1 transparent
    );
    border: none;
}

QWidget#brandOrnament {
    color: rgba({{text.brand.gold}}, 0.30);
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 10pt;
    letter-spacing: 4px;
}

QWidget#contentShell {
    background: transparent;
}

/* Rounded container wrapping the page stack — mirrors topBar radius */
QFrame#pageArea {
    background: rgba({{bg.panel}}, 0.42);
    border: 1px solid rgba({{border.default}}, 0.11);
    border-radius: __DIALOG_RADIUS__px;
}

QStackedWidget {
    background: transparent;
}

/* Hide the native scroll-area corner grip (⌟) */
QAbstractScrollArea::corner {
    background: transparent;
    border: none;
}

QSizeGrip {
    width: 0;
    height: 0;
    background: transparent;
}

QScrollArea,
QWidget#pageContainer {
    background: transparent;
}

QScrollBar:vertical {
    width: 7px;
    background: transparent;
    margin: 4px 0;
}

QScrollBar::handle:vertical {
    border-radius: 3px;
    background: rgba({{border.muted}}, 0.22);
    min-height: 24px;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: none;
}

QStatusBar {
    background: {{bg.surface}};
    color: {{text.secondary}};
    font-size: 12pt;
    border-top: 1px solid rgba({{border.default}}, 0.1);
}

QLabel#statusStepLabel,
QLabel#statusCostLabel {
    padding: 0 {{space-2}}px;
}

QLabel[dialogStatus="true"][tone="info"] {
    color: {{status.info.dialog}};
}
QLabel[dialogStatus="true"][tone="ok"] {
    color: {{status.ok.dialog}};
}
QLabel[dialogStatus="true"][tone="warn"] {
    color: {{accent.deep}};
}
QLabel[dialogStatus="true"][tone="error"] {
    color: {{status.error.dialog}};
}

"""

CONTENT = _CONTENT_TEMPLATE.replace("__DIALOG_RADIUS__", str(DIALOG_RADIUS))
