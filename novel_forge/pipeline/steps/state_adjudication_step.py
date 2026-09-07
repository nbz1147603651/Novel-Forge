"""LLM-adjudicated narrative state steps.

Local code in this module performs only mechanical work: context shaping,
schema parsing, quote location, and ledger persistence. Narrative verdicts
come from the LLM adjudication tasks.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.schemas.cognition import normalize_character_knowledge_coverage
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.narrative_state.evidence import EvidenceIndex
from novel_forge.narrative_state.knowledge_ops import (
    build_entity_lookup,
    knowledge_op_entity_ids,
    knowledge_op_evidence_candidates,
    knowledge_op_target_text,
    normalize_knowledge_op,
    normalize_knowledge_type,
)
from novel_forge.narrative_state.schemas import (
    AdjudicationDecision,
    CandidateStateDelta,
    ChapterContract,
    ContractCoverageItem,
    ContractCoverageReport,
    EvidenceSpan,
    FinalStateAdjudication,
    NarrativeAdjudicationReport,
    StateLedgerEntry,
    normalize_adjudication_severity,
    normalize_adjudication_verdict,
    normalize_delta_type,
    normalize_final_adjudication_payload,
    stable_id,
)
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.contract_execution_audit_step import _contains
from novel_forge.pipeline.token_budget import route_max_output_budget
from novel_forge.story_kernel.entity_projection import (
    entity_lookup_from_kernel,
    entity_registry_from_kernel,
)
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    StoryKernelStructuredWarning,
    TimelineAnchor,
)
from novel_forge.story_kernel.state_writer import StoryKernelStateWriter
from novel_forge.story_kernel.store import StoryKernelStore


@dataclass
class CandidateStateDeltaExtractionInput:
    chapter_number: int
    chapter_text: str
    chapter_contract: dict[str, Any]
    current_state: dict[str, Any]
    entity_registry: dict[str, Any]


@dataclass
class StateDeltaAdjudicationInput:
    chapter_number: int
    candidate: CandidateStateDelta
    chapter_contract: dict[str, Any]
    current_state: dict[str, Any]
    evidence_window: str
    chapter_text: str = ""


@dataclass
class FinalStateAdjudicationInput:
    chapter_number: int
    candidates: list[CandidateStateDelta]
    decisions: list[AdjudicationDecision]
    chapter_contract: dict[str, Any]
    current_state: dict[str, Any]
    contract_coverage: ContractCoverageReport | None = None
    pre_block_recheck: bool = False


@dataclass
class RepairAdjudicatedIssueInput:
    chapter_number: int
    chapter_text: str
    final_adjudication: FinalStateAdjudication
    decisions: list[AdjudicationDecision]
    candidates: list[CandidateStateDelta]
    chapter_contract: dict[str, Any]
    current_state: dict[str, Any]
    memory_repair_hints: dict[str, Any] | None = None


def _split_paragraphs(text: str) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _repair_candidate_ids(final_adjudication: FinalStateAdjudication) -> set[str]:
    return {
        str(candidate_id or "").strip()
        for candidate_id in list(final_adjudication.repair_candidate_ids or [])
        if str(candidate_id or "").strip()
    }


def _repair_anchor_quotes(input_data: RepairAdjudicatedIssueInput) -> list[str]:
    repair_ids = _repair_candidate_ids(input_data.final_adjudication)
    quotes: list[str] = []
    for decision in list(input_data.decisions or []):
        if repair_ids and str(decision.candidate_id or "").strip() not in repair_ids:
            continue
        quotes.extend(str(item or "").strip() for item in list(decision.evidence_quotes or []))
    for candidate in list(input_data.candidates or []):
        if repair_ids and str(candidate.candidate_id or "").strip() not in repair_ids:
            continue
        for evidence in list(candidate.evidence or []):
            quotes.append(str(getattr(evidence, "quote", "") or "").strip())
            quotes.append(str(getattr(evidence, "context", "") or "").strip())

    cleaned: list[str] = []
    seen: set[str] = set()
    for quote in sorted(quotes, key=len, reverse=True):
        text = quote.strip()
        if len(text) < 16 or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _repair_context_windows(
    input_data: RepairAdjudicatedIssueInput,
    *,
    radius: int = 2,
    max_chars: int = 7000,
) -> str:
    """Return compact repair windows around adjudication evidence anchors.

    The repair model does not need the whole chapter for a local adjudicated issue.
    Keeping the prompt anchored to nearby paragraphs prevents long chapters from
    forcing a full-chapter response that hits provider output limits.
    """
    original_text = str(input_data.chapter_text or "").strip()
    paragraphs = _split_paragraphs(original_text)
    if not paragraphs:
        return original_text

    selected: set[int] = set()
    for quote in _repair_anchor_quotes(input_data):
        compact_quote = _compact_text(quote)
        if not compact_quote:
            continue
        for index, paragraph in enumerate(paragraphs):
            if compact_quote in _compact_text(paragraph):
                start = max(0, index - max(0, radius))
                end = min(len(paragraphs), index + max(0, radius) + 1)
                selected.update(range(start, end))
                break

    if not selected:
        return original_text

    groups: list[tuple[int, int]] = []
    ordered = sorted(selected)
    start = prev = ordered[0]
    for index in ordered[1:]:
        if index == prev + 1:
            prev = index
            continue
        groups.append((start, prev))
        start = prev = index
    groups.append((start, prev))

    parts: list[str] = []
    used_chars = 0
    for start, end in groups:
        window = "\n\n".join(paragraphs[start : end + 1])
        header = f"【窗口：原文段落 {start + 1}-{end + 1}】"
        block = f"{header}\n{window}"
        if parts and used_chars + len(block) > max_chars:
            break
        parts.append(block)
        used_chars += len(block)
    return "\n\n---\n\n".join(parts) if parts else original_text


def _is_partial_repair_response(original_text: str, repaired_text: str) -> bool:
    original_count = count_chapter_words(original_text)
    repaired_count = count_chapter_words(repaired_text)
    if original_count < 800:
        return False
    return repaired_count < max(500, int(original_count * 0.55))


def _replace_best_matching_window(original_text: str, repaired_text: str) -> tuple[str, str]:
    original_paragraphs = _split_paragraphs(original_text)
    repaired_paragraphs = _split_paragraphs(repaired_text)
    if not original_paragraphs or not repaired_paragraphs:
        return original_text, "no_paragraph_window"

    max_window = min(4, max(1, len(repaired_paragraphs) + 1), len(original_paragraphs))
    repaired_compact = _compact_text(repaired_text)
    best: tuple[float, int, int, str] = (0.0, 0, 0, "")
    for window_size in range(1, max_window + 1):
        for start in range(0, len(original_paragraphs) - window_size + 1):
            window = "\n\n".join(original_paragraphs[start : start + window_size])
            ratio = difflib.SequenceMatcher(
                None,
                _compact_text(window),
                repaired_compact,
            ).ratio()
            if ratio > best[0]:
                best = (ratio, start, window_size, window)

    ratio, _start, _window_size, window = best
    if ratio < 0.58 or not window:
        return original_text, f"low_similarity:{ratio:.3f}"
    return original_text.replace(window, repaired_text, 1), f"paragraph_window:{ratio:.3f}"


def _evidence_quote_replacement_is_safe(
    original_text: str,
    quote: str,
    repaired_text: str,
) -> tuple[bool, str]:
    """Return whether a partial repair may replace a matched evidence quote."""

    original_paragraphs = _split_paragraphs(original_text)
    quote_text = str(quote or "").strip()
    if not quote_text:
        return False, "empty_quote"

    quote_paragraph = ""
    for paragraph in original_paragraphs:
        if quote_text in paragraph:
            quote_paragraph = paragraph
            break
    if not quote_paragraph:
        return False, "quote_not_found"

    repaired_paragraphs = _split_paragraphs(repaired_text)
    quote_is_full_paragraph = quote_text == quote_paragraph.strip()
    quote_words = max(1, count_chapter_words(quote_text))
    repaired_words = count_chapter_words(repaired_text)

    if not quote_is_full_paragraph:
        if len(repaired_paragraphs) > 1:
            return False, "multi_paragraph_replacement_for_inline_quote"
        if repaired_words > max(120, quote_words * 3):
            return False, "oversized_inline_quote_replacement"

    if quote_words < 40 and repaired_words > max(160, quote_words * 4):
        return False, "oversized_short_quote_replacement"

    return True, "safe"


def _coerce_adjudicated_repair_response(
    input_data: RepairAdjudicatedIssueInput,
    repaired_text: str,
) -> tuple[str, str]:
    """Coerce a state-repair response into safe full-chapter text.

    Models sometimes return only the locally repaired span despite the prompt asking
    for the full chapter.  Treat those responses as patches: merge them into the
    original chapter when a reliable anchor is available, otherwise keep the
    original text so a local fragment can never overwrite the chapter.
    """
    original_text = str(input_data.chapter_text or "")
    candidate_text = str(repaired_text or "").strip()
    if not _is_partial_repair_response(original_text, candidate_text):
        return candidate_text, "full_response"

    if candidate_text and candidate_text in original_text:
        return original_text, "partial_already_present"

    unsafe_quote_reasons: list[str] = []
    for quote in _repair_anchor_quotes(input_data):
        if quote in original_text:
            is_safe, reason = _evidence_quote_replacement_is_safe(
                original_text,
                quote,
                candidate_text,
            )
            if not is_safe:
                unsafe_quote_reasons.append(reason)
                continue
            merged = original_text.replace(quote, candidate_text, 1)
            if count_chapter_words(merged) >= max(
                500, int(count_chapter_words(original_text) * 0.8)
            ):
                return merged, "evidence_quote"
    if unsafe_quote_reasons:
        return original_text, f"partial_unmerged:{unsafe_quote_reasons[0]}"

    merged, action = _replace_best_matching_window(original_text, candidate_text)
    if merged != original_text:
        return merged, action
    return original_text, f"partial_unmerged:{action}"


class CandidateStateDeltaExtractionStep(
    PipelineStep[CandidateStateDeltaExtractionInput, list[CandidateStateDelta]]
):
    """Extract candidate state deltas from chapter text."""

    _DEFAULT_CANDIDATE_LIMIT = 8
    _DEFAULT_EVIDENCE_LIMIT = 2

    @property
    def step_name(self) -> str:
        return "candidate_state_deltas"

    async def _execute(
        self,
        input_data: CandidateStateDeltaExtractionInput,
    ) -> list[CandidateStateDelta]:
        contract_targets = compile_contract_targets(
            input_data.chapter_contract,
            chapter_number=input_data.chapter_number,
            entity_registry=input_data.entity_registry,
        )
        candidate_limit = _candidate_extraction_limit(self.settings, contract_targets)
        evidence_limit = _candidate_evidence_limit(self.settings)
        raw_data = await self._call_with_retry(
            TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
            {
                "chapter_number": input_data.chapter_number,
                "chapter_text": input_data.chapter_text,
                "chapter_contract": _json_text(
                    _scope_chapter_contract(input_data.chapter_contract)
                ),
                "contract_targets": _json_text(_contract_target_payloads(contract_targets)),
                "current_state": _json_text(_scope_current_state(input_data.current_state)),
                "entity_registry": _json_text(
                    _scope_entity_registry(
                        input_data.entity_registry,
                        chapter_text=input_data.chapter_text,
                        current_state=input_data.current_state,
                        chapter_contract=input_data.chapter_contract,
                    )
                ),
                "candidate_limit": candidate_limit,
                "evidence_limit": evidence_limit,
            },
            max_tokens=self._dynamic_max_tokens(
                TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
                max(4000, candidate_limit * 450),
                prompt_overhead=5000,
                min_tokens=4096,
            ),
            temperature=float(
                getattr(self.settings, "temp_extract_candidate_state_deltas", 0.2) or 0.2
            ),
            required_keys=("candidates",),
            max_retries=2,
        )
        if not isinstance(raw_data, dict):
            raise ValueError("EXTRACT_CANDIDATE_STATE_DELTAS must return a JSON object")
        candidates = normalize_candidate_state_deltas(
            raw_data.get("candidates", []),
            chapter_number=input_data.chapter_number,
            chapter_text=input_data.chapter_text,
            contract_targets=contract_targets,
            evidence_limit=evidence_limit,
        )
        candidates = _ensure_contract_evidence_candidates(
            candidates,
            chapter_number=input_data.chapter_number,
            chapter_text=input_data.chapter_text,
            chapter_contract=input_data.chapter_contract,
            entity_registry=input_data.entity_registry,
        )
        return _normalize_candidate_contract_targets(candidates, contract_targets=contract_targets)


class StateDeltaAdjudicationStep(PipelineStep[StateDeltaAdjudicationInput, AdjudicationDecision]):
    """Ask the LLM to adjudicate one candidate state delta."""

    @property
    def step_name(self) -> str:
        return "state_delta_adjudication"

    async def _execute(self, input_data: StateDeltaAdjudicationInput) -> AdjudicationDecision:
        contract_targets = compile_contract_targets(
            input_data.chapter_contract,
            chapter_number=input_data.chapter_number,
        )
        raw_data = await self._call_with_retry(
            TaskType.ADJUDICATE_STATE_DELTA,
            {
                "candidate_id": input_data.candidate.candidate_id,
                "candidate": _json_text(
                    _scope_candidate(
                        input_data.candidate,
                        evidence_limit=_candidate_evidence_limit(self.settings),
                    )
                ),
                "chapter_contract": _json_text(
                    _scope_chapter_contract(input_data.chapter_contract)
                ),
                "contract_targets": _json_text(_contract_target_payloads(contract_targets)),
                "current_state": _json_text(_scope_current_state(input_data.current_state)),
                "evidence_window": input_data.evidence_window,
            },
            max_tokens=route_max_output_budget(
                self._router,
                TaskType.ADJUDICATE_STATE_DELTA,
                min_tokens=2048,
            ),
            temperature=float(getattr(self.settings, "temp_adjudicate_state_delta", 0.1) or 0.1),
            required_keys=("candidate_id", "verdict", "severity", "rationale"),
            max_retries=2,
        )
        if not isinstance(raw_data, dict):
            raise ValueError("ADJUDICATE_STATE_DELTA must return a JSON object")
        payload = dict(raw_data)
        payload.setdefault("candidate_id", input_data.candidate.candidate_id)
        payload = self._normalize_decision_payload(
            payload,
            fallback_candidate_id=input_data.candidate.candidate_id,
            candidate=input_data.candidate,
            contract_targets=contract_targets,
            evidence_text=input_data.evidence_window or input_data.chapter_text,
        )
        return AdjudicationDecision.model_validate(payload)

    def _normalize_decision_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback_candidate_id: str,
        candidate: CandidateStateDelta,
        contract_targets: list[ContractTargetSpec],
        evidence_text: str,
    ) -> dict[str, Any]:
        """Normalize enum drift before the strict Pydantic boundary."""
        normalized = dict(payload)
        returned_candidate_id = str(normalized.get("candidate_id") or "").strip()
        if returned_candidate_id != fallback_candidate_id:
            self._logger.warning(
                "state_delta_decision_candidate_id_normalized | returned=%s | expected=%s",
                returned_candidate_id,
                fallback_candidate_id,
            )
            normalized["candidate_id"] = fallback_candidate_id
        raw_verdict = str(normalized.get("verdict", "") or "")
        raw_severity = str(normalized.get("severity", "") or "")
        normalized["verdict"] = normalize_adjudication_verdict(raw_verdict)
        normalized["severity"] = normalize_adjudication_severity(raw_severity)
        target_ids = _normalize_target_ids(
            normalized.get("covered_target_ids"),
            contract_targets=contract_targets,
        )
        if not target_ids:
            target_ids = list(getattr(candidate, "covered_target_ids", []) or [])
        normalized["covered_target_ids"] = target_ids
        normalized["coverage_status"] = _normalize_coverage_status(
            normalized.get("coverage_status"),
            verdict=normalized["verdict"],
            has_target_ids=bool(target_ids),
        )
        normalized["issue_kind"] = _normalize_issue_kind(normalized.get("issue_kind"))
        normalized["repair_kind"] = _normalize_repair_kind(
            normalized.get("repair_kind"),
            verdict=normalized["verdict"],
        )
        normalized["evidence_quotes"] = _filter_found_quotes(
            normalized.get("evidence_quotes"),
            evidence_text=evidence_text,
        )
        if raw_verdict != normalized["verdict"] or raw_severity != normalized["severity"]:
            self._logger.warning(
                "state_delta_decision_normalized | candidate=%s | verdict=%s->%s | severity=%s->%s",
                normalized.get("candidate_id", fallback_candidate_id),
                raw_verdict,
                normalized["verdict"],
                raw_severity,
                normalized["severity"],
            )
        return normalized


class FinalStateAdjudicationStep(PipelineStep[FinalStateAdjudicationInput, FinalStateAdjudication]):
    """Ask the LLM to decide final write/repair/pending outcomes."""

    @property
    def step_name(self) -> str:
        return "final_state_adjudication"

    @staticmethod
    def _estimate_final_output_chars(
        *,
        candidates: list[CandidateStateDelta],
        decisions: list[AdjudicationDecision],
        contract_targets: list[ContractTargetSpec],
        contract_coverage: ContractCoverageReport | None,
    ) -> int:
        """Estimate the final JSON size from the shape of adjudication inputs."""
        candidate_count = len(candidates)
        decision_count = len(decisions)
        target_count = len(contract_targets)
        coverage_count = 0
        if contract_coverage is not None:
            target_count = max(target_count, int(contract_coverage.total_required_targets or 0))
            coverage_count = max(
                len(contract_coverage.items),
                len(contract_coverage.uncovered_targets)
                + len(contract_coverage.unaccepted_targets),
            )

        accepted_count = sum(1 for decision in decisions if decision.verdict == "accept")
        pending_count = sum(
            1 for decision in decisions if decision.verdict in {"ambiguous", "defer"}
        )
        repair_count = sum(1 for decision in decisions if decision.verdict == "needs_repair")
        # Every accepted candidate needs a materialized update; this estimate is
        # an output-budget calculation, not a semantic selection boundary.
        state_update_slots = max(1, accepted_count or candidate_count)

        estimated_chars = (
            1800
            + candidate_count * 80
            + decision_count * 60
            + target_count * 45
            + coverage_count * 25
            + state_update_slots * 220
            + repair_count * 220
            + pending_count * 180
        )
        return max(4200, estimated_chars)

    async def _execute(self, input_data: FinalStateAdjudicationInput) -> FinalStateAdjudication:
        contract_targets = compile_contract_targets(
            input_data.chapter_contract,
            chapter_number=input_data.chapter_number,
        )
        use_llm_final_merge = _final_merge_requires_llm(
            input_data.decisions,
            contract_coverage=input_data.contract_coverage,
            pre_block_recheck=input_data.pre_block_recheck,
        )
        if not use_llm_final_merge:
            data = _mechanical_final_merge_payload(
                chapter_number=input_data.chapter_number,
                decisions=input_data.decisions,
            )
            self._on_step_event(
                "final_state_adjudication_mechanical",
                {
                    "chapter": input_data.chapter_number,
                    "reason": "all_candidate_decisions_converged",
                    "candidate_count": len(input_data.candidates),
                    "decision_count": len(input_data.decisions),
                },
            )
        else:
            prompt_candidates, prompt_decisions = _select_final_merge_review_inputs(
                input_data.candidates,
                input_data.decisions,
                contract_coverage=input_data.contract_coverage,
            )
            prompt_targets = _select_final_prompt_contract_targets(
                contract_targets,
                candidates=prompt_candidates,
                decisions=prompt_decisions,
                contract_coverage=input_data.contract_coverage,
            )
            context = _build_final_merge_context(
                chapter_number=input_data.chapter_number,
                candidates=prompt_candidates,
                decisions=prompt_decisions,
                current_state=input_data.current_state,
                contract_targets=prompt_targets,
                contract_coverage=input_data.contract_coverage,
                pre_block_recheck=input_data.pre_block_recheck,
                target_char_limit=_final_target_char_limit(self.settings),
            )
            context, context_compacted = _fit_final_merge_context_budget(
                context,
                max_chars=_final_context_char_budget(self.settings),
            )
            context_chars = _final_merge_context_chars(context)
            self._on_step_event(
                "final_state_prompt_scope",
                {
                    "chapter": input_data.chapter_number,
                    "candidate_count": len(input_data.candidates),
                    "decision_count": len(input_data.decisions),
                    "review_candidate_count": len(prompt_candidates),
                    "review_decision_count": len(prompt_decisions),
                    "all_target_count": len(contract_targets),
                    "prompt_target_count": len(prompt_targets),
                    "context_chars": context_chars,
                    "context_budget_chars": _final_context_char_budget(self.settings),
                    "context_compacted": context_compacted,
                },
            )
            if context_chars > _final_context_char_budget(self.settings):
                # The individual decisions remain authoritative. Refuse to
                # trade their evidence/contract IDs for a giant final prompt;
                # the mechanical merge preserves the existing repair/pending
                # outcome so downstream repair can proceed safely.
                data = _mechanical_final_merge_payload(
                    chapter_number=input_data.chapter_number,
                    decisions=input_data.decisions,
                    context_budget_exceeded=True,
                    coverage_incomplete=bool(
                        input_data.contract_coverage
                        and not input_data.contract_coverage.all_required_covered
                    ),
                )
                self._on_step_event(
                    "final_state_adjudication_mechanical",
                    {
                        "chapter": input_data.chapter_number,
                        "reason": "final_context_budget_exceeded",
                        "context_chars": context_chars,
                        "context_budget_chars": _final_context_char_budget(self.settings),
                    },
                )
            else:
                try:
                    raw_data = await self._call_with_retry(
                        TaskType.ADJUDICATE_FINAL_STATE,
                        context,
                        max_tokens=self._dynamic_max_tokens(
                            TaskType.ADJUDICATE_FINAL_STATE,
                            self._estimate_final_output_chars(
                                candidates=prompt_candidates,
                                decisions=prompt_decisions,
                                contract_targets=prompt_targets,
                                contract_coverage=input_data.contract_coverage,
                            ),
                            prompt_overhead=8000,
                            min_tokens=4096,
                            max_cap=8192,
                        ),
                        temperature=float(
                            getattr(self.settings, "temp_adjudicate_final_state", 0.1) or 0.1
                        ),
                        required_keys=(
                            "verdict",
                            "accepted_candidate_ids",
                            "pending_candidate_ids",
                            "repair_candidate_ids",
                            "should_block_archive",
                            "summary",
                        ),
                        max_retries=2,
                    )
                    if not isinstance(raw_data, dict):
                        raise ValueError("ADJUDICATE_FINAL_STATE must return a JSON object")
                    data = raw_data
                except (asyncio.TimeoutError, TimeoutError) as exc:
                    self._logger.warning(
                        "final_state_adjudication_fallback | chapter=%d | error=%s",
                        input_data.chapter_number, exc,
                    )
                    data = _mechanical_final_merge_payload(
                        chapter_number=input_data.chapter_number,
                        decisions=input_data.decisions,
                        coverage_incomplete=bool(
                            input_data.contract_coverage
                            and not input_data.contract_coverage.all_required_covered
                        ),
                    )
                    self._on_step_event(
                        "final_state_adjudication_mechanical",
                        {
                            "chapter": input_data.chapter_number,
                            "reason": "llm_timeout_fallback",
                            "error": str(exc)[:200],
                        },
                    )
                except Exception as exc:
                    from novel_forge.core.exceptions import ModelGatewayError
                    if isinstance(exc, ModelGatewayError):
                        self._logger.warning(
                            "final_state_adjudication_fallback | chapter=%d | error=%s",
                            input_data.chapter_number, exc,
                        )
                        data = _mechanical_final_merge_payload(
                            chapter_number=input_data.chapter_number,
                            decisions=input_data.decisions,
                            coverage_incomplete=bool(
                                input_data.contract_coverage
                                and not input_data.contract_coverage.all_required_covered
                            ),
                        )
                        self._on_step_event(
                            "final_state_adjudication_mechanical",
                            {
                                "chapter": input_data.chapter_number,
                                "reason": "llm_gateway_error_fallback",
                                "error": str(exc)[:200],
                            },
                        )
                    else:
                        raise
        payload = _reconcile_final_candidate_buckets(
            dict(data),
            decisions=input_data.decisions,
        )
        payload.setdefault("chapter_number", input_data.chapter_number)
        raw_verdict = str(payload.get("verdict", "") or "")
        raw_severity = str(payload.get("severity", "") or "")
        payload = normalize_final_adjudication_payload(payload)
        payload["target_coverage"] = _normalize_target_coverage_matrix(
            payload.get("target_coverage") or payload.get("coverage_matrix"),
            contract_targets=contract_targets,
            decisions=input_data.decisions,
        )
        payload = _filter_final_text_repairs(payload, decisions=input_data.decisions)
        payload["state_updates"] = _normalize_final_state_updates(
            payload.get("state_updates"),
            candidates=input_data.candidates,
            current_state=input_data.current_state,
            accepted_candidate_ids=payload.get("accepted_candidate_ids"),
        )
        if "severity" in payload:
            payload["severity"] = normalize_adjudication_severity(payload.get("severity"))
        normalized_verdict = str(payload.get("verdict", "") or "")
        normalized_severity = str(payload.get("severity", "") or "")
        if (raw_verdict and raw_verdict != normalized_verdict) or (
            raw_severity and raw_severity != normalized_severity
        ):
            self._logger.warning(
                "final_state_adjudication_normalized | verdict=%s->%s severity=%s->%s "
                "accepted=%d rejected=%d pending=%d repair=%d block=%s",
                raw_verdict,
                normalized_verdict,
                raw_severity,
                normalized_severity,
                _len_payload_items(payload.get("accepted_candidate_ids")),
                _len_payload_items(payload.get("rejected_candidate_ids")),
                _len_payload_items(payload.get("pending_candidate_ids")),
                _len_payload_items(payload.get("repair_candidate_ids")),
                bool(payload.get("should_block_archive")),
            )
        return FinalStateAdjudication.model_validate(payload)


class RepairAdjudicatedIssueStep(PipelineStep[RepairAdjudicatedIssueInput, str]):
    """Ask the LLM to repair only issues already selected by final adjudication."""

    @property
    def step_name(self) -> str:
        return "repair_adjudicated_issue"

    async def _execute(self, input_data: RepairAdjudicatedIssueInput) -> str:
        memory_repair_hints = _scope_memory_repair_hints(input_data.memory_repair_hints)
        context = {
            "chapter_number": input_data.chapter_number,
            "adjudicated_issue": _json_text(
                _scope_repair_adjudication_payload(
                    input_data.final_adjudication,
                    input_data.decisions,
                    input_data.candidates,
                )
            ),
            "chapter_contract": _json_text(_scope_chapter_contract(input_data.chapter_contract)),
            "current_state": _json_text(_scope_current_state(input_data.current_state)),
            "memory_repair_hints": _json_text(memory_repair_hints) if memory_repair_hints else "",
            "chapter_text": input_data.chapter_text,
            "repair_context": _repair_context_windows(input_data),
        }
        request = self._builder.build(
            TaskType.REPAIR_ADJUDICATED_ISSUE,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.REPAIR_ADJUDICATED_ISSUE,
                max(2000, len(input_data.chapter_text)),
                prompt_overhead=3500,
                min_tokens=4096,
            ),
            temperature=float(
                getattr(self.settings, "temp_repair_adjudicated_issue", 0.35) or 0.35
            ),
        )
        response = await self._router.route(request)
        repaired_text = extract_text_content(response.content or "").strip()
        if not repaired_text:
            raise ValueError("REPAIR_ADJUDICATED_ISSUE returned empty chapter text")
        repaired_text, repair_action = _coerce_adjudicated_repair_response(
            input_data,
            repaired_text,
        )
        if repair_action != "full_response":
            self._logger.warning(
                "repair_adjudicated_issue_partial_response | chapter=%d | action=%s | "
                "original_words=%d | response_words=%d | final_words=%d",
                input_data.chapter_number,
                repair_action,
                count_chapter_words(input_data.chapter_text),
                count_chapter_words(response.content or ""),
                count_chapter_words(repaired_text),
            )
        return repaired_text


def apply_adjudication_to_kernel(
    kernel: StoryKernel,
    report: NarrativeAdjudicationReport,
) -> StoryKernel:
    """Map accepted adjudication deltas to StoryKernel field updates.

    Returns a new StoryKernel with the accepted deltas applied.
    Does NOT persist — the caller is responsible for saving via StoryKernelStore.

    Mapping rules (delta_type → StoryKernel field):
        event → timeline + chapter_summaries
        relationship → relationships
        knowledge → knowledge_ledger
        item → object_ledger
        promise → promise_ledger
        world_rule → world_rules
        character_state → entities (attributes update)
        alias → entities (aliases update)
        other → notes
    """
    final = report.final_adjudication
    if final.should_block_archive or _llm_requested_repair(final):
        return kernel

    if not report.ledger_entries:
        return kernel

    new_timeline = list(kernel.timeline)
    new_knowledge = list(kernel.knowledge_ledger)
    new_relationships = list(kernel.relationships)
    new_promises = list(kernel.promise_ledger)
    new_entities = list(kernel.entities)
    new_summaries = dict(kernel.chapter_summaries)
    new_notes = kernel.notes
    new_warnings = list(kernel.structured_warnings)
    entity_lookup = entity_lookup_from_kernel(kernel)
    known_entity_ids = {entity.entity_id for entity in new_entities}

    chapter_number = report.chapter_number

    for entry in report.ledger_entries:
        delta_type = str(getattr(entry, "delta_type", "") or "").strip()
        state_update = dict(getattr(entry, "state_update", {}) or {})
        summary = str(getattr(entry, "summary", "") or "").strip()
        entity_ids = _resolve_entity_refs(
            _extract_entity_ids_from_entry(entry),
            entity_lookup,
        )

        if delta_type == "event":
            new_timeline.append(
                TimelineAnchor(
                    anchor_id=f"adj_{entry.entry_id}",
                    chapter=chapter_number,
                    event=summary or str(state_update.get("value", "")),
                    characters_involved=entity_ids,
                )
            )
            if summary:
                new_summaries[chapter_number] = summary

        elif delta_type == "relationship":
            pair = _resolve_entity_refs(
                _coerce_ref_list(state_update.get("relationship_pair")) or entity_ids,
                entity_lookup,
            )
            if (
                isinstance(pair, list)
                and len(pair) >= 2
                and pair[0] in known_entity_ids
                and pair[1] in known_entity_ids
            ):
                rel_type = Relationship._normalize_relation_type(
                    state_update.get("value", "acquaintance")
                )
                new_relationships.append(
                    Relationship(
                        relationship_id=f"adj_{entry.entry_id}",
                        source_entity_id=str(pair[0]),
                        target_entity_id=str(pair[1]),
                        relation_type=rel_type,
                        label=summary,
                        established_chapter=chapter_number,
                        last_shift_chapter=chapter_number,
                        shift_summary=summary,
                    )
                )
            else:
                _append_structured_warning(
                    new_warnings,
                    warning_type="missing_relationship_entities",
                    source="state_adjudication",
                    chapter_number=chapter_number,
                    details={
                        "entry_id": entry.entry_id,
                        "raw_refs": _coerce_ref_list(state_update.get("relationship_pair"))
                        or _extract_entity_ids_from_entry(entry),
                    },
                )

        elif delta_type == "knowledge":
            fact_text = str(state_update.get("value", "") or summary)
            knower_id = entity_ids[0] if entity_ids else ""
            knowledge_type = normalize_knowledge_type(
                state_update.get("knowledge_type") or state_update.get("type")
            )
            if fact_text and knower_id:
                new_knowledge.append(
                    KnowledgeLedger(
                        entry_id=f"adj_{entry.entry_id}",
                        entity_id=knower_id,
                        fact=fact_text,
                        knowledge_type=knowledge_type,
                        source_chapter=chapter_number,
                    )
                )
            elif fact_text:
                _append_structured_warning(
                    new_warnings,
                    warning_type="missing_knowledge_entity",
                    source="state_adjudication",
                    chapter_number=chapter_number,
                    details={
                        "entry_id": entry.entry_id,
                        "summary": summary,
                        "raw_refs": _extract_entity_ids_from_entry(entry),
                    },
                )

        elif delta_type == "promise":
            desc = summary or str(state_update.get("value", ""))
            if desc:
                new_promises.append(
                    PromiseLedger(
                        entry_id=f"adj_{entry.entry_id}",
                        description=desc,
                        promise_type="foreshadow",
                        planted_chapter=chapter_number,
                        status="planted",
                    )
                )

        elif delta_type == "item":
            item_note = f"[第{chapter_number}章物品变化] {summary}"
            new_notes = f"{new_notes}\n{item_note}".strip() if new_notes else item_note

        elif delta_type == "world_rule":
            rule_note = f"[第{chapter_number}章世界规则] {summary}"
            new_notes = f"{new_notes}\n{rule_note}".strip() if new_notes else rule_note

        elif delta_type == "character_state":
            _warn_missing_entities(
                new_warnings,
                entry=entry,
                entity_ids=entity_ids,
                entities=new_entities,
                warning_type="missing_character_state_entity",
                chapter_number=chapter_number,
            )
            _apply_character_state_to_entities(
                new_entities, entity_ids, state_update, chapter_number
            )

        elif delta_type == "alias":
            _warn_missing_entities(
                new_warnings,
                entry=entry,
                entity_ids=entity_ids,
                entities=new_entities,
                warning_type="missing_alias_entity",
                chapter_number=chapter_number,
            )
            _apply_alias_to_entities(new_entities, entity_ids, state_update, chapter_number)

        elif delta_type == "other":
            other_note = f"[第{chapter_number}章状态变化] {summary}"
            new_notes = f"{new_notes}\n{other_note}".strip() if new_notes else other_note

    return kernel.model_copy(
        update={
            "current_chapter": max(kernel.current_chapter, chapter_number),
            "entities": new_entities,
            "relationships": new_relationships,
            "timeline": new_timeline,
            "knowledge_ledger": new_knowledge,
            "promise_ledger": new_promises,
            "chapter_summaries": new_summaries,
            "notes": new_notes,
            "structured_warnings": new_warnings[-200:],
        }
    )


def _llm_requested_repair(final: FinalStateAdjudication) -> bool:
    return (
        final.verdict == "needs_repair"
        or bool(final.repair_candidate_ids)
        or bool(final.repair_issues)
    )


def _extract_entity_ids_from_entry(entry: StateLedgerEntry) -> list[str]:
    state_update = dict(getattr(entry, "state_update", {}) or {})
    entity_ids = state_update.get("entity_ids", [])
    if not entity_ids:
        entity_ids = getattr(entry, "entity_ids", [])
    if not entity_ids:
        entity_ids = state_update.get("present_characters", [])
    if not entity_ids:
        entity_ids = state_update.get("entity_id", "")
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    return [str(eid) for eid in entity_ids if str(eid).strip()]


def _coerce_ref_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item or "").strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _resolve_entity_refs(refs: list[str], lookup: dict[str, Entity]) -> list[str]:
    resolved: list[str] = []
    for ref in refs:
        text = str(ref or "").strip()
        if not text:
            continue
        entity = lookup.get(text)
        entity_id = entity.entity_id if entity is not None else text
        if entity_id not in resolved:
            resolved.append(entity_id)
    return resolved


def _warn_missing_entities(
    warnings: list[StoryKernelStructuredWarning],
    *,
    entry: StateLedgerEntry,
    entity_ids: list[str],
    entities: list[Entity],
    warning_type: str,
    chapter_number: int,
) -> None:
    known_ids = {entity.entity_id for entity in entities}
    missing = [entity_id for entity_id in entity_ids if entity_id not in known_ids]
    for entity_id in missing:
        _append_structured_warning(
            warnings,
            warning_type=warning_type,
            source="state_adjudication",
            entity_id=entity_id,
            chapter_number=chapter_number,
            details={
                "entry_id": entry.entry_id,
                "delta_type": entry.delta_type,
                "summary": entry.summary,
            },
        )


def _append_structured_warning(
    warnings: list[StoryKernelStructuredWarning],
    *,
    warning_type: str,
    source: str,
    chapter_number: int,
    details: dict[str, Any],
    entity_id: str = "",
) -> None:
    warnings.append(
        StoryKernelStructuredWarning(
            warning_type=warning_type,
            source=source,
            entity_id=entity_id,
            chapter_number=chapter_number,
            details=details,
        )
    )


def _apply_character_state_to_entities(
    entities: list[Entity],
    entity_ids: list[str],
    state_update: dict[str, Any],
    chapter_number: int,
) -> None:
    """Apply state updates by mutating the supplied entity list in place."""
    for entity in entities:
        if entity.entity_id not in entity_ids:
            continue
        attrs = dict(entity.attributes or {})
        if "value" in state_update:
            attrs["last_state_change"] = str(state_update["value"])
        if "summary" in state_update:
            attrs["last_state_summary"] = str(state_update["summary"])
        attrs["last_state_chapter"] = chapter_number
        entity.attributes = attrs
        entity.last_seen_chapter = max(entity.last_seen_chapter, chapter_number)


def _apply_alias_to_entities(
    entities: list[Entity],
    entity_ids: list[str],
    state_update: dict[str, Any],
    chapter_number: int,
) -> None:
    """Apply alias updates by mutating the supplied entity list in place."""
    alias = str(state_update.get("value", "") or "").strip()
    if not alias:
        return
    for entity in entities:
        if entity.entity_id not in entity_ids:
            continue
        if alias and alias not in entity.aliases:
            entity.aliases.append(alias)
        entity.last_seen_chapter = max(entity.last_seen_chapter, chapter_number)


async def run_narrative_state_adjudication(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    trace: Any,
    project_root: Any,
    chapter_number: int,
    chapter_text: str,
    chapter_contract: dict[str, Any],
    current_state: dict[str, Any],
    known_characters: list[str],
    on_step: Any,
    report_path: Any,
    persist_ledger: bool = True,
    kernel_store: StoryKernelStore | None = None,
    project_id: str = "",
) -> NarrativeAdjudicationReport:
    """Run extraction + adjudication + final LLM merge, then persist ledger/report."""
    store = NarrativeStateStore(project_root)
    known_characters = _filter_known_character_names(
        known_characters,
        current_state=current_state,
    )
    if kernel_store is not None and project_id:
        writer = StoryKernelStateWriter(kernel_store)
        kernel = await writer.upsert_entities_from_known_names(
            project_id=project_id,
            names=known_characters,
            entity_type="character",
            source="known_characters",
            chapter_number=chapter_number,
            step_names="state_adjudication",
        )
        registry_payload = entity_registry_from_kernel(kernel).model_dump(mode="json")
    else:
        registry = store.ensure_character_entities(known_characters)
        registry_payload = registry.model_dump(mode="json")

    extract_step = CandidateStateDeltaExtractionStep(
        router,
        builder,
        settings=settings,
        trace=trace,
    )
    on_step("candidate_state_deltas", {"chapter": chapter_number, "status": "starting"})
    candidates = await extract_step.run(
        CandidateStateDeltaExtractionInput(
            chapter_number=chapter_number,
            chapter_text=chapter_text,
            chapter_contract=chapter_contract,
            current_state=current_state,
            entity_registry=registry_payload,
        )
    )
    omitted_candidates: list[CandidateStateDelta] = []
    max_candidates = int(getattr(settings, "narrative_state_candidate_max_count", 0) or 0)
    contract_targets = compile_contract_targets(
        chapter_contract,
        chapter_number=chapter_number,
        entity_registry=registry_payload,
    )
    candidates, omitted_candidates = _select_candidates_for_adjudication(
        candidates,
        max_candidates=max_candidates,
        contract_targets=contract_targets,
    )
    required_target_ids = _required_target_ids(contract_targets)
    selected_required_target_ids = {
        target_id
        for candidate in candidates
        for target_id in _candidate_required_target_ids(
            candidate,
            contract_targets=contract_targets,
        )
    }
    omitted_required_candidate_count = sum(
        1
        for candidate in omitted_candidates
        if _candidate_required_target_ids(candidate, contract_targets=contract_targets)
    )
    on_step(
        "candidate_state_deltas",
        {
            "chapter": chapter_number,
            "count": len(candidates),
            "omitted_count": len(omitted_candidates),
            "archive_required_target_count": len(required_target_ids),
            "selected_archive_required_target_count": len(selected_required_target_ids),
            "omitted_archive_required_candidate_count": omitted_required_candidate_count,
            "status": "done",
        },
    )

    adjudication_step = StateDeltaAdjudicationStep(
        router,
        builder,
        settings=settings,
        trace=trace,
    )
    decisions = await _adjudicate_candidates(
        adjudication_step,
        candidates=candidates,
        chapter_contract=chapter_contract,
        current_state=current_state,
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        on_step=on_step,
    )
    candidates = _apply_adjudicated_target_mappings(
        candidates,
        decisions=decisions,
        contract_targets=contract_targets,
    )
    contract_coverage = build_contract_coverage_report(
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        candidates=candidates,
        decisions=decisions,
        settings=settings,
    )
    on_step(
        "contract_coverage_report",
        {
            "chapter": chapter_number,
            "total_required_targets": contract_coverage.total_required_targets,
            "covered_count": contract_coverage.covered_count,
            "evidence_found_count": contract_coverage.evidence_found_count,
            "all_required_covered": contract_coverage.all_required_covered,
            "uncovered_count": len(contract_coverage.uncovered_targets),
            "unaccepted_count": len(contract_coverage.unaccepted_targets),
        },
    )

    final_step = FinalStateAdjudicationStep(
        router,
        builder,
        settings=settings,
        trace=trace,
    )
    on_step("final_state_adjudication", {"chapter": chapter_number, "status": "starting"})
    final_adjudication = await final_step.run(
        FinalStateAdjudicationInput(
            chapter_number=chapter_number,
            candidates=candidates,
            decisions=decisions,
            chapter_contract=chapter_contract,
            current_state=current_state,
            contract_coverage=contract_coverage,
        )
    )
    (
        candidates,
        omitted_candidates,
        decisions,
        contract_coverage,
        final_adjudication,
    ) = await _rescue_omitted_contract_candidates(
        adjudication_step,
        final_step,
        candidates=candidates,
        omitted_candidates=omitted_candidates,
        decisions=decisions,
        final_adjudication=final_adjudication,
        contract_coverage=contract_coverage,
        contract_targets=contract_targets,
        chapter_contract=chapter_contract,
        current_state=current_state,
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        on_step=on_step,
        settings=settings,
    )
    if _should_run_pre_block_recheck(final_adjudication, settings):
        on_step(
            "state_adjudication_pre_block_recheck",
            {
                "chapter": chapter_number,
                "status": "starting",
                "original_verdict": final_adjudication.verdict,
                "original_should_block_archive": final_adjudication.should_block_archive,
            },
        )
        rechecked_final = await final_step.run(
            FinalStateAdjudicationInput(
                chapter_number=chapter_number,
                candidates=candidates,
                decisions=decisions,
                chapter_contract=chapter_contract,
                current_state=current_state,
                contract_coverage=contract_coverage,
                pre_block_recheck=True,
            )
        )
        on_step(
            "state_adjudication_pre_block_recheck",
            {
                "chapter": chapter_number,
                "status": "done",
                "original_should_block_archive": final_adjudication.should_block_archive,
                "rechecked_verdict": rechecked_final.verdict,
                "rechecked_should_block_archive": rechecked_final.should_block_archive,
            },
        )
        final_adjudication = rechecked_final
    contract_coverage = build_contract_coverage_report(
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        candidates=candidates,
        decisions=decisions,
        final_adjudication=final_adjudication,
        settings=settings,
    )
    on_step(
        "final_state_adjudication",
        {
            "chapter": chapter_number,
            "verdict": final_adjudication.verdict,
            "should_block_archive": final_adjudication.should_block_archive,
        },
    )

    ledger_entries = []
    if _final_allows_state_write(final_adjudication):
        ledger_entries = store.build_ledger_entries(
            chapter_number=chapter_number,
            candidates=candidates,
            decisions=decisions,
            final_adjudication=final_adjudication,
        )
        if persist_ledger:
            store.append_entries(ledger_entries)
            on_step(
                "state_ledger_written",
                {"chapter": chapter_number, "entries": len(ledger_entries)},
            )
        else:
            on_step(
                "state_ledger_prepared",
                {"chapter": chapter_number, "entries": len(ledger_entries)},
            )
    elif persist_ledger:
        on_step(
            "state_ledger_written",
            {"chapter": chapter_number, "entries": 0},
        )

    report = NarrativeAdjudicationReport(
        chapter_number=chapter_number,
        candidates=candidates,
        omitted_candidates=omitted_candidates,
        decisions=decisions,
        final_adjudication=final_adjudication,
        contract_coverage=contract_coverage,
        ledger_entries=ledger_entries,
        source_text_hash=_text_hash(chapter_text),
    )
    _save_report(report_path, report)
    canonical_report_path = store.adjudication_report_path(chapter_number)
    if str(canonical_report_path) != str(report_path):
        _save_report(canonical_report_path, report)
    store.save_report_artifacts(
        report=report,
        report_paths=[report_path, canonical_report_path],
    )

    if kernel_store is not None and project_id:
        await _write_adjudication_to_kernel(
            kernel_store=kernel_store,
            project_id=project_id,
            report=report,
            on_step=on_step,
        )

    return report


async def _write_adjudication_to_kernel(
    *,
    kernel_store: StoryKernelStore,
    project_id: str,
    report: NarrativeAdjudicationReport,
    on_step: Any,
) -> None:
    updated = await StoryKernelStateWriter(kernel_store).apply_adjudication_report(
        project_id=project_id,
        report=report,
    )
    on_step(
        "story_kernel_updated",
        {
            "chapter": report.chapter_number,
            "timeline_entries": len(updated.timeline),
            "knowledge_entries": len(updated.knowledge_ledger),
            "relationship_entries": len(updated.relationships),
            "promise_entries": len(updated.promise_ledger),
            "structured_warnings": len(updated.structured_warnings),
        },
    )


async def persist_adjudicated_state_ledger(
    *,
    project_root: Any,
    report: NarrativeAdjudicationReport,
    kernel_store: StoryKernelStore | None = None,
    project_id: str = "",
) -> list[StateLedgerEntry]:
    if not _final_allows_state_write(report.final_adjudication):
        return []
    entries = list(report.ledger_entries)
    if not entries:
        store_for_build = NarrativeStateStore(project_root)
        entries = store_for_build.build_ledger_entries(
            chapter_number=report.chapter_number,
            candidates=report.candidates,
            decisions=report.decisions,
            final_adjudication=report.final_adjudication,
        )
    if report.source_text_hash:
        entries = [
            entry.model_copy(
                update={
                    "source_text_hash": entry.source_text_hash or report.source_text_hash,
                    "chapter_revision_id": entry.chapter_revision_id or report.source_text_hash,
                }
            )
            for entry in entries
        ]
    store = NarrativeStateStore(project_root)
    store.append_entries(entries)
    store.append_pending_items(
        chapter_number=report.chapter_number,
        candidates=report.candidates,
        decisions=report.decisions,
        final_adjudication=report.final_adjudication,
        omitted_candidates=report.omitted_candidates,
    )

    if kernel_store is not None and project_id:
        await _persist_to_kernel(kernel_store, project_id, report)

    return entries


async def _persist_to_kernel(
    kernel_store: StoryKernelStore,
    project_id: str,
    report: NarrativeAdjudicationReport,
) -> None:
    await StoryKernelStateWriter(kernel_store).apply_adjudication_report(
        project_id=project_id,
        report=report,
        step_names="state_adjudication",
    )


async def _adjudicate_candidates(
    step: StateDeltaAdjudicationStep,
    *,
    candidates: list[CandidateStateDelta],
    chapter_contract: dict[str, Any],
    current_state: dict[str, Any],
    chapter_number: int,
    chapter_text: str,
    on_step: Any,
) -> list[AdjudicationDecision]:
    if not candidates:
        return []

    async def _run(candidate: CandidateStateDelta) -> AdjudicationDecision:
        evidence_window = "\n\n".join(
            span.context or span.quote for span in candidate.evidence if span.context or span.quote
        )
        return await step.run(
            StateDeltaAdjudicationInput(
                chapter_number=chapter_number,
                candidate=candidate,
                chapter_contract=chapter_contract,
                current_state=current_state,
                evidence_window=evidence_window,
                chapter_text=chapter_text,
            )
        )

    on_step(
        "state_delta_adjudication",
        {"chapter": chapter_number, "count": len(candidates), "status": "starting"},
    )
    decisions = await asyncio.gather(*(_run(candidate) for candidate in candidates))
    on_step(
        "state_delta_adjudication",
        {"chapter": chapter_number, "count": len(decisions), "status": "done"},
    )
    return list(decisions)


def _unaccepted_required_target_ids(report: ContractCoverageReport) -> set[str]:
    return {
        str(item.target_id or "").strip()
        for item in list(report.items or [])
        if str(item.target_id or "").strip()
        and item.status
        in {"uncovered", "evidence_found_unaccepted", "evidence_found_unadjudicated", "pending"}
    }


def _rescue_candidates_from_omitted(
    omitted_candidates: list[CandidateStateDelta],
    *,
    target_ids: set[str],
    contract_targets: list[ContractTargetSpec],
) -> list[CandidateStateDelta]:
    if not target_ids:
        return []
    infos = [
        info
        for info in _candidate_selection_info(
            omitted_candidates,
            contract_targets=contract_targets,
        )
        if set(info["required_hits"]) & target_ids
        and _candidate_has_found_evidence(info["candidate"])
    ]
    selected_indexes: set[int] = set()
    covered_targets: set[str] = set()
    for prefer_mechanical in (False, True):
        while True:
            best: dict[str, Any] | None = None
            best_key: tuple[int, int, int] | None = None
            for info in infos:
                index = int(info["index"])
                if index in selected_indexes:
                    continue
                if bool(info["is_mechanical_anchor"]) != prefer_mechanical:
                    continue
                new_targets = (set(info["required_hits"]) & target_ids) - covered_targets
                if not new_targets:
                    continue
                key = (len(new_targets), len(info["required_hits"]), -index)
                if best_key is None or key > best_key:
                    best = info
                    best_key = key
            if best is None:
                break
            selected_indexes.add(int(best["index"]))
            covered_targets.update(set(best["required_hits"]) & target_ids)
    return [
        candidate for index, candidate in enumerate(omitted_candidates) if index in selected_indexes
    ]


async def _rescue_omitted_contract_candidates(
    adjudication_step: StateDeltaAdjudicationStep,
    final_step: FinalStateAdjudicationStep,
    *,
    candidates: list[CandidateStateDelta],
    omitted_candidates: list[CandidateStateDelta],
    decisions: list[AdjudicationDecision],
    final_adjudication: FinalStateAdjudication,
    contract_coverage: ContractCoverageReport,
    contract_targets: list[ContractTargetSpec],
    chapter_contract: dict[str, Any],
    current_state: dict[str, Any],
    chapter_number: int,
    chapter_text: str,
    on_step: Any,
    settings: Any = None,
) -> tuple[
    list[CandidateStateDelta],
    list[CandidateStateDelta],
    list[AdjudicationDecision],
    ContractCoverageReport,
    FinalStateAdjudication,
]:
    if not bool(getattr(final_adjudication, "should_block_archive", False)):
        return candidates, omitted_candidates, decisions, contract_coverage, final_adjudication

    target_ids = _unaccepted_required_target_ids(contract_coverage)
    rescue_candidates = _rescue_candidates_from_omitted(
        omitted_candidates,
        target_ids=target_ids,
        contract_targets=contract_targets,
    )
    if not rescue_candidates:
        return candidates, omitted_candidates, decisions, contract_coverage, final_adjudication

    rescue_ids = {
        str(candidate.candidate_id or "").strip()
        for candidate in rescue_candidates
        if str(candidate.candidate_id or "").strip()
    }
    on_step(
        "state_adjudication_rescue",
        {
            "chapter": chapter_number,
            "status": "starting",
            "candidate_count": len(rescue_candidates),
            "target_ids": sorted(target_ids),
            "candidate_ids": sorted(rescue_ids),
        },
    )
    rescue_decisions = await _adjudicate_candidates(
        adjudication_step,
        candidates=rescue_candidates,
        chapter_contract=chapter_contract,
        current_state=current_state,
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        on_step=on_step,
    )
    merged_candidates = [*candidates, *rescue_candidates]
    merged_omitted = [
        candidate
        for candidate in omitted_candidates
        if str(candidate.candidate_id or "").strip() not in rescue_ids
    ]
    merged_decisions = [*decisions, *rescue_decisions]
    merged_candidates = _apply_adjudicated_target_mappings(
        merged_candidates,
        decisions=merged_decisions,
        contract_targets=contract_targets,
    )
    rescued_coverage = build_contract_coverage_report(
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        candidates=merged_candidates,
        decisions=merged_decisions,
        settings=settings,
    )
    rescued_final = await final_step.run(
        FinalStateAdjudicationInput(
            chapter_number=chapter_number,
            candidates=merged_candidates,
            decisions=merged_decisions,
            chapter_contract=chapter_contract,
            current_state=current_state,
            contract_coverage=rescued_coverage,
            pre_block_recheck=True,
        )
    )
    on_step(
        "state_adjudication_rescue",
        {
            "chapter": chapter_number,
            "status": "done",
            "candidate_count": len(rescue_candidates),
            "rescued_should_block_archive": rescued_final.should_block_archive,
            "rescued_verdict": rescued_final.verdict,
        },
    )
    return merged_candidates, merged_omitted, merged_decisions, rescued_coverage, rescued_final


def _filter_known_character_names(
    names: list[str],
    *,
    current_state: dict[str, Any],
) -> list[str]:
    roster_names = {
        str(_as_dict(item).get("name") or "").strip()
        for item in _iter_mapping_or_sequence_values(
            _as_dict(current_state).get("character_roster"),
        )
        if str(_as_dict(item).get("name") or "").strip()
        and not is_system_artifact_name(str(_as_dict(item).get("name") or "").strip())
    }
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in list(names or []):
        name = str(raw_name or "").strip()
        if not name or name in seen or is_system_artifact_name(name):
            continue
        if roster_names and name not in roster_names:
            continue
        seen.add(name)
        result.append(name)
    if result:
        return result
    return sorted(roster_names)


def _final_allows_state_write(final_adjudication: FinalStateAdjudication) -> bool:
    """Execute the LLM merge decision without adding local narrative judgement."""
    llm_requested_repair = (
        final_adjudication.verdict == "needs_repair"
        or bool(final_adjudication.repair_candidate_ids)
        or bool(final_adjudication.repair_issues)
    )
    return not final_adjudication.should_block_archive and not llm_requested_repair


def _should_run_pre_block_recheck(
    final_adjudication: FinalStateAdjudication,
    settings: Any,
) -> bool:
    if not bool(getattr(final_adjudication, "should_block_archive", False)):
        return False
    return bool(getattr(settings, "narrative_state_pre_block_recheck_enabled", True))


def normalize_candidate_state_deltas(
    raw_candidates: Any,
    *,
    chapter_number: int,
    chapter_text: str,
    contract_targets: list[ContractTargetSpec] | None = None,
    evidence_limit: int = CandidateStateDeltaExtractionStep._DEFAULT_EVIDENCE_LIMIT,
) -> list[CandidateStateDelta]:
    """Parse LLM candidates, normalize structure, and mechanically locate quotes.

    Local normalization is intentionally limited to format work: field aliases,
    evidence lookup, and writeable payload wrappers. Semantic acceptance,
    rejection, and blocking remain delegated to the LLM adjudication steps.
    """
    raw_list = raw_candidates if isinstance(raw_candidates, list) else []
    evidence_index = EvidenceIndex(chapter_number, chapter_text)
    results: list[CandidateStateDelta] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_list, start=1):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        candidate_id = str(payload.get("candidate_id") or "").strip()
        if not candidate_id:
            candidate_id = stable_id(
                "cand",
                chapter_number,
                payload.get("delta_type", ""),
                payload.get("summary", ""),
                idx,
            )
        if candidate_id in seen:
            candidate_id = stable_id("cand", chapter_number, candidate_id, idx)
        seen.add(candidate_id)
        payload = _normalize_candidate_aliases(payload)
        raw_delta_type = str(payload.get("delta_type") or "").strip()
        normalized_delta_type = normalize_delta_type(raw_delta_type)
        payload["candidate_id"] = candidate_id
        payload["chapter_number"] = chapter_number
        payload["delta_type"] = normalized_delta_type
        payload["entity_ids"] = _normalize_entity_ids(payload.get("entity_ids"))
        payload["covered_target_ids"] = _normalize_target_ids(
            payload.get("covered_target_ids"),
            contract_targets=contract_targets or [],
        )
        payload["proposed_delta"] = _normalize_proposed_delta(
            payload.get("proposed_delta"),
            raw_delta_type=raw_delta_type,
            normalized_delta_type=normalized_delta_type,
        )
        payload["evidence"] = _normalize_evidence(
            payload.get("evidence", []),
            evidence_index=evidence_index,
            limit=max(1, min(4, int(evidence_limit or 1))),
        )
        payload["proposed_delta"] = _ensure_proposed_delta_payload(
            payload["proposed_delta"],
            payload=payload,
            raw_delta_type=raw_delta_type,
            normalized_delta_type=normalized_delta_type,
        )
        if not payload["evidence"]:
            continue
        if not (
            str(payload.get("summary") or "").strip()
            or payload["proposed_delta"]
            or payload["entity_ids"]
        ):
            continue
        results.append(CandidateStateDelta.model_validate(payload))
    return _normalize_candidate_contract_targets(
        results,
        contract_targets=contract_targets or [],
    )


@dataclass(frozen=True)
class ContractEvidenceField:
    name: str
    prefer_last: bool = False
    delta_type: str = "event"


@dataclass(frozen=True)
class ContractTargetSpec:
    target_id: str
    field_name: str
    target_index: int
    target: str
    required_for_archive: bool = True
    state_path: str = ""
    evidence_candidates: tuple[str, ...] = ()
    delta_type: str = "event"
    entity_ids: tuple[str, ...] = ()
    value: str = ""
    knowledge_type: str = ""
    cognitive_subjects: tuple[str, ...] = ()
    cognitive_object: str = ""
    cognitive_level: str = ""
    action_level: str = ""
    character_knowledge_coverage: tuple[tuple[str, str], ...] = ()


_CONTRACT_EVIDENCE_ANCHOR_EXTRA = "state_adjudication_evidence_anchor"
_CONTRACT_ARCHIVE_REQUIRED_EXTRA = "state_adjudication_archive_required"
_CONTRACT_EVIDENCE_PREFER_LAST_EXTRA = "state_adjudication_prefer_last"
_CONTRACT_ENTRY_STATE_FIELD = "entry_state_requirements"
_COVERAGE_STATUSES = {"covered", "partial", "not_covered", "overreached", "needs_repair"}
_ISSUE_KINDS = {
    "none",
    "evidence",
    "contract_overreach",
    "mapping",
    "format",
    "state_path",
    "other",
}
_REPAIR_KINDS = {"none", "text", "mapping"}
_DEFAULT_CANDIDATE_SOFT_RESERVE = 4
_MAX_DYNAMIC_CANDIDATE_LIMIT = 200


def _chapter_contract_field_extra(field_name: str) -> dict[str, Any]:
    field_info = ChapterContract.model_fields.get(field_name)
    extra = getattr(field_info, "json_schema_extra", None) if field_info is not None else None
    return extra if isinstance(extra, dict) else {}


def compile_contract_targets(
    chapter_contract: dict[str, Any],
    *,
    chapter_number: int,
    entity_registry: dict[str, Any] | None = None,
    include_entry_state: bool = True,
) -> list[ContractTargetSpec]:
    """Compile chapter contract fields into stable target ids and local paths.

    The compiled targets are not a semantic judgement.  They are the fixed
    menu that LLM adjudicators may choose from, while local code verifies only
    ids, paths, and evidence shape.
    """
    targets: list[ContractTargetSpec] = []
    for field in _contract_evidence_fields():
        if field.name == _CONTRACT_ENTRY_STATE_FIELD and not include_entry_state:
            continue
        targets.extend(
            _iter_contract_target_specs(
                chapter_contract,
                field,
                chapter_number=chapter_number,
                entity_registry=entity_registry,
            )
        )
    return targets


def _contract_target_id(field_name: str, chapter_number: int, target_index: int) -> str:
    return f"{field_name}.{chapter_number}.{target_index:02d}"


def _contract_target_state_path(field_name: str, chapter_number: int, target_index: int) -> str:
    return f"contract.{_contract_target_id(field_name, chapter_number, target_index)}"


def _contract_target_required_for_archive(field_name: str) -> bool:
    extra = _chapter_contract_field_extra(field_name)
    if _CONTRACT_ARCHIVE_REQUIRED_EXTRA in extra:
        return bool(extra.get(_CONTRACT_ARCHIVE_REQUIRED_EXTRA))
    return field_name not in {_CONTRACT_ENTRY_STATE_FIELD, "cognitive_constraints"}


def _contract_targets_by_id(
    targets: list[ContractTargetSpec],
) -> dict[str, ContractTargetSpec]:
    return {target.target_id: target for target in targets if target.target_id}


def _contract_target_payloads(targets: list[ContractTargetSpec]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for target in targets:
        payloads.append(
            {
                "target_id": target.target_id,
                "field_name": target.field_name,
                "target_index": target.target_index,
                "target": target.target,
                "required_for_archive": target.required_for_archive,
                "state_path": target.state_path,
                "delta_type": target.delta_type,
            }
        )
    return payloads


def _candidate_extraction_limit(settings: Any, contract_targets: list[ContractTargetSpec]) -> int:
    configured = int(
        getattr(
            settings,
            "narrative_state_candidate_max_count",
            CandidateStateDeltaExtractionStep._DEFAULT_CANDIDATE_LIMIT,
        )
        or CandidateStateDeltaExtractionStep._DEFAULT_CANDIDATE_LIMIT
    )
    required_count = sum(1 for target in contract_targets if target.required_for_archive)
    dynamic_limit = max(configured, required_count + _DEFAULT_CANDIDATE_SOFT_RESERVE)
    return max(1, min(_MAX_DYNAMIC_CANDIDATE_LIMIT, dynamic_limit))


def _ensure_contract_evidence_candidates(
    candidates: list[CandidateStateDelta],
    *,
    chapter_number: int,
    chapter_text: str,
    chapter_contract: dict[str, Any],
    entity_registry: dict[str, Any] | None = None,
) -> list[CandidateStateDelta]:
    """Inject mechanically verified contract evidence anchors.

    This is not narrative judgement: it only adds a candidate when a literal
    contract target, or a quoted phrase inside that target, appears in the
    chapter text.  The LLM still adjudicates the candidate downstream.
    """
    evidence_index = EvidenceIndex(chapter_number, chapter_text)
    existing_quote_keys = {
        _compact_text(getattr(span, "quote", "") or "")
        for candidate in list(candidates or [])
        for span in list(candidate.evidence or [])
        if _compact_text(getattr(span, "quote", "") or "")
    }
    existing_ids = {
        str(getattr(candidate, "candidate_id", "") or "").strip()
        for candidate in list(candidates or [])
        if str(getattr(candidate, "candidate_id", "") or "").strip()
    }
    anchors: list[CandidateStateDelta] = []
    for field in _contract_evidence_fields():
        for target_spec in _iter_contract_target_specs(
            chapter_contract,
            field,
            chapter_number=chapter_number,
            entity_registry=entity_registry,
        ):
            quote = _first_found_contract_quote(
                target_spec.target,
                evidence_index=evidence_index,
                prefer_last=field.prefer_last,
                evidence_candidates=target_spec.evidence_candidates,
            )
            if not quote:
                continue
            quote_key = _compact_text(quote)
            if quote_key in existing_quote_keys:
                continue
            candidate_id = _unique_contract_candidate_id(
                field.name,
                target_spec.target_index,
                existing_ids=existing_ids,
            )
            existing_ids.add(candidate_id)
            existing_quote_keys.add(quote_key)
            span = evidence_index.locate_quote(
                quote,
                prefer_last=field.prefer_last,
            )
            anchors.append(
                CandidateStateDelta(
                    candidate_id=candidate_id,
                    chapter_number=chapter_number,
                    delta_type=target_spec.delta_type,
                    summary=_contract_candidate_summary(target_spec),
                    entity_ids=list(target_spec.entity_ids),
                    covered_target_ids=[target_spec.target_id],
                    proposed_delta={
                        "candidate_id": candidate_id,
                        "source": "chapter_contract",
                        "delta_type": target_spec.delta_type,
                        "scope": "contract_progression",
                        "state_path": target_spec.state_path,
                        "target_id": target_spec.target_id,
                        "covered_target_ids": [target_spec.target_id],
                        "contract_field": field.name,
                        "target": target_spec.target,
                        "required_for_archive": target_spec.required_for_archive,
                        "summary": _contract_candidate_summary(target_spec),
                        "value": target_spec.value or target_spec.target,
                        "entity_ids": list(target_spec.entity_ids),
                        "knowledge_type": target_spec.knowledge_type,
                        "cognitive_subjects": list(target_spec.cognitive_subjects),
                        "cognitive_object": target_spec.cognitive_object,
                        "cognitive_level": target_spec.cognitive_level,
                        "action_level": target_spec.action_level,
                        "character_knowledge_coverage": dict(
                            target_spec.character_knowledge_coverage
                        ),
                        "evidence_anchor": quote,
                    },
                    cognitive_subjects=list(target_spec.cognitive_subjects),
                    cognitive_object=target_spec.cognitive_object,
                    cognitive_level=target_spec.cognitive_level or None,
                    action_level=target_spec.action_level or None,
                    character_knowledge_coverage=dict(
                        target_spec.character_knowledge_coverage
                    ),
                    evidence=[span],
                    extraction_notes="mechanical_contract_evidence_anchor",
                )
            )
    if not anchors:
        return list(candidates or [])
    return [*anchors, *list(candidates or [])]


def build_contract_coverage_report(
    *,
    chapter_number: int,
    chapter_contract: dict[str, Any],
    candidates: list[CandidateStateDelta],
    decisions: list[AdjudicationDecision],
    final_adjudication: FinalStateAdjudication | None = None,
    settings: Any = None,
) -> ContractCoverageReport:
    """Build a mechanical coverage report for executable contract targets."""
    decisions_by_id = {
        str(decision.candidate_id or "").strip(): decision
        for decision in list(decisions or [])
        if str(decision.candidate_id or "").strip()
    }
    targets = [
        target
        for target in compile_contract_targets(
            chapter_contract,
            chapter_number=chapter_number,
            include_entry_state=False,
        )
        if target.required_for_archive
    ]
    final_coverage = _final_coverage_by_target(final_adjudication)
    items: list[ContractCoverageItem] = []
    for target_spec in targets:
        matching_candidates = [
            candidate
            for candidate in list(candidates or [])
            if target_spec.target_id in _candidate_covered_target_ids(candidate)
        ]
        decision_candidate_ids = [
            str(decision.candidate_id or "").strip()
            for decision in list(decisions or [])
            if target_spec.target_id in list(getattr(decision, "covered_target_ids", []) or [])
            and str(decision.candidate_id or "").strip()
        ]
        final_item = final_coverage.get(target_spec.target_id, {})
        final_candidate_ids = _scope_list(
            final_item.get("candidate_ids"),
            limit=12,
            item_limit=100,
        )
        candidate_ids = _unique_ordered(
            [
                *[
                    str(candidate.candidate_id or "").strip()
                    for candidate in matching_candidates
                    if str(candidate.candidate_id or "").strip()
                ],
                *decision_candidate_ids,
                *[str(item or "").strip() for item in final_candidate_ids],
            ]
        )
        evidence_quotes = _unique_ordered(
            str(span.quote or "").strip()
            for candidate in matching_candidates
            for span in list(candidate.evidence or [])
            if str(span.quote or "").strip()
        )
        verdict_buckets = _candidate_verdict_buckets(candidate_ids, decisions_by_id)
        evidence_found = any(
            bool(getattr(span, "found", False))
            for candidate in matching_candidates
            for span in list(candidate.evidence or [])
        )
        status = _coverage_status(
            evidence_found=evidence_found,
            accepted=verdict_buckets["accepted"],
            pending=verdict_buckets["pending"],
            repair=verdict_buckets["repair"],
            rejected=verdict_buckets["rejected"],
            final_status=str(final_item.get("coverage_status") or ""),
            final_candidate_ids=[
                str(item or "").strip() for item in final_candidate_ids if str(item or "").strip()
            ],
        )
        items.append(
            ContractCoverageItem(
                target_id=target_spec.target_id,
                field_name=target_spec.field_name,
                target_index=target_spec.target_index,
                target=target_spec.target,
                status=status,
                evidence_found=evidence_found,
                candidate_ids=candidate_ids,
                accepted_candidate_ids=verdict_buckets["accepted"],
                rejected_candidate_ids=verdict_buckets["rejected"],
                pending_candidate_ids=verdict_buckets["pending"],
                repair_candidate_ids=verdict_buckets["repair"],
                evidence_quotes=evidence_quotes[:8],
            )
        )

    covered_count = sum(1 for item in items if item.status == "accepted_covered")
    evidence_found_count = sum(1 for item in items if item.evidence_found)
    uncovered = [_coverage_target_summary(item) for item in items if item.status == "uncovered"]
    unaccepted = [
        _coverage_target_summary(item)
        for item in items
        if item.status not in {"accepted_covered", "uncovered"}
    ]
    # Apply coverage gap tolerance from settings
    tolerance = float(
        getattr(settings, "narrative_state_coverage_gap_tolerance", 0.0) or 0.0
    )
    if items and tolerance > 0.0:
        gap_ratio = 1.0 - (covered_count / len(items))
        all_covered = gap_ratio <= tolerance
    else:
        all_covered = bool(items) and covered_count == len(items)
    return ContractCoverageReport(
        chapter_number=chapter_number,
        total_required_targets=len(items),
        covered_count=covered_count,
        evidence_found_count=evidence_found_count,
        all_required_covered=all_covered,
        items=items,
        uncovered_targets=uncovered,
        unaccepted_targets=unaccepted,
    )


def _contract_evidence_fields() -> list[ContractEvidenceField]:
    fields: list[ContractEvidenceField] = []
    for name in ChapterContract.model_fields:
        extra = _chapter_contract_field_extra(name)
        if not extra.get(_CONTRACT_EVIDENCE_ANCHOR_EXTRA):
            continue
        fields.append(
            ContractEvidenceField(
                name=name,
                prefer_last=bool(extra.get(_CONTRACT_EVIDENCE_PREFER_LAST_EXTRA, False)),
                delta_type=normalize_delta_type(
                    extra.get("state_adjudication_delta_type", "event")
                ),
            )
        )
    return fields


def _candidate_covers_contract_target(
    candidate: CandidateStateDelta,
    *,
    field_name: str,
    target: str,
    evidence_candidates: tuple[str, ...] = (),
) -> bool:
    proposed = getattr(candidate, "proposed_delta", {}) or {}
    if isinstance(proposed, dict) and proposed.get("source") == "chapter_contract":
        if str(proposed.get("contract_field") or "").strip() == field_name:
            proposed_target = str(proposed.get("target") or "").strip()
            if proposed_target == target:
                return True

    target_quotes = {
        _compact_text(item)
        for item in _contract_quote_candidates(
            target,
            evidence_candidates=evidence_candidates,
        )
    }
    if not target_quotes:
        return False
    for span in list(getattr(candidate, "evidence", []) or []):
        quote = _compact_text(getattr(span, "quote", "") or "")
        if quote and quote in target_quotes:
            return True
    return False


def _candidate_covered_target_ids(candidate: CandidateStateDelta) -> set[str]:
    ids = {
        str(item or "").strip()
        for item in list(getattr(candidate, "covered_target_ids", []) or [])
        if str(item or "").strip()
    }
    proposed = _as_dict(getattr(candidate, "proposed_delta", {}))
    for item in _normalize_target_ids(proposed.get("target_id"), contract_targets=[]):
        if item:
            ids.add(item)
    for item in _normalize_target_ids(proposed.get("covered_target_ids"), contract_targets=[]):
        if item:
            ids.add(item)
    return ids


def _final_coverage_by_target(
    final_adjudication: FinalStateAdjudication | None,
) -> dict[str, dict[str, Any]]:
    if final_adjudication is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in list(getattr(final_adjudication, "target_coverage", []) or []):
        data = _as_dict(item)
        target_id = str(data.get("target_id") or "").strip()
        if target_id.startswith("contract."):
            target_id = target_id[len("contract.") :]
        status = str(data.get("coverage_status") or data.get("status") or "").strip()
        if target_id and status in _COVERAGE_STATUSES:
            result[target_id] = data
    return result


def _candidate_verdict_buckets(
    candidate_ids: list[str],
    decisions_by_id: dict[str, AdjudicationDecision],
) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "accepted": [],
        "rejected": [],
        "pending": [],
        "repair": [],
    }
    for candidate_id in candidate_ids:
        decision = decisions_by_id.get(candidate_id)
        verdict = str(getattr(decision, "verdict", "") or "").strip()
        if verdict == "accept":
            buckets["accepted"].append(candidate_id)
        elif verdict == "reject":
            buckets["rejected"].append(candidate_id)
        elif verdict in {"ambiguous", "defer"}:
            buckets["pending"].append(candidate_id)
        elif verdict == "needs_repair":
            buckets["repair"].append(candidate_id)
    return buckets


def _coverage_status(
    *,
    evidence_found: bool,
    accepted: list[str],
    pending: list[str],
    repair: list[str],
    rejected: list[str],
    final_status: str = "",
    final_candidate_ids: list[str] | None = None,
) -> str:
    final_ids = [item for item in list(final_candidate_ids or []) if item]
    if final_status in {"overreached", "needs_repair"}:
        return "repair_requested"
    if final_status == "covered":
        return "accepted_covered"
    if accepted:
        return "accepted_covered"
    if final_status == "partial":
        return "pending" if final_ids or pending else "evidence_found_unadjudicated"
    if final_status == "not_covered":
        return "uncovered"
    if repair:
        return "repair_requested"
    if pending:
        return "pending"
    if evidence_found:
        return "evidence_found_unaccepted" if rejected else "evidence_found_unadjudicated"
    return "uncovered"


def _coverage_target_summary(item: ContractCoverageItem) -> dict[str, Any]:
    return {
        "target_id": item.target_id,
        "field_name": item.field_name,
        "target_index": item.target_index,
        "target": item.target,
        "status": item.status,
        "candidate_ids": item.candidate_ids,
        "evidence_quotes": item.evidence_quotes[:3],
    }


def _unique_ordered(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _iter_contract_targets(chapter_contract: dict[str, Any], field_name: str) -> list[str]:
    field = ContractEvidenceField(name=field_name)
    return [
        spec.target
        for spec in _iter_contract_target_specs(chapter_contract, field, chapter_number=0)
    ]


def _cognitive_constraint_target_text(value: dict[str, Any]) -> str:
    label = str(
        value.get("label") or value.get("claim_id") or value.get("constraint_id") or ""
    ).strip()
    claim = str(
        value.get("claim") or value.get("claim_text") or value.get("forbidden_claim") or ""
    ).strip()
    obj = str(value.get("cognitive_object") or value.get("object") or "").strip()
    subjects = ", ".join(
        str(item or "").strip()
        for item in list(value.get("cognitive_subjects") or [])
        if str(item or "").strip()
    )
    cognitive_level = str(value.get("cognitive_level") or "").strip()
    action_level = str(value.get("action_level") or "").strip()
    coverage = normalize_character_knowledge_coverage(
        value.get("character_knowledge_coverage"),
        none_as_empty=True,
    )
    coverage_text = ", ".join(f"{character}={level}" for character, level in coverage.items())
    cognitive_chapter = str(value.get("cognitive_chapter") or "").strip()
    public_reveal_chapter = str(value.get("public_reveal_chapter") or "").strip()
    parts = [
        item
        for item in (
            f"claim={label}" if label else "",
            claim,
            f"对象={obj}" if obj else "",
            f"认知主体={subjects}" if subjects else "",
            f"允许认知={cognitive_level}" if cognitive_level else "",
            f"允许行动={action_level}" if action_level else "",
            f"角色知情={coverage_text}" if coverage_text else "",
            f"认知锚点=第{cognitive_chapter}章" if cognitive_chapter else "",
            f"公开锚点=第{public_reveal_chapter}章" if public_reveal_chapter else "",
        )
        if item
    ]
    return "；".join(parts)


def _cognitive_constraint_evidence_candidates(value: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in (
        "claim",
        "claim_text",
        "object",
        "cognitive_object",
        "label",
        "claim_id",
        "constraint_id",
        "forbidden_claim",
    ):
        text = str(value.get(key) or "").strip()
        if text:
            candidates.append(text)
    evidence = value.get("evidence")
    if isinstance(evidence, list):
        candidates.extend(str(item or "").strip() for item in evidence if str(item or "").strip())
    elif isinstance(evidence, str) and evidence.strip():
        candidates.append(evidence.strip())
    return _unique_ordered(candidates)


def _iter_contract_target_specs(
    chapter_contract: dict[str, Any],
    field: ContractEvidenceField,
    *,
    chapter_number: int,
    entity_registry: dict[str, Any] | None = None,
) -> list[ContractTargetSpec]:
    data = _as_dict(chapter_contract)
    raw_values = data.get(field.name)
    values = raw_values if isinstance(raw_values, list) else []
    specs: list[ContractTargetSpec] = []
    entity_lookup = _entity_lookup_from_registry(entity_registry)
    for index, value in enumerate(values, start=1):
        target_id = _contract_target_id(field.name, chapter_number, index)
        state_path = _contract_target_state_path(field.name, chapter_number, index)
        required_for_archive = _contract_target_required_for_archive(field.name)
        if field.name == "knowledge_ops":
            normalized = normalize_knowledge_op(value, entity_lookup=entity_lookup)
            target = knowledge_op_target_text(normalized)
            if not target:
                continue
            specs.append(
                ContractTargetSpec(
                    target_id=target_id,
                    field_name=field.name,
                    target_index=index,
                    target=target,
                    required_for_archive=required_for_archive,
                    state_path=state_path,
                    evidence_candidates=tuple(knowledge_op_evidence_candidates(normalized)),
                    delta_type="knowledge",
                    entity_ids=tuple(knowledge_op_entity_ids(normalized)),
                    value=str(normalized.get("fact") or target),
                    knowledge_type=str(normalized.get("knowledge_type") or "known"),
                )
            )
            continue

        if field.name == "cognitive_constraints":
            normalized = _as_dict(value)
            character_knowledge_coverage = normalize_character_knowledge_coverage(
                normalized.get("character_knowledge_coverage"),
                none_as_empty=True,
            )
            target = _cognitive_constraint_target_text(normalized)
            if not target:
                continue
            specs.append(
                ContractTargetSpec(
                    target_id=target_id,
                    field_name=field.name,
                    target_index=index,
                    target=target,
                    required_for_archive=required_for_archive,
                    state_path=state_path,
                    evidence_candidates=tuple(
                        _cognitive_constraint_evidence_candidates(normalized)
                    ),
                    delta_type="cognitive",
                    entity_ids=tuple(
                        str(item or "").strip()
                        for item in normalized.get("cognitive_subjects", [])
                        if str(item or "").strip()
                    ),
                    value=target,
                    cognitive_subjects=tuple(
                        str(item or "").strip()
                        for item in normalized.get("cognitive_subjects", [])
                        if str(item or "").strip()
                    ),
                    cognitive_object=str(
                        normalized.get("cognitive_object") or normalized.get("object") or ""
                    ).strip(),
                    cognitive_level=str(normalized.get("cognitive_level") or "").strip(),
                    action_level=str(normalized.get("action_level") or "").strip(),
                    character_knowledge_coverage=tuple(
                        character_knowledge_coverage.items()
                    ),
                )
            )
            continue

        text = str(value or "").strip()
        if not text:
            continue
        specs.append(
            ContractTargetSpec(
                target_id=target_id,
                field_name=field.name,
                target_index=index,
                target=text,
                required_for_archive=required_for_archive,
                state_path=state_path,
                delta_type=field.delta_type,
                value=text,
            )
        )
    return specs


def _entity_lookup_from_registry(
    entity_registry: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    if not isinstance(entity_registry, dict):
        return {"id_to_name": {}, "name_to_id": {}}
    entities = entity_registry.get("entities")
    if isinstance(entities, dict):
        values = list(entities.values())
    elif isinstance(entities, list):
        values = entities
    else:
        values = []
    return build_entity_lookup(values)


def _contract_candidate_summary(target: ContractTargetSpec) -> str:
    if target.delta_type == "knowledge":
        return f"契约知识变化已在正文出现：{target.target}"
    return f"契约目标已在正文出现：{target.target}"


def _first_found_contract_quote(
    target: str,
    *,
    evidence_index: EvidenceIndex,
    prefer_last: bool,
    evidence_candidates: tuple[str, ...] = (),
) -> str:
    for quote in _contract_quote_candidates(target, evidence_candidates=evidence_candidates):
        if evidence_index.locate_quote(quote, prefer_last=prefer_last).found:
            return quote
    # Fuzzy fallback: when exact match fails, use clause-level degraded matching
    # from _contains to find a shorter sub-phrase that IS present in the text.
    chapter_text = evidence_index.text
    for quote in _contract_quote_candidates(target, evidence_candidates=evidence_candidates):
        if _contains(chapter_text, quote):
            # Extract the longest matching sub-clause as the evidence quote
            parts = [p for p in re.split(r"[；,，、。/\s]+", quote) if len(p) >= 4]
            for part in sorted(parts, key=len, reverse=True):
                span = evidence_index.locate_quote(part, prefer_last=prefer_last)
                if span.found:
                    return part
            # If no single clause locates exactly, use the first 40 chars as anchor
            short = quote[:40]
            span = evidence_index.locate_quote(short, prefer_last=prefer_last)
            if span.found:
                return short
    return ""


def _contract_quote_candidates(
    target: str,
    *,
    evidence_candidates: tuple[str, ...] = (),
) -> list[str]:
    text = str(target or "").strip()
    raw_candidates = [
        str(item or "").strip() for item in evidence_candidates if str(item or "").strip()
    ]
    if not text and not raw_candidates:
        return []
    quoted = [
        item.strip()
        for source in [text, *raw_candidates]
        for item in re.findall(r"[「『“\"]([^」』”\"]{1,80})[」』”\"]", source)
        if item.strip()
    ]
    candidates = sorted(set(quoted), key=len, reverse=True)
    for item in raw_candidates:
        if len(item) <= 120:
            candidates.append(item)
    if text and len(text) <= 120:
        candidates.append(text)
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _unique_contract_candidate_id(
    field_name: str,
    target_index: int,
    *,
    existing_ids: set[str],
) -> str:
    base = f"contract_{field_name}_{target_index:03d}"
    candidate_id = base
    suffix = 2
    while candidate_id in existing_ids:
        candidate_id = f"{base}_{suffix}"
        suffix += 1
    return candidate_id


def _normalize_target_ids(
    raw_ids: Any,
    *,
    contract_targets: list[ContractTargetSpec],
) -> list[str]:
    valid_ids = set(_contract_targets_by_id(contract_targets))
    if isinstance(raw_ids, str):
        values = re.split(r"[,，;；、\n]+", raw_ids)
    elif isinstance(raw_ids, (list, tuple, set)):
        values = list(raw_ids)
    else:
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        target_id = str(value or "").strip()
        if target_id.startswith("contract."):
            target_id = target_id[len("contract.") :]
        if not target_id or target_id in seen:
            continue
        if valid_ids and target_id not in valid_ids:
            continue
        seen.add(target_id)
        result.append(target_id)
    return result


def _target_ids_from_candidate_payload(
    payload: dict[str, Any],
    *,
    contract_targets: list[ContractTargetSpec],
) -> list[str]:
    proposed = _as_dict(payload.get("proposed_delta"))
    raw_values: list[Any] = [
        payload.get("covered_target_ids"),
        proposed.get("covered_target_ids"),
        proposed.get("target_ids"),
        proposed.get("target_id"),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for target_id in _normalize_target_ids(raw, contract_targets=contract_targets):
            if target_id in seen:
                continue
            seen.add(target_id)
            result.append(target_id)
    return result


def _normalize_candidate_contract_targets(
    candidates: list[CandidateStateDelta],
    *,
    contract_targets: list[ContractTargetSpec],
) -> list[CandidateStateDelta]:
    targets_by_id = _contract_targets_by_id(contract_targets)
    if not targets_by_id:
        return list(candidates or [])
    normalized: list[CandidateStateDelta] = []
    for candidate in list(candidates or []):
        payload = candidate.model_dump(mode="json")
        target_ids = _target_ids_from_candidate_payload(
            payload,
            contract_targets=contract_targets,
        )
        if not target_ids:
            normalized.append(candidate)
            continue
        first_target = targets_by_id[target_ids[0]]
        proposed = dict(candidate.proposed_delta or {})
        proposed.update(
            {
                "scope": "contract_progression",
                "state_path": first_target.state_path,
                "target_id": first_target.target_id,
                "covered_target_ids": target_ids,
                "contract_field": first_target.field_name,
                "target": first_target.target,
                "required_for_archive": first_target.required_for_archive,
            }
        )
        normalized.append(
            candidate.model_copy(
                update={
                    "covered_target_ids": target_ids,
                    "proposed_delta": proposed,
                }
            )
        )
    return normalized


def _candidate_target_ids(
    candidate: CandidateStateDelta,
    *,
    contract_targets: list[ContractTargetSpec],
) -> list[str]:
    targets_by_id = _contract_targets_by_id(contract_targets)
    return [
        target_id
        for target_id in _target_ids_from_candidate_payload(
            candidate.model_dump(mode="json"),
            contract_targets=contract_targets,
        )
        if target_id in targets_by_id
    ]


def _required_target_ids(contract_targets: list[ContractTargetSpec]) -> set[str]:
    return {
        target.target_id
        for target in contract_targets
        if target.required_for_archive and target.target_id
    }


def _candidate_required_target_ids(
    candidate: CandidateStateDelta,
    *,
    contract_targets: list[ContractTargetSpec],
) -> set[str]:
    required_ids = _required_target_ids(contract_targets)
    return {
        target_id
        for target_id in _candidate_target_ids(candidate, contract_targets=contract_targets)
        if target_id in required_ids
    }


def _is_mechanical_contract_anchor(candidate: CandidateStateDelta) -> bool:
    proposed = _as_dict(getattr(candidate, "proposed_delta", {}))
    return (
        str(getattr(candidate, "extraction_notes", "") or "")
        == "mechanical_contract_evidence_anchor"
        or str(proposed.get("source") or "") == "chapter_contract"
    )


def _candidate_has_found_evidence(candidate: CandidateStateDelta) -> bool:
    return any(bool(getattr(span, "found", False)) for span in list(candidate.evidence or []))


def _candidate_selection_info(
    candidates: list[CandidateStateDelta],
    *,
    contract_targets: list[ContractTargetSpec],
) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "candidate": candidate,
            "required_hits": _candidate_required_target_ids(
                candidate,
                contract_targets=contract_targets,
            ),
            "target_count": len(
                _candidate_target_ids(candidate, contract_targets=contract_targets)
            ),
            "evidence_count": len(list(getattr(candidate, "evidence", []) or [])),
            "is_mechanical_anchor": _is_mechanical_contract_anchor(candidate),
        }
        for index, candidate in enumerate(list(candidates or []))
    ]


def _select_candidates_for_adjudication(
    candidates: list[CandidateStateDelta],
    *,
    max_candidates: int,
    contract_targets: list[ContractTargetSpec],
) -> tuple[list[CandidateStateDelta], list[CandidateStateDelta]]:
    """Limit non-hard candidates without dropping archive-required coverage.

    Archive-required candidates are selected before the ordinary candidate cap.
    Mechanical anchors are fallback evidence and only fill required targets not
    already represented by LLM-extracted candidates.
    """
    ordered_candidates = list(candidates or [])
    if max_candidates <= 0:
        return ordered_candidates, []

    infos = _candidate_selection_info(ordered_candidates, contract_targets=contract_targets)
    if len(ordered_candidates) <= max_candidates and not any(
        info["required_hits"] for info in infos
    ):
        return ordered_candidates, []

    selected_indexes: set[int] = set()
    covered_required: set[str] = set()
    hard_infos = [info for info in infos if info["required_hits"]]

    for info in sorted(
        [info for info in hard_infos if not info["is_mechanical_anchor"]],
        key=lambda item: (-len(item["required_hits"]), int(item["index"])),
    ):
        selected_indexes.add(int(info["index"]))
        covered_required.update(set(info["required_hits"]))

    for info in sorted(
        [info for info in hard_infos if info["is_mechanical_anchor"]],
        key=lambda item: (-len(set(item["required_hits"]) - covered_required), int(item["index"])),
    ):
        new_required = set(info["required_hits"]) - covered_required
        if not new_required and len(selected_indexes) >= max_candidates:
            continue
        selected_indexes.add(int(info["index"]))
        covered_required.update(new_required)

    soft_quota = max(0, max_candidates - len(selected_indexes))
    if soft_quota:
        for prefer_mechanical in (False, True):
            for info in infos:
                index = int(info["index"])
                if index in selected_indexes or info["required_hits"]:
                    continue
                if bool(info["is_mechanical_anchor"]) != prefer_mechanical:
                    continue
                selected_indexes.add(index)
                soft_quota -= 1
                if soft_quota <= 0:
                    break
            if soft_quota <= 0:
                break

    selected = [
        candidate for index, candidate in enumerate(ordered_candidates) if index in selected_indexes
    ]
    omitted = [
        candidate
        for index, candidate in enumerate(ordered_candidates)
        if index not in selected_indexes
    ]
    return selected, omitted


def _apply_adjudicated_target_mappings(
    candidates: list[CandidateStateDelta],
    *,
    decisions: list[AdjudicationDecision],
    contract_targets: list[ContractTargetSpec],
) -> list[CandidateStateDelta]:
    """Apply LLM-selected target ids to candidate write payloads.

    This is structural execution only: the LLM has selected the target ids;
    local code derives the canonical state_path from the compiled target menu.
    """
    decision_targets = {
        str(getattr(decision, "candidate_id", "") or "").strip(): list(
            getattr(decision, "covered_target_ids", []) or []
        )
        for decision in list(decisions or [])
    }
    merged: list[CandidateStateDelta] = []
    for candidate in list(candidates or []):
        candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
        if not candidate_id or not decision_targets.get(candidate_id):
            merged.append(candidate)
            continue
        existing = list(getattr(candidate, "covered_target_ids", []) or [])
        target_ids: list[str] = []
        seen: set[str] = set()
        for target_id in [*existing, *decision_targets[candidate_id]]:
            if target_id and target_id not in seen:
                seen.add(target_id)
                target_ids.append(target_id)
        merged.append(candidate.model_copy(update={"covered_target_ids": target_ids}))
    return _normalize_candidate_contract_targets(merged, contract_targets=contract_targets)


def _normalize_coverage_status(
    value: Any, *, verdict: str = "", has_target_ids: bool = False
) -> str:
    status = str(value or "").strip()
    if status in _COVERAGE_STATUSES:
        return status
    if verdict == "accept" and has_target_ids:
        return "covered"
    if verdict == "needs_repair":
        return "needs_repair"
    return ""


def _normalize_issue_kind(value: Any) -> str:
    issue_kind = str(value or "").strip()
    return issue_kind if issue_kind in _ISSUE_KINDS else "none"


def _normalize_repair_kind(value: Any, *, verdict: str = "") -> str:
    repair_kind = str(value or "").strip()
    if repair_kind in _REPAIR_KINDS:
        return repair_kind
    return "text" if verdict == "needs_repair" else "none"


def _filter_found_quotes(raw_quotes: Any, *, evidence_text: str) -> list[str]:
    if isinstance(raw_quotes, str):
        values = [raw_quotes]
    elif isinstance(raw_quotes, (list, tuple, set)):
        values = list(raw_quotes)
    else:
        values = []
    source = str(evidence_text or "")
    compact_source = _compact_text(source)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        quote = str(value or "").strip()
        if not quote or quote in seen:
            continue
        if quote in source or _compact_text(quote) in compact_source:
            seen.add(quote)
            result.append(quote)
    return result


def _final_merge_requires_llm(
    decisions: list[AdjudicationDecision],
    *,
    contract_coverage: ContractCoverageReport | None,
    pre_block_recheck: bool,
) -> bool:
    """Return whether a global LLM merge adds information beyond item verdicts.

    Individual adjudications are the narrative decision boundary. When each is
    decisive and the mechanical contract coverage is complete, a second model
    cannot safely improve that result; it only repeats already-proven context.
    """
    if pre_block_recheck or contract_coverage is None:
        return True
    # Coverage gap alone does NOT require LLM when all individual decisions
    # are already decisive.  The mechanical merge handles coverage_incomplete
    # as a soft warning (non-blocking) so the pipeline can progress.
    # LLM merge is only needed when decisions are ambiguous/defer/repair.
    for decision in list(decisions or []):
        verdict = str(decision.verdict or "ambiguous")
        repair_kind = str(decision.repair_kind or "none")
        if verdict in {"ambiguous", "defer"}:
            return True
        if verdict == "needs_repair" and repair_kind != "mapping":
            return True
    return False


def _mechanical_final_merge_payload(
    *,
    chapter_number: int,
    decisions: list[AdjudicationDecision],
    context_budget_exceeded: bool = False,
    coverage_incomplete: bool = False,
) -> dict[str, Any]:
    """Merge already-adjudicated candidate buckets without new narrative claims."""
    accepted: list[str] = []
    rejected: list[str] = []
    pending: list[str] = []
    repair: list[str] = []
    for decision in list(decisions or []):
        candidate_id = str(decision.candidate_id or "").strip()
        if not candidate_id:
            continue
        verdict = str(decision.verdict or "ambiguous")
        repair_kind = str(decision.repair_kind or "none")
        if verdict == "reject":
            rejected.append(candidate_id)
        elif verdict in {"ambiguous", "defer"}:
            pending.append(candidate_id)
        elif verdict == "needs_repair" and repair_kind == "text":
            repair.append(candidate_id)
        else:
            # ``accept`` and mapping-only repairs are writeable after the
            # per-item decision; mapping repair changes routing, not prose.
            accepted.append(candidate_id)

    should_block = bool(repair)
    if coverage_incomplete and not repair:
        verdict = "accept"
    elif coverage_incomplete:
        verdict = "needs_repair"
    elif repair:
        verdict = "needs_repair"
    elif pending:
        verdict = "defer"
    elif accepted:
        verdict = "accept"
    elif rejected:
        verdict = "reject"
    else:
        verdict = "ambiguous"
    summary = "逐项状态裁判与契约覆盖已收敛，按已裁定结果机械合并。"
    if context_budget_exceeded:
        summary = "最终合并上下文超过预算，保留逐项裁判结果并跳过重复模型归并。"
    if coverage_incomplete:
        summary = (
            "逐项裁判结果已保留；章节契约覆盖存在缺口，"
            "作为警告传递给下游审计，不阻断归档。"
        )
    return {
        "chapter_number": chapter_number,
        "verdict": verdict,
        "severity": "medium" if should_block or pending or coverage_incomplete else "low",
        "confidence": 1.0,
        "accepted_candidate_ids": _unique_ordered(accepted),
        "rejected_candidate_ids": _unique_ordered(rejected),
        "pending_candidate_ids": _unique_ordered(pending),
        "repair_candidate_ids": _unique_ordered(repair),
        "should_block_archive": should_block,
        "summary": summary,
    }


def _coverage_problem_target_ids(report: ContractCoverageReport | None) -> set[str]:
    if report is None:
        return set()
    result: set[str] = set()
    for item in [*list(report.uncovered_targets or []), *list(report.unaccepted_targets or [])]:
        if isinstance(item, dict):
            target_id = str(item.get("target_id") or item.get("id") or "").strip()
        else:
            target_id = str(getattr(item, "target_id", item) or "").strip()
        if target_id:
            result.add(target_id)
    return result


def _select_final_merge_review_inputs(
    candidates: list[CandidateStateDelta],
    decisions: list[AdjudicationDecision],
    *,
    contract_coverage: ContractCoverageReport | None,
) -> tuple[list[CandidateStateDelta], list[AdjudicationDecision]]:
    """Keep only unresolved or coverage-relevant items for exceptional merges."""
    problem_target_ids = _coverage_problem_target_ids(contract_coverage)
    decisions_by_id = {
        str(decision.candidate_id or "").strip(): decision
        for decision in list(decisions or [])
        if str(decision.candidate_id or "").strip()
    }
    selected_ids: set[str] = set()
    for candidate_id, decision in decisions_by_id.items():
        verdict = str(decision.verdict or "ambiguous")
        repair_kind = str(decision.repair_kind or "none")
        if verdict in {"ambiguous", "defer"} or (
            verdict == "needs_repair" and repair_kind != "mapping"
        ):
            selected_ids.add(candidate_id)
        if set(getattr(decision, "covered_target_ids", []) or []) & problem_target_ids:
            selected_ids.add(candidate_id)
    for candidate in list(candidates or []):
        if set(getattr(candidate, "covered_target_ids", []) or []) & problem_target_ids:
            selected_ids.add(str(candidate.candidate_id or "").strip())

    selected_candidates = [
        candidate
        for candidate in list(candidates or [])
        if str(candidate.candidate_id or "").strip() in selected_ids
    ]
    selected_decisions = [
        decision
        for decision in list(decisions or [])
        if str(decision.candidate_id or "").strip() in selected_ids
    ]
    return selected_candidates, selected_decisions


def _final_context_char_budget(settings: Any) -> int:
    value = int(getattr(settings, "narrative_state_final_context_max_chars", 18000) or 18000)
    return max(8000, min(64000, value))


def _final_target_char_limit(settings: Any) -> int:
    value = int(getattr(settings, "narrative_state_final_target_max_chars", 96) or 96)
    return max(40, min(240, value))


def _candidate_evidence_limit(settings: Any) -> int:
    value = int(
        getattr(
            settings,
            "narrative_state_candidate_evidence_limit",
            CandidateStateDeltaExtractionStep._DEFAULT_EVIDENCE_LIMIT,
        )
        or CandidateStateDeltaExtractionStep._DEFAULT_EVIDENCE_LIMIT
    )
    return max(1, min(4, value))


def _build_final_merge_context(
    *,
    chapter_number: int,
    candidates: list[CandidateStateDelta],
    decisions: list[AdjudicationDecision],
    current_state: dict[str, Any],
    contract_targets: list[ContractTargetSpec],
    contract_coverage: ContractCoverageReport | None,
    pre_block_recheck: bool,
    target_char_limit: int,
) -> dict[str, Any]:
    return {
        "chapter_number": chapter_number,
        "candidates": _json_text(_scope_final_candidates(candidates)),
        "decisions": _json_text(_scope_final_decisions(decisions)),
        "chapter_contract": _json_text({"chapter_number": chapter_number}),
        "contract_targets": _json_text(
            _scope_final_contract_targets(contract_targets, target_char_limit=target_char_limit)
        ),
        "current_state": _json_text(_scope_final_current_state(current_state)),
        "contract_coverage_report": _json_text(
            _scope_final_contract_coverage(
                contract_coverage,
                allowed_target_ids={target.target_id for target in contract_targets},
            )
        )
        if contract_coverage is not None
        else "",
        "pre_block_recheck": bool(pre_block_recheck),
    }


def _final_merge_context_chars(context: dict[str, Any]) -> int:
    return sum(len(str(value or "")) for value in context.values())


def _fit_final_merge_context_budget(
    context: dict[str, Any],
    *,
    max_chars: int,
) -> tuple[dict[str, Any], bool]:
    """Strip repeated prose before a final merge; never drop IDs or verdicts."""
    if _final_merge_context_chars(context) <= max_chars:
        return context, False
    compacted = dict(context)
    try:
        targets = json.loads(str(compacted.get("contract_targets") or "[]"))
        compacted["contract_targets"] = _compact_json_text(
            [
                {
                    "target_id": item.get("target_id", ""),
                    "required_for_archive": bool(item.get("required_for_archive", False)),
                    "state_path": item.get("state_path", ""),
                    "delta_type": item.get("delta_type", ""),
                }
                for item in targets
                if isinstance(item, dict)
            ]
        )
        current_state = json.loads(str(compacted.get("current_state") or "{}"))
        compacted["current_state"] = _compact_json_text(
            _compact_final_state_for_budget(current_state)
        )
        coverage = json.loads(str(compacted.get("contract_coverage_report") or "{}"))
        compacted["contract_coverage_report"] = _compact_json_text(
            _compact_final_coverage_for_budget(coverage)
        )
        decisions = json.loads(str(compacted.get("decisions") or "[]"))
        compacted["decisions"] = _compact_json_text(_compact_final_decisions_for_budget(decisions))
        candidates = json.loads(str(compacted.get("candidates") or "[]"))
        compacted["candidates"] = _compact_json_text(
            _compact_final_candidates_for_budget(candidates)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return compacted, True
    return compacted, True


def _compact_final_state_for_budget(state: Any) -> dict[str, Any]:
    data = _as_dict(state)
    slots = _as_dict(data.get("state_update_slots"))
    compact_slots: dict[str, Any] = {}
    for name, records in slots.items():
        if isinstance(records, dict):
            compact_slots[str(name)] = {
                key: value
                for key, value in records.items()
                if key in {"state_path", "target_id", "write_fields", "required_for_archive"}
            }
        elif isinstance(records, list):
            compact_slots[str(name)] = [
                {
                    key: value
                    for key, value in _as_dict(record).items()
                    if key in {"state_path", "target_id", "write_fields", "required_for_archive"}
                }
                for record in records
            ]
    return {
        "chapter_number": data.get("chapter_number", 0),
        "character_roster": [
            {
                "character_id": item.get("character_id", ""),
                "name": item.get("name", ""),
            }
            for item in list(data.get("character_roster") or [])
            if isinstance(item, dict)
        ],
        "state_update_slots": compact_slots,
    }


def _compact_final_coverage_for_budget(coverage: Any) -> dict[str, Any]:
    data = _as_dict(coverage)
    return {
        key: data.get(key)
        for key in (
            "chapter_number",
            "total_required_targets",
            "covered_count",
            "evidence_found_count",
            "all_required_covered",
            "uncovered_targets",
            "unaccepted_targets",
        )
    } | {
        "items": [
            {
                "target_id": item.get("target_id", ""),
                "status": item.get("status", ""),
                "evidence_found": bool(item.get("evidence_found", False)),
            }
            for item in list(data.get("items") or [])
            if isinstance(item, dict)
        ]
    }


def _compact_final_decisions_for_budget(decisions: Any) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in (
                "candidate_id",
                "verdict",
                "severity",
                "covered_target_ids",
                "coverage_status",
                "issue_kind",
                "repair_kind",
                "affected_state_paths",
                "pending_reason",
            )
        }
        for item in list(decisions or [])
        if isinstance(item, dict)
    ]


def _compact_final_candidates_for_budget(candidates: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in list(candidates or []):
        if not isinstance(item, dict):
            continue
        delta = _as_dict(item.get("proposed_delta"))
        result.append(
            {
                "candidate_id": item.get("candidate_id", ""),
                "delta_type": item.get("delta_type", ""),
                "covered_target_ids": item.get("covered_target_ids", []),
                "proposed_delta": {
                    key: delta.get(key)
                    for key in ("scope", "state_path", "target_id", "value")
                    if delta.get(key) not in (None, "", [], {})
                },
            }
        )
    return result


def _reconcile_final_candidate_buckets(
    payload: dict[str, Any],
    *,
    decisions: list[AdjudicationDecision],
) -> dict[str, Any]:
    """Enforce the per-candidate adjudication boundary on final merge buckets.

    The final model may merge or explicitly reject an individually accepted
    duplicate, but it cannot promote a rejected/pending candidate to an
    accepted write, nor omit an already-adjudicated candidate from every
    bucket. These are cross-stage consistency rules, not new narrative
    judgments.
    """
    result = dict(payload)
    decisions_by_id = {
        str(decision.candidate_id or "").strip(): decision
        for decision in list(decisions or [])
        if str(decision.candidate_id or "").strip()
    }
    known_ids = set(decisions_by_id)

    def bucket(name: str) -> list[str]:
        return _unique_ordered(
            candidate_id
            for candidate_id in _scope_list(result.get(name), limit=200, item_limit=100)
            if candidate_id in known_ids
        )

    accepted = bucket("accepted_candidate_ids")
    rejected = bucket("rejected_candidate_ids")
    pending = bucket("pending_candidate_ids")
    repair = bucket("repair_candidate_ids")

    # Explicit final rejection may de-duplicate an individually accepted item.
    accepted = [candidate_id for candidate_id in accepted if candidate_id not in rejected]
    for candidate_id, decision in decisions_by_id.items():
        verdict = str(decision.verdict or "ambiguous")
        repair_kind = str(decision.repair_kind or "none")
        if verdict == "reject":
            if candidate_id not in rejected:
                rejected.append(candidate_id)
            accepted = [item for item in accepted if item != candidate_id]
            pending = [item for item in pending if item != candidate_id]
            repair = [item for item in repair if item != candidate_id]
        elif verdict in {"ambiguous", "defer"}:
            if candidate_id not in pending and candidate_id not in rejected:
                pending.append(candidate_id)
            accepted = [item for item in accepted if item != candidate_id]
            repair = [item for item in repair if item != candidate_id]
        elif verdict == "needs_repair" and repair_kind == "text":
            if candidate_id not in repair:
                repair.append(candidate_id)
            accepted = [item for item in accepted if item != candidate_id]
            pending = [item for item in pending if item != candidate_id]
        elif verdict == "accept" or repair_kind == "mapping":
            if repair_kind == "mapping":
                repair = [item for item in repair if item != candidate_id]
            if (
                candidate_id not in accepted
                and candidate_id not in rejected
                and candidate_id not in pending
                and candidate_id not in repair
            ):
                accepted.append(candidate_id)

    result["accepted_candidate_ids"] = accepted
    result["rejected_candidate_ids"] = rejected
    result["pending_candidate_ids"] = pending
    result["repair_candidate_ids"] = repair
    # Re-derive the chapter action from authoritative buckets. The model's
    # natural-language summary/state-update projection remains untouched.
    result["verdict"] = ""
    normalized_result = normalize_final_adjudication_payload(result)
    if isinstance(normalized_result, dict):
        result = normalized_result
    if repair:
        result["should_block_archive"] = True
    return result


def _normalize_target_coverage_matrix(
    raw_items: Any,
    *,
    contract_targets: list[ContractTargetSpec],
    decisions: list[AdjudicationDecision],
) -> list[dict[str, Any]]:
    valid_targets = _contract_targets_by_id(contract_targets)
    if isinstance(raw_items, dict):
        iterable = [
            {"target_id": key, **(_as_dict(value) or {"coverage_status": value})}
            for key, value in raw_items.items()
        ]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        iterable = []
    decision_ids = {
        str(getattr(decision, "candidate_id", "") or "").strip()
        for decision in list(decisions or [])
        if str(getattr(decision, "candidate_id", "") or "").strip()
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in iterable:
        data = _as_dict(item)
        target_id = str(data.get("target_id") or data.get("id") or "").strip()
        if target_id.startswith("contract."):
            target_id = target_id[len("contract.") :]
        if target_id not in valid_targets or target_id in seen:
            continue
        status = str(
            data.get("coverage_status") or data.get("status") or data.get("verdict") or ""
        ).strip()
        if status not in _COVERAGE_STATUSES:
            continue
        candidate_ids = [
            candidate_id
            for candidate_id in _scope_list(
                data.get("candidate_ids") or data.get("covered_candidate_ids"),
                limit=12,
                item_limit=100,
            )
            if str(candidate_id or "").strip() in decision_ids
        ]
        repair_kind = _normalize_repair_kind(data.get("repair_kind"))
        seen.add(target_id)
        result.append(
            _drop_empty_mapping(
                {
                    "target_id": target_id,
                    "coverage_status": status,
                    "candidate_ids": candidate_ids,
                    "repair_kind": repair_kind,
                    "reason": _clean_text(data.get("reason") or data.get("rationale"), limit=240),
                }
            )
        )
    return result


def _filter_final_text_repairs(
    payload: dict[str, Any],
    *,
    decisions: list[AdjudicationDecision],
) -> dict[str, Any]:
    """Keep only text repairs in the repair branch.

    Mapping repairs are executable local structure fixes, so they must not
    trigger the full-text repair step or block archival by themselves.
    """
    result = dict(payload)
    text_repair_ids = {
        str(getattr(decision, "candidate_id", "") or "").strip()
        for decision in list(decisions or [])
        if str(getattr(decision, "repair_kind", "") or "").strip() == "text"
        and str(getattr(decision, "candidate_id", "") or "").strip()
    }
    raw_repair_ids = result.get("repair_candidate_ids")
    repair_ids = [
        candidate_id
        for candidate_id in _scope_list(raw_repair_ids, limit=24, item_limit=100)
        if candidate_id in text_repair_ids
    ]
    repair_issues: list[dict[str, Any]] = []
    for issue in list(result.get("repair_issues") or []):
        data = _as_dict(issue)
        candidate_id = str(data.get("candidate_id") or "").strip()
        repair_kind = _normalize_repair_kind(data.get("repair_kind"))
        if candidate_id and candidate_id in text_repair_ids:
            data["repair_kind"] = "text"
            repair_issues.append(data)
        elif repair_kind == "text" and not candidate_id:
            repair_issues.append(data)
    result["repair_candidate_ids"] = repair_ids
    result["repair_issues"] = repair_issues
    target_text_repairs = [
        item
        for item in list(result.get("target_coverage") or [])
        if _as_dict(item).get("coverage_status") in {"overreached", "needs_repair"}
        and _as_dict(item).get("repair_kind") == "text"
    ]
    if not repair_ids and not repair_issues and not target_text_repairs:
        if result.get("verdict") == "needs_repair":
            if result.get("accepted_candidate_ids") or result.get("state_updates"):
                result["verdict"] = "accept"
            elif result.get("pending_candidate_ids") or result.get("pending_items"):
                result["verdict"] = "defer"
            elif result.get("rejected_candidate_ids"):
                result["verdict"] = "reject"
            else:
                result["verdict"] = "ambiguous"
        result["should_block_archive"] = False
    return result


def _normalize_candidate_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept common model aliases while keeping the downstream canonical shape."""
    normalized = dict(payload)
    if not str(normalized.get("summary") or "").strip():
        for key in ("description", "desc", "rationale", "change_summary"):
            value = str(normalized.get(key) or "").strip()
            if value:
                normalized["summary"] = value
                break
    if not normalized.get("evidence"):
        for key in ("quote", "evidence_quote", "evidence_text", "text_quote"):
            value = str(normalized.get(key) or "").strip()
            if value:
                normalized["evidence"] = [{"quote": value}]
                break
    return normalized


def _normalize_entity_ids(raw_entity_ids: Any) -> list[str]:
    if raw_entity_ids is None:
        return []
    if isinstance(raw_entity_ids, str):
        values = re.split(r"[,，;；、\n]+", raw_entity_ids)
    elif isinstance(raw_entity_ids, (list, tuple, set)):
        values = list(raw_entity_ids)
    else:
        values = [raw_entity_ids]
    return [item for item in (str(value or "").strip() for value in values) if item]


def _normalize_proposed_delta(
    raw_proposed_delta: Any,
    *,
    raw_delta_type: str,
    normalized_delta_type: str,
) -> dict[str, Any]:
    if isinstance(raw_proposed_delta, dict):
        proposed_delta = dict(raw_proposed_delta)
    elif raw_proposed_delta in (None, ""):
        proposed_delta = {}
    else:
        proposed_delta = {"value": raw_proposed_delta}

    raw_delta_key = _canonical_delta_type_text(raw_delta_type)
    if raw_delta_key and raw_delta_key != normalized_delta_type:
        proposed_delta.setdefault("subtype", raw_delta_key)
        proposed_delta.setdefault("raw_delta_type", raw_delta_type)
    if normalized_delta_type == "knowledge":
        proposed_delta.setdefault("knowledge_type", "known")
    return proposed_delta


def _ensure_proposed_delta_payload(
    proposed_delta: dict[str, Any],
    *,
    payload: dict[str, Any],
    raw_delta_type: str,
    normalized_delta_type: str,
) -> dict[str, Any]:
    """Backfill a writeable state payload without making a semantic decision."""
    result = dict(proposed_delta or {})
    metadata_keys = {"subtype", "raw_delta_type", "knowledge_type"}
    has_meaningful_value = any(
        key not in metadata_keys and value not in (None, "", [], {})
        for key, value in result.items()
    )
    if has_meaningful_value:
        if normalized_delta_type == "knowledge":
            result.setdefault("knowledge_type", "known")
        if normalized_delta_type == "cognitive":
            _backfill_cognitive_delta_payload(result, payload=payload)
        return result

    summary = str(payload.get("summary") or "").strip()
    entity_ids = _normalize_entity_ids(payload.get("entity_ids"))
    evidence_quotes = [
        str(getattr(span, "quote", "") or "").strip()
        for span in list(payload.get("evidence") or [])
        if str(getattr(span, "quote", "") or "").strip()
    ]
    raw_delta_key = _canonical_delta_type_text(raw_delta_type)

    result.setdefault("source", "llm_candidate_normalized")
    result.setdefault("semantic_source", "llm_candidate")
    result.setdefault("format_source", "local_format_fallback")
    result.setdefault("delta_type", normalized_delta_type)
    if normalized_delta_type == "knowledge":
        result.setdefault("knowledge_type", "known")
    if normalized_delta_type == "cognitive":
        _backfill_cognitive_delta_payload(result, payload=payload)
    if raw_delta_key and raw_delta_key != normalized_delta_type:
        result.setdefault("subtype", raw_delta_key)
        result.setdefault("raw_delta_type", raw_delta_type)
    if summary:
        result.setdefault("summary", summary)
    if entity_ids:
        result.setdefault("entity_ids", entity_ids)
    if evidence_quotes:
        result.setdefault("evidence_quotes", evidence_quotes)
    return result


def _backfill_cognitive_delta_payload(result: dict[str, Any], *, payload: dict[str, Any]) -> None:
    for key in (
        "cognitive_subjects",
        "cognitive_object",
        "cognitive_level",
        "action_level",
        "character_knowledge_coverage",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            result.setdefault(key, value)
    if "object" in payload and payload.get("object") not in (None, ""):
        result.setdefault("cognitive_object", payload.get("object"))


def _canonical_delta_type_text(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .strip("\"'`，。,.；;：: ")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _normalize_evidence(
    raw_evidence: Any,
    *,
    evidence_index: EvidenceIndex,
    limit: int = CandidateStateDeltaExtractionStep._DEFAULT_EVIDENCE_LIMIT,
) -> list[EvidenceSpan]:
    raw_list = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence]
    spans: list[EvidenceSpan] = []
    for raw in raw_list:
        if isinstance(raw, str):
            quote = raw
        elif isinstance(raw, dict):
            quote = str(raw.get("quote") or raw.get("evidence") or "").strip()
        else:
            quote = ""
        if not quote:
            continue
        span = evidence_index.locate_quote(quote)
        if span.found:
            spans.append(span)
        if len(spans) >= limit:
            break
    return spans


_STATE_UPDATE_SCOPES = {
    "chapter_presence",
    "main_plot",
    "subplot",
    "relationship_carry_forward",
    "contract_progression",
}
_STATE_UPDATE_KEYS = (
    "candidate_id",
    "scope",
    "state_path",
    "value",
    "summary",
    "present_characters",
    "entity_ids",
    "plot_thread_id",
    "subplot_id",
    "relationship_pair",
    "target_id",
    "covered_target_ids",
    "contract_field",
    "target",
    "knowledge_type",
    "cognitive_subjects",
    "cognitive_object",
    "cognitive_level",
    "action_level",
    "character_knowledge_coverage",
    "next_impact",
    "source",
)


def _normalize_final_state_updates(
    raw_updates: Any,
    *,
    candidates: list[CandidateStateDelta],
    current_state: dict[str, Any],
    accepted_candidate_ids: Any,
) -> list[dict[str, Any]]:
    allowed_paths = _allowed_state_paths(current_state)
    candidate_updates = _candidate_update_payloads(candidates, allowed_paths=allowed_paths)
    accepted_ids = [
        str(item or "").strip()
        for item in (accepted_candidate_ids if isinstance(accepted_candidate_ids, list) else [])
        if str(item or "").strip()
    ]
    updates = raw_updates if isinstance(raw_updates, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for update in updates:
        compact = _compact_final_state_update(update, allowed_paths=allowed_paths)
        if not compact:
            continue
        candidate_id = str(compact.get("candidate_id") or "").strip()
        fallback = candidate_updates.get(candidate_id)
        if fallback:
            # The final LLM decides the value, but may omit structural fields
            # already validated on the candidate. Preserve that authoritative
            # shape and let explicit final fields override it.
            compact = _compact_final_state_update(
                {**fallback, **compact},
                allowed_paths=allowed_paths,
            )
        key = str(compact.get("candidate_id") or compact.get("state_path") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(compact)

    for candidate_id in accepted_ids:
        if candidate_id in seen:
            continue
        fallback = candidate_updates.get(candidate_id)
        if not fallback:
            continue
        result.append(fallback)
        seen.add(candidate_id)
    return result


def _allowed_state_paths(current_state: Any) -> set[str]:
    scoped = _as_dict(current_state).get("state_update_slots")
    data = _as_dict(scoped)
    paths: set[str] = set()

    def add_path(value: Any) -> None:
        path = _clean_text(_as_dict(value).get("state_path"), limit=160)
        if path:
            paths.add(path)

    add_path(data.get("chapter_presence"))
    for key in (
        "main_plot",
        "subplot",
        "relationship_carry_forward",
        "contract_progression",
    ):
        for item in _iter_mapping_or_sequence_values(data.get(key)):
            add_path(item)
    return paths


def _candidate_update_payloads(
    candidates: list[CandidateStateDelta],
    *,
    allowed_paths: set[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candidate in list(candidates or []):
        proposed = _as_dict(getattr(candidate, "proposed_delta", {}))
        proposed.setdefault("candidate_id", getattr(candidate, "candidate_id", ""))
        proposed.setdefault("summary", getattr(candidate, "summary", ""))
        compact = _compact_final_state_update(proposed, allowed_paths=allowed_paths)
        candidate_id = str(getattr(candidate, "candidate_id", "") or "").strip()
        if candidate_id and compact:
            compact["candidate_id"] = candidate_id
            result[candidate_id] = compact
    return result


def _compact_final_state_update(
    update: Any,
    *,
    allowed_paths: set[str],
) -> dict[str, Any]:
    if not isinstance(update, dict):
        return {}
    state_path = _clean_text(update.get("state_path") or update.get("path"), limit=160)
    if allowed_paths and not state_path:
        return {}
    if state_path and allowed_paths and state_path not in allowed_paths:
        return {}
    payload: dict[str, Any] = {}
    for key in _STATE_UPDATE_KEYS:
        if key not in update:
            continue
        if key == "character_knowledge_coverage":
            payload[key] = normalize_character_knowledge_coverage(
                update.get(key),
                none_as_empty=True,
            )
        elif key == "cognitive_subjects":
            payload[key] = _unique_ordered(update.get(key) or [])
        elif key in {
            "present_characters",
            "entity_ids",
            "relationship_pair",
            "covered_target_ids",
        }:
            payload[key] = _scope_list(update.get(key), limit=12, item_limit=80)
        else:
            payload[key] = _clean_or_keep_state_value(update.get(key))
    if state_path:
        payload["state_path"] = state_path
    scope = _normalize_state_update_scope(payload.get("scope"))
    if scope:
        payload["scope"] = scope
    elif state_path:
        inferred = _infer_state_update_scope(state_path)
        if inferred:
            payload["scope"] = inferred
    return _drop_empty_mapping(payload)


def _clean_or_keep_state_value(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return _clean_text(value, limit=180)
    if isinstance(value, (list, tuple, set)):
        return _scope_list(value, limit=8, item_limit=120)
    if isinstance(value, dict):
        return _scope_mapping(value, max_items=6, str_limit=120)
    return _clean_text(value, limit=180)


def _normalize_state_update_scope(value: Any) -> str:
    scope = str(value or "").strip()
    return scope if scope in _STATE_UPDATE_SCOPES else ""


def _infer_state_update_scope(state_path: str) -> str:
    if ".present_characters" in state_path or state_path.startswith("chapter."):
        return "chapter_presence"
    if state_path.startswith("plot.main"):
        return "main_plot"
    if state_path.startswith("subplot."):
        return "subplot"
    if state_path.startswith("relationship."):
        return "relationship_carry_forward"
    if state_path.startswith("contract."):
        return "contract_progression"
    return ""


def _clean_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；： \n") + "…"


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _drop_empty_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _context_value(value: Any) -> Any:
    """Normalize an already-scoped state artifact without discarding content."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _context_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_context_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _context_mapping(value: Any) -> dict[str, Any]:
    normalized = _context_value(value)
    return normalized if isinstance(normalized, dict) else {}


def _iter_mapping_or_sequence_values(value: Any, *, limit: int | None = None) -> list[Any]:
    if isinstance(value, dict):
        items = list(value.values())
        return items[:limit] if limit is not None else items
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return items[:limit] if limit is not None else items
    return []


def _scope_previous_exit_state(state: Any) -> dict[str, Any]:
    return _context_mapping(state)


def _scope_authoritative_projection_for_adjudication(projection: Any) -> dict[str, Any]:
    data = _as_dict(projection)
    return _drop_empty_mapping(
        {
            "last_chapter": data.get("last_chapter"),
            "pending_items": _context_value(data.get("pending_items")) or [],
        }
    )


def _scope_character_roster_for_adjudication(roster: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _iter_mapping_or_sequence_values(roster):
        data = _as_dict(item)
        name = str(data.get("name") or "").strip()
        if not name or is_system_artifact_name(name):
            continue
        if not any(
            data.get(key)
            for key in (
                "character_id",
                "role",
                "gender",
                "social_status",
                "voice",
                "location",
                "source",
            )
        ):
            continue
        result.append(
            _drop_empty_mapping(
                {
                    "character_id": str(data.get("character_id") or "").strip(),
                    "name": name,
                    "role": str(data.get("role") or "").strip(),
                    "gender": str(data.get("gender") or "").strip(),
                    "status": str(data.get("status") or "").strip(),
                    "time_layer": str(data.get("time_layer") or "").strip(),
                    "social_status": str(data.get("social_status") or "").strip(),
                    "last_seen_chapter": data.get("last_seen_chapter"),
                    "location": str(data.get("location") or "").strip(),
                }
            )
        )
    return result


def _scope_state_update_slots_for_adjudication(slots: Any) -> dict[str, Any]:
    return _context_mapping(slots)


def _scope_list(values: Any, *, limit: int = 12, item_limit: int = 220) -> list[Any]:
    if not isinstance(values, (list, tuple, set)):
        return []
    scoped: list[Any] = []
    for item in list(values)[:limit]:
        if isinstance(item, dict):
            scoped.append(_scope_mapping(item, max_items=10, str_limit=item_limit))
        elif hasattr(item, "model_dump"):
            scoped.append(
                _scope_mapping(item.model_dump(mode="json"), max_items=10, str_limit=item_limit)
            )
        else:
            text = _clean_text(item, limit=item_limit)
            if text:
                scoped.append(text)
    return scoped


def _scope_mapping(value: Any, *, max_items: int = 16, str_limit: int = 240) -> dict[str, Any]:
    data = _as_dict(value)
    scoped: dict[str, Any] = {}
    for idx, (key, item) in enumerate(data.items()):
        if idx >= max_items:
            scoped["_truncated"] = f"仅展示前 {max_items} 项"
            break
        key_text = _clean_text(key, limit=80)
        if not key_text:
            continue
        if isinstance(item, dict) or hasattr(item, "model_dump"):
            scoped[key_text] = _scope_mapping(item, max_items=8, str_limit=str_limit)
        elif isinstance(item, (list, tuple, set)):
            scoped[key_text] = _scope_list(item, limit=8, item_limit=str_limit)
        else:
            scoped[key_text] = _clean_text(item, limit=str_limit)
    return scoped


def _scope_chapter_contract(contract: Any) -> dict[str, Any]:
    """Keep only executable chapter-contract fields needed by state adjudication."""
    data = _as_dict(contract)
    scoped: dict[str, Any] = {}
    for field_name in ChapterContract.model_fields:
        value = data.get(field_name)
        if field_name == "chapter_number":
            scoped[field_name] = value or 0
        elif value not in (None, ""):
            scoped[field_name] = _context_value(value)
    scoped.setdefault("chapter_number", data.get("chapter_number", 0))
    return scoped


def _scope_current_state(state: Any) -> dict[str, Any]:
    """Keep complete, purpose-scoped current state without repeating persisted history."""
    data = _as_dict(state)
    return {
        "chapter_number": data.get("chapter_number", 0),
        "character_roster": _scope_character_roster_for_adjudication(data.get("character_roster")),
        "state_update_slots": _scope_state_update_slots_for_adjudication(
            data.get("state_update_slots")
        ),
        "previous_exit_state": _scope_previous_exit_state(data.get("previous_exit_state")),
        "active_relationships": _context_value(data.get("active_relationships")) or [],
        "active_plot_threads": _context_value(data.get("active_plot_threads")) or [],
        "authoritative_projection": _scope_authoritative_projection_for_adjudication(
            data.get("authoritative_projection")
        ),
    }


def _scope_final_current_state(state: Any) -> dict[str, Any]:
    """Keep only state fields the final merge can legally write against."""
    scoped = _scope_current_state(state)
    return {
        "chapter_number": scoped.get("chapter_number", 0),
        "character_roster": [
            {
                key: item.get(key)
                for key in ("character_id", "name", "role", "status")
                if item.get(key) not in (None, "", [], {})
            }
            for item in list(scoped.get("character_roster") or [])
            if isinstance(item, dict)
        ],
        "state_update_slots": _scope_final_state_update_slots(scoped.get("state_update_slots")),
    }


def _scope_final_state_update_slots(slots: Any) -> dict[str, Any]:
    """Expose writable paths, never repeat their long target prose at final merge."""
    data = _as_dict(slots)
    result: dict[str, Any] = {}
    allowed = {"state_path", "target_id", "contract_field", "required_for_archive", "write_fields"}
    for name, records in data.items():
        if isinstance(records, dict):
            result[str(name)] = {key: value for key, value in records.items() if key in allowed}
        elif isinstance(records, list):
            result[str(name)] = [
                {key: value for key, value in _as_dict(record).items() if key in allowed}
                for record in records
            ]
    return result


def _scope_entity_registry(
    registry: Any,
    *,
    chapter_text: str = "",
    current_state: Any = None,
    chapter_contract: Any = None,
) -> dict[str, Any]:
    """Route complete identity records that are referenced by this chapter's work order."""
    data = _as_dict(registry)
    state = _as_dict(current_state)
    reference_text = "\n".join(
        (
            str(chapter_text or ""),
            _json_text(_scope_chapter_contract(chapter_contract)),
            _json_text(_scope_previous_exit_state(state.get("previous_exit_state"))),
            _json_text(_context_value(state.get("active_relationships")) or []),
            _json_text(_context_value(state.get("active_plot_threads")) or []),
        )
    )
    entities = []
    for item in list(data.get("entities", []) or []):
        entity = _as_dict(item)
        entity_id = str(entity.get("entity_id") or "").strip()
        name = str(entity.get("name") or "").strip()
        aliases = [
            str(alias).strip()
            for alias in list(entity.get("aliases") or [])
            if str(alias or "").strip()
        ]
        if not name or not any(
            token and token in reference_text for token in (entity_id, name, *aliases)
        ):
            continue
        entities.append(
            {
                "entity_id": entity_id,
                "name": name,
                "entity_type": str(entity.get("entity_type") or "").strip(),
                "aliases": aliases,
            }
        )
    return {"entities": entities}


def _scope_evidence_span(span: Any) -> dict[str, Any]:
    data = _as_dict(span)
    return {
        "quote": _clean_text(data.get("quote"), limit=160),
        "paragraph_index": data.get("paragraph_index"),
        "found": bool(data.get("found", False)),
    }


def _scope_candidate(
    candidate: Any,
    *,
    evidence_limit: int = CandidateStateDeltaExtractionStep._DEFAULT_EVIDENCE_LIMIT,
) -> dict[str, Any]:
    data = _as_dict(candidate)
    return {
        "candidate_id": _clean_text(data.get("candidate_id"), limit=100),
        "chapter_number": data.get("chapter_number", 0),
        "delta_type": _clean_text(data.get("delta_type"), limit=40),
        "summary": _clean_text(data.get("summary"), limit=260),
        "entity_ids": _scope_list(data.get("entity_ids"), limit=8, item_limit=80),
        "covered_target_ids": _scope_list(data.get("covered_target_ids"), limit=12, item_limit=120),
        "proposed_delta": _scope_proposed_delta_for_prompt(data.get("proposed_delta")),
        "evidence": [
            _scope_evidence_span(span)
            for span in list(data.get("evidence", []) or [])[: max(1, min(4, evidence_limit))]
        ],
        "extraction_notes": _clean_text(data.get("extraction_notes"), limit=220),
    }


def _scope_proposed_delta_for_prompt(proposed_delta: Any) -> dict[str, Any]:
    scoped = _scope_mapping(proposed_delta, max_items=12)
    scoped.pop("raw_delta_type", None)
    scoped.pop("evidence", None)
    scoped.pop("evidence_quotes", None)
    scoped.pop("context", None)
    return scoped


def _scope_candidates(candidates: list[CandidateStateDelta]) -> list[dict[str, Any]]:
    return [_scope_candidate(candidate) for candidate in list(candidates or [])]


def _scope_final_candidates(candidates: list[CandidateStateDelta]) -> list[dict[str, Any]]:
    """Project candidates to merge/write fields after evidence adjudication."""
    result: list[dict[str, Any]] = []
    for candidate in list(candidates or []):
        scoped = _scope_candidate(candidate)
        result.append(
            {
                key: value
                for key, value in scoped.items()
                if key
                in {
                    "candidate_id",
                    "delta_type",
                    "summary",
                    "entity_ids",
                    "covered_target_ids",
                    "proposed_delta",
                }
            }
        )
    return result


def _scope_decision(decision: Any) -> dict[str, Any]:
    data = _as_dict(decision)
    return {
        "candidate_id": _clean_text(data.get("candidate_id"), limit=100),
        "verdict": _clean_text(data.get("verdict"), limit=40),
        "severity": _clean_text(data.get("severity"), limit=40),
        "confidence": data.get("confidence", 0.0),
        "rationale": _clean_text(data.get("rationale"), limit=320),
        "covered_target_ids": _scope_list(data.get("covered_target_ids"), limit=12, item_limit=120),
        "coverage_status": _clean_text(data.get("coverage_status"), limit=40),
        "issue_kind": _clean_text(data.get("issue_kind"), limit=40),
        "repair_kind": _clean_text(data.get("repair_kind"), limit=40),
        "evidence_quotes": _scope_list(data.get("evidence_quotes"), limit=4, item_limit=160),
        "affected_state_paths": _scope_list(
            data.get("affected_state_paths"),
            limit=8,
            item_limit=120,
        ),
        "repair_instruction": _clean_text(data.get("repair_instruction"), limit=320),
        "pending_reason": _clean_text(data.get("pending_reason"), limit=220),
    }


def _scope_decisions(decisions: list[AdjudicationDecision]) -> list[dict[str, Any]]:
    return [_scope_decision(decision) for decision in list(decisions or [])]


def _scope_final_decisions(decisions: list[AdjudicationDecision]) -> list[dict[str, Any]]:
    """Project per-candidate verdicts without repeating evidence and prose."""
    keys = {
        "candidate_id",
        "verdict",
        "severity",
        "confidence",
        "covered_target_ids",
        "coverage_status",
        "issue_kind",
        "repair_kind",
        "affected_state_paths",
        "repair_instruction",
        "pending_reason",
    }
    return [
        {key: value for key, value in _scope_decision(decision).items() if key in keys}
        for decision in list(decisions or [])
    ]


def _select_final_prompt_contract_targets(
    contract_targets: list[ContractTargetSpec],
    *,
    candidates: list[CandidateStateDelta],
    decisions: list[AdjudicationDecision],
    contract_coverage: ContractCoverageReport | None,
) -> list[ContractTargetSpec]:
    """Keep archive targets plus context-only targets actually referenced upstream."""
    referenced_ids: set[str] = {
        str(target_id or "").strip()
        for item in [*list(candidates or []), *list(decisions or [])]
        for target_id in list(getattr(item, "covered_target_ids", []) or [])
        if str(target_id or "").strip()
    }
    if contract_coverage is not None:
        referenced_ids.update(_coverage_problem_target_ids(contract_coverage))
    return [
        target
        for target in contract_targets
        if target.required_for_archive or target.target_id in referenced_ids
    ]


def _scope_final_contract_targets(
    contract_targets: list[ContractTargetSpec],
    *,
    target_char_limit: int = 96,
) -> list[dict[str, Any]]:
    """Render the minimal fixed target menu consumed by the final merger."""
    return [
        {
            "target_id": target.target_id,
            "target": _clean_text(target.target, limit=max(40, min(240, target_char_limit))),
            "required_for_archive": target.required_for_archive,
            "state_path": target.state_path,
            "delta_type": target.delta_type,
        }
        for target in contract_targets
    ]


def _scope_final_contract_coverage(
    report: ContractCoverageReport,
    *,
    allowed_target_ids: set[str],
) -> dict[str, Any]:
    """Remove repeated target prose/evidence from the mechanical coverage report."""

    def coverage_target_id(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("target_id") or value.get("id") or "").strip()
        return str(getattr(value, "target_id", value) or "").strip()

    items: list[dict[str, Any]] = []
    for item in list(report.items or []):
        target_id = str(getattr(item, "target_id", "") or "").strip()
        if not target_id or target_id not in allowed_target_ids:
            continue
        items.append(
            {
                "target_id": target_id,
                "status": str(getattr(item, "status", "") or ""),
                "evidence_found": bool(getattr(item, "evidence_found", False)),
                "candidate_ids": list(getattr(item, "candidate_ids", []) or []),
                "accepted_candidate_ids": list(getattr(item, "accepted_candidate_ids", []) or []),
                "pending_candidate_ids": list(getattr(item, "pending_candidate_ids", []) or []),
                "repair_candidate_ids": list(getattr(item, "repair_candidate_ids", []) or []),
            }
        )
    return {
        "chapter_number": report.chapter_number,
        "total_required_targets": report.total_required_targets,
        "covered_count": report.covered_count,
        "evidence_found_count": report.evidence_found_count,
        "all_required_covered": report.all_required_covered,
        "uncovered_targets": [
            target_id
            for item in list(report.uncovered_targets or [])
            if (target_id := coverage_target_id(item)) in allowed_target_ids
        ],
        "unaccepted_targets": [
            target_id
            for item in list(report.unaccepted_targets or [])
            if (target_id := coverage_target_id(item)) in allowed_target_ids
        ],
        "items": items,
    }


def _scope_final_adjudication(final: Any) -> dict[str, Any]:
    data = _as_dict(final)
    return {
        "chapter_number": data.get("chapter_number", 0),
        "verdict": _clean_text(data.get("verdict"), limit=40),
        "severity": _clean_text(data.get("severity"), limit=40),
        "confidence": data.get("confidence", 0.0),
        "repair_candidate_ids": _scope_list(data.get("repair_candidate_ids"), limit=12),
        "repair_issues": _scope_list(data.get("repair_issues"), limit=12),
        "target_coverage": _scope_list(data.get("target_coverage"), limit=24),
        "should_block_archive": bool(data.get("should_block_archive", False)),
        "summary": _clean_text(data.get("summary"), limit=360),
    }


def _scope_repair_adjudication_payload(
    final: FinalStateAdjudication,
    decisions: list[AdjudicationDecision],
    candidates: list[CandidateStateDelta],
) -> dict[str, Any]:
    """Build a repair-only adjudication payload for REPAIR_ADJUDICATED_ISSUE."""
    final_payload = _scope_final_adjudication(final)
    repair_ids = {
        str(item or "").strip()
        for item in getattr(final, "repair_candidate_ids", []) or []
        if str(item or "").strip()
    }
    for issue in list(getattr(final, "repair_issues", []) or []):
        if isinstance(issue, dict):
            candidate_id = str(issue.get("candidate_id") or "").strip()
            if candidate_id:
                repair_ids.add(candidate_id)
    if not repair_ids:
        repair_ids = {
            str(getattr(decision, "candidate_id", "") or "").strip()
            for decision in list(decisions or [])
            if str(getattr(decision, "verdict", "") or "").strip() == "needs_repair"
            and str(getattr(decision, "candidate_id", "") or "").strip()
        }
    if repair_ids:
        final_payload["repair_candidate_ids"] = sorted(repair_ids)
    repair_decisions = [
        decision
        for decision in list(decisions or [])
        if str(getattr(decision, "candidate_id", "") or "").strip() in repair_ids
    ]
    repair_candidates = [
        candidate
        for candidate in list(candidates or [])
        if str(getattr(candidate, "candidate_id", "") or "").strip() in repair_ids
    ]
    return {
        "final_adjudication": final_payload,
        "repair_decisions": _scope_decisions(repair_decisions),
        "repair_candidates": _scope_candidates(repair_candidates),
    }


def _scope_memory_repair_hints(memory_hints: dict[str, Any] | None) -> dict[str, Any]:
    """Return bounded, explicitly non-authoritative memory hints for repair only."""
    data = _as_dict(memory_hints)
    if not data:
        return {}
    result = {
        "source_type": "memory_hint_non_authoritative",
        "allowed_use": [
            "帮助定位可能相关的旧章语境",
            "帮助修复段落保持人物语气、伏笔承接和风格连续",
        ],
        "not_allowed_use": [
            "不得把记忆摘要当作正文证据",
            "不得据此新增状态事实或新增修复目标",
            "不得覆盖裁判问题、章节契约或当前硬事实状态",
        ],
        "relevant_history": _scope_list(
            data.get("relevant_history") or data.get("memory_relevant_history"),
            limit=6,
            item_limit=260,
        ),
        "previous_chapter_events": _scope_list(
            data.get("previous_chapter_events") or data.get("memory_previous_chapter_events"),
            limit=8,
            item_limit=220,
        ),
        "motif_suggestions": _scope_list(
            data.get("motif_suggestions") or data.get("memory_motif_suggestions"),
            limit=6,
            item_limit=220,
        ),
        "forbidden_repetition": _scope_list(
            data.get("forbidden_repetition") or data.get("memory_forbidden_repetition"),
            limit=8,
            item_limit=220,
        ),
        "expression_channel_records": _scope_list(
            data.get("expression_channel_records") or data.get("memory_expression_channel_records"),
            limit=8,
            item_limit=220,
        ),
    }
    return {
        key: value
        for key, value in result.items()
        if key in {"source_type", "allowed_use", "not_allowed_use"} or value
    }


def _json_text(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _compact_json_text(value: Any) -> str:
    """Serialize an already-scope-safe final-merge payload without whitespace."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _len_payload_items(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1 if value else 0


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _save_report(path: Any, report: NarrativeAdjudicationReport) -> None:
    from novel_forge.persistence.filesystem import atomic_write_text

    atomic_write_text(
        path,
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )
