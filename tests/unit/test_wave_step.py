"""Tests for WaveStep (single-pass scene weaving for long-form chapters).

The wave step is the second half of the 6-phase Generate stage.  Unlike
``EditStep`` (multi-round), wave runs **exactly once** and records any
post-condition failures as warnings on ``WaveOutput.warnings`` rather than
raising.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.review.review_contracts import compile_repair_tickets_from_findings
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.long.services.quality.wave_integrity import (
    assess_wave_integrity,
    assess_wave_integrity_against_plan,
    wave_integrity_to_findings,
)
from novel_forge.pipeline.steps.wave_step import WaveInput, WaveOutput, WaveStep


def _settings() -> Settings:
    return Settings(_env_file=None)


@pytest.mark.parametrize("requirement", [{}, {"satisfaction": "optional"}])
def test_missing_optional_callback_cannot_create_a_repair_ticket(requirement):
    result = assess_wave_integrity_against_plan(
        current_text="她走出屋子，把钥匙交给同伴。",
        plan=SimpleNamespace(
            scene_intents=[],
            cross_scene_intent={
                "cross_scene_references": [
                    {
                        "description": "雨声反复敲窗",
                        "from_scene": "s1",
                        "to_scene": "s2",
                        "ref_type": "echo",
                        "requirement": requirement,
                    }
                ],
            },
        ),
        target_word_count=0,
    )
    assert not result.blocking
    assert result.metrics["cross_ref_total"] == 0
    assert wave_integrity_to_findings(result, chapter_number=2, current_text_hash="current") == []


# Anchor phrase used as the cross-ref description.  Kept short and distinctive
# so the substring check inside WaveStep's post-condition logic is reliable.
_CROSSREF_ANCHOR = "雾霭街道的灯摇晃着"


def _scene_intents() -> list[dict]:
    return [
        {
            "scene_id": "scene_01",
            "summary": "林远在雾霭街道与街灯互动",
            "purpose": "推进主线",
        },
        {
            "scene_id": "scene_02",
            "summary": "林远走进图书馆与老守夜人交谈",
            "purpose": "引入新角色",
        },
    ]


def _cross_scene_intent() -> dict:
    return {
        "cross_scene_references": [
            {
                "from_scene": "scene_01",
                "to_scene": "scene_02",
                "ref_type": "callback",
                "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                "description": _CROSSREF_ANCHOR,
            },
        ],
        "pacing_curve": [2, 4],  # length matches scene count
    }


def _woven_text() -> str:
    """A draft that includes the cross-ref anchor verbatim and the POV name."""
    pov = "林远"
    body = (
        f"{pov}站在{_CROSSREF_ANCHOR}，街灯在夜色里晃动。\n\n"
        "他穿过街道走向图书馆，街角的风把斗篷掀起来。\n\n"
        "老守夜人已经在门口等他，手里提着一盏旧灯笼。\n\n"
        f"{pov}走进图书馆，{_CROSSREF_ANCHOR}，光在墙角跳动。"
    )
    return body


def test_wave_integrity_classifies_critical_post_conditions_as_blocking() -> None:
    result = assess_wave_integrity(
        {
            "warnings": [
                "wave_post_cond_cross_ref: hit 0/6 (0%) < 80%",
                "wave_post_cond_anchor: scene scene_01 anchor words missing",
                "wave_post_cond_anchor: scene scene_02 anchor words missing",
                "wave_post_cond_anchor: scene scene_03 anchor words missing",
                "wave_post_cond_anchor: scene scene_04 anchor words missing",
                "wave_post_cond_word_count: 100 words not in [700, 1300] (target 1000 +/-30%)",
            ],
            "cross_ref_hits": [],
            "cross_ref_total": 6,
            "scene_anchor_total": 4,
            "scenes_woven": 4,
        },
        policy="repair",
    )

    issue_types = {issue.issue_type for issue in result.issues}
    assert result.blocking is True
    assert result.archive_blocking is False
    # Aggregate historical counts are not proof of a mandatory reference.
    assert issue_types == {
        "wave_scene_anchors_missing",
        "wave_word_count_out_of_range",
    }
    assert result.metrics["cross_ref_hit_count"] == 0
    assert result.metrics["anchor_missing_ratio"] == pytest.approx(1.0)
    assert result.metrics["diagnostic_blocking"] is True
    assert result.metrics["archive_blocking"] is False

    blocked = assess_wave_integrity(
        {
            "warnings": ["wave_post_cond_cross_ref: hit 0/1 (0%) < 80%"],
            "cross_ref_hits": [],
            "cross_ref_total": 1,
        },
        policy="block",
    )
    assert blocked.blocking is False
    assert blocked.archive_blocking is False
    assert blocked.model_dump()["archive_blocking"] is False


def test_wave_integrity_word_count_inherits_disabled_archive_gate() -> None:
    result = assess_wave_integrity(
        {
            "warnings": [
                "wave_post_cond_word_count: 100 words not in [700, 1300] (target 1000 +/-30%)",
            ],
        },
        policy="repair",
        word_count_policy="inherit",
        archive_gate_enabled=False,
    )

    assert result.blocking is False
    assert [issue.issue_type for issue in result.issues] == ["wave_word_count_out_of_range"]
    assert result.issues[0].blocking is False
    assert result.metrics["word_count_policy"] == "inherit"
    assert result.metrics["archive_gate_enabled"] is False
    assert result.metrics["word_count_blocking"] is False


def test_wave_integrity_word_count_can_be_enforced_when_archive_gate_disabled() -> None:
    result = assess_wave_integrity(
        {
            "warnings": [
                "wave_post_cond_word_count: 100 words not in [700, 1300] (target 1000 +/-30%)",
            ],
        },
        policy="repair",
        word_count_policy="enforce",
        archive_gate_enabled=False,
    )

    assert result.blocking is True
    assert result.issues[0].issue_type == "wave_word_count_out_of_range"
    assert result.issues[0].blocking is True
    assert result.metrics["word_count_policy"] == "enforce"
    assert result.metrics["word_count_blocking"] is True


def test_wave_integrity_keeps_minor_post_conditions_as_warning_only() -> None:
    result = assess_wave_integrity(
        {
            "warnings": [
                "wave_post_cond_cross_ref: hit 3/6 (50%) < 80%",
                "wave_post_cond_anchor: scene scene_01 anchor words missing",
            ],
            "cross_ref_hits": ["回声一", "回声二", "回声三"],
            "cross_ref_total": 6,
            "scene_anchor_total": 5,
            "scenes_woven": 5,
        },
        policy="repair",
    )

    assert result.blocking is False
    assert result.issues == []
    assert result.metrics["warning_count"] == 2


def test_wave_integrity_against_plan_accepts_paraphrased_anchors() -> None:
    plan = SimpleNamespace(
        cross_scene_intent={
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                    "description": "沈鹿溪清晨六分钟的风声录制回响到楼顶对话",
                }
            ]
        },
        scene_intents=[
            {
                "scene_id": "scene_01",
                "summary": "沈鹿溪背着摄影包在民宿木梯旁准备录风声",
            }
        ],
    )
    text = (
        "清晨，沈鹿溪背起摄影包，民宿木梯在脚下轻响。"
        "她按下录制键，让风声在镜头里停了整整六分钟。"
        "楼顶谈话开始时，那段风声又被她想起。"
    )

    result = assess_wave_integrity_against_plan(
        current_text=text,
        plan=plan,
        target_word_count=0,
        policy="block",
    )

    assert result.blocking is False
    assert result.metrics["cross_ref_hit_count"] == 1
    assert result.metrics["anchor_missing_count"] == 0


def test_wave_integrity_blocks_missing_required_dialogue_literal() -> None:
    literal = "有些事情不是你想知道就能知道的"
    plan = SimpleNamespace(
        cross_scene_intent={},
        required_literals=[
            {
                "contract_id": "secret-boundary",
                "literal": literal,
                "scene_id": "scene_02",
                "reason": "后文逐字回指",
                "placement_hint": "糖水铺对话中",
            }
        ],
        scene_intents=[
            {
                "scene_id": "scene_02",
                "summary": "团队在糖水铺聚餐",
                "required_outcome": "林小满说「有些事情不是你想知道就能知道的」",
            }
        ],
    )

    result = assess_wave_integrity_against_plan(
        current_text="他们路过糖水铺，却没有停下。",
        plan=plan,
        target_word_count=0,
        policy="repair",
    )

    literal_issues = [
        issue for issue in result.issues if issue.issue_type == "wave_required_literal_missing"
    ]
    assert result.blocking is True
    assert len(literal_issues) == 1
    assert literal_issues[0].metadata["objective_check"] is True
    assert result.metrics["missing_required_literal_count"] == 1


def test_wave_integrity_projects_each_blocking_item_to_precise_ticket() -> None:
    result = assess_wave_integrity(
        {
            "warnings": [
                "wave_post_cond_cross_ref: hit 0/2 (0%) < 80%",
                "wave_post_cond_anchor: scene scene_01 anchor words missing",
                "wave_post_cond_anchor: scene scene_02 anchor words missing",
            ],
            "cross_ref_hits": [],
            "cross_ref_total": 2,
            "cross_ref_expected": [
                {
                    "index": 1,
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                    "description": "雾霭街道的灯摇晃着",
                },
                {
                    "index": 2,
                    "from_scene": "scene_02",
                    "to_scene": "scene_03",
                    "ref_type": "echo",
                    "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                    "description": "旧灯笼又暗了一下",
                },
            ],
            "scene_anchor_total": 2,
            "scene_anchor_expected": [
                {
                    "scene_id": "scene_01",
                    "summary": "林远在雾霭街道与街灯互动",
                    "anchor_words": ["林远在雾霭街道与街灯互动"],
                },
                {
                    "scene_id": "scene_02",
                    "summary": "林远走进图书馆与老守夜人交谈",
                    "anchor_words": ["林远走进图书馆与老守夜人交谈"],
                },
            ],
            "scenes_woven": 2,
        },
        policy="repair",
    )

    issue_ids = [issue.issue_id for issue in result.issues]
    assert result.blocking is True
    assert len(issue_ids) == 4
    assert len(set(issue_ids)) == 4
    assert [issue.issue_type for issue in result.issues].count("wave_cross_ref_missing") == 2
    assert [issue.issue_type for issue in result.issues].count("wave_scene_anchors_missing") == 2

    findings = wave_integrity_to_findings(
        result,
        chapter_number=1,
        current_text_hash="hash-before",
    )
    tickets = compile_repair_tickets_from_findings(findings)

    assert len(tickets) == 4
    assert all(ticket.ticket_id and ticket.finding_ids for ticket in tickets)
    assert all(ticket.postconditions for ticket in tickets)
    assert {ticket.metadata.get("source_text_hash") for ticket in tickets} == {"hash-before"}


def test_wave_integrity_skipped_meta_downgrades_cross_ref_and_anchors() -> None:
    result = assess_wave_integrity(
        {
            "wave_skipped": True,
            "warnings": [
                "wave_post_cond_cross_ref: hit 0/1 (0%) < 80%",
                "wave_post_cond_anchor: scene scene_01 anchor words missing",
            ],
            "cross_ref_hits": [],
            "cross_ref_total": 1,
            "scene_anchor_total": 1,
        },
        policy="repair",
    )

    assert result.blocking is False
    assert result.issues == []
    assert result.metrics["wave_skipped"] is True


def test_wave_integrity_against_plan_preserves_skipped_state() -> None:
    plan = SimpleNamespace(
        cross_scene_intent={
            "cross_scene_references": [{"description": "Alpha Beta"}],
        },
        scene_intents=[{"scene_id": "scene_01", "summary": "Gamma Delta"}],
    )

    result = assess_wave_integrity_against_plan(
        current_text="unrelated text",
        plan=plan,
        target_word_count=0,
        wave_skipped=True,
    )

    assert result.blocking is False
    assert result.metrics["wave_skipped"] is True
    assert any(warning.startswith("wave_degraded_anchor:") for warning in result.warnings)
    assert {issue.issue_type for issue in result.issues} == set()


def _override_router(monkeypatch, router, text: str) -> None:
    """Patch ``router.route`` to return *text* directly (skip the LLM call)."""

    async def _route(request):
        from types import SimpleNamespace

        return SimpleNamespace(content=text)

    async def _stream_route(request, *, provider=None, on_delta=None, on_chunk=None):
        from types import SimpleNamespace

        if on_delta is not None:
            on_delta(text)
        if on_chunk is not None:
            on_chunk(text)
        return SimpleNamespace(content=text)

    monkeypatch.setattr(router, "route", _route)
    monkeypatch.setattr(router, "stream_route", _stream_route)


@pytest.mark.asyncio
async def test_wave_step_basic_flow_keeps_woven_prose(monkeypatch, router, builder) -> None:
    """Basic flow: the wave step forwards the model output as woven prose."""
    text = _woven_text()
    _override_router(monkeypatch, router, text)
    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=text,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(text),
            cross_scene_intent=_cross_scene_intent(),
            scene_intents=_scene_intents(),
        )
    )
    assert isinstance(result, WaveOutput)
    assert result.woven_prose == text
    assert result.warnings == []
    assert result.final_word_count == count_chapter_words(text)
    assert result.cross_ref_hits == [_CROSSREF_ANCHOR]


@pytest.mark.asyncio
async def test_wave_step_streaming_contract_leak_keeps_original_draft(
    monkeypatch, router, builder
) -> None:
    """Invalid streamed WAVE text is handled by WaveStep's draft-preserving fallback."""
    draft = _woven_text()
    leaked_wave_text = (
        "scene_intent：\n"
        "scene_01：先整理邺城水阁情报。\n"
        "scene_02：再转入账房。\n"
        "scene_03：最后制造伏笔。\n"
    )
    _override_router(monkeypatch, router, leaked_wave_text)

    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=draft,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(draft),
            cross_scene_intent=_cross_scene_intent(),
            scene_intents=_scene_intents(),
        )
    )

    assert result.woven_prose == draft
    assert result.final_word_count == count_chapter_words(draft)
    assert any("wave skipped" in warning for warning in result.warnings)
    assert any("scene_intent" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_wave_step_rule_or_evidence_leak_keeps_original_draft(
    monkeypatch, router, builder
) -> None:
    draft = _woven_text()
    _override_router(monkeypatch, router, "WR-001之律\n辨真：这不是正文。")

    result = await WaveStep(router, builder, settings=_settings()).run(
        WaveInput(
            chapter_text=draft,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(draft),
            cross_scene_intent=_cross_scene_intent(),
            scene_intents=_scene_intents(),
        )
    )

    assert result.woven_prose == draft
    assert any("WR-001之律" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_wave_step_post_conditions_all_pass(monkeypatch, router, builder) -> None:
    """All 5 post-conditions hold; the warning list stays empty."""
    text = _woven_text()
    _override_router(monkeypatch, router, text)
    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=text,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(text),
            cross_scene_intent=_cross_scene_intent(),
            scene_intents=_scene_intents(),
        )
    )
    assert result.warnings == []
    # pacing_curve length matches scene count
    assert all("pacing" not in w for w in result.warnings)
    # word count within +/-30%
    assert all("word_count" not in w for w in result.warnings)


@pytest.mark.asyncio
async def test_wave_step_post_conditions_accept_paraphrased_anchors(
    monkeypatch,
    router,
    builder,
) -> None:
    scene_intents = [
        {
            "scene_id": "scene_01",
            "summary": "沈鹿溪背着摄影包在民宿木梯旁准备录风声",
        }
    ]
    cross_scene = {
        "cross_scene_references": [
            {
                "from_scene": "scene_01",
                "to_scene": "scene_02",
                "ref_type": "callback",
                "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                "description": "沈鹿溪清晨六分钟的风声录制回响到楼顶对话",
            }
        ],
        "pacing_curve": [3],
    }
    text = (
        "清晨，沈鹿溪背起摄影包，民宿木梯在脚下轻响。"
        "她按下录制键，让风声在镜头里停了整整六分钟。"
        "楼顶谈话开始时，那段风声又被她想起。"
    )
    _override_router(monkeypatch, router, text)
    step = WaveStep(router, builder, settings=_settings())

    result = await step.run(
        WaveInput(
            chapter_text=text,
            context={"stage_cards": {"plan": {}, "scene_intents": scene_intents}},
            target_word_count=count_chapter_words(text),
            cross_scene_intent=cross_scene,
            scene_intents=scene_intents,
        )
    )

    assert result.cross_ref_hits == ["沈鹿溪清晨六分钟的风声录制回响到楼顶对话"]
    assert all("wave_post_cond_cross_ref" not in warning for warning in result.warnings)
    assert all("wave_post_cond_anchor" not in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_wave_step_post_condition_failures_write_warnings(
    monkeypatch, router, builder
) -> None:
    """Word count, pacing length, and cross-ref hit ratio all fail ->
    WaveStep records the three corresponding warnings without raising."""
    scene_intents = _scene_intents()  # 2 scenes
    bad_cross_scene = {
        "cross_scene_references": [
            {
                "from_scene": "scene_01",
                "to_scene": "scene_02",
                "ref_type": "callback",
                "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                "description": "anchor phrase that is nowhere in the draft",
            },
            {
                "from_scene": "scene_02",
                "to_scene": "scene_02",
                "ref_type": "echo",
                "requirement": {"satisfaction": "narrative", "source": "chapter_contract"},
                "description": "another phrase that is not in the draft either",
            },
        ],
        # pacing_curve length 3 != scene count 2
        "pacing_curve": [2, 3, 5],
    }
    # Very short draft — well below 70% of the requested target.
    draft = "短文本。" * 3
    _override_router(monkeypatch, router, draft)
    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=draft,
            context={"stage_cards": {"plan": {}, "scene_intents": scene_intents}},
            pov_character="林远",
            target_word_count=2000,
            cross_scene_intent=bad_cross_scene,
            scene_intents=scene_intents,
        )
    )
    joined = "\n".join(result.warnings)
    assert "wave_post_cond_word_count" in joined
    assert "wave_post_cond_pacing" in joined
    assert "wave_post_cond_cross_ref" in joined
    # cross_ref_hits should be empty because neither description is in the text
    assert result.cross_ref_hits == []


@pytest.mark.asyncio
async def test_wave_step_ignores_empty_cross_scene_reference_descriptions(
    monkeypatch, router, builder
) -> None:
    """Empty cross-scene reference shells are metadata, not failed anchors."""
    text = _woven_text()
    _override_router(monkeypatch, router, text)
    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=text,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(text),
            cross_scene_intent={
                "cross_scene_references": [
                    {"from_scene": "scene_01", "to_scene": "scene_02", "description": ""},
                    {"from_scene": "scene_02", "to_scene": "scene_03"},
                ],
                "pacing_curve": [2, 4],
            },
            scene_intents=_scene_intents(),
        )
    )

    assert all("wave_post_cond_cross_ref" not in warning for warning in result.warnings)
    assert result.cross_ref_hits == []


@pytest.mark.asyncio
async def test_wave_step_preserves_pov(monkeypatch, router, builder) -> None:
    """POV consistency is enforced as a warning, not an exception."""
    # Draft does NOT mention the POV name "林远" in the head paragraphs,
    # so the POV consistency check should warn.
    draft = "街灯在晃动。雾霭笼罩着一切。" * 50
    _override_router(monkeypatch, router, draft)
    step = WaveStep(router, builder, settings=_settings())
    result = await step.run(
        WaveInput(
            chapter_text=draft,
            context={"stage_cards": {"plan": {}, "scene_intents": _scene_intents()}},
            pov_character="林远",
            target_word_count=count_chapter_words(draft),
            cross_scene_intent={"cross_scene_references": [], "pacing_curve": [2, 4]},
            scene_intents=_scene_intents(),
        )
    )
    assert any("pov" in w.lower() for w in result.warnings)
    # No exception was raised; the step returned a WaveOutput.
    assert isinstance(result, WaveOutput)
