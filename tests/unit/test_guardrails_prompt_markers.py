"""Tests for centralized prompt-leak markers."""

from __future__ import annotations

from novel_forge.core.domain.guardrails import (
    IN_WORLD_TEXT_VERDICT,
    KNOWN_PROMPT_MARKERS,
    PROMPT_LEAK_VERDICT,
    classify_prompt_leak_candidate,
    confirmed_reported_prompt_leaks,
    detect_invalid_time_markers,
    detect_pov_intrusion,
    detect_prompt_leaks,
    normalize_invalid_time_markers,
    scrub_prompt_artifacts,
    text_has_transition_signal,
)


def test_known_prompt_markers_detect_space_separated_variants() -> None:
    hits = detect_prompt_leaks("角色提出了 B 方案，正文里不该保留这种痕迹。")

    assert "B 方案" in hits


def test_detect_prompt_leaks_merges_known_and_extra_markers() -> None:
    hits = detect_prompt_leaks(
        "这里先写【交接】，再写自定义痕迹。",
        extra_markers=("自定义痕迹",),
    )

    assert "【交接】" in KNOWN_PROMPT_MARKERS
    assert "【交接】" in hits
    assert "自定义痕迹" in hits


def test_detect_prompt_leaks_allows_narrative_markers() -> None:
    hits = detect_prompt_leaks("【闪回】她听见旧日雨声，又在铜灯前醒来。")

    assert hits == []


def test_reported_prompt_leak_ignores_in_world_bracketed_title() -> None:
    text = "邮件标题赫然写着：【紧急通知】关于贵司订单交付计划的调整说明。"

    assert classify_prompt_leak_candidate(text, "【紧急通知】") == IN_WORLD_TEXT_VERDICT
    assert confirmed_reported_prompt_leaks(text, ["【紧急通知】"]) == []


def test_reported_prompt_leak_confirms_known_prompt_marker() -> None:
    text = "她刚踏进门，正文里却混进【交接】这样的规划标记。"

    assert classify_prompt_leak_candidate(text, "【交接】") == PROMPT_LEAK_VERDICT
    assert confirmed_reported_prompt_leaks(text, ["【交接】"]) == ["【交接】"]


def test_detect_prompt_leaks_catches_continuity_meta_language() -> None:
    hits = detect_prompt_leaks("延续上章末尾的视角与情绪，此处的状态承接上章退场时的情绪标签。")

    assert "延续上章末尾的视角与情绪" in hits
    assert "此处的状态承接上章退场时的情绪标签" in hits


def test_detect_prompt_leaks_catches_planning_leak_sentences() -> None:
    hits = detect_prompt_leaks(
        "完成场景转换至禁室，以环境细节锚定新空间，留出悬念钩子。"
        "禁室中等待审判的她不知命运将何去何从，为第二章罗浮现身禁室埋下伏笔。"
    )

    assert any("完成场景转换" in item for item in hits)
    assert any("埋下伏笔" in item for item in hits)


def test_detect_prompt_leaks_catches_rule_and_evidence_labels() -> None:
    hits = detect_prompt_leaks("WR-001之律要求保密。\n辨真：药味与记录吻合。\n疑己：仍需复核。")

    assert any("WR-001之律" in item for item in hits)
    assert any("辨真：" in item for item in hits)
    assert any("疑己：" in item for item in hits)


def test_scrub_prompt_artifacts_removes_planning_leaks() -> None:
    original = (
        "殿门在身后合上，黑暗骤然压了下来。"
        "完成场景转换至禁室，以环境细节锚定新空间，留出悬念钩子。"
        "禁室中等待审判的她不知命运将何去何从，为第二章罗浮现身禁室埋下伏笔。"
        "她贴着潮湿石墙缓缓坐下，听见远处更漏。"
    )

    cleaned, removed = scrub_prompt_artifacts(original)

    assert "完成场景转换至禁室" not in cleaned
    assert "为第二章罗浮现身禁室埋下伏笔" not in cleaned
    assert "她贴着潮湿石墙缓缓坐下" in cleaned
    assert removed


def test_detect_prompt_leaks_catches_meta_narrative_closing_language() -> None:
    hits = detect_prompt_leaks("这是一个未决的线索。金丝蜃气纠缠形成呼应，这些伏笔终将回收。")

    assert any("未决的线索" in item for item in hits)
    assert any("这些伏笔终将回收" in item for item in hits)


def test_scrub_prompt_artifacts_removes_meta_narrative_summary_sentences() -> None:
    # "这是一个未决的线索" is a generic meta-narrative summary sentence — must be scrubbed.
    # "暗示动物亦受异力影响" was previously scrubbed as a project-specific phrase but is
    # no longer treated as a guardrail pattern (world-specific vocab should not be hardcoded).
    original = (
        "她把灯芯按回盏中，烫得指腹微麻。这是一个未决的线索。她没有回头，只听见窗外风铃一响。"
    )

    cleaned, removed = scrub_prompt_artifacts(original)

    assert "未决的线索" not in cleaned
    assert "她把灯芯按回盏中" in cleaned
    assert "她没有回头" in cleaned
    assert removed


def test_detect_prompt_leaks_catches_blueprint_label_leaks() -> None:
    hits = detect_prompt_leaks(
        "**情感障碍落地场景——第一阶段：对规则的盲从**"
        "她深吸一口气。"
        "**暴露风险信号：她说话时握紧了袖口。**"
    )

    assert any("落地场景" in item for item in hits)
    assert any("暴露风险信号" in item for item in hits)


def test_scrub_prompt_artifacts_removes_blueprint_label_leaks() -> None:
    original = (
        "她将这卷书放在火边。"
        "**情感障碍落地场景——第一阶段：对规则的盲从**"
        "她没有抬头。"
        "**暴露风险信号：她说话时握紧了袖口。**"
        "火星在她指尖下炸开。"
    )

    cleaned, removed = scrub_prompt_artifacts(original)

    assert "情感障碍落地场景" not in cleaned
    assert "暴露风险信号" not in cleaned
    assert "她将这卷书放在火边" in cleaned
    assert "火星在她指尖下炸开" in cleaned
    assert removed


def test_detect_invalid_time_markers_flags_overflowed_ke() -> None:
    findings = detect_invalid_time_markers("亥初六刻，铜漏仍在滴答。")
    assert findings
    assert findings[0]["marker"] == "亥初六刻"


def test_normalize_invalid_time_markers_replaces_only_overflowed_ke() -> None:
    normalized, replacements = normalize_invalid_time_markers(
        "亥初六刻，铜漏仍在滴答。子时三刻，风雪仍未停。"
    )

    assert "亥时末" in normalized
    assert "子时三刻" in normalized
    assert replacements == [{"marker": "亥初六刻", "replacement": "亥时末"}]
    assert detect_invalid_time_markers(normalized) == []


def test_detect_pov_intrusion_catches_soft_cognitive_inference() -> None:
    intrusions = detect_pov_intrusion(
        "赵元真实动摇了一瞬，却仍把折扇扣回袖中。",
        pov_character="风伏京",
        character_names=["风伏京", "赵元"],
        pov_scope="limited",
    )
    assert intrusions
    assert intrusions[0]["intrusion_type"] in {"cognitive_inference", "inner_thought"}


def test_detect_pov_intrusion_allows_omniscient_scope() -> None:
    intrusions = detect_pov_intrusion(
        "赵元心里明白风伏京已经起疑，却仍把折扇扣回袖中。",
        pov_character="风伏京",
        character_names=["风伏京", "赵元"],
        pov_scope="omniscient",
    )
    assert intrusions == []


def test_detect_pov_intrusion_objective_scope_does_not_use_limited_rule() -> None:
    intrusions = detect_pov_intrusion(
        "赵元心里明白风伏京已经起疑，却仍把折扇扣回袖中。",
        pov_character="风伏京",
        character_names=["风伏京", "赵元"],
        pov_scope="objective",
    )
    assert intrusions == []


def test_transition_signal_detects_return_route_verbs() -> None:
    assert text_has_transition_signal("她绕回风府西角门，翻墙入院。")


def test_transition_signal_detects_english_motion_verbs() -> None:
    assert text_has_transition_signal("She slipped back into the manor before dawn.")
