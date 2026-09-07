"""Tests for the carry-forward hard gate and CarryForwardItem schema.

Reproduces the Ch14→15 "八个字录音" failure: a previous chapter's open
must_carry_forward item with zero textual trace in the current chapter must
block archive (or be repaired), while items marked abandoned/deferred are the
intentional-ellipsis escape hatch.
"""

from __future__ import annotations

import types

import pytest

from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import CarryForwardItem, ChapterExitState
from novel_forge.pipeline.long.stages.finalize_checks import (
    _enforce_archive_hard_quality_blocks,
    _unclosed_carry_forward_items,
)
from novel_forge.pipeline.steps.continuity_eval.local_checks import _LocalChecks


def _outline(chapter_number: int = 15) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=chapter_number,
        title="测试章",
        goal="回应上一章遗留",
        beats_summary=["开场", "转折", "收束"],
    )


def _packet_with_previous_carry(items: list[CarryForwardItem]) -> ChapterStatePacket:
    return ChapterStatePacket(
        chapter_number=15,
        chapter_outline=_outline(),
        previous_exit_state=ChapterExitState(
            chapter_number=14,
            must_carry_forward=items,
        ),
        previous_chapter_ending="上一章的结尾文本。",
    )


def _runner() -> types.SimpleNamespace:
    settings = types.SimpleNamespace(
        long_continuity_hard_block_threshold=4.0,
        long_causal_hard_block_threshold=4.0,
        long_min_accept_score=5.0,
    )
    return types.SimpleNamespace(_settings=settings, _on_step=lambda *a, **k: None)


class TestCarryForwardItemSchema:
    def test_legacy_string_upgrades_to_open_item(self) -> None:
        ces = ChapterExitState(chapter_number=1, must_carry_forward=["八个字录音"])
        assert len(ces.must_carry_forward) == 1
        item = ces.must_carry_forward[0]
        assert item.text == "八个字录音"
        assert item.status == "open"

    def test_structured_dict_preserves_status(self) -> None:
        ces = ChapterExitState(
            chapter_number=1,
            must_carry_forward=[
                {"text": "放弃的线索", "status": "abandoned", "abandon_reason": "故意留白"},
                {"text": "推迟的线索", "status": "deferred", "deferred_to_chapter": 20},
            ],
        )
        assert ces.must_carry_forward[0].status == "abandoned"
        assert ces.must_carry_forward[0].abandon_reason == "故意留白"
        assert ces.must_carry_forward[1].status == "deferred"
        assert ces.must_carry_forward[1].deferred_to_chapter == 20

    def test_packet_upgrades_legacy_strings(self) -> None:
        pkt = ChapterStatePacket(chapter_number=2, chapter_outline=_outline(2), must_carry_forward=["a", "b"])
        assert all(isinstance(i, CarryForwardItem) for i in pkt.must_carry_forward)
        assert pkt.must_carry_forward[0].text == "a"


class TestUnclosedCarryForwardDetection:
    def test_open_item_missing_in_text_is_unclosed(self) -> None:
        """The Ch14→15 scenario: open carry-forward with zero textual trace."""
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="沈鹿溪录下但未保存的八个字：「那个视频不是完整的」")]
        )
        current_text = "这一章完全没有提及录音或视频，转向了别的话题。"
        unclosed = _unclosed_carry_forward_items(pkt, current_text)
        assert len(unclosed) == 1
        assert "八个字" in unclosed[0]

    def test_open_item_present_in_text_is_closed(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="信号窗口只剩最后五分钟")]
        )
        current_text = "她看了一眼手机，信号窗口只剩最后五分钟，必须做决定。"
        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_previous_ending_match_alone_does_not_close_item(self) -> None:
        """The current chapter must respond; prior-ending presence is not enough."""
        item = CarryForwardItem(text="沈鹿溪录下但未保存的八个字")
        pkt = ChapterStatePacket(
            chapter_number=15,
            chapter_outline=_outline(),
            previous_exit_state=ChapterExitState(
                chapter_number=14,
                must_carry_forward=[item],
            ),
            previous_chapter_ending="上一章最后明确写到：沈鹿溪录下但未保存的八个字。",
        )

        assert _unclosed_carry_forward_items(pkt, "本章完全转向别的话题。") == [item.text]

    def test_abandoned_item_is_skipped(self) -> None:
        """Escape hatch: abandoned items do not count as unclosed."""
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="放弃的线索", status="abandoned", abandon_reason="留白")]
        )
        current_text = "本章完全不提这条线索。"
        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_deferred_item_is_skipped(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="推迟的线索", status="deferred", deferred_to_chapter=20)]
        )
        current_text = "本章完全不提这条线索。"
        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_meta_instruction_items_are_skipped(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="下一章中揭示主角动机")]
        )
        current_text = "普通章节正文。"
        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_real_chapter2_wind_recording_paraphrase_is_closed(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="沈鹿溪每日清晨六分钟的风声录制已形成隐性约定")]
        )
        current_text = (
            "清晨六点，沈鹿溪把摄像机放在窗台边，按下录制键。"
            "风声穿过旧楼的缝隙，持续了整整六分钟。"
        )

        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_real_chapter2_silence_gap_paraphrase_is_closed(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="两种沉默之间的沟壑尚未跨越")]
        )
        current_text = "他们都没有先开口，两种沉默的沟壑仍然存在，只是被窗外的雨声压低。"

        assert _unclosed_carry_forward_items(pkt, current_text) == []

    def test_carry_forward_atom_match_still_blocks_weak_topic_overlap(self) -> None:
        item = "沈鹿溪录下但未保存的八个字：「那个视频不是完整的」"
        current_text = "这一章完全没有提及录音或视频，转向了别的话题。"

        assert not _LocalChecks._carry_forward_item_present_in_text(item, current_text)


class TestCarryForwardHardGate:
    def test_unclosed_open_item_blocks_archive(self) -> None:
        """The core fix: an unclosed open item raises ConsistencyViolationError."""
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="沈鹿溪录下但未保存的八个字：「那个视频不是完整的」")]
        )
        with pytest.raises(ConsistencyViolationError) as exc_info:
            _enforce_archive_hard_quality_blocks(
                _runner(),
                chapter_number=15,
                eval_report=None,
                continuity_report=None,
                causal_report=None,
                chapter_repair_report=None,
                current_text="本章完全不提录音和视频。",
                packet=pkt,
            )
        assert "必须承接的开放项" in str(exc_info.value)

    def test_unclosed_open_item_can_be_bypassed_after_ai_auto_repair(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        runner = _runner()
        runner._on_step = lambda step, payload: events.append((step, payload))
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="沈鹿溪录下但未保存的八个字：「那个视频不是完整的」")]
        )

        _enforce_archive_hard_quality_blocks(
            runner,
            chapter_number=15,
            eval_report=None,
            continuity_report=None,
            causal_report=None,
            chapter_repair_report=None,
            current_text="本章用动作回应了上一章的沉默，但没有复述那八个字。",
            packet=pkt,
            allow_carry_forward_archive_bypass=True,
        )

        assert events
        assert events[0][0] == "carry_forward_archive_bypass"
        assert events[0][1]["reason"] == "ai_auto_repair_already_applied"

    def test_carry_forward_bypass_does_not_bypass_other_hard_blocks(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="沈鹿溪录下但未保存的八个字：「那个视频不是完整的」")]
        )

        with pytest.raises(ConsistencyViolationError, match="因果分 0.9 低于硬阻断线 4.0"):
            _enforce_archive_hard_quality_blocks(
                _runner(),
                chapter_number=15,
                eval_report=None,
                continuity_report=None,
                causal_report=types.SimpleNamespace(causal_score=0.9, issues=[]),
                chapter_repair_report=None,
                current_text="本章用动作回应了上一章的沉默，但没有复述那八个字。",
                packet=pkt,
                allow_carry_forward_archive_bypass=True,
            )

    def test_all_items_present_does_not_block(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="信号窗口只剩最后五分钟")]
        )
        _enforce_archive_hard_quality_blocks(
            _runner(),
            chapter_number=15,
            eval_report=None,
            continuity_report=None,
            causal_report=None,
            chapter_repair_report=None,
            current_text="信号窗口只剩最后五分钟，她必须做决定。",
            packet=pkt,
        )  # must not raise

    def test_abandoned_only_does_not_block(self) -> None:
        pkt = _packet_with_previous_carry(
            [CarryForwardItem(text="放弃的线索", status="abandoned", abandon_reason="留白")]
        )
        _enforce_archive_hard_quality_blocks(
            _runner(),
            chapter_number=15,
            eval_report=None,
            continuity_report=None,
            causal_report=None,
            chapter_repair_report=None,
            current_text="本章完全不提这条线索。",
            packet=pkt,
        )  # must not raise

    def test_no_packet_does_not_block(self) -> None:
        _enforce_archive_hard_quality_blocks(
            _runner(),
            chapter_number=1,
            eval_report=None,
            continuity_report=None,
            causal_report=None,
            chapter_repair_report=None,
            current_text="开篇章正文。",
            packet=None,
        )  # must not raise
