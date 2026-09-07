"""Tests for AudioPlayerWidget component (offscreen Qt)."""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# Set offscreen platform before any Qt imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp() -> Any:
    """Create a QApplication for testing."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class TestAudioPlayerWidget:
    """Test AudioPlayerWidget."""

    def test_import(self) -> None:
        """Should be importable."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        assert AudioPlayerWidget is not None

    def test_create_widget(self, qapp: Any) -> None:
        """Should create widget without error."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        assert widget is not None

    def test_initial_state(self, qapp: Any) -> None:
        """Should have correct initial state."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        # Volume slider should be at default (e.g. 80)
        vol = widget._volume_slider.value()
        assert vol >= 0
        assert vol <= 100

    def test_audio_output_selector_follows_system_output(self, qapp: Any) -> None:
        """Every audition player exposes a live system/headphone route choice."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        assert widget._output_selector.is_following_system_default is True
        assert "跟随系统输出" in widget._output_selector._device_combo.itemText(0)

    def test_dubbing_player_exposes_output_selector(self, qapp: Any) -> None:
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        assert widget._output_selector.is_following_system_default is True
        assert widget._output_selector._device_combo.count() >= 1

    def test_single_entry_timeline_hands_next_navigation_back_to_parent(self, qapp: Any) -> None:
        """A subtitle timeline for one clip must not trap the Voice Room next button."""
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
        from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, TimelineEntry
        from novel_forge.tts.schemas import SegmentType

        widget = DubbingPlayerWidget()
        timeline = PlaybackTimeline(
            entries=[
                TimelineEntry(
                    segment_index=3,
                    start_ms=0,
                    end_ms=1_000,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c1",
                    character_name="林小满",
                    text="沈先生？",
                )
            ],
            total_duration_ms=1_000,
        )
        timeline.build_index()
        widget.set_timeline(timeline)
        requested: list[bool] = []
        widget.next_segment_requested.connect(lambda: requested.append(True))

        widget._on_next_segment()

        assert requested == [True]
        widget.shutdown()

    def test_dubbing_player_shutdown_releases_native_media(self, qapp: Any) -> None:
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        widget.shutdown()
        widget.shutdown()

        assert widget._shutdown_done is True
        assert widget._player.source().isEmpty()
        assert widget._player.audioOutput() is None

    def test_play_defers_until_media_loaded(self, qapp: Any, tmp_path: Any) -> None:
        """An audition's play() right after load_audio must not be dropped.

        QMediaPlayer.setSource() loads media asynchronously.  When play() is
        called while the status is still LoadingMedia, it must defer the call
        until LoadedMedia arrives instead of being silently dropped (the bug
        behind the "已播放已保存的试听 but no sound" symptom on macOS).
        """
        from PySide6.QtMultimedia import QMediaPlayer

        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        audio_file = tmp_path / "preview.mp3"
        # A small but non-empty blob passes the player's <128 byte guard.
        audio_file.write_bytes(b"\x00" * 256)

        # Simulate the async loading window: mediaStatus() reports LoadingMedia
        # until the LoadedMedia signal fires.
        status = {"value": QMediaPlayer.MediaStatus.LoadingMedia}
        widget._player.mediaStatus = lambda: status["value"]  # type: ignore[method-assign]
        calls: list[str] = []
        widget._player.play = lambda: calls.append("play")  # type: ignore[method-assign]

        widget.load_audio(audio_file)
        widget.play()

        # While still loading, the underlying player must NOT have been told to
        # play yet -- the request is parked as pending instead.
        assert calls == []
        assert widget._pending_play is True

        # Once loading completes, the deferred play fires exactly once.
        status["value"] = QMediaPlayer.MediaStatus.LoadedMedia
        widget._player.mediaStatusChanged.emit(status["value"])
        assert calls == ["play"]
        assert widget._pending_play is False

        widget.shutdown()

    def test_pause_cancels_deferred_play_while_media_is_loading(
        self, qapp: Any, tmp_path: Any
    ) -> None:
        """A visible pause action must prevent a pending load from auto-playing."""
        from PySide6.QtMultimedia import QMediaPlayer

        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        audio_file = tmp_path / "preview.mp3"
        audio_file.write_bytes(b"\x00" * 256)
        status = {"value": QMediaPlayer.MediaStatus.LoadingMedia}
        widget._player.mediaStatus = lambda: status["value"]  # type: ignore[method-assign]
        calls: list[str] = []
        widget._player.play = lambda: calls.append("play")  # type: ignore[method-assign]

        widget.load_audio(audio_file)
        widget.play()
        assert widget._pending_play is True

        widget.pause()
        status["value"] = QMediaPlayer.MediaStatus.LoadedMedia
        widget._player.mediaStatusChanged.emit(status["value"])

        assert widget._pending_play is False
        assert calls == []
        widget.shutdown()

    def test_invalid_media_clears_pending_play(self, qapp: Any, tmp_path: Any) -> None:
        """A corrupted source surfaces an error instead of leaving a silent audition."""
        from PySide6.QtMultimedia import QMediaPlayer

        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        audio_file = tmp_path / "preview.mp3"
        audio_file.write_bytes(b"\x00" * 256)

        status = {"value": QMediaPlayer.MediaStatus.LoadingMedia}
        widget._player.mediaStatus = lambda: status["value"]  # type: ignore[method-assign]
        widget._player.play = lambda: None  # type: ignore[method-assign]

        widget.load_audio(audio_file)
        widget.play()
        assert widget._pending_play is True

        status["value"] = QMediaPlayer.MediaStatus.InvalidMedia
        widget._player.mediaStatusChanged.emit(status["value"])
        assert widget._pending_play is False
        assert not widget._play_btn.isEnabled()

        widget.shutdown()

    def test_invalid_media_triggers_one_shot_remux_retry(self, qapp: Any, tmp_path: Any) -> None:
        """An InvalidMedia failure auto-remuxes the file and reloads it.

        MiniMax AIGC-watermarked MP3s defeat Qt's probe and report
        ``InvalidMedia``.  The player must remux the loaded file into a clean
        sidecar and reload it once, preserving any deferred auto-play intent so
        the audition still plays instead of going silent.
        """
        from PySide6.QtMultimedia import QMediaPlayer

        import novel_forge.desktop.components.dubbing_player as dp_module
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget

        widget = DubbingPlayerWidget()
        audio_file = tmp_path / "preview.mp3"
        audio_file.write_bytes(b"\x00" * 256)

        # Drive the player straight to InvalidMedia (the failing-probe state).
        status = {"value": QMediaPlayer.MediaStatus.LoadingMedia}
        widget._player.mediaStatus = lambda: status["value"]  # type: ignore[method-assign]
        reload_calls: list[str] = []
        widget._player.setSource = lambda url: reload_calls.append(str(url.toString()))  # type: ignore[method-assign]
        widget._player.play = lambda: None  # type: ignore[method-assign]

        # Stub the remux so the test does not depend on a real FFmpeg round-trip.
        remuxed = {"count": 0}
        original = dp_module.remux_for_qt_playback

        def fake_remux(src: object, dst: object) -> bool:
            remuxed["count"] += 1
            import pathlib

            pathlib.Path(dst).write_bytes(b"\x00" * 256)
            return True

        dp_module.remux_for_qt_playback = fake_remux  # type: ignore[assignment]
        try:
            widget.load_audio(audio_file)
            widget.play()  # defer until loaded
            assert widget._pending_play is True

            status["value"] = QMediaPlayer.MediaStatus.InvalidMedia
            widget._player.mediaStatusChanged.emit(status["value"])

            # The remux ran and the cleaned sidecar was reloaded (setSource
            # called again with a .qt sidecar path), while the deferred play
            # intent survives for the reload.
            assert remuxed["count"] == 1
            assert any(".qt" in call for call in reload_calls)
            assert widget._pending_play is True

            # Only one remux attempt per load -- a second InvalidMedia must
            # not loop on a genuinely broken file.
            widget._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
            assert remuxed["count"] == 1
        finally:
            dp_module.remux_for_qt_playback = original  # type: ignore[assignment]

        widget.shutdown()

    def test_set_volume(self, qapp: Any) -> None:
        """Should set volume correctly."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.set_volume(50)
        assert widget._volume_slider.value() == 50

    def test_set_volume_clamped(self, qapp: Any) -> None:
        """Should clamp volume to 0-100."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.set_volume(150)
        assert widget._volume_slider.value() == 100

        widget.set_volume(-10)
        assert widget._volume_slider.value() == 0

    def test_stop(self, qapp: Any) -> None:
        """Should stop without error."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.stop()  # Should not raise

    def test_shutdown_releases_native_media(self, qapp: Any) -> None:
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.shutdown()
        widget.shutdown()

        assert widget._shutdown_done is True
        assert widget._player.source().isEmpty()
        assert widget._player.audioOutput() is None

    def test_pause(self, qapp: Any) -> None:
        """Should pause without error."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.pause()  # Should not raise

    def test_play_without_load(self, qapp: Any) -> None:
        """Should handle play without load gracefully."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.play()  # Should not raise even without loaded audio

    def test_load_nonexistent_file(self, qapp: Any) -> None:
        """Should handle nonexistent file gracefully."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        widget.load_audio("/nonexistent/path/audio.mp3")
        # Should not crash

    def test_signals_exist(self, qapp: Any) -> None:
        """Should have expected signals."""
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        assert hasattr(widget, "playback_started")
        assert hasattr(widget, "playback_paused")
        assert hasattr(widget, "playback_finished")

    def test_audio_player_remux_on_invalid_media(self, qapp: Any, tmp_path: Any) -> None:
        """AudioPlayerWidget auto-remuxes MiniMax clips on InvalidMedia.

        Mirrors the DubbingPlayerWidget fallback: when Qt's FFmpeg probe
        rejects an AIGC-watermarked MP3, the player remuxes the loaded file
        into a clean ``.qt`` sidecar and reloads it once.  A second
        InvalidMedia must not loop.
        """
        from PySide6.QtMultimedia import QMediaPlayer

        import novel_forge.desktop.components.audio_player as ap_module
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        audio_file = tmp_path / "asset.mp3"
        audio_file.write_bytes(b"\x00" * 256)

        status = {"value": QMediaPlayer.MediaStatus.LoadingMedia}
        widget._player.mediaStatus = lambda: status["value"]  # type: ignore[method-assign]
        reload_calls: list[str] = []
        widget._player.setSource = lambda url: reload_calls.append(str(url.toString()))  # type: ignore[method-assign]

        remuxed = {"count": 0}
        original = ap_module.remux_for_qt_playback

        def fake_remux(src: object, dst: object) -> bool:
            remuxed["count"] += 1
            import pathlib

            pathlib.Path(dst).write_bytes(b"\x00" * 256)
            return True

        ap_module.remux_for_qt_playback = fake_remux  # type: ignore[assignment]
        try:
            widget.load_audio(audio_file)

            status["value"] = QMediaPlayer.MediaStatus.InvalidMedia
            widget._player.mediaStatusChanged.emit(status["value"])

            # Remux ran once and the cleaned sidecar was reloaded.
            assert remuxed["count"] == 1
            assert any(".qt" in call for call in reload_calls)

            # A second InvalidMedia must not trigger another remux.
            widget._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
            assert remuxed["count"] == 1
        finally:
            ap_module.remux_for_qt_playback = original  # type: ignore[assignment]

        widget.shutdown()

    def test_audio_player_remux_disabled_after_clear(self, qapp: Any, tmp_path: Any) -> None:
        """clear() resets the remux attempt flag so a later load can retry."""
        from PySide6.QtMultimedia import QMediaPlayer

        import novel_forge.desktop.components.audio_player as ap_module
        from novel_forge.desktop.components.audio_player import AudioPlayerWidget

        widget = AudioPlayerWidget()
        audio_file = tmp_path / "asset.mp3"
        audio_file.write_bytes(b"\x00" * 256)

        widget._player.mediaStatus = lambda: QMediaPlayer.MediaStatus.InvalidMedia  # type: ignore[method-assign]
        widget._player.setSource = lambda url: None  # type: ignore[method-assign]

        remuxed = {"count": 0}
        original = ap_module.remux_for_qt_playback

        def fake_remux(src: object, dst: object) -> bool:
            remuxed["count"] += 1
            import pathlib

            pathlib.Path(dst).write_bytes(b"\x00" * 256)
            return True

        ap_module.remux_for_qt_playback = fake_remux  # type: ignore[assignment]
        try:
            widget.load_audio(audio_file)
            widget._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
            assert remuxed["count"] == 1

            # After clear, a fresh load gets a fresh remux attempt.
            widget.clear()
            assert widget._remux_attempted is False
            widget.load_audio(audio_file)
            widget._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
            assert remuxed["count"] == 2
        finally:
            ap_module.remux_for_qt_playback = original  # type: ignore[assignment]

        widget.shutdown()

    def test_has_timeline_reflects_loaded_timeline(self, qapp: Any, tmp_path: Any) -> None:
        """``has_timeline`` reports whether a segment-addressable timeline is attached.

        The voice room uses this to decide whether double-clicking a segment can
        seek the chapter player; it must be False before any audio/timeline
        loads and True once a timeline is attached.
        """
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
        from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline

        widget = DubbingPlayerWidget()
        assert widget.has_timeline() is False

        widget.set_timeline(PlaybackTimeline(entries=[], total_duration_ms=0))
        assert widget.has_timeline() is True

        widget.clear()
        assert widget.has_timeline() is False

        widget.shutdown()

    def test_dubbing_seek_scrub_uses_thumb_position_for_subtitles(self, qapp: Any) -> None:
        """Old decoder ticks must not pull subtitles behind the seek thumb."""
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
        from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, TimelineEntry
        from novel_forge.tts.schemas import SegmentType

        widget = DubbingPlayerWidget()
        timeline = PlaybackTimeline(
            entries=[
                TimelineEntry(
                    segment_index=0,
                    start_ms=0,
                    end_ms=999,
                    segment_type=SegmentType.NARRATION,
                    character_id="narrator",
                    character_name="旁白",
                    text="第一段字幕",
                ),
                TimelineEntry(
                    segment_index=1,
                    start_ms=1_000,
                    end_ms=2_000,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c1",
                    character_name="林小满",
                    text="第二段字幕",
                ),
            ],
            total_duration_ms=2_000,
        )
        timeline.build_index()
        widget.set_timeline(timeline)
        changed: list[int] = []
        widget.segment_changed.connect(
            lambda segment_index, _character_id: changed.append(segment_index)
        )

        widget._on_position_changed(200)
        widget._on_seek_pressed()
        widget._on_seek_moved(1_500)
        # This is the stale position QMediaPlayer can emit while playback is
        # still running under a held slider thumb.
        widget._on_position_changed(300)

        assert changed == [0, 1]
        assert widget._last_segment_idx == 1
        assert widget._time_cur.text() == "0:01"
        widget.shutdown()

    def test_dubbing_seek_release_forces_subtitle_refresh_without_backend_signal(
        self, qapp: Any
    ) -> None:
        """A paused/silent backend seek still refreshes segment and word tracking."""
        from novel_forge.desktop.components.dubbing_player import DubbingPlayerWidget
        from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, TimelineEntry
        from novel_forge.tts.schemas import SegmentType

        widget = DubbingPlayerWidget()
        timeline = PlaybackTimeline(
            entries=[
                TimelineEntry(
                    segment_index=7,
                    start_ms=1_000,
                    end_ms=2_000,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c7",
                    character_name="沈岸",
                    text="拖动后应立即跟随",
                )
            ],
            total_duration_ms=2_000,
        )
        timeline.build_index()
        widget.set_timeline(timeline)
        widget._seek.setRange(0, 2_000)
        widget._seek.setValue(1_500)
        # Model a backend that accepts the seek but does not emit
        # positionChanged while paused.
        committed: list[int] = []
        widget._player.setPosition = lambda position: committed.append(position)  # type: ignore[method-assign]
        segments: list[int] = []
        words: list[tuple[int, int, int]] = []
        widget.segment_changed.connect(
            lambda segment_index, _character_id: segments.append(segment_index)
        )
        widget.word_highlight.connect(
            lambda segment_index, start, end: words.append((segment_index, start, end))
        )

        widget._on_seek_pressed()
        widget._on_seek_released()

        assert committed == [1_500]
        assert segments == [7]
        assert words and words[-1][0] == 7
        assert widget._last_segment_idx == 7
        widget.shutdown()
