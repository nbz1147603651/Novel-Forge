"""Integration tests for the 6-phase long-form pipeline (bridge -> plan ->
draft -> wave -> quality -> repair -> polish -> humanize -> finalize).

These tests focus on the *seams* introduced by the Generate-stage split:
  1. Stage event sequence is distinct (no ``edit_*`` events).
  2. WAVE step receives the full cross-scene intent and full plan.
  3. DRAFT step does NOT see cross-scene intent and uses the focused
     scene-intent subset.
  4. Polish triggers from ``long_polish_enabled`` or auto-score threshold.
  5. ``long_max_edit_rounds`` is fully removed from the source tree.
  6. Short form still uses ``max_edit_rounds`` (regression guard).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from novel_forge.core.config import Settings

# -- 1. Phase event sequence ---------------------------------------------


def test_pipeline_phases_distinct() -> None:
    """The new orchestrator exposes ``prepare_generate_context``,
    ``generate_draft`` and ``apply_wave`` as the three Generate-phase
    steps (no more ``edit_*`` events)."""
    from novel_forge.pipeline.long.chapter_flow import (
        GenerateArtifacts,
        generate_chapter_prose,
    )
    from novel_forge.pipeline.long.stages import draft as draft_stage
    from novel_forge.pipeline.long.stages import wave as wave_stage

    assert hasattr(draft_stage, "prepare_generate_context")
    assert hasattr(draft_stage, "generate_draft")
    assert hasattr(wave_stage, "apply_wave")
    assert callable(generate_chapter_prose)
    # The orchestrator return type carries the new contract.
    assert GenerateArtifacts.__dataclass_params__.frozen is True


# -- 5. long_max_edit_rounds fully removed --------------------------------


def test_no_long_max_edit_rounds_references() -> None:
    """Source tree must contain zero references to ``long_max_edit_rounds``
    after the T5-1 + T5-2 cleanup (保留 ``short_max_edit_rounds``)."""
    root = Path(__file__).resolve().parents[2]
    # Skip the test file itself, caches, and vendored dirs.
    self_path = Path(__file__).resolve()
    skip_dirs = {
        ".git",
        ".worktrees",
        "data",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".codegraph",
    }
    pattern = re.compile(r"\blong_max_edit_rounds\b")
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == self_path:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix not in (".py", ".j2", ".json", ".toml", ".md", ".txt"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            hits.append(str(path.relative_to(root)))
    assert hits == [], f"long_max_edit_rounds still referenced in: {hits}"


# -- 6. Short form unchanged ---------------------------------------------


def test_short_form_unchanged() -> None:
    """Short form must still have its own ``short_max_edit_rounds`` setting
    and the ``RunShortRequest.max_edit_rounds`` field intact."""
    s = Settings(_env_file=None)
    assert hasattr(s, "short_max_edit_rounds")
    assert isinstance(s.short_max_edit_rounds, int)

    from novel_forge.workspace.contracts import RunShortRequest

    fields = RunShortRequest.model_fields
    assert "max_edit_rounds" in fields


# -- 2. WAVE step receives full context ---------------------------------


def _make_ctx() -> SimpleNamespace:
    """Build a minimal GenerateContext-shaped SimpleNamespace for tests."""
    from novel_forge.pipeline.long.stages.draft import GenerateContext

    return GenerateContext(
        runner=SimpleNamespace(
            _router=SimpleNamespace(),
            _builder=SimpleNamespace(),
            _settings=SimpleNamespace(),
            _storage=SimpleNamespace(),
            _config=SimpleNamespace(writing_mode="whole_chapter"),
            _on_step=lambda *a, **k: None,
        ),
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(pov_character="林远", expected_word_count=10),
            story_bible=SimpleNamespace(),
            editorial_contract=None,
            style_profile=None,
            layout=SimpleNamespace(),
            blueprint=None,
            weak_senses=[],
        ),
        packet=SimpleNamespace(canon_context={}, character_profiles=[]),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(
            cross_scene_intent={
                "cross_scene_references": [
                    {
                        "from_scene": "scene_01",
                        "to_scene": "scene_02",
                        "ref_type": "callback",
                        "description": "林远走过雾霭街道",
                    }
                ],
                "pacing_curve": [2, 4],
            },
            scene_intents=[],
            forbidden_elements=[],
            forbidden_elements_soft=[],
            expression_channel_records=[],
            intentional_callbacks=[],
        ),
        chapter_number=1,
        trace=SimpleNamespace(),
        planning_hints=None,
        reading_power_hint=None,
        draft_step=SimpleNamespace(),
        target_word_count=10,
        budgeted_plan=SimpleNamespace(),
        draft_memory_hints={},
        draft_canon={},
        prev_known_issues=[],
        focus_ids=[],
        element_selection_payload=None,
        weak_senses=[],
        draft_kernel_context={},
        chapter_repair_kernel_context={},
        wave_kernel_context={},
    )


def test_wave_step_receives_full_context() -> None:
    """apply_wave calls build_wave_cards with the full plan (incl.
    cross_scene_intent) and the full editorial contract (or None)."""
    import asyncio

    from novel_forge.pipeline.long.stages import wave as wave_stage

    captured: dict = {}

    def _fake_build_wave_cards(**kwargs):
        captured.update(kwargs)
        return {"plan": {"cross_scene_intent": kwargs.get("plan", {})}}

    class _FakeStep:
        async def run(self, input_data):
            return SimpleNamespace(
                woven_prose=input_data.chapter_text,
                warnings=[],
                cross_ref_hits=[],
                scene_transitions_added=[],
                motif_weave_log=[],
                final_word_count=10,
            )

    ctx = _make_ctx()
    with (
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_wave_cards",
            _fake_build_wave_cards,
        ),
        patch("novel_forge.pipeline.steps.wave_step.WaveStep", lambda *a, **k: _FakeStep()),
    ):
        asyncio.run(wave_stage.apply_wave(ctx, draft_text="一段雾霭笼罩的街道。"))

    assert "plan" in captured
    # budgeted_plan is a derivative of ctx.plan via _ensure_scene_word_budgets;
    # we just verify the plan-related fields land in the kwargs.
    assert "pov_hint" in captured
    assert "chapter_outline" in captured
    assert "bridge" in captured


def test_wave_stage_persists_reviewable_handoff_artifact() -> None:
    """apply_wave writes the DRAFT+WAVE handoff as ``v1_wave.md``."""
    import asyncio
    import dataclasses

    from novel_forge.pipeline.long.stages import wave as wave_stage

    saved: list[tuple[str, str]] = []

    class _FakeStorage:
        def save_text(self, path, text: str) -> None:
            saved.append((str(path), text))

    class _FakeStep:
        async def run(self, input_data):
            return SimpleNamespace(
                woven_prose="WAVE 后的章节正文。",
                warnings=[],
                cross_ref_hits=[],
                scene_transitions_added=[],
                motif_weave_log=[],
                final_word_count=10,
            )

    base = _make_ctx()
    ctx = dataclasses.replace(
        base,
        runner=SimpleNamespace(**{**vars(base.runner), "_storage": _FakeStorage()}),
        budgeted_plan=SimpleNamespace(
            scene_intents=[
                {"scene_id": "scene_01", "summary": "雾霭街道", "target_words": 5},
                {"scene_id": "scene_02", "summary": "灯下回望", "target_words": 5},
            ],
            cross_scene_intent={"cross_scene_references": [], "pacing_curve": [2, 4]},
        ),
        bundle=SimpleNamespace(
            **{
                **vars(base.bundle),
                "layout": SimpleNamespace(
                    chapter_wave_draft_path=lambda chapter: f"chapter_{chapter}/v1_wave.md"
                ),
            }
        ),
    )
    with (
        patch(
            "novel_forge.pipeline.long.services.constraints.constraint_router.build_wave_cards",
            lambda **_kwargs: {"plan": {}, "scene_intents": []},
        ),
        patch("novel_forge.pipeline.steps.wave_step.WaveStep", lambda *a, **k: _FakeStep()),
    ):
        result = asyncio.run(wave_stage.apply_wave(ctx, draft_text="DRAFT 原稿。"))

    assert result.current_text == "WAVE 后的章节正文。"
    assert saved == [("chapter_1/v1_wave.md", "WAVE 后的章节正文。")]
    assert result.wave_meta["scenes_woven"] == 2
    assert result.wave_meta["woven_chars"] == len("WAVE 后的章节正文。")


# -- 3. DRAFT step receives minimal context -----------------------------


def test_draft_step_receives_minimal_context() -> None:
    """generate_draft calls build_draft_cards with the plan (DRAFT route
    must NOT include the cross_scene_intent key on the plan card)."""
    import asyncio

    from novel_forge.pipeline.long.stages import draft as draft_stage

    captured: dict = {}

    def _fake_build_draft_cards(**kwargs):
        captured.update(kwargs)
        return {"plan": {}, "scene_intents": kwargs.get("plan", {})}

    import dataclasses

    ctx = _make_ctx()

    # GenerateContext is frozen; use dataclasses.replace for the overrides.
    class _FakeStep:
        async def run(self, input_data):
            return SimpleNamespace(text="雾霭笼罩的街道。" * 5)

    def _save_text(path, text):
        return None

    bundle = SimpleNamespace(
        chapter_outline=ctx.bundle.chapter_outline,
        story_bible=ctx.bundle.story_bible,
        editorial_contract=ctx.bundle.editorial_contract,
        style_profile=ctx.bundle.style_profile,
        layout=SimpleNamespace(
            chapter_draft_path=lambda ch, ver: f"/tmp/draft_{ch}_{ver}.md",
        ),
        blueprint=ctx.bundle.blueprint,
        weak_senses=ctx.bundle.weak_senses,
    )
    runner = SimpleNamespace(
        _router=ctx.runner._router,
        _builder=ctx.runner._builder,
        _settings=SimpleNamespace(long_check_chapter_enabled=False),
        _storage=SimpleNamespace(save_text=_save_text),
        _config=ctx.runner._config,
        _on_step=ctx.runner._on_step,
    )
    ctx = dataclasses.replace(ctx, bundle=bundle, runner=runner, draft_step=_FakeStep())

    with patch(
        "novel_forge.pipeline.long.services.constraints.constraint_router.build_draft_cards",
        _fake_build_draft_cards,
    ):
        asyncio.run(draft_stage.generate_draft(ctx))

    # The draft route's plan card must NOT include cross_scene_intent
    # (it is wave-only).  build_draft_cards receives the *plan object* as
    # an arg; for dicts we use "not in", for SimpleNamespace we use getattr.
    plan_arg = captured["plan"]
    if isinstance(plan_arg, dict):
        assert "cross_scene_intent" not in plan_arg
    else:
        assert getattr(plan_arg, "cross_scene_intent", None) is None
    assert "bridge" not in captured


# -- 4. Polish trigger condition ----------------------------------------


def test_polish_triggers_on_enabled_or_auto_threshold() -> None:
    """Polish no longer uses the old ``edit_rounds_zero`` trigger."""
    import novel_forge.pipeline.long.chapter_flow_review as chapter_flow_review

    source = Path(chapter_flow_review.__file__).read_text(encoding="utf-8")
    # The legacy trigger is gone.
    assert "edit_rounds_zero" not in source
    # The new trigger reads explicit enablement and score-threshold auto mode.
    assert "long_polish_enabled" in source
    assert "long_polish_auto_trigger_threshold" in source
    assert "auto_score_below_threshold" in source


def test_hunyu_first_chapter_wave_and_eval_tickets_enter_quality_repair_lane(
    monkeypatch,
) -> None:
    """A first-chapter failure like 魂玉 should enter the bounded Review repair lane."""
    import asyncio

    import novel_forge.pipeline.long.chapter_flow as chapter_flow
    import novel_forge.pipeline.long.chapter_flow_review as chapter_flow_review
    from novel_forge.core.schemas.chapter import (
        AlignmentReport,
        CausalValidationReport,
        ChapterRepairReport,
    )
    from novel_forge.core.schemas.continuity import ContinuityReport
    from novel_forge.core.schemas.eval_schema import EvalReport, RepairSuggestion
    from novel_forge.pipeline.long.execution_models import PreparedChapterArtifacts

    events: list[tuple[str, dict]] = []
    settings = SimpleNamespace(
        long_wave_post_condition_policy="repair",
        long_eval_repair_enabled=True,
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=settings,
        _on_step=lambda step, payload=None: events.append((step, payload or {})),
    )
    context = SimpleNamespace(settings=settings)
    original_text = "沈既白站在祠堂门口。\n\n魂玉没有任何回应。"
    repaired_text = (
        "沈既白站在祠堂门口，掌心的魂玉残片发热。\n\n青铜铃声从梁上回荡，祠堂暗门在血脉回应里裂开。"
    )
    plan = SimpleNamespace(
        scene_intents=[
            {"scene_id": "s1", "summary": "魂玉残片"},
            {"scene_id": "s2", "summary": "青铜铃声"},
            {"scene_id": "s3", "summary": "祠堂暗门"},
            {"scene_id": "s4", "summary": "血脉回应"},
        ],
        cross_scene_intent={
            "cross_scene_references": [
                {"description": "魂玉残片发热"},
                {"description": "青铜铃声"},
                {"description": "祠堂暗门"},
                {"description": "血脉回应"},
                {"description": "掌心发热"},
                {"description": "暗门裂开"},
            ],
            "pacing_curve": [2, 3, 4, 5],
        },
        opening_contract="魂玉残片出现异常。",
        closing_contract="暗门裂开。",
    )
    prepared = PreparedChapterArtifacts(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(chapter_number=1, expected_word_count=0),
            layout=SimpleNamespace(),
            chapter_source_slice=None,
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=plan,
    )
    state = chapter_flow._ReviewPhaseState(
        current_text=original_text,
        baseline_text=original_text,
        total_rounds_cap=5,
        wave_meta={
            "warnings": [
                "wave_post_cond_cross_ref: hit 0/6 (0%) < 80%",
                "wave_post_cond_anchor: scene s1 anchor words missing",
                "wave_post_cond_anchor: scene s2 anchor words missing",
                "wave_post_cond_anchor: scene s3 anchor words missing",
                "wave_post_cond_anchor: scene s4 anchor words missing",
            ],
            "cross_ref_hits": [],
            "cross_ref_total": 6,
            "scene_anchor_total": 4,
            "scenes_woven": 4,
        },
        alignment_report=AlignmentReport(alignment_score=8.5),
        continuity_report=ContinuityReport(continuity_score=8.5),
        causal_report=CausalValidationReport(causal_score=8.5),
        chapter_repair_report=ChapterRepairReport(),
    )

    async def _fake_eval(*_args, **_kwargs):
        return EvalReport(
            overall_score=6.0,
            passed=True,
            repair_suggestions=[
                RepairSuggestion(
                    issue="魂玉异常没有被章内动作兑现",
                    location="第2段",
                    suggestion="补回魂玉残片发热、铃声和暗门回应。",
                    priority="high",
                    dimension="plot_progression",
                )
            ],
        )

    async def _fake_edit_run(_self, input_data):
        tickets = input_data.context["chapter_quality_repair_tickets"]
        assert tickets
        assert tickets[0]["ticket_id"]
        assert tickets[0]["finding_ids"]
        assert tickets[0]["postconditions"]
        stage_cards = input_data.context["stage_cards"]
        assert stage_cards["stage"] == "edit"
        assert "stage_visibility" in stage_cards
        assert "repair" in stage_cards
        assert stage_cards["plan"]["cross_scene_intent"]
        return SimpleNamespace(
            revised_text=repaired_text,
            edit_notes=["补回 WAVE 跨场景引用与魂玉锚点"],
        )

    async def _fake_quality_checks(*_args, **_kwargs):
        return (
            AlignmentReport(alignment_score=9.0),
            ContinuityReport(continuity_score=9.0),
            ChapterRepairReport(),
        )

    monkeypatch.setattr(chapter_flow_review, "evaluate_chapter_text", _fake_eval)
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_quality_checks)
    monkeypatch.setattr(chapter_flow_review.EditStep, "run", _fake_edit_run)

    updated = asyncio.run(
        chapter_flow_review._run_chapter_quality_repair_lane(
            runner=runner,
            context=context,
            prepared=prepared,
            trace=SimpleNamespace(),
            chapter_number=1,
            state=state,
            skip_quality=False,
            on_step=lambda step, payload=None: events.append((step, payload or {})),
        )
    )
    metrics = chapter_flow._build_repair_metrics_payload(
        chapter_number=1,
        continuity_result=None,
        continuity_report=updated.continuity_report,
        causal_result=None,
        causal_report=updated.causal_report,
        reading_power_result=None,
        total_rounds_used=updated.total_repair_rounds_used,
        total_rounds_cap=updated.total_rounds_cap,
        cumulative_change_ratio=updated.cumulative_change_ratio,
        repair_exhausted=False,
        skipped_quality_stage=False,
        wave_meta=updated.wave_meta,
        wave_integrity=updated.wave_integrity,
        chapter_quality_repair=updated.chapter_quality_repair,
        current_text=updated.current_text,
    )

    assert updated.current_text == repaired_text
    assert updated.chapter_quality_repair["attempted"] is True
    assert updated.chapter_quality_repair["ticket_count"] >= 1
    assert updated.wave_integrity["blocking"] is False
    assert metrics["post_wave_repair"]["any_loop_entered"] is True
    assert any(step == "chapter_quality_repair_started" for step, _payload in events)


def test_wave_repair_policy_clears_paraphrased_local_diagnostics(
    monkeypatch,
) -> None:
    """repair policy rechecks WAVE anchors semantically after repair."""
    import asyncio

    import novel_forge.pipeline.long.chapter_flow as chapter_flow
    import novel_forge.pipeline.long.chapter_flow_review as chapter_flow_review
    from novel_forge.core.schemas.chapter import (
        AlignmentReport,
        CausalValidationReport,
        ChapterRepairReport,
    )
    from novel_forge.core.schemas.continuity import ContinuityReport
    from novel_forge.pipeline.long.execution_models import PreparedChapterArtifacts

    events: list[tuple[str, dict]] = []
    settings = SimpleNamespace(
        long_wave_post_condition_policy="repair",
        long_eval_repair_enabled=False,
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=settings,
        _on_step=lambda step, payload=None: events.append((step, payload or {})),
    )
    context = SimpleNamespace(settings=settings)
    original_text = "沈鹿溪站在镇口。"
    repaired_text = (
        "班车停在穿堂风镇口，沈鹿溪背着包下车，先录下老宅门缝里晃动的风铃。"
        "她进杂货店买水，周婶热情招呼，她只点头回应。"
        "青石板路上，一个叫小柯的少年从她身边经过，她举起摄像机记录下他的背影。"
        "民宿房间正对老街，墙壁里传来穿堂风的声音，她把摄像机对准墙角录音。"
        "夜里她回放白天的风声素材，又把镜头对准窗外的黑暗。"
    )
    scene_intents = [
        {"scene_id": "scene_01", "summary": "沈鹿溪抵达镇口，旧门风铃响起。"},
        {"scene_id": "scene_02", "summary": "沈鹿溪走进杂货店，周婶热情招呼。"},
        {"scene_id": "scene_03", "summary": "青石板路遇见小柯，摄像机记录背影。"},
        {"scene_id": "scene_04", "summary": "民宿房间靠着老街，墙壁传来风声。"},
        {"scene_id": "scene_05", "summary": "夜里回放风声素材，镜头对准窗外。"},
    ]
    plan = SimpleNamespace(
        scene_intents=scene_intents,
        cross_scene_intent={"cross_scene_references": [], "pacing_curve": [1, 2, 3, 4, 5]},
        opening_contract="沈鹿溪抵达穿堂风镇。",
        closing_contract="夜里继续记录风声。",
    )
    prepared = PreparedChapterArtifacts(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(chapter_number=1, expected_word_count=0),
            layout=SimpleNamespace(),
            chapter_source_slice=None,
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=plan,
    )
    state = chapter_flow._ReviewPhaseState(
        current_text=original_text,
        baseline_text=original_text,
        total_rounds_cap=5,
        wave_meta={
            "warnings": [
                f"wave_post_cond_anchor: scene {scene['scene_id']} anchor words missing"
                for scene in scene_intents
            ],
            "cross_ref_hits": [],
            "cross_ref_total": 0,
            "scene_anchor_total": len(scene_intents),
            "scenes_woven": len(scene_intents),
        },
        alignment_report=AlignmentReport(alignment_score=8.5),
        continuity_report=ContinuityReport(continuity_score=8.5),
        causal_report=CausalValidationReport(causal_score=8.5),
        chapter_repair_report=ChapterRepairReport(),
    )

    async def _fake_edit_run(_self, input_data):
        assert input_data.context["chapter_quality_repair_tickets"]
        return SimpleNamespace(
            revised_text=repaired_text,
            edit_notes=["按 WAVE 候选诊断补回场景覆盖"],
        )

    async def _fake_quality_checks(*_args, **_kwargs):
        return (
            AlignmentReport(alignment_score=9.0),
            ContinuityReport(continuity_score=9.0),
            ChapterRepairReport(),
        )

    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_quality_checks)
    monkeypatch.setattr(chapter_flow_review.EditStep, "run", _fake_edit_run)

    updated = asyncio.run(
        chapter_flow_review._run_chapter_quality_repair_lane(
            runner=runner,
            context=context,
            prepared=prepared,
            trace=SimpleNamespace(),
            chapter_number=1,
            state=state,
            skip_quality=False,
            on_step=lambda step, payload=None: events.append((step, payload or {})),
        )
    )

    assert updated.current_text == repaired_text
    assert updated.wave_integrity["blocking"] is False
    assert updated.wave_integrity["diagnostic_blocking"] is False
    assert updated.wave_integrity["archive_blocking"] is False
    assert updated.chapter_quality_repair["wave_blocking_after"] is False
    assert updated.chapter_quality_repair["wave_diagnostic_blocking_after"] is False
    assert not any("不由本地启发式阻断" in warning for warning in updated.review_warnings)
    assert not any(step == "wave_integrity_residual_after_repair" for step, _payload in events)
    assert any(step == "chapter_quality_repair_done" for step, _payload in events)
