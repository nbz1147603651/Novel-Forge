from __future__ import annotations

from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance


def test_repair_attempt_guidance_rotates_strategy_and_explains_stagnation() -> None:
    guidance = build_repair_attempt_guidance(
        domain="causal",
        round_number=2,
        max_rounds=3,
        issues=[
            {
                "issue_type": "event_without_cause",
                "summary": "角色突然决定去码头，缺少触发信息。",
            }
        ],
        previous_issues=[
            {
                "issue_type": "event_without_cause",
                "summary": "角色突然决定去码头，缺少触发信息。",
            }
        ],
        current_score=6.1,
        previous_score=6.0,
        score_threshold=8.0,
        previous_strategy="single_sentence_patch",
    )

    assert guidance["strategy_id"] == "scene_causality"
    assert "本轮必须更换表达通道" in "；".join(guidance["diagnosis"])
    assert "机械补句" in "；".join(guidance["diagnosis"])
    assert guidance["previous_strategy"] == "single_sentence_patch"
    assert guidance["issue_focus"] == ["角色突然决定去码头，缺少触发信息。"]


def test_repair_attempt_guidance_detects_score_drop() -> None:
    guidance = build_repair_attempt_guidance(
        domain="guard",
        round_number=1,
        max_rounds=2,
        issues=[{"issue_type": "guard_constraint_missing", "summary": "缺少护栏约束兑现。"}],
        current_score=2.6,
        previous_score=6.6,
        score_threshold=8.0,
    )

    diagnosis = "；".join(guidance["diagnosis"])
    assert "分数下降 4.0" in diagnosis
    assert "恢复被稀释的主线/因果锚点" in diagnosis
    assert guidance["new_direction"]
