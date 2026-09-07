from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.draft import EditResult
from novel_forge.pipeline.long.stages import causal_repair, dedup_pronoun
from novel_forge.pipeline.steps.edit_step import EditStep
from novel_forge.pipeline.steps.patch_step import ChapterPatchStep


class _StorageStub:
    def save_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class _LayoutStub:
    def __init__(self, root: Path) -> None:
        self._root = root

    def chapter_draft_path(self, chapter_num: int, version: int) -> Path:
        return self._root / f"chapter_{chapter_num:03d}_v{version}.md"


class _PronounStepStub:
    def _build_character_pronouns(self, *_args, **_kwargs) -> dict[str, str]:
        return {}

    def generate_fix_prompt(self, *_args, **_kwargs) -> str:
        return "请修复代词问题。"


def _make_runner() -> Any:
    events: list[tuple[str, Any]] = []

    def _on_step(step: str, data: Any) -> None:
        events.append((step, data))

    return SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(),
        _storage=_StorageStub(),
        _on_step=_on_step,
        events=events,
    )


def _make_bundle(tmp_path: Path) -> Any:
    return SimpleNamespace(
        story_bible=SimpleNamespace(tone="冷峻", genre="悬疑"),
        chapter_outline=SimpleNamespace(
            goal="推进主线冲突",
            main_plot_points=["主线推进A"],
            subplot_points=["支线推进B"],
            subplot_focus="支线聚焦",
        ),
        layout=_LayoutStub(tmp_path),
        style_profile={
            "source_elements": ["genre", "tone"],
            "summary": "快节奏与动作钩子优先。",
            "modules": [{"name": "节奏", "rules": ["段末留动作钩子"]}],
        },
    )


@pytest.mark.asyncio
async def test_repair_pronouns_propagates_style_layers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(self, input_data):  # noqa: ANN001
        captured["context"] = input_data.context
        return EditResult(revised_text="修复后正文", edit_notes=[], iteration=input_data.iteration)

    monkeypatch.setattr(EditStep, "run", _fake_run)

    runner = _make_runner()
    bundle = _make_bundle(tmp_path)
    packet = SimpleNamespace(canon_context={"characters": {"林远": {"gender": "女"}}})
    pronoun_report = {
        "issues": [{"character": "林远", "expected_pronoun": "她", "actual_pronoun": "他"}],
        "pov_issues": 1,
        "total_issues": 1,
    }

    revised = await dedup_pronoun._repair_pronouns(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text="他抬头看向窗外。",
        chapter_number=1,
        pronoun_report=pronoun_report,
        pronoun_step=_PronounStepStub(),
        trace=SimpleNamespace(),
    )

    assert revised == "修复后正文"
    ctx = captured["context"]
    assert "style" not in ctx or ctx.get("style") is None
    assert ctx["stage_cards"]["style"]["summary"] == bundle.style_profile["summary"]


@pytest.mark.asyncio
async def test_alignment_repair_edit_propagates_style_layers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(self, input_data):  # noqa: ANN001
        captured["context"] = input_data.context
        return EditResult(
            revised_text="对齐修复后正文", edit_notes=[], iteration=input_data.iteration
        )

    monkeypatch.setattr(EditStep, "run", _fake_run)

    runner = _make_runner()
    bundle = _make_bundle(tmp_path)
    packet = SimpleNamespace(canon_context={"characters": {"林远": {"gender": "女"}}})
    alignment_report = SimpleNamespace(
        alignment_score=6.2,
        repair_actions=["补足主线推进点", "强化结尾钩子"],
    )

    revised = await causal_repair.alignment_repair_edit(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=None,
        plan=SimpleNamespace(),
        current_text="原始正文",
        chapter_number=2,
        alignment_report=alignment_report,
        trace=SimpleNamespace(),
    )

    assert revised == "对齐修复后正文"
    ctx = captured["context"]
    assert "style" not in ctx or ctx.get("style") is None
    assert ctx["stage_cards"]["style"]["summary"] == bundle.style_profile["summary"]
    assert ctx["alignment_must_cover"] == []
    assert ctx["alignment_repair_actions"] == ["补足主线推进点", "强化结尾钩子"]
    assert ctx["alignment_weak_subplot_points"] == []


@pytest.mark.asyncio
async def test_alignment_repair_edit_builds_structured_directives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(self, input_data):  # noqa: ANN001
        captured["context"] = input_data.context
        return EditResult(
            revised_text="结构化修复后正文", edit_notes=[], iteration=input_data.iteration
        )

    async def _no_op_patch(self, input_data):  # noqa: ANN001
        return SimpleNamespace(
            revised_text=input_data.chapter_text,
            patches_applied=0,
            patches_attempted=1,
            fallback=True,
        )

    monkeypatch.setattr(EditStep, "run", _fake_run)
    monkeypatch.setattr(ChapterPatchStep, "run", _no_op_patch)

    runner = _make_runner()
    bundle = _make_bundle(tmp_path)
    packet = SimpleNamespace(canon_context={"characters": {"林远": {"gender": "女"}}})
    alignment_report = SimpleNamespace(
        alignment_score=5.8,
        missing_main_points=["主角必须拿到档案", "必须交代密钥来源"],
        repair_actions=["补一段拿到档案的现场动作", "把密钥来源落到对话证据"],
        weak_subplot_points=["支线人物沈昭的立场变化"],
        supportive_subplot_points=["保留与父亲旧案的暗线呼应"],
    )
    plan = SimpleNamespace(
        required_state_transitions=["主角从被动转为主动调查"],
        key_revelations=["密钥对应旧档案编号"],
        foreshadowing_plan=["埋下父亲旧案的再调查入口"],
        required_literals=[
            {
                "contract_id": "secret-boundary",
                "literal": "有些事情不是你想知道就能知道的",
                "scene_id": "scene_02",
                "reason": "后文必须逐字回指这句拒绝",
                "placement_hint": "林小满回应档案来源时",
            }
        ],
        scene_intents=[
            {
                "scene_id": "scene_02",
                "required_outcome": "林小满说「有些事情不是你想知道就能知道的」",
            }
        ],
    )

    revised = await causal_repair.alignment_repair_edit(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=None,
        plan=plan,
        current_text="原始正文",
        chapter_number=2,
        alignment_report=alignment_report,
        trace=SimpleNamespace(),
    )

    assert revised == "结构化修复后正文"
    ctx = captured["context"]
    assert ctx["alignment_must_cover"] == ["主角必须拿到档案", "必须交代密钥来源"]
    assert ctx["alignment_repair_actions"] == ["补一段拿到档案的现场动作", "把密钥来源落到对话证据"]
    assert ctx["alignment_weak_subplot_points"] == ["支线人物沈昭的立场变化"]
    assert ctx["alignment_preserve_points"] == [
        "保留与父亲旧案的暗线呼应",
        "主角从被动转为主动调查",
        "密钥对应旧档案编号",
        "埋下父亲旧案的再调查入口",
    ]
    assert ctx["alignment_required_literals"][0]["literal"] == ("有些事情不是你想知道就能知道的")
    assert ctx["alignment_required_literals"][0]["reason"] == "后文必须逐字回指这句拒绝"


@pytest.mark.asyncio
async def test_alignment_repair_uses_targeted_patch_for_literal_only_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    literal = "有些事情不是你想知道就能知道的"

    async def _fake_patch(self, input_data):  # noqa: ANN001
        captured["patch_input"] = input_data
        return SimpleNamespace(
            revised_text=input_data.chapter_text + f"\n\n林小满说：「{literal}」",
            patches_applied=1,
            patches_attempted=1,
            fallback=False,
        )

    async def _unexpected_full_edit(self, input_data):  # noqa: ANN001
        raise AssertionError("literal-only repair must use the compact patch path first")

    monkeypatch.setattr(ChapterPatchStep, "run", _fake_patch)
    monkeypatch.setattr(EditStep, "run", _unexpected_full_edit)

    runner = _make_runner()
    bundle = _make_bundle(tmp_path)
    packet = SimpleNamespace(canon_context={"characters": {}})
    plan = SimpleNamespace(
        required_state_transitions=[],
        key_revelations=[],
        foreshadowing_plan=[],
        required_literals=[
            {
                "contract_id": "chapter-009-secret-boundary",
                "literal": literal,
                "scene_id": "scene_04",
                "reason": "后文解谜将逐字回指",
                "placement_hint": "聚餐中沈岸追问银手链之后",
            }
        ],
        scene_intents=[
            {
                "scene_id": "scene_04",
                "summary": f"林小满在聚餐时对沈岸说「{literal}」",
            }
        ],
    )
    alignment_report = SimpleNamespace(
        alignment_score=9.6,
        missing_main_points=[],
        repair_actions=[],
        weak_subplot_points=[],
        supportive_subplot_points=[],
    )
    current_text = "沈岸看向银手链，想起昨夜聚餐时林小满的回避。"

    revised = await causal_repair.alignment_repair_edit(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=None,
        plan=plan,
        current_text=current_text,
        chapter_number=9,
        alignment_report=alignment_report,
        trace=SimpleNamespace(),
    )

    assert literal in revised
    patch_input = captured["patch_input"]
    assert len(patch_input.issues) == 1
    assert patch_input.issues[0].issue_type == "missing_plan_literal"
    assert patch_input.issues[0].paragraph_start == 1
    assert patch_input.issues[0].paragraph_end == 1
    assert patch_input.issues[0].anchor_type == "llm_locator_from_plan_contract"
    assert patch_input.issues[0].location == "聚餐中沈岸追问银手链之后"
    assert "后文解谜将逐字回指" in patch_input.issues[0].fix_suggestion
    assert "逐字" in patch_input.issues[0].fix_suggestion
    event_names = [name for name, _payload in runner.events]
    assert "alignment_literal_patch_attempt" in event_names
    assert "alignment_literal_patch_result" in event_names
