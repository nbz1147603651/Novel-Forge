"""Navigation styles: top bar, nav buttons, chapter rail, mode selectors."""

from __future__ import annotations

from novel_forge.desktop.tokens.radius import CHIP_RADIUS, COMPACT_CARD_RADIUS, DIALOG_RADIUS

_CONTENT = """

QToolButton#sideRailToggle {
    background: rgba({{bg.surface}}, 0.08);
    border: 1px solid rgba({{text.brand.light}}, 0.12);
    border-radius: 8px;
    color: rgba({{text.brand.light}}, 0.82);
    font-size: 13pt;
    font-weight: 700;
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
}

QToolButton#sideRailToggle:hover {
    background: rgba({{bg.surface}}, 0.14);
    border-color: rgba({{accent.primary}}, 0.46);
    color: {{text.brand.light}};
}

QPushButton#navButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    color: rgba({{text.brand.light}}, 0.78);
    font-family: "Kaiti SC", "STKaiti", "Songti SC", "STSong", "SimSun", "Georgia";
    font-size: 20pt;
    font-weight: 500;
    padding: 6px 14px;
    margin: 2px 0;
    text-align: center;
}

QPushButton#navButton[density="compact"] {
    border-radius: 10px;
    font-size: 18pt;
    padding: 4px 10px;
}

QPushButton#navButton:hover {
    background: rgba({{bg.surface}}, 0.08);
    border-color: rgba({{bg.surface}}, 0.12);
    color: {{text.primary}};
}

QPushButton#navButton[active="true"],
QPushButton#navButton[active="true"]:hover,
QPushButton#navButton[active="true"]:pressed {
    background: rgba({{accent.primary}}, 0.16);
    border-color: rgba({{accent.primary}}, 0.32);
    color: {{accent.primary}};
    font-weight: 600;
}

QPushButton#navButton:checked,
QPushButton#navButton:checked:hover,
QPushButton#navButton:checked:pressed {
    background: rgba({{accent.primary}}, 0.16);
    border-color: rgba({{accent.primary}}, 0.32);
    color: {{accent.primary}};
    font-weight: 600;
}

QWidget#activeNavIndicator {
    background: {{accent.primary}};
    border-radius: 2px;
}

QWidget#activeNavIndicator[density="compact"] {
    border-radius: 1.5px;
}

/* Nav scroll area — transparent, no frame, thin scrollbar */
QScrollArea#sideRailNavScroll {
    background: transparent;
    border: none;
}

QScrollArea#sideRailNavScroll > QWidget {
    background: transparent;
}

QScrollArea#sideRailNavScroll QScrollBar:vertical {
    width: 4px;
    background: transparent;
    margin: 2px 0;
}

QScrollArea#sideRailNavScroll QScrollBar::handle:vertical {
    border-radius: 2px;
    background: rgba({{text.brand.light}}, 0.15);
    min-height: 20px;
}

QScrollArea#sideRailNavScroll QScrollBar::add-line:vertical,
QScrollArea#sideRailNavScroll QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollArea#sideRailNavScroll QScrollBar::add-page:vertical,
QScrollArea#sideRailNavScroll QScrollBar::sub-page:vertical {
    background: none;
}

QFrame#railFooter {
    background: rgba({{bg.surface}}, 0.07);
    border: 1px solid rgba({{text.brand.gold}}, 0.10);
    border-radius: 12px;
}

QLabel#railFooterTitle {
    color: {{text.rail.footer}};
    font-size: 14pt;
    font-weight: 600;
}

QLabel#railFooterMeta {
    color: rgba({{text.rail.footer}}, 0.68);
    font-size: 12pt;
}

QFrame#topBar {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 18px;
}

QLabel#topBarEyebrow,
QLabel#eyebrowLabel {
    color: {{text.eyebrow}};
    font-size: 12pt;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
}

QLabel#topBarEyebrow[density="compact"] {
    font-size: 11pt;
}

QLabel#topBarTitle {
    color: {{text.topbar.title}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 22pt;
    font-weight: 600;
}

QLabel#topBarTitle[density="compact"] {
    font-size: 20pt;
}

QLabel#topBarSubtitle,
QLabel#sectionSubtitle {
    color: {{text.topbar.subtitle}};
    font-size: 13pt;
}

QLabel#topBarSubtitle[density="compact"] {
    font-size: 12pt;
}

QLabel#topBarMeta {
    color: {{text.topbar.meta}};
    font-size: 12pt;
}

/* Chapter rail buttons: compact, refined cards */
QPushButton#chapterRailBtn {
    background: rgba({{bg.control}}, 0.85);
    border: 1px solid rgba({{border.muted}}, 0.12);
    border-radius: 6px;
    color: {{text.chapter.rail}};
    font-size: 12pt;
    font-weight: 500;
    padding: 5px 8px;
    text-align: left;
    min-height: 30px;
    max-width: 260px;
}

QPushButton#chapterRailBtn:checked {
    background: rgba({{accent.primary}}, 0.13);
    border: 1px solid rgba({{accent.primary}}, 0.38);
    color: {{accent.deep}};
    font-weight: 700;
}

QPushButton#chapterRailBtn:hover:!checked {
    background: rgba({{bg.control.hover}}, 0.8);
    border-color: rgba({{border.muted}}, 0.22);
}

/* Chapter rail selection indicator — sliding accent bar */
QWidget#railIndicator {
    background: rgba({{accent.primary}}, 0.85);
    border-radius: 2px;
}

/* Rail scroll area — transparent background.
   三层都需声明：scroll area 本身 / viewport(> QWidget) / rail_container(> QWidget > QWidget) */
QScrollArea#railScroll,
QScrollArea#railScroll > QWidget,
QScrollArea#railScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}

QPushButton#chapterRailBtn[railVariant="stale"] {
    color: {{text.stale}};
    font-style: italic;
}

QPushButton#chapterRailBtn[railVariant="stale"]:checked {
    background: rgba({{border.warm}}, 0.10);
    border-color: rgba({{border.warm}}, 0.28);
    color: {{text.stale.checked}};
    font-weight: 600;
}

QPushButton#chapterRailBtn[railVariant="stale"]:hover:!checked {
    background: rgba({{bg.control.hover}}, 0.30);
}

QPushButton#chapterRailBtn[railVariant="rewriting"] {
    color: {{accent.primary}};
    font-weight: 600;
}

QPushButton#chapterRailBtn[railVariant="rewriting"]:checked {
    background: rgba({{accent.primary}}, 0.18);
    border-color: rgba({{accent.primary}}, 0.45);
    color: {{accent.rewriting}};
    font-weight: 700;
}

QPushButton#chapterRailBtn[railVariant="rewriting"]:hover:!checked {
    background: rgba({{accent.primary}}, 0.10);
}

/* Chapter rail status indicators */
QLabel#jobStatusIndicator {
    font-size: 14pt;
    font-weight: 700;
}
QLabel#jobStatusIndicator[tone="success"] {
    color: {{status.success.job}};
}
QLabel#jobStatusIndicator[tone="warning"] {
    color: {{accent.primary}};
}
QLabel#jobStatusIndicator[tone="muted"] {
    color: {{text.stale}};
}
QLabel#jobStatusIndicator[tone="danger"] {
    color: {{status.danger}};
}

QWidget#chapterListPanel {
    background: rgba({{bg.panel}}, 0.95);
    border-right: 1px solid rgba({{border.default}}, 0.15);
}

QLineEdit#chapterSearch {
    background: rgba({{bg.input}}, 0.9);
    border: 1px solid rgba({{border.default}}, 0.2);
    border-radius: 6px;
    color: {{text.heading}};
    font-size: 12pt;
    padding: 5px 8px;
    margin: 4px 4px 2px 4px;
}

QLineEdit#chapterSearch:focus {
    border-color: rgba({{accent.primary}}, 0.45);
}

QListWidget#chapterList {
    background: transparent;
    border: none;
    font-size: 12pt;
    outline: none;
    padding: 2px;
}

QListWidget#chapterList::item {
    border-radius: 5px;
    color: {{text.body.alt}};
    padding: 5px 8px;
    margin: 1px 3px;
}

QListWidget#chapterList::item:selected {
    background: rgba({{accent.primary}}, 0.12);
    color: {{text.heading}};
    font-weight: 600;
}

QListWidget#chapterList::item:hover:!selected {
    background: rgba({{bg.control.hover}}, 0.45);
}

/* ── Mode selector buttons (短篇 / 长篇 large toggle) ─────────────── */

QPushButton#modeBtn {
    background: rgba({{bg.surface}}, 0.82);
    border: 1.5px solid rgba({{border.default}}, 0.18);
    border-radius: 14px;
    text-align: left;
    padding: 0px;
}

QPushButton#modeBtn:hover {
    background: rgba({{bg.input.soft}}, 0.96);
    border-color: rgba({{accent.primary}}, 0.28);
}

QPushButton#modeBtn[selected="true"],
QPushButton#modeBtn:checked {
    background: rgba({{accent.primary}}, 0.10);
    border: 2px solid rgba({{accent.primary}}, 0.45);
}

QLabel#modeBtnTitle {
    color: {{text.heading.warm}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 17pt;
    font-weight: 700;
    background: transparent;
}

QLabel#modeBtnSub {
    color: {{text.mode.sub}};
    font-size: 12pt;
    background: transparent;
}

/* ── Sub-mode buttons (立项初始化 / 章节续写 smaller toggle) ─────── */

QPushButton#subModeBtn {
    background: rgba({{bg.inset}}, 0.8);
    border: 1.5px solid rgba({{border.warm}}, 0.22);
    border-radius: 10px;
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 600;
    padding: 6px 14px;
}

QPushButton#subModeBtn:hover {
    background: rgba({{bg.surface}}, 0.9);
    border-color: rgba({{accent.primary}}, 0.3);
}

QPushButton#subModeBtn[selected="true"],
QPushButton#subModeBtn:checked {
    background: rgba({{accent.primary}}, 0.12);
    border: 2px solid rgba({{accent.primary}}, 0.48);
    color: {{accent.deep}};
}

QPushButton#subModeBtn[toolRole="ai"] {
    background: rgba({{bg.surface}}, 0.96);
    border: 1.5px solid rgba({{accent.primary}}, 0.36);
    color: {{accent.deep}};
    font-weight: 700;
}

QPushButton#subModeBtn[toolRole="ai"]:hover {
    background: rgba({{accent.primary}}, 0.12);
    border-color: rgba({{accent.primary}}, 0.56);
}

QPushButton#subModeBtn[compact="true"] {
    border-width: 1px;
    border-radius: 8px;
    font-size: 12pt;
    padding: 4px 10px;
    min-height: 24px;
}

/* ── Compact action button override ────────────────────────────────── */

QPushButton#actionButton[compact="true"] {
    font-size: 12pt;
    padding: 4px 12px;
    min-height: 26px;
    border-radius: 8px;
}

/* ── TopBar action button transitions (Task 15) ───────────────────────── */
/* Generic (no specific variant): accent.primary at 8% alpha tint on hover. */
QPushButton#actionButton:hover {
    background: rgba({{accent.primary}}, 0.08);
}

/* Primary variant: explicit tokenized hover so primary buttons stay vibrant. */
QPushButton#actionButton[variant="primary"]:hover {
    background: {{accent.primary.hover}};
    border-color: {{accent.primary.hover}};
}

/* ── Task 11 — quick-action styling (rich doc viewer, project card, quick nav, rail secondary) ── */

/* Rich document viewer toolbar */
QFrame#richDocToolbar {
    background: rgba({{bg.input}}, 0.55);
    border-bottom: 1px solid rgba({{border.default}}, 0.18);
}
QLabel#richDocTitle {
    color: {{text.tab.hover}};
    font-weight: 600;
    font-size: 13pt;
}
QToolButton#richDocToolBtn {
    background: transparent;
    border: 1px solid rgba({{border.default}}, 0.30);
    border-radius: 4px;
    padding: 4px 10px;
    color: {{text.tab.hover}};
    font-size: 12pt;
}
QToolButton#richDocToolBtn:hover {
    background: rgba({{accent.primary}}, 0.08);
    border-color: rgba({{accent.primary}}, 0.50);
}
QToolButton#richDocToolBtn:checked {
    background: rgba({{accent.primary}}, 0.15);
    color: {{accent.primary}};
}
QFrame#richDocStatus {
    background: rgba({{bg.panel}}, 0.45);
    border-top: 1px solid rgba({{border.default}}, 0.10);
}
QLabel#richDocStatusItem {
    color: {{text.muted}};
    font-size: 11pt;
}

/* Project card quick action */
QToolButton#projectCardAction {
    background: rgba({{bg.input}}, 0.55);
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: 4px;
    padding: 3px 8px;
    color: {{text.tab.hover}};
    font-size: 11pt;
}
QToolButton#projectCardAction:hover {
    background: rgba({{accent.primary}}, 0.10);
    color: {{accent.primary}};
    border-color: rgba({{accent.primary}}, 0.40);
}

"""

CONTENT = (
    _CONTENT
    .replace("border-radius: 8px;", f"border-radius: {CHIP_RADIUS}px;")
    .replace("border-radius: 12px;", f"border-radius: {COMPACT_CARD_RADIUS}px;")
    .replace("border-radius: 14px;", f"border-radius: {DIALOG_RADIUS}px;")
)
