"""Unit tests for the COMPLIANCE stage (Batch 5)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from novel_forge.film.compliance import (
    COMPLIANCE_REPORT_RELATIVE_PATH,
    ComplianceAuditor,
    drama_compliance_checklist,
    validate_semantic_output,
)
from novel_forge.film.schemas import (
    ComplianceAction,
    ComplianceSeverity,
    FilmStage,
    FilmStudioState,
    FilmTimeline,
    ProductionBible,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
)
from novel_forge.persistence.models import ProjectLayout


def _layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    return layout


def _state(*lines: str) -> FilmStudioState:
    scene = ScreenplayScene(
        scene_id="sc-01",
        sequence_number=1,
        heading="内景·公寓·夜",
        lines=[ScreenplayLine(kind="dialogue", speaker="主角", text=text) for text in lines],
    )
    return FilmStudioState(
        project_id="demo",
        project_title="演示项目",
        production_bible=ProductionBible(project_id="demo", title="演示项目"),
        screenplay=Screenplay(title="演示项目", scenes=[scene]),
        timeline=FilmTimeline(name="master"),
    )


def test_checklist_covers_three_tiers_with_compilable_patterns() -> None:
    checklist = drama_compliance_checklist()
    severities = {item.severity for item in checklist}
    assert severities == {
        ComplianceSeverity.RED_LINE,
        ComplianceSeverity.HIGH_RISK,
        ComplianceSeverity.POSITIVE_VALUE,
    }
    assert len(checklist) >= 10
    for item in checklist:
        if item.pattern:
            re.compile(item.pattern)
        if item.severity != ComplianceSeverity.POSITIVE_VALUE:
            assert item.pattern, f"{item.check_id} needs a deterministic pattern"


def test_stage_order_places_compliance_before_delivery() -> None:
    assert "EDIT" in FilmStage.__members__
    order = list(FilmStage)
    assert order.index(FilmStage.COMPLIANCE) == order.index(FilmStage.DELIVERY) - 1


def test_deterministic_scan_blocks_on_red_line(tmp_path: Path) -> None:
    auditor = ComplianceAuditor(_layout(tmp_path), "demo")
    state = _state("这段剧情要传授犯罪方法才能推进")
    findings = auditor.deterministic_scan(auditor.collect_texts(state))
    assert any(
        item.check_id == "RL_VIOLENCE_GLORY" and item.action == ComplianceAction.BLOCK
        for item in findings
    )


async def test_audit_clean_text_passes_and_persists_report(tmp_path: Path) -> None:
    auditor = ComplianceAuditor(_layout(tmp_path), "demo")
    state = _state("主角整理好文件，决定明天重新出发。")
    report = await auditor.audit(state, use_ai=False)
    assert report.passed is True
    assert report.blocked is False
    assert report.findings == []
    persisted = tmp_path / "project" / COMPLIANCE_REPORT_RELATIVE_PATH
    assert persisted.exists()
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    assert payload["passed"] is True


async def test_audit_red_line_blocks_and_high_risk_requires_revision(tmp_path: Path) -> None:
    auditor = ComplianceAuditor(_layout(tmp_path), "demo")
    state = _state(
        "反派靠炫富和金钱万能摆平了一切",
        "他还偷偷传授犯罪方法",
    )
    report = await auditor.audit(state, use_ai=False)
    assert report.blocked is True
    assert report.passed is False
    severities = {item.severity for item in report.findings}
    assert ComplianceSeverity.RED_LINE in severities
    assert ComplianceSeverity.HIGH_RISK in severities


class _StubRouter:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def route(self, request: Any) -> Any:
        class _Response:
            content = json.dumps(self._payload, ensure_ascii=False)

        return _Response()


async def test_semantic_findings_merge_and_red_line_override_rejected(tmp_path: Path) -> None:
    router = _StubRouter(
        {
            "findings": [
                {
                    "check_id": "PV_JUSTICE",
                    "verdict": "fail",
                    "detail": "反派未受制裁",
                    "location": "结局场次",
                },
                {
                    "check_id": "RL_POLITICAL",
                    "verdict": "fail",
                    "detail": "LLM 试图裁决红线项，必须被拒绝",
                    "location": "任意",
                },
                {"check_id": "UNKNOWN_CHECK", "verdict": "fail", "detail": "x", "location": "y"},
            ],
            "summary": "一项正向价值观建议",
        }
    )
    auditor = ComplianceAuditor(_layout(tmp_path), "demo", router=router)
    state = _state("主角望向窗外，故事结束。")
    report = await auditor.audit(state, use_ai=True)
    check_ids = [item.check_id for item in report.findings]
    assert "PV_JUSTICE" in check_ids
    assert "RL_POLITICAL" not in check_ids
    assert "UNKNOWN_CHECK" not in check_ids
    # Positive-value findings are advisory: they must not block delivery.
    assert report.blocked is False
    assert report.passed is True


def test_validate_semantic_output_guard() -> None:
    assert validate_semantic_output({"findings": [{"check_id": "PV_JUSTICE"}]}) is True
    assert validate_semantic_output({"findings": "not-a-list"}) is False
    assert validate_semantic_output({"findings": [{"detail": "missing check_id"}]}) is False
