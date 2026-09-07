"""Voice Studio script rendering mixin.

Extracted from ``page.py`` to reduce single-file size (M0 refactor).  All
methods here operate on ``self._*`` state initialized by ``VoiceStudioPage``,
so this mixin must be composed with it and never instantiated standalone.

Covers:
- Avatar button synchronization with the active voice team.
- Dubbing-script HTML rendering (segment prefixes, status icons, backgrounds,
  emotion/tone meta, stress-word highlighting, paralinguistic icons, scene
  dividers, sound-design mix plan).
- Speaker-review badge state.
- Script browser HTML replacement with stream-anchor preservation.
- Synthesis / script-generation spinner animation ticks.
- Streaming director-card preview rendering.
"""

from __future__ import annotations

import html as html_lib
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QTextBrowser

from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.pages.voice_studio.helpers import (
    HTML_BODY_FONT_FAMILY as _HTML_BODY_FONT_FAMILY,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    PARALINGUISTIC_ICONS as _PARALINGUISTIC_ICONS,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    ChapterScriptStream as _ChapterScriptStream,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    emotion_display as _emotion_display,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    incremental_detect_stream_anomalies as _incremental_detect_stream_anomalies,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    incremental_json_array_objects as _incremental_json_array_objects,
)
from novel_forge.desktop.pages.voice_studio.helpers import (
    segment_status_color as _segment_status_color,
)
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba
from novel_forge.tts.schemas import DubbingScript, EmotionTag, SegmentType
from novel_forge.tts.script_integrity import unresolved_speaker_indices

__all__ = ("ScriptRenderMixin",)


class ScriptRenderMixin:
    """Visual script rendering helpers for :class:`VoiceStudioPage`.

    These methods read and mutate ``self._*`` fields owned by the page; they
    are intentionally kept as bound methods so the existing signal wiring
    (avatar clicks, follow-playback checkbox, animation timers) continues to
    work without indirection.
    """

    _highlighted_character: str | None
    _script_streams: dict[int, _ChapterScriptStream]
    _synth_anim_phase: int
    _stream_render_scheduled: bool = False
    _stream_last_render_at: float = 0.0
    _speaker_review_signature: tuple[Any, ...] | None = None

    # Minimum interval between streaming preview re-renders.  LLM stream
    # deltas arrive at ~50/s; coalescing to 400 ms keeps the UI responsive
    # while avoiding a full HTML rebuild on every token chunk.  Combined with
    # the segment-count render dedup in _render_script_stream(), the actual
    # rebuild frequency is ~1 per new segment (typically 3-8 s apart) rather
    # than every 250 ms regardless of visual change.
    _STREAM_RENDER_MIN_INTERVAL_S: float = 0.40

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any: ...

    # ─── Avatars ──────────────────────────────────────────────────────────

    def _update_avatars(self) -> None:
        """Update character avatar buttons from voice team."""
        if not hasattr(self, "_avatar_layout_inner"):
            self._avatars_dirty = True
            return
        self._avatars_dirty = False
        while self._avatar_layout_inner.count():
            item = self._avatar_layout_inner.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        self._avatar_buttons.clear()

        if not self._voice_team:
            return

        narrator_btn = QPushButton("旁白")
        narrator_btn.setObjectName("avatarTag")
        narrator_btn.setProperty("character_id", "")
        narrator_btn.setCheckable(True)
        narrator_btn.clicked.connect(lambda: self._on_avatar_clicked(""))
        self._avatar_layout_inner.addWidget(narrator_btn)
        self._avatar_buttons[""] = narrator_btn

        for entry in self._voice_team.entries:
            btn = QPushButton(f"{entry.character_name}")
            btn.setObjectName("avatarTag")
            btn.setProperty("character_id", entry.character_id)
            btn.setCheckable(True)
            btn.setToolTip(
                f"音色: {entry.voice_id}\n"
                f"Provider: {entry.provider.value}\n"
                f"质量: {entry.quality_score:.0%}"
            )
            btn.clicked.connect(
                lambda checked, cid=entry.character_id: self._on_avatar_clicked(cid)
            )
            self._avatar_layout_inner.addWidget(btn)
            self._avatar_buttons[entry.character_id] = btn

        self._avatar_layout_inner.addStretch()

    def _on_avatar_clicked(self, character_id: str) -> None:
        """Handle avatar button click - highlight character's lines."""
        if self._highlighted_character == character_id:
            self._highlighted_character = None
            for btn in self._avatar_buttons.values():
                btn.setChecked(False)
        else:
            self._highlighted_character = character_id
            for cid, btn in self._avatar_buttons.items():
                btn.setChecked(cid == character_id)
        self._render_script_html()

    # ─── Script HTML rendering ────────────────────────────────────────────

    def _render_script_html(self, *, preserve_scroll: bool = True) -> None:
        """Render the dubbing script as colored HTML with segment anchors.

        Enriches each segment with: emotion tag (colored dot + label), tone hint,
        stress-word highlighting, paralinguistic icons, and scene-transition
        divider lines.
        """
        if not hasattr(self, "_script_browser"):
            return
        if not self._current_script:
            self._set_browser_html(
                self._script_browser,
                f"<p style='color:{qcolor_hex('text.muted')};'>\u6682\u65e0\u811a\u672c</p>",
                preserve_scroll=False,
            )
            self._refresh_seg_metrics(None)
            self._refresh_speaker_review_ui()
            return

        html_parts = [
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size: 13pt; "
            "line-height: 1.8; padding: 4px;'>"
        ]

        playback_idx = self._playback_segment_idx if self._playback_active else -1
        unresolved_speakers = set(unresolved_speaker_indices(self._current_script))

        for seg in self._current_script.segments:
            # Scene transition divider (rendered before the segment)
            if seg.transition and seg.transition.label:
                html_parts.append(self._render_transition_divider(seg.transition))

            status = self._segment_status.get(seg.segment_index, "pending")
            color = _segment_status_color(status)
            is_active = seg.segment_index == playback_idx

            is_highlighted = (
                self._highlighted_character is not None
                and seg.character_id == self._highlighted_character
            )

            prefix = self._render_segment_prefix(seg, color)
            status_icon = self._render_status_icon(status)
            bg_style = self._render_segment_bg(is_active, is_highlighted, status)
            needs_speaker_review = seg.segment_index in unresolved_speakers
            if needs_speaker_review and not is_active:
                bg_style = (
                    f"background:{qcolor_rgba('status.danger', 0.10)}; "
                    f"border-left:4px solid {qcolor_hex('status.danger.alt')}; "
                    "border-radius:4px; padding:5px 8px; margin:3px 0;"
                )

            # Emotion + tone-hint tags row
            meta_html = self._render_segment_meta(seg)

            # Stress-word highlighting in the text body
            body_html = self._render_segment_text(seg, color)

            # Paralinguistic icons
            para_html = self._render_paralinguistic_icons(seg)

            anchor = f"<a name='seg_{seg.segment_index}'></a>"
            edit_link = (
                f" <a href='edit-segment:{seg.segment_index}' "
                f"style='font-size:10pt; color:{qcolor_hex('status.info')}; text-decoration:none;'>"
                f"{'去复核' if needs_speaker_review else '编辑'}</a>"
            )
            review_label = (
                f"<span style='color:{qcolor_hex('status.danger.deep')}; "
                f"background:{qcolor_rgba('status.danger', 0.14)}; font-size:9pt; "
                "font-weight:bold; border-radius:6px; padding:1px 6px;'>说话人待复核</span> "
                if needs_speaker_review
                else ""
            )
            active_indicator = ""
            if is_active:
                active_indicator = f"<span style='color:{qcolor_hex('status.info')}; font-size:14pt;'>\u25b6 </span>"

            html_parts.append(
                f"<div style='{bg_style}'>{anchor}{active_indicator}"
                f"{status_icon} {review_label}{prefix} {meta_html}"
                f"<span style='color:{color};'>{body_html}</span>{para_html}{edit_link}</div>"
            )

        html_parts.append(self._render_sound_design(self._current_script))

        html_parts.append("</div>")
        self._set_browser_html(
            self._script_browser,
            "".join(html_parts),
            preserve_scroll=preserve_scroll,
        )
        self._refresh_seg_metrics(self._current_script)
        self._refresh_speaker_review_ui()

    def _refresh_speaker_review_ui(self) -> None:
        if not hasattr(self, "_speaker_review_bar"):
            return
        script = self._current_script
        unresolved = unresolved_speaker_indices(script) if script is not None else ()
        reviewed = 0
        if script is not None:
            audit = script.metadata.get("speaker_adjudication")
            if isinstance(audit, dict):
                manual_reviews = audit.get("manual_reviews")
                reviewed = len(manual_reviews) if isinstance(manual_reviews, list) else 0
        segment_order = (
            tuple(segment.segment_index for segment in script.segments)
            if script is not None
            else ()
        )
        signature = (script is not None, segment_order, unresolved, reviewed)
        if self._speaker_review_signature == signature:
            return
        self._speaker_review_signature = signature
        previous_tone = self._speaker_review_bar.property("tone")
        if script is None:
            self._speaker_review_badge.setText("说话人复核")
            self._speaker_review_badge.set_tone("muted")
            self._speaker_review_hint.setText("生成脚本后，这里会显示需要人工确认的具体片段。")
            self._speaker_review_btn.setVisible(False)
            self._speaker_review_bar.setProperty("tone", "muted")
        else:
            if unresolved:
                segment_positions = {
                    segment.segment_index: position
                    for position, segment in enumerate(script.segments, start=1)
                }
                first_segment_id = unresolved[0]
                first_position = segment_positions[first_segment_id]
                self._speaker_review_badge.setText(f"待复核 {len(unresolved)} 段")
                self._speaker_review_badge.set_tone("danger")
                self._speaker_review_hint.setText(
                    f"红色片段尚未确认说话人；首处是第 {first_position} 段。"
                    "完成后才会开放配音室试听与正式合成。"
                )
                self._speaker_review_btn.setText(f"复核第 {first_position} 段")
                self._speaker_review_btn.setVisible(True)
                self._speaker_review_bar.setProperty("tone", "danger")
            else:
                self._speaker_review_badge.setText("说话人已确认")
                self._speaker_review_badge.set_tone("success")
                suffix = f" · 已记录 {reviewed} 次人工确认" if reviewed else ""
                self._speaker_review_hint.setText(f"本章说话人复核已完成{suffix}，可以进入配音室。")
                self._speaker_review_btn.setVisible(False)
                self._speaker_review_bar.setProperty("tone", "success")
        if self._speaker_review_bar.property("tone") != previous_tone:
            self._speaker_review_bar.style().unpolish(self._speaker_review_bar)
            self._speaker_review_bar.style().polish(self._speaker_review_bar)

    def _on_review_pending_speakers(self) -> None:
        if self._current_script is None:
            return
        unresolved = unresolved_speaker_indices(self._current_script)
        if unresolved:
            self._on_edit_script(unresolved[0])

    # ─── Browser HTML replacement ─────────────────────────────────────────

    def _set_browser_html(
        self,
        browser: QTextBrowser,
        content: str,
        *,
        preserve_scroll: bool,
    ) -> None:
        """Replace script markup while preserving a reader's stream anchor.

        Never restore a relative scrollbar ratio here: when streaming adds a
        lot of content, a fixed ratio maps to a much lower line and visibly
        outruns the model output.
        """
        scrollbar = browser.verticalScrollBar()
        previous_value = scrollbar.value()
        stream_follow = (
            self._script_stream_follow
            if browser is getattr(self, "_script_browser", None)
            else None
        )
        stream_anchor = (
            stream_follow.capture_content_anchor()
            if preserve_scroll and stream_follow is not None
            else None
        )
        self._programmatic_scroll = True
        try:
            update_browser_html(browser, content)
            if stream_anchor is not None and stream_follow is not None:
                stream_follow.restore_content_anchor(stream_anchor)
            elif preserve_scroll:
                scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        finally:
            self._programmatic_scroll = False

    def _on_script_browser_manually_scrolled(self, _action: int) -> None:
        """Pause auto-follow only after the reader deliberately moves either script view."""
        if self._playback_active and not self._programmatic_scroll:
            self._set_playback_follow(False)

    def _on_follow_playback_toggled(self, checked: bool) -> None:
        self._set_playback_follow(checked)
        if checked and self._playback_active and self._playback_segment_idx >= 0:
            self._scroll_to_segment(self._playback_segment_idx)
            self._scroll_sync_to_segment(self._playback_segment_idx)

    def _set_playback_follow(self, enabled: bool) -> None:
        self._follow_playback = enabled
        checkbox = getattr(self, "_follow_playback_check", None)
        if checkbox is not None and checkbox.isChecked() != enabled:
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)

    # ─── Segment fragment renderers ───────────────────────────────────────

    def _render_segment_prefix(self, seg: Any, color: str) -> str:
        """Render the type/character prefix for a segment."""
        if seg.segment_type == SegmentType.NARRATION:
            return f"<span style='color:{qcolor_hex('text.muted')};'>[旁白]</span>"
        elif seg.segment_type == SegmentType.DIALOGUE:
            name = html_lib.escape(str(seg.character_name or "角色"))
            return f"<span style='color:{color}; font-weight:bold;'>[{name}]</span>"
        elif seg.segment_type == SegmentType.INNER_THOUGHT:
            name = html_lib.escape(str(seg.character_name or "角色"))
            return f"<span style='color:{qcolor_hex('motif.purple')};'>[{name}·内心]</span>"
        elif seg.segment_type == SegmentType.BGM:
            return f"<span style='color:{qcolor_hex('status.warning')};'>[BGM]</span>"
        elif seg.segment_type == SegmentType.SFX:
            return f"<span style='color:{qcolor_hex('accent.warm')};'>[音效]</span>"
        elif seg.segment_type == SegmentType.SILENCE:
            return f"<span style='color:{qcolor_hex('text.disabled')};'>[静音]</span>"
        return ""

    def _render_status_icon(self, status: str) -> str:
        """Render the status icon for a segment (with spinner animation)."""
        if status == "synthesizing":
            phase = self._synth_anim_phase % 8
            dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
            spinner = dots[phase]
            return f"<span style='color:{qcolor_hex('status.info')};'>{spinner}</span>"
        elif status == "completed":
            return "✅"
        elif status == "failed":
            return "❌"
        elif status == "skipped":
            return ""
        return "⚪"

    def _render_segment_bg(self, is_active: bool, is_highlighted: bool, status: str) -> str:
        """Render the background style for a segment."""
        if is_active:
            return (
                f"background:{qcolor_rgba('status.info', 0.18)}; "
                f"border-left:3px solid {qcolor_hex('status.info')}; "
                "border-radius: 4px; padding: 4px 8px; "
                "margin: 2px 0;"
            )
        if is_highlighted:
            return (
                f"background:{qcolor_rgba('status.info', 0.08)}; "
                "border-radius:4px; padding:2px 4px;"
            )
        if status == "synthesizing":
            return (
                f"background:{qcolor_rgba('status.info', 0.06)}; "
                "border-radius:4px; padding:2px 4px;"
            )
        return ""

    def _render_segment_meta(self, seg: Any) -> str:
        """Render the effective performance direction for a segment."""
        parts: list[str] = []
        emo = getattr(seg, "emotion", None)
        if emo and emo != EmotionTag.NEUTRAL:
            label, ecolor = _emotion_display(emo)
            parts.append(
                f"<span style='color:{ecolor}; font-size:10pt; font-weight:bold;'>● {label}</span>"
            )
        # Sub-emotion (emotional gradient)
        sub = getattr(seg, "sub_emotion", None)
        if sub and sub != EmotionTag.NEUTRAL:
            _, scolor = _emotion_display(sub)
            parts.append(f"<span style='color:{scolor}; font-size:9pt;'>→ {sub.value}</span>")
        tone = getattr(seg, "tone_hint", "") or ""
        if tone:
            parts.append(
                f"<span style='color:{qcolor_hex('status.info')}; font-size:10pt; "
                f"background:{qcolor_hex('bg.hover.accent')}; border-radius:6px; padding:1px 6px;'>"
                f"{html_lib.escape(str(tone))}</span>"
            )
        speed = getattr(seg, "speed_override", None)
        if speed is not None:
            parts.append(
                f"<span style='color:{qcolor_hex('text.muted.strong')}; font-size:9pt;'>{float(speed):.2g}×</span>"
            )
        volume = getattr(seg, "vol_override", None)
        if volume is not None:
            parts.append(
                f"<span style='color:{qcolor_hex('text.muted.strong')}; font-size:9pt;'>音量 {float(volume):.2g}×</span>"
            )
        pitch = getattr(seg, "pitch_override", None)
        if pitch is not None:
            parts.append(
                f"<span style='color:{qcolor_hex('text.muted.strong')}; font-size:9pt;'>音高 {int(pitch):+d}st</span>"
            )
        distance = str(getattr(seg, "narrator_distance", "") or "")
        if distance:
            distance_label = {"close": "近距旁白", "medium": "中距旁白", "distant": "远距旁白"}.get(
                distance, distance
            )
            parts.append(
                f"<span style='color:{qcolor_hex('text.muted.strong')}; font-size:9pt;'>{distance_label}</span>"
            )
        effect = getattr(seg, "voice_effect", None)
        effect_name = str(getattr(effect, "sound_effects", "") or "")
        if effect_name:
            effect_label = {
                "spacious_echo": "空旷回声",
                "auditorium_echo": "礼堂广播",
                "lofi_telephone": "电话失真",
                "robotic": "机械声",
            }.get(effect_name, effect_name)
            parts.append(
                f"<span style='color:{qcolor_hex('motif.purple')}; font-size:9pt;'>{effect_label}</span>"
            )
        if not parts:
            return ""
        return "<span style='margin-left:6px;'>" + " ".join(parts) + "</span> "

    def _render_sound_design(self, script: DubbingScript) -> str:
        """Render BGM, environment and SFX cues as an explicit mix plan."""
        if not (script.soundscapes or script.bgm_suggestions or script.sfx_cues):
            return ""
        parts = [
            f"<div style='margin-top:14px; border-top:1px solid {qcolor_hex('separator')}; padding-top:9px;'>",
            f"<div style='font-size:11pt; font-weight:600; color:{qcolor_hex('text.secondary')};'>"
            "声场与混音设计 "
            f"<a href='edit-sound-design:' style='font-size:9pt; color:{qcolor_hex('status.info')}; "
            "text-decoration:none;'>编辑声场</a></div>",
        ]
        for soundscape in script.soundscapes:
            end = (
                f"至片段 {soundscape.end_segment_index + 1}"
                if soundscape.end_segment_index is not None
                else "延续至场景结束"
            )
            soundscape_start = (
                soundscape.start_segment_index + 1
                if soundscape.start_segment_index is not None
                else 1
            )
            parts.append(
                f"<div style='margin:5px 0; color:{qcolor_hex('text.body')}'>"
                f"<b>[环境] {html_lib.escape(soundscape.name)}</b> · 片段 {soundscape_start} {end} · "
                f"音量 {soundscape.volume:.0%} · 对白下压 {soundscape.ducking_db:g}dB · "
                f"{'循环' if soundscape.loop else '不循环'}<br>"
                f"<span style='color:{qcolor_hex('text.muted')}'>"
                f"{html_lib.escape(soundscape.description)}</span></div>"
            )
        for bgm in script.bgm_suggestions:
            bgm_start = (
                f"片段 {bgm.start_segment_index + 1}"
                if bgm.start_segment_index is not None
                else "章节开始"
            )
            end = (
                f"至片段 {bgm.end_segment_index + 1}"
                if bgm.end_segment_index is not None
                else "至章节末尾"
            )
            parts.append(
                f"<div style='margin:5px 0; color:{qcolor_hex('text.body')}'>"
                f"<b>[BGM] {html_lib.escape(bgm.track_name or bgm.mood or '背景音乐')}</b> · "
                f"{bgm_start} {end} · 音量 {bgm.volume:.0%} · 淡入 {bgm.fade_in_ms}ms · "
                f"淡出 {bgm.fade_out_ms}ms · 对白下压 {bgm.ducking_db:g}dB</div>"
            )
        for sfx in script.sfx_cues:
            anchor = (
                f"片段 {sfx.trigger_segment_index + 1}"
                if sfx.trigger_segment_index is not None
                else f"{sfx.trigger_ms / 1000:.1f}s"
            )
            parts.append(
                f"<div style='margin:5px 0; color:{qcolor_hex('text.body')}'>"
                f"<b>[音效] {html_lib.escape(sfx.effect_name)}</b> · {anchor} · "
                f"音量 {sfx.volume:.0%} · {sfx.duration_ms}ms<br>"
                f"<span style='color:{qcolor_hex('text.muted')};'>{html_lib.escape(sfx.description)}</span></div>"
            )
        parts.append("</div>")
        return "".join(parts)

    def _render_segment_text(self, seg: Any, color: str) -> str:
        """Render segment text with stress words highlighted."""
        text = str(seg.text or "")
        stress_words = getattr(seg, "stress_words", None) or []
        if not stress_words:
            return html_lib.escape(text)
        # Highlight first non-overlapping occurrences while escaping all model text.
        matches: list[tuple[int, int]] = []
        for raw_word in stress_words:
            word = str(raw_word or "")
            if not word:
                continue
            start = text.find(word)
            end = start + len(word)
            if start >= 0 and not any(
                start < old_end and end > old_start for old_start, old_end in matches
            ):
                matches.append((start, end))
        if not matches:
            return html_lib.escape(text)
        cursor = 0
        parts: list[str] = []
        for start, end in sorted(matches):
            parts.append(html_lib.escape(text[cursor:start]))
            parts.append(
                f"<span style='color:{qcolor_hex('status.warning')}; font-weight:bold; "
                f"background:{qcolor_rgba('status.warning', 0.12)}; border-radius:3px; "
                f"padding:0 2px;'>{html_lib.escape(text[start:end])}</span>"
            )
            cursor = end
        parts.append(html_lib.escape(text[cursor:]))
        return "".join(parts)

    def _render_paralinguistic_icons(self, seg: Any) -> str:
        """Render paralinguistic tag icons appended after the text."""
        tags = getattr(seg, "paralinguistic_tags", None) or []
        if not tags:
            return ""
        icons: list[str] = []
        for tag in tags:
            glyph = _PARALINGUISTIC_ICONS.get(tag.tag_type, "•")
            title = tag.description or tag.tag_type
            icons.append(
                f"<span style='font-size:11pt;' title='{html_lib.escape(str(title), quote=True)}'>"
                f"{glyph}</span>"
            )
        if not icons:
            return ""
        return " <span style='margin-left:4px;'>" + " ".join(icons) + "</span>"

    def _render_transition_divider(self, transition: Any) -> str:
        """Render a scene-transition divider line with a label."""
        label = transition.label or transition.transition_type
        icon_map = {
            "time_skip": "⏱",
            "location_change": "📍",
            "pov_shift": "👁",
            "tone_shift": "🎭",
            "flashback": "💭",
        }
        icon = icon_map.get(transition.transition_type, "─")
        return (
            f"<div style='text-align:center; margin:10px 0 6px; color:{qcolor_hex('text.muted')}; "
            "font-size:10pt;'>"
            f"<span style='letter-spacing:2px;'>━━━━</span> "
            f"<span style='color:{qcolor_hex('text.muted.strong')};'>{icon} {html_lib.escape(str(label))}</span> "
            f"<span style='letter-spacing:2px;'>━━━━</span>"
            "</div>"
        )

    # ─── Metrics & animation ──────────────────────────────────────────────

    def _refresh_seg_metrics(self, script: DubbingScript | None) -> None:
        """Refresh segment-count metric cards on the script tab."""
        if not hasattr(self, "_seg_metric_total"):
            return
        if not script or not script.segments:
            self._seg_metric_total.set_content("片段总数", "0", "")
            self._seg_metric_narration.set_content("旁白", "0", "")
            self._seg_metric_dialogue.set_content("对白", "0", "")
            self._seg_metric_sfx.set_content("场景声音", "0", "")
            return
        total = len(script.segments)
        narration = sum(1 for s in script.segments if s.segment_type == SegmentType.NARRATION)
        dialogue = sum(
            1
            for s in script.segments
            if s.segment_type in (SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT)
        )
        sfx = sum(
            1
            for s in script.segments
            if s.segment_type in (SegmentType.SFX, SegmentType.SILENCE, SegmentType.BGM)
        )
        self._seg_metric_total.set_content("片段总数", str(total), "")
        self._seg_metric_narration.set_content("旁白", str(narration), "")
        self._seg_metric_dialogue.set_content("对白", str(dialogue), "")
        sound_cues = sfx + len(script.bgm_suggestions) + len(script.soundscapes)
        self._seg_metric_sfx.set_content("场景声音", str(sound_cues), "")

    def _tick_synth_animation(self) -> None:
        """Timer callback to animate synthesizing spinner."""
        self._synth_anim_phase += 1
        self._render_script_html()

    def _tick_script_gen_animation(self) -> None:
        """Advance legacy animation state without rewriting the browser.

        Kept as a compatibility hook for restored sessions and older tests.
        Script production is now event-driven; repeatedly calling setHtml()
        here reset the viewport every 400 ms and made the whole page appear to
        refresh while autonomous production was running.
        """
        chapter = self._generating_chapter
        if chapter is None:
            return
        stream = self._script_streams.get(chapter)
        if stream is None or stream.active:
            return
        stream.dots = (stream.dots + 1) % 4

    # ─── Streaming preview ────────────────────────────────────────────────

    def _schedule_stream_render(self) -> None:
        """Coalesce high-frequency stream deltas into bounded re-renders.

        The first delta renders immediately so the user sees content right
        away; subsequent renders are throttled to one per 120 ms window.
        """
        now = time.monotonic()
        elapsed = now - self._stream_last_render_at
        if elapsed >= self._STREAM_RENDER_MIN_INTERVAL_S:
            self._stream_last_render_at = now
            self._render_script_stream()
            return
        if self._stream_render_scheduled:
            return
        self._stream_render_scheduled = True
        delay_ms = int((self._STREAM_RENDER_MIN_INTERVAL_S - elapsed) * 1000) + 1
        QTimer.singleShot(delay_ms, self._flush_scheduled_stream_render)

    def _flush_scheduled_stream_render(self) -> None:
        """Execute a previously coalesced stream render."""
        self._stream_render_scheduled = False
        self._stream_last_render_at = time.monotonic()
        self._render_script_stream()

    def _render_script_stream(self) -> None:
        """Render complete director cards as structured JSON arrives.

        Renders from the currently-displayed chapter's stream buffer. When
        the displayed chapter has no active stream, this is a no-op so the
        on-disk script rendering (_render_script_html) is left untouched.

        Performance: the expensive full-document HTML rebuild is SKIPPED
        when the parsed segment count has not changed since the last render.
        During streaming, most render ticks only accumulate partial text for
        the current segment — no visual change occurs until a new segment
        completes.  The progress bar above already shows real-time character
        reception, so the user still perceives activity.

        Anomaly detection: duplicated segment text, JSON structural echoes,
        and character inflation are flagged inline so the user knows the
        preview may be replaced by the backend's validated output.
        """
        if not hasattr(self, "_script_browser"):
            return
        chapter = self._active_chapter_number
        stream = self._script_streams.get(chapter)
        if stream is None:
            return
        segments = _incremental_json_array_objects(stream, "segments")

        # ── Render dedup: skip full HTML rebuild when nothing visual changed ──
        # The only visual change in the streaming preview is a new segment
        # appearing.  If segment count is unchanged and no forced render is
        # pending, skip the expensive clear()+insertHtml() cycle entirely.
        if (
            not stream._force_render
            and len(segments) == stream._last_rendered_segment_count
        ):
            return
        stream._force_render = False
        stream._last_rendered_segment_count = len(segments)
        source_chars = int(stream.progress_data.get("source_chars") or 0)
        # 用段落纯文本长度而非原始 JSON 流长度做膨胀比较，
        # 避免 JSON 结构元数据（segment_index / emotion / tone_hint 等）
        # 天然产生的 2-3 倍开销触发误报。
        segment_text_chars = sum(len(str(item.get("text") or "")) for item in segments)
        anomalies = _incremental_detect_stream_anomalies(
            stream, segments, segment_text_chars, source_chars
        )
        duplicate_set = set(anomalies.duplicate_indices)
        json_echo_set = set(anomalies.json_echo_indices)

        title_suffix = (
            " · 检测到异常，终态以校验为准"
            if anomalies.has_anomalies
            else " · 预览待校验"
        )
        status_line = (
            f"已接收 {len(stream.text)} 字符 · 已完成 {len(segments)} 段结构"
        )
        if anomalies.char_inflation:
            status_line += " · ⚠ 输出量异常偏大"
        parts = [
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; padding:10px 12px;'>",
            f"<div style='color:{qcolor_hex('status.info')}; font-size:13pt; font-weight:600;'>"
            f"AI 声音导演正在逐段编排{html_lib.escape(title_suffix)}</div>",
            f"<div style='color:{qcolor_hex('text.muted')}; font-size:10pt; margin:3px 0 10px;'>"
            f"{html_lib.escape(status_line)}</div>",
        ]
        type_labels = {
            "narration": "旁白",
            "dialogue": "对白",
            "inner_thought": "内心独白",
            "bgm": "BGM",
            "sfx": "音效",
            "silence": "停顿",
        }
        for index, item in enumerate(segments):
            kind = str(item.get("segment_type") or "narration")
            speaker = str(item.get("character_name") or type_labels.get(kind, kind))
            text = str(item.get("text") or "")
            emotion = str(item.get("emotion") or "neutral")
            tone = str(item.get("tone_hint") or "")
            speed = item.get("speed_override")
            meta = [type_labels.get(kind, kind), emotion]
            if tone:
                meta.append(tone)
            if speed is not None:
                meta.append(f"{speed}×")

            is_duplicate = index in duplicate_set
            is_json_echo = index in json_echo_set
            border_color = (
                qcolor_hex("status.warning") if is_duplicate else qcolor_hex("status.info")
            )
            bg_color = (
                qcolor_rgba("status.warning", 0.06)
                if is_duplicate
                else qcolor_rgba("status.info", 0.06)
            )
            anomaly_badges = ""
            if is_duplicate:
                anomaly_badges += (
                    f" <span style='color:{qcolor_hex('status.warning')}; font-size:9pt; "
                    f"font-weight:bold; background:{qcolor_rgba('status.warning', 0.12)}; "
                    "border-radius:6px; padding:1px 6px;'>⚠ 与上段重复</span>"
                )
            if is_json_echo:
                anomaly_badges += (
                    f" <span style='color:{qcolor_hex('status.danger')}; font-size:9pt; "
                    f"font-weight:bold; background:{qcolor_rgba('status.danger', 0.12)}; "
                    "border-radius:6px; padding:1px 6px;'>⚠ 结构异常</span>"
                )
            # Qt rich text does not support CSS opacity; use a muted
            # foreground colour to visually demote structurally-broken text.
            text_color = (
                qcolor_hex("text.muted") if is_json_echo else qcolor_hex("text.primary")
            )
            parts.append(
                f"<div style='border-left:3px solid {border_color}; background:{bg_color};"
                " border-radius:5px; padding:7px 9px; margin:5px 0;'>"
                f"<div style='font-size:10pt; color:{qcolor_hex('text.muted.strong')};'>#{index + 1:02d} · "
                f"{html_lib.escape(speaker)} · {html_lib.escape(' · '.join(meta))}{anomaly_badges}</div>"
                f"<div style='font-size:12pt; color:{text_color}; margin-top:2px;'>"
                f"{html_lib.escape(text)}</div></div>"
            )
        if not segments:
            parts.append(
                f"<div style='color:{qcolor_hex('text.muted')}; padding:24px 4px;'>正在识别旁白、角色及场景边界…</div>"
            )
        if anomalies.has_anomalies and segments:
            parts.append(
                f"<div style='color:{qcolor_hex('status.warning')}; font-size:10pt; "
                f"margin:8px 0 2px; padding:6px 8px; background:{qcolor_rgba('status.warning', 0.08)}; "
                "border-radius:5px;'>"
                "检测到模型输出异常；流结束后将自动校验，未通过的内容会被安全替换。</div>"
            )
        parts.append("</div>")
        self._set_browser_html(self._script_browser, "".join(parts), preserve_scroll=True)
        hint = getattr(self, "_script_source_hint", None)
        if hint is not None:
            hint.setText(f"真实流式生成 · {len(segments)} 段已就绪")
            hint.setProperty("state", "streaming")
            hint.style().unpolish(hint)
            hint.style().polish(hint)

    def _get_or_create_stream(self, chapter: int) -> _ChapterScriptStream:
        """Get or create the per-chapter stream state for dubbing script generation."""
        stream = self._script_streams.get(chapter)
        if stream is None:
            stream = _ChapterScriptStream()
            self._script_streams[chapter] = stream
        return stream

    def _render_script_gen_placeholder(self, chapter: int) -> None:
        """Render the initial 'analyzing script' placeholder for one chapter.

        Shared by the animation timer and the chapter-switch path so the
        placeholder markup stays in one place.
        """
        if not hasattr(self, "_script_browser"):
            return
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size:13pt; color:{qcolor_hex('status.info')};"
            " text-align: center; padding: 40px;'>"
            "<div style='font-size: 18pt; margin-bottom: 12px;'>📝</div>"
            "正在分析脚本…<br>"
            f"<span style='font-size:10pt; color:{qcolor_hex('text.muted')};'>"
            "进度会在上方按真实处理阶段更新，脚本区不会自动刷屏</span></div>"
        )
        self._set_browser_html(self._script_browser, html, preserve_scroll=False)
