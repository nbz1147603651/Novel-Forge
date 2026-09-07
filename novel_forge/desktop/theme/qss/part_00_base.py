"""Order-preserving QSS fragment: part_00_base."""

from __future__ import annotations

CONTENT = """

QPushButton#actionButton {
    border-radius: 10px;
    min-height: 38px;
    padding: 0 18px;
    font-size: 13pt;
    font-weight: 600;
}

QPushButton#actionButton[density="compact"] {
    border-radius: 9px;
    min-height: 34px;
    padding: 0 14px;
    font-size: 12pt;
}

QPushButton#actionButton[variant="primary"] {
    background: {{accent.primary}};
    border: 1px solid {{accent.primary}};
    color: {{white}};
    font-weight: 700;
}

QPushButton#actionButton[variant="primary"]:hover {
    background: {{accent.primary.hover}};
    border-color: {{accent.primary.hover}};
}

QPushButton#actionButton[variant="primary"]:pressed {
    background: {{accent.primary.pressed}};
    border-color: {{accent.primary.pressed}};
}

QPushButton#actionButton[variant="secondary"] {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.control}}, 0.18);
    color: {{text.body.warm}};
}

QPushButton#actionButton[variant="secondary"]:hover {
    background: {{bg.hover.secondary}};
}

QPushButton#actionButton[variant="quiet"] {
    background: transparent;
    border: 1px solid rgba({{border.control}}, 0.18);
    color: {{text.quiet}};
}

QPushButton#actionButton[variant="danger"] {
    background: rgba({{status.danger.alt}}, 0.10);
    border: 1px solid rgba({{status.danger.alt}}, 0.35);
    color: {{status.danger.deep}};
}

QPushButton#actionButton[variant="danger"]:hover {
    background: rgba({{status.danger.alt}}, 0.18);
    border-color: rgba({{status.danger.alt}}, 0.55);
}

QPushButton#actionButton[errorState="clean"] {
    background: rgba({{status.success.warm}}, 0.10);
    border: 1px solid rgba({{status.success.warm}}, 0.28);
    color: {{status.success.warm}};
}

QPushButton#actionButton[errorState="clean"]:hover {
    background: rgba({{status.success.warm}}, 0.16);
    border-color: rgba({{status.success.warm}}, 0.42);
}

QPushButton#actionButton[errorState="error"] {
    background: rgba({{status.danger.dark}}, 0.14);
    border: 1px solid rgba({{status.danger.dark}}, 0.48);
    color: {{status.danger.dark}};
}

QPushButton#actionButton[errorState="error"]:hover {
    background: rgba({{status.danger.dark}}, 0.22);
    border-color: rgba({{status.danger.dark}}, 0.66);
}

QPushButton#actionButton:disabled {
    background: rgba({{bg.control.hover}}, 0.6);
    border: 1px solid rgba({{border.default}}, 0.12);
    color: {{text.disabled}};
    opacity: 0.5;
}

QPushButton#actionButton:focus {
    outline: none;
    outline-offset: 0px;
}

QPushButton#actionButton[variant="primary"]:focus {
    border-color: {{accent.primary}};
    background: {{accent.primary}};
}

QPushButton#actionButton[variant="danger"]:focus,
QPushButton#actionButton[errorState="error"]:focus {
    outline: none;
    outline-offset: 0px;
}

QPushButton#actionButton:disabled:focus {
    border: 1px solid rgba({{border.default}}, 0.12);
    background: rgba({{bg.control.hover}}, 0.6);
}

QLineEdit#dashboardSearch {
    background: rgba({{bg.input.soft}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 10px;
    color: {{text.body.warm}};
    font-size: 13pt;
    padding: 8px 14px;
    min-height: 34px;
}

QLineEdit#dashboardSearch:focus {
    border: 1px solid rgba({{accent.primary}}, 0.55);
    outline: 2px solid rgba({{accent.primary}}, 0.35);
    outline-offset: 1px;
}

QLineEdit#dashboardSearch:hover {
    border: 1px solid rgba({{border.default}}, 0.32);
}

QPushButton#filterChip {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.muted}}, 0.14);
    border-radius: 11px;
    color: {{text.chapter.rail}};
    font-size: 12pt;
    font-weight: 600;
    padding: 5px 12px;
}

QPushButton#filterChip:checked {
    background: rgba({{accent.primary}}, 0.14);
    border-color: rgba({{accent.primary}}, 0.4);
    color: {{accent.deep}};
}

QFrame#surface {
    border-radius: __SURFACE_RADIUS__px;
}

QFrame#surface[tone="hero"] {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {{bg.hero.start}},
        stop: 0.45 {{bg.hero.mid}},
        stop: 1 {{bg.hero.end}}
    );
    border: 1px solid rgba({{border.soft}}, 0.22);
}

QFrame#surface[tone="elevated"] {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 {{bg.surface}},
        stop: 1 {{bg.surface.elevated}}
    );
    border: 1px solid rgba({{border.warm}}, 0.18);
}

QFrame#surface[tone="panel"] {
    background: rgba({{bg.surface}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.14);
}

QFrame#surface[tone="card"] {
    background: rgba({{bg.input.soft}}, 0.98);
    border: 1px solid rgba({{border.warm}}, 0.12);
}

QFrame#surface[tone="card"]:hover {
    border: 1px solid rgba({{accent.primary}}, 0.35);
}

QFrame#surface[tone="card"][compact="true"] {
    border-radius: __COMPACT_CARD_RADIUS__px;
    background: rgba({{bg.input.soft}}, 0.92);
}

QFrame#surface[tone="inset"] {
    background: rgba({{bg.inset}}, 0.9);
    border: 1px dashed rgba({{border.warm}}, 0.2);
}

QLabel#heroTitle {
    color: {{text.heading.deep}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 24pt;
    font-weight: 600;
}

QLabel#heroTitle[density="compact"] {
    font-size: 22pt;
}

QLabel#heroBody {
    color: {{text.secondary}};
    font-size: 13pt;
}

QLabel#heroBody[density="compact"] {
    font-size: 12pt;
}

QToolTip {
    background: {{bg.surface}};
    color: {{text.body.warm}};
    border: 1px solid rgba({{border.default}}, 0.28);
    border-radius: __TOOLTIP_RADIUS__px;
    padding: 8px;
    font-size: 12pt;
}

QScrollArea#dashboardJobsScroll {
    border: none;
    background: transparent;
}

QWidget#dashboardTaskFlowContent,
QScrollArea#dashboardJobsScroll > QWidget > QWidget {
    background: rgba({{bg.control}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 14px;
}

/* Rounded corners for inner widgets inside Surface cards */
QFrame#surface QListWidget,
QFrame#surface QTextEdit {
    border-radius: __COMPACT_CARD_RADIUS__px;
    border: 1px solid rgba({{border.default}}, 0.12);
}

QFrame#surface[tone="card"] QListWidget,
QFrame#surface[tone="card"] QTextEdit {
    background: rgba({{bg.input}}, 0.6);
}

QFrame#surface[tone="panel"] QListWidget,
QFrame#surface[tone="panel"] QTextEdit {
    background: rgba({{bg.surface}}, 0.8);
}

/* Top bar project selector */
QComboBox#topBarProjectSelector {
    background: rgba({{bg.input}}, 0.85);
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.heading}};
    font-size: 12pt;
    min-height: 32px;
    padding: 0 12px;
}

QComboBox#topBarProjectSelector::drop-down {
    border-left: 1px solid rgba({{border.default}}, 0.15);
    width: 28px;
}

QComboBox#topBarProjectSelector QAbstractItemView {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.2);
    color: {{text.heading}};
    selection-background-color: rgba({{accent.primary}}, 0.12);
}

/* Avatar tag buttons in voice studio */
QPushButton#avatarTag {
    border-radius: __CHIP_RADIUS__px;
    border: 1px solid rgba({{border.default}}, 0.18);
    background: rgba({{bg.input}}, 0.8);
    color: {{text.heading}};
    font-size: 11pt;
    padding: 4px 12px;
    min-height: 32px;
}

QPushButton#avatarTag:hover {
    border: 1px solid rgba({{accent.primary}}, 0.35);
    background: rgba({{accent.primary}}, 0.08);
}

QPushButton#avatarTag:checked {
    border: 1px solid rgba({{accent.primary}}, 0.5);
    background: rgba({{accent.primary}}, 0.15);
    color: {{accent.primary}};
    font-weight: 700;
}

/* Dubbing script browser */
QTextBrowser#dubbingScriptBrowser {
    border-radius: __COMPACT_CARD_RADIUS__px;
    border: 1px solid rgba({{border.default}}, 0.12);
    background: rgba({{bg.surface}}, 0.8);
    color: {{text.body.warm}};
    font-size: 13pt;
    padding: 8px;
}

/* Avatar scroll area */
QScrollArea#avatarScrollArea {
    border: none;
    background: transparent;
}

QScrollArea#avatarScrollArea > QWidget > QWidget {
    background: transparent;
}

/* Voice studio tabs frame — unified bordered container matching 卷帙 page style */
QFrame#voiceStudioTabsFrame {
    background: rgba({{bg.panel}}, 0.42);
    border: 1px solid rgba({{border.default}}, 0.11);
    border-radius: __COMPACT_CARD_RADIUS__px;
    padding: 4px 8px 4px 8px;
}

/* Voice studio tabs */
QTabWidget#voiceStudioTabs::pane {
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: __COMPACT_CARD_RADIUS__px;
    background: transparent;
}

QTabBar#voiceStudioTabsBar::tab {
    border-radius: __CHIP_RADIUS__px;
    padding: 6px 16px;
    margin-right: 4px;
}

QTabBar#voiceStudioTabsBar::tab:selected {
    background: rgba({{accent.primary}}, 0.10);
    color: {{accent.primary}};
    font-weight: 700;
}

/* Audio player placeholder */
QLabel#audioPlayerPlaceholder {
    border-radius: __COMPACT_CARD_RADIUS__px;
    border: 2px dashed rgba({{border.default}}, 0.20);
    background: rgba({{bg.surface}}, 0.5);
    color: {{text.muted}};
    font-size: 13pt;
}

/* Reusable staged-loading surface. Skeletons paint their own token-aware
   shimmer; QSS only provides the stable text hierarchy around them. */
QWidget#loadingState {
    background: rgba({{bg.surface}}, 0.58);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: __COMPACT_CARD_RADIUS__px;
}

QLabel#loadingStateMessage {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 600;
}

QLabel#loadingStateDetail {
    color: {{text.muted}};
    font-size: 10.5pt;
}

"""
