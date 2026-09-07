"""Humanize library dashboard page.

Lists all entries in the humanize pattern library (builtin + user + imported),
supports filtering, search, and CRUD actions (edit / disable / delete / merge /
view examples). Top toolbar exposes bulk operations (new / import / export /
find duplicates). A red error banner surfaces library degraded state.

The page is intentionally a ``QWidget`` (not ``ScrollPage``) so it can be
embedded inside the 火候 (Settings) page as a tab OR hosted as a child window
via a button. The card on the 火候 page uses ``_HumanizeLibraryCard`` (a
``QFrame``) to surface live stats and launch into the dashboard.

Per ``.omo/plans/humanize-library.md`` Task 13, the dashboard is a pure CRUD
facade on top of ``HumanizeLibrary`` — no LLM calls inside the UI thread.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.config import Settings
from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.desktop.widgets import (
    ActionButton,
    SectionHeading,
    ask_confirmation,
    show_info_message,
    show_warning_message,
)
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    LibraryError,
    LibraryReadOnlyError,
)
from novel_forge.obs.logger import get_logger

_logger = get_logger("desktop.humanize_library_dashboard")

# ---------------------------------------------------------------------------
# Table column contract — single source of truth for the dashboard
# ---------------------------------------------------------------------------

TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pattern_id", "ID"),
    ("pattern_name", "名称"),
    ("source", "来源"),
    ("category", "分类"),
    ("severity", "严重度"),
    ("hit_count", "命中数"),
    ("last_hit_chapter", "最近章节"),
    ("enabled", "启用"),
)
# Severity order used for sort + filter display
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# Filter / sort sentinel for "all"
ANY_VALUE: str = "全部"


# ---------------------------------------------------------------------------
# Table models
#
# The legacy widget-based table allocated one widget per row, which makes
# 1000+ rows extremely slow. The QTableView + QAbstractTableModel pattern
# below only materializes the rows the viewport can see — the visible-rows-
# only property is what makes this refactor necessary. See plan Task 11
# (I-4, P1).
# ---------------------------------------------------------------------------


class _HumanizeLibraryModel(QAbstractTableModel):
    """Read-only table model backing the dashboard ``QTableView``.

    Columns mirror ``TABLE_COLUMNS``: ``(pattern_id, pattern_name, source,
    category, severity, hit_count, last_hit_chapter, enabled)``. Cell content
    is computed on demand from the underlying ``HumanizeLibraryEntry`` list.

    The model intentionally supports a minimal role set:

    * ``Qt.ItemDataRole.DisplayRole`` — the rendered string
    * ``Qt.ItemDataRole.TextAlignmentRole`` — column-aware alignment
    * ``Qt.ItemDataRole.FontRole`` — strike-out for disabled rows, regular
      weight for builtin rows
    * ``Qt.ItemDataRole.UserRole`` — opaque row id (used by context menu)

    Header sorting reorders the model's current filtered entries in-place. The
    page must read row data back through ``entry_at()`` because model order may
    differ from ``HumanizeLibraryDashboardPage._filtered`` after a header click.
    """

    def __init__(
        self,
        entries: list[HumanizeLibraryEntry] | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[HumanizeLibraryEntry] = list(entries or [])

    # -- public API ----------------------------------------------------------

    def set_entries(self, entries: list[HumanizeLibraryEntry]) -> None:
        """Replace the row data and notify attached views."""
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> HumanizeLibraryEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def sort(  # type: ignore[override]
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        if not (0 <= column < len(TABLE_COLUMNS)):
            return
        self.layoutAboutToBeChanged.emit()
        self._entries.sort(
            key=lambda entry: _entry_sort_key(entry, column),
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()

    # -- QAbstractTableModel contract ---------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            # Tree-model convention: only root has rows.
            return 0
        return len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(TABLE_COLUMNS)

    def headerData(  # type: ignore[override]
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(TABLE_COLUMNS):
                return TABLE_COLUMNS[section][1]
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._entries)):
            return None
        if not (0 <= col < len(TABLE_COLUMNS)):
            return None
        entry = self._entries[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return _entry_cell_text(entry, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            # Center-align numeric / icon columns
            if col in (5, 6, 7):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if role == Qt.ItemDataRole.FontRole:
            from PySide6.QtGui import QFont

            font = QFont()
            if col == 2 and entry.source == "builtin":
                # Dim builtin labels lightly to keep user / imported visually distinct
                font.setBold(False)
            if not entry.enabled:
                font.setStrikeOut(True)
            return font
        if role == Qt.ItemDataRole.UserRole:
            return entry.pattern_id
        return None


class _DuplicatesModel(QAbstractTableModel):
    """Tiny read-only model for the ``_DuplicatesDialog`` 5-column table."""

    HEADERS: tuple[str, ...] = ("ID A", "名称 A", "ID B", "名称 B", "相似度")

    def __init__(
        self,
        duplicates: list[tuple[HumanizeLibraryEntry, HumanizeLibraryEntry, float]] | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._duplicates: list[
            tuple[HumanizeLibraryEntry, HumanizeLibraryEntry, float]
        ] = list(duplicates or [])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._duplicates)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(  # type: ignore[override]
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._duplicates)):
            return None
        if not (0 <= col < len(self.HEADERS)):
            return None
        entry_a, entry_b, score = self._duplicates[row]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return entry_a.pattern_id
            if col == 1:
                return entry_a.pattern_name
            if col == 2:
                return entry_b.pattern_id
            if col == 3:
                return entry_b.pattern_name
            if col == 4:
                return f"{score:.2f}"
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 4:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.UserRole:
            return (entry_a.pattern_id, entry_b.pattern_id)
        return None


def _entry_cell_text(entry: HumanizeLibraryEntry, col: int) -> str:
    """Render the display string for a single (entry, column) cell.

    Kept as a module-level helper so the model + tests can share the
    formatting rules (centered dash for missing values, ✓/✗ for enabled, etc.).
    """
    if col == 0:
        return entry.pattern_id
    if col == 1:
        return entry.pattern_name
    if col == 2:
        return entry.source
    if col == 3:
        return entry.category or "—"
    if col == 4:
        return entry.severity
    if col == 5:
        return str(entry.hit_count)
    if col == 6:
        return str(entry.last_hit_chapter) if entry.last_hit_chapter is not None else "—"
    if col == 7:
        return "✓" if entry.enabled else "✗"
    return ""


def _entry_sort_key(entry: HumanizeLibraryEntry, col: int) -> tuple[int, str] | str | int | bool:
    if col == 0:
        return entry.pattern_id
    if col == 1:
        return entry.pattern_name
    if col == 2:
        return entry.source
    if col == 3:
        return entry.category or ""
    if col == 4:
        return (SEVERITY_RANK.get(entry.severity, 99), entry.severity)
    if col == 5:
        return entry.hit_count
    if col == 6:
        return entry.last_hit_chapter or 0
    if col == 7:
        return entry.enabled
    return ""


# ---------------------------------------------------------------------------
# Entry edit dialog (lightweight; Task 12 owns the full candidate picker)
# ---------------------------------------------------------------------------


class _EntryEditDialog(QDialog):
    """Inline create / edit dialog for a single library entry.

    For a full candidate picker that supports drag-selection + chip merge,
    use ``AddToHumanizeLibraryDialog`` from Task 12. This dialog covers the
    "+ New" and "[Edit]" actions on the dashboard where the user already has
    a pattern in hand and just needs to fill in the metadata.
    """

    def __init__(
        self,
        entry: HumanizeLibraryEntry | None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._original_id: str | None = None
        self.setObjectName("humanizeEditDialog")
        self.setWindowTitle("编辑模式" if entry is not None else "新建模式")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("模式元数据")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 4, 0, 4)
        form.setSpacing(8)

        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("lib_user_xxxxxxxx")
        self._id_edit.setObjectName("entryIdEdit")
        form.addRow("ID", self._id_edit)

        self._name_edit = QLineEdit()
        self._name_edit.setObjectName("entryNameEdit")
        form.addRow("名称", self._name_edit)

        self._category_edit = QLineEdit()
        self._category_edit.setObjectName("entryCategoryEdit")
        form.addRow("分类", self._category_edit)

        self._severity_combo = QComboBox()
        self._severity_combo.addItems(["critical", "high", "medium", "low"])
        self._severity_combo.setObjectName("entrySeverityCombo")
        form.addRow("严重度", self._severity_combo)

        self._keywords_edit = QLineEdit()
        self._keywords_edit.setPlaceholderText("逗号分隔,例如:滥情,堆砌,口水")
        self._keywords_edit.setObjectName("entryKeywordsEdit")
        form.addRow("关键词", self._keywords_edit)

        self._notes_edit = QLineEdit()
        self._notes_edit.setObjectName("entryNotesEdit")
        form.addRow("备注", self._notes_edit)

        self._example_edit = QLineEdit()
        self._example_edit.setPlaceholderText("一句话举例")
        self._example_edit.setObjectName("entryExampleEdit")
        form.addRow("示例短语", self._example_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons
        layout.addWidget(buttons)

        if entry is not None:
            self._original_id = entry.pattern_id
            self._id_edit.setText(entry.pattern_id)
            self._id_edit.setReadOnly(True)
            self._name_edit.setText(entry.pattern_name)
            self._category_edit.setText(entry.category)
            self._severity_combo.setCurrentText(entry.severity)
            self._keywords_edit.setText(",".join(entry.keywords))
            self._notes_edit.setText(entry.notes)
            self._example_edit.setText(
                entry.example_phrases[0] if entry.example_phrases else ""
            )

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            show_warning_message(self, "缺少名称", "请填写模式名称。")
            return
        if self._original_id is None:
            proposed = self._id_edit.text().strip()
            if not proposed:
                # Auto-generate a user ID — visible to the user before commit
                proposed = f"lib_user_{secrets.token_hex(4)}"
                self._id_edit.setText(proposed)
            elif not proposed.startswith(("lib_user_", "lib_imported_")):
                # Reject non-prefixed user-defined IDs
                show_warning_message(
                    self,
                    "ID 不合法",
                    "用户模式 ID 必须以 lib_user_ 或 lib_imported_ 开头。",
                )
                return
        self.accept()

    def build_entry(self, base: HumanizeLibraryEntry | None) -> HumanizeLibraryEntry:
        """Construct a HumanizeLibraryEntry from the current form state."""
        pattern_id = self._original_id or self._id_edit.text().strip()
        keywords = [
            token.strip() for token in self._keywords_edit.text().split(",") if token.strip()
        ]
        example_text = self._example_edit.text().strip()
        example_phrases = [example_text] if example_text else []
        severity_value = self._severity_combo.currentText()

        if base is not None:
            return base.model_copy(
                update={
                    "pattern_name": self._name_edit.text().strip(),
                    "category": self._category_edit.text().strip(),
                    "severity": severity_value,
                    "keywords": keywords,
                    "notes": self._notes_edit.text().strip(),
                    "example_phrases": example_phrases,
                }
            )

        # Fresh entry — defaults match HumanizeLibraryEntry defaults
        return HumanizeLibraryEntry(
            pattern_id=pattern_id,
            pattern_name=self._name_edit.text().strip(),
            category=self._category_edit.text().strip(),
            severity=severity_value,
            example_phrases=example_phrases,
            keywords=keywords,
            source="user",
            notes=self._notes_edit.text().strip(),
            detection_method="regex",
            enabled=True,
        )


# ---------------------------------------------------------------------------
# Duplicates dialog
# ---------------------------------------------------------------------------


class _DuplicatesDialog(QDialog):
    """Read-only viewer for ``HumanizeLibrary.find_duplicates`` results."""

    def __init__(
        self,
        duplicates: list[tuple[HumanizeLibraryEntry, HumanizeLibraryEntry, float]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeEditDialog")
        self.setWindowTitle("重复模式对")
        self.setMinimumSize(720, 480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"共 {len(duplicates)} 对高相似模式")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        if not duplicates:
            empty = QLabel("未发现相似对 — 库当前无重复。")
            empty.setObjectName("dialogHint")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        else:
            hint = QLabel("下方为 BM25 相似度最高的若干对；可在主面板右键合并到任一目标。")
            hint.setObjectName("dialogHint")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            self._table = QTableView()
            self._table.setObjectName("humanizeLibraryTable")
            self._duplicates_model = _DuplicatesModel(duplicates, parent=self)
            self._table.setModel(self._duplicates_model)
            # Pin row height via the vertical header so the view avoids
            # measuring each row separately. (QTableView lacks
            # ``setUniformRowHeights`` — that helper is on ``QTreeView``.)
            self._table.verticalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Fixed
            )
            self._table.verticalHeader().setDefaultSectionSize(24)
            self._table.verticalHeader().setVisible(False)
            self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# Hit examples dialog
# ---------------------------------------------------------------------------


class _HitExamplesDialog(QDialog):
    """Show the most recent hit examples for a single entry."""

    def __init__(
        self,
        entry: HumanizeLibraryEntry,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeEditDialog")
        self.setWindowTitle(f"命中样例 — {entry.pattern_name}")
        self.setMinimumSize(560, 360)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel(
            f"{entry.pattern_id} · 累计命中 {entry.hit_count} 次"
            + (
                f" · 最近章节 {entry.last_hit_chapter}"
                if entry.last_hit_chapter is not None
                else ""
            )
        )
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        meta_rows: list[tuple[str, str]] = [
            ("分类", entry.category or "—"),
            ("严重度", entry.severity),
            (
                "最近命中时间",
                entry.last_seen_at.isoformat() if entry.last_seen_at else "—",
            ),
        ]
        for label_text, value_text in meta_rows:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)
            label_widget = QLabel(f"{label_text}：")
            label_widget.setObjectName("dialogFieldLabel")
            value_widget = QLabel(value_text)
            value_widget.setObjectName("dialogFieldValue")
            value_widget.setWordWrap(True)
            row_layout.addWidget(label_widget)
            row_layout.addWidget(value_widget, 1)
            layout.addLayout(row_layout)

        if entry.example_phrases:
            layout.addWidget(self._section_label("内置示例短语"))
            for phrase in entry.example_phrases:
                phrase_label = QLabel(f"· {phrase}")
                phrase_label.setObjectName("dialogExample")
                phrase_label.setWordWrap(True)
                layout.addWidget(phrase_label)
        else:
            empty = QLabel("暂无示例短语。")
            empty.setObjectName("dialogHint")
            layout.addWidget(empty)

        if entry.notes:
            layout.addWidget(self._section_label("备注"))
            notes_label = QLabel(entry.notes)
            notes_label.setObjectName("dialogNotes")
            notes_label.setWordWrap(True)
            layout.addWidget(notes_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("dialogSectionLabel")
        return label


# ---------------------------------------------------------------------------
# Merge target picker
# ---------------------------------------------------------------------------


class _MergeTargetDialog(QDialog):
    """Pick a target entry to merge another entry into."""

    def __init__(
        self,
        source: HumanizeLibraryEntry,
        candidates: list[HumanizeLibraryEntry],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeEditDialog")
        self.setWindowTitle("合并到…")
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"选择「{source.pattern_name}」要合并到的目标条目")
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self._combo = QComboBox()
        self._combo.setObjectName("mergeTargetCombo")
        for entry in candidates:
            if entry.pattern_id == source.pattern_id:
                continue
            label = f"{entry.pattern_id} · {entry.pattern_name}"
            self._combo.addItem(label, entry.pattern_id)
        layout.addWidget(self._combo)

        self._hit_total = QSpinBox()
        self._hit_total.setRange(0, 1_000_000)
        self._hit_total.setValue(source.hit_count)
        self._hit_total.setObjectName("mergeHitTotal")
        layout.addWidget(QLabel("合并后保留的命中数(用于目标条目):"))
        layout.addWidget(self._hit_total)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("合并")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def target_id(self) -> str:
        data = self._combo.currentData()
        return str(data) if data is not None else ""

    @property
    def merged_hit_count(self) -> int:
        return int(self._hit_total.value())


# ---------------------------------------------------------------------------
# Stats bar (footer)
# ---------------------------------------------------------------------------


class _StatsBar(QFrame):
    """Compact stat strip rendered at the bottom of the dashboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeStatsBar")
        self.setProperty("tone", "inset")
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)

        self._fields: dict[str, QLabel] = {}
        for key, default in (
            ("total", "0"),
            ("enabled", "0"),
            ("user", "0"),
            ("imported", "0"),
            ("stale", "0"),
            ("last_rebuild", "—"),
        ):
            layout.addWidget(self._make_chip(key, default))

        layout.addStretch()

    def _make_chip(self, key: str, default: str) -> QWidget:
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)
        title = QLabel(self._title_for(key))
        title.setObjectName("statsBarLabel")
        value = QLabel(default)
        value.setObjectName("statsBarValue")
        wrapper_layout.addWidget(title)
        wrapper_layout.addWidget(value)
        self._fields[key] = value
        return wrapper

    @staticmethod
    def _title_for(key: str) -> str:
        return {
            "total": "总数",
            "enabled": "启用",
            "user": "用户",
            "imported": "导入",
            "stale": "过期向量",
            "last_rebuild": "最近重建",
        }.get(key, key)

    def update_stats(
        self,
        *,
        total: int,
        enabled: int,
        user: int,
        imported: int,
        stale: int,
        last_rebuild: str | None,
    ) -> None:
        self._fields["total"].setText(str(total))
        self._fields["enabled"].setText(str(enabled))
        self._fields["user"].setText(str(user))
        self._fields["imported"].setText(str(imported))
        self._fields["stale"].setText(str(stale))
        self._fields["last_rebuild"].setText(last_rebuild or "—")


# ---------------------------------------------------------------------------
# Main dashboard page
# ---------------------------------------------------------------------------


class HumanizeLibraryDashboardPage(QWidget):
    """List / filter / search / CRUD dashboard for the humanize library.

    The page is a top-level ``QWidget`` rather than a ``ScrollPage`` so it can
    be embedded as a tab inside the fire page (火候) or hosted standalone as a
    child window from a launcher card.

    Signals:
        stats_refreshed: emitted whenever the library stats are recomputed,
            useful for the fire-page card to subscribe to live changes.
    """

    stats_refreshed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeLibraryDashboard")

        self._library: HumanizeLibrary | None = None
        self._entries: list[HumanizeLibraryEntry] = []
        self._filtered: list[HumanizeLibraryEntry] = []
        self._context_menu: QMenu | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._reload_from_library)

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_library(self, library: HumanizeLibrary | None) -> None:
        """Inject a library instance (e.g. from a test fixture).

        When *None* is passed, ``refresh()`` will lazily open the default
        library from ``Settings().humanize_library_resolved_path`` so the
        page can be used standalone.
        """
        self._library = library
        self.refresh()

    def refresh(self) -> None:
        """Reload all entries from the library.

        Library I/O runs synchronously on the calling thread. The default
        library is small (≤ a few hundred rows), so this is safe for the UI
        thread; if the dataset grows we can hoist this onto a QThreadPool
        worker without changing the public API.
        """
        self._refresh_timer.start()

    def shutdown(self) -> None:
        """Disconnect signals and stop timers per the desktop AGENTS.md."""
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        try:
            self._refresh_timer.timeout.disconnect(self._reload_from_library)
        except (RuntimeError, TypeError):
            pass
        # Clear menu references so a context-menu lifetime does not outlive
        # the page; PySide6 keeps these on the heap otherwise.
        self._context_menu = None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top header — heading + description
        header = QWidget()
        header.setObjectName("humanizeDashboardHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 6)
        header_layout.setSpacing(4)
        heading = SectionHeading(
            "拟人化库",
            "管理内置与用户加入的 AI 写作痕迹模式:编辑、禁用、删除、合并、查重。",
        )
        header_layout.addWidget(heading)
        outer.addWidget(header)

        # Error banner — hidden by default; shown when the library is unhealthy
        self._error_banner = QFrame()
        self._error_banner.setObjectName("humanizeErrorBanner")
        self._error_banner.setProperty("tone", "danger")
        self._error_banner.setVisible(False)
        banner_layout = QHBoxLayout(self._error_banner)
        banner_layout.setContentsMargins(16, 8, 16, 8)
        banner_layout.setSpacing(8)
        self._error_label = QLabel()
        self._error_label.setObjectName("errorBannerText")
        self._error_label.setWordWrap(True)
        banner_layout.addWidget(self._error_label, 1)
        outer.addWidget(self._error_banner)

        # Filter row + top action row
        controls = QWidget()
        controls.setObjectName("humanizeDashboardControls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(20, 4, 20, 4)
        controls_layout.setSpacing(8)

        controls_layout.addLayout(self._build_filter_row())
        controls_layout.addLayout(self._build_action_row())
        outer.addWidget(controls)

        # Table — takes all remaining vertical space
        #
        # QTableView + QAbstractTableModel renders only visible rows. For 1000+
        # entries the old per-row widget allocation was the dominant page-load cost.
        self._table = QTableView()
        self._table.setObjectName("humanizeLibraryTable")
        self._model = _HumanizeLibraryModel(parent=self)
        self._table.setModel(self._model)
        # Pin row height via the vertical header so the view avoids
        # measuring each row separately. (QTableView lacks
        # ``setUniformRowHeights`` — that helper is on ``QTreeView``.)
        self._table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_item_double_clicked)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self._table, 1)

        # Stats bar — pinned at the bottom
        self._stats_bar = _StatsBar()
        outer.addWidget(self._stats_bar)

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        source_label = QLabel("来源")
        source_label.setObjectName("filterLabel")
        self._source_combo = QComboBox()
        self._source_combo.setObjectName("filterSourceCombo")
        self._source_combo.addItem(ANY_VALUE)
        self._source_combo.addItems(["builtin", "user", "imported"])
        self._source_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(source_label)
        row.addWidget(self._source_combo)

        category_label = QLabel("分类")
        category_label.setObjectName("filterLabel")
        self._category_combo = QComboBox()
        self._category_combo.setObjectName("filterCategoryCombo")
        self._category_combo.addItem(ANY_VALUE)
        self._category_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(category_label)
        row.addWidget(self._category_combo)

        severity_label = QLabel("严重度")
        severity_label.setObjectName("filterLabel")
        self._severity_combo = QComboBox()
        self._severity_combo.setObjectName("filterSeverityCombo")
        self._severity_combo.addItem(ANY_VALUE)
        self._severity_combo.addItems(["critical", "high", "medium", "low"])
        self._severity_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(severity_label)
        row.addWidget(self._severity_combo)

        row.addSpacing(8)

        search_label = QLabel("搜索")
        search_label.setObjectName("filterLabel")
        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("filterSearchEdit")
        self._search_edit.setPlaceholderText("按名称/关键词/备注模糊匹配")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        row.addWidget(search_label)
        row.addWidget(self._search_edit, 1)

        return row

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        add_btn = ActionButton("+ 新建", variant="primary")
        add_btn.setProperty("role", "add")
        add_btn.setProperty("density", "compact")
        add_btn.clicked.connect(self._on_add_clicked)
        row.addWidget(add_btn)

        import_btn = ActionButton("导入…", variant="secondary")
        import_btn.setProperty("role", "import")
        import_btn.setProperty("density", "compact")
        import_btn.clicked.connect(self._on_import_clicked)
        row.addWidget(import_btn)

        export_btn = ActionButton("导出…", variant="secondary")
        export_btn.setProperty("role", "export")
        export_btn.setProperty("density", "compact")
        export_btn.clicked.connect(self._on_export_clicked)
        row.addWidget(export_btn)

        find_dup_btn = ActionButton("🔍 找重复", variant="secondary")
        find_dup_btn.setProperty("role", "findDuplicates")
        find_dup_btn.setProperty("density", "compact")
        find_dup_btn.clicked.connect(self._on_find_duplicates_clicked)
        row.addWidget(find_dup_btn)

        row.addStretch()
        return row

    # ------------------------------------------------------------------
    # Library access
    # ------------------------------------------------------------------

    def _open_library(self) -> HumanizeLibrary | None:
        if self._library is not None:
            return self._library
        try:
            settings = Settings()
            path = settings.humanize_library_resolved_path
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("settings_unavailable | error=%s", exc)
            self._show_error(f"无法读取配置:{exc}")
            return None

        if not path.exists():
            self._show_error(f"库路径不存在:{path}")
            return None

        try:
            self._library = HumanizeLibrary.from_path(path / "library.db")
        except LibraryError as exc:
            self._show_error(f"库打开失败:{exc}")
            return None
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("library_open_failed | error=%s", exc)
            self._show_error(f"库打开失败:{exc}")
            return None
        return self._library

    def _show_error(self, message: str) -> None:
        self._error_banner.setVisible(True)
        self._error_label.setText(f"Library degraded:{message}")

    def _clear_error(self) -> None:
        if self._error_banner.isVisible():
            self._error_banner.setVisible(False)
            self._error_label.clear()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _reload_from_library(self) -> None:
        library = self._open_library()
        if library is None:
            return

        # Health check
        try:
            healthy = library.is_healthy()
        except Exception as exc:  # pragma: no cover - defensive
            self._show_error(f"健康检查失败:{exc}")
            return

        if not healthy:
            self._show_error("is_healthy() 返回 False — 库降级 (degraded_xxx)。")
        else:
            self._clear_error()

        try:
            entries = library.list_all()
        except Exception as exc:
            self._show_error(f"list_all 失败:{exc}")
            return

        self._entries = list(entries)
        self._refresh_category_options()
        self._apply_filter()
        self._refresh_stats_bar(library)
        self.stats_refreshed.emit(self._compute_stats_dict(library))

    def _refresh_category_options(self) -> None:
        categories = sorted({entry.category for entry in self._entries if entry.category})
        current = self._category_combo.currentText()
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        self._category_combo.addItem(ANY_VALUE)
        self._category_combo.addItems(categories)
        if current in [self._category_combo.itemText(i) for i in range(self._category_combo.count())]:
            self._category_combo.setCurrentText(current)
        self._category_combo.blockSignals(False)

    def _apply_filter(self) -> None:
        source_filter = self._source_combo.currentText() if hasattr(self, "_source_combo") else ANY_VALUE
        category_filter = (
            self._category_combo.currentText() if hasattr(self, "_category_combo") else ANY_VALUE
        )
        severity_filter = (
            self._severity_combo.currentText() if hasattr(self, "_severity_combo") else ANY_VALUE
        )
        search_text = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""

        filtered: list[HumanizeLibraryEntry] = []
        for entry in self._entries:
            if source_filter != ANY_VALUE and entry.source != source_filter:
                continue
            if category_filter != ANY_VALUE and entry.category != category_filter:
                continue
            if severity_filter != ANY_VALUE and entry.severity != severity_filter:
                continue
            if search_text:
                haystack = " ".join(
                    [
                        entry.pattern_name,
                        entry.pattern_id,
                        entry.category,
                        " ".join(entry.keywords),
                        entry.notes,
                    ]
                ).lower()
                if search_text not in haystack:
                    continue
            filtered.append(entry)

        filtered.sort(
            key=lambda e: (
                SEVERITY_RANK.get(e.severity, 99),
                e.source != "builtin",
                e.pattern_id,
            )
        )
        self._filtered = filtered
        self._populate_table()

    def _populate_table(self) -> None:
        # The model owns visible-row order after header sorting. Resetting the
        # model here restores the default severity/source/id order after a
        # filter or library refresh.
        self._table.setSortingEnabled(False)
        self._model.set_entries(self._filtered)
        self._table.setSortingEnabled(True)

    def _refresh_stats_bar(self, library: HumanizeLibrary) -> None:
        try:
            stats = library.stats()
        except Exception as exc:
            _logger.warning("library_stats_failed | error=%s", exc)
            return
        # Determine "last rebuild" — proxied by last_seen_at from the entry table
        last_rebuild = stats.last_updated_at
        self._stats_bar.update_stats(
            total=stats.total,
            enabled=stats.enabled,
            user=stats.user_count,
            imported=stats.imported_count,
            stale=stats.stale_vector_count,
            last_rebuild=last_rebuild,
        )

    def _compute_stats_dict(self, library: HumanizeLibrary) -> dict[str, Any]:
        try:
            stats = library.stats()
        except Exception:
            return {}
        return {
            "total": stats.total,
            "enabled": stats.enabled,
            "user": stats.user_count,
            "imported": stats.imported_count,
            "stale": stats.stale_vector_count,
            "last_updated_at": stats.last_updated_at,
            "healthy": library.is_healthy() if hasattr(library, "is_healthy") else True,
        }

    # ------------------------------------------------------------------
    # Filter / search slots
    # ------------------------------------------------------------------

    def _on_search_changed(self, _text: str) -> None:
        self._apply_filter()

    def _on_filter_changed(self) -> None:
        self._apply_filter()

    # ------------------------------------------------------------------
    # Top action slots
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        dialog = _EntryEditDialog(None, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dialog.build_entry(None)
        library = self._open_library()
        if library is None:
            return
        try:
            library.add(entry)
        except LibraryError as exc:
            show_warning_message(self, "新建失败", str(exc))
            return
        self.refresh()

    def _on_import_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择拟人化库归档", "", "拟人化库归档 (*.tar.gz);;所有文件 (*)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            show_warning_message(self, "读取失败", f"无法读取归档文件:{exc}")
            return
        library = self._open_library()
        if library is None:
            return
        try:
            report = library.import_archive(data, merge_strategy="skip")
        except LibraryError as exc:
            show_warning_message(self, "导入失败", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            show_warning_message(self, "导入失败", str(exc))
            return
        show_info_message(
            self,
            "导入完成",
            f"新增 {report.imported} 条,跳过 {report.skipped} 条,覆盖 {report.overwritten} 条。",
        )
        self.refresh()

    def _on_export_clicked(self) -> None:
        library = self._open_library()
        if library is None:
            return
        try:
            blob = library.export()
        except LibraryError as exc:
            show_warning_message(self, "导出失败", str(exc))
            return
        default_name = (
            f"humanize_library_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.tar.gz"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "导出拟人化库", default_name, "拟人化库归档 (*.tar.gz);;所有文件 (*)"
        )
        if not path:
            return
        try:
            Path(path).write_bytes(blob)
        except OSError as exc:
            show_warning_message(self, "写入失败", f"无法写入文件:{exc}")
            return
        show_info_message(self, "导出成功", f"已导出到\n{path}")
        _logger.info("humanize_library_exported | path=%s | bytes=%d", path, len(blob))

    def _on_find_duplicates_clicked(self) -> None:
        library = self._open_library()
        if library is None:
            return
        try:
            duplicates = library.find_duplicates()
        except LibraryError as exc:
            show_warning_message(self, "查重失败", str(exc))
            return
        dialog = _DuplicatesDialog(duplicates, parent=self)
        dialog.exec()

    # ------------------------------------------------------------------
    # Row-level actions
    # ------------------------------------------------------------------

    def _selected_pattern_ids(self) -> list[str]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        ids: list[str] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is not None:
                ids.append(entry.pattern_id)
        return ids

    def _on_item_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        entry = self._model.entry_at(index.row())
        if entry is not None:
            self._on_view_hits(entry.pattern_id)

    def _on_context_menu(self, position: QPoint) -> None:
        pattern_ids = self._selected_pattern_ids()
        if not pattern_ids:
            return
        # Build context menu lazily to ensure the right-click feels native.
        self._context_menu = QMenu(self)
        view_action = QAction("查看命中样例", self._context_menu)
        view_action.triggered.connect(lambda: self._on_view_hits(pattern_ids[0]))
        self._context_menu.addAction(view_action)

        edit_action = QAction("编辑", self._context_menu)
        edit_action.triggered.connect(lambda: self._on_edit_clicked(pattern_ids[0]))
        self._context_menu.addAction(edit_action)

        # Determine enable / disable state from the first selected row
        first = self._open_entry(pattern_ids[0])
        if first is not None:
            if first.enabled:
                disable_action = QAction("禁用", self._context_menu)
                disable_action.triggered.connect(
                    lambda: self._on_disable_clicked(pattern_ids[0])
                )
                self._context_menu.addAction(disable_action)
            else:
                enable_action = QAction("启用", self._context_menu)
                enable_action.triggered.connect(
                    lambda: self._on_enable_clicked(pattern_ids[0])
                )
                self._context_menu.addAction(enable_action)

        if first is not None and first.source != "builtin":
            delete_action = QAction("删除", self._context_menu)
            delete_action.triggered.connect(
                lambda: self._on_delete_clicked(pattern_ids[0])
            )
            self._context_menu.addAction(delete_action)
            merge_action = QAction("合并到…", self._context_menu)
            merge_action.triggered.connect(
                lambda: self._on_merge_clicked(pattern_ids[0])
            )
            self._context_menu.addAction(merge_action)

        self._context_menu.exec(self._table.viewport().mapToGlobal(position))

    def _open_entry(self, pattern_id: str) -> HumanizeLibraryEntry | None:
        library = self._open_library()
        if library is None:
            return None
        try:
            return library.get(pattern_id)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("library_get_failed | id=%s | error=%s", pattern_id, exc)
            return None

    def _on_edit_clicked(self, pattern_id: str) -> None:
        existing = self._open_entry(pattern_id)
        if existing is None:
            show_warning_message(self, "编辑失败", f"未找到条目:{pattern_id}")
            return
        dialog = _EntryEditDialog(existing, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.build_entry(existing)
        library = self._open_library()
        if library is None:
            return
        try:
            library.update(pattern_id, **updated.model_dump(exclude={"pattern_id"}))
        except LibraryReadOnlyError as exc:
            show_warning_message(self, "编辑失败", str(exc))
            return
        except LibraryError as exc:
            show_warning_message(self, "编辑失败", str(exc))
            return
        self.refresh()

    def _on_disable_clicked(self, pattern_id: str) -> None:
        library = self._open_library()
        if library is None:
            return
        try:
            library.disable(pattern_id)
        except LibraryError as exc:
            show_warning_message(self, "禁用失败", str(exc))
            return
        self.refresh()

    def _on_enable_clicked(self, pattern_id: str) -> None:
        library = self._open_library()
        if library is None:
            return
        try:
            library.enable(pattern_id)
        except LibraryError as exc:
            show_warning_message(self, "启用失败", str(exc))
            return
        self.refresh()

    def _on_delete_clicked(self, pattern_id: str) -> None:
        library = self._open_library()
        if library is None:
            return
        existing = library.get(pattern_id)
        if existing is None:
            return
        if existing.source == "builtin":
            show_warning_message(self, "无法删除", "内置条目为只读,无法删除。")
            return
        if not ask_confirmation(
            self,
            "确认删除",
            f"将永久删除「{existing.pattern_name}」({pattern_id}),且不可恢复。",
            confirm_text="删除",
            confirm_variant="danger",
        ):
            return
        try:
            library.remove(pattern_id)
        except LibraryReadOnlyError as exc:
            show_warning_message(self, "删除失败", str(exc))
            return
        except LibraryError as exc:
            show_warning_message(self, "删除失败", str(exc))
            return
        self.refresh()

    def _on_merge_clicked(self, source_id: str) -> None:
        library = self._open_library()
        if library is None:
            return
        source = library.get(source_id)
        if source is None:
            show_warning_message(self, "合并失败", f"未找到源条目:{source_id}")
            return
        if source.source == "builtin":
            show_warning_message(self, "无法合并", "内置条目不可作为合并源。")
            return
        candidates = [e for e in self._entries if e.pattern_id != source_id]
        if not candidates:
            show_warning_message(self, "无可合并目标", "库中不存在可作为目标的其他条目。")
            return
        dialog = _MergeTargetDialog(source, candidates, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target_id = dialog.target_id
        if not target_id:
            return
        target = library.get(target_id)
        if target is None:
            show_warning_message(self, "合并失败", f"未找到目标条目:{target_id}")
            return
        if not ask_confirmation(
            self,
            "确认合并",
            f"将把「{source.pattern_name}」的 {source.hit_count} 次命中合并到「{target.pattern_name}」,"
            f"并删除源条目。",
            confirm_text="合并",
            confirm_variant="primary",
        ):
            return
        try:
            # Manual merge: bump target hit_count, then remove source.
            # We use update() so the FTS index stays in sync.
            merged_hit_count = max(
                target.hit_count + source.hit_count, dialog.merged_hit_count
            )
            library.update(
                target_id,
                hit_count=merged_hit_count,
                last_hit_chapter=(
                    source.last_hit_chapter
                    if source.last_hit_chapter is not None
                    else target.last_hit_chapter
                ),
            )
            library.remove(source_id)
        except LibraryError as exc:
            show_warning_message(self, "合并失败", str(exc))
            return
        show_info_message(
            self,
            "合并完成",
            f"已合并到「{target.pattern_name}」,合并后命中数 {merged_hit_count}。",
        )
        self.refresh()

    def _on_view_hits(self, pattern_id: str) -> None:
        entry = self._open_entry(pattern_id)
        if entry is None:
            show_warning_message(self, "未找到", f"未找到条目:{pattern_id}")
            return
        dialog = _HitExamplesDialog(entry, parent=self)
        dialog.exec()


# ---------------------------------------------------------------------------
# Stats card used on the fire page (火候) — small launcher for the dashboard
# ---------------------------------------------------------------------------


class _HumanizeLibraryCard(QFrame):
    """Compact stats + launch button shown on the 火候 (settings) page.

    Emits ``open_dashboard_requested`` when the user clicks 「打开…」 so the
    host window can decide how to surface the dashboard (tab vs child window).
    """

    open_dashboard_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("humanizeLibraryCard")
        self.setProperty("tone", "card")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("拟人化库")
        title.setObjectName("cardTitle")
        title_row.addWidget(title)
        self._badge = QLabel("—")
        self._badge.setObjectName("cardBadge")
        self._badge.setProperty("tone", "default")
        title_row.addWidget(self._badge)
        title_row.addStretch()
        layout.addLayout(title_row)

        hint = QLabel("管理内置与用户加入的 AI 写作痕迹模式。")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._stats_label = QLabel("加载中…")
        self._stats_label.setObjectName("cardStats")
        self._stats_label.setWordWrap(True)
        layout.addWidget(self._stats_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._open_btn = ActionButton("打开…", variant="primary")
        self._open_btn.setProperty("role", "openDashboard")
        self._open_btn.setProperty("density", "compact")
        self._open_btn.clicked.connect(self.open_dashboard_requested)
        button_row.addWidget(self._open_btn)
        self._refresh_btn = ActionButton("刷新", variant="secondary")
        self._refresh_btn.setProperty("role", "refreshDashboard")
        self._refresh_btn.setProperty("density", "compact")
        self._refresh_btn.clicked.connect(self.refresh)
        button_row.addWidget(self._refresh_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

    def refresh(self) -> None:
        try:
            library = HumanizeLibrary.from_default_path()
        except Exception as exc:
            self._stats_label.setText(f"库不可用:{exc}")
            self._badge.setText("异常")
            self._badge.setProperty("tone", "danger")
            self._badge.style().unpolish(self._badge)
            self._badge.style().polish(self._badge)
            return

        try:
            stats = library.stats()
            healthy = library.is_healthy()
        except Exception as exc:
            self._stats_label.setText(f"统计失败:{exc}")
            self._badge.setText("异常")
            self._badge.setProperty("tone", "danger")
            self._badge.style().unpolish(self._badge)
            self._badge.style().polish(self._badge)
            return

        self._stats_label.setText(
            f"总条目 {stats.total} · 启用 {stats.enabled} ·  "
            f"用户 {stats.user_count} · 导入 {stats.imported_count}\n"
            f"过期向量 {stats.stale_vector_count} · 最近更新 {stats.last_updated_at or '—'}"
        )
        if healthy:
            self._badge.setText("健康")
            self._badge.setProperty("tone", "success")
        else:
            self._badge.setText("降级")
            self._badge.setProperty("tone", "warning")
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)

    def update_from_stats_dict(self, payload: dict[str, Any]) -> None:
        """Update the card from a payload emitted by HumanizeLibraryDashboardPage."""
        total = int(payload.get("total", 0))
        enabled = int(payload.get("enabled", 0))
        user = int(payload.get("user", 0))
        imported = int(payload.get("imported", 0))
        stale = int(payload.get("stale", 0))
        last_updated = payload.get("last_updated_at") or "—"
        healthy = bool(payload.get("healthy", True))
        self._stats_label.setText(
            f"总条目 {total} · 启用 {enabled} · 用户 {user} · 导入 {imported}\n"
            f"过期向量 {stale} · 最近更新 {last_updated}"
        )
        if healthy:
            self._badge.setText("健康")
            self._badge.setProperty("tone", "success")
        else:
            self._badge.setText("降级")
            self._badge.setProperty("tone", "warning")
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)


__all__ = [
    "HumanizeLibraryDashboardPage",
    "TABLE_COLUMNS",
    "ANY_VALUE",
    "SEVERITY_RANK",
    # Internal classes are exported for testability and parallel task wiring
    "_EntryEditDialog",
    "_DuplicatesDialog",
    "_HitExamplesDialog",
    "_MergeTargetDialog",
    "_StatsBar",
    "_HumanizeLibraryCard",
]
