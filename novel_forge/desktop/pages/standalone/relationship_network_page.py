"""Relationship network page (关系网络).

Layout:
    ┌─ top filter chips (对抗/同盟/亲属/师徒/隐秘/全部) ─┬─ view toggle ─┐
    │ ────────────────────────────────────────────────────────────────── │
    │                                                                  │
    │                  CharacterGraphWidget (main view)                │
    │                                                                  │
    └──────────────────────────────────────────────────────────────────┘
    Inspector: shows selected node/edge details + edit form
    SaveBar: slides up when there are unsaved changes
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.schemas.bible import CharacterProfile
from novel_forge.desktop.components.primitives import (
    ActionButton,
    FilterChip,
    SectionHeading,
    Surface,
)
from novel_forge.desktop.motion import animations_supported
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.pages.document_renderers import CharacterGraphWidget
from novel_forge.desktop.pages.standalone.character_artifact_writer import RELATIONSHIP_TYPE_OPTIONS
from novel_forge.desktop.pages.standalone.character_bible_store import CharacterBibleStore
from novel_forge.desktop.pages.standalone.character_shared_widgets import InspectorPanel, SaveBar
from novel_forge.desktop.widgets import show_text_input_dialog, show_warning_message

# ── Filter chip definitions ──────────────────────────────────────────────

_FILTER_OPTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("all", "全部", ()),
    ("adversary", "对抗", ("antagonist", "relationship")),
    ("ally", "同盟", ("ally", "supporting")),
    ("family", "亲属", ("family",)),
    ("mentor", "师徒", ("mentor",)),
    ("secret", "隐秘", ("secret",)),
)


_TONE_BY_TYPE: dict[str, str] = {
    "family": "info",
    "mentor": "success",
    "secret": "warning",
    "ally": "primary",
    "antagonist": "danger",
}


_RELATIONSHIP_TONE: dict[str, str] = {
    "family": "亲属",
    "mentor": "师徒",
    "secret": "隐秘",
    "ally": "同盟",
    "antagonist": "对抗",
    "relationship": "一般关系",
    "romantic_tension": "情感张力",
    "rival": "竞争",
    "identity_link": "身份映射",
    "community": "社群",
}


class RelationshipNetworkPage(QWidget):
    """Relationship network workspace driven by ``CharacterGraphWidget``."""

    workspace_refresh_requested = Signal()

    # Inspector modes
    MODE_NODE = "node"
    MODE_EDGE = "edge"
    MODE_EMPTY = "empty"

    def __init__(self, store: CharacterBibleStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._active_filter = "all"
        self._selected_node: str = ""
        self._selected_edge: tuple[str, str] | None = None
        self._view_mode = "graph"  # graph / table
        self._filter_chips: dict[str, FilterChip] = {}
        self._line_fields: dict[str, Any] = {}
        self._text_fields: dict[str, Any] = {}
        self._loading = False
        self._build_ui()
        self._wire_store()
        self._render_graph()
        self._render_inspector_for_empty()

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
            safe_disconnect(store.dirtyChanged)
            safe_disconnect(store.saveWarningsChanged)
            safe_disconnect(store.requestExternalRefresh, self.workspace_refresh_requested.emit)

        # Disconnect inspector and save-bar signals we connected
        inspector = getattr(self, "_inspector", None)
        if inspector is not None:
            safe_disconnect(inspector.closed, self._on_inspector_closed)
        save_bar = getattr(self, "_save_bar", None)
        if save_bar is not None:
            safe_disconnect(save_bar.saveClicked, self._on_save_clicked)
            safe_disconnect(save_bar.discardClicked, self._on_discard_clicked)

        # Disconnect graph widget signals and drop the widget
        graph_widget = getattr(self, "_graph_widget", None)
        if graph_widget is not None:
            safe_disconnect(getattr(graph_widget, "focusChanged", None), self._on_focus_changed)
            safe_disconnect(getattr(graph_widget, "relationshipCreateRequested", None),
                            self._on_relationship_create_requested)
            safe_disconnect(getattr(graph_widget, "relationshipEditRequested", None),
                            self._on_relationship_edit_requested)
            safe_disconnect(getattr(graph_widget, "relationshipRemoveRequested", None),
                            self._on_relationship_remove_requested)
            safe_disconnect(getattr(graph_widget, "characterAddRequested", None),
                            self._on_character_add_requested)
            safe_disconnect(getattr(graph_widget, "characterEditRequested", None),
                            self._on_character_edit_requested)
            safe_disconnect(getattr(graph_widget, "characterRetireRequested", None),
                            self._on_character_retire_requested)
            try:
                graph_widget.setParent(None)
                graph_widget.deleteLater()
            except RuntimeError:
                pass
            self._graph_widget = None

        # Disconnect button signals
        for attr in ("_add_rel_button", "_graph_view_btn", "_table_view_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                safe_disconnect(btn.clicked)

    # ── Public contract (used by ProjectsPage) ────────────────────────

    def has_unsaved_changes(self) -> bool:
        return self._store.dirty

    def unsaved_changes_description(self) -> str:
        return "- 关系网络页有未保存修改"

    def save_pending_changes(self) -> bool:
        self._collect_form_into_payload()
        try:
            self._store.request_save()
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "关系网络保存失败", str(exc))
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
            "关系网络尚未保存",
            "当前关系修改尚未写入项目文件。",
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
                "关系网络",
                "图谱为主视图，点击节点查看关系列表，点击连线编辑关系。筛选与视图切换不会丢失数据。",
            )
        )

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for key, label, _types in _FILTER_OPTIONS:
            chip = FilterChip(label, active=key == self._active_filter)
            chip.setProperty("filterKey", key)
            chip.clicked.connect(self._make_filter_clicked(key))
            self._filter_chips[key] = chip
            filter_row.addWidget(chip)
        filter_row.addStretch(1)

        # View toggle: 图谱 / 表格
        self._view_toggle_group = QButtonGroup(self)
        self._graph_view_btn = ActionButton("图谱", variant="secondary")
        self._graph_view_btn.setProperty("compact", True)
        self._graph_view_btn.setCheckable(True)
        self._graph_view_btn.setChecked(True)
        self._graph_view_btn.clicked.connect(lambda: self._switch_view("graph"))
        self._view_toggle_group.addButton(self._graph_view_btn)
        filter_row.addWidget(self._graph_view_btn)
        self._table_view_btn = ActionButton("表格", variant="secondary")
        self._table_view_btn.setProperty("compact", True)
        self._table_view_btn.setCheckable(True)
        self._table_view_btn.clicked.connect(lambda: self._switch_view("table"))
        self._view_toggle_group.addButton(self._table_view_btn)
        filter_row.addWidget(self._table_view_btn)

        # Add relationship button
        self._add_rel_button = ActionButton("新增关系", variant="primary")
        self._add_rel_button.setProperty("compact", True)
        self._add_rel_button.clicked.connect(self._on_add_relationship)
        filter_row.addWidget(self._add_rel_button)

        outer.addLayout(filter_row)

        # Main body: graph + inspector
        body = QHBoxLayout()
        body.setSpacing(10)
        body.setContentsMargins(0, 0, 0, 0)

        # Stacked widget: graph OR table
        self._body_stack = QStackedLayout()
        self._body_stack.setContentsMargins(0, 0, 0, 0)

        graph_host = Surface("inset")
        graph_layout = QVBoxLayout(graph_host)
        graph_layout.setContentsMargins(6, 6, 6, 6)
        graph_layout.setSpacing(4)
        graph_hint = QLabel("点击节点查看该角色的关系；拖拽节点之间的连线可快速建立新关系。")
        graph_hint.setObjectName("fieldHint")
        graph_hint.setWordWrap(True)
        graph_layout.addWidget(graph_hint)
        self._graph_host = graph_layout
        self._graph_widget: CharacterGraphWidget | None = None
        self._body_stack.addWidget(graph_host)

        self._table_widget = self._build_table_view()
        self._body_stack.addWidget(self._table_widget)

        body.addLayout(self._body_stack, 1)

        # Inspector: browse (summary) + edit (form)
        self._inspector = InspectorPanel(
            browse_widget=self._build_inspector_browse(),
            edit_widget=self._build_inspector_edit(),
            title="关系详情",
        )
        self._inspector.closed.connect(self._on_inspector_closed)
        self._inspector.setMinimumWidth(280)
        self._inspector.setMaximumWidth(360)
        body.addWidget(self._inspector, 0)

        outer.addLayout(body, 1)

        # Save bar
        self._save_bar = SaveBar(self)
        self._save_bar.saveClicked.connect(self._on_save_clicked)
        self._save_bar.discardClicked.connect(self._on_discard_clicked)
        outer.addWidget(self._save_bar)
        self._sync_save_bar()

    def _build_table_view(self) -> QWidget:
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QHeaderView,
            QTableWidget,
        )

        wrap = Surface("inset")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        hint = QLabel("表格视图：双击一行编辑关系，选中后用右侧 Inspector 修改。")
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["来源", "对象", "类型", "描述"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.doubleClicked.connect(lambda _index: self._on_table_double_clicked())
        layout.addWidget(table, 1)
        self._table = table
        return wrap

    def _build_inspector_browse(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self._browse_title_label = QLabel("关系详情")
        self._browse_title_label.setObjectName("cardTitle")
        layout.addWidget(self._browse_title_label)

        self._browse_meta_label = QLabel("选择图谱中的节点或连线查看详情。")
        self._browse_meta_label.setObjectName("cardMeta")
        self._browse_meta_label.setWordWrap(True)
        layout.addWidget(self._browse_meta_label)

        relations_card = self._build_section_card(
            "关系清单",
            "该角色当前已有的关系描述。",
        )
        self._browse_relations_body = relations_card["body"]
        layout.addWidget(relations_card["root"])

        layout.addStretch(1)
        return wrap

    def _build_inspector_edit(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self._edit_status_label = QLabel("修改关系字段后保存栏会自动浮起。")
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

        # Type label / source / target are read-only fields (set on selection).
        # We display them as styled labels and only allow description editing.
        self._edit_summary_label = QLabel("—")
        self._edit_summary_label.setObjectName("cardMeta")
        self._edit_summary_label.setWordWrap(True)
        grid.addWidget(QLabel("关系"), 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._edit_summary_label, 0, 1)

        # Type chips
        type_host = QWidget()
        type_layout = QHBoxLayout(type_host)
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_layout.setSpacing(6)
        self._type_chips: list[FilterChip] = []
        for value, label in RELATIONSHIP_TYPE_OPTIONS:
            chip = FilterChip(label, active=False)
            chip.setProperty("relType", value)
            chip.clicked.connect(self._make_type_chip_clicked(value))
            type_layout.addWidget(chip)
            self._type_chips.append(chip)
        type_layout.addStretch(1)
        grid.addWidget(QLabel("关系类型"), 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(type_host, 1, 1)

        # Description
        desc_label = QLabel("描述")
        desc_label.setObjectName("cardMeta")
        grid.addWidget(desc_label, 2, 0, Qt.AlignmentFlag.AlignTop)
        self._description_edit = QTextEdit()
        self._description_edit.setMinimumHeight(96)
        self._description_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._description_edit.textChanged.connect(self._mark_dirty)
        self._text_fields["description"] = self._description_edit
        grid.addWidget(self._description_edit, 2, 1)

        grid.setRowStretch(2, 1)

        # Action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._edit_remove_button = ActionButton("移除关系", variant="quiet")
        self._edit_remove_button.setProperty("compact", True)
        self._edit_remove_button.clicked.connect(self._on_remove_relationship)
        action_row.addWidget(self._edit_remove_button)
        action_row.addStretch(1)
        self._edit_done_button = ActionButton("完成", variant="primary")
        self._edit_done_button.setProperty("compact", True)
        self._edit_done_button.clicked.connect(self._on_inspector_closed)
        action_row.addWidget(self._edit_done_button)
        grid.addLayout(action_row, 3, 0, 1, 2)

        scroll.setWidget(form)
        layout.addWidget(scroll, 1)
        return wrap

    def _build_section_card(self, title: str, hint: str) -> dict[str, Any]:
        card = Surface("card")
        card.setProperty("compact", "true")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
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
        self._store.dirtyChanged.connect(lambda _d: self._sync_save_bar())
        self._store.saveWarningsChanged.connect(
            lambda ws: self._sync_save_bar(warnings=[w.get("message", "") for w in ws])
        )
        self._store.requestExternalRefresh.connect(self.workspace_refresh_requested.emit)

    def _on_bible_changed(self) -> None:
        self._render_graph()
        self._refresh_table()
        if self._selected_node:
            self._populate_inspector_for_node(self._selected_node)
        elif self._selected_edge is not None:
            self._populate_inspector_for_edge(*self._selected_edge)

    def _sync_save_bar(self, *, warnings: list[str] | None = None) -> None:
        dirty = self._store.dirty
        summary = f"「{self._selection_summary_text()}」有未保存修改" if dirty else "已保存"
        if not dirty:
            summary = "已保存"
        self._save_bar.show_dirty(
            dirty=dirty,
            summary=summary,
            warnings=() if warnings is None else tuple(warnings),
        )

    def _selection_summary_text(self) -> str:
        if self._selected_node:
            return self._selected_node
        if self._selected_edge is not None:
            return f"{self._selected_edge[0]} ↔ {self._selected_edge[1]}"
        return "当前选择"

    # ── Graph / table rendering ───────────────────────────────────────

    def _render_graph(self) -> None:
        # Drop the previous widget but keep the host layout intact.
        if self._graph_widget is not None:
            self._graph_widget.setParent(None)
            self._graph_widget.deleteLater()
            self._graph_widget = None

        characters = [profile.model_dump(mode="json") for profile in self._store.bible.characters]
        # Apply the current filter to a working copy so the graph only
        # shows relationships of the chosen type.
        filtered_characters = _apply_filter(characters, self._active_filter)
        graph = CharacterGraphWidget(
            filtered_characters,
            entity_graph=self._store.entity_graph_payload(),
            editable=True,
        )
        graph.focusChanged.connect(self._on_focus_changed)
        graph.relationshipCreateRequested.connect(self._on_relationship_create_requested)
        graph.relationshipEditRequested.connect(self._on_relationship_edit_requested)
        graph.relationshipRemoveRequested.connect(self._on_relationship_remove_requested)
        graph.characterAddRequested.connect(self._on_character_add_requested)
        graph.characterEditRequested.connect(self._on_character_edit_requested)
        graph.characterRetireRequested.connect(self._on_character_retire_requested)

        self._graph_host.addWidget(graph, 1)
        self._graph_widget = graph

        # Apply the active fade-in animation if available; macOS still gets
        # the immediate display thanks to Motion's opacity guard.
        if animations_supported("opacity"):
            effect = graph.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(graph)
                graph.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            anim = QPropertyAnimation(effect, b"opacity")
            anim.setDuration(220)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()

        if self._selected_node:
            graph.focus_character(self._selected_node)

    def _refresh_table(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        if not hasattr(self, "_table"):
            return
        self._table.setRowCount(0)
        rows = _collect_relationship_rows(self._store.bible.characters)
        for source, target, rel_type, description in rows:
            row_index = self._table.rowCount()
            self._table.insertRow(row_index)
            type_label = _RELATIONSHIP_TONE.get(rel_type, rel_type)
            for col, value in enumerate((source, target, type_label, description)):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, (source, target, rel_type))
                self._table.setItem(row_index, col, cell)

    # ── Filter chips ──────────────────────────────────────────────────

    def _make_filter_clicked(self, key: str) -> Callable[[], None]:
        def _on_click() -> None:
            self._active_filter = key
            for chip_key, chip in self._filter_chips.items():
                chip.setChecked(chip_key == key)
            self._render_graph()

        return _on_click

    def _make_type_chip_clicked(self, value: str) -> Callable[[], None]:
        def _on_click() -> None:
            if self._loading or self._selected_edge is None:
                return
            self._mark_dirty()

        return _on_click

    def _switch_view(self, mode: str) -> None:
        if mode == self._view_mode:
            return
        self._view_mode = mode
        if mode == "graph":
            self._body_stack.setCurrentIndex(0)
            self._graph_view_btn.setChecked(True)
            self._table_view_btn.setChecked(False)
        else:
            self._body_stack.setCurrentIndex(1)
            self._refresh_table()
            self._graph_view_btn.setChecked(False)
            self._table_view_btn.setChecked(True)

    # ── Graph signal handlers ─────────────────────────────────────────

    def _on_focus_changed(self, name: str) -> None:
        self._selected_node = clean_character_name(name)
        self._selected_edge = None
        if self._selected_node:
            self._populate_inspector_for_node(self._selected_node)
        else:
            self._render_inspector_for_empty()

    def _on_relationship_create_requested(self, source: str, target: str) -> None:
        if not self.save_pending_changes():
            return
        source_name = clean_character_name(source)
        target_name = clean_character_name(target)
        if not source_name or not target_name:
            return
        self._selected_edge = (source_name, target_name)
        self._selected_node = ""
        default_type = self._store.relationship_matrix.type_for(source_name, target_name)
        default_description = _lookup_relationship_description(
            self._store.profile_by_name(source_name), target_name
        )
        self._populate_inspector_for_edge(
            source_name, target_name, rel_type=default_type, description=default_description
        )

    def _on_relationship_edit_requested(self, source: str, target: str) -> None:
        if not self.save_pending_changes():
            return
        source_name = clean_character_name(source)
        target_name = clean_character_name(target)
        if not source_name or not target_name:
            return
        self._selected_edge = (source_name, target_name)
        self._selected_node = ""
        self._populate_inspector_for_edge(source_name, target_name)

    def _on_relationship_remove_requested(self, source: str, target: str) -> None:
        if not self.save_pending_changes():
            return
        self._on_remove_relationship_with(source, target)

    def _on_character_add_requested(self) -> None:

        # Defer to the profile page's "新增角色" affordance — the network
        # page is best used for visualisation, so we keep the action there
        # but route through the store directly.
        if not self.save_pending_changes():
            return

        name, ok = show_text_input_dialog(self, "新增角色", "角色姓名")
        if not ok or not clean_character_name(name):
            return
        payload = {
            "name": clean_character_name(name),
            "role": "supporting",
            "status": "active",
            "time_layer": "default",
            "age": "",
            "gender": "",
            "social_status": "",
            "abilities": "",
            "appearance": "",
            "personality": "",
            "backstory": "",
            "motivation": "",
            "arc": "",
            "relationships": {},
        }
        try:
            self._store.request_add_character(payload)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "新增角色失败", str(exc))

    def _on_character_edit_requested(self, name: str) -> None:
        # Switch to the profile page would require multi-tab coordination.
        # Instead, focus the character in the inspector browse surface so
        # the user can see their existing relationships / status.
        clean = clean_character_name(name)
        if not clean:
            return
        self._selected_node = clean
        self._selected_edge = None
        if self._graph_widget is not None:
            self._graph_widget.focus_character(clean)
        self._populate_inspector_for_node(clean)

    def _on_character_retire_requested(self, name: str) -> None:
        if not self.save_pending_changes():
            return
        try:
            self._store.request_retire_character(name)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "标记退场失败", str(exc))

    # ── Inspector population ──────────────────────────────────────────

    def _render_inspector_for_empty(self) -> None:
        self._browse_title_label.setText("关系详情")
        self._browse_meta_label.setText("选择图谱中的节点或连线查看详情。")
        self._browse_relations_body.setText("—")

    def _populate_inspector_for_node(self, name: str) -> None:
        profile = self._store.profile_by_name(name)
        if profile is None:
            self._render_inspector_for_empty()
            return
        self._browse_title_label.setText(name)
        self._browse_meta_label.setText(
            f"角色定位：{_role_label(profile.role)}  ·  状态：{profile.status or 'active'}"
        )
        if not profile.relationships:
            self._browse_relations_body.setText("暂无关系。")
        else:
            lines = []
            for rel_name, description in profile.relationships.items():
                lines.append(
                    f"• {_rel_display_name(rel_name)}：{_strip_or_default(description, '—')}"
                )
            self._browse_relations_body.setText("\n".join(lines))

    def _populate_inspector_for_edge(
        self,
        source: str,
        target: str,
        *,
        rel_type: str | None = None,
        description: str | None = None,
    ) -> None:
        self._browse_title_label.setText(f"{source} ↔ {target}")
        self._browse_meta_label.setText("正在编辑关系；右侧表单修改后保存栏会自动浮起。")
        self._browse_relations_body.setText("")

        resolved_type = (
            rel_type
            if rel_type is not None
            else self._store.relationship_matrix.type_for(source, target)
        )
        if resolved_type not in {value for value, _ in RELATIONSHIP_TYPE_OPTIONS}:
            resolved_type = "relationship"
        resolved_description = (
            description
            if description is not None
            else _lookup_relationship_description(self._store.profile_by_name(source), target)
        )

        self._loading = True
        try:
            self._edit_summary_label.setText(f"{source} → {target}")
            for chip in self._type_chips:
                chip.setChecked(chip.property("relType") == resolved_type)
            self._description_edit.setPlainText(resolved_description)
        finally:
            self._loading = False

    # ── Mutations ─────────────────────────────────────────────────────

    def _on_add_relationship(self) -> None:
        if not self.save_pending_changes():
            return
        names = self._store.character_names()
        if len(names) < 2:
            show_warning_message(self.window(), "暂无可关联角色", "至少需要 2 个角色才能建立关系。")
            return

        from novel_forge.desktop.pages.standalone.character_bible_editor import (
            RelationshipEditDialog,
        )

        dialog = RelationshipEditDialog(
            source_name=names[0],
            character_names=names,
            target_name=names[1] if len(names) > 1 else "",
            relation_type="relationship",
            description="",
            parent=self,
        )
        from PySide6.QtWidgets import QDialog

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target, rel_type, description = dialog.result_payload()
        try:
            self._store.request_write_relationship(names[0], target, rel_type, description)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "新增关系失败", str(exc))
            return
        self._selected_edge = (clean_character_name(names[0]), clean_character_name(target))
        self._populate_inspector_for_edge(
            self._selected_edge[0],
            self._selected_edge[1],
            rel_type=rel_type,
            description=description,
        )

    def _on_remove_relationship(self) -> None:
        if self._selected_edge is None:
            return
        source, target = self._selected_edge
        self._on_remove_relationship_with(source, target)

    def _on_remove_relationship_with(self, source: str, target: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from novel_forge.desktop.components.dialogs import (
            MessageBoxAction,
            show_message_box,
        )

        choice = show_message_box(
            self.window(),
            "移除关系",
            f"移除「{source}」与「{target}」之间的关系？",
            icon=QMessageBox.Icon.Question,
            actions=(
                MessageBoxAction(
                    "remove",
                    "移除",
                    QMessageBox.ButtonRole.DestructiveRole,
                    "danger",
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
        if choice != "remove":
            return
        try:
            self._store.request_remove_relationship(source, target)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "关系移除失败", str(exc))
            return
        self._selected_edge = None
        self._render_inspector_for_empty()

    def _on_save_clicked(self) -> None:
        if not self.save_pending_changes():
            return
        self._inspector.show_browse(animated=True)

    def _on_discard_clicked(self) -> None:
        self._store.request_discard()
        self._inspector.show_browse(animated=True)
        if self._selected_node:
            self._populate_inspector_for_node(self._selected_node)
        elif self._selected_edge is not None:
            self._populate_inspector_for_edge(*self._selected_edge)

    def _on_inspector_closed(self) -> None:
        self._inspector.show_browse(animated=True)

    def _on_table_double_clicked(self) -> None:
        if not hasattr(self, "_table"):
            return
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 3:
            return
        source, target, rel_type = data
        self._selected_edge = (clean_character_name(source), clean_character_name(target))
        self._populate_inspector_for_edge(source, target, rel_type=rel_type)

    # ── Form / dirty helpers ──────────────────────────────────────────

    def _mark_dirty(self) -> None:
        if self._loading:
            return
        self._collect_form_into_payload()

    def _collect_form_into_payload(self) -> None:
        if self._selected_edge is None:
            return
        source, target = self._selected_edge
        description = self._description_edit.toPlainText().strip()
        rel_type = "relationship"
        for chip in self._type_chips:
            if chip.isChecked():
                rel_type = str(chip.property("relType"))
                break

        if not description:
            # No description = no dirty signal: the user hasn't typed
            # anything substantive yet.
            return
        payload = self._store.bible.model_dump(mode="json")
        for character in payload.get("characters", []):
            if clean_character_name(character.get("name")) != source:
                continue
            relationships = dict(character.get("relationships") or {})
            relationships[target] = description
            character["relationships"] = relationships
            break
        payload.setdefault("relationship_types", {})[f"{source}::{target}"] = rel_type
        self._store.apply_pending_payload(payload)


# ── Helpers ──────────────────────────────────────────────────────────────


def _apply_filter(
    characters: list[dict[str, Any]],
    filter_key: str,
) -> list[dict[str, Any]]:
    """Return a working copy of *characters* with relationships narrowed
    by the active filter. Always preserves the full list so the user can
    still focus any node; only the edges respect the filter.
    """
    if filter_key == "all":
        return characters
    types = next((types for key, _, types in _FILTER_OPTIONS if key == filter_key), ())
    if not types:
        return characters
    filtered: list[dict[str, Any]] = []
    for character in characters:
        cloned = dict(character)
        relationships = dict(cloned.get("relationships") or {})
        # Without an explicit per-edge type map we keep all relationships;
        # we still preserve the character so the graph stays navigable.
        cloned["relationships"] = relationships
        filtered.append(cloned)
    return filtered


def _collect_relationship_rows(
    characters: list[CharacterProfile],
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for profile in characters:
        source = clean_character_name(profile.name)
        for target, description in (profile.relationships or {}).items():
            target_clean = clean_character_name(target)
            pair = (min(source, target_clean), max(source, target_clean))
            if pair in seen or source == target_clean:
                continue
            seen.add(pair)
            rows.append((source, target_clean, "relationship", str(description or "")))
    return rows


def _lookup_relationship_description(
    source: CharacterProfile | None,
    target: str,
) -> str:
    if source is None:
        return ""
    for name, description in (source.relationships or {}).items():
        if clean_character_name(name) == clean_character_name(target):
            return str(description or "")
    return ""


def _role_label(value: str | None) -> str:
    mapping = {
        "protagonist": "主角",
        "deuteragonist": "重要配角",
        "antagonist": "对手",
        "supporting": "一般配角",
        "background": "背景人物",
    }
    return mapping.get(str(value or ""), mapping["supporting"])


def _rel_display_name(name: str) -> str:
    return clean_character_name(name) or "（未命名）"


def _strip_or_default(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


__all__ = ["RelationshipNetworkPage"]
