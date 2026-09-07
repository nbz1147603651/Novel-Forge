"""Widgets for the chapter studio page.

This module contains reusable UI components used in the chapter studio workspace,
extracted from chapter_studio_page.py for better separation of concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QModelIndex,
    QPersistentModelIndex,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.constants import LABEL_CHAPTER_RE as _JOB_LABEL_CHAPTER_PATTERN
from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.theme import qcolor_hex, resolve_qcolor
from novel_forge.desktop.widgets import Surface

# Priority for job status (lower number = higher priority for display)
_STATUS_PRIORITY: dict[DesktopJobState, int] = {
    DesktopJobState.RUNNING: 0,
    DesktopJobState.QUEUED: 1,
    DesktopJobState.PAUSED: 2,
    DesktopJobState.FAILED: 3,
    DesktopJobState.SUCCEEDED: 4,
}


def _job_chapter_number(job: DesktopJobRecord) -> int | None:
    raw = (job.result or {}).get("chapter_number")
    try:
        if raw is not None and str(raw).strip():
            return int(raw)
    except (TypeError, ValueError):
        pass
    match = _JOB_LABEL_CHAPTER_PATTERN.search(job.label or "")
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _status_priority(status: DesktopJobState) -> int:
    return _STATUS_PRIORITY.get(status, 99)


@dataclass(frozen=True)
class ChapterDisplayState:
    """Resolved rail display state after merging chapter and job status."""

    icon: str
    color: str
    variant: str = "default"
    ignored_job_status: DesktopJobState | None = None


def resolve_chapter_display_state(
    job_status: DesktopJobState | None, raw_status: Any
) -> ChapterDisplayState:
    """Resolve the single source of truth for chapter rail presentation.

    Chapter files/checkpoints are the durable truth. Historical paused/failed
    jobs may remain in the local job list after a manual archive, so they must
    not downgrade a chapter that is already marked done.
    """
    raw_status = str(raw_status or "").strip()
    active_job_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
    if job_status == DesktopJobState.RUNNING:
        return ChapterDisplayState("⟳", qcolor_hex("accent.primary"), "running")
    if job_status == DesktopJobState.QUEUED:
        return ChapterDisplayState("⏳", qcolor_hex("text.stale"), "running")

    if raw_status in ("completed", "done") and job_status not in active_job_states:
        ignored = job_status if job_status in {DesktopJobState.PAUSED, DesktopJobState.FAILED} else None
        return ChapterDisplayState("✓", qcolor_hex("status.success.job"), ignored_job_status=ignored)
    if raw_status == "stale" and job_status not in active_job_states:
        return ChapterDisplayState("✗", qcolor_hex("status.danger"), "stale")
    if raw_status == "rewriting" and job_status not in active_job_states:
        return ChapterDisplayState("○", qcolor_hex("text.stale"), "rewriting")

    status_map: dict[DesktopJobState, tuple[str, str]] = {
        DesktopJobState.PAUSED: ("⏸", qcolor_hex("text.stale")),
        DesktopJobState.SUCCEEDED: ("✓", qcolor_hex("status.success.job")),
        DesktopJobState.FAILED: ("✗", qcolor_hex("status.danger")),
    }
    if job_status is not None and job_status in status_map:
        icon, color = status_map[job_status]
        return ChapterDisplayState(icon, color)
    if raw_status in ("writing", "draft"):
        return ChapterDisplayState("●", qcolor_hex("text.stale"))
    return ChapterDisplayState("○", qcolor_hex("text.stale"))


def _status_icon(
    job_status: DesktopJobState | None, raw_status: Any
) -> tuple[str, str]:
    state = resolve_chapter_display_state(job_status, raw_status)
    return state.icon, state.color


class _ChapterRailButton(QPushButton):
    """Button representing a chapter in the rail navigation."""

    def __init__(
        self,
        chapter_number: int,
        text: str,
        *,
        active: bool = False,
        variant: str = "default",
    ) -> None:
        super().__init__(text)
        self.chapter_number = chapter_number
        self.setObjectName("chapterRailBtn")
        self.setCheckable(True)
        self.setChecked(active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(30)
        self.setMaximumHeight(34)
        self.setToolTip(text)
        self.setAccessibleName(f"Chapter {chapter_number}: {text}")
        self.setProperty("railVariant", variant)
        self.setStyleSheet("text-align: left; padding-left: 6px;")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )


# Custom data roles for the chapter rail list model
ChapterRailRole_Number = Qt.ItemDataRole.UserRole + 1
ChapterRailRole_StatusLabel = Qt.ItemDataRole.UserRole + 2
ChapterRailRole_RawStatus = Qt.ItemDataRole.UserRole + 3
ChapterRailRole_Variant = Qt.ItemDataRole.UserRole + 4
ChapterRailRole_JobStatus = Qt.ItemDataRole.UserRole + 5


class ChapterRailDelegate(QStyledItemDelegate):
    """Delegate painting chapter rail items to match #chapterRailBtn QSS style."""

    _MIN_ITEM_WIDTH = 220
    _STATUS_SLOT_WIDTH = 26
    _TEXT_LEFT_PADDING = 6
    _TEXT_STATUS_GAP = 8

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if not index.isValid():
            return

        is_checked = bool(index.data(Qt.ItemDataRole.CheckStateRole))
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_running = index.data(ChapterRailRole_Variant) == "running"
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        rect = self._visible_rect(option)
        # Inner rect with padding matching QSS padding: 7px 10px
        inner = rect.adjusted(10, 7, -10, -7)
        if inner.height() < 20 or inner.width() < 32:
            return  # Too small to render meaningfully

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        if is_running:
            bg = resolve_qcolor("status.danger", 30)
        elif is_checked:
            bg = resolve_qcolor("accent.primary", 33)
        elif is_hovered:
            bg = resolve_qcolor("bg.control.hover", 204)
        else:
            bg = resolve_qcolor("bg.control", 217)
        painter.setBrush(QBrush(bg))

        # Border
        if is_running:
            border = resolve_qcolor("status.danger", 80)
        elif is_checked:
            border = resolve_qcolor("accent.primary", 97)
        elif is_hovered:
            border = resolve_qcolor("border.default", 56)
        else:
            border = resolve_qcolor("border.default", 31)
        painter.setPen(QPen(border, 1))

        # Rounded rect (border-radius: 8px)
        painter.drawRoundedRect(inner, 8, 8)

        # Selection indicator: thin accent bar on left edge
        if is_checked:
            indicator_rect = QRectF(
                inner.left() + 1,
                inner.center().y() - 8,
                3,
                16,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(resolve_qcolor("accent.primary", 217)))
            painter.drawRoundedRect(indicator_rect, 2, 2)

        # Status icon
        raw_status = index.data(ChapterRailRole_RawStatus) or ""
        job_status = index.data(ChapterRailRole_JobStatus)
        status_icon, status_color = _status_icon(job_status, raw_status)

        # Text
        font = QFont()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Bold if is_checked or is_running else QFont.Weight.Medium)
        painter.setFont(font)
        if is_running:
            painter.setPen(QPen(resolve_qcolor("status.danger.alt")))
        elif is_checked:
            painter.setPen(QPen(resolve_qcolor("accent.deep")))
        else:
            painter.setPen(QPen(resolve_qcolor("text.chapter.rail")))

        # Elide text if too long
        fm = painter.fontMetrics()
        reserved_status_width = (
            self._STATUS_SLOT_WIDTH + self._TEXT_STATUS_GAP if status_icon else 0
        )
        text_left = inner.left() + self._TEXT_LEFT_PADDING
        text_width = max(
            0,
            inner.width() - self._TEXT_LEFT_PADDING - reserved_status_width,
        )
        text_rect = QRectF(text_left, inner.top(), text_width, inner.height())
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(
            text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided
        )

        if status_icon:
            icon_font = QFont()
            icon_font.setPixelSize(13)
            icon_font.setWeight(QFont.Weight.Bold)
            painter.setFont(icon_font)
            painter.setPen(QPen(QColor(status_color)))
            icon_width = min(self._STATUS_SLOT_WIDTH, inner.width())
            icon_rect = QRectF(
                inner.right() - icon_width,
                inner.top(),
                icon_width,
                inner.height(),
            )
            painter.drawText(
                icon_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                status_icon,
            )

        painter.restore()

    def _visible_rect(self, option: QStyleOptionViewItem) -> QRectF:
        """Return the item rect clamped to the viewport width.

        The delegate keeps a fallback width for offscreen tests and model sizing,
        but the chapter rail hides horizontal scrollbars. When the fallback is
        wider than the real viewport, painting the status icon against that full
        width pushes it outside the visible area.
        """
        rect = QRectF(option.rect)
        viewport_width = self._viewport_width(option)
        if viewport_width > 0 and rect.width() > viewport_width:
            rect.setWidth(viewport_width)
        return rect

    def _viewport_width(self, option: QStyleOptionViewItem) -> int:
        widget = getattr(option, "widget", None)
        if widget is None:
            return 0
        viewport = getattr(widget, "viewport", None)
        if not callable(viewport):
            return 0
        try:
            return max(0, int(viewport().width()))
        except RuntimeError:
            return 0

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        width = self._viewport_width(option) or option.rect.width()
        if width <= 0:
            width = self._MIN_ITEM_WIDTH
        return QSize(width, 48)


class ChapterRailPanel(Surface):
    """Left-side chapter navigation rail."""

    chapter_selected = Signal(int)
    _VIRTUAL_THRESHOLD = 50

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("panel", parent)
        # Surface("panel") 会添加 QGraphicsDropShadowEffect，
        # 该效果会让整个子树走离屏渲染路径，在 macOS 上导致
        # QScrollArea viewport 出现合成伪影（交替深色遮挡条纹）。
        # 轨道面板通过 border + background 已有足够层次感，无需阴影。
        self.setGraphicsEffect(None)  # type: ignore[arg-type]  # PySide accepts clearing with None.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        heading = QLabel("章节轨道")
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        self._meta_label = QLabel("尚未载入项目")
        self._meta_label.setObjectName("cardMeta")
        self._meta_label.setWordWrap(True)
        layout.addWidget(self._meta_label)

        sep = QFrame()
        sep.setObjectName("railSep")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Button-based rail (for <= 50 chapters)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setObjectName("railScroll")

        rail_container = QWidget()
        self._rail_buttons_layout = QVBoxLayout(rail_container)
        self._rail_buttons_layout.setSpacing(3)
        self._rail_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._rail_buttons_layout.addStretch()
        scroll.setWidget(rail_container)
        self._scroll = scroll

        layout.addWidget(scroll, 1)

        # Selection indicator: thin accent bar that slides to active chapter
        self._indicator = QWidget(rail_container)
        self._indicator.setObjectName("railIndicator")
        self._indicator.setFixedWidth(3)
        self._indicator.setVisible(False)
        self._indicator.raise_()

        # Slide animation for the indicator
        self._indicator_animation = QPropertyAnimation(self._indicator, b"geometry")
        self._indicator_animation.setDuration(200)
        self._indicator_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Virtual list components (for > 50 chapters) — lazily created
        self._list_view: QListView | None = None
        self._list_model: QStandardItemModel | None = None
        self._list_delegate: ChapterRailDelegate | None = None
        self._use_virtual = False

        # Job status per chapter: chapter_number -> DesktopJobState
        self._chapter_job_status: dict[int, DesktopJobState] = {}

    def bind_jobs(self, jobs: list[DesktopJobRecord], *, project_id: str = "") -> None:
        self._chapter_job_status.clear()
        for job in jobs:
            if project_id and job.project_id != project_id:
                continue
            chapter_num = _job_chapter_number(job)
            if chapter_num is None:
                continue
            existing = self._chapter_job_status.get(chapter_num)
            if existing is None or _status_priority(existing) > _status_priority(job.status):
                self._chapter_job_status[chapter_num] = job.status

    def _animate_indicator_to(self, target_button: QPushButton) -> None:
        try:
            btn_geo = target_button.geometry()
        except RuntimeError:
            return
        if btn_geo.height() <= 0:
            self._indicator.setVisible(False)
            return
        indicator_height = max(btn_geo.height() - 8, 16)
        target_rect = self._indicator.geometry()
        target_rect.setX(0)
        target_rect.setY(btn_geo.y() + (btn_geo.height() - indicator_height) // 2)
        target_rect.setWidth(3)
        target_rect.setHeight(indicator_height)

        if not animations_supported():
            self._indicator.setGeometry(target_rect)
            return

        self._indicator_animation.stop()
        if not self._indicator.isVisible():
            self._indicator.setGeometry(target_rect)
            self._indicator.setVisible(True)
            return
        self._indicator_animation.setStartValue(self._indicator.geometry())
        self._indicator_animation.setEndValue(target_rect)
        self._indicator_animation.start()

    def _position_indicator_after_layout(self, active_button: QPushButton) -> None:
        self._indicator.setVisible(True)
        self._indicator.raise_()
        self._animate_indicator_to(active_button)
        self._scroll.ensureWidgetVisible(active_button)

    def update_meta(self, title: str, total_chapters: int, current_chapter: int) -> None:
        """Update the meta label with project info."""
        self._meta_label.setText(f"{title} · 共 {total_chapters} 章 · 当前第 {current_chapter} 章")

    def render_chapters(self, chapters: list[dict[str, Any]], current_chapter: int) -> None:
        use_virtual = len(chapters) > self._VIRTUAL_THRESHOLD
        if use_virtual:
            self._ensure_virtual_list()
            self._render_virtual(chapters, current_chapter)
        else:
            self._ensure_button_list()
            self._render_buttons(chapters, current_chapter)

    def _ensure_virtual_list(self) -> None:
        if self._use_virtual and self._list_view is not None:
            self._list_view.setVisible(True)
            self._scroll.setVisible(False)
            self._indicator.setVisible(False)
            return
        self._use_virtual = True
        self._scroll.setVisible(False)
        self._indicator.setVisible(False)
        self._clear_button_rail()

        if self._list_view is None:
            self._list_view = QListView()
            self._list_view.setFrameShape(QFrame.Shape.NoFrame)
            self._list_view.setUniformItemSizes(True)
            self._list_view.setSelectionMode(QListView.SelectionMode.SingleSelection)
            self._list_view.setSelectionBehavior(QListView.SelectionBehavior.SelectRows)
            self._list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._list_view.setMouseTracking(True)
            self._list_view.setObjectName("railListView")

            self._list_model = QStandardItemModel()
            self._list_delegate = ChapterRailDelegate()
            self._list_view.setModel(self._list_model)
            self._list_view.setItemDelegate(self._list_delegate)
            self._list_view.clicked.connect(self._on_virtual_clicked)

            layout = self.layout()
            if isinstance(layout, QVBoxLayout):
                layout.addWidget(self._list_view, 1)
        self._list_view.setVisible(True)

    def _ensure_button_list(self) -> None:
        if not self._use_virtual:
            return
        self._use_virtual = False
        if self._list_view is not None:
            self._list_view.setVisible(False)
        self._scroll.setVisible(True)

    def _on_virtual_clicked(self, index: QModelIndex) -> None:
        num = index.data(ChapterRailRole_Number)
        if num is not None:
            self.chapter_selected.emit(int(num))

    def _clear_button_rail(self) -> None:
        from novel_forge.desktop.widgets import clear_layout

        self._indicator_animation.stop()
        self._indicator.setVisible(False)
        clear_layout(self._rail_buttons_layout)
        self._rail_buttons_layout.addStretch()

    def _render_buttons(self, chapters: list[dict[str, Any]], current_chapter: int) -> None:
        self._clear_button_rail()

        active_button = None
        for chapter in chapters:
            num = chapter.get("chapter_number", 0)
            raw_status = chapter.get("status", "pending")
            status_label = chapter.get("status_label", "")
            title = chapter.get("title", "未命名")
            tooltip = f"第 {num} 章 {title}"
            if status_label:
                tooltip += f" · {status_label}"
            if len(title) > 10:
                title = title[:10] + "…"
            label = f"第 {num} 章 {title}"

            job_status = self._chapter_job_status.get(num)
            display_state = resolve_chapter_display_state(job_status, raw_status)
            variant = display_state.variant

            button = _ChapterRailButton(
                num,
                label,
                active=num == current_chapter,
                variant=variant,
            )
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda checked=False, chapter_num=num: self.chapter_selected.emit(chapter_num)
            )

            button.setMaximumWidth(260)
            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(2, 1, 2, 1)
            container_layout.setSpacing(2)
            container_layout.addWidget(button, 1)
            indicator = self._create_status_label(job_status, raw_status)
            container_layout.addWidget(indicator, 0)
            self._rail_buttons_layout.insertWidget(
                self._rail_buttons_layout.count() - 1, container
            )

            if num == current_chapter:
                active_button = button

        if active_button is not None:
            QTimer.singleShot(
                0,
                lambda btn=active_button: self._position_indicator_after_layout(btn),
            )
        else:
            self._indicator_animation.stop()
            self._indicator.setVisible(False)

    def _create_status_label(
        self, job_status: DesktopJobState | None, raw_status: str = ""
    ) -> QLabel:
        icon, color = _status_icon(job_status, raw_status)
        label = QLabel(icon)
        label.setFixedWidth(20)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {color}; background: transparent;"
        )
        if job_status == DesktopJobState.RUNNING:
            self._start_pulse_animation(label)
        return label

    def _start_pulse_animation(self, label: QLabel) -> None:
        if not animations_supported():
            return
        pulse_anim = QPropertyAnimation(label, b"windowOpacity", label)
        pulse_anim.setDuration(800)
        pulse_anim.setStartValue(1.0)
        pulse_anim.setEndValue(0.3)
        pulse_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        pulse_anim.setLoopCount(-1)
        pulse_anim.setDirection(QPropertyAnimation.Direction.Backward)
        label._pulse_anim = pulse_anim  # type: ignore[attr-defined]
        pulse_anim.start()

    def _render_virtual(self, chapters: list[dict[str, Any]], current_chapter: int) -> None:
        if self._list_model is None or self._list_view is None:
            return
        self._list_view.setUpdatesEnabled(False)
        try:
            self._list_model.clear()

            active_row = -1
            for row, chapter in enumerate(chapters):
                num = chapter.get("chapter_number", 0)
                raw_status = chapter.get("status", "pending")
                status_label = chapter.get("status_label", "")
                title = chapter.get("title", "未命名")
                full_tooltip = f"第 {num} 章 {title}"
                if status_label:
                    full_tooltip += f" · {status_label}"
                if len(title) > 10:
                    title = title[:10] + "…"
                label = f"第 {num} 章 {title}"

                job_status = self._chapter_job_status.get(num)
                display_state = resolve_chapter_display_state(job_status, raw_status)
                variant = display_state.variant

                item = QStandardItem(label)
                item.setData(num, ChapterRailRole_Number)
                item.setData(status_label, ChapterRailRole_StatusLabel)
                item.setData(raw_status, ChapterRailRole_RawStatus)
                item.setData(variant, ChapterRailRole_Variant)
                item.setData(job_status, ChapterRailRole_JobStatus)
                item.setData(full_tooltip, Qt.ItemDataRole.ToolTipRole)
                item.setCheckable(False)
                if num == current_chapter:
                    item.setData(Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
                    active_row = row
                self._list_model.appendRow(item)

            selection = self._list_view.selectionModel()
            if active_row >= 0 and selection is not None:
                active_index = self._list_model.index(active_row, 0)
                selection.select(
                    active_index,
                    selection.SelectionFlag.ClearAndSelect,
                )
                self._list_view.scrollTo(active_index)
            elif selection is not None:
                selection.clearSelection()
        finally:
            self._list_view.setUpdatesEnabled(True)

    def reset(self) -> None:
        self._meta_label.setText("尚未载入项目")
        self._ensure_button_list()
        self._clear_button_rail()
        if self._list_model is not None:
            self._list_model.clear()


class ModeSelector(QWidget):
    """Mode selector radio buttons for chapter studio."""

    mode_changed = Signal(int, bool)  # (button_id, checked) — matches QButtonGroup.idToggled

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        mode_label = QLabel("裁决模式")
        mode_label.setObjectName("cardMeta")
        layout.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._mode_group = QButtonGroup(self)

        self._mode_manual_btn = QRadioButton("全手动")
        self._mode_manual_btn.setToolTip("所有决策由用户手动操作。")
        self._mode_manual_btn.setAccessibleName("Mode: Manual (全手动)")
        self._mode_manual_btn.setChecked(True)

        self._mode_suggest_btn = QRadioButton("AI 建议")
        self._mode_suggest_btn.setToolTip("AI 提供推荐方案，用户可修改后确认。")
        self._mode_suggest_btn.setAccessibleName("Mode: AI Suggest (AI 建议)")

        self._mode_auto_btn = QRadioButton("本章自动")
        self._mode_auto_btn.setToolTip("AI 自动完成当前章节（准备、起草、归档），完成后停止。")
        self._mode_auto_btn.setAccessibleName("Mode: Auto chapter (本章自动)")

        self._mode_book_auto_btn = QRadioButton("章节连跑")
        self._mode_book_auto_btn.setToolTip("AI 从当前章连续自动推进，直到全书完成。")
        self._mode_book_auto_btn.setAccessibleName("Mode: Auto book (章节连跑)")

        self._mode_group.addButton(self._mode_manual_btn, 0)
        self._mode_group.addButton(self._mode_suggest_btn, 1)
        self._mode_group.addButton(self._mode_auto_btn, 2)
        self._mode_group.addButton(self._mode_book_auto_btn, 3)

        mode_row.addWidget(self._mode_manual_btn)
        mode_row.addWidget(self._mode_suggest_btn)
        mode_row.addWidget(self._mode_auto_btn)
        mode_row.addWidget(self._mode_book_auto_btn)
        layout.addLayout(mode_row)

        self._mode_group.idToggled.connect(self.mode_changed.emit)

    def current_mode(self) -> int:
        """Return current mode index (0=manual, 1=suggest, 2=auto)."""
        return self._mode_group.checkedId()

    def set_mode(self, mode: int, *, animate: bool = True) -> None:
        """Set the current mode programmatically.

        Programmatic project/state sync can run repeatedly while the page is
        refreshing. Avoid restarting opacity animations in those paths because
        it leaves the radio buttons transparent until the next paint cycle.
        """
        btn_map = {
            0: self._mode_manual_btn,
            1: self._mode_suggest_btn,
            2: self._mode_auto_btn,
            3: self._mode_book_auto_btn,
        }
        btn = btn_map.get(mode)
        if btn is None:
            return
        changed = not btn.isChecked()
        if not btn.isChecked():
            btn.setChecked(True)
        if animate and changed:
            self._animate_active_mode_label(btn)
        else:
            self._restore_mode_button_opacity()

    def _animate_active_mode_label(self, active_btn: QRadioButton) -> None:
        """Fade-in the active mode label (Task 19 visual polish).

        Uses ``Motion.fade_in`` (200ms, macOS-safe opacity per D1,
        reduced-motion aware). All four buttons are faded together so
        the visual transition reads as a single coordinated event
        regardless of which mode becomes active.
        """
        from novel_forge.desktop.motion import Motion, animations_supported

        if not animations_supported("opacity"):
            self._restore_mode_button_opacity()
            return
        del active_btn
        for btn in self._mode_buttons():
            try:
                Motion.fade_in(btn, duration=200, easing="standard")
            except RuntimeError:
                continue

    def _mode_buttons(self) -> tuple[QRadioButton, QRadioButton, QRadioButton, QRadioButton]:
        return (
            self._mode_manual_btn,
            self._mode_suggest_btn,
            self._mode_auto_btn,
            self._mode_book_auto_btn,
        )

    def _restore_mode_button_opacity(self) -> None:
        """Stop pending fades and keep mode controls readable during sync refreshes."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        for btn in self._mode_buttons():
            anim = btn.property("_motion_anim")
            stop = getattr(anim, "stop", None)
            if callable(stop):
                try:
                    stop()
                except RuntimeError:
                    pass
            cached = getattr(btn, "_motion_anims", None)
            if isinstance(cached, list):
                for item in list(cached):
                    stop_cached = getattr(item, "stop", None)
                    if callable(stop_cached):
                        try:
                            stop_cached()
                        except RuntimeError:
                            pass
                try:
                    btn._motion_anims = []  # type: ignore[attr-defined]
                except RuntimeError:
                    pass
            effect = btn.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                effect.setOpacity(1.0)
            btn.setProperty("_motion_anim", None)


class WritingModeSelector(QWidget):
    """Segmented writing-mode selector for chapter drafting strategy."""

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QLabel("写作模式")
        label.setObjectName("cardMeta")
        layout.addWidget(label)

        self._group = QButtonGroup(self)
        self._whole_btn = QRadioButton("整章写作")
        self._whole_btn.setToolTip("按现有流程一次生成完整章节草稿。")
        self._whole_btn.setChecked(True)
        self._scene_btn = QRadioButton("场景级写作")
        self._scene_btn.setToolTip(
            "高级模式：先规划独立场景，验证依赖后按组生成，再拼接并做全章统一润色。"
        )
        self._group.addButton(self._whole_btn, 0)
        self._group.addButton(self._scene_btn, 1)

        layout.addWidget(self._whole_btn)
        layout.addWidget(self._scene_btn)
        self._group.idToggled.connect(self._emit_mode)

    def _emit_mode(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        self.mode_changed.emit("scene_level" if button_id == 1 else "whole_chapter")

    def current_mode(self) -> str:
        return "scene_level" if self._group.checkedId() == 1 else "whole_chapter"

    def set_mode(self, mode: str) -> None:
        target = self._scene_btn if mode == "scene_level" else self._whole_btn
        if not target.isChecked():
            target.setChecked(True)


class CompassCard(Surface):
    """A single card in the compass showing context."""

    _COLLAPSED_BODY_HEIGHT: int = 24

    def __init__(
        self,
        title: str,
        tone: str = "card",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(tone, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        heading = QLabel(title)
        heading.setObjectName("cardMeta")
        layout.addWidget(heading)

        self._body = QLabel("待载入")
        self._body.setObjectName("cardBody")
        self._body.setWordWrap(True)
        layout.addWidget(self._body)

        self._foot = QLabel("")
        self._foot.setObjectName("cardHint")
        self._foot.setWordWrap(True)
        layout.addWidget(self._foot)

        self._expanded: bool = True
        self._expand_anim: QPropertyAnimation | None = None
        self._pending_hide_foot: bool = False
        self._foot_hide_timer = QTimer(self)
        self._foot_hide_timer.setSingleShot(True)
        self._foot_hide_timer.timeout.connect(self._hide_foot_after_collapse)

    def update_content(self, body: str, foot: str = "") -> None:
        """Update the card content."""
        self._body.setText(body)
        self._foot.setText(foot)

    def reset(self) -> None:
        """Reset to default empty state."""
        self._body.setText("待载入")
        self._foot.setText("")

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expand: bool, *, animate: bool = True) -> None:
        """Expand or collapse the card with a 250ms fade + height transition.

        Task 19 polish: ``Motion.fade_in`` for the foot label and
        ``Motion.collapse_height`` for the body.  The height animation
        is gated by ``animations_supported("geometry")`` so macOS (D1)
        gets the opacity-only variant — the height is applied instantly
        there.
        """
        if expand == self._expanded:
            return
        self._expanded = expand

        from novel_forge.desktop.motion import Motion, animations_supported

        if not animate:
            self._apply_expanded_state(expand)
            return

        if expand:
            self._foot_hide_timer.stop()
            if self._pending_hide_foot:
                self._foot.setVisible(True)
                self._pending_hide_foot = False
            try:
                Motion.fade_in(self._foot, duration=250, easing="standard")
            except RuntimeError:
                pass
        else:
            try:
                Motion.fade_out(self._foot, duration=250, easing="accelerate")
            except RuntimeError:
                pass
            self._pending_hide_foot = True
            self._foot_hide_timer.start(260)

        if animations_supported("geometry"):
            current_height = self._body.height() or self._COLLAPSED_BODY_HEIGHT
            target_height = (
                self.sizeHint().height() if expand else self._COLLAPSED_BODY_HEIGHT
            )
            try:
                self._expand_anim = Motion.collapse_height(
                    self._body,
                    start_height=current_height,
                    end_height=target_height,
                    duration=250,
                    easing="standard",
                )
            except RuntimeError:
                self._expand_anim = None
        else:
            self._body.setVisible(expand)
            if expand:
                self._body.setMinimumHeight(0)

    def _hide_foot_after_collapse(self) -> None:
        if not self._expanded:
            self._foot.setVisible(False)
            self._pending_hide_foot = False

    def _apply_expanded_state(self, expand: bool) -> None:
        self._foot_hide_timer.stop()
        self._pending_hide_foot = False
        if expand:
            self._foot.setVisible(True)
            self._body.setVisible(True)
            self._body.setMinimumHeight(0)
        else:
            self._foot.setVisible(False)
            self._body.setVisible(False)


class MotifTipsWidget(QWidget):
    """Widget displaying memory-enhanced motif suggestions.

    Shows relevant motif suggestions based on current chapter context,
    helping writers maintain thematic coherence across chapters.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        header = QLabel("母题提示")
        header.setObjectName("motifTipsHeader")
        layout.addWidget(header)

        self._content = QLabel("暂无母题建议")
        self._content.setWordWrap(True)
        self._content.setObjectName("motifTipsContent")
        layout.addWidget(self._content)

        self._warnings_label = QLabel("")
        self._warnings_label.setWordWrap(True)
        self._warnings_label.setObjectName("motifTipsWarning")
        self._warnings_label.setVisible(False)
        layout.addWidget(self._warnings_label)

    def update_motifs(
        self,
        motif_suggestions: list[dict[str, Any]] | None = None,
        motif_warnings: list[dict[str, Any]] | None = None,
        current_chapter: int = 0,
    ) -> None:
        """Update motif suggestions and warnings.

        Args:
            motif_suggestions: List of motif suggestion dicts
            motif_warnings: List of motif warning dicts (e.g., repetition warnings)
            current_chapter: Current chapter number
        """
        if not motif_suggestions and not motif_warnings:
            self._content.setText("暂无母题建议")
            self._warnings_label.setVisible(False)
            return

        suggestions_text = ""
        if motif_suggestions:
            suggestion_parts = []
            for s in motif_suggestions[:3]:
                suggestion_text = s.get("suggestion", "")
                priority = s.get("priority", "medium")

                priority_icon = (
                    "🔴"
                    if priority in ("high", "critical")
                    else "🟡"
                    if priority == "medium"
                    else "⚪"
                )
                suggestion_parts.append(f"{priority_icon} {suggestion_text}")

            suggestions_text = "\n".join(suggestion_parts)

        warnings_text = ""
        if motif_warnings:
            warning_parts = []
            for w in motif_warnings[:2]:
                motif_name = w.get("motif_name", "未知母题")
                severity = w.get("severity", "")
                warning_parts.append(f"⚠️ {motif_name} ({severity})")

            if warning_parts:
                warnings_text = "重复警告: " + " | ".join(warning_parts)

        if suggestions_text:
            self._content.setText(suggestions_text)
        else:
            self._content.setText("暂无相关母题建议")

        if warnings_text:
            self._warnings_label.setText(warnings_text)
            self._warnings_label.setVisible(True)
        else:
            self._warnings_label.setVisible(False)


class CompassPanel(QWidget):
    """Panel containing three compass cards for context."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._cards: dict[str, CompassCard] = {
            "previous": CompassCard("上一章实际结果", "inset"),
            "current": CompassCard("本章目标", "hero"),
            "next": CompassCard("下一章预埋点", "inset"),
        }

        for card in self._cards.values():
            layout.addWidget(card, 1)

    def update_context(
        self,
        previous_summary: str,
        previous_exit_summary: str,
        current_goal: str,
        current_outline: str,
        next_goal: str,
        next_title: str,
        motif_suggestions: list[dict[str, Any]] | None = None,
        motif_warnings: list[dict[str, Any]] | None = None,
        current_chapter: int = 0,
    ) -> None:
        """Update all compass cards with context data."""
        self._cards["previous"].update_content(
            previous_summary or "上一章尚无正文，可直接围绕本章目标起笔。",
            previous_exit_summary or "",
        )
        self._cards["current"].update_content(
            current_goal or "当前章尚未配置目标。",
            current_outline or "",
        )
        self._cards["next"].update_content(
            next_goal or "下一章暂未设定，先聚焦当前章。",
            next_title or "",
        )

    def reset(self) -> None:
        """Reset all cards to empty state."""
        for card in self._cards.values():
            card.reset()


__all__ = [
    "ChapterRailDelegate",
    "ChapterRailPanel",
    "ChapterRailRole_Number",
    "ChapterRailRole_RawStatus",
    "ChapterRailRole_StatusLabel",
    "ChapterRailRole_Variant",
    "CompassCard",
    "CompassPanel",
    "ModeSelector",
    "_ChapterRailButton",
]
