"""Character profile page (角色档案).

Browse mode (default):
    ┌─ character cards ─┬─ summary/details ─┬─ inspector (selected) ─┐
    │ Alice (active)    │ Card title         │ Recent relationships    │
    │ Bob   (dormant)   │ Status chips       │ Appeared chapters       │
    │ ...               │ Arc / traits cards │ Status tag              │
    └───────────────────┴────────────────────┴─────────────────────────┘

Edit mode (toggled by the "编辑" action button on the selected character):
    Same 3-column layout, but the right column flips to an editable
    inspector with the role/age/personality/... fields, and a SaveBar
    floats up from the bottom once the user starts typing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.schemas.bible import CharacterProfile
from novel_forge.desktop.components.primitives import (
    ActionButton,
    Badge,
    FilterChip,
    SectionHeading,
    Surface,
    clear_layout,
)
from novel_forge.desktop.motion import animations_supported
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.pages.standalone.character_bible_store import CharacterBibleStore
from novel_forge.desktop.pages.standalone.character_shared_widgets import InspectorPanel, SaveBar
from novel_forge.desktop.widgets import show_warning_message

# ── Field metadata (label + input type) ──────────────────────────────────

_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("name", "姓名"),
    ("role", "定位"),
    ("status", "状态"),
    ("time_layer", "时间线"),
    ("age", "年龄"),
    ("gender", "性别"),
    ("social_status", "身份 / 职业"),
    ("abilities", "能力 / 资源"),
    ("appearance", "外貌"),
    ("personality", "性格"),
    ("backstory", "背景故事"),
    ("motivation", "动机"),
    ("arc", "人物弧线"),
)

_TEXT_FIELDS = {
    "abilities",
    "appearance",
    "personality",
    "backstory",
    "motivation",
    "arc",
}

_CHOICE_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "role": (
        ("protagonist", "主角"),
        ("deuteragonist", "重要配角"),
        ("antagonist", "对手"),
        ("supporting", "一般配角"),
        ("background", "背景人物"),
    ),
    "status": (
        ("active", "活跃"),
        ("dormant", "暂离"),
        ("retired", "退场"),
    ),
    "time_layer": (
        ("default", "默认"),
        ("modern", "当前线"),
        ("past", "过去线"),
        ("cross_temporal", "跨时间线"),
    ),
}


@dataclass(frozen=True)
class _StatusTone:
    bg: str
    fg: str
    label: str


_STATUS_TONES: dict[str, _StatusTone] = {
    "active": _StatusTone("success", "活跃", "活跃"),
    "dormant": _StatusTone("warning", "暂离", "暂离"),
    "retired": _StatusTone("muted", "退场", "退场"),
}


# ── Page entry point ─────────────────────────────────────────────────────


class CharacterProfilePage(QWidget):
    """Browse/edit character profile workspace.

    Implements the legacy ``has_unsaved_changes`` / ``save_pending_changes``
    / ``confirm_close`` contract so it can replace ``CharacterBibleEditor``
    inside ``ProjectsPage._add_project_document_tab`` without touching the
    host page.
    """

    workspace_refresh_requested = Signal()

    def __init__(self, store: CharacterBibleStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._current_name: str = self._store.first_character_name()
        self._line_fields: dict[str, QLineEdit] = {}
        self._text_fields: dict[str, QTextEdit] = {}
        self._choice_chips: dict[str, list[FilterChip]] = {}
        self._loading = False
        self._browse_anim: QPropertyAnimation | None = None

        self._build_ui()
        self._wire_store()
        self._populate_character_list()
        if self._current_name:
            self._select_character(self._current_name)
        else:
            self._render_empty_detail()

    def shutdown(self) -> None:
        """Stop timers, disconnect signals, and tear down sub-widgets (I-5).

        Idempotent: safe to call multiple times.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        # Disconnect store signals (host owns the store; we just unsub our handlers)
        store = getattr(self, "_store", None)
        if store is not None:
            safe_disconnect(store.bibleChanged, self._on_bible_changed)
            safe_disconnect(store.dirtyChanged, self._on_dirty_changed)
            safe_disconnect(store.saveWarningsChanged, self._on_warnings_changed)
            safe_disconnect(store.requestExternalRefresh, self.workspace_refresh_requested.emit)

        # Disconnect inspector and save-bar signals we connected
        inspector = getattr(self, "_inspector", None)
        if inspector is not None:
            safe_disconnect(inspector.closed, self._on_inspector_closed)
        save_bar = getattr(self, "_save_bar", None)
        if save_bar is not None:
            safe_disconnect(save_bar.saveClicked, self._on_save_clicked)
            safe_disconnect(save_bar.discardClicked, self._on_discard_clicked)

        # Disconnect list widget signals
        list_widget = getattr(self, "_list_widget", None)
        if list_widget is not None:
            safe_disconnect(list_widget.currentItemChanged, self._on_list_item_changed)

        # Disconnect field/chip signals
        for line in getattr(self, "_line_fields", {}).values():
            safe_disconnect(line.textChanged)
        for edit in getattr(self, "_text_fields", {}).values():
            safe_disconnect(edit.textChanged)
        for chips in getattr(self, "_choice_chips", {}).values():
            for chip in chips:
                safe_disconnect(chip.clicked)

        # Disconnect action buttons
        for attr in ("_add_button", "_retire_button", "_edit_toggle"):
            btn = getattr(self, attr, None)
            if btn is not None:
                safe_disconnect(btn.clicked)

        # Stop any in-flight detail-swap animation
        anim = getattr(self, "_browse_anim", None)
        if anim is not None:
            try:
                if anim.state() == anim.State.Running:  # type: ignore[attr-defined]
                    anim.stop()
            except (RuntimeError, AttributeError):
                pass

    # ── Public contract (used by ProjectsPage) ────────────────────────

    def has_unsaved_changes(self) -> bool:
        return self._store.dirty

    def unsaved_changes_description(self) -> str:
        return "- 角色档案页有未保存修改"

    def save_pending_changes(self) -> bool:
        if not self._collect_form_into_payload():
            return False
        try:
            self._store.request_save()
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "角色档案保存失败", str(exc))
            return False
        return True

    def confirm_close(self) -> bool:
        from PySide6.QtWidgets import QMessageBox

        from novel_forge.desktop.components.dialogs import (
            MessageBoxAction,
            show_message_box,
        )

        if not self.has_unsaved_changes():
            return True
        choice = show_message_box(
            self.window(),
            "角色档案尚未保存",
            "当前角色资料修改尚未写入项目文件。",
            informative_text="可以保存后继续，也可以放弃这次未保存修改。",
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    "save",
                    "保存",
                    QMessageBox.ButtonRole.AcceptRole,
                    "primary",
                    True,
                ),
                MessageBoxAction(
                    "discard",
                    "放弃修改",
                    QMessageBox.ButtonRole.DestructiveRole,
                    "danger",
                ),
                MessageBoxAction(
                    "cancel",
                    "继续编辑",
                    QMessageBox.ButtonRole.RejectRole,
                    "secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice == "save":
            return self.save_pending_changes()
        if choice == "discard":
            self._store.request_discard()
            return True
        return False

    # ── UI assembly ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        outer.addWidget(
            SectionHeading(
                "角色档案",
                "浏览角色列表、详情与人物弧线；点击编辑进入编辑模式，修改后保存栏会自动浮起。",
            )
        )

        # Page-level actions: 新增 / 进入编辑模式 / 标记退场
        action_bar = QHBoxLayout()
        action_bar.setSpacing(8)
        self._add_button = ActionButton("新增角色", variant="secondary")
        self._add_button.clicked.connect(self._on_add_character)
        action_bar.addWidget(self._add_button)

        self._retire_button = ActionButton("标记退场", variant="quiet")
        self._retire_button.setProperty("compact", True)
        self._retire_button.clicked.connect(self._on_retire_selected)
        action_bar.addWidget(self._retire_button)
        action_bar.addStretch(1)

        self._edit_toggle = ActionButton("编辑", variant="primary")
        self._edit_toggle.setProperty("compact", True)
        self._edit_toggle.clicked.connect(self._on_edit_toggle_clicked)
        action_bar.addWidget(self._edit_toggle)

        outer.addLayout(action_bar)

        # Three-column body: list / detail / inspector.
        body = QHBoxLayout()
        body.setSpacing(10)
        body.setContentsMargins(0, 0, 0, 0)

        body.addWidget(self._build_list_panel(), 0)
        body.addWidget(self._build_detail_panel(), 1)
        self._inspector = InspectorPanel(
            browse_widget=self._build_inspector_browse(),
            edit_widget=self._build_inspector_edit(),
            title="详情",
        )
        self._inspector.closed.connect(self._on_inspector_closed)
        self._inspector.setMinimumWidth(280)
        self._inspector.setMaximumWidth(360)
        body.addWidget(self._inspector, 0)
        outer.addLayout(body, 1)

        # Save bar — sits in the layout as the very last item, full width.
        self._save_bar = SaveBar(self)
        self._save_bar.saveClicked.connect(self._on_save_clicked)
        self._save_bar.discardClicked.connect(self._on_discard_clicked)
        outer.addWidget(self._save_bar)

        self._sync_save_bar()

    def _build_list_panel(self) -> QWidget:
        panel = Surface("inset")
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QLabel("角色")
        header.setObjectName("cardMeta")
        layout.addWidget(header)

        self._list_widget = QListWidget()
        self._list_widget.setObjectName("readerList")
        self._list_widget.setSpacing(4)
        self._list_widget.currentItemChanged.connect(self._on_list_item_changed)
        layout.addWidget(self._list_widget, 1)
        return panel

    def _build_detail_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(12)

        # Title / status row
        self._detail_title_row = QHBoxLayout()
        self._detail_title_row.setSpacing(10)
        self._detail_title = QLabel("选择角色")
        self._detail_title.setObjectName("cardTitleLarge")
        self._detail_title_row.addWidget(self._detail_title)
        self._detail_title_row.addStretch(1)
        self._detail_status_chip = Badge("", tone="default")
        self._detail_status_chip.setVisible(False)
        self._detail_title_row.addWidget(self._detail_status_chip)
        layout.addLayout(self._detail_title_row)

        # Identity cards row
        self._identity_grid = QGridLayout()
        self._identity_grid.setHorizontalSpacing(10)
        self._identity_grid.setVerticalSpacing(8)
        layout.addLayout(self._identity_grid)

        # Arc summary card
        self._arc_card = Surface("card")
        self._arc_card.setProperty("compact", "true")
        arc_layout = QVBoxLayout(self._arc_card)
        arc_layout.setContentsMargins(14, 12, 14, 12)
        arc_layout.setSpacing(6)
        arc_title = QLabel("人物弧线")
        arc_title.setObjectName("cardTitle")
        arc_layout.addWidget(arc_title)
        self._arc_body = QLabel("选择左侧角色查看人物弧线。")
        self._arc_body.setObjectName("cardBody")
        self._arc_body.setWordWrap(True)
        arc_layout.addWidget(self._arc_body)
        layout.addWidget(self._arc_card)

        # Long-form fields (motivation / backstory / ...)
        self._long_card = Surface("card")
        self._long_card.setProperty("compact", "true")
        long_layout = QVBoxLayout(self._long_card)
        long_layout.setContentsMargins(14, 12, 14, 12)
        long_layout.setSpacing(8)
        long_title = QLabel("背景 / 性格 / 外貌")
        long_title.setObjectName("cardTitle")
        long_layout.addWidget(long_title)
        self._long_body = QLabel("")
        self._long_body.setObjectName("cardBody")
        self._long_body.setWordWrap(True)
        self._long_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        long_layout.addWidget(self._long_body)
        layout.addWidget(self._long_card)

        layout.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _build_inspector_browse(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        relationships_card = self._build_inspector_section(
            "主要关系",
            "选择角色后展示其关系摘要。",
        )
        self._browse_relationships_label = relationships_card["body"]
        layout.addWidget(relationships_card["root"])

        appeared_card = self._build_inspector_section(
            "登场章节",
            "该角色在已生成章节中的登场情况。",
        )
        self._browse_appeared_label = appeared_card["body"]
        layout.addWidget(appeared_card["root"])

        tags_card = self._build_inspector_section(
            "角色标签",
            "由定位 / 状态 / 时间线自动汇总。",
        )
        self._browse_tags_label = tags_card["body"]
        layout.addWidget(tags_card["root"])

        layout.addStretch(1)
        return wrap

    def _build_inspector_edit(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self._edit_status_label = QLabel("修改会立即影响档案与图谱；底部保存栏会显示未保存项。")
        self._edit_status_label.setObjectName("fieldHint")
        self._edit_status_label.setWordWrap(True)
        layout.addWidget(self._edit_status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form = QWidget()
        grid = QGridLayout(form)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        for row, (key, label_text) in enumerate(_FIELD_LABELS):
            label = QLabel(label_text)
            label.setObjectName("cardMeta")
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            if key in _CHOICE_OPTIONS:
                host = QWidget()
                host_layout = QHBoxLayout(host)
                host_layout.setContentsMargins(0, 0, 0, 0)
                host_layout.setSpacing(6)
                chips: list[FilterChip] = []
                for value, chip_label in _CHOICE_OPTIONS[key]:
                    chip = FilterChip(chip_label, active=False)
                    chip.setProperty("dataValue", value)
                    chip.clicked.connect(self._make_chip_clicked(key, value))
                    host_layout.addWidget(chip)
                    chips.append(chip)
                host_layout.addStretch(1)
                self._choice_chips[key] = chips
                grid.addWidget(host, row, 1)
            elif key in _TEXT_FIELDS:
                edit = QTextEdit()
                edit.setMinimumHeight(72)
                edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                edit.setObjectName("characterEditTextField")
                edit.textChanged.connect(self._mark_dirty)
                self._text_fields[key] = edit
                grid.addWidget(edit, row, 1)
            else:
                line = QLineEdit()
                line.setMinimumHeight(32)
                line.setObjectName("characterEditLineField")
                line.textChanged.connect(self._mark_dirty)
                self._line_fields[key] = line
                grid.addWidget(line, row, 1)

        scroll.setWidget(form)
        layout.addWidget(scroll, 1)
        return wrap

    def _build_inspector_section(self, title: str, hint: str) -> dict[str, Any]:
        card = Surface("card")
        card.setProperty("compact", "true")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("cardMeta")
        v.addWidget(title_label)
        hint_label = QLabel(hint)
        hint_label.setObjectName("fieldHint")
        hint_label.setWordWrap(True)
        v.addWidget(hint_label)
        body_label = QLabel("—")
        body_label.setObjectName("cardBody")
        body_label.setWordWrap(True)
        v.addWidget(body_label)
        return {"root": card, "body": body_label, "title": title_label}

    # ── Store wiring ──────────────────────────────────────────────────

    def _wire_store(self) -> None:
        self._store.bibleChanged.connect(self._on_bible_changed)
        self._store.dirtyChanged.connect(self._on_dirty_changed)
        self._store.saveWarningsChanged.connect(self._on_warnings_changed)
        self._store.requestExternalRefresh.connect(self.workspace_refresh_requested.emit)

    def _on_bible_changed(self) -> None:
        self._populate_character_list()
        if self._current_name:
            self._select_character(self._current_name, animate=False)
        else:
            self._render_empty_detail()

    def _on_dirty_changed(self, dirty: bool) -> None:
        self._sync_save_bar()

    def _on_warnings_changed(self, warnings: list[dict[str, str]]) -> None:
        self._sync_save_bar(warnings=[w.get("message", "") for w in warnings])

    def _sync_save_bar(self, *, warnings: list[str] | None = None) -> None:
        dirty = self._store.dirty
        summary = (
            f"「{self._current_name or '当前角色'}」有未保存修改"
            if dirty and self._current_name
            else "有未保存修改"
        )
        self._save_bar.show_dirty(
            dirty=dirty,
            summary=summary,
            warnings=() if warnings is None else tuple(warnings),
        )

    # ── List / detail population ──────────────────────────────────────

    def _populate_character_list(self) -> None:
        self._loading = True
        try:
            previous = self._current_name
            self._list_widget.clear()
            for index, profile in enumerate(self._store.bible.characters):
                name = clean_character_name(profile.name)
                item = QListWidgetItem(_character_list_label(profile))
                item.setData(Qt.ItemDataRole.UserRole, name)
                self._list_widget.addItem(item)
                if name == previous:
                    self._list_widget.setCurrentRow(index)
            if self._list_widget.count() and self._list_widget.currentRow() < 0:
                self._list_widget.setCurrentRow(0)
        finally:
            self._loading = False

    def _on_list_item_changed(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        if self._loading or current is None:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if name and name != self._current_name:
            self._select_character(name)

    def _select_character(self, name: str, *, animate: bool = True) -> None:
        self._current_name = name
        profile = self._store.profile_by_name(name)
        self._render_detail(profile, animate=animate)
        self._populate_inspector_browse(profile)
        self._populate_inspector_edit(profile)
        # Ensure the inspector is showing the browse surface in browse mode.
        if not self._inspector.is_edit_visible():
            self._inspector.show_browse(animated=False)
        self._sync_save_bar()

    def _render_empty_detail(self) -> None:
        self._detail_title.setText("暂无角色")
        self._detail_status_chip.setVisible(False)
        for i in reversed(range(self._identity_grid.count())):
            item = self._identity_grid.takeAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._arc_body.setText("完成立项后此处会出现角色人物弧线。")
        self._long_body.setText("")

    def _render_detail(self, profile: CharacterProfile | None, *, animate: bool) -> None:
        clear_layout(self._identity_grid)
        if profile is None:
            self._render_empty_detail()
            return

        self._detail_title.setText(clean_character_name(profile.name) or "未命名角色")
        tone = _STATUS_TONES.get(str(profile.status or "active"), _STATUS_TONES["active"])
        self._detail_status_chip.setText(tone.label)
        self._detail_status_chip.set_tone(tone.bg)
        self._detail_status_chip.setVisible(True)

        identity_fields = (
            ("定位", _role_label(profile.role)),
            ("时间线", _time_layer_label(profile.time_layer)),
            ("年龄", str(profile.age or "")),
            ("性别", str(profile.gender or "")),
            ("身份", str(profile.social_status or "")),
        )
        for col, (label_text, value_text) in enumerate(identity_fields):
            cell = self._build_identity_cell(label_text, value_text or "—")
            self._identity_grid.addWidget(cell, 0, col)
        self._identity_grid.setColumnStretch(0, 1)
        self._identity_grid.setColumnStretch(1, 1)
        self._identity_grid.setColumnStretch(2, 1)
        self._identity_grid.setColumnStretch(3, 1)
        self._identity_grid.setColumnStretch(4, 1)

        self._arc_body.setText(_strip_or_default(profile.arc, "暂无人物弧线。"))
        long_text = _compose_long_form(profile)
        self._long_body.setText(long_text or "暂无详细描述。")

        if animate:
            self._animate_detail_swap()

    def _build_identity_cell(self, label: str, value: str) -> QWidget:
        cell = Surface("inset")
        cell.setProperty("compact", "true")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        label_label = QLabel(label)
        label_label.setObjectName("fieldHint")
        layout.addWidget(label_label)
        value_label = QLabel(value)
        value_label.setObjectName("cardTitle")
        value_label.setWordWrap(True)
        layout.addWidget(value_label)
        return cell

    def _populate_inspector_browse(self, profile: CharacterProfile | None) -> None:
        if profile is None:
            self._browse_relationships_label.setText("选择左侧角色查看关系摘要。")
            self._browse_appeared_label.setText("—")
            self._browse_tags_label.setText("—")
            return

        relationships = profile.relationships or {}
        if not relationships:
            self._browse_relationships_label.setText("暂无关系。")
        else:
            self._browse_relationships_label.setText(
                "\n".join(
                    f"• {_rel_display_name(name)}：{_strip_or_default(value, '—')}"
                    for name, value in relationships.items()
                )
            )

        self._browse_appeared_label.setText(_summarize_appearance(profile))

        tag_lines = [
            f"• 定位：{_role_label(profile.role)}",
            f"• 状态：{_STATUS_TONES.get(str(profile.status or 'active'), _STATUS_TONES['active']).label}",
            f"• 时间线：{_time_layer_label(profile.time_layer)}",
        ]
        self._browse_tags_label.setText("\n".join(tag_lines))

    def _populate_inspector_edit(self, profile: CharacterProfile | None) -> None:
        self._loading = True
        try:
            for key, line in self._line_fields.items():
                line.setText(str(_get_attr(profile, key) or ""))
            for key, edit in self._text_fields.items():
                edit.setPlainText(str(_get_attr(profile, key) or ""))
            for key, chips in self._choice_chips.items():
                current = str(_get_attr(profile, key) or "")
                for chip in chips:
                    chip.setChecked(chip.property("dataValue") == current)
        finally:
            self._loading = False
        self._edit_status_label.setText(
            "修改字段后保存栏会自动浮起；点击保存写入项目文件。"
            if profile is not None
            else "请先在左侧选择或新增角色。"
        )

    # ── Inspector browse / edit switching ─────────────────────────────

    def _on_edit_toggle_clicked(self) -> None:
        if not self._current_name:
            show_warning_message(self.window(), "暂无可编辑角色", "请先新增或选择一个角色。")
            return
        if self._inspector.is_edit_visible():
            # Switch back to browse without forcing a save — the user might
            # want to discard from the save bar instead.
            self._inspector.show_browse(animated=True)
            self._edit_toggle.setText("编辑")
        else:
            self._inspector.show_edit(animated=True)
            self._edit_toggle.setText("取消编辑")

    def _on_inspector_closed(self) -> None:
        if self._inspector.is_edit_visible():
            self._edit_toggle.setText("编辑")
            self._inspector.show_browse(animated=True)

    # ── Mutations ─────────────────────────────────────────────────────

    def _make_chip_clicked(self, key: str, value: str) -> Callable[[], None]:
        def _on_click() -> None:
            if self._loading:
                return
            for chip in self._choice_chips.get(key, []):
                chip.setChecked(chip.property("dataValue") == value)
            self._mark_dirty()

        return _on_click

    def _mark_dirty(self) -> None:
        if self._loading:
            return
        # Build a fresh bible payload from the current form, then push it
        # through the store so the dirty flag is computed correctly.
        self._collect_form_into_payload()

    def _collect_form_into_payload(self) -> bool:
        if not self._current_name:
            return True
        payload = self._store.bible.model_dump(mode="json")
        target_name = self._current_name
        updated = False
        for index, character in enumerate(payload.get("characters", [])):
            if clean_character_name(character.get("name")) != target_name:
                continue
            for key, line in self._line_fields.items():
                character[key] = line.text()
            for key, edit in self._text_fields.items():
                character[key] = edit.toPlainText()
            for key, chips in self._choice_chips.items():
                for chip in chips:
                    if chip.isChecked():
                        character[key] = chip.property("dataValue")
                        break
                else:
                    continue
            payload["characters"][index] = character
            updated = True
            break
        if updated:
            self._store.apply_pending_payload(payload)
        return True

    # ── Save / discard callbacks ──────────────────────────────────────

    def _on_save_clicked(self) -> None:
        if self.save_pending_changes():
            # Restore browse surface after a successful save so the user
            # sees the persisted state.
            self._inspector.show_browse(animated=True)
            self._edit_toggle.setText("编辑")

    def _on_discard_clicked(self) -> None:
        self._store.request_discard()
        self._inspector.show_browse(animated=True)
        self._edit_toggle.setText("编辑")

    # ── Add / retire ──────────────────────────────────────────────────

    def _on_add_character(self) -> None:
        from novel_forge.desktop.pages.standalone.character_bible_editor import (
            CharacterProfileDialog,
        )

        if not self.save_pending_changes():
            return
        dialog = CharacterProfileDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._store.request_add_character(dialog.payload())
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "新增角色失败", str(exc))
            return
        new_name = clean_character_name(dialog.payload().get("name"))
        self._current_name = new_name
        self._select_character(new_name, animate=True)

    def _on_retire_selected(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from novel_forge.desktop.components.dialogs import (
            MessageBoxAction,
            show_message_box,
        )

        if not self._current_name:
            return
        if not self.save_pending_changes():
            return
        choice = show_message_box(
            self.window(),
            "标记角色退场",
            f"将「{self._current_name}」标记为 retired，不会删除历史引用。",
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    "retire",
                    "标记退场",
                    QMessageBox.ButtonRole.AcceptRole,
                    "primary",
                    True,
                ),
                MessageBoxAction(
                    "cancel",
                    "取消",
                    QMessageBox.ButtonRole.RejectRole,
                    "secondary",
                ),
            ),
            escape_key="cancel",
        )
        if choice != "retire":
            return
        try:
            self._store.request_retire_character(self._current_name)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "标记退场失败", str(exc))

    # ── Animations ────────────────────────────────────────────────────

    def _animate_detail_swap(self) -> None:
        # Lightweight cross-fade on the detail panel when the selection
        # changes. We touch only the bodies that swap text — the title is
        # recoloured by QSS so a fade looks like a flicker there.
        if not animations_supported("opacity"):
            return
        for child in (self._arc_body, self._long_body):
            effect = child.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(child)
                child.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            anim = QPropertyAnimation(effect, b"opacity")
            anim.setDuration(220)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()


# ── Helpers ──────────────────────────────────────────────────────────────


def _role_label(value: str | None) -> str:
    mapping = dict(_CHOICE_OPTIONS["role"])
    return mapping.get(str(value or ""), mapping["supporting"])


def _time_layer_label(value: str | None) -> str:
    mapping = dict(_CHOICE_OPTIONS["time_layer"])
    return mapping.get(str(value or ""), mapping["default"])


def _rel_display_name(name: str) -> str:
    return clean_character_name(name) or "（未命名）"


def _strip_or_default(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _get_attr(profile: CharacterProfile | None, key: str) -> object:
    if profile is None:
        return ""
    if hasattr(profile, key):
        return getattr(profile, key)
    return ""


def _compose_long_form(profile: CharacterProfile) -> str:
    sections: list[str] = []
    for key, label in (
        ("motivation", "动机"),
        ("backstory", "背景故事"),
        ("personality", "性格"),
        ("appearance", "外貌"),
        ("abilities", "能力 / 资源"),
    ):
        value = _strip_or_default(getattr(profile, key, ""), "")
        if value:
            sections.append(f"【{label}】\n{value}")
    return "\n\n".join(sections)


def _summarize_appearance(profile: CharacterProfile) -> str:
    # We don't yet have a chapter-appearance index surfaced here; for the
    # MVP we summarize the bible's status / time_layer and prompt the user
    # that the chapter-level data comes from chapter runs.
    status_label = _STATUS_TONES.get(str(profile.status or "active"), _STATUS_TONES["active"]).label
    return (
        f"状态：{status_label}\n"
        f"时间线：{_time_layer_label(profile.time_layer)}\n"
        "（章节登场统计由章节生成后写入角色档案）"
    )


def _character_list_label(profile: CharacterProfile) -> str:
    suffix = " · 退场" if str(profile.status or "active") == "retired" else ""
    return f"{clean_character_name(profile.name)}{suffix}"


__all__ = ["CharacterProfilePage"]
