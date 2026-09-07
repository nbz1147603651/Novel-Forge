"""Form styles: inputs, ComboBox, SpinBox, TextEdit, checkboxes, settings fields."""

from __future__ import annotations

from novel_forge.desktop.tokens.radius import CHIP_RADIUS, COMPACT_CARD_RADIUS

_CONTENT = """

QLineEdit,
QTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background: rgba({{bg.input}}, 0.88);
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 10px;
    combobox-popup: 0;
    padding: 6px 10px;
    font-size: 13pt;
}

QTextEdit {
    padding: 8px 10px;
}

QLineEdit:focus,
QTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    outline: 2px solid rgba({{accent.primary}}, 0.6);
    border-color: rgba({{accent.primary}}, 0.55);
    background: rgba({{bg.input}}, 0.96);
    outline-offset: 2px;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

/* ── Workflow param inputs — cross-platform height lock ── */
QLineEdit#workflowParamInput,
QComboBox#workflowParamInput,
QSpinBox#workflowParamInput {
    min-height: 32px;
    max-height: 32px;
    padding: 5px 10px;
}

QComboBox::down-arrow {
    image: url(__ARROW_DOWN__);
    width: 10px;
    height: 6px;
}

QComboBox QAbstractItemView {
    background: {{bg.dialog}};
    border: 1px solid rgba({{border.default}}, 0.22);
    border-radius: 8px;
    color: {{text.heading}};
    outline: 0;
    padding: 4px 0;
    selection-background-color: rgba({{accent.primary}}, 0.14);
    selection-color: {{text.topbar.title}};
}

QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 6px 12px;
}

QCheckBox {
    color: {{text.checkbox}};
    font-size: 13pt;
    font-weight: 600;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba({{border.control}}, 0.3);
    background: rgba({{bg.input}}, 0.9);
}

QCheckBox::indicator:checked {
    background: {{accent.primary}};
    border-color: {{accent.primary}};
}

QCheckBox:disabled {
    color: rgba({{text.checkbox}}, 0.35);
}

QCheckBox::indicator:disabled {
    background: rgba({{bg.control.hover}}, 0.4);
    border-color: rgba({{border.control}}, 0.12);
}

/* ── Settings Field ──────────────────────────────── */

QLabel#settingLabel {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 700;
}

QLabel#settingHint {
    color: {{text.muted}};
    font-size: 12pt;
}

QLabel#settingGroupTitle {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 700;
    padding-top: 6px;
}

QWidget#settingSubgroupHeader {
    background: transparent;
}

QLabel#settingSubgroupTitle {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 700;
    padding-top: 4px;
}

QLabel#settingSubgroupHint {
    color: {{text.muted}};
    font-size: 12pt;
}

/* Routing group — header, desc, separator */
QLabel#routingGroupIcon {
    font-size: 15pt;
    padding: 0;
}
QLabel#routingGroupTitle {
    color: {{text.body.alt}};
    font-size: 14pt;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QLabel#routingGroupDesc {
    color: {{text.muted}};
    font-size: 12pt;
    padding-left: 22px;
    padding-bottom: 4px;
}
QFrame#routingGroupSep {
    border: none;
    border-top: 1px solid {{separator}};
    margin-top: 8px;
    margin-bottom: 2px;
}
QLabel#routingSubgroupTitle {
    color: {{text.body.alt}};
    font-size: 13pt;
    font-weight: 700;
}
QLabel#routingSubgroupDesc {
    color: {{text.routing.sub}};
    font-size: 12pt;
}

/* Routing fallback dropdown popup */
QLabel#routeTaskLabel {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 700;
}
QLabel#routeTaskHint {
    color: {{text.muted}};
    font-size: 12pt;
}
QComboBox#routeModelCombo,
QComboBox#routeThinkingMode,
QComboBox#fallbackMiniToggle {
    font-size: 13pt;
    min-height: 24px;
    padding: 3px 7px;
}
QCheckBox#routeMultiTurnToggle,
QCheckBox#fallbackMiniToggle {
    font-size: 13pt;
    font-weight: 600;
}
QCheckBox#routeMultiTurnToggle::indicator,
QCheckBox#fallbackMiniToggle::indicator {
    width: 16px;
    height: 16px;
}
QPushButton#actionButton[fallbackTrigger="true"] {
    text-align: left;
    padding-left: 12px;
    border-color: rgba({{border.default}}, 0.18);
}
QPushButton#actionButton[fallbackTrigger="true"]:hover {
    border-color: rgba({{accent.primary}}, 0.32);
}
QPushButton#actionButton[fallbackTrigger="true"][hasFallback="true"] {
    border-color: rgba({{accent.primary}}, 0.34);
    background: rgba({{bg.hover.accent}}, 0.9);
    color: {{accent.fallback}};
}
/* _FallbackDropdownButton — left text + right-pinned ▼ */
QFrame#fallbackDropdownBtn {
    background: rgba({{bg.control}}, 0.92);
    border: 1px solid rgba({{border.control}}, 0.18);
    border-radius: 8px;
    min-height: 26px;
}
QFrame#fallbackDropdownBtn:hover {
    background: {{bg.hover.secondary}};
    border-color: rgba({{accent.primary}}, 0.32);
}
QFrame#fallbackDropdownBtn[hasFallback="true"] {
    border-color: rgba({{accent.primary}}, 0.34);
    background: rgba({{bg.hover.accent}}, 0.9);
}
QFrame#fallbackDropdownBtn:disabled {
    background: rgba({{bg.control.hover}}, 0.6);
    border-color: rgba({{border.control}}, 0.10);
}
QLabel#fallbackDropdownLabel {
    font-size: 12pt;
    color: {{text.body.warm}};
    background: transparent;
    border: none;
}
QLabel#fallbackDropdownArrow {
    font-size: 10pt;
    color: {{text.fallback.arrow}};
    background: transparent;
    border: none;
}
QFrame#fallbackRoutesPopup {
    background: rgba({{bg.surface}}, 0.99);
    border: 1px solid rgba({{border.default}}, 0.2);
    border-radius: 12px;
}
QLabel#fallbackPopupTitle {
    color: {{text.fallback.title}};
    font-size: 12pt;
    font-weight: 700;
}
QLabel#fallbackPopupHint {
    color: {{text.muted}};
    font-size: 11pt;
}
QLabel#fallbackPopupStatus {
    color: {{text.fallback.status}};
    font-size: 11pt;
    font-weight: 600;
    background: rgba({{bg.control}}, 0.82);
    border: 1px solid rgba({{border.warm}}, 0.12);
    border-radius: 8px;
    padding: 3px 8px;
}
QListWidget#fallbackRoutesList {
    background: transparent;
    border: none;
    outline: none;
}
QWidget#fallbackPopupRow {
    border: 1px solid rgba({{border.warm}}, 0.12);
    border-radius: 10px;
    background: rgba({{bg.input}}, 0.78);
}
QWidget#fallbackPopupRow:hover {
    border-color: rgba({{border.soft}}, 0.28);
}
QWidget#fallbackPopupRow[activeRank="true"][routeEnabled="true"] {
    border-color: rgba({{accent.primary}}, 0.3);
    background: rgba({{bg.hover.accent}}, 0.94);
}
QWidget#fallbackPopupRow[activeRank="true"][rankTier="top1"] {
    border-color: rgba({{accent.rank.top1}}, 0.38);
    background: rgba({{bg.hover.accent}}, 0.97);
}
QWidget#fallbackPopupRow[activeRank="true"][rankTier="top2"] {
    border-color: rgba({{accent.rank.top2}}, 0.32);
    background: rgba({{bg.hover.accent}}, 0.95);
}
QWidget#fallbackPopupRow[activeRank="true"][rankTier="top3"] {
    border-color: rgba({{accent.rank.top3}}, 0.28);
    background: rgba({{bg.hover.accent}}, 0.94);
}
QWidget#fallbackPopupRow[activeRank="false"] {
    border-color: rgba({{border.warm}}, 0.1);
    background: rgba({{bg.panel.muted}}, 0.74);
}
QWidget#fallbackPopupRow[routeEnabled="false"] {
    border-color: rgba({{border.warm}}, 0.08);
    background: rgba({{bg.panel.muted}}, 0.72);
}
QLabel#fallbackPopupHandle {
    color: {{text.fallback.handle}};
    font-size: 12pt;
}
QLabel#fallbackPopupRank {
    border-radius: 10px;
    font-size: 11pt;
    font-weight: 700;
    color: {{accent.rank.label}};
    background: rgba({{accent.primary}}, 0.16);
}
QLabel#fallbackPopupRank[activeRank="false"] {
    color: {{text.fallback.rank.inactive}};
    background: rgba({{border.default}}, 0.2);
}
QLabel#fallbackPopupRank[rankTier="top1"] {
    color: {{accent.rank.top1}};
    background: rgba({{accent.primary}}, 0.22);
}
QLabel#fallbackPopupRank[rankTier="top2"] {
    color: {{accent.rank.top2}};
    background: rgba({{accent.rank.top2}}, 0.2);
}
QLabel#fallbackPopupRank[rankTier="top3"] {
    color: {{accent.rank.top3}};
    background: rgba({{accent.rank.top3}}, 0.18);
}
QLabel#fallbackPopupName {
    color: {{text.fallback.name}};
    font-size: 12pt;
    font-weight: 600;
}
QLabel#fallbackPopupName[activeRank="false"] {
    color: {{text.fallback.rank.inactive}};
}
QCheckBox#fallbackMiniToggle {
    color: {{text.fallback.checkbox}};
    font-size: 11pt;
    font-weight: 600;
    spacing: 3px;
}
QCheckBox#fallbackMiniToggle::indicator {
    width: 13px;
    height: 13px;
    border-radius: 4px;
    border: 1px solid rgba({{border.control}}, 0.28);
    background: rgba({{bg.input}}, 0.95);
}
QCheckBox#fallbackMiniToggle::indicator:checked {
    background: {{accent.primary}};
    border-color: {{accent.primary}};
}
QCheckBox#fallbackMiniToggle:disabled {
    color: {{text.fallback.disabled}};
}
QCheckBox#fallbackMiniToggle::indicator:disabled {
    background: rgba({{bg.control.hover}}, 0.35);
    border-color: rgba({{border.control}}, 0.14);
}

QLabel#formFieldHint {
    color: {{text.muted}};
    font-size: 11pt;
    padding-left: 2px;
}

QLabel#workflowFieldSectionTitle {
    color: {{text.heading}};
    font-size: 13pt;
    font-weight: 800;
    padding-top: 4px;
}

QLabel#workflowFieldSectionSubtitle {
    color: {{text.muted}};
    font-size: 11pt;
    padding-left: 2px;
}

QPushButton#workflowFieldCard {
    background: rgba({{bg.input.soft}}, 0.92);
    border: 1px solid rgba({{border.default}}, 0.16);
    border-radius: 8px;
    color: {{text.heading}};
    font-size: 12pt;
    font-weight: 600;
    line-height: 1.35;
    padding: 10px 12px;
    text-align: left;
}

QPushButton#workflowFieldCard:hover {
    background: rgba({{bg.hover.accent}}, 0.96);
    border-color: rgba({{accent.primary}}, 0.34);
}

QPushButton#workflowFieldCard:pressed {
    background: rgba({{bg.control.hover}}, 0.98);
    border-color: rgba({{accent.primary}}, 0.46);
}

QPushButton#fieldDialogNavButton {
    background: rgba({{bg.control}}, 0.88);
    border: 1px solid rgba({{border.control}}, 0.16);
    border-radius: 8px;
    color: {{text.body.warm}};
    font-size: 12pt;
    font-weight: 700;
    min-width: 150px;
    min-height: 34px;
    padding: 0 12px;
    text-align: left;
}

QPushButton#fieldDialogNavButton:hover {
    background: {{bg.hover.secondary}};
    border-color: rgba({{accent.primary}}, 0.28);
}

QPushButton#fieldDialogNavButton:checked {
    background: rgba({{accent.primary}}, 0.13);
    border-color: rgba({{accent.primary}}, 0.38);
    color: {{accent.deep}};
}

QTextEdit#workflowFieldDialogText {
    min-height: 360px;
}

QCheckBox#blueprintPanelToggle {
    color: {{text.body.alt}};
    font-size: 11pt;
    font-weight: 600;
    spacing: 6px;
}

QScrollArea#blueprintElementScroll {
    border: 1px solid rgba({{border.default}}, 0.12);
    border-radius: 10px;
    background: {{bg.surface}};
}

QScrollArea#blueprintElementScroll > QWidget > QWidget {
    background: {{bg.surface}};
}

QWidget#blueprintElementRow {
    border: 1px solid rgba({{border.warm}}, 0.14);
    border-radius: 10px;
    background: {{bg.blueprint.row}};
}

QWidget#blueprintElementRow:hover {
    border-color: rgba({{accent.primary}}, 0.26);
    background: rgba({{bg.hover.accent}}, 0.94);
}

QLabel#blueprintElementName {
    color: {{text.artifact}};
    font-size: 11pt;
    font-weight: 600;
    letter-spacing: 0.25px;
}

QLabel#blueprintCategoryBadge {
    color: {{text.blueprint.badge}};
    background: rgba({{accent.primary}}, 0.12);
    border: 1px solid rgba({{accent.primary}}, 0.2);
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 10pt;
    font-weight: 600;
}

QCheckBox#blueprintRowToggle {
    color: {{text.body.alt}};
    font-size: 11pt;
    font-weight: 500;
    spacing: 6px;
    padding-right: 6px;
}

QLabel#blueprintWeightMeta {
    color: {{text.muted}};
    font-size: 10pt;
    letter-spacing: 0.2px;
}

QLabel#blueprintWeightValue {
    color: {{text.body}};
    font-size: 11pt;
    font-weight: 600;
}

QWidget#blueprintElementRow[densityProfile="compact"] QLabel#blueprintElementName {
    font-size: 10pt;
}

QWidget#blueprintElementRow[densityProfile="compact"] QLabel#blueprintCategoryBadge {
    font-size: 9pt;
    padding: 1px 6px;
}

QWidget#blueprintElementRow[densityProfile="compact"] QCheckBox#blueprintRowToggle {
    font-size: 10pt;
}

QWidget#blueprintElementRow[densityProfile="compact"] QLabel#blueprintWeightMeta {
    font-size: 9pt;
}

QWidget#blueprintElementRow[densityProfile="compact"] QLabel#blueprintWeightValue {
    font-size: 10pt;
}

QWidget#blueprintElementRow[densityProfile="roomy"] QLabel#blueprintElementName {
    font-size: 12pt;
}

QWidget#blueprintElementRow[densityProfile="roomy"] QLabel#blueprintCategoryBadge {
    font-size: 10pt;
}

QWidget#settingInputWrap {
    min-width: 200px;
    max-width: 220px;
}

QWidget#secretLineInput {
    background: transparent;
}

QWidget#secretLineInput QLineEdit {
    min-width: 120px;
}

QToolButton#secretToggleButton {
    min-height: 32px;
    padding: 4px 8px;
    border: 1px solid rgba({{border.default}}, 0.14);
    border-radius: 8px;
    background: rgba({{bg.control}}, 0.72);
    color: rgba({{text.primary}}, 0.86);
    font-size: 11pt;
    font-weight: 700;
}

QToolButton#secretToggleButton:hover {
    background: rgba({{accent.primary}}, 0.12);
    border-color: rgba({{accent.primary}}, 0.26);
}

QToolButton#secretToggleButton:checked {
    background: rgba({{accent.primary}}, 0.18);
    color: rgb({{accent.primary}});
    border-color: rgba({{accent.primary}}, 0.32);
}

QSpinBox,
QDoubleSpinBox,
QComboBox,
QLineEdit {
    min-height: 32px;
    padding: 4px 10px;
    font-size: 13pt;
}

QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus,
QLineEdit:focus {
    border-color: rgba({{accent.primary}}, 0.4);
    outline: 2px solid rgba({{accent.primary}}, 0.6);
    outline-offset: 2px;
}

QSpinBox::up-button,
QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border: none;
    border-top-right-radius: 10px;
    background: rgba({{bg.control}}, 0.78);
}

QSpinBox::down-button,
QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border: none;
    border-bottom-right-radius: 10px;
    background: rgba({{bg.control}}, 0.78);
}

QSpinBox::up-button:hover,
QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {
    background: rgba({{bg.control.hover}}, 0.92);
}

QSpinBox::up-button:pressed,
QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed,
QDoubleSpinBox::down-button:pressed {
    background: rgba({{bg.control.pressed}}, 0.94);
}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {
    image: url(__ARROW_UP__);
    width: 10px;
    height: 6px;
}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {
    image: url(__ARROW_DOWN__);
    width: 10px;
    height: 6px;
}

/* ── Required field label ──────────────────────────────────────────── */

QLabel#fieldRequiredLabel {
    color: {{text.heading.warm}};
    font-size: 13pt;
    font-weight: 700;
}

/* ── Task 20 — Settings task route row hover highlight ─────────────── */

/* Hover tint derives from ``bg.hover.secondary`` at 70% alpha so the
   row reads as "elevated but not selected" while keeping the underlying
   Surface tone visible.  Pixel-identical to the legacy hover (D13). */
QWidget#taskRouteRow {
    border-radius: 8px;
}

QWidget#taskRouteRow:hover {
    background: rgba({{bg.hover.secondary}}, 0.7);
    border-radius: 8px;
}

"""

CONTENT = _CONTENT.replace("border-radius: 8px;", f"border-radius: {CHIP_RADIUS}px;").replace(
    "border-radius: 12px;", f"border-radius: {COMPACT_CARD_RADIUS}px;"
)
