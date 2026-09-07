"""DubbingPlayer — audio player with real-time text highlighting sync.

Extends QMediaPlayer with PlaybackTimeline integration to provide:
- Segment-level highlighting (which character is speaking)
- Character-level position tracking (which word is being spoken)
- Seek-to-segment navigation
- Auto-scroll to keep current segment visible
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.audio_output import AudioOutputSelector
from novel_forge.desktop.components.signal_coalescing import LatestValueCoalescer
from novel_forge.desktop.widgets import ActionButton, Surface
from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, TimelineEntry
from novel_forge.tts.runtime.audio_runtime import remux_for_qt_playback


class _FlowLayout(QLayout):
    """Wrapping flow layout — reflows children across rows as width shrinks.

    Prevents transport buttons from overflowing or overlapping adjacent
    panels when the application window is not maximized.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = 5) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self._spacing

        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            next_x = x + item.sizeHint().width() + spacing
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + item.sizeHint().width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + margins.bottom()


class DubbingPlayerWidget(QWidget):
    """Audio player with real-time dubbing text synchronization.

    Emits signals when the current segment or character position changes,
    enabling the parent UI to highlight the active line and scroll it
    into view during playback.
    """

    # Emitted when the active segment changes: (segment_index, character_id)
    segment_changed = Signal(int, str)
    # Emitted for word-level highlight: (segment_index, char_start, char_end)
    word_highlight = Signal(int, int, int)
    # Emitted with overall progress: (fraction 0.0–1.0, position_ms, total_ms)
    progress_updated = Signal(float, int, int)
    # Emitted when playback finishes
    playback_finished = Signal()
    # Emitted whenever transport switches between playing and non-playing.
    # The parent surface can mirror play/pause state without reaching into the
    # private QMediaPlayer instance (for example Voice Room's full-chapter CTA).
    playback_state_changed = Signal(bool)
    # Emitted when current character changes: (character_name, character_id)
    character_changed = Signal(str, str)
    # Emitted when the user clicks prev/next without a timeline attached.
    # The parent (e.g. voice room) navigates its own segment list accordingly.
    prev_segment_requested = Signal()
    next_segment_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._timeline: PlaybackTimeline | None = None
        self._duration_ms: int = 0
        self._is_seeking: bool = False
        self._last_segment_idx: int = -1
        self._last_char_idx: int = -1
        self._last_character_id: str = ""
        self._position_event_serial: int = 0
        self._was_active = False
        self._shutdown_done = False
        # When a caller asks to play before the media has finished loading,
        # defer the play() call until QMediaPlayer signals LoadedMedia.  Qt's
        # setSource() is asynchronous: calling play() immediately afterwards is
        # silently dropped on the macOS backend, so an audition would show its
        # "已播放" status without producing any sound.
        self._pending_play: bool = False
        # Track the on-disk source so an ``InvalidMedia`` failure can trigger a
        # one-shot remux (MiniMax AIGC-watermarked MP3s defeat Qt's probe) and
        # reload the cleaned sidecar instead of leaving the audition silent.
        self._loaded_path: Path | None = None
        self._remux_attempted: bool = False
        # Throttle expensive UI updates during playback.  QMediaPlayer fires
        # positionChanged every ~50-100 ms; rebuilding rich-text labels on
        # every tick starves the event loop and causes audio stutter on macOS.
        # A 120 ms coalesce timer keeps text sync smooth without blocking audio.
        self._text_preview_coalescer = LatestValueCoalescer[tuple[TimelineEntry, int]](
            self,
            interval_ms=120,
            callback=self._flush_text_preview,
        )
        self._setup_player()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize the player UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Player surface
        surface = Surface("inset")
        s_layout = QVBoxLayout(surface)
        s_layout.setContentsMargins(*(10, 6, 10, 6) if self._compact else (14, 10, 14, 10))
        s_layout.setSpacing(4 if self._compact else 6)

        # Now-playing info
        self._now_playing = QLabel("未加载音频")
        self._now_playing.setObjectName("dubbingNowPlaying")
        self._now_playing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_layout.addWidget(self._now_playing)

        # Current speaker label
        self._speaker_label = QLabel("")
        self._speaker_label.setObjectName("dubbingSpeakerLabel")
        self._speaker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_layout.addWidget(self._speaker_label)

        # Current text preview (the line being spoken)
        self._text_preview = QLabel("")
        self._text_preview.setObjectName("dubbingTextPreview")
        self._text_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_preview.setWordWrap(True)
        self._text_preview.setMinimumHeight(32 if self._compact else 40)
        s_layout.addWidget(self._text_preview)

        # Seek slider with time labels
        seek_row = QHBoxLayout()
        self._time_cur = QLabel("0:00")
        self._time_cur.setObjectName("dubbingTimeLabel")
        self._time_cur.setMinimumWidth(42)
        seek_row.addWidget(self._time_cur)

        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.setEnabled(False)
        self._seek.sliderPressed.connect(self._on_seek_pressed)
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.sliderMoved.connect(self._on_seek_moved)
        seek_row.addWidget(self._seek)

        self._time_total = QLabel("0:00")
        self._time_total.setObjectName("dubbingTimeLabel")
        self._time_total.setMinimumWidth(42)
        self._time_total.setAlignment(Qt.AlignmentFlag.AlignRight)
        seek_row.addWidget(self._time_total)
        s_layout.addLayout(seek_row)

        # Control buttons + volume + output in a wrapping flow layout so the
        # transport never overflows or overlaps adjacent panels when the
        # window is not maximized.
        ctrl = _FlowLayout(spacing=5 if self._compact else 10)

        self._prev_seg_btn = ActionButton("⏮ 上段", variant="quiet")
        self._prev_seg_btn.setEnabled(False)
        self._prev_seg_btn.clicked.connect(self._on_prev_segment)
        ctrl.addWidget(self._prev_seg_btn)

        self._rewind_btn = ActionButton("⏪ 10s", variant="quiet")
        self._rewind_btn.setEnabled(False)
        self._rewind_btn.clicked.connect(self._on_rewind)
        ctrl.addWidget(self._rewind_btn)

        self._play_btn = ActionButton("▶ 播放", variant="primary")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play_pause)
        ctrl.addWidget(self._play_btn)

        self._forward_btn = ActionButton("10s ⏩", variant="quiet")
        self._forward_btn.setEnabled(False)
        self._forward_btn.clicked.connect(self._on_forward)
        ctrl.addWidget(self._forward_btn)

        self._next_seg_btn = ActionButton("下段 ⏭", variant="quiet")
        self._next_seg_btn.setEnabled(False)
        self._next_seg_btn.clicked.connect(self._on_next_segment)
        ctrl.addWidget(self._next_seg_btn)

        if self._compact:
            for button in (
                self._prev_seg_btn,
                self._rewind_btn,
                self._play_btn,
                self._forward_btn,
                self._next_seg_btn,
            ):
                button.setProperty("density", "compact")

        # Volume group — kept as one unit so it wraps together.
        vol_group = QWidget()
        vol_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        vol_row = QHBoxLayout(vol_group)
        vol_row.setContentsMargins(0, 0, 0, 0)
        vol_row.setSpacing(4)
        vol_row.addWidget(QLabel("🔊"))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(90 if self._compact else 120)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self._vol_slider)
        self._vol_label = QLabel("80%")
        self._vol_label.setMinimumWidth(35)
        vol_row.addWidget(self._vol_label)
        # Progress percentage
        self._pct_label = QLabel("0%")
        self._pct_label.setObjectName("dubbingPctLabel")
        vol_row.addWidget(self._pct_label)
        ctrl.addWidget(vol_group)

        s_layout.addLayout(ctrl)
        self._output_selector = AudioOutputSelector(self._audio_output)
        s_layout.addWidget(self._output_selector)
        layout.addWidget(surface)

    def _setup_player(self) -> None:
        """Initialize QMediaPlayer + QAudioOutput."""
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.8)

        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)

    # ─── Public API ────────────────────────────────────────────────────────

    def load_audio(
        self,
        path: str | Path,
        timeline: PlaybackTimeline | None = None,
    ) -> None:
        """Load audio file and optional timeline for text sync."""
        # A fresh load invalidates any previously deferred play request so the
        # new source does not inherit a stale auto-play intent.
        self._pending_play = False
        self._remux_attempted = False
        path = Path(path)
        if not path.exists():
            self._now_playing.setText(f"文件不存在: {path.name}")
            self._player.setSource(QUrl())
            self._timeline = None
            self._play_btn.setEnabled(False)
            self._loaded_path = None
            return

        file_size = path.stat().st_size
        if file_size < 128:
            self._now_playing.setText(f"音频文件无效 ({file_size} B)")
            self._play_btn.setEnabled(False)
            self._player.setSource(QUrl())
            self._timeline = None
            self._loaded_path = None
            return

        self._loaded_path = path
        url = QUrl.fromLocalFile(str(path))
        self._player.setSource(url)
        self._now_playing.setText(path.stem)
        self._play_btn.setEnabled(True)

        # Set timeline
        self._timeline = timeline
        self._last_segment_idx = -1
        self._last_char_idx = -1
        self._last_character_id = ""

        if timeline and timeline.total_duration_ms > 0:
            self._seek.setEnabled(True)

    def clear(self) -> None:
        """Clear the active audio and return every transport control to its empty state."""
        self._text_preview_coalescer.clear()
        self._pending_play = False
        self._remux_attempted = False
        self._loaded_path = None
        self._player.stop()
        self._player.setSource(QUrl())
        self._timeline = None
        self._duration_ms = 0
        self._was_active = False
        self._last_segment_idx = -1
        self._last_char_idx = -1
        self._last_character_id = ""
        self._now_playing.setText("未加载音频")
        self._speaker_label.clear()
        self._text_preview.clear()
        self._time_cur.setText("0:00")
        self._time_total.setText("0:00")
        self._seek.setRange(0, 0)
        self._seek.setValue(0)
        self._seek.setEnabled(False)
        self._play_btn.setText("▶ 播放")
        self._play_btn.setEnabled(False)
        self._prev_seg_btn.setEnabled(False)
        self._next_seg_btn.setEnabled(False)
        self._rewind_btn.setEnabled(False)
        self._forward_btn.setEnabled(False)
        self._pct_label.setText("0%")

    def shutdown(self) -> None:
        """Release multimedia resources before Qt tears down the widget tree."""

        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._output_selector.shutdown()
        self.clear()
        self._player.setAudioOutput(None)  # type: ignore[arg-type]  # Qt accepts a null output

    def set_timeline(self, timeline: PlaybackTimeline) -> None:
        """Set or update the playback timeline."""
        self._timeline = timeline
        self._last_segment_idx = -1

    def has_timeline(self) -> bool:
        """Return True when a segment-addressable timeline is attached."""
        return self._timeline is not None

    def set_context_visible(self, visible: bool) -> None:
        """Show or hide the built-in title/speaker/text preview block.

        Compact inspector surfaces can provide a richer subtitle stage of
        their own while retaining this widget's transport and output controls.
        """
        self._now_playing.setVisible(visible)
        self._speaker_label.setVisible(visible)
        self._text_preview.setVisible(visible)

    def play(self) -> None:
        """Start or resume playback.

        ``QMediaPlayer.setSource()`` loads media asynchronously.  If this is
        called right after ``load_audio`` (e.g. an audition's auto-play), the
        media is still ``LoadingMedia`` and a direct ``play()`` is silently
        dropped on the macOS backend.  Defer the call until ``LoadedMedia``
        arrives via ``mediaStatusChanged``.
        """
        status = self._player.mediaStatus()
        if status == QMediaPlayer.MediaStatus.LoadingMedia:
            self._pending_play = True
            return
        self._pending_play = False
        self._player.play()

    def pause(self) -> None:
        """Pause playback."""
        # A pause issued while media is still loading must also cancel the
        # deferred auto-play request; otherwise LoadedMedia would unexpectedly
        # start audio again after the UI already switched to a paused state.
        self._pending_play = False
        self._player.pause()

    def stop(self) -> None:
        """Stop playback."""
        self._pending_play = False
        self._player.stop()
        self._was_active = False

    def is_playing(self) -> bool:
        """Return True if currently playing."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def seek_to_segment(self, segment_index: int) -> None:
        """Jump playback to the start of a specific segment."""
        if not self._timeline:
            return
        for entry in self._timeline.entries:
            if entry.segment_index == segment_index:
                self._commit_seek(entry.start_ms)
                return

    def get_current_entry(self) -> TimelineEntry | None:
        """Return the timeline entry at the current playback position."""
        if not self._timeline:
            return None
        return self._timeline.segment_at(self._player.position())

    # ─── Private slots ─────────────────────────────────────────────────────

    def _on_play_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_rewind(self) -> None:
        pos = max(0, self._player.position() - 10_000)
        self._commit_seek(pos)

    def _on_forward(self) -> None:
        pos = min(self._duration_ms, self._player.position() + 10_000)
        self._commit_seek(pos)

    def _on_prev_segment(self) -> None:
        """Jump to the previous segment.

        When a timeline is attached, seeks to the previous entry within the
        loaded audio.  Otherwise emits ``prev_segment_requested`` so the
        parent (e.g. voice room) can navigate its own segment list.
        """
        if not self._timeline:
            self.prev_segment_requested.emit()
            return
        pos = self._player.position()
        prev_entry: TimelineEntry | None = None
        for entry in self._timeline.entries:
            if entry.start_ms >= pos:
                break
            prev_entry = entry
        if prev_entry:
            self._commit_seek(prev_entry.start_ms)
            return
        # A single-clip timeline still needs to hand navigation back to its
        # parent Voice Room so the visible selection can move across clips.
        self.prev_segment_requested.emit()

    def _on_next_segment(self) -> None:
        """Jump to the next segment.

        When a timeline is attached, seeks to the next entry within the
        loaded audio.  Otherwise emits ``next_segment_requested`` so the
        parent (e.g. voice room) can navigate its own segment list.
        """
        if not self._timeline:
            self.next_segment_requested.emit()
            return
        pos = self._player.position()
        for entry in self._timeline.entries:
            if entry.start_ms > pos + 500:
                self._commit_seek(entry.start_ms)
                return
        self.next_segment_requested.emit()

    def _on_volume_changed(self, value: int) -> None:
        self._audio_output.setVolume(value / 100.0)
        self._vol_label.setText(f"{value}%")

    def _on_seek_pressed(self) -> None:
        self._is_seeking = True

    def _on_seek_released(self) -> None:
        position = self._seek.value()
        self._is_seeking = False
        self._commit_seek(position)

    def _commit_seek(self, position: int) -> None:
        """Commit a seek and deterministically synchronize every subtitle consumer."""
        # Invalidate before setPosition(): some Qt multimedia backends emit
        # positionChanged synchronously here while others do not emit until a
        # later decoder tick (and a paused player may not emit at all).  Keeping
        # the invalidation after setPosition therefore makes subtitle refresh
        # backend-dependent and can leave it permanently pinned to the old
        # segment after a chapter-level seek.
        self._last_segment_idx = -1
        self._last_char_idx = -1
        self._last_character_id = ""
        position_event_serial = self._position_event_serial
        self._player.setPosition(position)
        # Publish the requested position ourselves as the fallback for
        # silent/paused seeks.  Avoid duplicating the relatively expensive
        # parent subtitle render on backends that already emitted
        # positionChanged synchronously.
        if self._position_event_serial == position_event_serial:
            self._publish_position(position, force_timeline=True)

    def _on_seek_moved(self, position: int) -> None:
        # Track the thumb, not the decoder's still-advancing old position.  The
        # user should see the target segment/subtitle while scrubbing and the
        # release handler will commit exactly the same position to the player.
        self._publish_position(position)

    def _on_position_changed(self, position: int) -> None:
        """Core sync: update slider, emit segment/word signals."""
        self._position_event_serial += 1
        if self._is_seeking:
            # QMediaPlayer keeps emitting its pre-seek playback position while
            # the slider is being dragged.  Letting those ticks reach timeline
            # sync races the scrub preview and sends the subtitle back to the
            # old segment.
            return
        self._seek.setValue(position)
        self._publish_position(position)

    def _publish_position(self, position: int, *, force_timeline: bool = False) -> None:
        """Publish one coherent transport/timeline position to every UI consumer."""
        self._time_cur.setText(self._fmt_time(position))

        # Progress — throttle to every 250 ms to avoid flooding the parent
        # with badge updates the user cannot visually track.
        total = max(self._duration_ms, self._timeline.total_duration_ms if self._timeline else 0)
        frac = position / total if total > 0 else 0.0
        self._pct_label.setText(f"{frac:.0%}")
        self.progress_updated.emit(frac, position, total)

        # Timeline sync
        if not self._timeline:
            return

        entry, char_idx = self._timeline.char_position_at(position)
        if entry is None:
            if force_timeline:
                # A seek may land in an intentional inter-segment silence.  Do
                # not retain the old entry cache: the first tick in the next
                # spoken segment must emit even if it has the same index state
                # as a prior playback visit.
                self._last_segment_idx = -1
                self._last_char_idx = -1
                self._last_character_id = ""
            return

        # Segment changed?
        if force_timeline or entry.segment_index != self._last_segment_idx:
            self._last_segment_idx = entry.segment_index
            self.segment_changed.emit(entry.segment_index, entry.character_id)

            # Update speaker label
            if entry.character_name:
                type_label = {
                    "narration": "旁白",
                    "dialogue": entry.character_name,
                    "inner_thought": f"{entry.character_name}·内心",
                }.get(entry.segment_type.value, entry.character_name)
                self._speaker_label.setText(f"🎙 {type_label}")
            else:
                self._speaker_label.setText("🎙 旁白")

            # Update text preview with highlight (immediate on segment change)
            self._update_text_preview(entry, char_idx)

            # Character changed?
            if force_timeline or entry.character_id != self._last_character_id:
                self._last_character_id = entry.character_id
                self.character_changed.emit(entry.character_name, entry.character_id)

        # Word-level highlight update (throttled: only when char changes)
        if force_timeline or char_idx != self._last_char_idx:
            self._last_char_idx = char_idx
            # Highlight window: current char ± 5 chars
            start = max(0, char_idx - 2)
            end = min(len(entry.text), char_idx + 5)
            self.word_highlight.emit(entry.segment_index, start, end)

            # Throttle text preview HTML rebuild to avoid audio stutter
            self._text_preview_coalescer.submit((entry, char_idx))

    def _flush_text_preview(self, pending: tuple[TimelineEntry, int]) -> None:
        """Coalesced text-preview update; called by the throttle timer."""
        entry, char_idx = pending
        self._update_text_preview(entry, char_idx)

    def _update_text_preview(self, entry: TimelineEntry, char_idx: int) -> None:
        """Update the text preview with highlighted current position."""
        text = entry.text
        if not text:
            self._text_preview.setText("")
            return

        # Show a window around the current character
        window_start = max(0, char_idx - 15)
        window_end = min(len(text), char_idx + 20)
        before = text[window_start:char_idx]
        current = text[char_idx : char_idx + 1] if char_idx < len(text) else ""
        after = text[char_idx + 1 : window_end]

        prefix = "…" if window_start > 0 else ""
        suffix = "…" if window_end < len(text) else ""

        rendered = (
            f"{prefix}"
            f"<span style='color: #888;'>{html.escape(before)}</span>"
            f"<span style='color: #FFC107; font-weight: bold; "
            f"background: rgba(255,193,7,0.15); border-radius: 3px; "
            f"padding: 1px 2px;'>{html.escape(current)}</span>"
            f"<span style='color: #ccc;'>{html.escape(after)}</span>"
            f"{suffix}"
        )
        self._text_preview.setText(rendered)

    def _on_duration_changed(self, duration: int) -> None:
        self._duration_ms = duration
        self._seek.setRange(0, duration)
        self._time_total.setText(self._fmt_time(duration))
        has_audio = duration > 0
        self._seek.setEnabled(has_audio)
        self._rewind_btn.setEnabled(has_audio)
        self._forward_btn.setEnabled(has_audio)
        # Prev/next segment buttons: always enabled when audio is loaded.
        # With a timeline they seek within the audio; without one they emit
        # signals for the parent to navigate its own segment list.
        self._prev_seg_btn.setEnabled(has_audio)
        self._next_seg_btn.setEnabled(has_audio)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._was_active = True
            self._play_btn.setText("⏸ 暂停")
            self.playback_state_changed.emit(True)
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._play_btn.setText("▶ 播放")
            self.playback_state_changed.emit(False)
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self._play_btn.setText("▶ 播放")
            self.playback_state_changed.emit(False)
            if self._was_active:
                self._was_active = False
                self.playback_finished.emit()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Resolve deferred auto-play once the source finishes loading.

        ``setSource()`` is asynchronous; a ``play()`` issued while the status
        is ``LoadingMedia`` is dropped silently on macOS.  When ``play()`` was
        deferred, fire it here once ``LoadedMedia`` arrives.  ``InvalidMedia``
        triggers a one-shot remux (MiniMax AIGC-watermarked MP3s defeat Qt's
        probe) and reload of the cleaned sidecar instead of leaving the
        audition silent.
        """
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._pending_play:
                self._pending_play = False
                self._player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            if self._remux_for_playback():
                return
            self._pending_play = False
            self._now_playing.setText("播放错误: 音频格式不支持或文件损坏")
            self._play_btn.setEnabled(False)

    def _remux_for_playback(self) -> bool:
        """One-shot fallback: rewrite the loaded file so Qt can decode it.

        MiniMax embeds a large AIGC-watermark ID3 tag that Qt's tiny media
        probe cannot see past, so a perfectly valid audition reports
        ``InvalidMedia`` and plays no sound.  Remux through FFmpeg (stripping
        the tag, generous probe) into a ``.qt`` sidecar and reload it.  Tried
        at most once per ``load_audio`` to avoid looping on genuinely broken
        files.
        """
        if self._remux_attempted or self._loaded_path is None:
            return False
        self._remux_attempted = True
        src = self._loaded_path
        sidecar = src.with_suffix(".qt" + (".mp3" if src.suffix.lower() != ".wav" else ".wav"))
        try:
            if not remux_for_qt_playback(src, sidecar):
                return False
        except Exception:
            return False
        # Preserve any deferred auto-play intent across the reload.
        pending = self._pending_play
        self._loaded_path = sidecar
        self._player.setSource(QUrl.fromLocalFile(str(sidecar)))
        self._pending_play = pending
        return True

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            friendly = {
                QMediaPlayer.Error.FormatError: "音频格式不支持或文件损坏",
                QMediaPlayer.Error.NetworkError: "网络错误",
            }
            optional_errors = (
                ("AccessDeniedError", "无权访问该文件"),
                ("AccessDenied", "无权访问该文件"),
                ("ResourceError", "音频资源不可用"),
                ("ServiceMissing", "音频服务不可用"),
            )
            for member_name, label in optional_errors:
                member = getattr(QMediaPlayer.Error, member_name, None)
                if member is not None:
                    friendly[member] = label
            desc = friendly.get(error, message or "未知错误")
            self._now_playing.setText(f"播放错误: {desc}")
            self._play_btn.setEnabled(False)

    # ─── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_time(ms: int) -> str:
        total_s = ms // 1000
        m, s = divmod(total_s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
