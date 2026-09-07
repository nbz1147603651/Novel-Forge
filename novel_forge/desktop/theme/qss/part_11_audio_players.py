"""QSS fragment for audio player components (dubbing + standard).

Migrates inline ``setStyleSheet()`` calls from:
- ``components/dubbing_player.py``
- ``components/audio_player.py``

All hardcoded colours are replaced with design tokens so that theme
switching (light/dark) is honoured automatically.
"""

from __future__ import annotations

_CONTENT = """

/* ═══════════════  Audio Players  ═════════════════════════════════ */

/* ── Dubbing Player ─────────────────────────────────────────────── */

QLabel#dubbingNowPlaying {
    font-size: 12pt;
    color: {{text.muted}};
    font-weight: 500;
}

QLabel#dubbingSpeakerLabel {
    font-size: 11pt;
    color: {{accent.primary}};
    font-weight: bold;
    min-height: 20px;
}

QLabel#dubbingTextPreview {
    font-size: 13pt;
    color: {{text.primary}};
    padding: 4px 8px;
    background: rgba({{bg.surface}}, 0.5);
    border-radius: 6px;
}

QLabel#dubbingTimeLabel {
    font-size: 10pt;
    color: {{text.muted}};
}

QLabel#dubbingPctLabel {
    font-size: 10pt;
    color: {{text.muted}};
}

/* ── Standard Audio Player ──────────────────────────────────────── */

QLabel#audioTitle {
    font-size: 13pt;
    color: {{text.muted}};
}

QLabel#audioTimeLabel {
    font-size: 10pt;
    color: {{text.secondary}};
}

/* ── Voice Studio ───────────────────────────────────────────────── */

QLabel#voiceCapabilityHint,
QLabel#voiceStudioDeferredHint {
    color: {{text.muted}};
    font-size: 10.5pt;
}

QLabel#voiceCastStrategyHint {
    color: {{text.secondary}};
    background: rgba({{accent.primary}}, 0.07);
    border: 1px solid rgba({{accent.primary}}, 0.16);
    border-radius: 8px;
    padding: 7px 9px;
    font-size: 9.5pt;
}

QFrame#voiceTeamTaskCard {
    background: rgba({{accent.primary}}, 0.06);
    border: 1px solid rgba({{accent.primary}}, 0.24);
    border-left: 3px solid {{accent.primary}};
    border-radius: 8px;
}

QLabel#voiceTeamTaskTitle {
    color: {{text.heading}};
    font-size: 10pt;
    font-weight: 700;
}

QLabel#voiceTeamTaskDetail {
    color: {{text.secondary}};
    font-size: 9.5pt;
}

QProgressBar#voiceTeamTaskProgress {
    min-height: 16px;
    max-height: 16px;
    border: 1px solid {{border.muted}};
    border-radius: 5px;
    background: rgba({{bg.control.hover}}, 0.55);
    color: {{text.primary}};
    font-size: 8.5pt;
    font-weight: 600;
    text-align: center;
}

QProgressBar#voiceTeamTaskProgress::chunk {
    border-radius: 4px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.light}},
        stop: 1 {{accent.primary}}
    );
}

QLabel#voicePlatformMappingHint,
QLabel#voicePlatformCapabilitySummary {
    color: {{text.secondary}};
    background: rgba({{accent.primary}}, 0.07);
    border: 1px solid rgba({{accent.primary}}, 0.20);
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 10.5pt;
}

QTextBrowser#audioModelPlanReport {
    color: {{text.primary}};
    background: rgba({{bg.control}}, 0.46);
    border: 1px solid rgba({{accent.primary}}, 0.18);
    border-radius: 10px;
    padding: 8px;
    selection-background-color: rgba({{accent.primary}}, 0.28);
}

QWidget#voiceStudioTopContext {
    background: transparent;
}

QWidget#voiceHeaderStat {
    background: transparent;
    border-right: 1px solid rgba({{border.default}}, 0.18);
}

QLabel#voiceHeaderStatTitle {
    color: {{text.muted}};
    font-size: 9pt;
}

QLabel#voiceHeaderStatValue {
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 16pt;
    font-weight: 700;
}

QWidget#voiceProviderSwitch {
    min-height: 36px;
    min-width: 148px;
    padding: 0 12px;
    border: 1px solid rgba({{accent.primary}}, 0.36);
    border-radius: 8px;
    background: rgba({{accent.primary}}, 0.08);
}

QWidget#voiceProviderSwitch:hover {
    border-color: {{accent.primary}};
    background: rgba({{accent.primary}}, 0.14);
}

QWidget#voiceProviderSwitch:focus {
    border-color: {{accent.primary}};
}

QWidget#voiceProviderSwitch[loading="true"] {
    border-color: rgba({{border.default}}, 0.24);
    background: rgba({{bg.control.hover}}, 0.5);
}

QLabel#voiceProviderLabel {
    color: {{text.heading}};
    font-weight: 700;
    font-size: 10.5pt;
    background: transparent;
    border: none;
}

QWidget#voiceProviderSwitch[loading="true"] QLabel#voiceProviderLabel {
    color: {{text.muted}};
}

QLabel#voiceProviderArrow {
    color: {{text.muted}};
    font-size: 8pt;
    background: transparent;
    border: none;
}

QWidget#voiceProviderSwitch:hover QLabel#voiceProviderArrow {
    color: {{accent.primary}};
}

/* ── Provider Popup ─────────────────────────────────────────────── */

QFrame#voiceProviderPopup {
    background: {{bg.surface.elevated}};
    border: 1px solid {{border.soft}};
    border-radius: 10px;
    padding: 0;
}

QWidget#voiceProviderRow {
    background: transparent;
    border-radius: 7px;
    border: none;
}

QWidget#voiceProviderRow[hover="true"] {
    background: rgba({{accent.primary}}, 0.08);
}

QWidget#voiceProviderRow[checked="true"] {
    background: rgba({{accent.primary}}, 0.12);
}

QWidget#voiceProviderRow[checked="true"][hover="true"] {
    background: rgba({{accent.primary}}, 0.16);
}

QLabel#voiceProviderName {
    color: {{text.primary}};
    font-size: 10.5pt;
    font-weight: 500;
    background: transparent;
    border: none;
}

QWidget#voiceProviderRow[checked="true"] QLabel#voiceProviderName {
    color: {{accent.primary}};
    font-weight: 700;
}

QLabel#voiceProviderModels {
    color: {{text.muted}};
    font-size: 9pt;
    background: transparent;
    border: none;
}

QWidget#voiceStudioStatusBar {
    background: transparent;
}

QProgressBar#voiceTeamBuildProgress,
QProgressBar#voiceScriptGenerationProgress {
    min-height: 18px;
    max-height: 18px;
    border: 1px solid {{border.muted}};
    border-radius: 6px;
    background: rgba({{bg.control.hover}}, 0.55);
    color: {{text.primary}};
    font-size: 9pt;
    font-weight: 600;
    text-align: center;
}

QProgressBar#voiceTeamBuildProgress::chunk,
QProgressBar#voiceScriptGenerationProgress::chunk {
    border-radius: 5px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {{accent.light}},
        stop: 1 {{accent.primary}}
    );
}

QLineEdit#voiceCastSearch {
    min-height: 32px;
    border-radius: 8px;
    padding-left: 10px;
    padding-right: 10px;
}

/* Character list with status icons */
QListWidget#voiceCastList {
    background: {{bg.surface}};
    border: 1px solid {{border.muted}};
    border-radius: 6px;
    padding: 4px;
    outline: 0;
}

/* Explicitly repaint the viewport on every page switch/scroll.  The item view
   must not expose stale pixels from the previously visible workspace page. */
QListWidget#voiceCastList > QWidget#qt_scrollarea_viewport {
    background: {{bg.surface}};
}

QListWidget#voiceCastList::item {
    min-height: 46px;
    padding: 7px 10px;
    border-radius: 7px;
}

QListWidget#voiceCastList::item:selected {
    background: rgba({{accent.primary}}, 0.14);
    color: {{text.heading}};
}

QListWidget#voiceCastList::item:hover {
    background: rgba({{accent.primary}}, 0.06);
}

/* Voice info panel (structured HTML display) */
QTextBrowser#voiceInfoPanel {
    background: rgba({{bg.surface}}, 0.6);
    border: 1px solid {{border.muted}};
    border-radius: 9px;
    padding: 10px;
}

QLabel#voiceMatchBadge {
    color: {{text.secondary}};
    background: rgba({{bg.control.hover}}, 0.72);
    border: 1px solid {{border.muted}};
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 9.5pt;
    font-weight: 700;
}

QLabel#voiceMatchBadge[tone="good"] {
    color: {{status.success}};
    background: rgba({{status.success}}, 0.10);
    border-color: rgba({{status.success}}, 0.24);
}

QLabel#voiceMatchBadge[tone="warning"] {
    color: {{status.warning}};
    background: rgba({{status.warning}}, 0.10);
    border-color: rgba({{status.warning}}, 0.26);
}

QFrame#voicePreviewStrip {
    background: rgba({{accent.primary}}, 0.07);
    border: 1px solid rgba({{accent.primary}}, 0.18);
    border-radius: 9px;
}

QLabel#voicePreviewTitle,
QLabel#voiceParameterTitle {
    color: {{text.heading}};
    font-size: 10.5pt;
    font-weight: 700;
}

QLabel#voicePreviewStatus {
    color: {{text.muted}};
    font-size: 9.5pt;
}

QFrame#voiceParameterCard {
    background: rgba({{bg.control}}, 0.38);
    border: 1px solid {{border.muted}};
    border-radius: 9px;
}

QLabel#voiceParameterName {
    color: {{text.secondary}};
    font-size: 9.5pt;
    font-weight: 600;
}

/* Dubbing script browser (rich HTML) */
QTextBrowser#dubbingScriptBrowser {
    background: {{bg.panel}};
    border: 1px solid {{border.muted}};
    border-radius: 6px;
    padding: 8px;
}

QTextBrowser#syncScriptBrowser {
    background: {{bg.panel}};
    border: 1px solid {{border.muted}};
    border-radius: 6px;
    padding: 6px;
}

QTextBrowser#voiceMixManifestBrowser {
    background: {{bg.panel}};
    border: 1px solid {{border.muted}};
    border-radius: 6px;
    padding: 6px;
}

/* Compact, resizable script/subtitle pane in Voice Studio. */
QSplitter#voiceStudioAudioSplitter::handle {
    background: rgba({{border.default}}, 0.14);
    width: 5px;
    margin: 8px 2px;
    border-radius: 2px;
}

QSplitter#voiceStudioAudioSplitter::handle:hover {
    background: rgba({{accent.primary}}, 0.42);
}

QTabWidget#audioRightTabs::pane {
    background: rgba({{bg.surface}}, 0.88);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
    top: -1px;
}

QTabWidget#audioRightTabs QTabBar::tab {
    min-width: 72px;
    padding: 5px 10px;
    margin-right: 3px;
    color: {{text.muted}};
    background: rgba({{bg.panel}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}

QTabWidget#audioRightTabs QTabBar::tab:selected {
    color: {{text.primary}};
    background: rgba({{bg.surface}}, 0.96);
    font-weight: 700;
}

QLabel#voiceScriptSourceHint[state="ready"] {
    color: {{status.success}};
    font-size: 11px;
}

QLabel#voiceScriptSourceHint[state="stale"] {
    color: {{status.danger}};
    font-size: 11px;
    font-weight: 650;
}

QFrame#voiceScriptFreshnessBar {
    background: rgba({{status.success}}, 0.07);
    border: 1px solid rgba({{status.success}}, 0.20);
    border-radius: 8px;
}

QFrame#voiceScriptFreshnessBar[state="stale"] {
    background: rgba({{status.warning}}, 0.09);
    border-color: rgba({{status.warning}}, 0.28);
}

QLabel#voiceScriptFreshnessHint {
    color: {{text.secondary}};
    font-size: 10.5pt;
}

QLabel#voiceScriptSourceHint[state="snapshot"],
QLabel#voiceScriptSourceHint[state="empty"] {
    color: {{text.muted}};
    font-size: 11px;
}

QListWidget#voiceScriptSegmentList {
    background: rgba({{bg.panel}}, 0.82);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
    padding: 4px;
    outline: 0;
}

QListWidget#voiceScriptSegmentList::item {
    min-height: 28px;
    padding: 6px 8px;
    border-radius: 6px;
}

QListWidget#voiceScriptSegmentList::item:selected {
    background: rgba({{accent.primary}}, 0.14);
    color: {{text.primary}};
}

QTextEdit#voiceScriptTextEditor {
    background: rgba({{bg.input.soft}}, 0.94);
    border: 1px solid rgba({{border.default}}, 0.18);
    border-radius: 8px;
    padding: 8px;
    line-height: 1.55;
}

QScrollArea#voiceScriptEditorScroll,
QScrollArea#voiceScriptEditorScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}

QFrame#voiceScriptGuidancePanel {
    color: {{text.body.alt}};
    background: rgba({{accent.primary}}, 0.055);
    border: 1px solid rgba({{accent.primary}}, 0.16);
    border-radius: 8px;
}

QLabel#voiceScriptSynthesisDirection {
    color: {{text.body.alt}};
    background: transparent;
    border: none;
}

QLabel#voiceScriptSmartAdvice {
    color: {{text.muted.strong}};
    background: transparent;
    border: none;
    padding-top: 4px;
}

QLabel#voiceScriptDurationEstimate {
    color: {{text.muted}};
    font-size: 11px;
}

QFrame#voiceSpeakerReviewBar,
QFrame#voiceScriptGenerationBar,
QFrame#voiceSpeakerReviewPanel {
    background: rgba({{bg.surface}}, 0.70);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
}

QFrame#voiceSpeakerReviewBar[tone="danger"],
QFrame#voiceSpeakerReviewPanel[tone="danger"] {
    background: rgba({{status.danger}}, 0.07);
    border-color: rgba({{status.danger.alt}}, 0.30);
}

QFrame#voiceSpeakerReviewBar[tone="success"],
QFrame#voiceSpeakerReviewPanel[tone="success"] {
    background: rgba({{status.success}}, 0.06);
    border-color: rgba({{status.success}}, 0.24);
}

QLabel#voiceSpeakerReviewHint {
    color: {{text.body.alt}};
    background: transparent;
    border: none;
}

QListWidget#voiceSoundCueList {
    background: rgba({{bg.panel}}, 0.82);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
    padding: 4px;
    outline: 0;
}

QListWidget#voiceSoundCueList::item {
    min-height: 30px;
    padding: 7px 8px;
    border-radius: 6px;
}

QListWidget#voiceSoundCueList::item:selected {
    background: rgba({{accent.primary}}, 0.14);
    color: {{text.primary}};
}

QLabel#voiceSoundAnchorHint {
    color: {{text.body.alt}};
    background: rgba({{accent.primary}}, 0.055);
    border: 1px solid rgba({{accent.primary}}, 0.16);
    border-radius: 8px;
    padding: 8px;
}

/* Voice Studio tabs */
QTabWidget#voiceStudioTabs::pane {
    border: none;           /* outer frame provides the border */
    border-radius: 0;       /* container owns the rounded corners */
    top: 0;                 /* no offset — flush inside container */
}

QTabBar#voiceStudioTabsBar::tab {
    min-width: 92px;
    padding: 7px 18px;
    margin-right: 3px;
    border: 1px solid transparent;
    border-radius: 9px;
}

QTabBar#voiceStudioTabsBar::tab:selected {
    color: {{accent.primary}};
    background: rgba({{accent.primary}}, 0.11);
    border-color: rgba({{accent.primary}}, 0.24);
}

QTabBar#voiceStudioTabsBar::tab:!selected {
    color: {{text.muted}};
}

/* Avatar tag buttons (character chips) */
QPushButton#avatarTag {
    border: 1px solid {{border.muted}};
    border-radius: 14px;
    padding: 4px 12px;
    background: {{bg.surface}};
    color: {{text.secondary}};
    font-size: 11pt;
}

QPushButton#avatarTag:hover {
    background: rgba({{accent.primary}}, 0.08);
    border-color: {{accent.primary}};
}

QPushButton#avatarTag:checked {
    background: {{accent.primary}};
    color: white;
    border-color: {{accent.primary}};
}

/* Voice Studio semantic typography — no page-local hardcoded colours. */
QLabel#voiceWorkspaceTitle,
QLabel#voiceDialogTitle {
    color: {{text.heading}};
    font-size: 15px;
    font-weight: 700;
}

QLabel#voiceDialogTitle {
    font-size: 16px;
}

QLabel#voiceSectionTitle {
    color: {{text.body.alt}};
    font-size: 13px;
    font-weight: 700;
}

QLabel#voiceFieldLabel {
    color: {{text.muted}};
    font-size: 12px;
}

QLabel#voiceRoomSoundContext {
    color: {{text.body.alt}};
    background: rgba({{accent.primary}}, 0.06);
    border: 1px solid rgba({{accent.primary}}, 0.16);
    border-radius: 8px;
    padding: 7px 9px;
}

QLabel#voiceRoomPlaybackState {
    color: {{accent.primary}};
    background: rgba({{accent.primary}}, 0.09);
    border: 1px solid rgba({{accent.primary}}, 0.18);
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 11px;
    font-weight: 600;
}

QLabel#voiceRoomSubtitleTitle {
    color: {{text.muted}};
    font-size: 11px;
    font-weight: 700;
}

QLabel#voiceRoomCaptionPosition {
    color: {{text.muted}};
    font-size: 10.5px;
    font-variant-numeric: tabular-nums;
}

QLabel#voiceRoomPlayerScope {
    color: {{text.secondary}};
    font-size: 11px;
    font-weight: 600;
    padding: 2px 2px 0 2px;
}

QLabel#voiceRoomDirection {
    color: {{text.body.alt}};
    font-size: 12px;
    padding: 2px 1px;
}

QListWidget#voiceRoomSegmentList {
    color: {{text.body.alt}};
    background: transparent;
    border: none;
    outline: none;
    padding: 2px;
}

QListWidget#voiceRoomSegmentList::item {
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 8px;
    margin: 1px 0;
}

QListWidget#voiceRoomSegmentList::item:hover {
    background: rgba({{accent.primary}}, 0.06);
    border-color: rgba({{accent.primary}}, 0.10);
}

QListWidget#voiceRoomSegmentList::item:selected {
    color: {{text.heading}};
    background: rgba({{accent.primary}}, 0.13);
    border-color: rgba({{accent.primary}}, 0.24);
}

QFrame#voiceRoomCaptionStage {
    background: rgba({{bg.control}}, 0.34);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 12px;
}

QTextEdit#voiceRoomSegmentText,
QTextBrowser#voiceRoomSegmentText {
    color: {{text.body.alt}};
    background: rgba({{bg.surface}}, 0.82);
    border: none;
    border-radius: 9px;
    padding: 12px 14px;
    selection-background-color: rgba({{accent.primary}}, 0.24);
}

QWidget#voiceRoomActionBar {
    background: rgba({{bg.control}}, 0.46);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 9px;
}

QFrame#voiceRoomTransportDock {
    background: rgba({{bg.surface}}, 0.48);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 10px;
}

QWidget#voiceRoomSegmentPlayer QWidget#surfaceInset,
QWidget#voiceRoomChapterPlayer QWidget#surfaceInset {
    border-radius: 10px;
}

QSplitter#voiceStudioRoomSplitter::handle,
QSplitter#soundLibrarySplitter::handle {
    background: rgba({{border.default}}, 0.14);
    width: 5px;
    margin: 8px 2px;
    border-radius: 2px;
}

QSplitter#voiceStudioRoomSplitter::handle:hover,
QSplitter#soundLibrarySplitter::handle:hover {
    background: rgba({{accent.primary}}, 0.42);
}

QTabWidget#voicePostWorkspaceTabs::pane {
    background: rgba({{bg.surface}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 10px;
    top: -1px;
}

QTabWidget#voicePostWorkspaceTabs QTabBar::tab {
    min-width: 96px;
    padding: 6px 14px;
    margin-right: 4px;
    color: {{text.muted}};
    background: rgba({{bg.control}}, 0.76);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 8px 8px 0 0;
}

QTabWidget#voicePostWorkspaceTabs QTabBar::tab:selected {
    color: {{accent.primary}};
    background: rgba({{bg.surface}}, 0.98);
    border-color: rgba({{accent.primary}}, 0.24);
    font-weight: 700;
}

QListWidget#soundLibraryList {
    background: rgba({{bg.input}}, 0.84);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 9px;
    padding: 5px;
    outline: 0;
}

QWidget#soundLibraryListViewport {
    background: rgba({{bg.input}}, 0.84);
    border-radius: 8px;
}

QListWidget#soundLibraryList::item {
    min-height: 42px;
    padding: 7px 9px;
    border-radius: 7px;
}

QListWidget#soundLibraryList::item:selected {
    color: {{text.heading}};
    background: rgba({{accent.primary}}, 0.13);
}

QTextBrowser#soundLibraryDetail {
    color: {{text.body.alt}};
    background: rgba({{bg.input}}, 0.72);
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 9px;
    padding: 8px;
}

QWidget#soundLibraryDetailViewport {
    background: rgba({{bg.input}}, 0.72);
    border-radius: 8px;
}

QLineEdit#soundLibraryTags {
    border-radius: 9px;
}

/* ── Voice Studio compact workspace density ────────────────────── */

QFrame#voiceStudioTabsFrame {
    padding: 2px 4px;
}

QTabBar#voiceStudioTabsBar::tab {
    min-width: 82px;
    padding: 5px 13px;
    margin-right: 2px;
    border-radius: 8px;
}

QWidget#voiceStudioPage QPushButton#actionButton {
    min-height: 28px;
    padding: 0 10px;
    border-radius: 8px;
    font-size: 11pt;
}

QWidget#voiceStudioPage QComboBox,
QWidget#voiceStudioPage QLineEdit,
QWidget#voiceStudioPage QSpinBox,
QWidget#voiceStudioPage QDoubleSpinBox {
    min-height: 27px;
    padding: 3px 8px;
    border-radius: 8px;
    font-size: 11pt;
}

QWidget#voiceStudioPage QCheckBox {
    font-size: 11pt;
}

QWidget#voiceStudioPage QLabel#badge {
    padding: 2px 7px;
    border-radius: 8px;
    font-size: 10pt;
}

QWidget#voiceStudioPage QLabel#settingLabel {
    font-size: 11.5pt;
}

QWidget#voiceStudioPage QLabel#settingHint,
QWidget#voiceStudioPage QLabel#panelDescription,
QWidget#voiceStudioPage QLabel#voiceCapabilityHint {
    font-size: 10.5pt;
}

/* Platform settings use a vertical, Fire-style information hierarchy. */
QWidget#voiceStudioSettingsTab,
QScrollArea#voiceStudioSettingsScroll,
QScrollArea#voiceStudioSettingsScroll > QWidget,
QScrollArea#voiceStudioSettingsScroll > QWidget > QWidget,
QWidget#voiceStudioSettingsContent {
    background: {{bg.panel}};
    border: none;
}

QWidget#voiceSettingsCategory {
    background: transparent;
    padding: 5px 4px 1px 4px;
}

QWidget#voiceSettingsCategory QLabel#sectionTitle {
    color: {{text.heading}};
    font-family: "Songti SC", "STSong", "NSimSun", "Georgia";
    font-size: 15pt;
    font-weight: 600;
}

QWidget#voiceSettingsCategory QLabel#sectionSubtitle {
    color: {{text.muted}};
    font-size: 10.5pt;
}

QWidget#voiceStudioSettingsContent QPushButton#collapseToggle {
    min-height: 34px;
    padding: 7px 12px;
    border-radius: 9px;
    font-size: 12pt;
}

QWidget#voiceStudioSettingsContent QFrame#collapseBody {
    background: {{bg.surface}};
    border-color: rgba({{border.default}}, 0.12);
}

QWidget#voiceCompactToolbar {
    background: rgba({{bg.surface}}, 0.66);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 8px;
}

QLabel#audioEmptyHint {
    color: {{text.muted}};
    background: rgba({{bg.control}}, 0.42);
    border: 1px solid rgba({{border.default}}, 0.10);
    border-radius: 7px;
    padding: 4px 8px;
    font-size: 10.5pt;
}

QTabWidget#voicePostWorkspaceTabs QTabBar::tab {
    min-width: 86px;
    padding: 4px 10px;
}

QMenu#voiceStudioActionMenu {
    background: {{bg.surface.elevated}};
    border: 1px solid rgba({{border.default}}, 0.20);
    border-radius: 9px;
    padding: 4px;
    color: {{text.body.alt}};
}

QMenu#voiceStudioActionMenu::item {
    min-height: 26px;
    padding: 4px 18px 4px 10px;
    border-radius: 6px;
}

QMenu#voiceStudioActionMenu::item:selected {
    background: rgba({{accent.primary}}, 0.12);
    color: {{text.heading}};
}

QLabel#voiceRoomSoundContext {
    padding: 5px 7px;
}

/* ── TTS cleanup dialog ────────────────────────────────────── */
QLabel#voiceStudioCleanCategoryHint {
    color: {{text.muted}};
    font-size: 9.5pt;
    padding-left: 2px;
}

QLabel#voiceStudioCleanPreview {
    color: {{text.secondary}};
    background: rgba({{accent.primary}}, 0.07);
    border: 1px solid rgba({{accent.primary}}, 0.16);
    border-radius: 8px;
    padding: 7px 9px;
    font-size: 10pt;
    font-weight: 600;
}

"""

CONTENT = _CONTENT
