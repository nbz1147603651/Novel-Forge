"""Interleaved stream detail widget with per-segment collapsible reasoning.

This module provides the Level 2 / Level 3 streaming detail view: an
ordered list of :class:`SegmentWidget` instances (one per content or
reasoning segment) rendered inside a scrollable container.  Each
reasoning segment has a clickable header that toggles its body visibility,
so users can independently collapse/expand individual thinking blocks.

This replaces the single :class:`QTextBrowser` approach used in
``TaskFocusPanel`` for the expanded detail view, because QTextBrowser
lacks reliable per-element interaction (no DOM, no ``<details>`` support).
Using individual Qt widgets gives native show/hide behavior for free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from novel_forge.common.token_usage import format_token_count
from novel_forge.desktop.components.stream_behavior import StreamFollowController
from novel_forge.desktop.components.stream_rendering import (
    detect_stream_render_kind,
    stream_document_html,
    stream_html_from_text,
)
from novel_forge.desktop.task_observation import (
    ObservedStreamState,
    StreamSegment,
)
from novel_forge.pipeline.progress import display_step_name

_SPINNER_FRAMES = ("◴", "◷", "◶", "◵")
_CONTENT_PARAGRAPH_STYLE = (
    "text-indent: 2em; margin: 0 0 0.32em 0; line-height: 1.62; "
    "word-break: break-all; overflow-wrap: anywhere;"
)
_BLANK_PARAGRAPH_STYLE = "margin: 0 0 0.24em 0; line-height: 1.1;"
_REASONING_PARAGRAPH_STYLE = (
    "margin: 0 0 0.28em 0; line-height: 1.48; word-break: break-all; overflow-wrap: anywhere;"
)
# An expanded reasoning block opens at a calm, readable height and grows with
# incoming text until this cap.  Beyond it, the block gets its own scrollbar
# so it cannot swallow the live-output viewport.
_REASONING_INITIAL_HEIGHT = 144
_REASONING_MAX_HEIGHT = 400


class ReasoningScrollArea(QScrollArea):
    """Inner scroll area for reasoning blocks that forwards wheel events
    to the parent when at top/bottom boundaries.

    This enables nested scrolling: the inner area scrolls independently
    while content is in the middle, but passes wheel events up to the
    outer StreamDetailWidget when the user reaches the edges.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Qt uses positive angle delta for wheel-up and negative for
        # wheel-down.  Forward only when the inner view cannot move farther.
        delta = event.angleDelta().y()
        scrollbar = self.verticalScrollBar()
        value = scrollbar.value()
        maximum = scrollbar.maximum()

        if maximum <= 0:
            self._forward_to_parent(event)
            return
        # At top and trying to scroll up → forward to parent.
        if value <= 0 and delta > 0:
            self._forward_to_parent(event)
            return
        # At bottom and trying to scroll down → forward to parent.
        if value >= maximum and delta < 0:
            self._forward_to_parent(event)
            return

        # Normal case: handle internally
        super().wheelEvent(event)

    def _forward_to_parent(self, event: QWheelEvent) -> None:
        """Send the wheel event to the nearest ancestor QScrollArea."""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                # Map the event position to the parent's coordinate space
                mapped_pos = self.mapTo(parent.viewport(), event.position().toPoint())
                new_event = QWheelEvent(
                    mapped_pos,
                    event.globalPosition(),
                    event.pixelDelta(),
                    event.angleDelta(),
                    event.buttons(),
                    event.modifiers(),
                    event.phase(),
                    event.inverted(),
                )
                QApplication.sendEvent(parent.viewport(), new_event)
                return
            parent = parent.parentWidget()


def _display_task(raw: str) -> str:
    task = str(raw or "").strip()
    if not task:
        return "文本生成"
    label = display_step_name(task.lower())
    return label if label and label != task.lower() else task


def _parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _duration_text(started_at: str, updated_at: str, ended_at: str = "") -> str:
    start = _parse_dt(started_at)
    end = _parse_dt(ended_at or updated_at)
    if start is None or end is None:
        return ""
    seconds = max(0.0, (end - start).total_seconds())
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _status_label(status: str) -> str:
    return {
        "running": "调用中",
        "streaming": "输出中",
        "complete": "完成",
        "validating": "校验中",
        "repairing": "修复中",
        "retrying": "重试中",
        "validated": "已校验",
        "validation_failed": "校验失败",
        "error": "错误",
        "restarted": "已重试",
    }.get(str(status or "").strip(), str(status or "记录"))


def _compact_rich_text_lines(text: str) -> list[str | None]:
    """Return text rows with repeated blank lines collapsed to one spacer."""

    rows: list[str | None] = []
    blank_pending = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line:
            rows.append(line)
            blank_pending = False
        elif rows and not blank_pending:
            rows.append(None)
            blank_pending = True
    return rows


def _clip_preview(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n...已截断，仅显示前部预览。"


def _constrain_label_width(label: QLabel) -> None:
    """Keep long streamed labels from contributing an oversized width hint."""

    label.setMinimumWidth(0)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


# ── Segment widgets ────────────────────────────────────────────────────────


class SegmentWidget(QFrame):
    """One segment in the interleaved stream view.

    Content segments render as compact rich text labels.  Reasoning segments
    have a clickable header bar that toggles body visibility.
    """

    layout_changed = Signal()

    def __init__(
        self,
        segment: StreamSegment,
        *,
        collapsed: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._segment = segment
        self._collapsed = bool(collapsed and segment.kind == "reasoning")
        self._streaming = False
        self._cursor_visible = True
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(360)
        self._cursor_timer.timeout.connect(self._toggle_cursor)
        self.setObjectName("streamSegment")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._body_scroll: ReasoningScrollArea | None = None
        self._body_follow: StreamFollowController | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self._segment.kind == "reasoning":
            self._build_reasoning(layout)
        else:
            self._build_content(layout)

    def update_segment(self, segment: StreamSegment) -> bool:
        """Update the rendered text when a stream segment grows in place."""
        if segment.kind != self._segment.kind:
            return False
        if segment == self._segment:
            return True
        self._segment = segment
        if self._segment.kind == "reasoning":
            self._body.setText(self._reasoning_html(self._segment.text))
            self._sync_reasoning_label()
            self._queue_reasoning_height_sync()
        else:
            self._body.setText(self._render_content_html())
        return True

    def _build_reasoning(self, layout: QVBoxLayout) -> None:
        # Header bar — clickable to toggle collapse.
        header = QFrame()
        header.setObjectName("reasoningHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        # Styles in global QSS fragment (part_13_stream_detail)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(6)
        char_count = len(self._segment.text)
        self._toggle_label = QLabel(f"▼ 思考 · {char_count} 字")
        self._toggle_label.setObjectName("reasoningToggleLabel")
        _constrain_label_width(self._toggle_label)
        # Styles in global QSS fragment (part_13_stream_detail)
        header_layout.addWidget(self._toggle_label, 1)
        header_layout.addStretch()
        header.mousePressEvent = self._toggle  # type: ignore[method-assign]
        layout.addWidget(header)

        # Body — the reasoning text wrapped in a bounded ReasoningScrollArea.
        # The custom scroll area forwards wheel events to the outer container
        # when at top/bottom boundaries, enabling nested scrolling without
        # the inner scrollbar swallowing all wheel events.
        self._body = QLabel()
        self._body.setObjectName("reasoningBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.setMinimumWidth(0)
        # Styles in global QSS fragment (part_13_stream_detail)
        self._body.setText(self._reasoning_html(self._segment.text))
        self._body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._body_scroll = ReasoningScrollArea()
        self._body_scroll.setObjectName("reasoningBodyScroll")
        self._body_scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._body_scroll.setAccessibleName("思考内容，可滚动")
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._body_scroll.setFixedHeight(_REASONING_INITIAL_HEIGHT)
        self._body_scroll.setMaximumHeight(_REASONING_MAX_HEIGHT)
        self._body_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._body_scroll.setWidget(self._body)
        self._body_follow = StreamFollowController(self._body_scroll)
        layout.addWidget(self._body_scroll)
        self._body_scroll.setVisible(not self._collapsed)
        self._body.setVisible(not self._collapsed)
        self._sync_reasoning_label()
        if not self._collapsed:
            self._queue_reasoning_height_sync()

    def _build_content(self, layout: QVBoxLayout) -> None:
        self._body = QLabel()
        self._body.setObjectName("contentBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.setMinimumWidth(0)
        # Styles in global QSS fragment (part_13_stream_detail)
        self._body.setText(self._render_content_html())
        self._body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._body)

    def _toggle(self, _event: Any) -> None:
        self._collapsed = not self._collapsed
        visible = not self._collapsed
        if self._body_scroll is not None:
            self._body_scroll.setVisible(visible)
        self._body.setVisible(visible)
        self._sync_reasoning_label()
        if visible:
            self._queue_reasoning_height_sync()
        # Force immediate layout recalculation so the parent layout
        # repositions subsequent widgets without waiting for the event loop.
        seg_layout = self.layout()
        if seg_layout is not None:
            seg_layout.activate()
            seg_layout.invalidate()
            seg_layout.activate()
        self.layout_changed.emit()

    def _queue_reasoning_height_sync(self) -> None:
        if self._segment.kind != "reasoning" or self._collapsed:
            return
        if self._body_follow is not None:
            self._body_follow.notify_content_changed()
        QTimer.singleShot(0, self._sync_reasoning_height)

    def _sync_reasoning_height(self) -> None:
        """Grow an expanded reasoning body with its content, up to the cap.

        Uses a two-pass measurement to account for the vertical scrollbar
        that may appear after the first pass, narrowing the viewport and
        increasing the required height.  Without this correction the
        reasoning block can under-report its needed height, causing its
        content to overflow and visually overlap the next segment.
        """

        if self._collapsed or self._body_scroll is None:
            return
        try:
            # The label renders inside the scroll-area viewport whose width
            # determines text wrapping and therefore content height.
            viewport_width = max(1, self._body_scroll.viewport().width())

            # First pass — measure at the current viewport width.
            natural_height = self._body.heightForWidth(viewport_width)
            if natural_height <= 0:
                natural_height = self._body.sizeHint().height()
            if natural_height <= 0:
                natural_height = _REASONING_INITIAL_HEIGHT

            current_height = self._body_scroll.height()

            # Second pass — only when the content *actually* overflows the
            # current scroll-area height (meaning a scrollbar is or will be
            # visible).  Re-measure at the narrower width that accounts for
            # the scrollbar so the height is not under-estimated.
            if natural_height > current_height:
                scrollbar = self._body_scroll.verticalScrollBar()
                scrollbar_width = (
                    scrollbar.sizeHint().width()
                    if scrollbar is not None
                    else 0
                )
                if scrollbar_width > 0:
                    narrow_width = max(1, viewport_width - scrollbar_width)
                    adjusted = self._body.heightForWidth(narrow_width)
                    if adjusted > 0 and adjusted > natural_height:
                        natural_height = adjusted

            target_height = min(
                _REASONING_MAX_HEIGHT,
                max(_REASONING_INITIAL_HEIGHT, natural_height),
            )
            if self._body_scroll.height() == target_height:
                if self._body_follow is not None:
                    self._body_follow.notify_content_changed()
                return
            self._body_scroll.setFixedHeight(target_height)
            # Force the segment's own layout to recalculate immediately so
            # that the parent StreamDetailWidget repositions subsequent
            # segments in the same layout pass.
            seg_layout = self.layout()
            if seg_layout is not None:
                seg_layout.activate()
            self.updateGeometry()
            self.layout_changed.emit()
            if self._body_follow is not None:
                self._body_follow.notify_content_changed()
        except RuntimeError:
            # A deferred size update may run after its stream view closes.
            return

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._queue_reasoning_height_sync()

    def _sync_reasoning_label(self) -> None:
        char_count = len(self._segment.text)
        arrow = "▶" if self._collapsed else "▼"
        self._toggle_label.setText(f"{arrow} 思考 · {char_count} 字")

    def set_streaming(self, active: bool) -> None:
        """Show a blinking cursor at the end of the latest content segment."""
        if self._segment.kind != "content":
            active = False
        if self._streaming == active:
            return
        self._streaming = active
        self._cursor_visible = True
        if active:
            self._cursor_timer.start()
        else:
            self._cursor_timer.stop()
        if self._segment.kind == "content":
            self._body.setText(self._render_content_html())

    def _toggle_cursor(self) -> None:
        if not self._streaming or self._segment.kind != "content":
            return
        self._cursor_visible = not self._cursor_visible
        self._body.setText(self._render_content_html())

    def _render_content_html(self) -> str:
        return self._content_html(
            self._segment.text,
            cursor=self._streaming and self._cursor_visible,
        )

    @staticmethod
    def _reasoning_html(text: str) -> str:
        import html as html_mod

        paragraphs = []
        for line in _compact_rich_text_lines(text):
            if line is not None:
                paragraphs.append(
                    f"<p style='{_REASONING_PARAGRAPH_STYLE}'>{html_mod.escape(line)}</p>"
                )
            else:
                paragraphs.append(f"<p style='{_BLANK_PARAGRAPH_STYLE}'>&nbsp;</p>")
        return (
            "<div style='line-height: 1.48; max-width: 100%; "
            "word-break: break-all; overflow-wrap: anywhere;'>"
            f"{''.join(paragraphs)}</div>"
        )

    @staticmethod
    def _content_html(text: str, *, cursor: bool = False) -> str:
        if not text.strip() and not cursor:
            return ""
        return stream_document_html(
            stream_html_from_text(text, paragraph_indent=True, cursor=cursor)
        )

    @property
    def segment(self) -> StreamSegment:
        return self._segment

    def collapse(self) -> None:
        if self._segment.kind == "reasoning" and not self._collapsed:
            self._toggle(None)

    def expand(self) -> None:
        if self._segment.kind == "reasoning" and self._collapsed:
            self._toggle(None)


# ── Header / status cards ──────────────────────────────────────────────────


class StreamHeaderBar(QFrame):
    """Compact metadata strip for the currently selected stream."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamHeaderBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        self._label = QLabel("")
        self._label.setObjectName("streamHeaderText")
        _constrain_label_width(self._label)
        # Styles in global QSS fragment (part_13_stream_detail)
        layout.addWidget(self._label, 1)
        # Styles in global QSS fragment (part_13_stream_detail)

    def set_stream(self, stream: ObservedStreamState) -> None:
        task = _display_task(stream.task)
        status = _status_label(stream.status)
        bullet = "●" if stream.status in {"streaming", "running"} else "✓"
        if stream.status in {"error", "validation_failed"}:
            bullet = "!"
        render_label = detect_stream_render_kind(stream.text).label
        if stream.output_kind.lower() == "json":
            render_label = {
                "validated": "JSON",
                "failed": "JSON 诊断",
            }.get(stream.validation_status, "JSON 草稿")
        parts = [
            task,
            render_label,
            f"第 {stream.attempt} 轮" if stream.attempt else "",
            f"{bullet} {status}",
            f"{stream.text_length or len(stream.text)} 字",
            _duration_text(stream.started_at, stream.updated_at, stream.ended_at),
            stream.model or stream.provider,
        ]
        if stream.total_tokens:
            parts.append(f"Token {format_token_count(stream.total_tokens)}")
        if stream.prompt_tokens or stream.completion_tokens:
            parts.append(
                f"输入 {format_token_count(stream.prompt_tokens)} / "
                f"输出 {format_token_count(stream.completion_tokens)}"
            )
        if stream.cost_usd:
            parts.append(f"${stream.cost_usd:.4f}")
        self._label.setText(" · ".join(part for part in parts if part))


class ModelCallCard(QFrame):
    """Summary card for non-streaming model_call observation records."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modelCallCard")
        self._expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._title = QLabel("")
        self._title.setObjectName("modelCallTitle")
        _constrain_label_width(self._title)
        top.addWidget(self._title, 1)
        self._toggle_btn = QPushButton("展开")
        self._toggle_btn.setObjectName("modelCallToggle")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_preview)
        top.addWidget(self._toggle_btn)
        layout.addLayout(top)

        self._meta = QLabel("")
        self._meta.setObjectName("modelCallMeta")
        _constrain_label_width(self._meta)
        layout.addWidget(self._meta)

        self._preview = QLabel("")
        self._preview.setObjectName("modelCallPreview")
        _constrain_label_width(self._preview)
        self._preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._preview)
        self._preview.setVisible(False)

        self._stats_label = QLabel("")
        self._stats_label.setObjectName("modelCallStats")
        _constrain_label_width(self._stats_label)
        self._stats_label.setWordWrap(True)
        layout.addWidget(self._stats_label)

        # Styles in global QSS fragment (part_13_stream_detail)

    def set_stream(self, stream: ObservedStreamState) -> None:
        status = _status_label(stream.status)
        task = _display_task(stream.task)
        self._title.setText(f"{task} · {status}")
        meta_parts = [
            f"第 {stream.attempt} 轮" if stream.attempt else "",
            f"第 {stream.chapter_number} 章" if stream.chapter_number else "",
            f"{stream.text_length or len(stream.text)} 字",
            _duration_text(stream.started_at, stream.updated_at, stream.ended_at),
            stream.model or stream.provider,
            f"Token {format_token_count(stream.total_tokens)}" if stream.total_tokens else "",
        ]
        self._meta.setText(" · ".join(part for part in meta_parts if part))
        usage_parts = []
        if stream.prompt_tokens:
            usage_parts.append(f"输入 {format_token_count(stream.prompt_tokens)}")
        if stream.completion_tokens:
            usage_parts.append(f"输出 {format_token_count(stream.completion_tokens)}")
        if stream.cost_usd:
            usage_parts.append(f"实际费用 ${stream.cost_usd:.4f}")
        # Build segment statistics summary.
        self._update_segment_stats(stream, usage_parts=usage_parts)
        preview = stream.error or stream.text or "暂无输出预览。"
        self._preview.setText(_clip_preview(preview))
        if stream.status == "error":
            self.setProperty("tone", "error")
        else:
            self.setProperty("tone", "default")
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_segment_stats(
        self,
        stream: ObservedStreamState,
        *,
        usage_parts: list[str] | None = None,
    ) -> None:
        """Compute and display reasoning/content segment statistics."""
        segments = stream.segments
        if not segments:
            # Fallback: show character-level ratio from text fields.
            total_chars = (stream.text_length or len(stream.text)) + (
                stream.reasoning_length or len(stream.reasoning_text)
            )
            if total_chars > 0:
                r_chars = stream.reasoning_length or len(stream.reasoning_text)
                c_chars = stream.text_length or len(stream.text)
                r_pct = round(r_chars / total_chars * 100)
                c_pct = 100 - r_pct
                parts = [
                    f"思考 {r_pct}% ({r_chars:,}字)",
                    f"正文 {c_pct}% ({c_chars:,}字)",
                    *(usage_parts or []),
                ]
                self._stats_label.setText("  ·  ".join(parts))
            else:
                self._stats_label.setText("  ·  ".join(usage_parts or []))
            self._stats_label.setVisible(bool(self._stats_label.text()))
            return

        reasoning_segs = [s for s in segments if s.kind == "reasoning"]
        content_segs = [s for s in segments if s.kind == "content"]
        r_chars = sum(len(s.text) for s in reasoning_segs)
        c_chars = sum(len(s.text) for s in content_segs)
        total_chars = r_chars + c_chars
        parts: list[str] = []
        if total_chars > 0:
            r_pct = round(r_chars / total_chars * 100)
            c_pct = 100 - r_pct
            parts.append(f"思考 {r_pct}% ({r_chars:,}字)")
            parts.append(f"正文 {c_pct}% ({c_chars:,}字)")
        parts.append(f"共 {len(segments)} 段")
        # Show reasoning segment count when present.
        if reasoning_segs:
            parts.append(f"思考 {len(reasoning_segs)} 段")
        parts.extend(usage_parts or [])
        self._stats_label.setText("  ·  ".join(parts))
        self._stats_label.setVisible(bool(parts))

    def _toggle_preview(self) -> None:
        self._expanded = not self._expanded
        self._preview.setVisible(self._expanded)
        self._toggle_btn.setText("收起" if self._expanded else "展开")


def _make_error_card(message: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(message or "流式输出出错。")
    label.setObjectName("streamErrorCard")
    _constrain_label_width(label)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    # Styles in global QSS fragment (part_13_stream_detail)
    label.setParent(parent)
    return label


def apply_dark_palette(widget: QWidget) -> None:
    """Apply dark mode to stream detail widgets via dynamic property.

    Dark mode styles are defined in the global QSS fragment
    (part_13_stream_detail) using ``[darkMode="true"]`` selectors.
    """
    widget.setProperty("darkMode", "true")
    # Propagate to children that also have darkMode QSS rules.
    for child in widget.findChildren(QWidget):
        child.setProperty("darkMode", "true")
        child.style().unpolish(child)
        child.style().polish(child)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ── Stream detail container ────────────────────────────────────────────────


class StreamDetailWidget(QScrollArea):
    """Scrollable interleaved view of content and reasoning segments.

    Renders each :class:`StreamSegment` as an independent
    :class:`SegmentWidget`.  Provides toolbar buttons to collapse/expand
    all reasoning segments at once, and supports incremental updates
    (only appending new segments, not re-rendering existing ones).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamDetailScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Styles in global QSS fragment (part_13_stream_detail)

        self._host = QWidget()
        self._host.setObjectName("streamDetailHost")
        self._host.setMinimumWidth(0)
        self._host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(6)
        self._header_bar = StreamHeaderBar(self._host)
        self._header_bar.setVisible(False)
        self._layout.addWidget(self._header_bar)
        self._placeholder = QLabel("等待当前节点输出。")
        self._placeholder.setObjectName("cardHint")
        _constrain_label_width(self._placeholder)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setMinimumHeight(120)
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch()
        self.setWidget(self._host)

        self._segment_widgets: list[SegmentWidget] = []
        self._rendered_count = 0
        self._reasoning_user_override: bool | None = None
        self._follow_controller = StreamFollowController(self)
        self._current_stream: ObservedStreamState | None = None
        self._truncation_hint: QLabel | None = None
        self._validation_hint: QLabel | None = None
        self._model_call_card: ModelCallCard | None = None
        self._error_card: QLabel | None = None
        self._placeholder_base_text = "等待当前节点输出。"
        self._spinner_index = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(240)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._sync_host_width()

    def set_stream(self, stream: ObservedStreamState | None) -> None:
        """Update the view to reflect ``stream``.

        If the stream is the same as the current one and has new segments
        appended, only the new tail is rendered (incremental append).
        If the stream changed, the view is rebuilt from scratch.
        """
        if stream is None:
            self._clear()
            self._current_stream = None
            self._sync_host_width()
            return

        self._sync_host_width()
        if stream.source == "model_call":
            self._render_model_call(stream)
            self._current_stream = stream
            self._auto_scroll()
            return

        prev = self._current_stream
        if prev is not None and prev.stream_id == stream.stream_id:
            self._sync_header(stream)
            self._sync_validation_hint(stream)
            self._sync_truncation_hint(stream)
            self._sync_error_card(stream)
            # If segment count decreased (stream restarted), rebuild.
            if len(stream.segments) < self._rendered_count:
                self._rebuild(stream)
            else:
                # Same stream — update already-rendered segments first.  The
                # accumulator merges consecutive same-kind chunks, so the most
                # common update is "same segment count, last segment grew".
                rebuild_needed = False
                for index, segment in enumerate(stream.segments[: self._rendered_count]):
                    if not self._segment_widgets[index].update_segment(segment):
                        rebuild_needed = True
                        break
                if rebuild_needed:
                    self._rebuild(stream)
                else:
                    new_segments = stream.segments[self._rendered_count :]
                    if new_segments:
                        self._append_segments(new_segments)
                # The shared controller only scrolls when the reader remains
                # at the latest output.  This also keeps a newly appended,
                # collapsed reasoning header visible without disrupting a
                # reader who has scrolled back to inspect earlier text.
                if self._segment_widgets:
                    self._auto_scroll()
        else:
            # Different stream — full rebuild and start at its newest output.
            self._rebuild(stream)
            self._follow_controller.reset_to_latest()
            self._auto_scroll()

        self._sync_streaming_cursor(stream)
        self._current_stream = stream

    def _rebuild(self, stream: ObservedStreamState) -> None:
        self._clear(keep_header=True)
        self._sync_header(stream)
        self._sync_validation_hint(stream)
        self._sync_truncation_hint(stream)
        self._sync_error_card(stream)
        self._append_segments(stream.segments)
        if stream.segments or stream.error:
            self._placeholder.setVisible(False)

    def _clear(
        self,
        placeholder_text: str = "等待当前节点输出。",
        *,
        keep_header: bool = False,
    ) -> None:
        for widget in self._segment_widgets:
            widget.set_streaming(False)
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._segment_widgets.clear()
        if self._truncation_hint is not None:
            self._layout.removeWidget(self._truncation_hint)
            self._truncation_hint.deleteLater()
            self._truncation_hint = None
        if self._validation_hint is not None:
            self._layout.removeWidget(self._validation_hint)
            self._validation_hint.deleteLater()
            self._validation_hint = None
        if self._model_call_card is not None:
            self._layout.removeWidget(self._model_call_card)
            self._model_call_card.deleteLater()
            self._model_call_card = None
        if self._error_card is not None:
            self._layout.removeWidget(self._error_card)
            self._error_card.deleteLater()
            self._error_card = None
        if not keep_header:
            self._header_bar.setVisible(False)
        self._rendered_count = 0
        self._reasoning_user_override = None
        self._follow_controller.reset_to_latest()
        self._placeholder_base_text = placeholder_text
        self._spinner_index = 0
        self._placeholder.setText(placeholder_text)
        self._placeholder.setVisible(True)
        self._start_spinner()

    def _sync_header(self, stream: ObservedStreamState) -> None:
        self._header_bar.set_stream(stream)
        self._header_bar.setVisible(True)

    def _render_model_call(self, stream: ObservedStreamState) -> None:
        self._clear("等待模型返回…", keep_header=True)
        self._sync_header(stream)
        self._placeholder.setVisible(False)
        self._stop_spinner()
        card = ModelCallCard(self._host)
        card.set_stream(stream)
        self._model_call_card = card
        self._layout.insertWidget(self._layout.count() - 1, card)

    def _sync_error_card(self, stream: ObservedStreamState) -> None:
        if stream.status not in {"error", "validation_failed"}:
            if self._error_card is not None:
                self._layout.removeWidget(self._error_card)
                self._error_card.deleteLater()
                self._error_card = None
            return
        message = stream.error or (
            "结构化输出未通过后端校验。"
            if stream.status == "validation_failed"
            else "流式输出出错。"
        )
        if self._error_card is None:
            self._error_card = _make_error_card(message, self._host)
            self._layout.insertWidget(self._content_insert_index(), self._error_card)
        else:
            self._error_card.setText(message)
        self._placeholder.setVisible(False)
        self._stop_spinner()

    def _sync_truncation_hint(self, stream: ObservedStreamState) -> None:
        if stream.truncated:
            self._ensure_truncation_hint()
        elif self._truncation_hint is not None:
            self._layout.removeWidget(self._truncation_hint)
            self._truncation_hint.deleteLater()
            self._truncation_hint = None

    def _sync_validation_hint(self, stream: ObservedStreamState) -> None:
        if stream.output_kind.lower() != "json":
            if self._validation_hint is not None:
                self._layout.removeWidget(self._validation_hint)
                self._validation_hint.deleteLater()
                self._validation_hint = None
            return
        status = stream.validation_status
        message = {
            "validating": "结构化输出已结束，正在进行后端校验；当前内容仅为草稿。",
            "repairing": "后端正在修复结构化输出；当前内容不会作为正式结果。",
            "retrying": "结构化输出未通过，本次操作正在生成新的候选。",
            "validated": "后端校验已通过；以下为本次结构化结果。",
            "failed": "后端校验未通过；以下片段仅作诊断，不会作为正式结果。",
        }.get(status, "尚无后端校验结论；以下结构化片段仅作诊断预览。")
        if self._validation_hint is None:
            hint = QLabel(message)
            hint.setObjectName("streamValidationHint")
            _constrain_label_width(hint)
            self._layout.insertWidget(1, hint)
            self._validation_hint = hint
        else:
            self._validation_hint.setText(message)
        self._validation_hint.setProperty(
            "tone",
            "success" if status == "validated" else "error" if status == "failed" else "warning",
        )
        self._validation_hint.style().unpolish(self._validation_hint)
        self._validation_hint.style().polish(self._validation_hint)

    def _ensure_truncation_hint(self) -> None:
        if self._truncation_hint is not None:
            return
        hint = QLabel("前文已折叠，仅显示最近 50 KB 内容。")
        hint.setObjectName("streamTruncationHint")
        _constrain_label_width(hint)
        # Styles in global QSS fragment (part_13_stream_detail)
        self._layout.insertWidget(1, hint)
        self._truncation_hint = hint
        self._placeholder.setVisible(False)
        self._stop_spinner()

    def _append_segments(self, segments: tuple[StreamSegment, ...]) -> None:
        if not segments:
            return
        self._placeholder.setVisible(False)
        self._stop_spinner()
        insert_before = self._layout.count() - 1  # before the stretch

        for seg in segments:
            collapsed = self._should_collapse_new_segment(seg)
            widget = SegmentWidget(seg, collapsed=collapsed, parent=self._host)
            widget.layout_changed.connect(self._auto_scroll)
            self._segment_widgets.append(widget)
            self._layout.insertWidget(insert_before, widget)
            insert_before += 1
        self._rendered_count += len(segments)

    def _should_collapse_new_segment(self, segment: StreamSegment) -> bool:
        if segment.kind != "reasoning":
            return False
        if self._reasoning_user_override is not None:
            return not self._reasoning_user_override
        # 默认折叠所有思考段，避免大段推理内容抢占视觉焦点
        return True

    def _content_insert_index(self) -> int:
        index = 1
        if self._validation_hint is not None:
            index += 1
        if self._truncation_hint is not None:
            index += 1
        return index

    def _sync_streaming_cursor(self, stream: ObservedStreamState) -> None:
        active_index = -1
        if stream.status == "streaming":
            for index in range(len(self._segment_widgets) - 1, -1, -1):
                if self._segment_widgets[index].segment.kind == "content":
                    active_index = index
                    break
        for index, widget in enumerate(self._segment_widgets):
            widget.set_streaming(index == active_index)

    def _start_spinner(self) -> None:
        if self._placeholder.isVisible() and not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        if self._spinner_timer.isActive():
            self._spinner_timer.stop()

    def _tick_spinner(self) -> None:
        if not self._placeholder.isVisible():
            self._stop_spinner()
            return
        frame = _SPINNER_FRAMES[self._spinner_index % len(_SPINNER_FRAMES)]
        self._spinner_index += 1
        self._placeholder.setText(f"{frame} {self._placeholder_base_text}")

    def _auto_scroll(self) -> None:
        self._follow_controller.notify_content_changed()

    def _sync_host_width(self) -> None:
        width = self.viewport().width()
        if width > 0:
            self._host.setMaximumWidth(width)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_host_width()

    def collapse_all_reasoning(self) -> None:
        self._reasoning_user_override = False
        for widget in self._segment_widgets:
            widget.collapse()

    def expand_all_reasoning(self) -> None:
        self._reasoning_user_override = True
        for widget in self._segment_widgets:
            widget.expand()

    @property
    def segment_count(self) -> int:
        return len(self._segment_widgets)
