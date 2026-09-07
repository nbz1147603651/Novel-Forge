from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.schemas import CharacterBible, CharacterProfile
from novel_forge.desktop.components.dialogs import MessageBoxAction, show_message_box
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.pages.document_renderers import CharacterGraphWidget
from novel_forge.desktop.pages.standalone.character_artifact_writer import (
    RELATIONSHIP_TYPE_OPTIONS,
    add_character,
    load_character_bible,
    remove_relationship_edge,
    retire_character,
    write_character_bible,
    write_relationship_edge,
)
from novel_forge.desktop.widgets import ActionButton, Surface, show_warning_message
from novel_forge.persistence.models import ProjectLayout

_ROLE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("protagonist", "主角"),
    ("deuteragonist", "重要配角"),
    ("antagonist", "对手"),
    ("supporting", "配角"),
    ("minor", "次要角色"),
)
_STATUS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("active", "活跃"),
    ("dormant", "暂离"),
    ("retired", "退场"),
)
_TIME_LAYER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("default", "默认"),
    ("modern", "当前线"),
    ("past", "过去线"),
    ("cross_temporal", "跨时间线"),
    ("memory_only", "回忆线"),
)
_TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("abilities", "能力 / 资源"),
    ("appearance", "外貌"),
    ("personality", "性格"),
    ("backstory", "背景"),
    ("arc", "角色弧光"),
    ("voice", "声纹"),
    ("notes", "备注"),
)


class CharacterProfileDialog(QDialog):
    """Minimal dialog for adding a character profile."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle("新增角色")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel("新增角色")
        title.setObjectName("dialogText")
        root.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        root.addLayout(form)

        self._name = QLineEdit()
        self._name.setPlaceholderText("角色姓名")
        form.addWidget(QLabel("姓名"), 0, 0)
        form.addWidget(self._name, 0, 1)

        self._role = _choice_combo(_ROLE_OPTIONS, "supporting")
        form.addWidget(QLabel("定位"), 1, 0)
        form.addWidget(self._role, 1, 1)

        self._gender = QLineEdit()
        form.addWidget(QLabel("性别"), 2, 0)
        form.addWidget(self._gender, 2, 1)

        self._age = QLineEdit()
        form.addWidget(QLabel("年龄"), 3, 0)
        form.addWidget(self._age, 3, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("actionButton")
        cancel.setProperty("variant", "secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = QPushButton("创建")
        confirm.setObjectName("actionButton")
        confirm.setProperty("variant", "primary")
        confirm.clicked.connect(self._accept_if_valid)
        buttons.addWidget(confirm)
        root.addLayout(buttons)

    def payload(self) -> dict[str, Any]:
        return {
            "name": self._name.text().strip(),
            "role": self._role.currentData() or "supporting",
            "gender": self._gender.text().strip(),
            "age": self._age.text().strip(),
            "status": "active",
            "time_layer": "default",
        }

    def _accept_if_valid(self) -> None:
        if not clean_character_name(self._name.text()):
            show_warning_message(self, "角色名不能为空", "请先填写角色姓名。")
            return
        self.accept()


class RelationshipEditDialog(QDialog):
    """Dialog for creating or updating one relationship edge."""

    def __init__(
        self,
        *,
        source_name: str,
        character_names: list[str],
        target_name: str = "",
        relation_type: str = "relationship",
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle("编辑关系")
        self.setMinimumSize(520, 360)
        self._source_name = clean_character_name(source_name)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel(f"{self._source_name} 的关系")
        title.setObjectName("dialogText")
        root.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        root.addLayout(form)

        self._target = QComboBox()
        self._target.setMinimumHeight(34)
        for name in character_names:
            clean_name = clean_character_name(name)
            if clean_name and clean_name != self._source_name:
                self._target.addItem(clean_name, clean_name)
        _set_combo_data(self._target, clean_character_name(target_name))
        form.addWidget(QLabel("对象"), 0, 0)
        form.addWidget(self._target, 0, 1)

        self._relation_type = _choice_combo(RELATIONSHIP_TYPE_OPTIONS, relation_type)
        form.addWidget(QLabel("类型"), 1, 0)
        form.addWidget(self._relation_type, 1, 1)

        self._description = QTextEdit()
        self._description.setPlaceholderText("关系描述，例如：彼此试探但在关键行动中互相掩护。")
        self._description.setPlainText(description)
        self._description.setMinimumHeight(120)
        form.addWidget(QLabel("描述"), 2, 0, Qt.AlignmentFlag.AlignTop)
        form.addWidget(self._description, 2, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("actionButton")
        cancel.setProperty("variant", "secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = QPushButton("保存")
        confirm.setObjectName("actionButton")
        confirm.setProperty("variant", "primary")
        confirm.clicked.connect(self._accept_if_valid)
        buttons.addWidget(confirm)
        root.addLayout(buttons)

    def result_payload(self) -> tuple[str, str, str]:
        return (
            str(self._target.currentData() or "").strip(),
            str(self._relation_type.currentData() or "relationship").strip(),
            " ".join(self._description.toPlainText().split()),
        )

    def _accept_if_valid(self) -> None:
        target, _relation_type, description = self.result_payload()
        if not target:
            show_warning_message(self, "关系对象不能为空", "请先选择一个目标角色。")
            return
        if target == self._source_name:
            show_warning_message(self, "关系对象无效", "不能创建角色自身关系。")
            return
        if not description:
            show_warning_message(self, "关系描述不能为空", "请补充这条关系的具体描述。")
            return
        self.accept()


class CharacterBibleEditor(QWidget):
    """Editable character and relationship surface for character_bible.json."""

    workspace_refresh_requested = Signal()

    def __init__(self, project_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir = Path(project_dir)
        self._layout = ProjectLayout(self._project_dir)
        self._bible = load_character_bible(self._project_dir)
        self._saved_payload = self._bible.model_dump(mode="json")
        self._current_name = ""
        self._loading = False
        self._dirty = False
        self._line_fields: dict[str, QLineEdit] = {}
        self._text_fields: dict[str, QTextEdit] = {}
        self._combo_fields: dict[str, QComboBox] = {}
        self._view_buttons: dict[str, ActionButton] = {}
        self._content_stack: QStackedWidget | None = None
        self._profile_stack: QStackedWidget | None = None
        self._profile_title: QLabel | None = None
        self._profile_meta: QLabel | None = None
        self._profile_identity_grid: QGridLayout | None = None
        self._profile_sections: dict[str, QLabel] = {}
        self._relationship_detail_title: QLabel | None = None
        self._relationship_detail_body: QLabel | None = None
        self._edit_button: ActionButton | None = None
        self._save_button: ActionButton | None = None
        self._discard_button: ActionButton | None = None
        self._graph_host: QVBoxLayout | None = None
        self._graph_scroll: QScrollArea | None = None
        self._graph_widget: CharacterGraphWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._status = QLabel("")
        self._status.setObjectName("fieldHint")
        self._status.setWordWrap(True)
        self._status.hide()
        root.addWidget(self._status)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_character_list_panel())
        splitter.addWidget(self._build_editor_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self._reload_character_list(select_name=self._first_character_name())
        self._sync_status()

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def shutdown(self) -> None:
        """Stop timers, disconnect signals, and tear down sub-widgets (I-5).

        Idempotent: safe to call multiple times.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        # Disconnect list widget signals
        char_list = getattr(self, "_character_list", None)
        if char_list is not None:
            safe_disconnect(char_list.currentItemChanged, self._on_character_changed)

        # Disconnect search/filter signals
        search_edit = getattr(self, "_character_search", None)
        if search_edit is not None:
            safe_disconnect(search_edit.textChanged)
        filter_combo = getattr(self, "_status_filter", None)
        if filter_combo is not None:
            safe_disconnect(filter_combo.currentIndexChanged)

        # Disconnect field signals (line edits, text edits, combos)
        for line in getattr(self, "_line_fields", {}).values():
            safe_disconnect(line.textChanged)
        for edit in getattr(self, "_text_fields", {}).values():
            safe_disconnect(edit.textChanged)
        for combo in getattr(self, "_combo_fields", {}).values():
            safe_disconnect(combo.currentIndexChanged)

        # Disconnect toolbar buttons
        for button in getattr(self, "_view_buttons", {}).values():
            safe_disconnect(button.clicked)
        for attr in ("_edit_button", "_save_button", "_discard_button"):
            btn = getattr(self, attr, None)
            if btn is not None:
                safe_disconnect(btn.clicked)

        # Disconnect relationship table signals
        rel_table = getattr(self, "_relationship_table", None)
        if rel_table is not None:
            safe_disconnect(rel_table.doubleClicked)
            safe_disconnect(rel_table.itemSelectionChanged)

        # Disconnect graph widget signals and drop the widget
        graph_widget = getattr(self, "_graph_widget", None)
        if graph_widget is not None:
            safe_disconnect(getattr(graph_widget, "focusChanged", None),
                            self._focus_character_from_graph)
            safe_disconnect(getattr(graph_widget, "relationshipCreateRequested", None))
            safe_disconnect(getattr(graph_widget, "relationshipEditRequested", None))
            safe_disconnect(getattr(graph_widget, "relationshipRemoveRequested", None),
                            self._remove_relationship)
            safe_disconnect(getattr(graph_widget, "characterAddRequested", None),
                            self._add_character)
            safe_disconnect(getattr(graph_widget, "characterEditRequested", None),
                            self._focus_character_from_graph)
            safe_disconnect(getattr(graph_widget, "characterRetireRequested", None),
                            self._retire_character_from_graph)
            try:
                graph_widget.setParent(None)
                graph_widget.deleteLater()
            except RuntimeError:
                pass
            self._graph_widget = None

        graph_scroll = getattr(self, "_graph_scroll", None)
        if graph_scroll is not None:
            try:
                graph_scroll.deleteLater()
            except RuntimeError:
                pass
            self._graph_scroll = None

    def has_active_edit_session(self) -> bool:
        return self._profile_stack is not None and self._profile_stack.currentIndex() == 1

    def save_pending_changes(self) -> bool:
        if not self._apply_form_to_current(show_errors=True):
            return False
        try:
            result = write_character_bible(self._project_dir, self._bible)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "角色设定保存失败", str(exc))
            return False
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._dirty = False
        self._reload_character_list(select_name=self._current_name)
        self._rebuild_graph()
        self._sync_status(warnings=result.warnings)
        self.workspace_refresh_requested.emit()
        return True

    def unsaved_changes_description(self) -> str:
        return "- 卷帙页（角色与关系）有未保存修改"

    def confirm_close(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        choice = show_message_box(
            self.window(),
            "角色设定尚未保存",
            "当前角色资料或关系描述还没有写入项目文件。",
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
            self._discard_changes()
            return True
        return False

    def _build_character_list_panel(self) -> QWidget:
        panel = Surface("inset")
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("角色")
        title.setObjectName("cardMeta")
        layout.addWidget(title)

        self._character_search = QLineEdit()
        self._character_search.setPlaceholderText("搜索角色")
        self._character_search.setMinimumHeight(32)
        layout.addWidget(self._character_search)

        self._status_filter = QComboBox()
        self._status_filter.setMinimumHeight(32)
        self._status_filter.addItem("全部状态", "all")
        self._status_filter.addItem("活跃", "active")
        self._status_filter.addItem("暂离", "dormant")
        self._status_filter.addItem("退场", "retired")
        layout.addWidget(self._status_filter)

        self._character_list = QListWidget()
        self._character_list.setObjectName("readerList")
        self._character_list.currentItemChanged.connect(self._on_character_changed)
        layout.addWidget(self._character_list, 1)

        self._character_search.textChanged.connect(lambda _text: self._refresh_character_filter())
        self._status_filter.currentIndexChanged.connect(lambda _index: self._refresh_character_filter())

        add_btn = ActionButton("新增角色", variant="secondary")
        add_btn.clicked.connect(self._add_character)
        layout.addWidget(add_btn)

        retire_btn = ActionButton("标记退场", variant="secondary")
        retire_btn.clicked.connect(self._retire_selected_character)
        layout.addWidget(retire_btn)

        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in (("profile", "档案"), ("relationships", "关系"), ("graph", "图谱")):
            button = ActionButton(label, variant="secondary")
            button.setCheckable(True)
            button.setProperty("compact", True)
            button.clicked.connect(lambda _checked=False, view_key=key: self._switch_view(view_key))
            group.addButton(button)
            self._view_buttons[key] = button
            toolbar.addWidget(button)
        self._view_buttons["profile"].setChecked(True)

        toolbar.addStretch(1)

        self._edit_button = ActionButton("编辑角色", variant="primary")
        self._edit_button.setProperty("compact", True)
        self._edit_button.clicked.connect(self._toggle_profile_edit)
        toolbar.addWidget(self._edit_button)

        self._discard_button = ActionButton("放弃修改", variant="quiet")
        self._discard_button.setProperty("compact", True)
        self._discard_button.clicked.connect(self._discard_changes)
        toolbar.addWidget(self._discard_button)

        self._save_button = ActionButton("保存修改", variant="primary")
        self._save_button.setProperty("compact", True)
        self._save_button.clicked.connect(self._save_from_toolbar)
        toolbar.addWidget(self._save_button)

        layout.addLayout(toolbar)

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._build_profile_page())
        self._content_stack.addWidget(self._build_relationship_panel())
        self._content_stack.addWidget(self._build_graph_panel())
        layout.addWidget(self._content_stack, 1)

        self._rebuild_graph()

        return panel

    def _build_profile_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._profile_stack = QStackedWidget()
        self._profile_stack.addWidget(self._build_profile_read_view())
        self._profile_stack.addWidget(self._build_profile_form())
        layout.addWidget(self._profile_stack, 1)
        return page

    def _build_profile_read_view(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("characterProfileReadScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("characterProfileReadContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(12)

        header = Surface("inset")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(6)
        self._profile_title = QLabel("选择角色")
        self._profile_title.setObjectName("cardTitleLarge")
        header_layout.addWidget(self._profile_title)
        self._profile_meta = QLabel("从左侧选择一个角色查看档案。")
        self._profile_meta.setObjectName("fieldHint")
        self._profile_meta.setWordWrap(True)
        header_layout.addWidget(self._profile_meta)
        self._profile_identity_grid = QGridLayout()
        self._profile_identity_grid.setHorizontalSpacing(10)
        self._profile_identity_grid.setVerticalSpacing(8)
        header_layout.addLayout(self._profile_identity_grid)
        layout.addWidget(header)

        for key, title in (
            ("arc", "角色弧光"),
            ("personality", "性格"),
            ("backstory", "背景"),
            ("abilities", "能力 / 资源"),
            ("appearance", "外貌"),
            ("voice", "声纹"),
            ("notes", "备注"),
        ):
            card, body = self._build_summary_card(title)
            self._profile_sections[key] = body
            layout.addWidget(card)

        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _build_summary_card(self, title: str) -> tuple[QWidget, QLabel]:
        card = Surface("inset")
        card.setProperty("compact", "true")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("cardMeta")
        layout.addWidget(title_label)
        body = QLabel("—")
        body.setObjectName("cardBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        return card, body

    def _build_profile_form(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("characterProfileFormScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("characterProfileFormContent")
        grid = QGridLayout(content)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self._line_fields = {
            "name": QLineEdit(),
            "age": QLineEdit(),
            "gender": QLineEdit(),
            "social_status": QLineEdit(),
        }
        self._combo_fields = {
            "role": _choice_combo(_ROLE_OPTIONS, "supporting"),
            "status": _choice_combo(_STATUS_OPTIONS, "active"),
            "time_layer": _choice_combo(_TIME_LAYER_OPTIONS, "default"),
        }

        row = 0
        for key, label in (
            ("name", "姓名"),
            ("role", "定位"),
            ("status", "状态"),
            ("time_layer", "时间线"),
            ("age", "年龄"),
            ("gender", "性别"),
            ("social_status", "身份 / 职业"),
        ):
            grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignTop)
            widget = self._line_fields.get(key) or self._combo_fields[key]
            widget.setMinimumHeight(32)
            grid.addWidget(widget, row, 1)
            row += 1

        for key, label in _TEXT_FIELDS:
            edit = QTextEdit()
            edit.setMinimumHeight(68)
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._text_fields[key] = edit
            grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(edit, row, 1)
            row += 1

        for field in self._line_fields.values():
            field.textChanged.connect(lambda _text: self._mark_dirty())
        for field in self._text_fields.values():
            field.textChanged.connect(self._mark_dirty)
        for field in self._combo_fields.values():
            field.currentIndexChanged.connect(lambda _index: self._mark_dirty())

        scroll.setWidget(content)
        return scroll

    def _build_relationship_panel(self) -> QWidget:
        panel = Surface("inset")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("关系")
        title.setObjectName("cardMeta")
        header.addWidget(title)
        header.addStretch()
        add_btn = ActionButton("新增关系", variant="secondary")
        add_btn.clicked.connect(self._add_relationship)
        header.addWidget(add_btn)
        edit_btn = ActionButton("编辑关系", variant="secondary")
        edit_btn.clicked.connect(self._edit_selected_relationship)
        header.addWidget(edit_btn)
        remove_btn = ActionButton("移除关系", variant="secondary")
        remove_btn.clicked.connect(self._remove_selected_relationship)
        header.addWidget(remove_btn)
        layout.addLayout(header)

        self._relationship_table = QTableWidget(0, 3)
        self._relationship_table.setHorizontalHeaderLabels(["对象", "类型", "描述"])
        self._relationship_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._relationship_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._relationship_table.verticalHeader().setVisible(False)
        self._relationship_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._relationship_table.horizontalHeader().resizeSection(0, 140)
        self._relationship_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._relationship_table.horizontalHeader().resizeSection(1, 110)
        self._relationship_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._relationship_table.doubleClicked.connect(lambda _index: self._edit_selected_relationship())
        self._relationship_table.itemSelectionChanged.connect(self._sync_relationship_detail)
        layout.addWidget(self._relationship_table, 1)

        detail = Surface("card")
        detail.setProperty("compact", "true")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(6)
        self._relationship_detail_title = QLabel("关系详情")
        self._relationship_detail_title.setObjectName("cardMeta")
        detail_layout.addWidget(self._relationship_detail_title)
        self._relationship_detail_body = QLabel("选择一条关系查看完整描述。")
        self._relationship_detail_body.setObjectName("cardBody")
        self._relationship_detail_body.setWordWrap(True)
        self._relationship_detail_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self._relationship_detail_body)
        layout.addWidget(detail)

        return panel

    def _build_graph_panel(self) -> QWidget:
        graph_panel = Surface("inset")
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(10, 10, 10, 10)
        graph_layout.setSpacing(8)

        header = QHBoxLayout()
        graph_title = QLabel("关系图谱")
        graph_title.setObjectName("cardMeta")
        header.addWidget(graph_title)
        header.addStretch(1)
        graph_layout.addLayout(header)

        self._graph_host = graph_layout
        return graph_panel

    def _switch_view(self, view_key: str) -> None:
        if self._content_stack is None:
            return
        if view_key != "profile" and not self._commit_profile_form(show_errors=True):
            self._view_buttons.get("profile", self._view_buttons[view_key]).setChecked(True)
            return
        index_by_key = {"profile": 0, "relationships": 1, "graph": 2}
        index = index_by_key.get(view_key, 0)
        self._content_stack.setCurrentIndex(index)
        for key, button in self._view_buttons.items():
            button.setChecked(key == view_key)
        if view_key == "relationships":
            self._populate_relationships(self._profile_by_name(self._current_name))
        elif view_key == "graph":
            self._rebuild_graph()
            self._focus_current_character_in_graph()

    def _toggle_profile_edit(self) -> None:
        if self._profile_stack is None:
            return
        if not self._current_name:
            show_warning_message(self.window(), "暂无可编辑角色", "请先新增或选择一个角色。")
            return
        if self._content_stack is not None:
            self._content_stack.setCurrentIndex(0)
            for key, button in self._view_buttons.items():
                button.setChecked(key == "profile")
        if self._profile_stack.currentIndex() == 0:
            self._profile_stack.setCurrentIndex(1)
            if self._edit_button is not None:
                self._edit_button.setText("完成编辑")
            return
        if not self._commit_profile_form(show_errors=True):
            return
        self._profile_stack.setCurrentIndex(0)
        if self._edit_button is not None:
            self._edit_button.setText("编辑角色")

    def _commit_profile_form(self, *, show_errors: bool) -> bool:
        if self._profile_stack is None or self._profile_stack.currentIndex() == 0:
            return True
        if not self._apply_form_to_current(show_errors=show_errors):
            return False
        self._reload_character_list(select_name=self._current_name)
        self._render_profile_summary(self._profile_by_name(self._current_name))
        self._populate_relationships(self._profile_by_name(self._current_name))
        return True

    def _save_from_toolbar(self) -> None:
        if self.save_pending_changes() and self._profile_stack is not None:
            self._profile_stack.setCurrentIndex(0)
            if self._edit_button is not None:
                self._edit_button.setText("编辑角色")

    def _discard_changes(self) -> None:
        if not self._dirty:
            return
        self._bible = CharacterBible.model_validate(self._saved_payload)
        self._dirty = False
        self._reload_character_list(select_name=self._current_name)
        self._rebuild_graph()
        if self._profile_stack is not None:
            self._profile_stack.setCurrentIndex(0)
        if self._edit_button is not None:
            self._edit_button.setText("编辑角色")
        self._sync_status()

    def _refresh_character_filter(self) -> None:
        if self._loading:
            return
        if not self._commit_profile_form(show_errors=False):
            return
        self._reload_character_list(select_name=self._current_name)

    def _character_matches_filter(self, profile: CharacterProfile) -> bool:
        query = ""
        if hasattr(self, "_character_search"):
            query = self._character_search.text().strip().lower()
        status_filter = "all"
        if hasattr(self, "_status_filter"):
            status_filter = str(self._status_filter.currentData() or "all")
        name = clean_character_name(profile.name)
        haystack = " ".join(
            str(value or "")
            for value in (
                name,
                profile.role,
                profile.status,
                profile.time_layer,
                profile.social_status,
                profile.gender,
            )
        ).lower()
        if query and query not in haystack:
            return False
        if status_filter != "all" and str(profile.status or "active") != status_filter:
            return False
        return True

    def _first_character_name(self) -> str:
        if not self._bible.characters:
            return ""
        return clean_character_name(self._bible.characters[0].name)

    def _reload_character_list(self, *, select_name: str = "") -> None:
        current = clean_character_name(select_name) or clean_character_name(self._current_name)
        self._loading = True
        self._character_list.clear()
        selected_row = -1
        for profile in self._bible.characters:
            if not self._character_matches_filter(profile):
                continue
            name = clean_character_name(profile.name)
            item = QListWidgetItem(_character_list_label(profile))
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._character_list.addItem(item)
            if name == current:
                selected_row = self._character_list.count() - 1
        if self._character_list.count():
            if selected_row < 0:
                selected_row = 0
            self._character_list.setCurrentRow(selected_row)
            item = self._character_list.currentItem()
            self._current_name = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
            self._load_profile(self._profile_by_name(self._current_name))
        else:
            self._current_name = ""
            self._load_profile(None)
        self._loading = False

    def _on_character_changed(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        if self._loading:
            return
        if previous is not None and not self._apply_form_to_current(show_errors=True):
            self._loading = True
            self._character_list.setCurrentItem(previous)
            self._loading = False
            return
        self._current_name = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        self._load_profile(self._profile_by_name(self._current_name))
        self._rebuild_graph()

    def _profile_by_name(self, name: str) -> CharacterProfile | None:
        target = clean_character_name(name)
        for profile in self._bible.characters:
            if clean_character_name(profile.name) == target:
                return profile
        return None

    def _load_profile(self, profile: CharacterProfile | None) -> None:
        self._loading = True
        if profile is None:
            for field in self._line_fields.values():
                field.clear()
            for field in self._text_fields.values():
                field.clear()
            for field in self._combo_fields.values():
                field.setCurrentIndex(0)
            self._relationship_table.setRowCount(0)
            self._render_profile_summary(None)
            self._sync_relationship_detail()
            self._loading = False
            return

        payload = profile.model_dump(mode="json")
        for key, field in self._line_fields.items():
            field.setText(str(payload.get(key) or ""))
        _set_combo_data(self._combo_fields["role"], str(payload.get("role") or "supporting"))
        _set_combo_data(self._combo_fields["status"], str(payload.get("status") or "active"))
        _set_combo_data(self._combo_fields["time_layer"], str(payload.get("time_layer") or "default"))
        for key, field in self._text_fields.items():
            field.setPlainText(str(payload.get(key) or ""))
        self._loading = False
        self._render_profile_summary(profile)
        self._populate_relationships(profile)

    def _populate_relationships(self, profile: CharacterProfile | None) -> None:
        self._relationship_table.setRowCount(0)
        if profile is None:
            self._sync_relationship_detail()
            return
        matrix = self._relationship_matrix_lookup()
        rows = sorted((profile.relationships or {}).items(), key=lambda item: clean_character_name(item[0]))
        self._relationship_table.setRowCount(len(rows))
        source = clean_character_name(profile.name)
        for row, (target, description) in enumerate(rows):
            clean_target = clean_character_name(target)
            rel_type = matrix.get(frozenset((source, clean_target)), "relationship")
            self._relationship_table.setItem(row, 0, QTableWidgetItem(clean_target))
            self._relationship_table.setItem(row, 1, QTableWidgetItem(_relationship_type_label(rel_type)))
            self._relationship_table.setItem(row, 2, QTableWidgetItem(str(description or "")))
        if rows:
            self._relationship_table.setCurrentCell(0, 0)
        self._sync_relationship_detail()

    def _render_profile_summary(self, profile: CharacterProfile | None) -> None:
        if self._profile_title is None or self._profile_meta is None:
            return
        if profile is None:
            self._profile_title.setText("暂无角色")
            self._profile_meta.setText("当前筛选下没有角色，或项目尚未生成角色设定。")
            if self._profile_identity_grid is not None:
                _clear_layout(self._profile_identity_grid)
            for body in self._profile_sections.values():
                body.setText("—")
            return

        name = clean_character_name(profile.name) or "未命名角色"
        self._profile_title.setText(name)
        relationship_count = len(profile.relationships or {})
        self._profile_meta.setText(
            f"{_role_label(profile.role)} · {_status_label(profile.status)} · "
            f"{_time_layer_label(profile.time_layer)} · {relationship_count} 条关系"
        )

        if self._profile_identity_grid is not None:
            _clear_layout(self._profile_identity_grid)
            for col, (label, value) in enumerate(
                (
                    ("年龄", profile.age),
                    ("性别", profile.gender),
                    ("身份 / 职业", profile.social_status),
                    ("状态", _status_label(profile.status)),
                )
            ):
                self._profile_identity_grid.addWidget(
                    self._build_identity_cell(label, _strip_or_default(value, "—")),
                    0,
                    col,
                )

        values = profile.model_dump(mode="json")
        for key, body in self._profile_sections.items():
            body.setText(_strip_or_default(values.get(key), "暂无内容。"))

    def _build_identity_cell(self, label: str, value: str) -> QWidget:
        cell = Surface("card")
        cell.setProperty("compact", "true")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        label_widget = QLabel(label)
        label_widget.setObjectName("fieldHint")
        layout.addWidget(label_widget)
        value_widget = QLabel(value)
        value_widget.setObjectName("cardBody")
        value_widget.setWordWrap(True)
        layout.addWidget(value_widget)
        return cell

    def _sync_relationship_detail(self) -> None:
        if self._relationship_detail_title is None or self._relationship_detail_body is None:
            return
        source, target, description = self._selected_relationship()
        if not source or not target:
            self._relationship_detail_title.setText("关系详情")
            self._relationship_detail_body.setText("选择一条关系查看完整描述。")
            return
        rel_type = self._relationship_matrix_lookup().get(frozenset((source, target)), "relationship")
        self._relationship_detail_title.setText(
            f"{source} ↔ {target} · {_relationship_type_label(rel_type)}"
        )
        self._relationship_detail_body.setText(_strip_or_default(description, "暂无描述。"))

    def _apply_form_to_current(self, *, show_errors: bool) -> bool:
        if not self._current_name:
            return True
        profile = self._profile_by_name(self._current_name)
        if profile is None:
            return True
        payload = profile.model_dump(mode="json")
        for key, field in self._line_fields.items():
            payload[key] = field.text().strip()
        for key, field in self._combo_fields.items():
            payload[key] = str(field.currentData() or "").strip()
        for key, field in self._text_fields.items():
            payload[key] = field.toPlainText().strip()
        try:
            updated = CharacterProfile.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            if show_errors:
                show_warning_message(self.window(), "角色资料无效", str(exc))
            return False
        characters: list[CharacterProfile] = []
        old_name = clean_character_name(profile.name)
        new_name = clean_character_name(updated.name)
        if not new_name:
            if show_errors:
                show_warning_message(self.window(), "角色名不能为空", "请先填写角色姓名。")
            return False
        for item in self._bible.characters:
            if clean_character_name(item.name) == old_name:
                characters.append(updated)
            else:
                if clean_character_name(item.name) == new_name:
                    if show_errors:
                        show_warning_message(
                            self.window(),
                            "角色名重复",
                            f"已经存在名为「{new_name}」的角色。",
                        )
                    return False
                characters.append(item)
        if old_name != new_name:
            characters = [_rename_relationship_refs(item, old_name, new_name) for item in characters]
            self._current_name = new_name
        self._bible = CharacterBible(characters=characters)
        return True

    def _mark_dirty(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._sync_status()

    def _sync_status(self, *, warnings: tuple[str, ...] = ()) -> None:
        status_parts: list[str] = []
        if self._dirty:
            status_parts.append("有未保存修改。")
        status_parts.extend(warning for warning in warnings if warning)
        status_text = " ".join(status_parts).strip()
        self._status.setText(status_text)
        self._status.setVisible(bool(status_text))
        if self._save_button is not None:
            self._save_button.setEnabled(self._dirty)
        if self._discard_button is not None:
            self._discard_button.setEnabled(self._dirty)

    def _character_names(self) -> list[str]:
        return [clean_character_name(profile.name) for profile in self._bible.characters]

    def _add_character(self) -> None:
        if not self.save_pending_changes():
            return
        dialog = CharacterProfileDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = add_character(self._project_dir, dialog.payload())
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "新增角色失败", str(exc))
            return
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._dirty = False
        new_name = clean_character_name(dialog.payload().get("name"))
        self._reload_character_list(select_name=new_name)
        self._rebuild_graph()
        self._sync_status(warnings=result.warnings)
        self.workspace_refresh_requested.emit()

    def _retire_selected_character(self) -> None:
        current = self._current_name
        if not current:
            return
        if not self.save_pending_changes():
            return
        choice = show_message_box(
            self.window(),
            "标记角色退场",
            f"将「{current}」标记为 retired，不会删除历史引用。",
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
            result = retire_character(self._project_dir, current)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "标记退场失败", str(exc))
            return
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._dirty = False
        self._reload_character_list(select_name=current)
        self._rebuild_graph()
        self._sync_status(warnings=result.warnings)
        self.workspace_refresh_requested.emit()

    def _add_relationship(self) -> None:
        self._open_relationship_dialog(self._current_name)

    def _edit_selected_relationship(self) -> None:
        source, target, description = self._selected_relationship()
        if not source or not target:
            return
        rel_type = self._relationship_matrix_lookup().get(frozenset((source, target)), "relationship")
        self._open_relationship_dialog(
            source,
            target_name=target,
            relation_type=rel_type,
            description=description,
        )

    def _remove_selected_relationship(self) -> None:
        source, target, _description = self._selected_relationship()
        if not source or not target:
            return
        self._remove_relationship(source, target)

    def _selected_relationship(self) -> tuple[str, str, str]:
        row = self._relationship_table.currentRow()
        if row < 0:
            return "", "", ""
        target_item = self._relationship_table.item(row, 0)
        desc_item = self._relationship_table.item(row, 2)
        return (
            clean_character_name(self._current_name),
            clean_character_name(target_item.text() if target_item else ""),
            str(desc_item.text() if desc_item else ""),
        )

    def _open_relationship_dialog(
        self,
        source_name: str,
        *,
        target_name: str = "",
        relation_type: str = "relationship",
        description: str = "",
    ) -> None:
        source = clean_character_name(source_name)
        if not source:
            return
        if not self.save_pending_changes():
            return
        dialog = RelationshipEditDialog(
            source_name=source,
            character_names=self._character_names(),
            target_name=target_name,
            relation_type=relation_type,
            description=description,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target, selected_type, selected_description = dialog.result_payload()
        self._save_relationship(source, target, selected_type, selected_description)

    def _save_relationship(
        self,
        source_name: str,
        target_name: str,
        relation_type: str,
        description: str,
    ) -> None:
        try:
            result = write_relationship_edge(
                self._project_dir,
                source_name,
                target_name,
                relation_type,
                description,
            )
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "关系保存失败", str(exc))
            return
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._dirty = False
        self._reload_character_list(select_name=source_name)
        self._rebuild_graph()
        self._sync_status(warnings=result.warnings)
        self.workspace_refresh_requested.emit()

    def _remove_relationship(self, source_name: str, target_name: str) -> None:
        choice = show_message_box(
            self.window(),
            "移除关系",
            f"移除「{source_name}」与「{target_name}」之间的关系？",
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
            result = remove_relationship_edge(self._project_dir, source_name, target_name)
        except Exception as exc:  # noqa: BLE001
            show_warning_message(self.window(), "关系移除失败", str(exc))
            return
        self._bible = result.character_bible
        self._saved_payload = self._bible.model_dump(mode="json")
        self._dirty = False
        self._reload_character_list(select_name=source_name)
        self._rebuild_graph()
        self._sync_status(warnings=result.warnings)
        self.workspace_refresh_requested.emit()

    def _rebuild_graph(self) -> None:
        if self._graph_host is None:
            return
        if self._graph_scroll is not None:
            self._graph_scroll.deleteLater()
            self._graph_scroll = None
            self._graph_widget = None
        entity_graph = self._load_entity_graph_payload()
        graph = CharacterGraphWidget(
            [profile.model_dump(mode="json") for profile in self._bible.characters],
            entity_graph=entity_graph,
            editable=True,
        )
        graph.focusChanged.connect(self._focus_character_from_graph)
        graph.relationshipCreateRequested.connect(
            lambda source, target: self._open_relationship_dialog(
                source,
                target_name=target,
                description=self._relationship_description(source, target),
            )
        )
        graph.relationshipEditRequested.connect(
            lambda source, target: self._open_relationship_dialog(
                source,
                target_name=target,
                relation_type=self._relationship_matrix_lookup().get(
                    frozenset((source, target)),
                    "relationship",
                ),
                description=self._relationship_description(source, target),
            )
        )
        graph.relationshipRemoveRequested.connect(self._remove_relationship)
        graph.characterAddRequested.connect(self._add_character)
        graph.characterEditRequested.connect(self._focus_character_from_graph)
        graph.characterRetireRequested.connect(self._retire_character_from_graph)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(320)
        scroll.setWidget(graph)
        self._graph_host.addWidget(scroll, 1)
        self._graph_scroll = scroll
        self._graph_widget = graph

    def _focus_character_from_graph(self, name: str) -> None:
        if "→" in name:
            name = name.split("→", 1)[0]
        target = clean_character_name(name)
        if not target:
            return
        for row in range(self._character_list.count()):
            item = self._character_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == target:
                self._character_list.setCurrentRow(row)
                return

    def _retire_character_from_graph(self, name: str) -> None:
        self._focus_character_from_graph(name)
        self._retire_selected_character()

    def _relationship_description(self, source_name: str, target_name: str) -> str:
        source = self._profile_by_name(source_name)
        if source is None:
            return ""
        target = clean_character_name(target_name)
        for name, description in (source.relationships or {}).items():
            if clean_character_name(name) == target:
                return str(description or "")
        return ""

    def _relationship_matrix_lookup(self) -> dict[frozenset[str], str]:
        path = self._layout.states_dir / "init_v2" / "character_relationship_matrix.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        matrix = payload.get("relationship_matrix")
        if not isinstance(matrix, list):
            return {}
        lookup: dict[frozenset[str], str] = {}
        for item in matrix:
            if not isinstance(item, dict):
                continue
            left = clean_character_name(
                item.get("character_a") or item.get("source_name") or item.get("source")
            )
            right = clean_character_name(
                item.get("character_b") or item.get("target_name") or item.get("target")
            )
            rel_type = str(item.get("relation_type") or "relationship").strip()
            if left and right:
                lookup[frozenset((left, right))] = rel_type
        return lookup

    def _load_entity_graph_payload(self) -> dict[str, Any]:
        path = self._layout.narrative_state_dir / "entity_graph.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _focus_current_character_in_graph(self) -> None:
        if self._graph_widget is None or not self._current_name:
            return
        self._graph_widget.focus_character(self._current_name)


def _choice_combo(options: tuple[tuple[str, str], ...], current_value: str) -> QComboBox:
    combo = QComboBox()
    combo.setMinimumHeight(34)
    for value, label in options:
        combo.addItem(label, value)
    _set_combo_data(combo, current_value)
    return combo


def _set_combo_data(combo: QComboBox, value: str) -> None:
    for index in range(combo.count()):
        if str(combo.itemData(index) or "") == str(value or ""):
            combo.setCurrentIndex(index)
            return
    if combo.count():
        combo.setCurrentIndex(0)


def _relationship_type_label(relation_type: str) -> str:
    labels = dict(RELATIONSHIP_TYPE_OPTIONS)
    return labels.get(relation_type, labels["relationship"])


def _role_label(role: str | None) -> str:
    labels = dict(_ROLE_OPTIONS)
    return labels.get(str(role or ""), labels["supporting"])


def _status_label(status: str | None) -> str:
    labels = dict(_STATUS_OPTIONS)
    return labels.get(str(status or ""), labels["active"])


def _time_layer_label(time_layer: str | None) -> str:
    labels = dict(_TIME_LAYER_OPTIONS)
    return labels.get(str(time_layer or ""), labels["default"])


def _strip_or_default(value: object, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _character_list_label(profile: CharacterProfile) -> str:
    status = str(profile.status or "active")
    suffix = " · 退场" if status == "retired" else f" · {_role_label(profile.role)}"
    return f"{clean_character_name(profile.name)}{suffix}"


def _clear_layout(layout: QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()


def _rename_relationship_refs(
    profile: CharacterProfile,
    old_name: str,
    new_name: str,
) -> CharacterProfile:
    relationships = {
        (new_name if clean_character_name(name) == old_name else clean_character_name(name)): value
        for name, value in (profile.relationships or {}).items()
    }
    return profile.model_copy(update={"relationships": relationships})
