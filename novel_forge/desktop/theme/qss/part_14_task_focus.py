"""Theme-managed task observation, streaming, and task-flow surfaces."""

CONTENT = """

/* ═══════════════  Task Focus & Task Flow  ═════════════════════════ */

QFrame#taskFocusPanel {
    background: rgba({{bg.surface}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 12px;
}

QFrame#taskFocusDecision {
    background: rgba({{bg.inset}}, 0.9);
    border: 1px dashed rgba({{border.warm}}, 0.20);
    border-radius: 10px;
}

QTextBrowser#taskFocusStream {
    background: rgba({{bg.input.soft}}, 0.90);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    padding: 10px 12px;
    color: {{text.primary}};
    font-size: 13px;
}

QWidget#compactStreamPreview {
    background: rgba({{bg.panel.muted}}, 0.62);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 8px;
}

QLabel#taskFocusTicker {
    color: {{text.muted.strong}};
    font-size: 13px;
    padding: 2px 0;
}

QLabel#taskPetUsage {
    color: {{accent.muted}};
    background: rgba({{accent.primary}}, 0.09);
    border: 1px solid rgba({{accent.primary}}, 0.18);
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 700;
}

QLabel#taskPetUsage[compact="true"] {
    background: transparent;
    border: none;
    padding: 0px;
    font-size: 9px;
}

QFrame#taskPetBubbleCard {
    background: rgba({{bg.surface}}, 0.97);
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: 18px;
}

QFrame#taskPetBubbleCard[tone="active"] {
    border-color: rgba({{accent.primary}}, 0.28);
}

QFrame#taskPetBubbleCard[tone="success"] {
    border-color: rgba({{status.success.job}}, 0.28);
}

QFrame#taskPetBubbleCard[tone="danger"] {
    border-color: rgba({{status.danger.alt}}, 0.32);
}

QFrame#taskPetBubbleCard[tone="warning"] {
    border-color: rgba({{status.warning}}, 0.34);
}

QLabel#taskPetBubbleTitle {
    background: transparent;
    color: {{text.primary}};
    font-size: 13px;
    font-weight: 700;
}

QLabel#taskPetBubbleTitle[compact="true"] {
    font-size: 12px;
}

QLabel#taskPetBubbleDetail {
    background: transparent;
    color: {{text.muted.strong}};
    font-size: 11px;
}

QLabel#taskPetBubbleDetail[compact="true"] {
    font-size: 10px;
}

QProgressBar#taskPetBubbleProgress {
    min-height: 3px;
    max-height: 3px;
    background: rgba({{border.default}}, 0.16);
    border: none;
    border-radius: 1px;
}

QProgressBar#taskPetBubbleProgress::chunk {
    background: {{accent.primary}};
    border-radius: 1px;
}

QProgressBar#taskPetBubbleProgress[tone="success"]::chunk {
    background: {{status.success.job}};
}

QProgressBar#taskPetBubbleProgress[tone="danger"]::chunk {
    background: {{status.danger.alt}};
}

QProgressBar#taskPetBubbleProgress[tone="warning"]::chunk {
    background: {{status.warning}};
}

QProgressBar#taskPetBubbleProgress[tone="muted"]::chunk {
    background: {{text.muted}};
}

QToolButton#taskPetBubbleToggle {
    background: rgba({{bg.surface}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.24);
    border-radius: 11px;
    color: {{text.muted.strong}};
    padding: 0px;
}

QToolButton#taskPetBubbleToggle:hover {
    background: {{bg.surface.elevated}};
    border-color: rgba({{accent.primary}}, 0.42);
    color: {{text.primary}};
}

QToolButton#taskPetBubbleToggle:pressed {
    background: rgba({{accent.primary}}, 0.12);
    border-color: rgba({{accent.primary}}, 0.54);
}

QFrame#taskSwitcherBar {
    background: rgba({{bg.surface}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 10px;
}

QScrollArea#taskSwitcherScroll,
QScrollArea#taskFocusDialogScroll,
QScrollArea#taskModelCallContentScroll,
QScrollArea#workflowTaskFlowScroll,
QScrollArea#chapterTaskFlowScroll {
    background: transparent;
    border: none;
}

QPushButton#taskSwitcherChip {
    background: rgba({{bg.input.soft}}, 0.82);
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: 14px;
    color: {{text.muted.strong}};
    padding: 2px 12px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton#taskSwitcherChip:hover {
    background: rgba({{bg.hover.accent}}, 0.98);
    border-color: rgba({{accent.primary}}, 0.34);
}

QPushButton#taskSwitcherChip:checked {
    background: rgba({{accent.primary}}, 0.10);
    border: 1px solid rgba({{accent.primary}}, 0.58);
    color: {{text.primary}};
}

QPushButton#taskSwitcherChip[tone="warning"] {
    color: {{status.warning.alt}};
}

QPushButton#taskSwitcherChip[tone="danger"] {
    color: {{status.danger.alt}};
}

QPushButton#taskSwitcherChip[tone="success"] {
    color: {{status.success.job}};
}

QPushButton#taskSwitcherChip[terminal="true"] {
    color: {{text.disabled}};
}

QPushButton#taskSwitcherCloseButton {
    background: rgba({{bg.input.soft}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 12px;
    color: {{text.muted}};
    padding: 0px;
    font-size: 11px;
    font-weight: 700;
}

QPushButton#taskSwitcherCloseButton:hover {
    background: rgba({{status.danger}}, 0.12);
    border-color: rgba({{status.danger}}, 0.32);
    color: {{status.danger.deep}};
}

QTabWidget#taskFocusDialogTabs::pane,
QTabWidget#floatingStreamTabs::pane {
    background: {{bg.input.soft}};
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
    top: -1px;
}

QTabWidget#taskFocusDialogTabs QTabBar::tab,
QTabWidget#floatingStreamTabs QTabBar::tab {
    min-width: 92px;
    padding: 6px 12px;
    margin-right: 3px;
    color: {{text.tab.alt}};
    background: rgba({{bg.panel}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}

QTabWidget#floatingStreamTabs QTabBar::tab {
    padding: 8px 14px;
    margin-right: 4px;
}

QTabWidget#taskFocusDialogTabs QTabBar::tab:selected,
QTabWidget#floatingStreamTabs QTabBar::tab:selected {
    color: {{text.primary}};
    background: {{bg.input.soft}};
    font-weight: 700;
}

QTabWidget#taskFocusDialogTabs QTabBar::tab:!selected,
QTabWidget#floatingStreamTabs QTabBar::tab:!selected {
    margin-top: 2px;
}

QFrame#taskModelCallPanel {
    background: rgba({{bg.surface}}, 0.82);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 12px;
}

QProgressBar#taskModelCallBar {
    background: rgba({{border.default}}, 0.10);
    border: none;
    border-radius: 6px;
    height: 12px;
    color: {{text.muted.strong}};
    text-align: center;
    font-size: 10px;
}

QProgressBar#taskModelCallBar::chunk {
    background: rgba({{accent.primary}}, 0.62);
    border-radius: 6px;
}

QTextBrowser#taskModelCallPreview {
    background: rgba({{bg.input.soft}}, 0.90);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    padding: 8px 10px;
    color: {{text.artifact}};
    font-size: 12px;
}

QComboBox#taskModelCallSelect {
    min-height: 28px;
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: 7px;
    padding: 2px 8px;
    background: rgba({{bg.input.soft}}, 0.82);
    color: {{text.artifact}};
}

QWidget#taskModelCallContent,
QWidget#workflowTaskFlowContent {
    background: transparent;
    border: none;
}

QWidget#chapterTaskFlowContent,
QScrollArea#chapterTaskFlowScroll > QWidget > QWidget {
    background: rgba({{bg.control}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 14px;
}

QFrame#floatingStreamWindow {
    background: {{bg.surface}};
    border: 1px solid rgba({{border.warm}}, 0.18);
    border-radius: 10px;
}

QFrame#floatingStreamHeader {
    background: rgba({{border.default}}, 0.08);
    border-radius: 8px;
}

QTabWidget#chapterStudioArtifactTabs {
    background: transparent;
}

QTabWidget#chapterStudioArtifactTabs::pane {
    background: rgba({{bg.input.soft}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 0 0 10px 10px;
    top: -1px;
}

QTabWidget#chapterStudioArtifactTabs::tab-bar,
QTabWidget#chapterStudioArtifactTabs QTabBar {
    left: 0px;
    background: transparent;
}

QTabWidget#chapterStudioArtifactTabs QTabBar::tab {
    background: rgba({{bg.control}}, 0.74);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    color: {{text.tab}};
    font-size: 12px;
    font-weight: 600;
    padding: 4px 7px;
    margin: 0 1px 0 0;
    min-width: 0px;
}

QTabWidget#chapterStudioArtifactTabs QTabBar::tab:selected {
    background: rgba({{bg.surface}}, 0.98);
    color: {{text.heading}};
}

QTabWidget#chapterStudioArtifactTabs QTabBar::tab:hover:!selected {
    background: rgba({{bg.hover.accent}}, 0.92);
    color: {{text.tab.hover}};
}

"""
