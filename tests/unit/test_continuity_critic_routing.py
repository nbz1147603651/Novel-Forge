from __future__ import annotations

from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.pipeline.long.stages.continuity_repair import _convert_critique_to_continuity


def test_critic_character_issue_without_boundary_markers_does_not_enter_continuity() -> None:
    critique = CritiqueReport(
        chapter_number=4,
        overall_score=7.0,
        issues=[
            CritiqueIssue(
                issue_type="character_inconsistency",
                severity="critical",
                summary="正文直接揭示身份，属于章内表达和人物呈现问题。",
                evidence="照片里的人影与她几乎重合。",
                affected_chapters=[4],
                suggested_fix="改为更含蓄的章内表达。",
                confidence=0.92,
            )
        ],
    )

    report = _convert_critique_to_continuity(critique, chapter_number=4)

    assert report.issues == []
    assert report.continuity_score == 10.0
    assert "非连续性问题" in report.summary


def test_critic_boundary_character_issue_can_enter_continuity() -> None:
    critique = CritiqueReport(
        chapter_number=4,
        overall_score=7.0,
        issues=[
            CritiqueIssue(
                issue_type="character_inconsistency",
                severity="high",
                summary="上一章结尾建立的受伤状态没有在本章开场承接。",
                evidence="本章开场直接让角色奔跑。",
                affected_chapters=[3, 4],
                suggested_fix="补上开场状态承接。",
                confidence=0.85,
            )
        ],
    )

    report = _convert_critique_to_continuity(critique, chapter_number=4)

    assert len(report.issues) == 1
    assert report.issues[0].issue_type == "character_inconsistency"
    assert report.continuity_score < 10.0


def test_critic_conversion_coerces_structured_evidence_and_priority_labels() -> None:
    critique = CritiqueReport(
        chapter_number=13,
        overall_score=7.0,
        issues=[
            CritiqueIssue(
                issue_type="continuity_error",
                severity="P1",  # type: ignore[arg-type]
                summary="",
                evidence={
                    "prev_chapter_end": "暮色完全沉下来",
                    "current_chapter_start": "斜落的日光",
                },
                affected_chapters=[12, 13],
                suggested_fix={"strategy": "补一句次日傍晚的过渡"},
                confidence=0.85,
            )
        ],
    )

    report = _convert_critique_to_continuity(critique, chapter_number=13)

    issue = report.issues[0]
    assert issue.severity == "high"
    assert "prev_chapter_end: 暮色完全沉下来" in issue.evidence
    assert "current_chapter_start: 斜落的日光" in issue.evidence
    assert issue.fix_actions == ["strategy: 补一句次日傍晚的过渡"]


def test_continuity_report_accepts_historical_structured_evidence_payload() -> None:
    report = ContinuityReport.model_validate(
        {
            "continuity_score": 7.0,
            "summary": "历史 checkpoint",
            "issues": [
                {
                    "issue_type": "time_discontinuity",
                    "severity": "P2",
                    "summary": "时间承接不清",
                    "evidence": {
                        "prev_chapter_end": "暮色完全沉下来",
                        "current_chapter_start": "斜落的日光",
                    },
                }
            ],
        }
    )

    issue = report.issues[0]
    assert issue.severity == "medium"
    assert issue.evidence == "prev_chapter_end: 暮色完全沉下来；current_chapter_start: 斜落的日光"
    assert issue.evidence_pairs == [
        {"source": "prev_chapter_end", "evidence": "暮色完全沉下来"},
        {"source": "current_chapter_start", "evidence": "斜落的日光"},
    ]
