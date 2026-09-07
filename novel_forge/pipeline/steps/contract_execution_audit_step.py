"""Contract execution audit using candidate deltas + milestone windows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.narrative_state.knowledge_ops import (
    knowledge_op_evidence_candidates,
    knowledge_op_target_text,
    normalize_knowledge_op,
)
from novel_forge.narrative_state.schemas import (
    CandidateStateDelta,
    ContractExecutionReport,
)
from novel_forge.pipeline.steps.base import PipelineStep


@dataclass
class ContractExecutionAuditInput:
    chapter_number: int
    chapter_text: str
    chapter_contract: dict[str, Any]
    current_state: dict[str, Any] = field(default_factory=dict)
    milestone_window: dict[str, Any] = field(default_factory=dict)
    progression_ledger_tail: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[CandidateStateDelta] = field(default_factory=list)
    strictness: str = "block"
    future_leak_guard_enabled: bool = True


class ContractExecutionAuditStep(
    PipelineStep[ContractExecutionAuditInput, ContractExecutionReport]
):
    """Adjudicate whether a chapter stayed within its executable contract."""

    @property
    def step_name(self) -> str:
        return "contract_execution_audit"

    async def _execute(
        self,
        input_data: ContractExecutionAuditInput,
    ) -> ContractExecutionReport:
        local = build_local_contract_execution_report(input_data)
        request_payload = {
            "chapter_number": input_data.chapter_number,
            "contract_item": _json_text(input_data.chapter_contract),
            "current_state": _json_text(
                {
                    "current_state": input_data.current_state,
                    "progression_ledger_tail": input_data.progression_ledger_tail,
                    "milestone_window": input_data.milestone_window,
                }
            ),
            "evidence_window": _evidence_window(input_data),
            "local_prescreen": _json_text(local.model_dump(mode="json")),
            "audit_mode": "full_contract_execution",
        }
        data = await self._call_with_retry(
            TaskType.ADJUDICATE_CONTRACT_COMPLETION,
            request_payload,
            max_tokens=self._dynamic_max_tokens(
                TaskType.ADJUDICATE_CONTRACT_COMPLETION,
                max(1800, len(input_data.chapter_text) // 6),
                prompt_overhead=3200,
                min_tokens=2048,
            ),
            temperature=float(
                getattr(self.settings, "temp_adjudicate_contract_completion", 0.1) or 0.1
            ),
            required_keys=("verdict", "severity", "rationale"),
            max_retries=2,
        )
        return merge_contract_execution_adjudication(local, data, strictness=input_data.strictness)


def should_run_contract_execution_audit(
    *,
    contract: dict[str, Any],
    milestone_window: dict[str, Any] | None = None,
) -> bool:
    """Avoid burning a model call when a legacy contract has no progression controls."""

    if not isinstance(contract, dict) or not contract:
        return False
    controlled_fields = (
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
        "knowledge_ops",
        "cognitive_constraints",
    )
    if any(_has_meaningful_items(contract.get(field, [])) for field in controlled_fields):
        return True
    window = milestone_window or {}
    return bool(window.get("current") or window.get("future_guardrails"))


def build_local_contract_execution_report(
    input_data: ContractExecutionAuditInput,
) -> ContractExecutionReport:
    """Mechanical pre-screen for candidate evidence only.

    Local matching is intentionally non-authoritative: it only proposes
    suspicious spans for the LLM contract-completion judge.  Final missing /
    forbidden / future-leak findings must come from LLM adjudication.
    """

    contract = input_data.chapter_contract or {}
    evidence = _searchable_text(input_data)
    required = _string_list(contract.get("required_progressions", []))
    allowed = set(_string_list(contract.get("allowed_progressions", [])))
    forbidden = _string_list(contract.get("forbidden_progressions", []))
    future_risks = _string_list(contract.get("future_leak_risks", []))
    knowledge_ops = _knowledge_op_target_specs(contract.get("knowledge_ops", []))
    cognitive_specs = _cognitive_constraint_target_specs(
        contract.get("cognitive_constraints", []),
        current_chapter=input_data.chapter_number,
    )

    missing_candidates = [item for item in required if not _contains(evidence, item)]
    missing_knowledge = [
        target
        for target, candidates in knowledge_ops
        if not any(_contains(evidence, candidate) for candidate in candidates)
    ]
    forbidden_candidates = [item for item in forbidden if _contains(evidence, item)]
    future_candidates = [item for item in future_risks if _contains(evidence, item)]
    cognitive_candidates = [
        target
        for target, candidates in cognitive_specs
        if any(_contains(evidence, candidate) for candidate in candidates)
    ]
    if input_data.future_leak_guard_enabled:
        for milestone in list((input_data.milestone_window or {}).get("future_guardrails", []) or []):
            if not isinstance(milestone, dict):
                continue
            summary = str(milestone.get("summary") or "").strip()
            title = str(milestone.get("title") or "").strip()
            summary_tail = summary.rsplit("：", 1)[-1].strip() if "：" in summary else ""
            for candidate in (summary, summary_tail, title):
                if candidate and _contains(evidence, candidate):
                    future_candidates.append(candidate)

    observed = _observed_candidate_summaries(input_data.candidates)
    unexpected_candidates = [
        item
        for item in observed
        if item
        and item not in allowed
        and not any(_contains(item, allowed_item) for allowed_item in allowed)
    ][:8]
    local_candidate_hits = [
        *_candidate_hit_rows("missing_required_progression", missing_candidates),
        *_candidate_hit_rows("missing_knowledge_op", missing_knowledge),
        *_candidate_hit_rows("unexpected_progression", unexpected_candidates),
        *_candidate_hit_rows("forbidden_progression", forbidden_candidates),
        *_candidate_hit_rows("future_leak", future_candidates),
        *_candidate_hit_rows("cognitive_constraint", cognitive_candidates),
    ]
    return ContractExecutionReport(
        chapter_number=input_data.chapter_number,
        missing_required_progressions=missing_candidates,
        missing_knowledge_ops=missing_knowledge,
        cognitive_constraint_hits=[],
        local_candidate_hits=local_candidate_hits,
        contract_completion_score=10.0,
        repair_or_replan_decision="llm_adjudicate",
        verdict="ambiguous" if local_candidate_hits else "accept",
        severity="medium" if local_candidate_hits else "low",
        rationale="本地预筛只提供候选线索；最终契约完成、未来泄露与阻断决定必须由 LLM 裁判给出。",
        should_block_archive=False,
        source_text_hash=source_text_hash(input_data.chapter_text),
    )


def merge_contract_execution_adjudication(
    local: ContractExecutionReport,
    raw: Any,
    *,
    strictness: str,
) -> ContractExecutionReport:
    """Merge LLM adjudication with mechanical hard hits."""

    data = raw if isinstance(raw, dict) else {}
    payload = local.model_dump(mode="json")
    payload["missing_required_progressions"] = []
    payload["missing_knowledge_ops"] = []
    payload["unaccepted_knowledge_ops"] = []
    payload["unexpected_progressions"] = []
    payload["forbidden_progression_hits"] = []
    payload["future_leak_hits"] = []
    payload["cognitive_constraint_hits"] = []
    payload["evidence_quotes"] = []
    for key in (
        "missing_required_progressions",
        "missing_knowledge_ops",
        "unaccepted_knowledge_ops",
        "unexpected_progressions",
        "forbidden_progression_hits",
        "future_leak_hits",
        "cognitive_constraint_hits",
        "evidence_quotes",
    ):
        if isinstance(data.get(key), list):
            payload[key] = _dedupe(list(data.get(key, [])))
    if "contract_completion_score" in data:
        try:
            payload["contract_completion_score"] = max(
                0.0,
                min(10.0, float(data.get("contract_completion_score"))),
            )
        except (TypeError, ValueError):
            pass
    payload["verdict"] = str(data.get("verdict") or payload.get("verdict") or "ambiguous")
    payload["severity"] = str(data.get("severity") or payload.get("severity") or "medium")
    payload["rationale"] = str(data.get("rationale") or payload.get("rationale") or "")
    payload["repair_or_replan_decision"] = str(
        data.get("repair_or_replan_decision")
        or payload.get("repair_or_replan_decision")
        or "continue"
    )

    strict = str(strictness or "block").lower()
    if payload["cognitive_constraint_hits"] and not (
        payload["forbidden_progression_hits"] or payload["future_leak_hits"]
    ):
        payload["future_leak_hits"] = _dedupe(
            [*(payload.get("future_leak_hits") or []), payload["cognitive_constraint_hits"][0]]
        )
    has_hard_hits = bool(
        payload["forbidden_progression_hits"]
        or payload["future_leak_hits"]
        or payload["cognitive_constraint_hits"]
    )
    should_block = strict in {"block", "strict"} and has_hard_hits
    if strict == "strict" and (
        payload["missing_required_progressions"] or payload["missing_knowledge_ops"]
    ):
        should_block = True
    llm_blocks = str(payload["verdict"]).lower() in {"needs_repair", "reject"} and str(
        payload["severity"]
    ).lower() in {"high", "critical"}
    llm_explicit_block = bool(data.get("should_block_archive") or data.get("block"))
    # Severity gate: LLM explicit block is only honoured when hard hits exist
    # or severity is high/critical.  Medium-severity missing_required_progressions
    # (without forbidden/future_leak/cognitive hits) are soft violations that
    # should produce a repair ticket rather than a hard archive block.
    severity_norm = str(payload["severity"]).lower()
    llm_explicit_block_gated = llm_explicit_block and (
        has_hard_hits or severity_norm in {"high", "critical"}
    )
    payload["should_block_archive"] = bool(
        should_block or (strict != "warn" and (llm_blocks or llm_explicit_block_gated))
    )
    # Resolve a real ``repair_or_replan_decision`` whenever the audit is blocking
    # but the LLM left a placeholder (the local pre-screen seeds
    # ``"llm_adjudicate"``; the merge fallback seeds ``"continue"``; the LLM may
    # also return an empty string).  Without this, downstream
    # ``compile_contract_audit_repair_ticket`` filters on ``"repair" in
    # decision`` and returns ``None`` — the desktop user then sees a hard
    # ``ConsistencyViolationError`` carrying the meaningless literal
    # ``"llm_adjudicate (verdict=reject, severity=high, score=10.0)"``.
    if payload["should_block_archive"]:
        verdict_norm = str(payload["verdict"]).lower()
        severity_norm = str(payload["severity"]).lower()
        placeholder_decisions = {"llm_adjudicate", "continue", ""}
        if str(payload["repair_or_replan_decision"]).lower() in placeholder_decisions:
            if severity_norm == "critical":
                payload["repair_or_replan_decision"] = "replan"
            elif verdict_norm == "needs_repair":
                payload["repair_or_replan_decision"] = "repair"
            elif verdict_norm == "reject":
                payload["repair_or_replan_decision"] = "repair_or_replan"
            else:
                payload["repair_or_replan_decision"] = "repair_or_replan"
    # When the LLM blocks via prose only (no enumerated hit lists), synthesise
    # a single primary hit from the rationale so the ticket compiler can pick a
    # ``primary_hit``.  Without this, ticket compilation returns ``None`` and
    # the desktop recovery path hard-fails even though the LLM clearly flagged
    # a violation.
    if (
        payload["should_block_archive"]
        and not has_hard_hits
        and not payload["missing_required_progressions"]
        and not payload["missing_knowledge_ops"]
    ):
        synth_hit = _extract_primary_hit_from_rationale(
            str(payload.get("rationale") or ""),
        )
        if synth_hit:
            rationale_lower = str(payload.get("rationale") or "").lower()
            if any(token in rationale_lower for token in ("future", "泄露", "未来", "payoff", "提前", "guardrail")):
                payload["future_leak_hits"] = _dedupe(
                    [*(payload.get("future_leak_hits") or []), synth_hit]
                )
            elif any(token in rationale_lower for token in ("forbidden", "禁行", "不允许", "禁止")):
                payload["forbidden_progression_hits"] = _dedupe(
                    [*(payload.get("forbidden_progression_hits") or []), synth_hit]
                )
            else:
                payload["forbidden_progression_hits"] = _dedupe(
                    [*(payload.get("forbidden_progression_hits") or []), synth_hit]
                )
    if payload["should_block_archive"] and payload["repair_or_replan_decision"] == "continue":
        payload["repair_or_replan_decision"] = "repair_or_replan"
    return ContractExecutionReport.model_validate(payload)


def _extract_primary_hit_from_rationale(rationale: str) -> str:
    """Best-effort extraction of a single primary violation phrase from a prose rationale.

    The LLM is asked (in v2.1.4+ prompts) to enumerate hit lists, but older audits
    or smaller models may still return only a prose ``rationale``.  In that case the
    downstream ticket compiler has no anchor; this helper picks the longest
    quoted span or the longest ``「...」`` / ``"..."`` enclosed phrase, falling
    back to the first comma/semicolon-delimited clause.  Returns ``""`` when no
    candidate is suitable.
    """

    import re

    text = str(rationale or "").strip()
    if not text:
        return ""
    for pattern in (r"「([^」]{4,80})」", r"『([^』]{4,80})』"):
        matches = re.findall(pattern, text)
        if matches:
            return max(matches, key=len).strip()
    clauses = [clause.strip() for clause in re.split(r"[；;。]", text) if clause.strip()]
    candidates: list[str] = []
    for clause in clauses:
        for phrase in re.split(r"[，,、\s]+", clause):
            phrase = phrase.strip()
            if len(phrase) >= 4:
                candidates.append(phrase)
    if candidates:
        return max(candidates, key=len)[:120]
    return text[:80]


def _evidence_window(input_data: ContractExecutionAuditInput) -> str:
    candidate_lines = [
        f"- {item.candidate_id}: {item.summary}" for item in input_data.candidates[:30]
    ]
    text = input_data.chapter_text
    if len(text) > 8000:
        text = text[:4000] + "\n...\n" + text[-3000:]
    return "候选状态变化：\n" + "\n".join(candidate_lines) + "\n\n正文窗口：\n" + text


def _searchable_text(input_data: ContractExecutionAuditInput) -> str:
    return "\n".join(
        [
            input_data.chapter_text,
            *_observed_candidate_summaries(input_data.candidates),
        ]
    )


def _observed_candidate_summaries(candidates: list[CandidateStateDelta]) -> list[str]:
    return [str(item.summary or "").strip() for item in candidates if str(item.summary or "").strip()]


def _contains(source: str, needle: str) -> bool:
    text = _compact(source)
    term = _compact(needle)
    if not text or not term or len(term) < 4:
        return False
    if term in text:
        return True
    if len(term) >= 8:
        term_bigrams = {term[i : i + 2] for i in range(len(term) - 1)}
        text_bigrams = {text[i : i + 2] for i in range(len(text) - 1)}
        if term_bigrams:
            overlap = len(term_bigrams & text_bigrams) / len(term_bigrams)
            if overlap >= 0.58:
                return True
    # Clause-level degraded matching: when a long progression description has
    # the majority of its sub-clauses present in the text, treat it as matched
    # rather than completely missing.  This prevents false "missing" verdicts
    # when the core event happened but minor details differ.
    parts = [part for part in re.split(r"[；,，、。/\s]+", str(needle)) if len(part) >= 4]
    if parts:
        hit_count = sum(1 for part in parts if _compact(part) in text)
        if hit_count >= max(1, int(len(parts) * 0.6)):
            return True
    return False


def _compact(value: Any) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:（）()《》〈〉【】\[\]「」『』“”\"'`·—\-]+", "", str(value or ""))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        raw = list(value)
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = []
    return _dedupe([str(item or "").strip() for item in raw if str(item or "").strip()])


def _has_meaningful_items(value: Any) -> bool:
    if isinstance(value, list):
        return any(bool(item) for item in value)
    return bool(_string_list(value))


def _knowledge_op_target_specs(value: Any) -> list[tuple[str, list[str]]]:
    if not isinstance(value, list):
        return []
    targets: list[tuple[str, list[str]]] = []
    for item in value:
        normalized = normalize_knowledge_op(item)
        target = knowledge_op_target_text(normalized)
        if target:
            candidates = _dedupe([target, *knowledge_op_evidence_candidates(normalized)])
            targets.append((target, candidates))
    return targets


def _cognitive_constraint_target_specs(
    value: Any,
    *,
    current_chapter: int,
) -> list[tuple[str, list[str]]]:
    if not isinstance(value, list):
        return []
    targets: list[tuple[str, list[str]]] = []
    for item in value:
        data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(data, dict):
            continue
        cognitive_chapter = _optional_positive_int(data.get("cognitive_chapter"))
        public_reveal_chapter = _optional_positive_int(data.get("public_reveal_chapter"))
        future_anchors = [
            chapter
            for chapter in (cognitive_chapter, public_reveal_chapter)
            if chapter and chapter > current_chapter
        ]
        if not future_anchors:
            continue
        object_text = str(data.get("cognitive_object") or data.get("object") or "").strip()
        claim_text = str(data.get("claim_text") or data.get("claim") or "").strip()
        evidence = str(data.get("evidence") or "").strip()
        claim_id = str(data.get("claim_id") or data.get("constraint_id") or "").strip()
        subjects = _string_list(data.get("cognitive_subjects", []))
        label = object_text or claim_text or evidence or claim_id
        if not label:
            continue
        anchor = min(future_anchors)
        target = f"第{current_chapter}章不得提前确认「{label}」（目标第{anchor}章）"
        candidates = _dedupe(
            [
                label,
                object_text,
                claim_text,
                evidence,
                claim_id,
                *_cognitive_phrase_variants(label),
                *_cognitive_phrase_variants(object_text),
                *_cognitive_phrase_variants(claim_text),
                *subjects,
            ]
        )
        targets.append((target, candidates))
    return targets


def _cognitive_phrase_variants(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    variants: list[str] = []
    reveal_markers = ("无人见过", "没人见过", "从未见过", "无人认得", "没人认得")
    for marker in reveal_markers:
        if marker not in text:
            continue
        prefix, suffix = text.split(marker, 1)
        subject = (prefix or suffix).strip(" ，,；;。:：")
        if subject:
            variants.append(f"{marker}{subject}")
            variants.append(f"{subject}{marker}")
    return variants


def _optional_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _candidate_hit_rows(category: str, values: list[str]) -> list[dict[str, str]]:
    return [
        {
            "category": category,
            "text": item,
            "source": "local_prescreen",
            "authority": "candidate_only",
        }
        for item in _dedupe(values)
    ]


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


__all__ = [
    "ContractExecutionAuditInput",
    "ContractExecutionAuditStep",
    "build_local_contract_execution_report",
    "merge_contract_execution_adjudication",
    "should_run_contract_execution_audit",
]
