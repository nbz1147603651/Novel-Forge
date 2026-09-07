"""Tests for local prompt-leak prechecks in the long draft stage."""

from __future__ import annotations

from novel_forge.pipeline.long.stages.draft import _has_prompt_leaks_local


def test_draft_precheck_uses_central_prompt_leak_patterns() -> None:
    assert _has_prompt_leaks_local("opening_contract：本章开头应承接前文。")
    assert _has_prompt_leaks_local("她以动作完成场景转换至渡口。")
    assert not _has_prompt_leaks_local("她推开门，看见渡口灯火在雨里摇晃。")
