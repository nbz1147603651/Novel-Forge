"""合规审核阶段（COMPLIANCE）：红线 / 高风险 / 正向价值观三级检查。

Deterministic pattern scans run first (cheap, reproducible, offline-safe); the
optional ``COMPLIANCE_CHECK`` LLM task only adjudicates the semantic
positive-value checks (and may surface extra high-risk findings).  Red-line
findings block delivery outright, high-risk findings require revision, and the
final report is persisted and folded into the ``DeliveryManifest``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.registry import PromptRegistry

from .schemas import (
    ComplianceAction,
    ComplianceCheckItem,
    ComplianceFinding,
    ComplianceReport,
    ComplianceSeverity,
    FilmStudioState,
)

StepCallback = Callable[[str, dict[str, Any]], None]

#: Project-relative location of the persisted compliance report.
COMPLIANCE_REPORT_RELATIVE_PATH = "film/compliance/report.json"


def _noop_step(_step: str, _payload: dict[str, Any]) -> None:
    return None


def drama_compliance_checklist() -> tuple[ComplianceCheckItem, ...]:
    """Structured short-drama compliance checklist (红线/高风险/正向价值观)."""
    return (
        ComplianceCheckItem(
            check_id="RL_POLITICAL",
            severity=ComplianceSeverity.RED_LINE,
            category="政治安全",
            title="政治敏感与损害国家尊严内容",
            description="剧情、台词或字幕不得涉及政治敏感议题、损害国家与民族尊严。",
            pattern=r"颠覆国家|分裂国家|损害国家尊严|侮辱(国旗|国歌|英烈)",
            remediation="删除相关情节与台词，必要时重写该场次。",
        ),
        ComplianceCheckItem(
            check_id="RL_VIOLENCE_GLORY",
            severity=ComplianceSeverity.RED_LINE,
            category="暴力血腥",
            title="美化暴力、血腥渲染与犯罪教学",
            description="不得美化暴力犯罪、渲染血腥细节或传授犯罪方法。",
            pattern=r"美化(暴力|犯罪)|宣扬以暴制暴|传授犯罪方法|血腥(特写|细节)",
            remediation="弱化暴力呈现，将犯罪结局改为受法律制裁。",
        ),
        ComplianceCheckItem(
            check_id="RL_MINOR",
            severity=ComplianceSeverity.RED_LINE,
            category="未成年人保护",
            title="未成年人不当情节",
            description="不得出现未成年人恋爱、犯罪诱导或校园霸凌美化。",
            pattern=r"未成年.{0,8}(恋爱|犯罪|结婚|辍学混社会)|美化(校园)?霸凌",
            remediation="将涉事角色改为成年人或删除相关情节。",
        ),
        ComplianceCheckItem(
            check_id="RL_SUPERSTITION",
            severity=ComplianceSeverity.RED_LINE,
            category="封建迷信",
            title="宣扬封建迷信且无科学收束",
            description="超自然设定需有合理化收束，不得宣扬巫术改命、占卜决定论。",
            pattern=r"宣扬(封建迷信|巫术)|算命改命|占卜决定(人生|命运)",
            remediation="为超自然情节补充科学化或心理化解释收束。",
        ),
        ComplianceCheckItem(
            check_id="RL_GAMBLE_DRUG",
            severity=ComplianceSeverity.RED_LINE,
            category="黄赌毒",
            title="涉毒涉赌情节",
            description="不得出现吸毒、贩毒正面描写或赌博技巧教学。",
            pattern=r"吸毒|贩毒|赌博(技巧|玩法教学)|制毒",
            remediation="删除涉毒涉赌情节，涉赌人物改为负面结局。",
        ),
        ComplianceCheckItem(
            check_id="HR_CLICKBAIT",
            severity=ComplianceSeverity.HIGH_RISK,
            category="低俗引流",
            title="低俗擦边与软色情引流",
            description="标题、卡点与开场不得使用擦边元素引流。",
            pattern=r"湿身|透视装|擦边|床戏特写",
            remediation="替换擦边表达，用冲突悬念替代感官刺激。",
        ),
        ComplianceCheckItem(
            check_id="HR_MONEY_WORSHIP",
            severity=ComplianceSeverity.HIGH_RISK,
            category="价值导向",
            title="拜金炫富与金钱万能论",
            description="不得将金钱塑造为解决一切问题的唯一手段。",
            pattern=r"拜金|炫富|金钱万能|有钱就能(摆平|买到一切)",
            remediation="补充能力与情感维度的解决路径，弱化金钱决定论。",
        ),
        ComplianceCheckItem(
            check_id="HR_MEDICAL",
            severity=ComplianceSeverity.HIGH_RISK,
            category="医疗健康",
            title="夸大医疗功效与神医神药",
            description="不得出现包治百病、祖传秘方治愈重疾等误导表述。",
            pattern=r"包治百病|祖传秘方(治|根除)|神医(一针|一帖)",
            remediation="将疗效表述改为正规医疗路径或模糊化处理。",
        ),
        ComplianceCheckItem(
            check_id="HR_DISCRIMINATION",
            severity=ComplianceSeverity.HIGH_RISK,
            category="群体尊重",
            title="地域、性别与职业歧视",
            description="台词与旁白不得包含对特定群体的贬损性概括。",
            pattern=r"地域黑|(男人|女人)都(不是好东西|一个德性)|职业歧视",
            remediation="删除歧视性台词，改为针对具体角色的个性化表达。",
        ),
        ComplianceCheckItem(
            check_id="PV_GROWTH",
            severity=ComplianceSeverity.POSITIVE_VALUE,
            category="正向价值观",
            title="主角成长弧光",
            description="主角应通过自身努力与选择完成成长，而非仅靠外力或捷径。",
            remediation="在关键节点补充主角主动选择与代价。",
        ),
        ComplianceCheckItem(
            check_id="PV_JUSTICE",
            severity=ComplianceSeverity.POSITIVE_VALUE,
            category="正向价值观",
            title="善恶有报的结局导向",
            description="主要反派应受到法律或道义制裁，不得让恶行无代价收场。",
            remediation="在结局补充反派的代价与主角的正向落点。",
        ),
        ComplianceCheckItem(
            check_id="PV_FAMILY",
            severity=ComplianceSeverity.POSITIVE_VALUE,
            category="正向价值观",
            title="亲情与责任的正向呈现",
            description="家庭冲突最终应指向理解或和解，不得一味渲染亲情撕裂。",
            remediation="在家庭线收束处补充和解或相互理解的细节。",
        ),
    )


_ACTION_BY_SEVERITY = {
    ComplianceSeverity.RED_LINE: ComplianceAction.BLOCK,
    ComplianceSeverity.HIGH_RISK: ComplianceAction.REVISE,
    ComplianceSeverity.POSITIVE_VALUE: ComplianceAction.REVIEW,
}


class ComplianceAuditor:
    """Runs the structured checklist over one film project."""

    def __init__(
        self,
        layout: ProjectLayout,
        project_id: str,
        *,
        router: Any = None,
        on_step: StepCallback = _noop_step,
    ) -> None:
        self.layout = layout
        self.project_id = project_id
        self.router = router
        self.on_step = on_step

    @property
    def report_path(self) -> Any:
        return self.layout.root / COMPLIANCE_REPORT_RELATIVE_PATH

    # ------------------------------------------------------------ collection

    @staticmethod
    def collect_texts(state: FilmStudioState) -> list[tuple[str, str]]:
        """Collect (location_label, text) pairs from the screenplay."""
        texts: list[tuple[str, str]] = []
        if state.screenplay.synopsis:
            texts.append(("剧本梗概", state.screenplay.synopsis))
        for scene in state.screenplay.scenes:
            heading = scene.heading or scene.scene_id
            for line in scene.lines:
                texts.append((f"{heading}·{line.kind}", line.text))
        return texts

    # ------------------------------------------------------------- scanning

    def deterministic_scan(
        self, texts: list[tuple[str, str]]
    ) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        for item in drama_compliance_checklist():
            if not item.pattern:
                continue
            regex = re.compile(item.pattern)
            for location, text in texts:
                match = regex.search(text)
                if match is None:
                    continue
                findings.append(
                    ComplianceFinding(
                        check_id=item.check_id,
                        severity=item.severity,
                        title=item.title,
                        detail=f"命中「{match.group(0)}」；整改建议：{item.remediation}",
                        location=location,
                        action=_ACTION_BY_SEVERITY[item.severity],
                    )
                )
        return findings

    # -------------------------------------------------------------- audit

    async def audit(self, state: FilmStudioState, *, use_ai: bool = True) -> ComplianceReport:
        texts = self.collect_texts(state)
        findings = self.deterministic_scan(texts)
        checklist = drama_compliance_checklist()
        if self.router is not None and use_ai:
            findings.extend(await self._semantic_check(texts, checklist))
        blocked = any(
            item.severity == ComplianceSeverity.RED_LINE for item in findings
        )
        open_high_risk = any(
            item.severity == ComplianceSeverity.HIGH_RISK for item in findings
        )
        advisory = [
            item.check_id
            for item in findings
            if item.severity == ComplianceSeverity.POSITIVE_VALUE
        ]
        summary_bits = [
            f"红线 {sum(1 for item in findings if item.severity == ComplianceSeverity.RED_LINE)} 项",
            f"高风险 {sum(1 for item in findings if item.severity == ComplianceSeverity.HIGH_RISK)} 项",
            f"正向价值观建议 {len(advisory)} 项",
        ]
        report = ComplianceReport(
            target_id=state.project_id,
            passed=not blocked and not open_high_risk,
            blocked=blocked,
            findings=findings,
            summary="；".join(summary_bits),
        )
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.report_path, report.model_dump(mode="json"))
        self.on_step(
            "film_compliance_audited",
            {"passed": report.passed, "blocked": report.blocked, "findings": len(findings)},
        )
        return report

    async def _semantic_check(
        self,
        texts: list[tuple[str, str]],
        checklist: tuple[ComplianceCheckItem, ...],
    ) -> list[ComplianceFinding]:
        try:
            prompt = PromptRegistry().render(
                TaskType.COMPLIANCE_CHECK,
                checklist=[item.model_dump(mode="json") for item in checklist],
                texts=[{"location": location, "text": text} for location, text in texts],
            )
        except Exception:
            self.on_step("film_compliance_semantic_skip", {"reason": "prompt_unavailable"})
            return []
        request = ModelRequest(
            task_type=TaskType.COMPLIANCE_CHECK,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.2,
            response_schema_name="compliance_check",
        )
        try:
            response = await self.router.route(request)
            payload = _json_object(response.content)
        except (RuntimeError, ValueError, TypeError):
            self.on_step("film_compliance_semantic_skip", {"reason": "unavailable"})
            return []
        return _parse_semantic_findings(payload, checklist)


def _json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("compliance response is not a JSON object")
    return parsed


def _parse_semantic_findings(
    payload: dict[str, Any], checklist: tuple[ComplianceCheckItem, ...]
) -> list[ComplianceFinding]:
    by_id = {item.check_id: item for item in checklist}
    findings: list[ComplianceFinding] = []
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return findings
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        check_id = str(raw.get("check_id") or "")
        item = by_id.get(check_id)
        if item is None:
            continue
        if item.severity == ComplianceSeverity.RED_LINE:
            # Red lines stay deterministic-only; the LLM cannot override them.
            continue
        verdict = str(raw.get("verdict") or raw.get("action") or "")
        if verdict in {"pass", "none", ""}:
            continue
        findings.append(
            ComplianceFinding(
                check_id=check_id,
                severity=item.severity,
                title=item.title,
                detail=str(raw.get("detail") or item.remediation),
                location=str(raw.get("location") or ""),
                action=_ACTION_BY_SEVERITY[item.severity],
            )
        )
    return findings


def validate_semantic_output(payload: dict[str, Any]) -> bool:
    """Accept/reject guard for COMPLIANCE_CHECK output (mirrors spoken-text rewrite)."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return False
    for raw in findings:
        if not isinstance(raw, dict) or not str(raw.get("check_id") or ""):
            return False
    return True
