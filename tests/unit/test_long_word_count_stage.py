from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.utils.text_validation import assess_word_count
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.stages.word_count import (
    WordCountRestructureResult,
    build_word_count_contract_anchors,
    load_word_count_rejection_count,
    measure_anchor_coverage,
    record_word_count_rejection,
    word_count_archive_gate_max_rejections,
    word_count_rejection_limit_reached,
)


def test_build_word_count_contract_anchors_uses_plan_bridge_and_guard() -> None:
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            goal="确认旧照片来源",
            main_plot_points=["主角拿到怀表线索"],
            subplot_points=["竞争对手施压"],
        )
    )
    bridge = SimpleNamespace(
        action_handoff="承接上一章门口对峙",
        bridge_summary="从会场转入古董店调查",
        emotional_carryover="怀疑与克制",
        pending_questions=["怀表为何发烫"],
        sensory_anchors=["钟声"],
        causal_link=SimpleNamespace(
            previous_event="上一章收到名片",
            causal_mechanism="名片触发调查",
            unresolved_question="陆云峥是否知情",
            open_threads=["金镯刻字"],
        ),
    )
    scene = SimpleNamespace(
        summary="进入古董店",
        purpose="取得老照片",
        conflict="店主试探",
        required_outcome="确认照片来自外滩钟楼",
        exit_target_state="主角带走照片",
        relationship_dynamics="双方从防备转为有限合作",
        required_characters=["沈念卿", "陆云峥"],
    )
    plan = SimpleNamespace(
        opening_contract="从上一章疑问切入",
        closing_contract="以照片线索收束",
        required_state_transitions=["沈念卿从回避到追查"],
        key_revelations=["照片上的钟楼年份异常"],
        relationship_evolution=["互相试探"],
        intentional_callbacks=["怀表发烫"],
        scene_intents=[scene],
    )
    packet = SimpleNamespace(
        must_carry_forward=["上一章的名片"],
        guard_constraints=["不得删除怀表发烫"],
    )

    anchors = build_word_count_contract_anchors(
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
    )

    assert "确认旧照片来源" in anchors
    assert "承接上一章门口对峙" in anchors
    assert "确认照片来自外滩钟楼" in anchors
    assert "不得删除怀表发烫" in anchors


def test_measure_anchor_coverage_detects_preserved_terms() -> None:
    anchors = (
        "确认照片来自外滩钟楼",
        "怀表为何发烫",
        "沈念卿从回避到追查",
    )
    text = "沈念卿拿起照片，照片里的外滩钟楼清晰可见。她按住怀表，金属忽然发烫。"

    coverage = measure_anchor_coverage(text, anchors)

    assert coverage.total == 3
    assert coverage.covered >= 2


def test_word_count_rejection_count_persists_per_chapter(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    before = assess_word_count("字" * 6900, 4200)
    result = WordCountRestructureResult(
        text="字" * 6900,
        changed=False,
        accepted=False,
        mode="structural_compress",
        reason="candidate_still_outside_buffer",
        before=before,
        after=before,
        anchors=(),
        coverage_before=measure_anchor_coverage("", ()),
        coverage_after=measure_anchor_coverage("", ()),
    )

    assert load_word_count_rejection_count(storage, layout, 2) == 0
    assert record_word_count_rejection(storage, layout, 2, result) == 1
    assert record_word_count_rejection(storage, layout, 2, result) == 2
    assert load_word_count_rejection_count(storage, layout, 2) == 2


def test_word_count_rejection_limit_policy() -> None:
    settings = SimpleNamespace(long_word_count_archive_gate_max_rejections=2)

    assert word_count_archive_gate_max_rejections(settings) == 2
    assert word_count_rejection_limit_reached(settings, 1) is False
    assert word_count_rejection_limit_reached(settings, 2) is True
    assert word_count_rejection_limit_reached(
        SimpleNamespace(long_word_count_archive_gate_max_rejections=0),
        0,
    ) is True
