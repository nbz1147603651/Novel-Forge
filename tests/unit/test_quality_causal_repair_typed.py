"""Regression tests for typed causal repair flow.

After architectural unification, the full-text edit path lives in
CausalRepairStep._fulltext_repair (called by _execute when patch fails
or for edit-type issues like opening_causal_gap).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairInput, CausalRepairStep


class _FakeBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object], dict[str, object]]] = []

    def build(self, task_type, context, **kwargs):
        self.calls.append((task_type, context, kwargs))
        return SimpleNamespace(task_type=task_type, context=context, kwargs=kwargs)


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[object] = []

    async def route(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.content)


class _FakeBridge:
    def model_dump(self, mode="python"):
        return {"transition_mode": "direct_continue", "action_handoff": "角色仍握着令牌", "causal_link": None}


@pytest.mark.asyncio
async def test_causal_repair_step_fulltext_edit_path() -> None:
    """_fulltext_repair calls REPAIR_CAUSAL template with correct context."""
    builder = _FakeBuilder()
    router = _FakeRouter('{"content": "修复后的正文修复后的正文修复后的正文修复后的正文修复后的正文修复后的正文"}')
    settings = SimpleNamespace(temp_repair_causal=0.35)
    step = CausalRepairStep(router, builder, settings=settings, trace=object())

    issue = CausalIssue(
        issue_type="opening_causal_gap",
        summary="开头承接断裂",
        evidence="第一段直接切到新场景",
        fix_suggestion="补足上章动作接力",
        location="开头",
        severity="high",
    )
    empty_report = CausalValidationReport(issues=[issue])
    input_data = CausalRepairInput(
        chapter_number=6,
        chapter_text="原正文原正文原正文原正文原正文原正文原正文原正文原正文原正文",
        causal_link={"previous_event": "上一章刚发生争执"},
        chapter_bridge=_FakeBridge(),
        causal_report=empty_report,
        previous_chapter_ending="上章结尾余波未散。",
        character_profiles=[{"name": "风伏京", "gender": "女"}],
    )

    revised = await step._fulltext_repair(input_data, [issue])

    assert revised.startswith("修复后的正文")
    assert builder.calls
    task_type, context, kwargs = builder.calls[0]
    assert getattr(task_type, "value", str(task_type)) == "repair_causal"
    assert context["previous_chapter_ending"] == "上章结尾余波未散。"
    assert context["issue_types"] == ["opening_causal_gap"]
    assert "风伏京" in context["character_whitelist_note"]
    assert "stage_cards" in context
    assert kwargs["temperature"] == 0.35


@pytest.mark.asyncio
async def test_causal_repair_step_fulltext_unwraps_think_json_wrapper() -> None:
    builder = _FakeBuilder()
    router = _FakeRouter('</think>\n\n{"revised_text": "修复后的正文A修复后的正文A修复后的正文A"}')
    settings = SimpleNamespace(temp_repair_causal=0.35)
    step = CausalRepairStep(router, builder, settings=settings, trace=object())

    issue = CausalIssue(
        issue_type="opening_causal_gap",
        summary="开头承接断裂",
        evidence="第一段直接切到新场景",
        fix_suggestion="补足上章动作接力",
        location="开头",
        severity="high",
    )
    input_data = CausalRepairInput(
        chapter_number=6,
        chapter_text="原正文原正文原正文原正文",
        causal_link={"previous_event": "上一章刚发生争执"},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
        previous_chapter_ending="上章结尾余波未散。",
    )

    revised = await step._fulltext_repair(input_data, [issue])
    assert revised.startswith("修复后的正文A")
    assert not revised.startswith("</think>")


@pytest.mark.asyncio
async def test_causal_repair_step_uses_window_strategy_for_transition_issues(monkeypatch) -> None:
    """missing_causal_transition should enter window strategy before full-text."""
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    calls = {"window": 0, "fulltext": 0}

    async def _fake_window_repair(_input, _issues, current_text, window_size=3):
        calls["window"] += 1
        return current_text + "\n\n补入转折铺垫"

    async def _fake_fulltext_repair(_input, _issues, current_text=None, **_kwargs):
        calls["fulltext"] += 1
        return (current_text or "") + "\n\n不应走到这里"

    monkeypatch.setattr(step, "_window_repair", _fake_window_repair)
    monkeypatch.setattr(step, "_fulltext_repair", _fake_fulltext_repair)

    issue = CausalIssue(
        issue_type="missing_causal_transition",
        summary="从质问突然切到撤退，缺少转折铺垫",
        evidence="第6段直接撤离",
        fix_suggestion="补转折前的异常细节",
        location="第6段",
        severity="high",
    )
    input_data = CausalRepairInput(
        chapter_number=7,
        chapter_text="原正文第一段\n\n原正文第二段",
        causal_link={"previous_event": "双方对峙"},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert "补入转折铺垫" in result.revised_text
    assert calls["window"] == 1
    assert calls["fulltext"] == 0


@pytest.mark.asyncio
async def test_causal_repair_post_check_keeps_soft_forbidden_advisory(monkeypatch) -> None:
    """A causal edit may warn on hard rules, but never upgrades a soft rule."""
    step = CausalRepairStep(
        _FakeRouter(""),
        _FakeBuilder(),
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    async def _fake_window_repair(_input, _issues, current_text, window_size=3):
        return current_text + "\n\n冷白灯光落在桌面上。"

    monkeypatch.setattr(step, "_window_repair", _fake_window_repair)
    issue = CausalIssue(
        issue_type="missing_causal_transition",
        summary="缺少转折铺垫",
        evidence="第2段直接离开",
        fix_suggestion="补充触发原因",
        location="第2段",
        severity="high",
    )
    result = await step._execute(
        CausalRepairInput(
            chapter_number=7,
            chapter_text="角色停在门边。",
            causal_link={},
            chapter_bridge=_FakeBridge(),
            causal_report=CausalValidationReport(issues=[issue]),
            forbidden_elements=["惨淡月光"],
            forbidden_elements_soft=["冷白灯光"],
        )
    )

    assert result.applied is True
    assert result.forbidden_element_warnings == []


@pytest.mark.asyncio
async def test_causal_repair_step_routes_opening_gap_to_patch_with_boundary_context(
    monkeypatch,
) -> None:
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    captured: dict[str, object] = {}

    async def _fake_patch_run(_self, payload):
        from novel_forge.pipeline.steps.patch_step import PatchResult

        captured["payload"] = payload
        return PatchResult(
            revised_text=payload.chapter_text + "\n\n补丁修复开头因果",
            patches_applied=1,
            patches_attempted=1,
            fallback=False,
        )

    async def _fail_fulltext(*_args, **_kwargs):
        raise AssertionError("opening_causal_gap should not jump to fulltext when patch succeeds")

    monkeypatch.setattr("novel_forge.pipeline.steps.patch_step.ChapterPatchStep.run", _fake_patch_run)
    monkeypatch.setattr(step, "_fulltext_repair", _fail_fulltext)

    issue = CausalIssue(
        issue_type="opening_causal_gap",
        summary="开头承接断裂",
        evidence="首段直接跳到对峙现场",
        fix_suggestion="补上上章余波的动作接力",
        location="开头",
        severity="high",
    )
    prev_ending = "她把密钥攥进掌心，刚推门时走廊传来追兵脚步。"
    input_data = CausalRepairInput(
        chapter_number=6,
        chapter_text="第一段。\n\n第二段。",
        causal_link={"previous_event": "上一章刚发生追击"},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
        previous_chapter_ending=prev_ending,
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert "补丁修复开头因果" in result.revised_text
    payload = captured["payload"]
    assert payload.boundary_context["focus"] == "开头"
    assert payload.boundary_context["previous_chapter_ending"] == prev_ending
    assert payload.boundary_context["opening_contract"] == "角色仍握着令牌"
    assert payload.issues[0].location == "第1段（开头承接处）"
    assert "必须出现在第1段" in payload.issues[0].fix_suggestion


@pytest.mark.asyncio
async def test_causal_repair_step_repairs_selected_medium_issue(monkeypatch) -> None:
    """Manual-selected medium issues should still be routed into repair."""
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    async def _fake_window_repair(_input, _issues, current_text, window_size=3):
        return current_text + "\n\n补全因果来源"

    async def _fake_fulltext_repair(_input, _issues, current_text=None, **_kwargs):
        return current_text or ""

    monkeypatch.setattr(step, "_window_repair", _fake_window_repair)
    monkeypatch.setattr(step, "_fulltext_repair", _fake_fulltext_repair)

    issue = CausalIssue(
        issue_type="event_without_cause",
        summary="角色突然提前知道据点",
        evidence="第5段“她已提前等在暗门后”",
        fix_suggestion="补充信息传递链路",
        location="第5段",
        severity="medium",
    )
    input_data = CausalRepairInput(
        chapter_number=9,
        chapter_text="第一段\n\n第二段",
        causal_link={},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
        must_fix_summaries=[issue.summary],
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert "补全因果来源" in result.revised_text
    assert "event_without_cause" in result.repaired_issue_types


@pytest.mark.asyncio
async def test_causal_repair_step_prefers_selected_issue_ids(monkeypatch) -> None:
    """When issue IDs are available, same-summary non-target issues are not selected."""
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )
    captured: dict[str, list[CausalIssue]] = {}

    async def _fake_window_repair(_input, issues, current_text, window_size=3):
        captured["issues"] = list(issues)
        return current_text + "\n\n只修目标 ID"

    monkeypatch.setattr(step, "_window_repair", _fake_window_repair)

    target = CausalIssue(
        issue_id="target-causal-id",
        issue_type="event_without_cause",
        summary="同一个摘要",
        evidence="第5段",
        fix_suggestion="补信息来源",
        location="第5段",
        severity="medium",
    )
    other = CausalIssue(
        issue_id="other-causal-id",
        issue_type="event_without_cause",
        summary="同一个摘要",
        evidence="第8段",
        fix_suggestion="补另一个信息来源",
        location="第8段",
        severity="medium",
    )
    input_data = CausalRepairInput(
        chapter_number=9,
        chapter_text="第一段\n\n第二段",
        causal_link={},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[target, other]),
        must_fix_summaries=[target.summary],
        must_fix_issue_ids=[target.issue_id],
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert [issue.issue_id for issue in captured["issues"]] == ["target-causal-id"]


@pytest.mark.asyncio
async def test_causal_repair_step_routes_degraded_anchor_to_fulltext(monkeypatch) -> None:
    """Ambiguous evidence anchors must not be re-guessed into a window repair."""
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    calls = {"window": 0, "fulltext": 0}

    async def _fail_window_repair(_input, _issues, current_text, window_size=3):
        calls["window"] += 1
        raise AssertionError("anchor_degraded issue should bypass window repair")

    async def _fake_fulltext_repair(_input, _issues, current_text=None, **_kwargs):
        calls["fulltext"] += 1
        return (current_text or "") + "\n\n使用全文上下文处理歧义锚点"

    monkeypatch.setattr(step, "_window_repair", _fail_window_repair)
    monkeypatch.setattr(step, "_fulltext_repair", _fake_fulltext_repair)

    issue = CausalIssue(
        issue_type="event_without_cause",
        summary="铜铃触发暗门缺少因果铺垫",
        evidence="铜铃响了三下",
        fix_suggestion="补充铜铃与暗门机关的因果关系",
        location="未指定",
        severity="high",
        anchor_type="anchor_degraded",
    )
    input_data = CausalRepairInput(
        chapter_number=9,
        chapter_text="第一段里铜铃响了三下。\n\n第二段里铜铃响了三下，暗门打开。",
        causal_link={},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert calls["window"] == 0
    assert calls["fulltext"] == 1
    assert "event_without_cause" in result.repaired_issue_types


@pytest.mark.asyncio
async def test_causal_repair_step_escalates_patch_issue_to_fulltext_on_retry(monkeypatch) -> None:
    """Round-2 retries should escalate patch-type issues to full-text strategy."""
    builder = _FakeBuilder()
    router = _FakeRouter("")
    step = CausalRepairStep(
        router,
        builder,
        settings=SimpleNamespace(temp_repair_causal=0.35),
        trace=object(),
    )

    calls = {"fulltext": 0}

    async def _fail_patch(*_args, **_kwargs):
        raise AssertionError("patch path should be bypassed on escalated retry")

    async def _fake_fulltext_repair(_input, _issues, current_text=None, **_kwargs):
        calls["fulltext"] += 1
        return (current_text or "") + "\n\n改为全文修复路径"

    monkeypatch.setattr("novel_forge.pipeline.steps.patch_step.ChapterPatchStep.run", _fail_patch)
    monkeypatch.setattr(step, "_fulltext_repair", _fake_fulltext_repair)

    issue = CausalIssue(
        issue_type="address_form_mismatch",
        summary="称呼“殿下”与身份不符",
        evidence="第3段对话",
        fix_suggestion="改称呼",
        location="第3段",
        severity="high",
    )
    input_data = CausalRepairInput(
        chapter_number=4,
        chapter_text="旧正文",
        causal_link={},
        chapter_bridge=_FakeBridge(),
        causal_report=CausalValidationReport(issues=[issue]),
        repair_round=2,
        prev_round_issues=[issue.summary],
    )

    result = await step._execute(input_data)

    assert result.applied is True
    assert calls["fulltext"] == 1
    assert "address_form_mismatch" in result.repaired_issue_types
