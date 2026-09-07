"""Issue normalization, deduplication, and fix action parsing."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.review.audit_taxonomy import is_continuity_issue
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    coerce_paragraph_count,
)
from novel_forge.core.utils.string import clean_str
from novel_forge.pipeline.steps.continuity_eval.semantics import _IssueSemantics


class _IssueNormalizer(_IssueSemantics):
    """Methods for normalizing LLM output issues, deduplication, and anchor enrichment."""

    _SEVERITY_NORM: dict[str, str] = {
        "critical": "critical",
        "严重": "critical",
        "极严重": "critical",
        "high": "high",
        "高": "high",
        "medium": "medium",
        "中": "medium",
        "中等": "medium",
        "一般": "medium",
        "low": "low",
        "低": "low",
        "轻微": "low",
        "minor": "low",
    }
    _REWRITE_SCOPE_NORM: dict[str, str] = {
        "opening": "opening",
        "开头": "opening",
        "开场": "opening",
        "middle": "middle",
        "中段": "middle",
        "中间": "middle",
        "closing": "closing",
        "结尾": "closing",
        "末尾": "closing",
        "结束": "closing",
        "chapter": "chapter",
        "全章": "chapter",
        "全文": "chapter",
        "paragraph": "paragraph",
        "段落": "paragraph",
        "段": "paragraph",
    }
    _VALID_REWRITE_SCOPES: frozenset[str] = frozenset(
        {"opening", "middle", "closing", "chapter", "paragraph"}
    )
    _ISSUE_TYPE_ALIASES: dict[str, str] = {
        "bridge_contract_violation": "bridge_contract_not_followed",
        "bridge_contract_mismatch": "bridge_contract_not_followed",
        "handoff_gap": "handoff_missing",
        "missing_carry_forward": "carry_forward_missing",
        "text_repeat": "text_repetition",
        "sensory_repetition": "sensory_anchor_repetition",
        "continuity_break": "continuity_gap",
        "fact_error": "factual_error",
        "pov_violation": "pov_jump",
        "pov_breach": "pov_jump",
        "perspective_violation": "pov_jump",
    }
    _RESOLVED_SUMMARY_MARKERS = frozenset(
        {
            "无需修复",
            "无须修复",
            "已解决",
            "已经解决",
            "经验证不成立",
            "问题不成立",
            "不再成立",
            "已消除",
            "已修复",
        }
    )

    @classmethod
    def _normalize_issue_type(cls, value: Any) -> str:
        raw = clean_str(value).lower()
        if not raw:
            return "continuity_gap"
        return cls._ISSUE_TYPE_ALIASES.get(raw, raw)

    @classmethod
    def _is_continuity_issue_type(cls, value: Any) -> bool:
        return is_continuity_issue(issue_type=cls._normalize_issue_type(value))

    @classmethod
    def _is_continuity_issue(cls, issue: dict[str, Any]) -> bool:
        return is_continuity_issue(
            issue_type=cls._normalize_issue_type(issue.get("issue_type")),
            source_module=issue.get("source_module", ""),
            dimension=issue.get("dimension", ""),
            summary=issue.get("summary", ""),
            evidence=issue.get("evidence") or issue.get("evidence_quote") or "",
            metadata=issue.get("metadata") if isinstance(issue.get("metadata"), dict) else None,
        )

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return 0
        return ivalue if ivalue > 0 else 0

    @staticmethod
    def _format_paragraph_location(start: int, end: int, note: str = "") -> str:
        if start <= 0:
            return note
        end = end if end > 0 else start
        prefix = f"第{start}段" if start == end else f"第{start}-{end}段"
        return f"{prefix}（{note}）" if note else prefix

    @staticmethod
    def _number_paragraphs(text: str) -> str:
        paragraphs = [p.strip() for p in str(text or "").split("\n\n") if p.strip()]
        if not paragraphs:
            return str(text or "")
        return "\n\n".join(f"[第{idx}段] {para}" for idx, para in enumerate(paragraphs, 1))

    @classmethod
    def _span_from_scope(
        cls,
        *,
        issue_type: str,
        rewrite_scope: str,
        total_paragraphs: int,
        opening_window_paragraphs: int = DEFAULT_OPENING_PARAGRAPHS,
    ) -> tuple[int, int, str, float] | None:
        if total_paragraphs <= 0:
            return None
        issue_type = issue_type.lower()
        scope = rewrite_scope.lower()
        opening_end = min(
            coerce_paragraph_count(
                opening_window_paragraphs,
                default=DEFAULT_OPENING_PARAGRAPHS,
                maximum=8,
            ),
            total_paragraphs,
        )
        if (
            scope == "opening"
            or "opening" in issue_type
            or issue_type in {"bridge_contract_not_followed", "location_jump", "custody_break"}
        ):
            return 1, opening_end, "开头承接处", 0.72
        if scope == "closing" or any(token in issue_type for token in ("closing", "ending")):
            start = max(1, total_paragraphs - 2)
            return start, total_paragraphs, "结尾交接处", 0.72
        if scope == "middle":
            mid = max(1, (total_paragraphs + 1) // 2)
            return max(1, mid - 1), min(total_paragraphs, mid + 1), "中段", 0.55
        if issue_type == "carry_forward_missing":
            return 1, opening_end, "承接上一章遗留信息处", 0.70
        return None

    @classmethod
    def _infer_fix_mode(cls, issue: dict[str, Any]) -> str:
        raw = clean_str(issue.get("fix_mode")).lower()
        issue_type = clean_str(issue.get("issue_type")).lower()
        rewrite_scope = clean_str(issue.get("rewrite_scope")).lower()
        is_opening_boundary = issue_type == "opening_gap" or (
            issue_type == "bridge_contract_not_followed"
            and rewrite_scope in {"", "chapter", "opening"}
        )
        if raw in {"none", "no_op", "noop", "skip", "resolved"}:
            return raw
        if is_opening_boundary and raw != "fulltext":
            return "window"
        if raw in {"replace", "insert", "window", "fulltext"}:
            return raw
        has_para_anchor = cls._positive_int(issue.get("paragraph_start")) > 0
        if any(
            token in issue_type for token in ("missing", "carry_forward", "opening_gap", "bridge")
        ):
            return "insert" if has_para_anchor else "window"
        if any(
            token in issue_type
            for token in ("forbidden", "repetition", "prompt_leak", "time_marker", "pov")
        ):
            return "replace" if has_para_anchor else "window"
        scope = clean_str(issue.get("rewrite_scope")).lower()
        if scope in {"opening", "middle", "closing", "paragraph"}:
            return "window"
        return "fulltext"

    @classmethod
    def _enrich_issue_anchor(
        cls,
        issue: dict[str, Any],
        chapter_text: str,
        *,
        opening_window_paragraphs: int = DEFAULT_OPENING_PARAGRAPHS,
    ) -> dict[str, Any]:
        if not isinstance(issue, dict):
            return issue

        from novel_forge.core.utils.patch_utils import resolve_paragraph_locally, split_paragraphs

        enriched = dict(issue)
        paragraphs = split_paragraphs(chapter_text or "")
        total = len(paragraphs)
        opening_end = min(
            coerce_paragraph_count(
                opening_window_paragraphs,
                default=DEFAULT_OPENING_PARAGRAPHS,
                maximum=8,
            ),
            total,
        )
        if total <= 0:
            enriched["fix_mode"] = cls._infer_fix_mode(enriched)
            return enriched

        start = cls._positive_int(enriched.get("paragraph_start"))
        end = cls._positive_int(enriched.get("paragraph_end")) or start
        raw_conf = enriched.get("location_confidence")
        try:
            confidence = float(raw_conf or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if start <= 0:
            evidence = clean_str(enriched.get("evidence_quote")) or clean_str(
                enriched.get("evidence")
            )
            location = clean_str(enriched.get("location"))
            targets, anchor_type, resolved_conf = resolve_paragraph_locally(
                paragraphs,
                evidence=evidence,
                location=location,
            )
            if anchor_type != "fallback" and targets:
                start = min(targets) + 1
                end = max(targets) + 1
                enriched["anchor_type"] = enriched.get("anchor_type") or anchor_type
                confidence = max(confidence, resolved_conf)
                if not enriched.get("evidence_quote") and anchor_type.startswith("evidence"):
                    enriched["evidence_quote"] = evidence

        if start <= 0:
            inferred = cls._span_from_scope(
                issue_type=clean_str(enriched.get("issue_type")),
                rewrite_scope=clean_str(enriched.get("rewrite_scope")),
                total_paragraphs=total,
                opening_window_paragraphs=opening_window_paragraphs,
            )
            if inferred:
                start, end, note, inferred_conf = inferred
                enriched["anchor_type"] = enriched.get("anchor_type") or "inferred_scope"
                confidence = max(confidence, inferred_conf)
                if not enriched.get("location"):
                    enriched["location"] = cls._format_paragraph_location(start, end, note)

        if start > 0:
            issue_type = clean_str(enriched.get("issue_type")).lower()
            rewrite_scope = clean_str(enriched.get("rewrite_scope")).lower()
            is_opening_boundary = issue_type == "opening_gap" or (
                issue_type == "bridge_contract_not_followed"
                and rewrite_scope in {"", "chapter", "opening"}
            )
            if is_opening_boundary and start <= opening_end:
                start = 1
                end = max(end, opening_end)
            start = max(1, min(start, total))
            end = max(start, min(end or start, total))
            enriched["paragraph_start"] = start
            enriched["paragraph_end"] = end
            if is_opening_boundary and end > start:
                enriched["location"] = cls._format_paragraph_location(start, end, "开头承接窗口")
            elif not enriched.get("location"):
                enriched["location"] = cls._format_paragraph_location(start, end)
            if not enriched.get("anchor_type"):
                enriched["anchor_type"] = "explicit_para"
            enriched["location_confidence"] = round(max(confidence, 0.7), 2)

            if (
                clean_str(enriched.get("fix_mode")).lower() == "insert"
                and not cls._positive_int(enriched.get("insert_after_para"))
                and not cls._positive_int(enriched.get("insert_before_para"))
            ):
                enriched["insert_after_para"] = start

        enriched["fix_mode"] = cls._infer_fix_mode(enriched)
        return enriched

    @classmethod
    def _is_resolved_recheck_note(cls, issue: dict[str, Any]) -> bool:
        """Return True when a targeted recheck note explicitly says no issue remains."""

        fix_mode = clean_str(issue.get("fix_mode")).lower()
        if fix_mode in {"none", "no_op", "noop", "skip", "resolved"}:
            return True

        text = " ".join(
            clean_str(issue.get(key)) for key in ("summary", "evidence", "evidence_quote")
        )
        if not text:
            return False
        return any(marker in text for marker in cls._RESOLVED_SUMMARY_MARKERS)

    @classmethod
    def _normalize_fix_actions(cls, raw_actions: Any) -> list[str]:
        if not isinstance(raw_actions, list):
            return []

        actions: list[str] = []
        for raw in raw_actions:
            if isinstance(raw, str):
                cleaned = clean_str(raw)
            elif isinstance(raw, dict):
                paragraph = cls._positive_int(raw.get("paragraph"))
                original = clean_str(raw.get("original"))
                suggestion = clean_str(raw.get("suggestion"))
                target_text = clean_str(raw.get("target_text"))
                replacement_text = clean_str(raw.get("replacement_text"))
                rationale = clean_str(raw.get("rationale"))
                parts: list[str] = []
                if paragraph > 0:
                    parts.append(f"第{paragraph}段")
                if original and suggestion:
                    parts.append(f"将\u201c{original}\u201d改为\u201c{suggestion}\u201d")
                elif target_text and replacement_text:
                    parts.append(f"将\u201c{target_text}\u201d替换为\u201c{replacement_text}\u201d")
                elif suggestion:
                    parts.append(suggestion)
                elif replacement_text:
                    parts.append(replacement_text)
                else:
                    for key in (
                        "action",
                        "advice",
                        "instruction",
                        "summary",
                        "note",
                        "description",
                    ):
                        value = clean_str(raw.get(key))
                        if value:
                            parts.append(value)
                            break
                if rationale:
                    parts.append(f"理由：{rationale}")
                cleaned = "；".join(part for part in parts if part)
            else:
                cleaned = clean_str(raw)
            if cleaned:
                actions.append(cleaned)
        return actions

    @classmethod
    def _normalize_issues(
        cls,
        payload: Any,
        chapter_text: str = "",
        *,
        opening_window_paragraphs: int = DEFAULT_OPENING_PARAGRAPHS,
    ) -> list[dict[str, Any]]:
        raw_list = payload if isinstance(payload, list) else []
        issues: list[dict[str, Any]] = []
        for raw in raw_list:
            if isinstance(raw, str):
                summary = clean_str(raw)
                if summary:
                    issues.append(
                        {
                            "issue_id": "",
                            "issue_type": "continuity_gap",
                            "severity": "medium",
                            "confidence": 0.0,
                            "source": "llm",
                            "repair_surface": "chapter_text",
                            "status": "open",
                            "blocking": False,
                            "summary": summary,
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
                            "fix_actions": [],
                            "expected_state": {},
                            "observed_state": {},
                            "missing_anchors": [],
                            "postconditions": [],
                            "repair_directive": None,
                            "validator_id": "",
                            "diagnostic_note": "",
                        }
                    )
                continue
            if not isinstance(raw, dict):
                continue
            if not cls._is_continuity_issue(raw):
                continue
            _raw_sev = clean_str(raw.get("severity")).lower()
            _norm_sev = cls._SEVERITY_NORM.get(_raw_sev, "medium")
            _raw_scope = clean_str(raw.get("rewrite_scope")).lower()
            _norm_scope = cls._REWRITE_SCOPE_NORM.get(_raw_scope)
            if _norm_scope is None:
                _norm_scope = _raw_scope if _raw_scope in cls._VALID_REWRITE_SCOPES else "chapter"
            issue = {
                "issue_id": clean_str(raw.get("issue_id")),
                "issue_type": cls._normalize_issue_type(raw.get("issue_type")),
                "severity": _norm_sev,
                "confidence": raw.get("confidence") or 0.0,
                "source": clean_str(raw.get("source")),
                "repair_surface": clean_str(raw.get("repair_surface")).lower(),
                "status": clean_str(raw.get("status")).lower(),
                "blocking": bool(raw.get("blocking", False)),
                "summary": clean_str(raw.get("summary")),
                "evidence": clean_str(raw.get("evidence")),
                "location": clean_str(raw.get("location")),
                "location_confidence": raw.get("location_confidence") or 0.0,
                "anchor_type": clean_str(raw.get("anchor_type")),
                "paragraph_start": cls._positive_int(raw.get("paragraph_start")),
                "paragraph_end": cls._positive_int(raw.get("paragraph_end")),
                "evidence_quote": clean_str(raw.get("evidence_quote")),
                "fix_mode": clean_str(raw.get("fix_mode")).lower(),
                "insert_before_para": cls._positive_int(raw.get("insert_before_para")),
                "insert_after_para": cls._positive_int(raw.get("insert_after_para")),
                "affected_characters": (
                    raw.get("affected_characters")
                    if isinstance(raw.get("affected_characters"), list)
                    else []
                ),
                "rewrite_scope": _norm_scope,
                "fix_actions": cls._normalize_fix_actions(raw.get("fix_actions")),
                "expected_state": raw.get("expected_state")
                if isinstance(raw.get("expected_state"), dict)
                else {},
                "observed_state": raw.get("observed_state")
                if isinstance(raw.get("observed_state"), dict)
                else {},
                "missing_anchors": raw.get("missing_anchors")
                if isinstance(raw.get("missing_anchors"), list)
                else [],
                "postconditions": raw.get("postconditions")
                if isinstance(raw.get("postconditions"), list)
                else [],
                "repair_directive": raw.get("repair_directive")
                if isinstance(raw.get("repair_directive"), dict)
                else None,
                "validator_id": clean_str(raw.get("validator_id")),
                "diagnostic_note": clean_str(raw.get("diagnostic_note")),
            }
            if cls._is_resolved_recheck_note(issue):
                continue
            issues.append(
                cls._enrich_issue_anchor(
                    issue,
                    chapter_text,
                    opening_window_paragraphs=opening_window_paragraphs,
                )
            )
        return [
            cls._enrich_issue_anchor(
                issue,
                chapter_text,
                opening_window_paragraphs=opening_window_paragraphs,
            )
            for issue in issues
            if not cls._is_resolved_recheck_note(issue)
        ]

    @classmethod
    def _dedupe_issues(cls, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_exact: set[tuple[str, str]] = set()
        seen_fuzzy: dict[tuple[str, int], dict[str, Any]] = {}

        for issue in issues:
            issue_type = issue.get("issue_type", "")
            summary = issue.get("summary", "")

            key_exact = (issue_type, summary)
            if key_exact in seen_exact:
                continue
            seen_exact.add(key_exact)

            tokens = cls._normalize_summary_tokens(summary)
            token_hash = hash(tokens)
            key_fuzzy = (issue_type, token_hash)

            if key_fuzzy in seen_fuzzy:
                existing = seen_fuzzy[key_fuzzy]
                existing_tokens = cls._normalize_summary_tokens(existing.get("summary", ""))
                intersection = tokens & existing_tokens
                union = tokens | existing_tokens
                if len(union) > 0 and len(intersection) / len(union) > 0.7:
                    existing_sev = existing.get("severity", "low")
                    new_sev = issue.get("severity", "low")
                    if cls._severity_rank(new_sev) > cls._severity_rank(existing_sev):
                        for idx, d in enumerate(deduped):
                            if d is existing:
                                deduped[idx] = issue
                                seen_fuzzy[key_fuzzy] = issue
                                break
                    continue

            scope = issue.get("fix_mode", issue.get("rewrite_scope", ""))
            location_matched = False
            for existing in deduped:
                if (
                    existing.get("issue_type") == issue_type
                    and existing.get("fix_mode", existing.get("rewrite_scope", "")) == scope
                    and cls._location_nearby(
                        existing.get("location", ""), issue.get("location", "")
                    )
                ):
                    if cls._severity_rank(issue.get("severity", "low")) >= cls._severity_rank(
                        existing.get("severity", "low")
                    ):
                        idx = deduped.index(existing)
                        deduped[idx] = issue
                    location_matched = True
                    break

            if not location_matched:
                deduped.append(issue)
                seen_fuzzy[key_fuzzy] = issue

        return deduped

    @classmethod
    def _normalize_summary_tokens(cls, summary: str) -> frozenset[str]:
        _STOP_WORDS = frozenset(
            {
                "的",
                "了",
                "在",
                "是",
                "有",
                "和",
                "与",
                "及",
                "或",
                "但",
                "而",
                "这",
                "那",
                "中",
                "其",
                "一个",
                "都",
                "就",
                "才",
                "很",
                "却",
                "已",
                "并",
                "被",
                "把",
                "从",
                "向",
                "到",
                "过",
                "着",
            }
        )
        _PUNCT_CHARS = set("，。！？、；：\"\"''（）[]{}【】 \t\n\r")
        text = "".join(c for c in summary if c not in _PUNCT_CHARS)
        tokens = []
        for i in range(len(text)):
            if text[i] not in _STOP_WORDS:
                tokens.append(text[i])
                if i + 1 < len(text) and text[i + 1] not in _STOP_WORDS:
                    tokens.append(text[i : i + 2])
        return frozenset(tokens[:10])

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity.lower(), 0)

    @staticmethod
    def _location_nearby(loc1: str, loc2: str) -> bool:
        m1 = re.search(r"第(\d+)", loc1)
        m2 = re.search(r"第(\d+)", loc2)
        if m1 and m2:
            return abs(int(m1.group(1)) - int(m2.group(1))) <= 2
        return loc1 == loc2
