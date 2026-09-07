"""Audio player widget — QMediaPlayer-based audio playback for Voice Studio.

Provides play/pause, seek, volume control, and time display.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from novel_forge.desktop.components.audio_output import AudioOutputSelector
from novel_forge.desktop.widgets import ActionButton, Surface
from novel_forge.tts.runtime.audio_runtime import remux_for_qt_playback


class AudioPlayerWidget(QWidget):
    """Audio player widget with play/pause, seek, volume, and time display.

    Uses QMediaPlayer + QAudioOutput for MP3/WAV playback.
    """

    playback_started = Signal()
    playback_paused = Signal()
    playback_finished = Signal()

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._duration_ms: int = 0
        self._is_seeking = False
        self._shutdown_done = False
        # Track the on-disk source so an ``InvalidMedia`` failure can trigger a
        # one-shot remux (MiniMax AIGC-watermarked MP3s defeat Qt's probe) and
        # reload the cleaned sidecar instead of leaving playback silent.
        self._loaded_path: Path | None = None
        self._remux_attempted: bool = False
        self._setup_player()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*(0, 0, 0, 0) if self._compact else (16, 12, 16, 12))
        layout.setSpacing(4 if self._compact else 8)

        # Main player surface
        player_surface = Surface("inset")
        player_layout = QVBoxLayout(player_surface)
        player_layout.setContentsMargins(*(8, 5, 8, 5) if self._compact else (12, 10, 12, 10))
        player_layout.setSpacing(4 if self._compact else 8)

        # Title label
        self._title_label = QLabel("未加载音频")
        self._title_label.setObjectName("audioTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        player_layout.addWidget(self._title_label)

        # Progress bar (seek slider)
        seek_layout = QHBoxLayout()
        self._time_current = QLabel("0:00")
        self._time_current.setObjectName("audioTimeLabel")
        self._time_current.setMinimumWidth(40)
        seek_layout.addWidget(self._time_current)

        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setEnabled(False)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)
        self._seek_slider.sliderMoved.connect(self._on_seek_moved)
        seek_layout.addWidget(self._seek_slider)

        self._time_total = QLabel("0:00")
        self._time_total.setObjectName("audioTimeLabel")
        self._time_total.setMinimumWidth(40)
        self._time_total.setAlignment(Qt.AlignmentFlag.AlignRight)
        seek_layout.addWidget(self._time_total)

        player_layout.addLayout(seek_layout)

        # Control buttons row
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(5 if self._compact else 12)

        # Rewind 10s
        self._rewind_btn = ActionButton("⏪ 10s", variant="quiet")
        self._rewind_btn.setEnabled(False)
        self._rewind_btn.clicked.connect(self._on_rewind)
        controls_layout.addWidget(self._rewind_btn)

        controls_layout.addStretch()

        # Play/Pause button
        self._play_btn = ActionButton("▶ 播放", variant="primary")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play_pause)
        controls_layout.addWidget(self._play_btn)

        controls_layout.addStretch()

        # Forward 10s
        self._forward_btn = ActionButton("10s ⏩", variant="quiet")
        self._forward_btn.setEnabled(False)
        self._forward_btn.clicked.connect(self._on_forward)
        controls_layout.addWidget(self._forward_btn)

        if self._compact:
            for button in (self._rewind_btn, self._play_btn, self._forward_btn):
                button.setProperty("density", "compact")

        player_layout.addLayout(controls_layout)

        # Volume row
        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("🔊"))
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setMaximumWidth(120)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_layout.addWidget(self._volume_slider)
        self._volume_label = QLabel("80%")
        self._volume_label.setMinimumWidth(35)
        volume_layout.addWidget(self._volume_label)
        volume_layout.addStretch()
        player_layout.addLayout(volume_layout)

        self._output_selector = AudioOutputSelector(self._audio_output)
        player_layout.addWidget(self._output_selector)

        layout.addWidget(player_surface)

    def _setup_player(self) -> None:
        """Initialize QMediaPlayer and QAudioOutput."""
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.8)

        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)

    # ─── Public API ────────────────────────────────────────────────────────

    def load_audio(self, path: str | Path) -> None:
        """Load an audio file for playback."""
        self._remux_attempted = False
        path = Path(path)
        if not path.exists():
            self._title_label.setText(f"文件不存在: {path.name}")
            self._loaded_path = None
            return

        # Guard against empty or suspiciously small files (e.g. mock data)
        file_size = path.stat().st_size
        if file_size < 128:
            self._title_label.setText(f"音频文件无效 ({file_size} B): {path.name}")
            self._play_btn.setEnabled(False)
            self._loaded_path = None
            return

        self._loaded_path = path
        url = QUrl.fromLocalFile(str(path))
        self._player.setSource(url)
        self._title_label.setText(path.stem)
        self._play_btn.setEnabled(True)

    def play(self) -> None:
        """Start or resume playback."""
        self._player.play()

    def pause(self) -> None:
        """Pause playback."""
        self._player.pause()

    def stop(self) -> None:
        """Stop playback and reset position."""
        self._player.stop()

    def clear(self) -> None:
        """Release the active media source and reset the transport controls."""

        self._remux_attempted = False
        self._loaded_path = None
        self._player.stop()
        self._player.setSource(QUrl())
        self._duration_ms = 0
        self._is_seeking = False
        self._title_label.setText("未加载音频")
        self._time_current.setText("0:00")
        self._time_total.setText("0:00")
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setValue(0)
        self._seek_slider.setEnabled(False)
        self._play_btn.setText("▶ 播放")
        self._play_btn.setEnabled(False)
        self._rewind_btn.setEnabled(False)
        self._forward_btn.setEnabled(False)

    def shutdown(self) -> None:
        """Release multimedia resources before Qt tears down the widget tree."""

        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._output_selector.shutdown()
        self.clear()
        self._player.setAudioOutput(None)  # type: ignore[arg-type]  # Qt accepts a null output

    def set_volume(self, volume: int) -> None:
        """Set volume (0-100)."""
        self._volume_slider.setValue(volume)

    def is_playing(self) -> bool:
        """Return True if currently playing."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # ─── Private slots ─────────────────────────────────────────────────────

    def _on_play_pause(self) -> None:
        """Toggle play/pause."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_rewind(self) -> None:
        """Rewind 10 seconds."""
        pos = max(0, self._player.position() - 10000)
        self._player.setPosition(pos)

    def _on_forward(self) -> None:
        """Forward 10 seconds."""
        pos = min(self._duration_ms, self._player.position() + 10000)
        self._player.setPosition(pos)

    def _on_volume_changed(self, value: int) -> None:
        """Handle volume slider change."""
        self._audio_output.setVolume(value / 100.0)
        self._volume_label.setText(f"{value}%")

    def _on_seek_pressed(self) -> None:
        """User started seeking."""
        self._is_seeking = True

    def _on_seek_released(self) -> None:
        """User finished seeking — jump to position."""
        self._is_seeking = False
        position = self._seek_slider.value()
        self._player.setPosition(position)

    def _on_seek_moved(self, position: int) -> None:
        """Update time label while seeking."""
        self._time_current.setText(self._format_time(position))

    def _on_position_changed(self, position: int) -> None:
        """Update seek slider and time label during playback."""
        if not self._is_seeking:
            self._seek_slider.setValue(position)
            self._time_current.setText(self._format_time(position))

    def _on_duration_changed(self, duration: int) -> None:
        """Update seek slider range when duration changes."""
        self._duration_ms = duration
        self._seek_slider.setRange(0, duration)
        self._time_total.setText(self._format_time(duration))
        self._seek_slider.setEnabled(duration > 0)
        self._rewind_btn.setEnabled(duration > 0)
        self._forward_btn.setEnabled(duration > 0)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Update play button text and emit signals."""
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("⏸ 暂停")
            self.playback_started.emit()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._play_btn.setText("▶ 播放")
            self.playback_paused.emit()
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self._play_btn.setText("▶ 播放")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Auto-remux on ``InvalidMedia`` so MiniMax clips still play.

        MiniMax embeds a large AIGC-watermark ID3 tag that Qt's tiny media
        probe cannot see past, reporting ``InvalidMedia`` for a valid file.
        Remux through FFmpeg into a clean ``.qt`` sidecar and reload it once;
        if that also fails, fall through to the error display.
        """
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            if self._remux_for_playback():
                return
            self._title_label.setText("播放错误: 音频格式不支持或文件损坏")
            self._play_btn.setEnabled(False)

    def _remux_for_playback(self) -> bool:
        """One-shot fallback: rewrite the loaded file so Qt can decode it.

        Tried at most once per ``load_audio`` to avoid looping on genuinely
        broken files.  Mirrors ``DubbingPlayerWidget._remux_for_playback``.
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
        self._loaded_path = sidecar
        self._player.setSource(QUrl.fromLocalFile(str(sidecar)))
        return True

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        """Handle player error."""
        if error != QMediaPlayer.Error.NoError:
            # Provide user-friendly error messages
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
            self._title_label.setText(f"播放错误: {desc}")
            self._play_btn.setEnabled(False)

    # ─── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_time(ms: int) -> str:
        """Format milliseconds to mm:ss."""
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"
