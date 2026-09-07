"""Report scoring and recheck focus for continuity evaluation."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    coerce_paragraph_count,
)
from novel_forge.core.utils.string import clean_str
from novel_forge.pipeline.steps.continuity_eval.normalizer import _IssueNormalizer


class _Scorer(_IssueNormalizer):
    """Scoring and report normalization methods."""

    _SEVERITY_PENALTY = {
        "critical": 3.5,
        "high": 2.5,
        "medium": 1.0,
        "low": 0.5,
    }
    _ADVISORY_TYPES = {
        "opening_gap",
        "closing_gap",
        "closing_contract_mismatch",
        "information_consistency",
        "relationship_change_support",
        "relationship_development",
    }
    _STRUCTURAL_TYPES = {
        "bridge_contract_not_followed",
        "carry_forward_missing",
        "custody_break",
        "knowledge_contradiction",
        "location_jump",
        "pov_jump",
    }
    _SUMMARY_BLOCKING_RE = re.compile(r"(?<![a-z])(?:critical|high|blocking|blocker)(?![a-z])")
    _SUMMARY_NEGATED_BLOCKING_RE = re.compile(
        r"(?:无|没有|未发现|未见|不存在|并无|无需|已解决|已修复|不再存在|no|not|without)"
        r".{0,12}"
        r"(?:critical|high|blocking|blocker|阻断|严重|高危|高风险|未解决|混乱|断裂|倒流)",
        re.IGNORECASE,
    )
    _SUMMARY_ZH_BLOCKING_TERMS = (
        "阻断",
        "严重",
        "高危",
        "高风险",
        "未解决",
        "混乱",
        "断裂",
        "倒流",
    )
    _SUMMARY_CONTINUITY_TERMS = (
        "连贯",
        "连续",
        "时间线",
        "倒流",
        "跳切",
        "跳场",
        "承接",
        "桥接",
        "开场",
        "章末",
        "地点",
        "pov",
        "重复描写同一事件",
        "逻辑断裂",
    )

    @classmethod
    def _summary_claims_open_continuity_problem(cls, summary: str) -> bool:
        text = clean_str(summary).lower()
        if not text or cls._SUMMARY_NEGATED_BLOCKING_RE.search(text):
            return False
        claims_blocker = bool(cls._SUMMARY_BLOCKING_RE.search(text)) or any(
            term in text for term in cls._SUMMARY_ZH_BLOCKING_TERMS
        )
        has_continuity_scope = any(term in text for term in cls._SUMMARY_CONTINUITY_TERMS)
        return claims_blocker and has_continuity_scope

    @classmethod
    def _issue_from_unstructured_summary(cls, summary: str) -> dict[str, Any]:
        return {
            "issue_id": "",
            "issue_type": "continuity_gap",
            "severity": "high",
            "confidence": 0.62,
            "source": "llm",
            "repair_surface": "chapter_text",
            "status": "open",
            "blocking": True,
            "summary": summary[:240],
            "evidence": "",
            "location": "",
            "location_confidence": 0.0,
            "anchor_type": "",
            "paragraph_start": 0,
            "paragraph_end": 0,
            "evidence_quote": "",
            "fix_mode": "fulltext",
            "insert_before_para": 0,
            "insert_after_para": 0,
            "affected_characters": [],
            "rewrite_scope": "chapter",
            "fix_actions": [
                "根据报告摘要定位并修复仍未结构化列出的连续性问题；若摘要误报，重新输出不暗示高优先级问题的 summary。"
            ],
            "expected_state": {},
            "observed_state": {},
            "missing_anchors": [],
            "postconditions": [],
            "validator_id": "",
            "diagnostic_note": "LLM summary claimed a blocking continuity problem while issues was empty; synthesized by normalizer.",
        }

    @classmethod
    def _issue_penalty(cls, issue: dict[str, Any]) -> float:
        severity = clean_str(issue.get("severity")).lower()
        issue_type = clean_str(issue.get("issue_type")).lower()
        penalty = cls._SEVERITY_PENALTY.get(severity, 1.0)
        if issue_type in cls._ADVISORY_TYPES:
            penalty *= 0.6
        if issue_type == "carry_forward_missing":
            # A missing carry-forward item is now a critical structural break
            # (it can block archive). Penalize per missing item without the
            # legacy 3.0 cap, so the continuity score reflects the real
            # severity and the structural-type floor (5.5) applies.
            evidence = clean_str(issue.get("evidence"))
            missing_count = len([item for item in evidence.split("；") if item.strip()])
            penalty = 2.5 + max(0, missing_count - 1) * 0.8
        return penalty

    @classmethod
    def _normalize_report(
        cls,
        payload: Any,
        input_data: Any,
        local_issues: list[dict[str, Any]] | None = None,
        use_local_as_prescreen: bool = True,
        confidence_threshold: float = 0.7,
    ) -> dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        opening_window_paragraphs = coerce_paragraph_count(
            getattr(input_data, "boundary_opening_paragraphs", DEFAULT_OPENING_PARAGRAPHS),
            default=DEFAULT_OPENING_PARAGRAPHS,
            maximum=8,
        )
        issues = cls._normalize_issues(
            data.get("issues", []),
            input_data.chapter_text,
            opening_window_paragraphs=opening_window_paragraphs,
        )
        summary = clean_str(data.get("summary"))
        llm_available = bool(data) and (
            "issues" in data
            or data.get("continuity_score") is not None
            or bool(summary)
        )
        synthesized_summary_issue = False
        if not issues and cls._summary_claims_open_continuity_problem(summary):
            issues.append(cls._issue_from_unstructured_summary(summary))
            synthesized_summary_issue = True

        if local_issues is None:
            build_local_issues = getattr(cls, "_build_local_issues", None)
            local_issues = (
                build_local_issues(input_data)
                if use_local_as_prescreen and callable(build_local_issues)
                else []
            )

        llm_issue_types = {
            clean_str(issue.get("issue_type")).lower()
            for issue in issues
            if clean_str(issue.get("issue_type"))
        }
        for local_issue in local_issues:
            confidence = local_issue.get("confidence", 0.5)
            if confidence < confidence_threshold:
                continue
            if local_issue.get("requires_llm_judgment"):
                continue
            if not cls._is_continuity_issue(local_issue):
                continue
            local_type = clean_str(local_issue.get("issue_type")).lower()
            if llm_available and local_type not in llm_issue_types:
                continue
            issues.append(local_issue)

        issues = cls._dedupe_issues(issues)
        issues = [cls._enrich_issue_semantics(issue, input_data) for issue in issues]
        active_issues = [issue for issue in issues if clean_str(issue.get("status")) == "open"]

        llm_score = data.get("continuity_score")
        if (
            isinstance(llm_score, (int, float))
            and 0.0 <= float(llm_score) <= 10.0
            and not synthesized_summary_issue
        ):
            score = round(float(llm_score), 1)
        else:
            score = 10.0
            for issue in active_issues:
                score -= cls._issue_penalty(issue)
            if any(
                clean_str(issue.get("issue_type")).lower() in cls._STRUCTURAL_TYPES
                and clean_str(issue.get("severity")).lower() in {"high", "critical"}
                for issue in active_issues
            ):
                score = min(score, 5.5)
            score = max(0.0, min(10.0, score))
        if not summary:
            summary = (
                "未发现明显跨章衔接问题。"
                if not active_issues
                else "发现需要定向修复的跨章衔接问题。"
            )

        _ISSUE_FIELDS = frozenset(
            {
                "issue_id",
                "source",
                "repair_surface",
                "status",
                "blocking",
                "confidence",
                "issue_type",
                "severity",
                "summary",
                "evidence",
                "location",
                "location_confidence",
                "anchor_type",
                "paragraph_start",
                "paragraph_end",
                "evidence_quote",
                "fix_mode",
                "insert_before_para",
                "insert_after_para",
                "affected_characters",
                "rewrite_scope",
                "fix_actions",
                "expected_state",
                "observed_state",
                "missing_anchors",
                "postconditions",
                "repair_directive",
                "validator_id",
                "diagnostic_note",
            }
        )
        clean_issues = [{k: v for k, v in issue.items() if k in _ISSUE_FIELDS} for issue in issues]

        return {
            "continuity_score": round(score, 1),
            "summary": summary,
            "issues": clean_issues,
        }

    @classmethod
    def _apply_recheck_focus(
        cls,
        report: ContinuityReport,
        *,
        must_resolve_summaries: list[str],
        prior_issues: list[dict[str, Any]],
    ) -> ContinuityReport:
        if not report.issues:
            return report

        target_summaries = [s.strip() for s in must_resolve_summaries if s and s.strip()]
        target_types: set[str] = set()
        for item in prior_issues:
            if not isinstance(item, dict):
                continue
            itype = clean_str(item.get("issue_type")).lower()
            if itype:
                target_types.add(itype)

        if not target_summaries and not target_types:
            return report

        def _norm(text: str) -> str:
            return "".join(ch.lower() for ch in text if ch.isalnum())

        def _trigrams(text: str) -> set[str]:
            return {text[i : i + 3] for i in range(len(text) - 2)}

        def _is_target(issue: Any) -> bool:
            itype = clean_str(issue.issue_type).lower()
            summary = _norm(clean_str(issue.summary))
            if itype in target_types:
                return True
            if not summary:
                return False
            summary_grams = _trigrams(summary)
            for ts in target_summaries:
                ns = _norm(ts)
                if not ns:
                    continue
                if summary in ns or ns in summary:
                    return True
                ts_grams = _trigrams(ns)
                if summary_grams and ts_grams:
                    union = len(summary_grams | ts_grams)
                    if union and len(summary_grams & ts_grams) / union >= 0.20:
                        return True
            return False

        filtered = []
        for issue in report.issues:
            if getattr(issue, "status", "open") != "open":
                filtered.append(issue)
                continue
            if _is_target(issue):
                filtered.append(issue)
            elif issue.severity in {"critical", "high"}:
                filtered.append(issue)

        if len(filtered) == len(report.issues):
            return report

        score = 10.0
        for issue in filtered:
            if getattr(issue, "status", "open") != "open":
                continue
            score -= cls._issue_penalty(
                {
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "evidence": issue.evidence,
                }
            )
        score = max(0.0, min(10.0, round(score, 1)))

        return ContinuityReport.model_validate(
            {
                "continuity_score": score,
                "summary": report.summary,
                "issues": [
                    iss.model_dump(mode="json") if hasattr(iss, "model_dump") else dict(iss)
                    for iss in filtered
                ],
            }
        )
