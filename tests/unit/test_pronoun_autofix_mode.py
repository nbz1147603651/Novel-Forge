from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.pipeline.long.stages import dedup_pronoun as quality


class _RunnerStub:
    def __init__(self, mode: str = "off") -> None:
        self._router = object()
        self._builder = object()
        self._settings = SimpleNamespace(pronoun_autofix_mode=mode)
        self.events: list[tuple[str, Any]] = []

    def _on_step(self, step: str, data: Any) -> None:
        self.events.append((step, data))


class _PronounStepStub:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[str] = []

    async def run(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = str(ctx.get("chapter_text", "") or "")
        self.calls.append(text)
        if text == "revised_text":
            # Recheck still bad: no improvement.
            return {
                "passed": False,
                "score": 7.0,
                "total_issues": 1,
                "pov_issues": 1,
                "issues": [{"character": "小芳"}],
                "requires_rewrite": True,
            }
        return {
            "passed": False,
            "score": 7.0,
            "total_issues": 1,
            "pov_issues": 1,
            "issues": [{"character": "小芳"}],
            "requires_rewrite": True,
        }


@pytest.mark.asyncio
async def test_run_pronoun_check_off_mode_never_repairs(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _RunnerStub(mode="off")
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(pov_character="小芳"))
    packet = SimpleNamespace(chapter_plan={}, canon_context={"characters": {"小芳": {"gender": "女"}}})

    async def _should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("_repair_pronouns should not be called in off mode")

    monkeypatch.setattr(quality, "PronounCheckStep", _PronounStepStub)
    monkeypatch.setattr(quality, "_repair_pronouns", _should_not_call)

    text, report = await quality.run_pronoun_check(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text="original_text",
        chapter_number=1,
        trace=SimpleNamespace(),
    )

    assert text == "original_text"
    assert report.get("requires_rewrite") is True


@pytest.mark.asyncio
async def test_run_pronoun_check_rolls_back_when_repair_not_improved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _RunnerStub(mode="pov_only")
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(pov_character="小芳"))
    packet = SimpleNamespace(chapter_plan={}, canon_context={"characters": {"小芳": {"gender": "女"}}})

    async def _fake_repair(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return "revised_text"

    monkeypatch.setattr(quality, "PronounCheckStep", _PronounStepStub)
    monkeypatch.setattr(quality, "_repair_pronouns", _fake_repair)

    text, report = await quality.run_pronoun_check(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text="original_text",
        chapter_number=1,
        trace=SimpleNamespace(),
    )

    # Recheck reported no improvement, so text should be rolled back.
    assert text == "original_text"
    assert report.get("pov_issues") == 1
    assert any(step == "pronoun_repair_recheck" for step, _ in runner.events)

