"""Dialog styles: QMessageBox, aiDialog, docViewerDialog, chapterStudioDialog."""

from __future__ import annotations

from novel_forge.desktop.tokens.radius import CHIP_RADIUS, DIALOG_RADIUS

_CONTENT_TEMPLATE = """

QMessageBox {
    background: {{bg.surface}};
}

QMessageBox QLabel {
    color: {{text.artifact}};
    font-size: 13pt;
}

QMessageBox QPushButton {
    min-width: 80px;
    min-height: 28px;
}

QMessageBox QPushButton#actionButton {
    min-width: 100px;
}

QDialog#appDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#settingsDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#appDialog QLabel#dialogText {
    color: {{text.artifact}};
    font-size: 13pt;
    font-weight: 600;
}

QDialog#appDialog QLabel#dialogTitle {
    color: {{text.heading.deep}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 18pt;
    font-weight: 700;
}

QDialog#appDialog QLabel#dialogContext {
    color: {{text.body.alt}};
    font-size: 11pt;
    padding: 7px 10px;
    background: rgba({{accent.primary}}, 0.07);
    border: 1px solid rgba({{accent.primary}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
}

QDialog#appDialog QLabel#dialogHelper,
QDialog#appDialog QLabel#dialogCounter {
    color: {{text.muted}};
    font-size: 10.5pt;
}

QDialog#appDialog QLabel#dialogInformative {
    color: {{text.secondary}};
    font-size: 12pt;
}

QDialog#appDialog QLineEdit#dialogInput {
    background: rgba({{bg.input}}, 0.82);
    border: 1px solid rgba({{accent.light}}, 0.28);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.heading}};
    font-size: 13pt;
    padding: 8px 10px;
    selection-background-color: rgba({{accent.light}}, 0.22);
}

QDialog#appDialog QLineEdit#dialogInput:focus {
    background: rgba({{bg.input}}, 0.96);
    border: 1px solid rgba({{accent.light}}, 0.72);
}

QDialog#appDialog QPlainTextEdit#dialogTextInput {
    background: rgba({{bg.input}}, 0.84);
    border: 1px solid rgba({{border.control}}, 0.24);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.heading}};
    font-size: 12.5pt;
    padding: 12px 14px;
    selection-background-color: rgba({{accent.light}}, 0.22);
}

QDialog#appDialog QPlainTextEdit#dialogTextInput:focus {
    background: rgba({{bg.input}}, 0.98);
    border: 1px solid rgba({{accent.light}}, 0.72);
}

QDialog#appDialog QPushButton#actionButton {
    min-width: 96px;
}

QDialog#appDialog QCheckBox {
    color: {{text.body}};
    font-size: 13pt;
    spacing: 6px;
}

QDialog#appDialog QLabel#voiceRebuildSection {
    color: {{text.heading.deep}};
    font-size: 12pt;
    font-weight: 700;
    padding-top: 2px;
}

QDialog#appDialog QListWidget#voiceRebuildList {
    background: rgba({{bg.input}}, 0.72);
    border: 1px solid rgba({{border.control}}, 0.28);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.body}};
    outline: 0;
    padding: 5px;
}

QDialog#appDialog QListWidget#voiceRebuildList::item {
    min-height: 28px;
    padding: 7px 10px;
    border-radius: 7px;
}

QDialog#appDialog QListWidget#voiceRebuildList::item:hover {
    background: rgba({{accent.primary}}, 0.07);
}

QDialog#appDialog QListWidget#voiceRebuildList::item:checked {
    background: rgba({{accent.primary}}, 0.13);
    color: {{text.heading}};
}

QDialog#appDialog QListWidget#voiceRebuildList::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba({{border.control}}, 0.42);
    background: rgba({{bg.input}}, 0.96);
}

QDialog#appDialog QListWidget#voiceRebuildList::indicator:checked {
    background: {{accent.primary}};
    border-color: {{accent.primary}};
}

QDialog#aiDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#aiDialog QScrollArea,
QDialog#aiDialog QScrollArea > QWidget,
QDialog#aiDialog QScrollArea > QWidget > QWidget#aiPolishScrollContent {
    background: transparent;
}

QLabel#aiProgressLabel {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 600;
}

QDialog#aiDialog QProgressBar {
    background: rgba({{bg.control.hover}}, 0.5);
    border-radius: 4px;
    border: none;
}

QDialog#aiDialog QProgressBar::chunk {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.light}},
        stop: 1 {{accent.dark}}
    );
    border-radius: 4px;
}

QDialog#docViewerDialog {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#docViewerDialog QLabel#docViewerLabel {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 600;
}

QDialog#chapterStudioDialog {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#chapterStudioDialog QLabel#dlgTitle {
    color: {{text.topbar.title}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 15pt;
    font-weight: 700;
}

QDialog#chapterStudioDialog QLabel#dlgSubtitle {
    color: {{text.secondary}};
    font-size: 12pt;
}

QDialog#chapterStudioDialog QLabel#dlgHint {
    color: {{text.muted}};
    font-size: 11pt;
}

QDialog#chapterStudioDialog QLabel#dlgSection {
    color: {{text.body}};
    font-size: 12pt;
    font-weight: 600;
}

QDialog#chapterStudioDialog QLabel#dlgCount {
    color: {{text.secondary}};
    font-size: 13pt;
    font-weight: 600;
}

QDialog#chapterStudioDialog QLabel#dlgBody,
QDialog#chapterStudioDialog QCheckBox#dlgBody {
    color: {{text.body}};
    font-size: 13pt;
    line-height: 1.6;
}

QDialog#chapterStudioDialog QFrame#dlgDivider {
    background: rgba({{border.default}}, 0.14);
    border: none;
}

QDialog#chapterStudioDialog QRadioButton,
QDialog#chapterStudioDialog QCheckBox {
    color: {{text.body}};
    font-size: 13pt;
    spacing: 6px;
}

QDialog#chapterStudioDialog QRadioButton::indicator,
QDialog#chapterStudioDialog QCheckBox::indicator {
    width: 16px;
    height: 16px;
}

QDialog#chapterStudioDialog QLabel#pathLabel {
    color: {{text.secondary}};
    font-size: 12pt;
    padding: 6px 10px;
    background: rgba({{border.default}}, 0.06);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 6px;
}

QDialog#chapterStudioDialog QScrollArea {
    background: transparent;
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 6px;
}

QDialog#humanizeEditDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#humanizeLibraryDashboardDialog {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#humanizeEditDialog QLabel#dialogTitle {
    color: {{text.heading.deep}};
    font-family: "Songti SC", "STSong", "Georgia";
    font-size: 17pt;
    font-weight: 700;
}

QDialog#humanizeEditDialog QLabel#dialogHint,
QDialog#humanizeEditDialog QLabel#dialogNotes,
QDialog#humanizeEditDialog QLabel#dialogExample,
QDialog#humanizeEditDialog QLabel#dialogFieldValue {
    color: {{text.body}};
    font-size: 13pt;
}

QDialog#humanizeEditDialog QLabel#dialogFieldLabel,
QDialog#humanizeEditDialog QLabel#dialogSectionLabel {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 700;
}

QDialog#humanizeEditDialog QPushButton {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.control}}, 0.18);
    border-radius: __CHIP_RADIUS__px;
    color: {{text.body.warm}};
    font-size: 13pt;
    font-weight: 600;
    min-height: 32px;
    min-width: 88px;
    padding: 0 14px;
}

QDialog#humanizeEditDialog QPushButton:hover {
    background: {{bg.hover.secondary}};
    border-color: rgba({{accent.primary}}, 0.30);
}

QDialog#finalRevisionDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

QDialog#errorLogDialog {
    background: rgba({{bg.surface}}, 0.98);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: __DIALOG_RADIUS__px;
}

/* ═══════════════  AI Hint Dialog labels  ════════════════════════════ */

QLabel#aiHintModeDesc {
    color: {{text.secondary}};
    padding: 2px 0;
}

QLabel#aiHintConflict {
    color: {{status.danger.alt}};
    padding: 2px 0;
}

/* ═══════════════  Diff Preview Dialog  ══════════════════════════════ */

QDialog#aiDialog[dialogRole="diffPreview"] {
    background: {{bg.surface}};
}

QLabel#diffDialogTitle {
    color: {{text.heading.deep}};
    font-size: 16px;
    font-weight: 800;
}

QLabel#diffDialogSubtitle {
    color: {{text.muted.strong}};
    font-size: 12px;
}

QLabel#diffSelectionBadge {
    background: rgba({{accent.primary}}, 0.10);
    border: 1px solid rgba({{accent.primary}}, 0.22);
    border-radius: 8px;
    color: {{accent.deep}};
    font-size: 12px;
    font-weight: 700;
    padding: 5px 10px;
}

QFrame#diffPanel {
    background: rgba({{bg.input.soft}}, 0.96);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 10px;
}

QListWidget#diffFieldList {
    background: transparent;
    border: none;
    color: {{text.artifact}};
    font-size: 13px;
    outline: none;
    selection-background-color: rgba({{accent.primary}}, 0.12);
    selection-color: {{text.heading.deep}};
}

QListWidget#diffFieldList::item {
    border-bottom: 1px solid rgba({{border.default}}, 0.08);
    padding: 8px 10px;
}

QListWidget#diffFieldList::item:hover {
    background: rgba({{accent.primary}}, 0.06);
}

QListWidget#diffFieldList::item:selected {
    background: rgba({{accent.primary}}, 0.13);
    color: {{text.heading.deep}};
}

QListWidget#diffFieldList::indicator {
    width: 0;
    height: 0;
}

QWidget#diffFieldItem {
    background: transparent;
}

QLabel#diffFieldLabel {
    color: {{text.heading.deep}};
    font-size: 13px;
    font-weight: 700;
}

QLabel#diffFieldKey {
    color: {{text.muted.strong}};
    font-family: "Menlo", "Consolas", "Courier New";
    font-size: 11px;
}

QPushButton#diffCheckButton {
    background: rgba({{bg.input}}, 0.88);
    border: 1px solid rgba({{border.muted}}, 0.28);
    border-radius: 6px;
    color: transparent;
    font-size: 13px;
    font-weight: 800;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    padding: 0;
}

QPushButton#diffCheckButton:hover {
    border-color: rgba({{accent.primary}}, 0.45);
}

QPushButton#diffCheckButton:checked {
    background: {{accent.primary}};
    border-color: {{accent.primary}};
    color: {{white}};
}

QTextBrowser#diffDetail {
    background: rgba({{bg.input}}, 0.78);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 8px;
    color: {{text.heading.deep}};
    font-size: 13px;
    padding: 6px;
    selection-background-color: rgba({{accent.primary}}, 0.18);
}

QSplitter#diffSplitter::handle {
    background: rgba({{border.default}}, 0.12);
    margin: 10px 4px;
    border-radius: 2px;
}

"""

CONTENT = _CONTENT_TEMPLATE.replace("__DIALOG_RADIUS__", str(DIALOG_RADIUS)).replace(
    "__CHIP_RADIUS__", str(CHIP_RADIUS)
)
