"""Order-preserving QSS fragment: part_03_humanize."""

from __future__ import annotations

CONTENT = """/* ── Humanize library dashboard ─────────────────── */

QFrame#humanizeLibraryCard {
    background: rgba({{bg.input.soft}}, 0.96);
    border: 1px solid rgba({{border.warm}}, 0.18);
    border-radius: __COMPACT_CARD_RADIUS__px;
}

QFrame#humanizeLibraryCard:hover {
    border-color: rgba({{accent.primary}}, 0.32);
}

QLabel#cardBadge {
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 11pt;
    font-weight: 700;
}

QLabel#cardBadge[tone="default"] {
    background: rgba({{border.default}}, 0.10);
    color: {{text.badge.default}};
}

QLabel#cardBadge[tone="success"] {
    background: rgba({{status.success.warm}}, 0.12);
    color: {{status.success.warm}};
}

QLabel#cardBadge[tone="warning"] {
    background: rgba({{status.warning.alt}}, 0.12);
    color: {{status.warning.alt}};
}

QLabel#cardBadge[tone="danger"] {
    background: rgba({{status.danger.alt}}, 0.12);
    color: {{status.danger.deep}};
}

QLabel#cardStats {
    color: {{text.body}};
    font-size: 13pt;
    line-height: 1.45;
}

QWidget#humanizeLibraryDashboard {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 {{bg.dialog}},
        stop: 1 {{bg.workspace}}
    );
}

QWidget#humanizeDashboardHeader {
    background: rgba({{bg.surface}}, 0.96);
    border-bottom: 1px solid rgba({{border.default}}, 0.12);
}

QWidget#humanizeDashboardControls {
    background: rgba({{bg.surface}}, 0.90);
    border-bottom: 1px solid rgba({{border.default}}, 0.12);
}

QWidget#humanizeLibraryDashboard QLabel#filterLabel {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 700;
}

QWidget#humanizeLibraryDashboard QComboBox {
    min-height: 34px;
    border-radius: __CHIP_RADIUS__px;
    font-weight: 600;
}

QLineEdit#filterSearchEdit {
    min-height: 34px;
    border-radius: __CHIP_RADIUS__px;
}

QFrame#humanizeErrorBanner {
    background: rgba({{status.danger.alt}}, 0.10);
    border-top: 1px solid rgba({{status.danger.alt}}, 0.18);
    border-bottom: 1px solid rgba({{status.danger.alt}}, 0.18);
}

QLabel#errorBannerText {
    color: {{status.danger.deep}};
    font-size: 12pt;
    font-weight: 600;
}

QTableWidget#humanizeLibraryTable {
    background: rgba({{bg.input.soft}}, 0.94);
    alternate-background-color: rgba({{bg.panel.muted}}, 0.72);
    border: none;
    gridline-color: rgba({{border.default}}, 0.12);
    color: {{text.body}};
    font-size: 13pt;
    selection-background-color: rgba({{accent.primary}}, 0.16);
    selection-color: {{text.heading}};
}

QTableWidget#humanizeLibraryTable QHeaderView::section {
    background: rgba({{bg.inset}}, 0.94);
    border: none;
    border-bottom: 1px solid rgba({{border.default}}, 0.18);
    border-right: 1px solid rgba({{border.default}}, 0.12);
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 700;
    padding: 8px 10px;
}

QTableWidget#humanizeLibraryTable::item {
    padding: 6px 8px;
    border-bottom: 1px solid rgba({{border.default}}, 0.07);
}

QTableWidget#humanizeLibraryTable::item:selected {
    background: rgba({{accent.primary}}, 0.16);
    color: {{text.heading}};
}

QFrame#humanizeStatsBar {
    background: rgba({{bg.inset}}, 0.92);
    border-top: 1px solid rgba({{border.default}}, 0.14);
}

QLabel#statsBarLabel {
    color: {{text.muted}};
    font-size: 12pt;
    font-weight: 600;
}

QLabel#statsBarValue {
    color: {{text.heading}};
    font-size: 12pt;
    font-weight: 700;
}

QWidget#finalRevisionWidget {
    background: transparent;
}

QWidget#finalRevisionToolbar {
    background: rgba({{bg.surface}}, 0.82);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __CHIP_RADIUS__px;
}

QWidget#finalRevisionWidget QPushButton#actionButton {
    min-height: 34px;
    padding: 0 14px;
}

QLabel#revisionModeLabel,
QLabel#revisionProposalTitle {
    color: {{text.body.warm}};
    font-size: 12pt;
    font-weight: 700;
}

QLabel#revisionStatus {
    color: {{text.muted}};
    font-size: 12pt;
}

QLabel#revisionDialogTitle {
    color: {{text.body.warm}};
    font-size: 14pt;
    font-weight: 700;
}

QLabel#revisionDialogHint {
    color: {{text.muted}};
    font-size: 12pt;
}

QLabel#revisionDialogSpinner {
    color: {{accent.warm}};
    font-size: 15pt;
    font-weight: 700;
}

QPushButton#revisionDirectionChip {
    background: rgba({{bg.inset}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.2);
    border-radius: 6px;
    color: {{text.revision.chip}};
    font-size: 12pt;
    padding: 5px 10px;
}

QPushButton#revisionDirectionChip:hover {
    background: rgba({{bg.control.hover}}, 0.95);
    border-color: rgba({{accent.primary}}, 0.34);
}

QTextEdit#finalRevisionEditor,
QTextEdit#revisionProposal,
QPlainTextEdit#revisionDirectionInput {
    background: rgba({{bg.input}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.primary}};
    font-family: "Songti SC", "STSong", "Georgia", serif;
    font-size: 15pt;
    padding: 18px 22px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}

QTextEdit#revisionProposal {
    background: rgba({{bg.hover.secondary}}, 0.9);
    font-size: 14pt;
}

QPlainTextEdit#revisionDirectionInput {
    background: rgba({{bg.input.soft}}, 0.96);
    font-size: 13pt;
    padding: 10px 12px;
}

QSplitter#revisionSplitter::handle {
    background: rgba({{border.default}}, 0.12);
}

QScrollArea#docTransparentScroll,
QScrollArea#docTransparentScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}

QLabel#charGraphHint,
QLabel#narrativeTimelineHint {
    color: {{text.muted}};
    font-size: 11pt;
    padding: 6px 0;
    background: rgba({{bg.inset}}, 0.6);
    border-top: 1px solid rgba({{border.default}}, 0.12);
}

QLabel#charGraphFocus {
    color: {{text.body}};
    font-size: 12pt;
    font-weight: 700;
    padding: 8px 10px;
    background: rgba({{bg.surface}}, 0.82);
    border-top: 1px solid rgba({{border.default}}, 0.10);
}

QWidget#charCard {
    background: rgb(255, 252, 247);
    border: 1px solid rgba({{border.default}}, 0.24);
    border-left-width: 3px;
    border-radius: __CHIP_RADIUS__px;
}

QWidget#charCard:hover {
    background: rgba({{bg.surface}}, 1.0);
    border-color: rgba({{border.default}}, 0.24);
}

QWidget#charCard[expanded="true"] {
    background: rgba({{bg.surface}}, 1.0);
    border-color: rgba({{border.default}}, 0.28);
}

QWidget#charCard[selected="true"] {
    background: rgba({{bg.hover.accent}}, 1.0);
    border-color: rgba({{accent.primary}}, 0.40);
}

QWidget#charCard[role="protagonist"] {
    border-left-color: {{accent.primary}};
}

QWidget#charCard[role="deuteragonist"] {
    border-left-color: {{accent.light}};
}

QWidget#charCard[role="antagonist"] {
    border-left-color: {{role.antagonist}};
}

QWidget#charCard[role="supporting"] {
    border-left-color: {{role.supporting}};
}

QWidget#charCard[role="minor"] {
    border-left-color: {{text.muted}};
}

QLabel#charCardName {
    color: {{text.heading.deep}};
    font-size: 14pt;
    font-weight: 700;
}

QLabel#charCardToggle {
    color: {{text.muted}};
    font-size: 12pt;
    font-weight: 700;
}

QLabel#charCardRoleBadge {
    border-radius: 6px;
    padding: 1px 6px;
    font-size: 10pt;
    font-weight: 700;
    border: 1px solid rgba({{border.default}}, 0.25);
}

QLabel#charCardRoleBadge[role="protagonist"] {
    color: {{accent.primary}};
    background: rgba({{accent.primary}}, 0.13);
    border-color: rgba({{accent.primary}}, 0.25);
}

QLabel#charCardRoleBadge[role="deuteragonist"] {
    color: {{accent.light}};
    background: rgba({{accent.light}}, 0.13);
    border-color: rgba({{accent.light}}, 0.25);
}

QLabel#charCardRoleBadge[role="antagonist"] {
    color: {{role.antagonist}};
    background: rgba({{role.antagonist}}, 0.13);
    border-color: rgba({{role.antagonist}}, 0.25);
}

QLabel#charCardRoleBadge[role="supporting"] {
    color: {{role.supporting}};
    background: rgba({{role.supporting}}, 0.13);
    border-color: rgba({{role.supporting}}, 0.25);
}

QLabel#charCardRoleBadge[role="minor"] {
    color: {{text.muted}};
    background: rgba({{text.muted}}, 0.12);
    border-color: rgba({{text.muted}}, 0.24);
}

QLabel#charCardMeta {
    color: {{text.muted}};
    font-size: 11pt;
}

QLabel#charCardMetaLine {
    color: {{text.char.meta}};
    font-size: 12pt;
    padding-left: 2px;
}

QLabel#charCardSummary {
    color: {{text.char.meta}};
    font-size: 12pt;
    line-height: 1.4;
    padding-left: 22px;
}

QWidget#charCardDetails {
    border-top: 1px solid rgba({{border.default}}, 0.12);
    margin-top: 4px;
    padding-top: 6px;
}

QLabel#charCardDetailLine,
QLabel#charCardRelationLine {
    color: {{text.body}};
    font-size: 12pt;
    line-height: 1.45;
    padding-left: 22px;
}

QLabel#charCardDetailTitle {
    color: {{text.char.meta}};
    font-size: 12pt;
    font-weight: 700;
    padding-left: 22px;
}

QLabel#charCardArc {
    color: {{text.body}};
    font-size: 12pt;
    padding-left: 2px;
}

QLabel#charCardStatusBadge {
    border-radius: 6px;
    padding: 1px 6px;
    font-size: 10pt;
    font-weight: 600;
    color: {{text.char.status}};
    background: rgba({{text.muted}}, 0.13);
    border: 1px solid rgba({{text.muted}}, 0.25);
}

QLabel#charCardStatusBadge[status="dormant"] {
    color: {{text.char.status}};
    background: rgba({{text.muted}}, 0.13);
    border-color: rgba({{text.muted}}, 0.25);
}

QLabel#charCardStatusBadge[status="retired"] {
    color: {{text.char.retired}};
    background: rgba({{accent.badge}}, 0.13);
    border-color: rgba({{accent.badge}}, 0.25);
}

QTabWidget#narrativeBlueprintBundleTabs::pane {
    background: transparent;
    border: none;
    padding-top: 6px;
}

QTabBar#narrativeBlueprintBundleTabsBar::tab {
    background: rgba({{bg.control.hover}}, 0.88);
    border: 1px solid rgba({{border.default}}, 0.22);
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: {{text.body.alt}};
    font-size: 12pt;
    font-weight: 600;
    margin-right: 2px;
    min-width: 82px;
    padding: 6px 13px;
}

QTabBar#narrativeBlueprintBundleTabsBar::tab:selected {
    background: rgb(255, 250, 243);
    border-color: rgba({{border.default}}, 0.32);
    color: {{text.heading}};
}

QTabBar#narrativeBlueprintBundleTabsBar::tab:hover:!selected {
    background: rgba({{bg.control.hover}}, 0.95);
}

QTabWidget#narrativeBlueprintAppendixTabs::pane,
QTabWidget#narrativeBlueprintCoherenceTabs::pane {
    background: rgb(255, 252, 247);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-top: none;
    border-radius: 0 0 7px 7px;
    padding-top: 4px;
}

QTabBar#narrativeBlueprintAppendixTabsBar::tab,
QTabBar#narrativeBlueprintCoherenceTabsBar::tab {
    background: rgba({{bg.control.hover}}, 0.30);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: {{text.tab.alt}};
    font-size: 11pt;
    font-weight: 600;
    margin-right: 2px;
    min-width: 72px;
    padding: 5px 10px;
}

QTabBar#narrativeBlueprintAppendixTabsBar::tab:selected,
QTabBar#narrativeBlueprintCoherenceTabsBar::tab:selected {
    background: rgba({{bg.input}}, 0.92);
    border-color: rgba({{border.default}}, 0.18);
    color: {{text.heading}};
}

QTabWidget#narrativeBlueprintTabs::pane {
    background: rgba({{bg.input.soft}}, 0.55);
    border: 1px solid rgba({{border.default}}, 0.08);
    border-top: none;
    border-radius: 0 0 __CHIP_RADIUS__px __CHIP_RADIUS__px;
    padding-top: 4px;
}

QTabBar#narrativeBlueprintTabsBar::tab {
    background: rgba({{bg.control.hover}}, 0.36);
    border: 1px solid rgba({{border.default}}, 0.13);
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    color: {{text.tab.alt}};
    font-size: 11pt;
    font-weight: 600;
    margin-right: 2px;
    min-width: 74px;
    padding: 5px 11px;
}

QTabBar#narrativeBlueprintTabsBar::tab:selected {
    background: rgba({{bg.input}}, 0.95);
    border-color: rgba({{border.default}}, 0.20);
    color: {{text.heading}};
}

QTabBar#narrativeBlueprintTabsBar::tab:hover:!selected {
    background: rgba({{bg.control.hover}}, 0.58);
}

QLabel#narrativeBlueprintEmpty {
    color: {{text.muted}};
    padding: 40px;
}

QLabel#motifTipsHeader {
    font-size: 13pt;
    font-weight: 600;
    color: {{motif.purple}};
}

QLabel#motifTipsContent {
    font-size: 12pt;
    color: {{slate}};
    background: {{slate.bg}};
    border-radius: 6px;
    padding: 8px;
}

QLabel#motifTipsWarning {
    font-size: 11pt;
    color: {{danger.red}};
    background: {{danger.red.bg}};
    border-radius: 4px;
    padding: 6px;
}

QLabel#memoryHint[warning="true"] {
    color: {{status.warning.alt}};
    font-size: 12pt;
}

QLabel#causalFixHint {
    color: {{text.muted}};
    font-size: 11pt;
}

"""
