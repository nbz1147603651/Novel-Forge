"""Order-preserving QSS fragment: part_04_tabs."""

from __future__ import annotations

CONTENT = """/* ── Tab Widget ─────────────────────────────────────────── */

QTabWidget::pane {
    background: rgba({{bg.surface}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-top: none;
    border-radius: 0 0 __COMPACT_CARD_RADIUS__px __COMPACT_CARD_RADIUS__px;
}

QTabWidget::tab-bar {
    left: 4px;
}

QTabBar::tab {
    background: rgba({{bg.control}}, 0.80);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-bottom: none;
    border-radius: __CHIP_RADIUS__px __CHIP_RADIUS__px 0 0;
    color: {{text.tab}};
    font-size: 12pt;
    font-weight: 600;
    padding: 6px 16px;
    margin-right: 2px;
    min-width: 56px;
}

QTabBar::tab:selected {
    background: rgba({{bg.surface}}, 0.98);
    border-color: rgba({{border.default}}, 0.20);
    color: {{text.heading}};
}

QTabBar::tab:hover:!selected {
    background: rgba({{bg.hover.accent}}, 0.92);
    color: {{text.tab.hover}};
}

QTabWidget#projectDocumentTabs::pane {
    background: rgba({{bg.input.soft}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 0 0 10px 10px;
    padding-top: 6px;
}

QTabBar#projectDocumentTabsBar::tab {
    background: rgba({{bg.control.hover}}, 0.70);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-bottom: none;
    border-radius: 7px 7px 0 0;
    color: {{text.tab.project}};
    font-size: 12pt;
    font-weight: 700;
    margin-right: 2px;
    min-width: 72px;
    padding: 7px 12px;
}

QTabBar#projectDocumentTabsBar::tab:selected {
    background: rgba({{bg.input.soft}}, 0.98);
    border-color: rgba({{border.default}}, 0.22);
    color: {{text.heading}};
}

QTabBar#projectDocumentTabsBar::tab:hover:!selected {
    background: rgba({{bg.hover.accent}}, 0.94);
    color: {{text.tab.hover}};
}

QTabWidget#projectNestedTabs::pane,
QTabWidget#chapterVersionTabs::pane,
QTabWidget#chapterReportTabs::pane,
QTabWidget#bookConsistencyTabs::pane,
QTabWidget#characterBibleBundleTabs::pane,
QTabWidget#elementSelectionBundleTabs::pane,
QTabWidget#styleProfileTabs::pane,
QTabWidget#voiceStudioTabs::pane {
    background: transparent;
    border: none;
    padding-top: 4px;
}

QTabBar#projectNestedTabsBar::tab,
QTabBar#chapterVersionTabsBar::tab,
QTabBar#chapterReportTabsBar::tab,
QTabBar#bookConsistencyTabsBar::tab,
QTabBar#characterBibleBundleTabsBar::tab,
QTabBar#elementSelectionBundleTabsBar::tab,
QTabBar#styleProfileTabsBar::tab,
QTabBar#voiceStudioTabsBar::tab {
    background: rgba({{bg.control.hover}}, 0.46);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-bottom: none;
    border-radius: 7px 7px 0 0;
    color: {{text.tab.alt}};
    font-size: 12pt;
    font-weight: 700;
    margin-right: 2px;
    min-width: 64px;
    padding: 5px 10px;
}

QTabBar#projectNestedTabsBar::tab:selected,
QTabBar#chapterVersionTabsBar::tab:selected,
QTabBar#chapterReportTabsBar::tab:selected,
QTabBar#bookConsistencyTabsBar::tab:selected,
QTabBar#characterBibleBundleTabsBar::tab:selected,
QTabBar#elementSelectionBundleTabsBar::tab:selected,
QTabBar#styleProfileTabsBar::tab:selected,
QTabBar#voiceStudioTabsBar::tab:selected {
    background: rgba({{bg.input.soft}}, 0.95);
    border-color: rgba({{border.default}}, 0.18);
    color: {{text.heading}};
}

"""
