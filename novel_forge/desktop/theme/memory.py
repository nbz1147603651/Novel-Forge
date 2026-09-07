"""Memory component styles for the desktop application."""

from __future__ import annotations

CONTENT = """
/* ═══════════════  Memory Components  ════════════════════════════════════════ */

/* --- Memory panel --- */
QWidget#memoryPanelContent {
    background: transparent;
}

QWidget#memoryPanelRoot {
    background: transparent;
    border: none;
}

QWidget#memoryTabPage {
    background: transparent;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
}

QLabel#memoryTitle {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 700;
}

QLabel#memoryMeta {
    color: {{text.muted.soft}};
    font-size: 11pt;
}

QLabel#memoryBody {
    color: {{text.body}};
    font-size: 12pt;
}

QLabel#memoryHint {
    color: {{text.muted}};
    font-size: 11pt;
}

QLabel#memoryBadge {
    font-size: 10pt;
}

QFrame#surface[tone="inset"][memoryHover="true"]:hover {
    border: 1px solid rgba({{accent.primary}}, 0.30);
    background: {{bg.memory.hover}};
}

QTabWidget#memoryTabs::pane {
    background: transparent;
    border: none;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
}

QTabWidget#memoryTabs > QStackedWidget,
QTabWidget#memoryTabs > QStackedWidget > QWidget#memoryTabPage {
    background: transparent;
    border: none;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
}

QScrollArea#memoryTabScroll {
    border: none;
    background: transparent;
}

QScrollArea#memoryTabScroll > QWidget#qt_scrollarea_viewport,
QScrollArea#memoryTabScroll > QWidget#qt_scrollarea_viewport > QWidget#memoryPanelContent {
    background: transparent;
    border: none;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
}

QTabWidget#memoryTabs QTabBar::tab {
    background: rgba({{bg.control}}, 0.80);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    color: {{text.tab}};
    font-size: 10pt;
    font-weight: 600;
    padding: 3px 5px;
    margin-right: 0;
    min-width: 24px;
    min-height: 18px;
}

QTabWidget#memoryTabs QTabBar::tab:selected {
    background: rgba({{bg.surface}}, 0.98);
    border-color: rgba({{border.default}}, 0.20);
    color: {{text.heading}};
}

QTabWidget#memoryTabs QTabBar::tab:hover:!selected {
    background: rgba({{bg.hover.accent}}, 0.92);
    color: {{text.tab.hover}};
}

QWidget#memoryTabsCorner {
    background: transparent;
}

/* --- Outline tracker --- */
QWidget#outlineTrackerContent {
    background: transparent;
}

QWidget#outlineTrackerBadge {
    background: {{status.success.bg}};
    border: 1px solid {{status.success.border}};
    border-radius: 10px;
    padding: 2px 8px;
}

QWidget#outlineTrackerBadgeWarning {
    background: {{status.warning.bg}};
    border: 1px solid {{status.warning.border}};
    border-radius: 10px;
    padding: 2px 8px;
}

/* --- Review panel --- */
QLabel#memoryReviewDetailLabel {
    font-size: 12pt;
    color: {{text.memory.review}};
    font-weight: 600;
}

/* --- Warning markers --- */
QLabel#memoryWarningMarker {
    color: {{status.warning.alt}};
    font-weight: 600;
    font-size: 14pt;
}

/* --- Issue card elements --- */
QLabel#memoryEvidenceLabel {
    font-style: italic;
    color: {{text.memory.category}};
}

QLabel#memoryFixLabel {
    color: {{text.memory.fix}};
}

QLabel#memoryAffectedLabel {
    font-size: 10pt;
    color: {{text.memory.last}};
}

QLabel#memoryLocLabel {
    font-size: 10pt;
    color: {{text.muted}};
}

QLabel#memoryFixHintLabel {
    color: {{text.muted}};
    font-size: 11pt;
}

/* --- Hint labels (warning/success states) --- */
QLabel#memoryHintWarning {
    color: {{status.warning.alt}};
    font-size: 12pt;
}

QLabel#memoryHintSuccess {
    color: {{text.memory.running}};
    font-size: 12pt;
}

/* --- Outline tracker badge --- */
QLabel#outlineTrackerIcon {
    font-size: 11pt;
}

QLabel#outlineTrackerLabelDefault {
    font-size: 10pt;
    color: {{status.success}};
    font-weight: 500;
}

QLabel#outlineTrackerLabelWarning {
    font-size: 10pt;
    color: {{status.warning}};
    font-weight: 500;
}

QLabel#outlineTrackerLabelInactive {
    font-size: 10pt;
    color: {{outline.ignored}};
    font-weight: 500;
}

/* --- Relationship cards --- */
QLabel#memoryRelTitle {
    font-weight: 600;
    color: {{text.heading}};
    font-size: 12pt;
}

QLabel#memoryRelType {
    color: {{text.memory.category}};
    font-size: 10pt;
}

QLabel#memoryRelEvents {
    color: {{text.memory.fix}};
    font-size: 10pt;
}

/* --- Theme cards --- */
QLabel#memoryThemeName {
    font-weight: 600;
    color: {{text.heading}};
    font-size: 12pt;
}

QLabel#memoryThemeCount {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 10pt;
}

QLabel#memoryThemeChapters {
    color: {{text.memory.category}};
    font-size: 10pt;
}

/* --- Thread cards --- */
QLabel#memoryThreadTitleActive {
    font-weight: 600;
    color: {{status.warning}};
    font-size: 12pt;
}

QLabel#memoryThreadTitleResolved {
    font-weight: 600;
    color: {{text.memory.category}};
    font-size: 12pt;
}

QLabel#memoryThreadName {
    font-weight: 600;
    color: {{text.heading}};
    font-size: 12pt;
}

QLabel#memoryThreadIntro {
    font-size: 10pt;
    color: {{text.memory.category}};
}

QLabel#memoryThreadResolved {
    color: {{text.memory.fix}};
    font-size: 10pt;
}

/* --- Guidance panel --- */
QLabel#memoryGuidanceTitle {
    color: {{text.guidance}};
    font-weight: 700;
    font-size: 12pt;
}

QPushButton#memoryGuidanceDismiss {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border: none;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 10pt;
}

QPushButton#memoryGuidanceDismiss:hover {
    background: {{bg.memory.hover}};
}

QLabel#memoryGuidanceIssues {
    color: {{text.memory.category}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryGuidanceIssueText {
    font-size: 11pt;
    padding-left: 8px;
    color: {{text.body}};
}

QLabel#memoryGuidanceLesson {
    color: {{status.success.deep}};
    font-size: 10pt;
    padding-left: 16px;
}

QLabel#memoryGuidanceRec {
    color: {{status.success.deep}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryGuidanceStrategy {
    color: {{status.success.deep}};
    font-size: 11pt;
    padding-left: 8px;
}

QLabel#memoryGuidanceAvoid {
    color: {{status.danger}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryGuidanceAvoidStrategy {
    color: {{status.danger}};
    font-size: 11pt;
    padding-left: 8px;
}

QLabel#memoryGuidanceRateHigh {
    background: {{status.success.rate}};
    color: {{status.success.deep}};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10pt;
    font-weight: 600;
}

QLabel#memoryGuidanceRateMid {
    background: {{status.warning.rate}};
    color: {{status.warning.text}};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10pt;
}

QLabel#memoryGuidanceRateLow {
    background: {{status.danger.rate}};
    color: {{status.danger.rate.text}};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10pt;
}

QLabel#memoryGuidanceWarning {
    color: {{status.warning.text}};
    font-size: 10pt;
}

/* --- Score display (dynamic color applied via inline style) --- */
QLabel#memoryScoreValue {
    font-size: 16pt;
    font-weight: bold;
}

/* --- Issue type tag --- */
QLabel#memoryIssueTag {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 10pt;
}

/* --- Small badge override for issue cards --- */
Badge#memorySmallBadge {
    padding: 1px 5px;
    font-size: 10pt;
    border-radius: 5px;
    font-weight: 600;
}

"""
