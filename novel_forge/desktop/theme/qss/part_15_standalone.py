"""QSS fragment for standalone page components.

Migrates inline ``setStyleSheet()`` calls that used ``resolve_qcolor()`` from:
- ``pages/standalone/subplot_manager.py`` (priority badge)
- ``pages/standalone/outline_editor.py`` (suggestion/overview cards, toolbar, inputs)
- ``pages/standalone/token_analytics.py`` (tab pricing/filter styles)
- ``components/checkpoint_dialog.py`` (option cards, bubble panel)

Rules use ``{{token.name}}`` placeholders resolved per-theme by ``get_stylesheet()``.
Dynamic colours use property selectors (``[priority="..."]``, ``[recommended="..."]``)
so theme switches are picked up automatically without per-widget ``refresh_theme_colors``.
"""

from __future__ import annotations

from novel_forge.desktop.tokens.radius import DIALOG_RADIUS

_CONTENT_TEMPLATE = """

/* ═══════════════  Subplot Priority Badge  ════════════════════════════ */

QLabel#subplotPriorityBadge {
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 600;
}

QLabel#subplotPriorityBadge[priority="primary"] {
    background: rgba({{status.danger.deep}}, 0.10);
    color: {{status.danger.deep}};
}

QLabel#subplotPriorityBadge[priority="normal"] {
    background: rgba({{text.chapter.rail}}, 0.08);
    color: {{text.chapter.rail}};
}

QLabel#subplotPriorityBadge[priority="background"] {
    background: rgba({{text.muted}}, 0.08);
    color: {{text.muted}};
}

/* ═══════════════  Outline Editor - Suggestion Card  ══════════════════ */

QFrame#suggestionCard {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
}

QLabel#suggestionTag {
    background: rgba({{status.info}}, 0.14);
    color: {{status.info}};
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 600;
}

QLabel#suggestionBody {
    color: {{text.artifact}};
    line-height: 1.35;
}

/* ═══════════════  Outline Editor - Chapter Overview Card  ════════════ */

QFrame#chapterOverviewCard {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 9px;
}

QLabel#overviewNum {
    font-size: 11px;
    font-weight: 700;
    color: {{accent.primary}};
}

QLabel#overviewTitle {
    font-size: 12px;
    font-weight: 600;
    color: {{text.primary}};
}

QLabel#overviewGoal {
    font-size: 10px;
    color: {{text.muted}};
    padding: 1px 0;
}

/* ═══════════════  Outline Editor - Extend Dialog  ════════════════════ */

QLabel#extendDialogTitle {
    font-size: 13px;
    font-weight: 700;
    color: {{text.heading}};
}

QLabel#extendDialogSummary {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 8px;
    padding: 8px;
    color: {{text.quiet}};
    font-size: 12px;
}

/* ═══════════════  Outline Editor - Toolbar & Inputs  ═════════════════ */

QWidget#outlineToolbar {
    background: rgba({{bg.workspace}}, 0.6);
}

QFrame#polishCommandPanel {
    background: rgba({{bg.surface}}, 0.78);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 10px;
}

QLineEdit#outlineHintInput {
    padding: 3px 8px;
    border: 1px solid rgba({{border.default}}, 0.25);
    border-radius: 7px;
    background: {{bg.surface}};
    font-size: 12px;
}

/* ═══════════════  Checkpoint Dialog  ═════════════════════════════════ */

#checkpointDialogPanel {
    background-color: {{bg.surface}};
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

#checkpointDialogContent {
    background-color: {{bg.surface}};
}

QScrollArea#checkpointDialogScroll,
QScrollArea#checkpointDialogScroll::viewport {
    background-color: {{bg.surface}};
    border: none;
}

#checkpointDialogSummary {
    background-color: {{bg.inset}};
    border-left: 3px solid {{accent.primary}};
    border-radius: __DIALOG_RADIUS__px;
}

#checkpointDialogSummary QLabel {
    color: {{text.primary}};
    font-size: 13px;
    line-height: 1.45;
}

QLabel#cardTitle {
    color: {{text.primary}};
    font-size: 13px;
    font-weight: 700;
    line-height: 1.25;
}

QLabel#cardBody {
    color: {{text.secondary}};
    font-size: 12px;
    line-height: 1.35;
}

/* ═══════════════  Character Bible Editor  ═════════════════════════════ */

/*
 * QScrollArea defaults to the native palette when neither its viewport nor
 * content widget paints a background.  The character dossier therefore used
 * to keep a grey rectangle after switching desktop themes.  Paint all three
 * layers from the semantic panel token so the long read and edit forms stay
 * within the active theme.
 */
QScrollArea#characterProfileReadScroll,
QScrollArea#characterProfileReadScroll::viewport,
QWidget#characterProfileReadContent,
QScrollArea#characterProfileFormScroll,
QScrollArea#characterProfileFormScroll::viewport,
QWidget#characterProfileFormContent {
    background: {{bg.panel}};
    border: none;
}

/* Option card - recommended vs not */
#checkpointOptionCard[recommended="true"] {
    background: rgba({{accent.primary}}, 0.08);
    border: 1px solid rgba({{accent.primary}}, 0.35);
    border-radius: __COMPACT_CARD_RADIUS__px;
}

#checkpointOptionCard[recommended="true"]:hover {
    background: rgba({{accent.primary}}, 0.14);
}

#checkpointOptionCard[recommended="false"] {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __COMPACT_CARD_RADIUS__px;
}

#checkpointOptionCard[recommended="false"]:hover {
    background: rgba({{bg.surface}}, 0.98);
}

/* Mini button - recommended (filled) vs not (outline) */
QPushButton#checkpointOptionMiniButton {
    border-radius: 11px;
    font-size: 11px;
    font-weight: 700;
    padding: 0;
}

QPushButton#checkpointOptionMiniButton[recommended="true"] {
    background: rgba({{accent.primary}}, 0.95);
    border: 1px solid {{accent.primary}};
    color: {{bg.surface}};
}

QPushButton#checkpointOptionMiniButton[recommended="true"]:hover {
    background: {{accent.primary}};
}

QPushButton#checkpointOptionMiniButton[recommended="false"] {
    background: rgba({{bg.surface}}, 0.78);
    border: 1px solid rgba({{border.default}}, 0.22);
    color: {{text.primary}};
}

QPushButton#checkpointOptionMiniButton[recommended="false"]:hover {
    background: rgba({{accent.primary}}, 0.08);
    border-color: rgba({{accent.primary}}, 0.32);
}

/* Notes area */
QTextEdit#checkpointDialogNotes {
    background: rgba({{bg.surface}}, 0.95);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __COMPACT_CARD_RADIUS__px;
    padding: 8px;
    color: {{text.primary}};
}

/* Close button */
QPushButton#checkpointDialogClose {
    background: transparent;
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 12px;
    color: {{text.secondary}};
    font-size: 15px;
    font-weight: 700;
}

QPushButton#checkpointDialogClose:hover {
    background: rgba({{accent.primary}}, 0.10);
    border-color: rgba({{accent.primary}}, 0.28);
    color: {{text.primary}};
}

/* ═══════════════  Token Analytics Tab  ═══════════════════════════════ */

QWidget#tokenAnalyticsTab QLabel#viewerFilterLabel {
    font-size: 11px;
}

QWidget#tokenAnalyticsTab QComboBox#projectSelector,
QWidget#tokenAnalyticsTab QDoubleSpinBox {
    min-height: 26px;
    font-size: 12px;
    padding: 2px 8px;
}

QWidget#tokenAnalyticsTab QPushButton#actionButton {
    min-height: 28px;
    font-size: 12px;
    padding: 4px 12px;
}

QWidget#tokenAnalyticsTab QTabBar::tab {
    min-width: 102px;
    min-height: 22px;
    font-size: 10px;
    padding: 3px 16px;
    border-radius: 10px 10px 0 0;
    margin-right: 4px;
}

QWidget#tokenAnalyticsTab QTabBar::tab:selected {
    font-weight: 700;
}

QWidget#tokenAnalyticsTab QTabBar::tab:hover:!selected {
    color: {{text.tab.hover}};
}

QWidget#tokenAnalyticsTab QWidget#tokenPricingPage {
    background: rgba({{bg.input.soft}}, 0.75);
}

QWidget#tokenAnalyticsTab QLabel#tokenPricingHint {
    color: {{text.muted}};
    font-size: 11px;
}

QWidget#tokenAnalyticsTab QLabel#tokenPricingSectionTitle {
    color: {{text.guidance}};
    font-size: 12px;
    font-weight: 700;
}

QWidget#tokenAnalyticsTab QWidget#tokenPricingRow {
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 8px;
    background: rgba({{bg.input}}, 0.6);
}

QWidget#tokenAnalyticsTab QLabel#tokenPricingRowTitle {
    color: {{text.body.warm}};
    font-size: 11px;
}

QWidget#tokenFilterBar {
    background: rgba({{bg.inset}}, 0.55);
    border-bottom: 1px solid rgba({{border.default}}, 0.14);
}

QWidget#tokenFilterBar QPushButton#filterChip {
    font-size: 11px;
    padding: 2px 10px;
    min-height: 22px;
}

"""

CONTENT = _CONTENT_TEMPLATE.replace("__DIALOG_RADIUS__", str(DIALOG_RADIUS))
