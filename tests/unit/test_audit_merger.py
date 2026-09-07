"""Tests for normalized audit result merging."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.workspace.audit_merger import AuditResultMerger


class _FakeLayout:
    def quality_gate_report_path(self, chapter_num: int) -> Path:
        return Path(f"chapter_{chapter_num:03d}_quality_gate.json")

    def continuity_report_path(self, chapter_num: int) -> Path:
        return Path(f"chapter_{chapter_num:03d}_continuity.json")

    def chapter_causal_report_path(self, chapter_num: int) -> Path:
        return Path(f"chapter_{chapter_num:03d}_causal.json")

    def eval_report_path(self, chapter_num: int) -> Path:
        return Path(f"chapter_{chapter_num:03d}_eval.json")


class _FakeStorage:
    def __init__(self, payloads: dict[Path, dict]) -> None:
        self._payloads = payloads

    def exists(self, path: Path) -> bool:
        return path in self._payloads

    def load_json(self, path: Path) -> dict:
        return self._payloads[path]


def test_audit_merger_prefers_quality_gate_findings_and_dimension_scores() -> None:
    layout = _FakeLayout()
    storage = _FakeStorage(
        {
            layout.quality_gate_report_path(2): {
                "dimension_scores": {
                    "alignment": {"score": 8.7, "threshold": 7.0, "passed": True},
                    "chapter_quality": {"score": 6.5, "threshold": 8.0, "passed": False},
                    "continuity": {"score": 9.0, "threshold": 6.0, "passed": True},
                    "reading_power": {"score": 7.2, "threshold": 5.0, "passed": True},
                    "guard": {"score": 10.0, "threshold": 8.0, "passed": True},
                },
                "review_findings": [
                    {
                        "finding_id": "f_quality",
                        "chapter_number": 2,
                        "source_module": "check_chapter",
                        "dimension": "chapter_quality",
                        "issue_type": "prompt_leak",
                        "severity": "critical",
                        "summary": "正文混入提示词",
                        "evidence_quote": "系统：你是",
                        "repair_goal": "删除提示词元语言",
                    }
                ],
            },
            layout.eval_report_path(2): {"overall_score": 8.1},
        }
    )

    result = AuditResultMerger.merge_reports(layout, 2, storage)

    assert result["alignment_score"] == 8.7
    assert result["chapter_quality_score"] == 6.5
    assert result["reading_power_score"] == 7.2
    assert result["continuity_score"] == 9.0
    assert result["overall_score"] == 8.1
    assert result["issue_count"] == 1
    assert "chapter_quality" in result["issues_by_dimension"]
    issue = result["critique"]["issues"][0]
    assert issue["dimension"] == "chapter_quality"
    assert issue["category"] == "prompt_leak"


def test_audit_merger_replaces_stale_dimension_when_fresh_recheck_is_passed() -> None:
    layout = _FakeLayout()
    storage = _FakeStorage(
        {
            layout.quality_gate_report_path(3): {
                "dimension_scores": {
                    "continuity": {"score": 4.0, "threshold": 6.0, "passed": False},
                    "causal": {"score": 8.0, "threshold": 7.0, "passed": True},
                },
                "review_findings": [
                    {
                        "finding_id": "old_continuity",
                        "chapter_number": 3,
                        "source_module": "check_continuity",
                        "dimension": "continuity",
                        "issue_type": "state_carryover_gap",
                        "severity": "high",
                        "summary": "旧的连续性问题",
                    },
                    {
                        "finding_id": "old_causal",
                        "chapter_number": 3,
                        "source_module": "causal_validation",
                        "dimension": "causal",
                        "issue_type": "motivation_gap",
                        "severity": "high",
                        "summary": "仍存在的因果问题",
                    },
                ],
            }
        }
    )

    fresh_continuity = SimpleNamespace(continuity_score=9.1, issues=[])

    result = AuditResultMerger.merge_reports(
        layout,
        3,
        storage,
        continuity_report_new=fresh_continuity,
    )

    assert result["dimension_scores"]["continuity"]["score"] == 9.1
    assert "continuity" not in result["issues_by_dimension"]
    assert "causal" in result["issues_by_dimension"]
