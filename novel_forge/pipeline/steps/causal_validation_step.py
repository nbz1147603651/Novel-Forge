"""CausalValidationStep — validates causal chain within a chapter."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from novel_forge.core.constants import TaskType
from novel_forge.core.review.audit_profiles import recheck_policy_context
from novel_forge.core.review.review_contracts import normalize_review_mode
from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.core.schemas.continuity import ChapterBridge
from novel_forge.core.utils.audit_issue import (
    audit_issue_prompt_payload,
    stable_issue_id,
)
from novel_forge.core.utils.field_extractor import field
from novel_forge.core.utils.string import clean_str
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep

_logger = get_logger("pipeline.causal_validation")


class CausalValidationInput(BaseModel):
    """Input for causal chain validation."""

    model_config = {"arbitrary_types_allowed": True}

    chapter_number: int
    chapter_text: str
    chapter_bridge: ChapterBridge
    causal_link: dict[str, Any]
    must_resolve_summaries: list[str] = Field(default_factory=list)
    """Critical/high-severity summaries from a prior repair round — injected so the
    eval model explicitly verifies each one was resolved."""
    must_resolve_issue_ids: list[str] = Field(default_factory=list)
    """Issue IDs for precise targeting during recheck. Preferred over summaries when available."""
    character_notes: str = ""
    """Optional one-liner per key character describing their special abilities / role.
    Injected into the prompt so the LLM can correctly attribute supernatural actions
    to established character capabilities rather than reporting event_without_cause."""
    previous_chapter_ending: str = ""
    """Tail text of the previous chapter (up to ~800 chars). Injected so the validator
    can verify whether the chapter opening actually connects to the previous chapter's
    concrete text, not just abstract bridge metadata."""
    recheck_mode: bool = False
    """When True, the prompt frames this run as a targeted repair-verification pass
    rather than a full audit. The LLM is asked to verify the repaired issues first
    and only flag obvious regressions introduced by the repair itself."""
    recheck_strategy: Literal["strict_targeted", "targeted_with_global_guard"] = (
        "targeted_with_global_guard"
    )
    """Controls the scope of post-repair recheck.
    - strict_targeted: only verify target issues, no additional scanning.
    - targeted_with_global_guard: verify targets + scan for new high/critical issues."""
    prior_issues: list[dict[str, Any]] = Field(default_factory=list)
    """Issues selected for this repair round (pre-repair snapshot).
    Used to keep recheck focused on the edited targets."""
    repaired_issue_types: list[str] = Field(default_factory=list)
    """Issue types involved in this repair cycle."""
    patch_only_repair: bool = False
    """True when repair used narrow-window patches only."""
    strict_review: bool = False
    """When True, use lower temperature and inject an independent reviewer prompt.
    Used for post-repair re-evaluation to reduce false positives."""
    kernel_context: dict[str, Any] = Field(default_factory=dict)
    """Minimal field slice from StoryKernel via ContextComposer.
    Contains causal-relevant fields: entities, relationships, timeline,
    knowledge_ledger, business_dependencies, promise_ledger.
    Injected into the LLM prompt so the model can reference canonical state."""


class CausalValidationStep(PipelineStep[CausalValidationInput, CausalValidationReport]):
    """Validates causal chain integrity via LLM deep audit."""

    _MIN_EMPTY_ISSUE_SCORE = 7.0
    _SEVERITY_WEIGHTS = {
        "critical": 3.0,
        "high": 2.0,
        "medium": 1.0,
        "low": 0.5,
    }
    _VALID_ISSUE_TYPES = {
        "opening_causal_gap",
        "missing_causal_transition",
        "event_without_cause",
        "unmotivated_decision",
        "question_resolved_too_early",
        "question_ignored",
        "causal_contradiction",
    }
    _NON_CAUSAL_ISSUE_TYPES = {
        "address_form_mismatch",
        "character_not_in_plan",
        "expression_clarity",
        "forbidden_element_usage",
        "forbidden_element_violation",
        "prompt_leak",
        "pov_intrusion",
        "text_repetition",
        "time_marker_invalid",
    }
    _VALID_SEVERITIES = {"critical", "high", "medium", "low"}

    @property
    def step_name(self) -> str:
        return "causal_validation"

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return 0
        return ivalue if ivalue > 0 else 0

    @staticmethod
    def _number_paragraphs(text: str) -> str:
        paragraphs = [p.strip() for p in str(text or "").split("\n\n") if p.strip()]
        if not paragraphs:
            return str(text or "")
        return "\n\n".join(f"[第{idx}段] {para}" for idx, para in enumerate(paragraphs, 1))

    @staticmethod
    def _parse_location_span(location: str, total_paragraphs: int) -> tuple[int, int] | None:
        if not location or total_paragraphs <= 0:
            return None
        loc = str(location)
        m = re.search(r"第\s*(\d+)\s*[－–—\-~]\s*(\d+)\s*段", loc)
        if m:
            start = max(1, min(int(m.group(1)), total_paragraphs))
            end = max(1, min(int(m.group(2)), total_paragraphs))
            if start > end:
                start, end = end, start
            return start, end
        m = re.search(r"第\s*(\d+)\s*段", loc)
        if m:
            para = max(1, min(int(m.group(1)), total_paragraphs))
            return para, para
        if any(token in loc for token in ("开头", "首段", "起首")):
            return 1, min(2, total_paragraphs)
        if any(token in loc for token in ("结尾", "末段", "尾段", "最后")):
            return max(1, total_paragraphs - 1), total_paragraphs
        return None

    @staticmethod
    def _norm_text(value: Any) -> str:
        text = "" if value is None else str(value)
        return "".join(ch.lower() for ch in text if ch.isalnum())

    @classmethod
    def _clean_list(
        cls,
        values: Any,
        *,
        limit: int | None = None,
        item_limit: int | None = None,
    ) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = clean_str(value)
            if item_limit is not None and len(text) > item_limit:
                text = text[: item_limit - 1].rstrip("，。、；： \n") + "…"
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
            if limit is not None and len(cleaned) >= limit:
                break
        return cleaned

    @classmethod
    def _issue_summary(cls, raw: dict[str, Any]) -> str:
        for key in (
            "summary",
            "description",
            "diagnostic_note",
            "rationale",
            "message",
            "fix_suggestion",
            "suggested_fix",
        ):
            value = clean_str(raw.get(key))
            if value:
                return value

        repair_directive = raw.get("repair_directive")
        if isinstance(repair_directive, dict):
            for key in ("summary", "description", "instruction", "rationale", "target_window"):
                value = clean_str(repair_directive.get(key))
                if value:
                    return value
        elif clean_str(repair_directive):
            return clean_str(repair_directive)

        for key in ("missing_anchors", "postconditions", "fix_actions"):
            values = raw.get(key)
            if isinstance(values, (list, tuple, set)):
                joined = "；".join(
                    item for item in (clean_str(value) for value in values) if item
                )
                if joined:
                    return joined

        evidence = clean_str(raw.get("evidence") or raw.get("evidence_quote"))
        return evidence

    @classmethod
    def _scope_causal_link(cls, causal_link: Any) -> dict[str, Any]:
        return {
            "previous_event": clean_str(field(causal_link, "previous_event")),
            "causal_mechanism": clean_str(field(causal_link, "causal_mechanism")),
            "unresolved_question": clean_str(field(causal_link, "unresolved_question")),
            "open_threads": cls._clean_list(field(causal_link, "open_threads", [])),
        }

    @classmethod
    def _scope_bridge(cls, bridge: ChapterBridge) -> dict[str, Any]:
        return {
            "bridge_summary": clean_str(field(bridge, "bridge_summary")),
            "emotional_carryover": clean_str(field(bridge, "emotional_carryover")),
            "action_handoff": clean_str(field(bridge, "action_handoff")),
            "opening_location": clean_str(field(bridge, "opening_location")),
            "opening_time": clean_str(field(bridge, "opening_time")),
        }

    @classmethod
    def _scope_prior_issues(
        cls,
        issues: list[dict[str, Any]],
        *,
        chapter_number: int = 0,
    ) -> list[dict[str, Any]]:
        scoped: list[dict[str, Any]] = []
        for issue in list(issues or []):
            if not isinstance(issue, dict):
                continue
            scoped.append(
                audit_issue_prompt_payload(
                    issue,
                    dimension="causal",
                    chapter_number=chapter_number,
                    source_module="causal_validation",
                )
            )
        return scoped

    @classmethod
    def _issue_id(
        cls,
        raw: dict[str, Any],
        *,
        issue_type: str,
        summary: str,
        evidence: str,
        location: str,
    ) -> str:
        explicit = clean_str(raw.get("issue_id"))[:120]
        if explicit:
            return explicit
        return stable_issue_id(
            "causal",
            issue_type=issue_type,
            summary=summary,
            evidence=raw.get("evidence_quote") or evidence,
            location=cls._location_key(location),
        )

    @classmethod
    def _build_llm_context(
        cls,
        input_data: CausalValidationInput,
        *,
        independent_reviewer_note: str = "",
    ) -> dict[str, Any]:
        has_previous_chapter = bool(
            input_data.chapter_number > 1
            and (
                clean_str(input_data.previous_chapter_ending)
                or clean_str((input_data.causal_link or {}).get("previous_event"))
                or clean_str(getattr(input_data.chapter_bridge.causal_link, "previous_event", ""))
            )
        )
        return {
            "chapter_number": input_data.chapter_number,
            "has_previous_chapter": has_previous_chapter,
            "is_first_chapter": input_data.chapter_number == 1,
            "chapter_text": input_data.chapter_text,
            "numbered_chapter_text": cls._number_paragraphs(input_data.chapter_text),
            "chapter_bridge": cls._scope_bridge(input_data.chapter_bridge),
            "causal_link": cls._scope_causal_link(input_data.causal_link),
            "must_resolve_summaries": cls._clean_list(input_data.must_resolve_summaries),
            "must_resolve_issue_ids": cls._clean_list(input_data.must_resolve_issue_ids),
            "character_notes": clean_str(input_data.character_notes),
            "previous_chapter_ending": clean_str(input_data.previous_chapter_ending)[-800:],
            "skip_previous_chapter_handoff": not has_previous_chapter,
            "recheck_mode": input_data.recheck_mode,
            "recheck_strategy": input_data.recheck_strategy,
            "recheck_policy": recheck_policy_context("causal"),
            "prior_issues": cls._scope_prior_issues(
                input_data.prior_issues,
                chapter_number=input_data.chapter_number,
            ),
            "repaired_issue_types": cls._clean_list(input_data.repaired_issue_types),
            "patch_only_repair": input_data.patch_only_repair,
            "strict_review": input_data.strict_review,
            "independent_reviewer_note": independent_reviewer_note,
            "kernel_context": input_data.kernel_context or {},
        }

    @classmethod
    def _summary_related(cls, summary: str, targets: list[str]) -> bool:
        norm_summary = cls._norm_text(summary)
        if not norm_summary:
            return False

        def _trigrams(text: str) -> set[str]:
            return {text[i : i + 3] for i in range(len(text) - 2)}

        summary_grams = _trigrams(norm_summary)

        for target in targets:
            norm_target = cls._norm_text(target)
            if not norm_target:
                continue
            if norm_summary in norm_target or norm_target in norm_summary:
                return True
            # Jaccard similarity on trigrams — more robust against LLM paraphrasing
            # than the original 4-gram sliding-window hit count.
            target_grams = _trigrams(norm_target)
            if summary_grams and target_grams:
                union = len(summary_grams | target_grams)
                if union and len(summary_grams & target_grams) / union >= 0.20:
                    return True
            # Fallback: sliding-window 4-gram hit count (original logic).
            if len(norm_summary) >= 8 and len(norm_target) >= 8:
                hits = 0
                for i in range(len(norm_summary) - 3):
                    if norm_summary[i : i + 4] in norm_target:
                        hits += 1
                        if hits >= 2:
                            return True
        return False

    @classmethod
    def _location_key(cls, location: str) -> str:
        return cls._norm_text(location)

    @classmethod
    def _score_from_issues(cls, issues: list[CausalIssue]) -> float:
        score = 10.0
        for issue in issues:
            score -= cls._SEVERITY_WEIGHTS.get(issue.severity, 1.0)
        return max(0.0, min(10.0, round(score, 1)))

    @classmethod
    def _summary_from_issues(cls, issues: list[CausalIssue], *, recheck_mode: bool) -> str:
        if not issues:
            return (
                "修复目标已通过点验，未发现需报告的因果问题。"
                if recheck_mode
                else "因果链完整，无明显断裂。"
            )
        high_count = sum(1 for i in issues if i.severity in {"critical", "high"})
        summary = f"发现{len(issues)}处因果链问题"
        if high_count:
            summary += f"，其中{high_count}处为高优先级"
        summary += "。"
        return summary

    @classmethod
    def _apply_recheck_focus(
        cls,
        report: CausalValidationReport,
        *,
        must_resolve_summaries: list[str],
        prior_issues: list[dict[str, Any]],
        recheck_strategy: Literal["strict_targeted", "targeted_with_global_guard"],
        must_resolve_issue_ids: list[str] | None = None,
    ) -> CausalValidationReport:
        """Filter recheck output so it stays focused on repaired targets.

        strict_targeted:
            keep only target-related issues.
        targeted_with_global_guard:
            keep target-related issues + newly exposed high/critical regressions.
        """
        if not report.issues:
            return report

        target_issue_ids = {
            str(issue_id).strip()
            for issue_id in (must_resolve_issue_ids or [])
            if str(issue_id).strip()
        }
        target_summaries = [clean_str(s) for s in must_resolve_summaries if clean_str(s)]
        target_signatures: set[tuple[str, str]] = set()
        for item in prior_issues:
            if not isinstance(item, dict):
                continue
            issue_id = clean_str(item.get("issue_id"))
            if issue_id:
                target_issue_ids.add(issue_id)
            itype = clean_str(item.get("issue_type")).lower()
            lkey = cls._location_key(clean_str(item.get("location")))
            if itype:
                target_signatures.add((itype, lkey))

        # If we have no target anchor at all, keep original report.
        if not target_issue_ids and not target_summaries and not target_signatures:
            return report

        filtered: list[CausalIssue] = []
        for issue in report.issues:
            issue_id = clean_str(getattr(issue, "issue_id", ""))
            issue_type = clean_str(issue.issue_type).lower()
            issue_loc = cls._location_key(issue.location)
            matches_target_signature = (issue_type, issue_loc) in target_signatures or any(
                sig_type == issue_type and sig_loc and sig_loc == issue_loc
                for sig_type, sig_loc in target_signatures
            )
            if target_issue_ids and issue_id and issue_id in target_issue_ids:
                is_target = True
            elif target_issue_ids and issue_id:
                # If the model failed to preserve the original ID, only trust
                # stronger type/location anchors; do not match by summary alone.
                is_target = matches_target_signature
            else:
                is_target = cls._summary_related(
                    issue.summary, target_summaries
                ) or matches_target_signature

            if is_target:
                filtered.append(issue)
                continue

            if recheck_strategy == "targeted_with_global_guard" and issue.severity in {
                "critical",
                "high",
            }:
                filtered.append(issue)

        if len(filtered) == len(report.issues):
            return report

        return CausalValidationReport(
            causal_score=cls._score_from_issues(filtered),
            summary=cls._summary_from_issues(filtered, recheck_mode=True),
            issues=filtered,
            causal_link_verified=not any(i.severity in {"critical", "high"} for i in filtered),
            validation_status=report.validation_status,
        )

    @classmethod
    def _normalize_issue(cls, raw: Any) -> CausalIssue | None:
        if not isinstance(raw, dict):
            return None
        issue_type = clean_str(raw.get("issue_type")).lower()
        if issue_type in cls._NON_CAUSAL_ISSUE_TYPES:
            return None
        if issue_type not in cls._VALID_ISSUE_TYPES:
            return None
        severity = clean_str(raw.get("severity")).lower()
        if severity not in cls._VALID_SEVERITIES:
            severity = "medium"
        summary = cls._issue_summary(raw)
        if not summary:
            return None
        paragraph_start = cls._positive_int(raw.get("paragraph_start"))
        paragraph_end = cls._positive_int(raw.get("paragraph_end")) or paragraph_start
        try:
            location_confidence = float(raw.get("location_confidence") or 0.0)
        except (TypeError, ValueError):
            location_confidence = 0.0
        location_confidence = max(0.0, min(1.0, location_confidence))
        location = clean_str(raw.get("location")) or "未指定"
        evidence = clean_str(raw.get("evidence"))
        return CausalIssue(
            issue_id=cls._issue_id(
                raw,
                issue_type=issue_type,
                summary=summary,
                evidence=evidence,
                location=location,
            ),
            issue_type=issue_type,
            severity=severity,
            location=location,
            location_confidence=location_confidence,
            anchor_type=clean_str(raw.get("anchor_type")),
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
            summary=summary,
            evidence=evidence,
            evidence_quote=clean_str(raw.get("evidence_quote")),
            fix_suggestion=clean_str(
                raw.get("fix_suggestion")
                or raw.get("suggested_fix")
                or raw.get("repair_suggestion")
            ),
            fix_mode=clean_str(raw.get("fix_mode")).lower(),
        )

    @classmethod
    def _enrich_issue_locations(
        cls,
        issues: list[CausalIssue],
        chapter_text: str,
    ) -> list[CausalIssue]:
        """Post-process issues to ensure every location has a paragraph number.

        If the LLM returned a descriptive location without a "第N段" prefix,
        use evidence text matching to find the paragraph index and prepend it.
        This ensures downstream consumers (repair window, issue_signature,
        recheck fuzzy match) can all reliably extract a paragraph anchor.
        """
        if not chapter_text or not issues:
            return issues

        paragraphs = [p for p in chapter_text.split("\n\n") if p.strip()]
        if not paragraphs:
            return issues

        enriched: list[CausalIssue] = []
        for issue in issues:
            location = issue.location or ""
            span = cls._parse_location_span(location, len(paragraphs))
            if issue.paragraph_start > 0:
                start = max(1, min(issue.paragraph_start, len(paragraphs)))
                end = max(start, min(issue.paragraph_end or start, len(paragraphs)))
                new_location = location
                if not new_location or new_location == "未指定":
                    new_location = f"第{start}段" if start == end else f"第{start}-{end}段"
                enriched.append(
                    issue.model_copy(
                        update={
                            "location": new_location,
                            "paragraph_start": start,
                            "paragraph_end": end,
                            "location_confidence": max(issue.location_confidence, 0.9),
                            "anchor_type": issue.anchor_type or "explicit_para",
                            "fix_mode": issue.fix_mode or cls._infer_fix_mode(issue),
                        }
                    )
                )
                continue

            # Already has paragraph number → turn it into explicit paragraph fields.
            if span is not None:
                start, end = span
                enriched.append(
                    issue.model_copy(
                        update={
                            "paragraph_start": start,
                            "paragraph_end": end,
                            "location_confidence": max(issue.location_confidence, 0.85),
                            "anchor_type": issue.anchor_type or "location_parsed",
                            "fix_mode": issue.fix_mode or cls._infer_fix_mode(issue),
                        }
                    )
                )
                continue

            # Try to locate via evidence text matching. Evidence is only promoted
            # to a paragraph anchor when it resolves unambiguously; descriptive
            # keyword fallback is deliberately lower-confidence.
            evidence_quote = issue.evidence_quote or issue.evidence or ""
            para_idx, ambiguous_evidence = cls._resolve_paragraph_by_evidence(
                evidence_quote, paragraphs
            )
            anchor_type = "evidence_match"
            location_confidence = 0.8
            if para_idx is None:
                # Fallback: try location keywords in paragraph content
                para_idx = cls._find_paragraph_by_keywords(location, paragraphs)
                anchor_type = "location_keyword"
                location_confidence = 0.5

            if para_idx is not None:
                para_num = para_idx + 1  # 0-based → 1-based
                if location and location != "未指定":
                    new_location = f"第{para_num}段（{location}）"
                else:
                    new_location = f"第{para_num}段"
                enriched.append(
                    issue.model_copy(
                        update={
                            "location": new_location,
                            "paragraph_start": para_num,
                            "paragraph_end": para_num,
                            "location_confidence": max(
                                issue.location_confidence, location_confidence
                            ),
                            "anchor_type": issue.anchor_type or anchor_type,
                            "evidence_quote": issue.evidence_quote or evidence_quote,
                            "fix_mode": issue.fix_mode or cls._infer_fix_mode(issue),
                        }
                    )
                )
            else:
                update = {"fix_mode": issue.fix_mode or cls._infer_fix_mode(issue)}
                if ambiguous_evidence:
                    update.update(
                        {
                            "anchor_type": issue.anchor_type or "anchor_degraded",
                            "evidence_quote": issue.evidence_quote or evidence_quote,
                        }
                    )
                enriched.append(issue.model_copy(update=update))

        return enriched

    @classmethod
    def _infer_fix_mode(cls, issue: CausalIssue) -> str:
        issue_type = (issue.issue_type or "").lower()
        if issue.fix_mode in {"replace", "insert", "window", "fulltext"}:
            return issue.fix_mode
        if issue_type == "opening_causal_gap":
            return "insert"
        if issue_type in {
            "missing_causal_transition",
            "event_without_cause",
            "unmotivated_decision",
            "question_resolved_too_early",
            "question_ignored",
        }:
            return "window"
        return "fulltext"

    @staticmethod
    def _find_paragraph_by_evidence(
        evidence: str,
        paragraphs: list[str],
    ) -> int | None:
        """Find the best-matching paragraph index for an evidence snippet."""
        para_idx, _ambiguous = CausalValidationStep._resolve_paragraph_by_evidence(
            evidence, paragraphs
        )
        return para_idx

    @staticmethod
    def _resolve_paragraph_by_evidence(
        evidence: str,
        paragraphs: list[str],
    ) -> tuple[int | None, bool]:
        """Find a paragraph by evidence and report whether the evidence is ambiguous."""
        if not evidence or not paragraphs:
            return None, False
        evidence_clean = evidence.replace("\n", "")
        if len(evidence_clean) < 6:
            return None, False
        # Fast path: direct substring match
        direct_hits: list[int] = []
        for i, para in enumerate(paragraphs):
            if evidence_clean in para.replace("\n", ""):
                direct_hits.append(i)
        if len(direct_hits) == 1:
            return direct_hits[0], False
        if direct_hits:
            return None, True
        # Slow path: sliding-window overlap for partial matches
        scored: list[tuple[int, int]] = []
        for i, para in enumerate(paragraphs):
            para_clean = para.replace("\n", "")
            overlap = 0
            for start in range(0, len(evidence_clean), 10):
                chunk = evidence_clean[start : start + 20]
                if len(chunk) >= 6 and chunk in para_clean:
                    overlap += len(chunk)
            if overlap > 0:
                scored.append((overlap, i))
        # Threshold lowered from 30 → 20 to match the 20-char minimum evidence spec.
        if not scored:
            return None, False
        scored.sort(reverse=True)
        best_overlap, best_idx = scored[0]
        if best_overlap < 20:
            return None, False
        if len(scored) > 1:
            second_overlap = scored[1][0]
            if second_overlap >= best_overlap * 0.75 or best_overlap - second_overlap < 10:
                return None, True
        return best_idx, False

    @staticmethod
    def _find_paragraph_by_keywords(
        location: str,
        paragraphs: list[str],
    ) -> int | None:
        """Find a paragraph by matching keywords from the location string."""
        if not location or not paragraphs:
            return None
        keywords = [w for w in re.split(r"[，。、\s]+", location) if len(w) >= 2]
        if not keywords:
            return None
        best_idx = -1
        best_hits = 0
        for i, para in enumerate(paragraphs):
            hits = sum(1 for kw in keywords if kw in para)
            if hits > best_hits:
                best_hits = hits
                best_idx = i
        return best_idx if best_idx >= 0 and best_hits >= 1 else None

    @classmethod
    def _normalize_llm_response(
        cls, payload: Any, local_issues: list[CausalIssue]
    ) -> CausalValidationReport:
        """Merge LLM result with local pre-check issues.

        Gating logic:
        - LLM issues always included.
        - Local issues are ONLY merged when:
          1. LLM validation was unavailable (payload is empty/no issues key), OR
          2. The local issue type matches an LLM-reported issue type (LLM agrees).
        - Otherwise, local issues are discarded to prevent phantom issues.
        """
        data = payload if isinstance(payload, dict) else {}

        llm_issues: list[CausalIssue] = []
        for raw in data.get("issues", []) or []:
            issue = cls._normalize_issue(raw)
            if issue:
                llm_issues.append(issue)

        llm_score = data.get("causal_score")
        llm_score_value: float | None = None
        if isinstance(llm_score, (int, float)) and 0.0 <= float(llm_score) <= 10.0:
            llm_score_value = round(float(llm_score), 1)

        # Gate: determine if LLM validation was available
        llm_available = bool(data.get("issues")) or llm_score_value is not None
        score_issue_mismatch = (
            llm_available
            and not llm_issues
            and llm_score_value is not None
            and llm_score_value < cls._MIN_EMPTY_ISSUE_SCORE
        )

        if llm_available and llm_issues:
            # LLM ran and returned issues: only merge local issues whose type
            # matches an LLM-reported type (LLM agrees with the category).
            llm_types = {i.issue_type for i in llm_issues}
            llm_keys = {
                (
                    li.issue_type,
                    getattr(li, "paragraph_start", 0),
                    getattr(li, "paragraph_end", 0),
                    (getattr(li, "evidence_quote", "") or "")[:50],
                )
                for li in llm_issues
            }
            deduped_local = [
                i
                for i in local_issues
                if i.issue_type in llm_types
                and (
                    i.issue_type,
                    getattr(i, "paragraph_start", 0),
                    getattr(i, "paragraph_end", 0),
                    (getattr(i, "evidence_quote", "") or "")[:50],
                )
                not in llm_keys
            ]
        elif not llm_available:
            # LLM unavailable: use all local issues as fallback
            deduped_local = list(local_issues)
        elif score_issue_mismatch and local_issues:
            # Low score + empty issue list is not an actionable clean bill of
            # health. Fall back to deterministic local issues if they exist.
            deduped_local = list(local_issues)
        else:
            # LLM available but returned no issues: discard local issues entirely
            deduped_local = []

        all_issues = llm_issues + deduped_local

        if llm_score_value is not None:
            if score_issue_mismatch and all_issues:
                score = min(llm_score_value, cls._score_from_issues(all_issues))
            else:
                score = llm_score_value
        else:
            score = cls._score_from_issues(all_issues)

        summary = clean_str(data.get("summary"))
        if score_issue_mismatch:
            if all_issues:
                summary = (
                    "因果校验返回低分但未给出可定位问题，已回退到本地预检问题。"
                )
            else:
                summary = (
                    "因果校验返回低分但未给出可定位问题，结果不一致，需重新校验或人工复核。"
                )
        if not summary:
            if not all_issues:
                summary = "因果链完整，无明显断裂。"
            else:
                high_count = sum(1 for i in all_issues if i.severity in {"critical", "high"})
                summary = f"发现{len(all_issues)}处因果链问题"
                if high_count:
                    summary += f"，其中{high_count}处为高优先级"
                summary += "。"

        causal_link_verified = data.get("causal_link_verified")
        if not isinstance(causal_link_verified, bool):
            causal_link_verified = not any(i.severity in {"critical", "high"} for i in all_issues)
        if score_issue_mismatch:
            causal_link_verified = False

        return CausalValidationReport(
            causal_score=score,
            summary=summary,
            issues=all_issues,
            causal_link_verified=causal_link_verified,
            validation_status="unavailable" if score_issue_mismatch and not all_issues else "ok",
        )

    @classmethod
    def _build_local_issues(cls, input_data: "CausalValidationInput") -> list[CausalIssue]:
        """Lightweight local heuristic pre-checks for structural causal issues.

        Complements LLM validation as a baseline safety net.  Focused on
        high-confidence, low-false-positive patterns:

        1. **opening_causal_gap** — chapter opening has zero keyword overlap with
           the previous chapter ending / causal_link.previous_event.
        2. **event_without_cause** — paragraph starts with an abrupt-event marker
           (突然/猛然/忽然…) with no noun overlap from the preceding paragraph.
        """
        issues: list[CausalIssue] = []
        text = input_data.chapter_text or ""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return issues

        # ── Check 1: Opening causal gap ───────────────────────────────────
        prev_ending = (input_data.previous_chapter_ending or "").strip()
        prev_event = ""
        causal_link_dict = input_data.causal_link or {}
        if isinstance(causal_link_dict, dict):
            prev_event = str(causal_link_dict.get("previous_event") or "").strip()
        if not prev_event and input_data.chapter_bridge.causal_link:
            prev_event = (input_data.chapter_bridge.causal_link.previous_event or "").strip()

        anchor_text = prev_event or prev_ending
        if anchor_text:
            keywords: set[str] = set(re.findall(r"[\u4e00-\u9fff]{2,4}", anchor_text))
            _STOPWORDS = {
                "我们",
                "你们",
                "他们",
                "她们",
                "它们",
                "这里",
                "那里",
                "然后",
                "开始",
                "知道",
                "看见",
                "发现",
                "已经",
                "因为",
                "所以",
                "虽然",
            }
            keywords -= _STOPWORDS
            opening_text = "\n\n".join(paragraphs[:2])

            # 多层检查策略：关键词匹配 → 语义相似度 → 过渡信号
            _has_keyword_overlap = keywords and any(kw in opening_text for kw in keywords)

            if not _has_keyword_overlap and keywords:
                # 关键词不匹配时，进行语义相似度检查
                # 使用 trigram Jaccard 相似度判断语义连贯性
                from novel_forge.core.domain.guardrails import (
                    text_has_release_signal,
                    text_has_transition_signal,
                )

                # 计算语义相似度
                def _trigrams(text: str) -> set[str]:
                    normed = "".join(ch.lower() for ch in text if ch.isalnum())
                    return {normed[i : i + 3] for i in range(len(normed) - 2)}

                anchor_grams = _trigrams(anchor_text)
                opening_grams = _trigrams(opening_text)

                semantic_similarity = 0.0
                if anchor_grams and opening_grams:
                    union = len(anchor_grams | opening_grams)
                    if union:
                        semantic_similarity = len(anchor_grams & opening_grams) / union

                _has_transition = text_has_transition_signal(opening_text)
                _has_release = text_has_release_signal(opening_text)

                # 通过条件：关键词重叠 OR 语义相似度足够高 OR 有过渡信号
                _passes_check = (
                    _has_keyword_overlap
                    or semantic_similarity >= 0.15  # 15% 相似度阈值
                    or _has_transition
                    or _has_release
                )

                if not _passes_check:
                    kw_sample = "、".join(list(keywords)[:5])
                    location = "第1段（章节开头）"
                    paragraph_end = min(2, len(paragraphs))
                    summary = "章节开头未见上章末尾关键事件的延续痕迹"
                    evidence = f"上章关键词（{kw_sample}）均未出现在开头两段，语义相似度不足"
                    issues.append(
                        CausalIssue(
                            issue_id=stable_issue_id(
                                "causal",
                                chapter_number=input_data.chapter_number,
                                issue_type="opening_causal_gap",
                                summary=summary,
                                evidence=evidence,
                                location=location,
                                repair_surface="chapter_text",
                                extra={
                                    "paragraph_start": 1,
                                    "paragraph_end": paragraph_end,
                                },
                            ),
                            issue_type="opening_causal_gap",
                            severity="medium",
                            location=location,
                            location_confidence=0.78,
                            anchor_type="inferred_scope",
                            paragraph_start=1,
                            paragraph_end=paragraph_end,
                            summary=summary,
                            evidence=evidence,
                            fix_suggestion=(
                                "在开头1-2段中自然引入上一章结尾的核心处境，使因果链不断裂"
                            ),
                            fix_mode="insert",
                        )
                    )

        # ── Check 2: Abrupt event without prior buildup ───────────────────
        _ABRUPT_RE = re.compile(r"^(突然|猛然|忽然|蓦然|顿时|骤然)")
        for i, para in enumerate(paragraphs[1:], start=1):
            if not _ABRUPT_RE.match(para):
                continue
            # Extract subject/action nouns from the abrupt sentence
            abrupt_sentence = para.split("。")[0][:80]
            prev_para = paragraphs[i - 1]
            abrupt_nouns: set[str] = set(re.findall(r"[\u4e00-\u9fff]{2,3}", abrupt_sentence))
            prev_nouns: set[str] = set(re.findall(r"[\u4e00-\u9fff]{2,3}", prev_para))
            # Only flag if no substantive noun overlap (reduces false positives)
            if abrupt_nouns and not abrupt_nouns.intersection(prev_nouns):
                match_result = _ABRUPT_RE.match(para) or re.match(r".", para)
                marker = match_result.group() if match_result else ""
                location = f"第{i + 1}段"
                summary = f"段落以\u300c{marker}\u300d起头，前文无铺垫"
                evidence = f"该段：\u300c{abrupt_sentence[:40]}\u2026\u300d；前段无关联词汇重叠"
                issues.append(
                    CausalIssue(
                        issue_id=stable_issue_id(
                            "causal",
                            chapter_number=input_data.chapter_number,
                            issue_type="event_without_cause",
                            summary=summary,
                            evidence=evidence,
                            location=location,
                            repair_surface="chapter_text",
                            extra={
                                "paragraph_start": i + 1,
                                "paragraph_end": i + 1,
                            },
                        ),
                        issue_type="event_without_cause",
                        severity="low",
                        location=location,
                        location_confidence=0.9,
                        anchor_type="explicit_para",
                        paragraph_start=i + 1,
                        paragraph_end=i + 1,
                        summary=summary,
                        evidence=evidence,
                        evidence_quote=abrupt_sentence,
                        fix_suggestion=("在该突兀事件前补充触发条件的铺垫，或将诱因插入前一段末尾"),
                        fix_mode="window",
                    )
                )

        return issues

    async def _execute(self, input_data: CausalValidationInput) -> CausalValidationReport:
        review_mode = normalize_review_mode(
            "targeted_recheck" if input_data.recheck_mode or input_data.strict_review else "full"
        )

        # Run local heuristic pre-checks regardless of LLM availability.
        local_issues = self._build_local_issues(input_data)

        # Strict review mode: lower temperature + independent reviewer prompt
        _review_temp = getattr(self.settings, "temp_post_repair_review", 0.0)
        _review_prompt = getattr(self.settings, "long_post_repair_review_independent_prompt", "")
        try:
            independent_reviewer_note = (
                _review_prompt if input_data.strict_review and _review_prompt else ""
            )
            raw_data = await self._call_with_retry(
                TaskType.VALIDATE_CAUSAL,
                self._build_llm_context(
                    input_data,
                    independent_reviewer_note=independent_reviewer_note,
                ),
                max_tokens=self._dynamic_max_tokens(
                    TaskType.VALIDATE_CAUSAL,
                    len(input_data.chapter_text),
                    prompt_overhead=2000,
                    max_cap=getattr(self.settings, "validate_causal_max_tokens", 16384),
                    safety_margin=0.90,
                    min_tokens=2048,
                ),
                temperature=_review_temp
                if input_data.strict_review
                else self.settings.temp_validate_causal,
            )
            report = self._normalize_llm_response(raw_data, local_issues)
            # Enrich locations: ensure every issue has a paragraph number
            # for reliable downstream consumption (repair window, signatures).
            report = CausalValidationReport(
                review_mode=review_mode,
                causal_score=report.causal_score,
                summary=report.summary,
                issues=self._enrich_issue_locations(
                    report.issues,
                    input_data.chapter_text,
                ),
                causal_link_verified=report.causal_link_verified,
                validation_status=report.validation_status,
            )
            if input_data.recheck_mode:
                report = self._apply_recheck_focus(
                    report,
                    must_resolve_summaries=input_data.must_resolve_summaries,
                    must_resolve_issue_ids=input_data.must_resolve_issue_ids,
                    prior_issues=input_data.prior_issues,
                    recheck_strategy=input_data.recheck_strategy,
                )
                report = report.model_copy(update={"review_mode": review_mode})
            return report
        except Exception as exc:
            fail_mode = (
                str(
                    getattr(self.settings, "causal_validation_fail_mode", "warn_unknown")
                    or "warn_unknown"
                )
                .strip()
                .lower()
            )
            _logger.warning(
                "LLM causal validation failed (chapter %d), skipping: %s",
                input_data.chapter_number,
                exc,
            )
            if fail_mode == "fail_open":
                _logger.warning(
                    "causal_validation_fail_open: 以 fail_open 模式放行因果链校验（LLM 调用失败）。"
                    "此模式存在风险——在高危问题被遗漏可能导致连续性退化。"
                    "建议将 causal_validation_fail_mode 设为 warn_unknown。"
                    " | chapter=%d | local_issues=%d",
                    input_data.chapter_number,
                    len(local_issues),
                )
                local_score = max(
                    0.0,
                    10.0 - sum(self._SEVERITY_WEIGHTS.get(i.severity, 1.0) for i in local_issues),
                )
                return CausalValidationReport(
                    review_mode=review_mode,
                    causal_score=round(local_score, 1),
                    summary=(
                        "因果链校验不可用（LLM 调用失败），已按 fail_open 策略放行"
                        + (
                            f"，本地预检发现{len(local_issues)}处问题"
                            if local_issues
                            else "，本地预检无问题"
                        )
                        + "。"
                    ),
                    issues=local_issues,
                    causal_link_verified=not any(
                        i.severity in {"critical", "high"} for i in local_issues
                    ),
                    validation_status="unavailable",
                )
            local_score = max(
                0.0, 10.0 - sum(self._SEVERITY_WEIGHTS.get(i.severity, 1.0) for i in local_issues)
            )
            return CausalValidationReport(
                review_mode=review_mode,
                causal_score=round(local_score, 1),
                summary=(
                    "因果链校验不可用（LLM 调用失败），结果依赖本地预检"
                    + (f"，发现{len(local_issues)}处问题" if local_issues else "，本地预检无问题")
                    + "，需人工复核。"
                ),
                issues=local_issues,
                causal_link_verified=False,
                validation_status="unavailable",
            )
