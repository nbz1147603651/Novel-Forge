"""Tests for causal validation issue anchoring."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.continuity import ChapterBridge
from novel_forge.pipeline.steps.causal_validation_step import (
    CausalValidationInput,
    CausalValidationStep,
)


def _first_chapter_causal_input() -> CausalValidationInput:
    return CausalValidationInput(
        chapter_number=1,
        chapter_text=(
            "林远推开库房门，只看见地上的灰尘。\n\n"
            "突然黑衣人报出密室入口和钥匙编号。"
        ),
        chapter_bridge=ChapterBridge(from_chapter=0, to_chapter=1),
        causal_link={},
        previous_chapter_ending="",
    )


def test_first_chapter_causal_context_skips_previous_handoff_not_internal_checks() -> None:
    payload = _first_chapter_causal_input()

    context = CausalValidationStep._build_llm_context(payload)
    local_issues = CausalValidationStep._build_local_issues(payload)

    assert context["chapter_number"] == 1
    assert context["has_previous_chapter"] is False
    assert context["is_first_chapter"] is True
    assert context["skip_previous_chapter_handoff"] is True
    assert not any(issue.issue_type == "opening_causal_gap" for issue in local_issues)
    assert any(issue.issue_type == "event_without_cause" for issue in local_issues)


@pytest.mark.asyncio
async def test_first_chapter_causal_validation_runs_llm_instead_of_auto_full_score(
    monkeypatch: pytest.MonkeyPatch,
    router,
    builder,
) -> None:
    payload = _first_chapter_causal_input()
    captured_context: dict[str, object] = {}

    async def _fake_call_with_retry(self, task_type, context, **kwargs):
        captured_context.update(context)
        return {
            "causal_score": 5.9,
            "summary": "首章章内因果突兀。",
            "causal_link_verified": False,
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "第2段",
                    "summary": "黑衣人突然掌握密室入口，前文没有信息来源。",
                    "evidence": "突然黑衣人报出密室入口和钥匙编号。",
                    "fix_suggestion": "补充黑衣人得到入口信息的来源。",
                }
            ],
        }

    monkeypatch.setattr(CausalValidationStep, "_call_with_retry", _fake_call_with_retry)
    step = CausalValidationStep(router, builder, settings=Settings(_env_file=None))

    report = await step.run(payload)

    assert captured_context["has_previous_chapter"] is False
    assert report.causal_score == pytest.approx(5.9)
    assert any(issue.issue_type == "event_without_cause" for issue in report.issues)


def test_causal_validation_enriches_paragraph_contract_from_location() -> None:
    text = (
        "第一段承接上章。\n\n"
        "第二段里黑衣人突然知道密室入口，却没有任何消息来源。\n\n"
        "第三段继续追击。"
    )
    report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "第2段（黑衣人得知入口处）",
                    "summary": "黑衣人得知密室入口缺少信息传递链。",
                    "evidence": "第二段里黑衣人突然知道密室入口，却没有任何消息来源。",
                    "fix_suggestion": "补充发出者、传递渠道和接收场景。",
                }
            ]
        },
        local_issues=[],
    )

    issue = CausalValidationStep._enrich_issue_locations(report.issues, text)[0]

    assert issue.paragraph_start == 2
    assert issue.paragraph_end == 2
    assert issue.location_confidence >= 0.85
    assert issue.anchor_type == "location_parsed"
    assert issue.fix_mode == "window"


def test_causal_validation_does_not_anchor_ambiguous_repeated_evidence() -> None:
    text = (
        "第一段里铜铃响了三下，众人都停住脚步。\n\n"
        "第二段里铜铃响了三下，暗门却在另一侧打开。\n\n"
        "第三段里角色开始追问消息来源。"
    )
    report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "未指定",
                    "summary": "铜铃触发暗门缺少因果铺垫。",
                    "evidence": "铜铃响了三下",
                    "fix_suggestion": "补充铜铃与暗门机关的因果关系。",
                }
            ]
        },
        local_issues=[],
    )

    issue = CausalValidationStep._enrich_issue_locations(report.issues, text)[0]

    assert issue.paragraph_start == 0
    assert issue.paragraph_end == 0
    assert issue.anchor_type == "anchor_degraded"
    assert issue.location_confidence == 0.0
    assert issue.fix_mode == "window"


def test_causal_validation_marks_keyword_fallback_as_low_confidence() -> None:
    text = (
        "第一段承接上章。\n\n"
        "第二段提到暗门忽然打开，但没人解释机关从何触发。\n\n"
        "第三段继续追击。"
    )
    report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "暗门，机关",
                    "summary": "暗门开启缺少触发原因。",
                    "evidence": "不存在于正文的证据片段",
                    "fix_suggestion": "补充触发机关的动作。",
                }
            ]
        },
        local_issues=[],
    )

    issue = CausalValidationStep._enrich_issue_locations(report.issues, text)[0]

    assert issue.paragraph_start == 2
    assert issue.paragraph_end == 2
    assert issue.anchor_type == "location_keyword"
    assert issue.location_confidence == 0.5
    assert issue.fix_mode == "window"


def test_causal_validation_ignores_non_causal_issue_types() -> None:
    report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_type": "address_form_mismatch",
                    "severity": "high",
                    "location": "第4段（称呼处）",
                    "location_confidence": 0.96,
                    "anchor_type": "explicit_para",
                    "paragraph_start": 4,
                    "paragraph_end": 4,
                    "summary": "非皇族角色被称为殿下。",
                    "evidence": "顾深低声唤她殿下。",
                    "evidence_quote": "低声唤她殿下",
                    "fix_suggestion": "改为姑娘。",
                    "fix_mode": "replace",
                }
            ]
        },
        local_issues=[],
    )

    assert report.issues == []
    assert report.causal_score == 10.0


def test_causal_validation_llm_context_is_scoped() -> None:
    payload = CausalValidationInput(
        chapter_number=3,
        chapter_text="第一段。\n\n第二段。",
        chapter_bridge=ChapterBridge(
            from_chapter=2,
            to_chapter=3,
            bridge_summary="上章结尾停在门口",
            emotional_carryover="紧张",
            action_handoff="推门",
            opening_location="档案室",
            opening_time="深夜",
            pending_questions=["不应直接进入因果提示"],
        ),
        causal_link={
            "previous_event": "发现密信",
            "causal_mechanism": "密信指向档案室",
            "unresolved_question": "谁寄出密信",
            "open_threads": ["密信来源"],
            "extra_global_context": "不应进入因果提示",
        },
        previous_chapter_ending="上一章结尾" * 100,
        prior_issues=[
            {
                "issue_id": "causal-opening-stable",
                "issue_type": "opening_causal_gap",
                "severity": "high",
                "summary": "开头断裂",
                "location": "第1段",
                "evidence": "不应转发的长证据",
            }
        ],
        must_resolve_issue_ids=["causal-opening-stable"],
        character_notes="角色能力参考",
    )

    context = CausalValidationStep._build_llm_context(
        payload,
        independent_reviewer_note="复审说明",
    )

    assert set(context["chapter_bridge"]) == {
        "bridge_summary",
        "emotional_carryover",
        "action_handoff",
        "opening_location",
        "opening_time",
    }
    assert "pending_questions" not in context["chapter_bridge"]
    assert "extra_global_context" not in context["causal_link"]
    assert context["prior_issues"][0]["issue_id"] == "causal-opening-stable"
    assert context["prior_issues"][0]["evidence"] == "不应转发的长证据"
    assert context["prior_issues"][0]["repair_focus"]
    assert context["prior_issues"][0]["postconditions"]
    assert context["must_resolve_issue_ids"] == ["causal-opening-stable"]
    assert context["previous_chapter_ending"].startswith("上一章结尾")
    assert len(context["previous_chapter_ending"]) <= 800


def test_causal_validation_preserves_or_generates_issue_id() -> None:
    explicit_report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_id": "causal-explicit",
                    "issue_type": "opening_causal_gap",
                    "severity": "high",
                    "location": "第1段",
                    "summary": "开头缺少上一章因果承接。",
                    "evidence": "第一段完全跳到新场景，没有承接上章事件余波。",
                }
            ]
        },
        local_issues=[],
    )
    generated_report = CausalValidationStep._normalize_llm_response(
        {
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "critical",
                    "location": "第2段",
                    "summary": "黑衣人突然掌握密室入口。",
                    "evidence": "第二段里黑衣人突然知道密室入口，却没有任何消息来源。",
                }
            ]
        },
        local_issues=[],
    )

    assert explicit_report.issues[0].issue_id == "causal-explicit"
    assert generated_report.issues[0].issue_id.startswith("causal-")


def test_causal_local_heuristic_issue_ids_are_stable() -> None:
    payload = CausalValidationInput(
        chapter_number=3,
        chapter_text=(
            "月光照在空院，檐下只有积水和旧木箱。\n\n"
            "突然铜铃自己响起，暗门随之打开。"
        ),
        chapter_bridge=ChapterBridge(from_chapter=2, to_chapter=3),
        causal_link={"previous_event": "林远在旧库房发现血字密信"},
        previous_chapter_ending="林远握着血字密信，意识到密信指向旧库房深处。",
    )

    first = CausalValidationStep._build_local_issues(payload)
    second = CausalValidationStep._build_local_issues(payload)

    assert {issue.issue_type for issue in first} >= {
        "opening_causal_gap",
        "event_without_cause",
    }
    assert [(issue.issue_type, issue.issue_id) for issue in first] == [
        (issue.issue_type, issue.issue_id) for issue in second
    ]
    assert all(issue.issue_id.startswith("ch003-causal-") for issue in first)


def test_causal_validation_keeps_description_only_issue() -> None:
    report = CausalValidationStep._normalize_llm_response(
        {
            "causal_score": 4.2,
            "summary": "发现因果链问题。",
            "issues": [
                {
                    "issue_type": "event_without_cause",
                    "severity": "high",
                    "location": "第3段",
                    "description": "角色突然知道关键暗号，但前文没有信息来源。",
                    "evidence": "他径直报出暗号。",
                    "fix_suggestion": "补充暗号来源或传递场景。",
                }
            ],
            "causal_link_verified": False,
        },
        local_issues=[],
    )

    assert len(report.issues) == 1
    assert report.issues[0].summary == "角色突然知道关键暗号，但前文没有信息来源。"
    assert report.causal_score == 4.2


async def test_apply_recheck_focus_strict_targeted() -> None:
    """strict_targeted: only target-related issues are kept."""
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport

    issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="high",
            location="第1段",
            summary="章节开头与上章结尾不连贯",
            paragraph_start=1, paragraph_end=2,
        ),
        CausalIssue(
            issue_type="event_without_cause",
            severity="medium",
            location="第5段",
            summary="事件缺乏因果铺垫",
            paragraph_start=5, paragraph_end=5,
        ),
        CausalIssue(
            issue_type="missing_causal_transition",
            severity="low",
            location="第10段",
            summary="转折缺少铺垫",
            paragraph_start=10, paragraph_end=10,
        ),
    ]
    report = CausalValidationReport(causal_score=7.0, summary="Test", issues=issues, causal_link_verified=True)

    # strict_targeted: only keep target-related issues
    filtered = CausalValidationStep._apply_recheck_focus(
        report,
        must_resolve_summaries=["章节开头与上章结尾不连贯"],
        prior_issues=[
            {"issue_type": "opening_causal_gap", "severity": "high",
             "location": "第1段", "summary": "章节开头与上章结尾不连贯"},
        ],
        recheck_strategy="strict_targeted",
    )
    assert len(filtered.issues) == 1, f"Expected 1 issue, got {len(filtered.issues)}"
    assert filtered.issues[0].issue_type == "opening_causal_gap"


async def test_apply_recheck_focus_targeted_with_global_guard() -> None:
    """targeted_with_global_guard: target issues + new high/critical regressions kept."""
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport

    issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="high",
            location="第1段",
            summary="章节开头与上章结尾不连贯",
            paragraph_start=1, paragraph_end=2,
        ),
        CausalIssue(
            issue_type="event_without_cause",
            severity="high",
            location="第5段",
            summary="修复引入的新严重问题",
            paragraph_start=5, paragraph_end=5,
        ),
        CausalIssue(
            issue_type="missing_causal_transition",
            severity="low",
            location="第10段",
            summary="低优先级问题应被过滤",
            paragraph_start=10, paragraph_end=10,
        ),
    ]
    report = CausalValidationReport(causal_score=6.0, summary="Test", issues=issues, causal_link_verified=False)

    filtered = CausalValidationStep._apply_recheck_focus(
        report,
        must_resolve_summaries=["章节开头与上章结尾不连贯"],
        prior_issues=[
            {"issue_type": "opening_causal_gap", "severity": "high",
             "location": "第1段", "summary": "章节开头与上章结尾不连贯"},
        ],
        recheck_strategy="targeted_with_global_guard",
    )
    # Should keep: target (opening_causal_gap) + new high (event_without_cause)
    # Should filter: low (missing_causal_transition)
    assert len(filtered.issues) == 2, f"Expected 2 issues, got {len(filtered.issues)}: {[i.issue_type for i in filtered.issues]}"
    kept_types = {i.issue_type for i in filtered.issues}
    assert "opening_causal_gap" in kept_types
    assert "event_without_cause" in kept_types
    assert "missing_causal_transition" not in kept_types


async def test_apply_recheck_focus_matches_issue_id_before_summary() -> None:
    """strict_targeted should keep the ID target and drop same-summary non-targets."""
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport

    issues = [
        CausalIssue(
            issue_id="target-causal-issue",
            issue_type="opening_causal_gap",
            severity="medium",
            location="第1段",
            summary="开头断裂",
            paragraph_start=1,
            paragraph_end=1,
        ),
        CausalIssue(
            issue_id="different-causal-issue",
            issue_type="opening_causal_gap",
            severity="medium",
            location="第8段",
            summary="开头断裂",
            paragraph_start=8,
            paragraph_end=8,
        ),
    ]
    report = CausalValidationReport(
        causal_score=7.0,
        summary="Test",
        issues=issues,
        causal_link_verified=False,
    )

    filtered = CausalValidationStep._apply_recheck_focus(
        report,
        must_resolve_summaries=["开头断裂"],
        prior_issues=[],
        must_resolve_issue_ids=["target-causal-issue"],
        recheck_strategy="strict_targeted",
    )

    assert [issue.issue_id for issue in filtered.issues] == ["target-causal-issue"]


async def test_apply_recheck_focus_keeps_id_drift_when_type_location_matches() -> None:
    """If a model regenerates the ID, type/location anchors still preserve the target."""
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport

    issue = CausalIssue(
        issue_id="regenerated-id",
        issue_type="opening_causal_gap",
        severity="high",
        location="第1段",
        summary="开头断裂仍存在",
        paragraph_start=1,
        paragraph_end=1,
    )
    report = CausalValidationReport(
        causal_score=6.0,
        summary="Test",
        issues=[issue],
        causal_link_verified=False,
    )

    filtered = CausalValidationStep._apply_recheck_focus(
        report,
        must_resolve_summaries=["开头断裂"],
        prior_issues=[
            {
                "issue_id": "target-causal-issue",
                "issue_type": "opening_causal_gap",
                "severity": "high",
                "location": "第1段",
                "summary": "开头断裂",
            }
        ],
        must_resolve_issue_ids=["target-causal-issue"],
        recheck_strategy="strict_targeted",
    )

    assert filtered.issues == [issue]


async def test_local_issues_not_merged_when_llm_clears() -> None:
    """Local issues should NOT be merged when LLM returns issues=[] (phantom issue prevention)."""
    from novel_forge.core.schemas.chapter import CausalIssue

    # Simulate LLM returning empty issues (clean bill of health)
    llm_payload = {
        "causal_score": 9.5,
        "summary": "因果链完整，无明显断裂。",
        "issues": [],
        "causal_link_verified": True,
    }

    # Simulate local heuristic firing a phantom opening_causal_gap
    local_issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="medium",
            location="第1段（章节开头）",
            paragraph_start=1,
            paragraph_end=2,
            summary="章节开头未见上章末尾关键事件的延续痕迹",
            evidence="上章关键词均未出现在开头两段，语义相似度不足",
        )
    ]

    report = CausalValidationStep._normalize_llm_response(llm_payload, local_issues)

    # LLM available and returned no issues → local issues should be discarded
    assert len(report.issues) == 0, (
        f"Expected 0 issues (LLM cleared), got {len(report.issues)}: "
        f"{[i.issue_type for i in report.issues]}"
    )
    assert report.causal_score == 9.5, "Should use LLM score when available"


async def test_low_score_empty_issue_response_falls_back_to_local_issues() -> None:
    """Low score plus issues=[] is not a clean bill of health."""
    from novel_forge.core.schemas.chapter import CausalIssue

    llm_payload = {
        "causal_score": 0.9,
        "summary": "因果链完整，无明显断裂。",
        "issues": [],
        "causal_link_verified": True,
    }
    local_issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="high",
            location="第1段（章节开头）",
            paragraph_start=1,
            paragraph_end=2,
            summary="章节开头未见上章关键事件的延续痕迹",
            evidence="上章关键词均未出现在开头两段",
        )
    ]

    report = CausalValidationStep._normalize_llm_response(llm_payload, local_issues)

    assert report.issues == local_issues
    assert report.causal_score == 0.9
    assert report.causal_link_verified is False
    assert report.validation_status == "ok"
    assert "低分但未给出可定位问题" in report.summary


async def test_low_score_empty_issue_response_without_local_issues_is_unavailable() -> None:
    llm_payload = {
        "causal_score": 0.9,
        "summary": "因果链完整，无明显断裂。",
        "issues": [],
        "causal_link_verified": True,
    }

    report = CausalValidationStep._normalize_llm_response(llm_payload, local_issues=[])

    assert report.issues == []
    assert report.causal_score == 0.9
    assert report.causal_link_verified is False
    assert report.validation_status == "unavailable"
    assert "结果不一致" in report.summary


async def test_local_issues_fallback_when_llm_unavailable() -> None:
    """Local issues should be used as fallback when LLM validation is unavailable."""
    from novel_forge.core.schemas.chapter import CausalIssue

    # Simulate LLM unavailable (empty payload)
    llm_payload = {}

    local_issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="medium",
            location="第1段（章节开头）",
            paragraph_start=1,
            paragraph_end=2,
            summary="章节开头未见上章末尾关键事件的延续痕迹",
            evidence="上章关键词均未出现在开头两段",
        )
    ]

    report = CausalValidationStep._normalize_llm_response(llm_payload, local_issues)

    # LLM unavailable → local issues should be included as fallback
    assert len(report.issues) == 1, f"Expected 1 local issue fallback, got {len(report.issues)}"
    assert report.issues[0].issue_type == "opening_causal_gap"


async def test_local_and_llm_merge_when_shared_type() -> None:
    """Local issues of same type as LLM should be merged without duplication."""
    from novel_forge.core.schemas.chapter import CausalIssue

    # LLM reports opening_causal_gap at paragraph 1
    llm_payload = {
        "causal_score": 7.0,
        "summary": "发现因果问题",
        "issues": [
            {
                "issue_type": "opening_causal_gap",
                "severity": "high",
                "location": "第1段",
                "summary": "章节开头与上章结尾无连接",
                "paragraph_start": 1,
                "paragraph_end": 2,
                "evidence": "上章提及的战斗场面",
                "evidence_quote": "战斗场面",
            }
        ],
        "causal_link_verified": False,
    }

    # Local also detects opening_causal_gap at the same location with same evidence_quote
    local_issues = [
        CausalIssue(
            issue_type="opening_causal_gap",
            severity="medium",
            location="第1段（章节开头）",
            paragraph_start=1,
            paragraph_end=2,
            summary="章节开头未见上章末尾关键事件的延续痕迹",
            evidence="上章关键词均未出现在开头两段",
            evidence_quote="战斗场面",
        )
    ]

    report = CausalValidationStep._normalize_llm_response(llm_payload, local_issues)

    # Should have exactly 1 issue (LLM takes priority, local of same type+location+quote deduped)
    assert len(report.issues) == 1, (
        f"Expected 1 issue (LLM + same-type local deduped), got {len(report.issues)}"
    )
    assert report.issues[0].issue_type == "opening_causal_gap"
