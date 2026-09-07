"""Order-preserving QSS fragment: part_10_memory."""

from __future__ import annotations

CONTENT = """/* ── Memory components ─────────────────────────────────────────── */

QLabel#memoryHealthTitle {
    font-size: 13pt;
    font-weight: 600;
    color: {{memory.health}};
}

QLabel#memoryHealthStatus {
    font-size: 12pt;
    font-weight: 500;
    padding: 6px;
    border-radius: 4px;
}

QLabel#memoryHealthStatus[tone="success"] {
    color: {{status.success.warm}};
    background-color: {{slate.bg}};
}

QLabel#memoryHealthStatus[tone="warning"] {
    color: {{accent.badge}};
    background-color: {{slate.bg}};
}

QLabel#memoryHealthStatus[tone="muted"] {
    color: {{text.muted.badge}};
    background-color: {{slate.bg}};
}

QLabel#memoryHealthStatus[tone="danger"] {
    color: {{status.danger.deep}};
    background-color: {{slate.bg}};
}

QLabel#memoryModuleName {
    font-size: 12pt;
    color: {{memory.health}};
}

QLabel#memorySeverityIcon {
    font-size: 12pt;
}

QLabel#memorySeverityLabel {
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryCategory {
    color: {{text.memory.category}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryMotifName {
    color: {{text.heading}};
    font-weight: 600;
    font-size: 12pt;
}

QLabel#memoryOccurrence {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 10pt;
}

QLabel#memoryLastChapter {
    color: {{text.memory.last}};
    font-size: 10pt;
}

QLabel#memoryContextTitle {
    font-size: 11pt;
}

QLabel#memoryContextTitle[highlight="true"] {
    color: {{status.warning.alt}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryPriorityDot {
    border-radius: 3px;
    min-width: 6px;
    max-width: 6px;
    min-height: 6px;
    max-height: 6px;
}

QLabel#memoryPriorityDot[priority="high"] {
    background: {{status.warning.alt}};
}

QLabel#memoryPriorityDot[priority="medium"] {
    background: {{text.memory.category}};
}

QLabel#memoryPriorityDot[priority="low"] {
    background: {{text.memory.last}};
}

QLabel#memoryPriorityCat {
    font-size: 11pt;
}

QLabel#memoryPriorityCat[priority="high"] {
    color: {{status.warning.alt}};
}

QLabel#memoryPriorityCat[priority="medium"] {
    color: {{text.memory.category}};
}

QLabel#memoryPriorityCat[priority="low"] {
    color: {{text.memory.last}};
}

QLabel#memoryPriorityName {
    color: {{text.heading}};
    font-weight: 600;
}

QLabel#memoryPriorityName[priority="high"] {
    color: {{status.warning.alt}};
}

QLabel#memoryPriorityName[priority="medium"] {
    color: {{text.memory.category}};
}

QLabel#memoryPriorityName[priority="low"] {
    color: {{text.memory.last}};
}

QLabel#memoryIssueType {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 10pt;
}

QLabel#memoryIssueBadge {
    padding: 1px 5px;
    font-size: 10pt;
    border-radius: 5px;
    font-weight: 600;
}

QLabel#memoryIssueLocation {
    font-size: 10pt;
    color: {{text.muted}};
}

QLabel#memoryIssueFixHint {
    color: {{text.muted}};
    font-size: 11pt;
}

QLabel#memorySeverityDot {
    border-radius: 4px;
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
}

QLabel#memorySeverityDot[severity="critical"] {
    background: {{status.danger.critical}};
}

QLabel#memorySeverityDot[severity="high"] {
    background: {{status.warning}};
}

QLabel#memorySeverityDot[severity="medium"] {
    background: {{status.warning.alt}};
}

QLabel#memorySeverityDot[severity="low"] {
    background: {{text.memory.category}};
}

QLabel#memorySeverityLabel {
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memorySeverityLabel[severity="critical"] {
    color: {{status.danger.critical}};
}

QLabel#memorySeverityLabel[severity="high"] {
    color: {{status.warning}};
}

QLabel#memorySeverityLabel[severity="medium"] {
    color: {{status.warning.alt}};
}

QLabel#memorySeverityLabel[severity="low"] {
    color: {{text.memory.category}};
}

QLabel#memoryEvidenceText {
    font-style: italic;
}

QLabel#memoryFixLabel {
    color: {{text.memory.fix}};
}

QLabel#memoryAffectedLabel {
    font-size: 10pt;
    color: {{text.memory.last}};
}

QLabel#memoryRepairHint {
    font-size: 12pt;
}

QLabel#memoryRepairHint[state="idle"] {
    color: {{status.warning.alt}};
}

QLabel#memoryRepairHint[state="running"] {
    color: {{text.memory.running}};
}

QLabel#memoryRelationsHint {
    font-size: 12pt;
}

QLabel#memoryRelationsHint[state="idle"] {
    color: {{status.warning.alt}};
}

QLabel#memoryRelationsHint[state="done"] {
    color: {{text.memory.running}};
}

QPushButton#memorySmallButton {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.control}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.body.warm}};
    font-size: 11pt;
    font-weight: 600;
    padding: 4px 10px;
    min-height: 24px;
}

QPushButton#memorySmallButton[variant="primary"] {
    background: {{accent.primary}};
    border: 1px solid {{accent.primary}};
    color: {{bg.surface.elevated}};
}

QPushButton#memorySmallButton[variant="primary"]:hover {
    background: {{accent.primary.hover}};
}

QPushButton#memorySmallButton[variant="secondary"]:hover {
    background: {{bg.hover.secondary}};
}

QPushButton#memorySmallButton[variant="quiet"] {
    background: transparent;
    border: 1px solid rgba({{border.control}}, 0.18);
    color: {{text.quiet}};
}

QPushButton#memorySmallButton[variant="danger"] {
    background: rgba({{status.danger.alt}}, 0.10);
    border: 1px solid rgba({{status.danger.alt}}, 0.35);
    color: {{status.danger.deep}};
}

QPushButton#memorySmallButton[variant="danger"]:hover {
    background: rgba({{status.danger.alt}}, 0.18);
    border-color: rgba({{status.danger.alt}}, 0.55);
}

QPushButton#memorySmallButton:disabled {
    background: rgba({{bg.control.hover}}, 0.6);
    border: 1px solid rgba({{border.default}}, 0.12);
    color: {{text.disabled}};
}

QLabel#memoryReviewPrevTitle {
    font-size: 13pt;
    font-weight: bold;
    color: {{text.artifact}};
}

QLabel#memoryReviewScoreLabel {
    font-size: 13pt;
    font-weight: bold;
    color: {{text.artifact}};
}

QLabel#memoryReviewScoreValue {
    font-size: 16pt;
    font-weight: bold;
}

QLabel#memoryReviewScoreValue[grade="none"] {
    color: {{text.muted}};
}

QLabel#memoryReviewScoreValue[grade="pass"] {
    color: {{status.success.deep}};
}

QLabel#memoryReviewScoreValue[grade="warn"] {
    color: {{status.warning.alt}};
}

QLabel#memoryReviewScoreValue[grade="fail"] {
    color: {{status.danger.critical}};
}

QLabel#memoryReviewDetailLabel {
    font-size: 12pt;
    color: {{text.memory.review}};
}

QLabel#memoryReviewDetailValue {
    font-size: 11pt;
    color: {{text.memory.review.value}};
}

QLabel#memoryWarningTitle {
    font-weight: 600;
}

QPushButton#memoryWarningClearBtn {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.control}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.body.warm}};
    font-size: 11pt;
    font-weight: 600;
    padding: 3px 8px;
    min-height: 22px;
}

QPushButton#memoryWarningClearBtn:hover {
    background: {{bg.hover.secondary}};
}

QPushButton#memoryWarningClearBtn:disabled {
    background: rgba({{bg.control.hover}}, 0.6);
    border: 1px solid rgba({{border.default}}, 0.12);
    color: {{text.disabled}};
}

QLabel#memoryOutlineIcon {
    font-size: 11pt;
}

QLabel#memoryOutlineLabel {
    font-size: 10pt;
    font-weight: 500;
}

QLabel#memoryOutlineLabel[state="tracked"] {
    color: {{status.success}};
}

QLabel#memoryOutlineLabel[state="pending"] {
    color: {{status.warning}};
}

QLabel#memoryOutlineLabel[state="resolved"] {
    color: {{status.success}};
}

QLabel#memoryOutlineLabel[state="ignored"] {
    color: {{outline.ignored}};
}

QFrame#memoryOutlineTracker {
    border-radius: 10px;
    padding: 3px 8px;
    border: 1.5px solid rgba({{border.warm}}, 0.22);
    background: rgba({{bg.control.hover}}, 0.14);
}

QLabel#memorySubplotTitle {
    font-weight: 600;
    color: {{text.heading}};
    font-size: 12pt;
}

QLabel#memorySubplotType {
    color: {{text.memory.category}};
    font-size: 10pt;
}

QLabel#memorySubplotEvents {
    color: {{text.memory.fix}};
    font-size: 10pt;
}

QLabel#memorySubplotName {
    font-weight: 600;
    color: {{text.heading}};
    font-size: 12pt;
}

QLabel#memorySubplotCount {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 10pt;
}

QLabel#memorySubplotChapters {
    color: {{text.memory.category}};
    font-size: 10pt;
}

QLabel#memorySubplotActiveTitle {
    font-weight: 600;
    color: {{status.warning}};
}

QLabel#memorySubplotResolvedTitle {
    font-weight: 600;
    color: {{text.memory.category}};
}

QLabel#memorySubplotItemName {
    font-weight: 600;
}

QLabel#memorySubplotItemStatus {
    font-size: 10pt;
}

QLabel#memorySubplotItemStatus[status="active"] {
    color: {{status.warning}};
}

QLabel#memorySubplotItemStatus[status="resolved"] {
    color: {{status.success.warm}};
}

QLabel#memorySubplotItemStatus[status="dormant"] {
    color: {{text.muted.badge}};
}

QLabel#memorySubplotIntro {
    font-size: 10pt;
}

QLabel#memorySubplotResolved {
    color: {{text.memory.fix}};
    font-size: 10pt;
}

QLabel#memoryLessonTitle {
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryLessonTitle[type="rec"] {
    color: {{status.success.deep}};
}

QLabel#memoryLessonTitle[type="avoid"] {
    color: {{status.danger}};
}

QLabel#memoryLessonStrategy {
    font-size: 11pt;
}

QLabel#memoryLessonRate {
    font-size: 10pt;
    font-weight: 600;
}

QLabel#memoryLessonRate[rate="high"] {
    color: {{status.success.warm}};
}

QLabel#memoryLessonRate[rate="medium"] {
    color: {{accent.badge}};
}

QLabel#memoryLessonRate[rate="low"] {
    color: {{status.danger.deep}};
}

QLabel#memoryLessonWarning {
    color: {{status.warning.text}};
    font-size: 10pt;
}

QLabel#memoryDismissBtn {
    font-size: 10pt;
    color: {{text.muted}};
    padding: 2px 6px;
    border-radius: 4px;
}

QLabel#memoryIssuesLabel {
    color: {{text.memory.category}};
    font-weight: 600;
    font-size: 11pt;
}

QLabel#memoryIssueItem {
    font-size: 11pt;
    padding-left: 8px;
}

QLabel#memoryLessonText {
    font-size: 11pt;
    color: {{text.body.alt}};
}

QLabel#windowTaskLabel {
    color: {{text.muted}};
    font-size: 13pt;
    margin-left: 8px;
}

QLabel#windowStatusLabel {
    color: {{text.disabled}};
    font-size: 12pt;
}

QLabel#windowHintLabel {
    color: {{status.success.warm}};
    font-size: 12pt;
}

QLabel#workflowPhaseHint {
    color: {{text.workflow}};
}

QLabel#workflowWarningLabel {
    color: {{status.warning.alt}};
}

QLabel#chapterStudioJobLabel {
    color: {{text.workflow}};
}

QLabel#dashboardWarningLabel {
    color: {{status.warning.alt}};
}

QLabel#subplotSep {
    background: rgba({{border.default}}, 0.08);
}

QLabel#subplotToggleIndicator {
    border-radius: 2px;
    min-width: 4px;
    max-width: 4px;
    min-height: 4px;
    max-height: 4px;
}

QLabel#subplotName {
    color: {{text.heading}};
    font-size: 12pt;
    font-weight: 600;
}

QLabel#subplotBadge {
    background: {{bg.memory.tag}};
    color: {{text.memory.category}};
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 10pt;
}

QLabel#subplotChapters {
    color: {{text.muted}};
    font-size: 11pt;
}

QLabel#subplotDesc {
    color: {{text.tab.hover}};
    font-size: 12pt;
    line-height: 1.6;
}

QLabel#subplotEvents {
    color: {{text.muted.strong}};
    font-size: 11pt;
    font-weight: 600;
    margin-top: 4px;
}

QLabel#subplotEventItem {
    color: {{text.body.alt}};
    font-size: 11pt;
    line-height: 1.5;
}

QLabel#subplotMore {
    color: {{text.muted}};
    font-size: 11pt;
    font-style: italic;
}

QLabel#subplotWeave {
    color: {{text.muted.strong}};
    font-size: 11pt;
    font-weight: 600;
    margin-top: 4px;
}

QLabel#subplotLinkItem {
    color: {{text.body.alt}};
    font-size: 11pt;
    line-height: 1.5;
}

QLabel#subplotRes {
    color: {{text.muted}};
    font-size: 11pt;
    font-style: italic;
}

QLabel#subplotInfo {
    color: {{text.muted.strong}};
    font-size: 12pt;
    margin-bottom: 8px;
}

QLabel#subplotEmpty {
    color: {{text.muted}};
    font-size: 12pt;
    padding: 20px;
}

QLabel#subplotArcList {
    color: {{text.body.alt}};
    font-size: 12pt;
}

QLabel#subplotSelectLabel {
    font-weight: 600;
    font-size: 13pt;
    color: {{text.primary}};
}

QLabel#subplotProgress {
    color: {{text.muted}};
    font-size: 12pt;
}

QLabel#subplotResult {
    color: {{text.subplot.result}};
    font-size: 12pt;
    padding: 8px;
    background: rgba({{status.success.job}}, 0.08);
    border-radius: 6px;
}

QPushButton#subplotToggleBtn {
    background: transparent;
    border: none;
    color: {{text.muted}};
    font-size: 10pt;
    padding: 0;
}

QCheckBox#subplotCheck {
    font-size: 12pt;
    color: {{text.tab.hover}};
    padding: 4px 0;
}

QLabel#subplotInfo {
    color: {{text.muted.strong}};
    font-size: 12pt;
    margin-bottom: 8px;
}

"""
