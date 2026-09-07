"""Voice Studio playback synchronization mixin.

Extracted from ``page.py`` (M0 refactor).  These handlers bridge audio
playback events (segment/character/word highlight, progress, completion)
into the script and Voice Room UI.

All methods operate on ``self._*`` state initialized by
:class:`VoiceStudioPage`; compose with it, never instantiate standalone.

Throttle timers (``_word_hl_throttle``, ``_progress_throttle``,
``_room_word_hl_throttle``) coalesce high-frequency playback ticks so the
Qt event loop is not starved (which causes audio stutter on the macOS
QMediaPlayer backend).
"""

from __future__ import annotations

import html as html_lib
from typing import TYPE_CHECKING, Any

from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.pages.voice_studio.helpers import (
    HTML_BODY_FONT_FAMILY as _HTML_BODY_FONT_FAMILY,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    segment_status_color as _segment_status_color,
)
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba

__all__ = ("PlaybackSyncMixin",)


class PlaybackSyncMixin:
    """Playback-driven UI synchronization for :class:`VoiceStudioPage`."""

    _pending_word_hl: tuple[int, int, int] | None
    _pending_room_word_hl: tuple[int, int, int] | None
    _pending_progress: tuple[float, int, int] | None

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any: ...

    # ─── Segment / character highlight ────────────────────────────────────

    def _on_playback_segment_changed(self, segment_idx: int, character_id: str) -> None:
        """Highlight the active segment in both script and sync browsers during playback."""
        self._playback_segment_idx = segment_idx
        self._playback_active = True
        # Update script tab (配音脚本) with highlight + auto-scroll
        self._render_script_html()
        self._scroll_to_segment(segment_idx)
        # Update sync script browser (实时脚本) in audio tab
        self._render_sync_script_html()
        self._scroll_sync_to_segment(segment_idx)
        # Voice Room chapter playback drives both sides of the workspace: the
        # queue follows on the left, while direction, text and subtitle state
        # refresh on the right.
        if self._voice_room_playback:
            self._select_voice_room_playback_segment(segment_idx)
            self._set_room_full_playback_ui(True, segment_index=segment_idx)
            if hasattr(self, "_room_status_badge"):
                self._room_status_badge.setText(
                    f"正在播放全章 · 第 {segment_idx + 1} 段 · 字幕跟随"
                )
                self._room_status_badge.set_tone("success")

    def _on_playback_character_changed(self, character_name: str, character_id: str) -> None:
        """Highlight the active character avatar during playback."""
        # Flash the avatar button
        for cid, btn in self._avatar_buttons.items():
            is_active = cid == character_id
            btn.setChecked(is_active)

    # ─── Word-level highlight (throttled) ─────────────────────────────────

    def _on_playback_word_highlight(self, segment_idx: int, char_start: int, char_end: int) -> None:
        """Queue word-level highlight; flushed by throttle timer to avoid audio stutter."""
        self._pending_word_hl = (segment_idx, char_start, char_end)
        self._start_visibility_throttle_timer(self._word_hl_throttle)

    def _flush_word_highlight(self) -> None:
        """Apply the most recent word highlight that was queued during playback."""
        pending = self._pending_word_hl
        if pending is None:
            return
        self._pending_word_hl = None
        segment_idx, char_start, char_end = pending
        playback_script = self._playback_script or self._current_script
        if not playback_script:
            return
        seg = next(
            (item for item in playback_script.segments if item.segment_index == segment_idx),
            None,
        )
        if seg is not None:
            text = seg.text
            before = text[:char_start]
            highlighted = text[char_start:char_end]
            after = text[char_end:]
            html = (
                f"<div style='font-size: 12pt; line-height: 1.8; padding: 8px;'>"
                f"<span style='color:{qcolor_hex('text.muted')};'>"
                f"{html_lib.escape(before)}</span>"
                f"<span style='color:{qcolor_hex('status.warning')}; font-weight:bold; "
                f"background:{qcolor_rgba('status.warning', 0.12)}; border-radius:3px; "
                f"padding: 2px 4px;'>{html_lib.escape(highlighted)}</span>"
                f"<span style='color:{qcolor_hex('text.disabled')};'>"
                f"{html_lib.escape(after)}</span>"
                f"</div>"
            )
            if hasattr(self, "_subtitle_text"):
                update_browser_html(self._subtitle_text, html)
            if self._voice_room_playback and hasattr(self, "_room_segment_text"):
                room_segment = self._selected_voice_room_segment()
                if room_segment is not None and room_segment.segment_index == segment_idx:
                    self._set_voice_room_segment_text(
                        room_segment,
                        highlight=(char_start, char_end),
                    )

    def _on_room_word_highlight(
        self,
        segment_idx: int,
        char_start: int,
        char_end: int,
    ) -> None:
        """Queue subtitle following for an isolated segment preview."""
        self._pending_room_word_hl = (segment_idx, char_start, char_end)
        self._start_visibility_throttle_timer(self._room_word_hl_throttle)

    def _flush_room_word_highlight(self) -> None:
        pending = self._pending_room_word_hl
        if pending is None or not hasattr(self, "_room_segment_text"):
            return
        self._pending_room_word_hl = None
        segment_idx, char_start, char_end = pending
        segment = self._selected_voice_room_segment()
        if segment is not None and segment.segment_index == segment_idx:
            self._set_voice_room_segment_text(segment, highlight=(char_start, char_end))

    # ─── Segment preview / chapter playback state ────────────────────────

    def _on_segment_preview_playback_state_changed(self, is_playing: bool) -> None:
        """Reflect isolated-take play/pause state beside the right subtitle stage."""
        if is_playing:
            if self._room_chapter_player is not None and self._room_chapter_player.is_playing():
                # Set the scope first so the chapter player's pause signal does
                # not overwrite the segment status that follows.
                self._voice_room_playback = False
                self._room_chapter_play_requested = False
                self._room_chapter_player.pause()
            self._room_preview_active = True
            segment_idx = self._selected_voice_room_segment_index()
            self._set_room_player_scope("segment", segment_index=segment_idx)
            suffix = f" · 第 {segment_idx + 1} 段" if segment_idx is not None else ""
            self._room_playback_state.setText(f"● 正在试听{suffix} · 字幕跟随")
            self._room_status_badge.setText("单节试听中 · 右侧字幕跟随")
            self._room_status_badge.set_tone("success")
        elif self._room_preview_active:
            self._room_playback_state.setText("● 试听已暂停 · 字幕停在当前位置")

    def _on_segment_preview_finished(self) -> None:
        self._room_word_hl_throttle.stop()
        self._pending_room_word_hl = None
        self._room_preview_active = False
        self._render_voice_room_segment()
        if hasattr(self, "_room_playback_state"):
            self._room_playback_state.setText("● 试听完成 · 字幕待机")

    def _on_chapter_playback_state_changed(self, is_playing: bool) -> None:
        """Keep Voice Room's full-chapter CTA synchronized with the hidden player."""
        if not self._voice_room_playback:
            return
        self._room_chapter_play_requested = is_playing
        self._set_room_full_playback_ui(is_playing, paused=not is_playing)
        if not is_playing and hasattr(self, "_room_status_badge"):
            self._room_status_badge.setText("全章播放已暂停 · 可继续播放")
            self._room_status_badge.set_tone("warning")

    # ─── Progress badge (throttled) ───────────────────────────────────────

    def _on_playback_progress(self, fraction: float, position_ms: int, total_ms: int) -> None:
        """Queue progress badge update; flushed by throttle timer."""
        self._pending_progress = (fraction, position_ms, total_ms)
        self._start_visibility_throttle_timer(self._progress_throttle)

    def _flush_progress_badge(self) -> None:
        """Apply the most recent progress update that was queued during playback."""
        pending = self._pending_progress
        if pending is None:
            return
        self._pending_progress = None
        fraction, position_ms, total_ms = pending
        pct = int(fraction * 100)
        if hasattr(self, "_progress_badge"):
            total_s = total_ms // 1000
            pos_s = position_ms // 1000
            self._progress_badge.setText(f"\u64ad\u653e: {pos_s}s / {total_s}s ({pct}%)")

    # ─── Playback completion ──────────────────────────────────────────────

    def _on_playback_finished(self) -> None:
        """Reset highlighting when playback finishes."""
        # Stop throttle timers so pending updates don't fire after reset.
        self._word_hl_throttle.stop()
        self._progress_throttle.stop()
        self._pending_word_hl = None
        self._pending_progress = None
        self._playback_active = False
        self._playback_segment_idx = -1
        was_voice_room_playback = self._voice_room_playback
        self._voice_room_playback = False
        self._room_chapter_play_requested = False
        self._render_script_html()
        self._render_sync_script_html()
        # Reset avatar highlights
        for btn in self._avatar_buttons.values():
            btn.setChecked(False)
        if was_voice_room_playback and hasattr(self, "_room_play_full_btn"):
            self._room_play_full_btn.setText("▶ 播放全章")
            self._room_playback_state.setText("● 全章播放完成 · 字幕待机")
            self._room_status_badge.setText("全章播放完成")
            self._room_status_badge.set_tone("success")

    # ─── Scroll & sync rendering ──────────────────────────────────────────

    def _scroll_to_segment(self, segment_idx: int) -> None:
        """Scroll the script tab browser to show the given segment."""
        if not self._follow_playback:
            return
        anchor = f"seg_{segment_idx}"
        self._programmatic_scroll = True
        try:
            self._script_browser.scrollToAnchor(anchor)
        finally:
            self._programmatic_scroll = False

    def _scroll_sync_to_segment(self, segment_idx: int) -> None:
        """Scroll the audio tab sync browser to show the given segment."""
        if self._follow_playback and hasattr(self, "_sync_script_browser"):
            anchor = f"seg_{segment_idx}"
            self._programmatic_scroll = True
            try:
                self._sync_script_browser.scrollToAnchor(anchor)
            finally:
                self._programmatic_scroll = False

    def _render_sync_script_html(self) -> None:
        """Render the script in the audio tab sync browser with playback highlight.

        Reuses the same meta/stress/transition rendering helpers as the script
        tab, but shows a compact view (no spinner animation, no character filter
        highlight) suited for real-time playback tracking.
        """
        if not hasattr(self, "_sync_script_browser"):
            return
        if not self._current_script:
            self._set_browser_html(
                self._sync_script_browser,
                f"<p style='color:{qcolor_hex('text.muted')};'>暂无脚本</p>",
                preserve_scroll=False,
            )
            return

        html_parts = [
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 12pt; "
            "line-height: 1.8; padding: 4px;'>"
        ]

        playback_idx = self._playback_segment_idx if self._playback_active else -1

        for seg in self._current_script.segments:
            if seg.transition and seg.transition.label:
                html_parts.append(self._render_transition_divider(seg.transition))

            status = self._segment_status.get(seg.segment_index, "pending")
            color = _segment_status_color(status)
            is_active = seg.segment_index == playback_idx

            prefix = self._render_segment_prefix(seg, color)
            status_icon = "✅" if status == "completed" else ("❌" if status == "failed" else "⚪")
            meta_html = self._render_segment_meta(seg)
            body_html = self._render_segment_text(seg, color)

            bg_style = ""
            if is_active:
                bg_style = (
                    f"background:{qcolor_rgba('status.info', 0.18)}; "
                    f"border-left:3px solid {qcolor_hex('status.info')}; "
                    "border-radius: 4px; padding: 4px 8px; "
                    "margin: 2px 0;"
                )

            anchor = f"<a name='seg_{seg.segment_index}'></a>"
            active_indicator = ""
            if is_active:
                active_indicator = (
                    f"<span style='color:{qcolor_hex('status.info')}; font-size:13pt;'>▶ </span>"
                )

            html_parts.append(
                f"<div style='{bg_style}'>{anchor}{active_indicator}"
                f"{status_icon} {prefix} {meta_html}"
                f"<span style='color:{color};'>{body_html}</span></div>"
            )

        html_parts.append("</div>")
        self._set_browser_html(
            self._sync_script_browser,
            "".join(html_parts),
            preserve_scroll=True,
        )
