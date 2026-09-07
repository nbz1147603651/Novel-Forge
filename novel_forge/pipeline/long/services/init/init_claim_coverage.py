"""Claim-to-contract coverage audit for long initialization.

This module keeps init-time Claims as an audit layer: Claims prove that the
final chapter contracts absorbed the important plan facts, but they are never
fed into chapter runtime prompts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from novel_forge.core.schemas.init_coherence import CoherenceClaim
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    CLAIM_LEDGER_JSON,
    init_coherence_artifact_hashes,
)

CLAIM_CONTRACT_COVERAGE_REPORT = "init_claim_contract_coverage.json"

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_CHAPTER_RANGE_DEFER_THRESHOLD = 12

_P0_TYPES = {"event", "state", "world_rule", "knowledge", "dependency", "relationship"}
_P1_TYPES = {"payoff", "promise"}
_SOFT_TYPES = {"other"}

# Every contract field the audit may consult when a claim was extracted
# from the contract itself. Kept as a module-level constant so the safety
# net cannot drift from the type-specific match list.
_ALL_COVERAGE_FIELDS: tuple[str, ...] = (
    "entry_state_requirements",
    "required_events",
    "allowed_changes",
    "forbidden_changes",
    "promise_ops",
    "relationship_ops",
    "item_ops",
    "knowledge_ops",
    "cognitive_constraints",
    "new_character_candidates",
    "exit_state_targets",
    "required_progressions",
    "allowed_progressions",
    "forbidden_progressions",
    "completion_criteria",
    "future_leak_risks",
)

_MATCH_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "event": (
        "required_events",
        "entry_state_requirements",
        "exit_state_targets",
        "hard_facts",
        "forbidden_changes",
        "completion_criteria",
    ),
    "state": (
        "entry_state_requirements",
        "exit_state_targets",
        "required_events",
        "hard_facts",
        "forbidden_changes",
        "completion_criteria",
    ),
    "knowledge": (
        "required_events",
        "entry_state_requirements",
        "exit_state_targets",
        "hard_facts",
        "completion_criteria",
    ),
    "world_rule": ("world_rules", "hard_facts", "forbidden_changes", "guard_constraints"),
    "dependency": (
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "future_leak_risks",
    ),
    "relationship": (
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "relationship_ops",
    ),
    "payoff": (
        "completion_criteria",
        "required_progressions",
        "future_leak_risks",
        "expected_payoffs",
        "promise_ops",
    ),
    "promise": (
        "completion_criteria",
        "required_progressions",
        "future_leak_risks",
        "expected_payoffs",
        "promise_ops",
    ),
}


def run_init_claim_contract_coverage_audit(
    ctx: Any,
    *,
    artifacts: dict[str, dict[str, Any]],
    chapter_contracts: dict[str, Any],
) -> dict[str, Any]:
    """Build, persist, and emit the init Claim-to-contract coverage report."""
    enabled = bool(getattr(ctx.settings, "init_claim_coverage_enabled", True))
    if not enabled:
        report = {
            "schema_version": 1,
            "report_type": "init_claim_contract_coverage",
            "verdict": "accept",
            "enabled": False,
            "degraded": False,
            "summary": "Claim-to-Contract 覆盖审计已关闭。",
            "total_claims": 0,
            "covered_claims": 0,
            "uncovered_claims": 0,
            "uncovered_p0_p1": 0,
            "items": [],
            "issues": [],
            "blocked": False,
        }
        _persist_report(ctx, report)
        return report

    ledger_path = ctx.layout.memory_dir / CLAIM_LEDGER_JSON
    block_degraded = bool(getattr(ctx.settings, "init_claim_coverage_block_degraded", True))
    if not ctx.storage.exists(ledger_path):
        report = _degraded_report(
            "缺少 Claims 账本，无法确认章节契约已覆盖初始化一致性 Claims。",
            reason="missing_claim_ledger",
            blocked=block_degraded,
        )
        _persist_report(ctx, report)
        return report

    try:
        ledger = ctx.storage.load_json(ledger_path)
    except Exception as exc:
        report = _degraded_report(
            f"一致性 Claims 账本读取失败：{exc}",
            reason="claim_ledger_unreadable",
            blocked=block_degraded,
        )
        _persist_report(ctx, report)
        return report

    if not isinstance(ledger, dict):
        report = _degraded_report(
            "一致性 Claims 账本格式无效，无法确认章节契约覆盖。",
            reason="claim_ledger_invalid",
            blocked=block_degraded,
        )
        _persist_report(ctx, report)
        return report

    artifact_hashes = init_coherence_artifact_hashes(artifacts)
    # The outline's declared commitment frontier is authoritative. Never infer
    # this from the contracts present: doing so would hide missing current rows.
    outline = artifacts.get("outline") or {}
    hard_through = _positive_int(outline.get("hard_through_chapter"))
    total_chapters = _positive_int(outline.get("total_chapters"))
    report = build_claim_contract_coverage_report(
        ledger,
        chapter_contracts,
        artifact_hashes=artifact_hashes,
        block_p0=bool(getattr(ctx.settings, "init_claim_coverage_block_p0", True)),
        block_p1=bool(getattr(ctx.settings, "init_claim_coverage_block_p1", False)),
        hard_through_chapter=(hard_through if 0 < hard_through < total_chapters else None),
    )
    stale_claims = _positive_int(report.get("stale_claims", 0))
    if (
        block_degraded
        and artifact_hashes
        and _positive_int(report.get("total_claims", 0)) == 0
        and stale_claims > 0
    ):
        report = _degraded_report(
            "一致性 Claims 账本与当前初始化 artifacts 不匹配，无法确认章节契约覆盖。",
            reason="claim_ledger_artifact_hash_mismatch",
            blocked=True,
        )
        report["stale_claims"] = stale_claims
    _persist_report(ctx, report)
    return report


def build_claim_contract_coverage_report(
    ledger: dict[str, Any],
    chapter_contracts: dict[str, Any],
    *,
    artifact_hashes: dict[str, str] | None = None,
    block_p0: bool = True,
    block_p1: bool = False,
    hard_through_chapter: int | None = None,
) -> dict[str, Any]:
    """Return a deterministic report showing which active Claims reached contracts."""
    artifact_hashes = {str(key): str(value) for key, value in (artifact_hashes or {}).items()}
    active_claims, stale_count = _load_active_claims(ledger, artifact_hashes=artifact_hashes)
    contracts_by_chapter = _contracts_by_chapter(chapter_contracts)

    items: list[dict[str, Any]] = []
    for claim in active_claims:
        items.append(
            _audit_claim(claim, contracts_by_chapter, hard_through_chapter=hard_through_chapter)
        )

    covered = sum(1 for item in items if item["coverage_status"] == "covered")
    uncovered = sum(1 for item in items if item["coverage_status"] == "uncovered")
    deferred = sum(1 for item in items if item["coverage_status"] == "deferred")
    uncovered_p0_p1_items = [
        item
        for item in items
        if item["coverage_status"] == "uncovered" and item["priority"] in {"P0", "P1"}
    ]
    block_items = [
        item
        for item in uncovered_p0_p1_items
        if (item["priority"] == "P0" and block_p0) or (item["priority"] == "P1" and block_p1)
    ]
    issues = [_coverage_issue(item, blocked=item in block_items) for item in uncovered_p0_p1_items]
    blocked = bool(block_items)
    verdict = "needs_repair" if blocked else ("warn" if uncovered_p0_p1_items else "accept")

    summary = _coverage_summary(
        verdict=verdict,
        total=len(items),
        covered=covered,
        uncovered=uncovered,
        uncovered_p0_p1=len(uncovered_p0_p1_items),
        stale_count=stale_count,
    )
    if hard_through_chapter is not None:
        summary += (
            f" 当前正式契约范围为第 1–{hard_through_chapter} 章，"
            f"延后审计 {deferred} 条；未来章节要求仍保留在 Claims 账本中。"
        )
    return {
        "schema_version": 1,
        "report_type": "init_claim_contract_coverage",
        "verdict": verdict,
        "enabled": True,
        "degraded": False,
        "blocked": blocked,
        "summary": summary,
        "total_claims": len(items),
        "covered_claims": covered,
        "uncovered_claims": uncovered,
        "uncovered_p0_p1": len(uncovered_p0_p1_items),
        "stale_claims": stale_count,
        "deferred_claims": deferred,
        "hard_through_chapter": hard_through_chapter,
        "block_p0": block_p0,
        "block_p1": block_p1,
        "items": items,
        "issues": issues,
    }


def _persist_report(ctx: Any, report: dict[str, Any]) -> None:
    ctx.storage.save_json(ctx.layout.reports_dir / CLAIM_CONTRACT_COVERAGE_REPORT, report)
    callback = getattr(ctx, "on_step", None)
    if callable(callback):
        callback(
            "init_claim_contract_coverage",
            {
                "verdict": report.get("verdict", "accept"),
                "summary": report.get("summary", ""),
                "blocked": bool(report.get("blocked", False)),
                "total_claims": int(report.get("total_claims", 0) or 0),
                "covered_claims": int(report.get("covered_claims", 0) or 0),
                "uncovered_claims": int(report.get("uncovered_claims", 0) or 0),
                "uncovered_p0_p1": int(report.get("uncovered_p0_p1", 0) or 0),
                "degraded": bool(report.get("degraded", False)),
                "deferred_claims": int(report.get("deferred_claims", 0) or 0),
                "hard_through_chapter": report.get("hard_through_chapter"),
            },
        )


def _degraded_report(
    summary: str,
    *,
    reason: str,
    blocked: bool,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "id": f"claim_contract_coverage_{reason}",
        "severity": "critical" if blocked else "warning",
        "type": "claim_contract_coverage_degraded",
        "description": summary,
        "resolution": "重跑初始化一致性 claims 抽取/裁判，重建 memory/init_coherence_claim_ledger.json。",
        "repair_scope": [],
        "degraded_reason": reason,
    }
    return {
        "schema_version": 1,
        "report_type": "init_claim_contract_coverage",
        "verdict": "needs_repair" if blocked else "accept",
        "enabled": True,
        "degraded": True,
        "degraded_reason": reason,
        "blocked": blocked,
        "summary": summary,
        "total_claims": 0,
        "covered_claims": 0,
        "uncovered_claims": 0,
        "uncovered_p0_p1": 0,
        "stale_claims": 0,
        "items": [],
        "issues": [issue] if blocked else [],
    }


def _load_active_claims(
    ledger: dict[str, Any],
    *,
    artifact_hashes: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    claims_by_id = ledger.get("claims_by_id")
    if not isinstance(claims_by_id, dict):
        return [], 0
    active_ids = [
        str(claim_id)
        for claim_id in (ledger.get("active_claim_ids") or [])
        if isinstance(claim_id, str)
    ]
    if not active_ids:
        active_ids = [
            str(claim_id)
            for claim_id, entry in claims_by_id.items()
            if isinstance(claim_id, str)
            and isinstance(entry, dict)
            and entry.get("status") == "active"
        ]

    claims: list[dict[str, Any]] = []
    stale_count = 0
    for claim_id in active_ids:
        entry = claims_by_id.get(claim_id)
        if not isinstance(entry, dict) or entry.get("status") != "active":
            continue
        artifact = str(entry.get("artifact") or "")
        if artifact_hashes and artifact not in artifact_hashes:
            stale_count += 1
            continue
        expected_hash = artifact_hashes.get(artifact)
        if expected_hash is not None:
            recorded_hash = str(entry.get("artifact_hash") or "")
            if recorded_hash and recorded_hash != expected_hash:
                stale_count += 1
                continue
        try:
            claim = CoherenceClaim.model_validate(entry)
        except Exception:
            stale_count += 1
            continue
        claims.append({**entry, **claim.model_dump(mode="json")})
    return claims, stale_count


def _contracts_by_chapter(chapter_contracts: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw_items = chapter_contracts.get("chapter_contracts")
    if not isinstance(raw_items, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        chapter_number = _positive_int(item.get("chapter_number"))
        if chapter_number:
            result[chapter_number] = item
    return result


def _audit_claim(
    claim: dict[str, Any],
    contracts_by_chapter: dict[int, dict[str, Any]],
    *,
    hard_through_chapter: int | None = None,
) -> dict[str, Any]:
    claim_id = str(claim.get("claim_id") or "")
    claim_type = str(claim.get("claim_type") or "other").strip().lower() or "other"
    priority = _claim_priority(claim, claim_type=claim_type)
    chapter_scope, deferred_reason = _claim_chapter_scope(claim)
    deferred_chapters: list[int] = []
    if hard_through_chapter is not None and hard_through_chapter > 0:
        # Broad-span heuristics must not hide explicit current obligations
        # when one Claim also names a distant reveal/payoff chapter.
        anchors = (
            _positive_ints(claim.get("cognitive_chapter"))
            + _positive_ints(claim.get("public_reveal_chapter"))
            + _positive_ints(claim.get("foreshadow_chapters"))
        )
        explicit = list(dict.fromkeys(_positive_ints(claim.get("chapter_numbers")) + anchors))
        # Range endpoints alone can still describe a macro arc. A concrete
        # cognition/reveal/foreshadow anchor, however, must not disappear just
        # because its paired reveal is far away.
        if explicit and (not deferred_reason or anchors):
            chapter_scope, deferred_reason = explicit, ""
        deferred_chapters = [n for n in chapter_scope if n > hard_through_chapter]
        chapter_scope = [n for n in chapter_scope if n <= hard_through_chapter]
        if deferred_chapters and not chapter_scope:
            deferred_reason = (
                f"Claim 位于正式契约边界第 {hard_through_chapter} 章之后；"
                "保留未来要求，待对应章节转为正式契约时重新审计。"
            )
    metadata = claim.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    base = {
        "claim_id": claim_id,
        "stage": str(claim.get("stream_finalized_stage") or claim.get("stage") or ""),
        "artifact": str(claim.get("artifact") or ""),
        "source_path": str(claim.get("source_path") or ""),
        "claim_type": claim_type,
        "priority": priority,
        "chapter_scope": chapter_scope,
        "deferred_chapters": deferred_chapters,
        "claim_text": str(claim.get("claim_text") or ""),
        "evidence": str(claim.get("evidence") or ""),
        "cognitive_subjects": _string_list(claim.get("cognitive_subjects")),
        "cognitive_object": str(claim.get("cognitive_object") or ""),
        "cognitive_level": str(claim.get("cognitive_level") or "unaware"),
        "action_level": str(claim.get("action_level") or "none"),
        "reader_awareness": str(claim.get("reader_awareness") or "unknown"),
        "character_knowledge_coverage": _string_dict(claim.get("character_knowledge_coverage")),
        "cognitive_chapter": _positive_int(claim.get("cognitive_chapter")) or None,
        "public_reveal_chapter": _positive_int(claim.get("public_reveal_chapter")) or None,
        "foreshadow_chapters": _positive_ints(claim.get("foreshadow_chapters")),
        "entity_adjudication_status": str(metadata.get("entity_adjudication_status") or ""),
    }
    if claim_type in _SOFT_TYPES:
        return {
            **base,
            "coverage_status": "not_applicable",
            "matched_contract_refs": [],
            "reason": "P2/other Claim 仅用于可视化，不要求进入章节契约。",
        }
    if deferred_reason:
        return {
            **base,
            "coverage_status": "deferred",
            "matched_contract_refs": [],
            "reason": deferred_reason,
        }

    fields = _fields_for_claim_type(claim_type)
    matched_refs: list[dict[str, Any]] = []
    cognitive_refs: list[dict[str, Any]] = []
    for chapter_number in chapter_scope:
        contract = contracts_by_chapter.get(chapter_number)
        if not contract:
            continue
        matched_refs.extend(_match_contract_fields(claim, contract, fields=fields))
        cognitive_refs.extend(_match_cognitive_constraints(claim, contract))
    requires_strict_cognitive = _claim_requires_cognitive_coverage(claim)
    cognitive_gaps = _cognitive_coverage_gaps(claim, cognitive_refs)
    has_cognitive_gaps = bool(cognitive_gaps)

    if requires_strict_cognitive and cognitive_refs and not has_cognitive_gaps:
        return {
            **base,
            "coverage_status": "covered",
            "matched_contract_refs": [*cognitive_refs, *matched_refs][:8],
            "reason": "Claim 已被目标章节契约的 cognitive_constraints 覆盖。",
        }
    if matched_refs and not requires_strict_cognitive:
        return {
            **base,
            "coverage_status": "covered",
            "matched_contract_refs": matched_refs[:8],
            "reason": "Claim 已被目标章节契约字段覆盖。",
        }

    # Midstream safety net #1: claims extracted from the chapter_contracts
    # artifact itself come from text that already lives in the contract.
    # If their subject/object appears in any non-cognitive field of the
    # same contract, count them as covered so the audit doesn't punish
    # legitimate placements in `required_events`, `entry_state_requirements`,
    # etc.
    if str(claim.get("artifact") or "") == "chapter_contracts":
        extracted_refs: list[dict[str, Any]] = []
        for chapter_number in chapter_scope:
            contract = contracts_by_chapter.get(chapter_number)
            if not contract:
                continue
            extracted_refs.extend(
                _match_contract_fields(claim, contract, fields=_ALL_COVERAGE_FIELDS)
            )
        if extracted_refs:
            return {
                **base,
                "coverage_status": "covered",
                "matched_contract_refs": extracted_refs[:8],
                "reason": ("Claim 由 chapter_contracts 自身抽取，文本已存在于章节契约字段。"),
            }

    return {
        **base,
        "coverage_status": "uncovered",
        "matched_contract_refs": [],
        "reason": _claim_coverage_reason(
            matched_refs=matched_refs,
            has_cognitive_gaps=has_cognitive_gaps,
            cognitive_gaps=cognitive_gaps,
        ),
        "cognitive_coverage_gaps": cognitive_gaps,
        "suggested_contract_fields": list(fields)
        + (["cognitive_constraints"] if requires_strict_cognitive else []),
        "requires_cognitive_coverage": requires_strict_cognitive,
    }


def _claim_priority(claim: dict[str, Any], *, claim_type: str) -> str:
    explicit = (
        str(
            claim.get("priority")
            or claim.get("authority")
            or _metadata(claim).get("priority")
            or _metadata(claim).get("authority")
            or ""
        )
        .strip()
        .upper()
    )
    if explicit in {"P0", "P1", "P2"}:
        return explicit
    risk = (
        str(
            claim.get("risk_level")
            or claim.get("severity")
            or _metadata(claim).get("risk_level")
            or _metadata(claim).get("severity")
            or ""
        )
        .strip()
        .lower()
    )
    if risk in {"critical", "high"}:
        return "P0"
    if bool(claim.get("irreversible")):
        return "P0"
    if claim_type in _P1_TYPES:
        return "P1"
    if claim_type in _P0_TYPES:
        return "P1"
    return "P2"


def _claim_chapter_scope(claim: dict[str, Any]) -> tuple[list[int], str]:
    raw_range = claim.get("chapter_range")
    if isinstance(raw_range, dict):
        start = _positive_int(raw_range.get("start"))
        end = _positive_int(raw_range.get("end"))
        if start and end and end >= start:
            if end - start + 1 > _CHAPTER_RANGE_DEFER_THRESHOLD:
                return (
                    [],
                    f"Claim 跨越章节范围过宽(>={_CHAPTER_RANGE_DEFER_THRESHOLD}),按宏观计划事实延后到契约审计外处理。",
                )
    numbers = _positive_ints(claim.get("chapter_numbers"))
    numbers.extend(_positive_ints(claim.get("cognitive_chapter")))
    numbers.extend(_positive_ints(claim.get("public_reveal_chapter")))
    numbers.extend(_positive_ints(claim.get("foreshadow_chapters")))
    numbers = list(dict.fromkeys(numbers))
    if numbers:
        return numbers, ""
    if isinstance(raw_range, dict):
        start = _positive_int(raw_range.get("start"))
        end = _positive_int(raw_range.get("end"))
        if start and end and end >= start:
            if end - start + 1 <= _CHAPTER_RANGE_DEFER_THRESHOLD:
                return list(range(start, end + 1)), ""
    return [], "Claim 缺少明确章节范围,按宏观计划事实 deferred,不强行塞入单章契约。"


def _fields_for_claim_type(claim_type: str) -> tuple[str, ...]:
    return _MATCH_FIELDS_BY_TYPE.get(claim_type, ("required_events", "completion_criteria"))


def _match_contract_fields(
    claim: dict[str, Any],
    contract: dict[str, Any],
    *,
    fields: Iterable[str],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    chapter_number = _positive_int(contract.get("chapter_number"))
    for field in fields:
        for text in _flatten_contract_text(contract.get(field)):
            if _texts_match(claim, text):
                refs.append({"chapter_number": chapter_number, "field": field, "text": text})
    return refs


def _match_cognitive_constraints(
    claim: dict[str, Any],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    chapter_number = _positive_int(contract.get("chapter_number"))
    claim_id = str(claim.get("claim_id") or "")
    for raw_item in contract.get("cognitive_constraints") or []:
        if not isinstance(raw_item, dict):
            continue
        if claim_id and str(raw_item.get("claim_id") or "") == claim_id:
            refs.append(
                {
                    "chapter_number": chapter_number,
                    "field": "cognitive_constraints",
                    "text": str(
                        raw_item.get("claim_text") or raw_item.get("cognitive_object") or ""
                    ),
                    "constraint": raw_item,
                    "match": "claim_id",
                }
            )
            continue
        if _cognitive_constraint_structurally_matches_claim(claim, raw_item):
            refs.append(
                {
                    "chapter_number": chapter_number,
                    "field": "cognitive_constraints",
                    "text": str(
                        raw_item.get("claim_text") or raw_item.get("cognitive_object") or ""
                    ),
                    "constraint": raw_item,
                    "match": "structure",
                }
            )
    return refs


def _cognitive_constraint_structurally_matches_claim(
    claim: dict[str, Any],
    constraint: dict[str, Any],
) -> bool:
    claim_object = _normalize_match_text(
        str(claim.get("cognitive_object") or claim.get("claim_text") or "")
    )
    constraint_object = _normalize_match_text(
        str(constraint.get("cognitive_object") or constraint.get("claim_text") or "")
    )
    if claim_object and not constraint_object:
        return False
    if (
        claim_object
        and constraint_object
        and claim_object not in constraint_object
        and constraint_object not in claim_object
    ):
        return False
    claim_subjects = {
        _normalize_match_text(subject) for subject in _explicit_cognitive_subjects(claim)
    }
    constraint_subjects = {
        _normalize_match_text(subject)
        for subject in _contract_list(constraint.get("cognitive_subjects"))
        if _normalize_match_text(subject)
    }
    if claim_subjects:
        if not constraint_subjects or not (claim_subjects & constraint_subjects):
            return False
    for key in ("cognitive_level", "action_level", "reader_awareness"):
        claim_value = str(claim.get(key) or "").strip().lower()
        constraint_value = str(constraint.get(key) or "").strip().lower()
        if _requires_cognitive_field_match(key, claim_value):
            if not constraint_value or claim_value != constraint_value:
                return False
        elif claim_value and constraint_value and claim_value != constraint_value:
            return False
    for key in ("cognitive_chapter", "public_reveal_chapter"):
        claim_number = _positive_int(claim.get(key))
        constraint_number = _positive_int(constraint.get(key))
        if claim_number and (not constraint_number or claim_number != constraint_number):
            return False
    return True


def _cognitive_constraint_matches_anchored_claim(
    claim: dict[str, Any],
    constraint: dict[str, Any],
) -> bool:
    """Match a stable claim anchor while tolerating canonical-name drift.

    Entity adjudication can rewrite an object's alias between two ledger
    snapshots without changing the claim's identity. An exact ``claim_id`` is
    authoritative for that object identity, but subjects, cognition/action
    levels, awareness, and chapter anchors must still match structurally.
    """

    claim_id = str(claim.get("claim_id") or "")
    if not claim_id or str(constraint.get("claim_id") or "") != claim_id:
        return False
    claim_object = str(claim.get("cognitive_object") or claim.get("claim_text") or "").strip()
    constraint_object = str(
        constraint.get("cognitive_object") or constraint.get("claim_text") or ""
    ).strip()
    if claim_object and not constraint_object:
        return False
    identity_aligned = {
        **constraint,
        "cognitive_object": claim_object,
    }
    return _cognitive_constraint_structurally_matches_claim(claim, identity_aligned)


def _texts_match(claim: dict[str, Any], candidate: str) -> bool:
    candidate_norm = _normalize_match_text(candidate)
    if not candidate_norm:
        return False
    claim_text = str(claim.get("claim_text") or "")
    evidence = str(claim.get("evidence") or "")
    parts = [
        claim_text,
        evidence,
        str(claim.get("subject_text") or ""),
        str(claim.get("state_after") or ""),
        str(claim.get("state_before") or ""),
        str(claim.get("axis") or ""),
        str(claim.get("event_type") or ""),
        str(claim.get("payoff_id") or ""),
        str(claim.get("payoff_kind") or ""),
    ]
    for part in parts:
        part_norm = _normalize_match_text(part)
        if len(part_norm) >= 4 and (part_norm in candidate_norm or candidate_norm in part_norm):
            return True
    claim_norm = _normalize_match_text(" ".join(parts[:2]))
    return _bigram_similarity(claim_norm, candidate_norm) >= 0.34


def _flatten_contract_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_contract_text(item))
        return result
    if isinstance(value, dict):
        dict_result: list[str] = []
        for nested in value.values():
            dict_result.extend(_flatten_contract_text(nested))
        return dict_result
    return []


def _contract_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    result: list[str] = []
    for item in _contract_list(value):
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        text = str(item or "").strip()
        if text:
            result[str(key)] = text
    return result


def _normalize_match_text(value: str) -> str:
    return _PUNCT_RE.sub("", str(value or "").lower())


def _bigram_similarity(left: str, right: str) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_bigrams = {left[index : index + 2] for index in range(len(left) - 1)}
    right_bigrams = {right[index : index + 2] for index in range(len(right) - 1)}
    denominator = min(len(left_bigrams), len(right_bigrams))
    if not denominator:
        return 0.0
    return len(left_bigrams & right_bigrams) / denominator


def _coverage_issue(item: dict[str, Any], *, blocked: bool) -> dict[str, Any]:
    claim_id = str(item.get("claim_id") or "")
    priority = str(item.get("priority") or "P1")
    severity = "high" if blocked else "medium"
    fields = item.get("suggested_contract_fields") or _fields_for_claim_type(
        str(item.get("claim_type") or "other")
    )
    reason_suffix = ""
    cognitive_gaps = item.get("cognitive_coverage_gaps") or []
    if cognitive_gaps:
        reason_suffix = f"认知契约缺口：{', '.join(cognitive_gaps)}。"
    return {
        "id": f"claim_contract_coverage_{claim_id}",
        "severity": severity,
        "description": (
            f"{priority} Claim 未被最终章节契约覆盖："
            f"{str(item.get('claim_text') or claim_id)[:120]}。"
            f"{reason_suffix}"
        ),
        "claim_id": claim_id,
        "claim_type": item.get("claim_type"),
        "priority": priority,
        "claim_snapshot": _claim_snapshot_for_repair(item),
        "repair_scope": [
            {
                "artifact": "chapter_contracts",
                "chapters": item.get("chapter_scope") or [],
                "fields": list(
                    dict.fromkeys(
                        [
                            *list(fields)[:8],
                            *(["cognitive_constraints", "knowledge_ops"] if cognitive_gaps else []),
                        ]
                    )
                ),
                "issue_ids": [f"claim_contract_coverage_{claim_id}"],
            }
        ],
    }


def _claim_snapshot_for_repair(item: dict[str, Any]) -> dict[str, Any]:
    """Return the claim fields needed to hydrate a cognitive constraint."""

    return {
        "claim_id": str(item.get("claim_id") or ""),
        "claim_type": str(item.get("claim_type") or ""),
        "artifact": str(item.get("artifact") or ""),
        "source_path": str(item.get("source_path") or ""),
        "claim_text": str(item.get("claim_text") or ""),
        "evidence": str(item.get("evidence") or ""),
        "cognitive_subjects": _string_list(item.get("cognitive_subjects")),
        "cognitive_object": str(item.get("cognitive_object") or ""),
        "cognitive_level": str(item.get("cognitive_level") or "unaware"),
        "action_level": str(item.get("action_level") or "none"),
        "reader_awareness": str(item.get("reader_awareness") or "unknown"),
        "character_knowledge_coverage": _string_dict(item.get("character_knowledge_coverage")),
        "chapter_scope": _positive_ints(item.get("chapter_scope")),
        "chapter_numbers": _positive_ints(item.get("chapter_numbers") or item.get("chapter_scope")),
        "cognitive_chapter": _positive_int(item.get("cognitive_chapter")) or None,
        "public_reveal_chapter": _positive_int(item.get("public_reveal_chapter")) or None,
        "foreshadow_chapters": _positive_ints(item.get("foreshadow_chapters")),
        "entity_adjudication_status": str(item.get("entity_adjudication_status") or ""),
    }


def _claim_requires_cognitive_coverage(claim: dict[str, Any]) -> bool:
    claim_type = str(claim.get("claim_type") or "other").strip().lower()
    if claim_type in _SOFT_TYPES:
        return False
    cognitive_subjects = _explicit_cognitive_subjects(claim)
    cognitive_object = str(claim.get("cognitive_object") or "").strip()
    cognitive_level = str(claim.get("cognitive_level") or "").strip().lower()
    action_level = str(claim.get("action_level") or "").strip().lower()
    reader_awareness = str(claim.get("reader_awareness") or "").strip().lower()
    return bool(
        cognitive_subjects
        or cognitive_object
        or cognitive_level in {"confirmed", "acknowledged", "partial", "suspicion", "subconscious"}
        or action_level in {"internal", "hinted", "revealed", "acted"}
        or reader_awareness in {"partial", "full"}
        or _non_default_character_knowledge_coverage(claim)
        or bool(_positive_int(claim.get("cognitive_chapter")))
        or bool(_positive_int(claim.get("public_reveal_chapter")))
        or bool(_positive_ints(claim.get("foreshadow_chapters")))
    )


def _explicit_cognitive_subjects(claim: dict[str, Any]) -> list[str]:
    subject_values: list[Any] = []
    raw_cognitive_subjects = claim.get("cognitive_subjects")
    if isinstance(raw_cognitive_subjects, list):
        subject_values.extend(raw_cognitive_subjects)
    elif raw_cognitive_subjects is not None:
        subject_values.append(raw_cognitive_subjects)
    subjects: list[str] = []
    for subject in subject_values:
        text = str(subject or "").strip()
        if not text:
            continue
        if text not in subjects:
            subjects.append(text)
    return subjects


def _requires_cognitive_field_match(key: str, value: str) -> bool:
    defaults = {
        "cognitive_level": "unaware",
        "action_level": "none",
        "reader_awareness": "unknown",
    }
    return bool(value and value != defaults.get(key, ""))


def _non_default_character_knowledge_coverage(claim: dict[str, Any]) -> bool:
    awareness = claim.get("character_knowledge_coverage")
    if not isinstance(awareness, dict):
        return False
    for value in awareness.values():
        text = str(value or "").strip().lower()
        if text in {"partial", "full"}:
            return True
    return False


def _cognitive_coverage_gaps(claim: dict[str, Any], refs: list[dict[str, Any]]) -> list[str]:
    if not refs:
        if _claim_requires_cognitive_coverage(claim):
            return ["未写入章节契约 cognitive_constraints。"]
        return []  # 非认知 claim,cognitive_constraints 缺失与覆盖无关
    gaps: list[str] = []
    constraints: list[dict[str, Any]] = []
    for ref in refs:
        constraint = ref.get("constraint")
        if isinstance(constraint, dict):
            constraints.append(constraint)
    if not constraints:
        return ["cognitive_constraints 结构为空。"]

    if any(_cognitive_constraint_matches_anchored_claim(claim, item) for item in constraints):
        return []

    for ref in refs:
        constraint = ref.get("constraint")
        if (
            str(ref.get("match") or "") == "structure"
            and isinstance(constraint, dict)
            and _cognitive_constraint_structurally_matches_claim(claim, constraint)
        ):
            return []

    if not any(
        str(item.get("claim_id") or "") == str(claim.get("claim_id") or "") for item in constraints
    ):
        gaps.append("cognitive_constraints 未保留 claim_id 精确锚点。")
    if not any(
        _cognitive_constraint_structurally_matches_claim(claim, item) for item in constraints
    ):
        gaps.append("cognitive_constraints 与 Claim 的主体/对象/层级/时间锚不一致。")
    return list(dict.fromkeys(gaps))


def _claim_coverage_reason(
    *,
    matched_refs: list[dict[str, Any]],
    has_cognitive_gaps: bool,
    cognitive_gaps: list[str],
) -> str:
    if has_cognitive_gaps:
        prefix = "契约命中文本，但认知语义未完成约束：" if matched_refs else "认知语义未完成约束："
        return prefix + "；".join(cognitive_gaps)
    if not matched_refs:
        return "未在目标章节契约字段中找到足够相近的执行约束。"
    return "未在目标章节契约字段中找到足够相近的执行约束。"


def _coverage_summary(
    *,
    verdict: str,
    total: int,
    covered: int,
    uncovered: int,
    uncovered_p0_p1: int,
    stale_count: int,
) -> str:
    if not total and stale_count:
        return f"Claim-to-Contract 覆盖审计完成：无可用活跃 Claims，过期/无效 {stale_count} 条。"
    prefix = {
        "accept": "Claim-to-Contract 覆盖审计通过",
        "warn": "Claim-to-Contract 覆盖审计存在提醒",
        "needs_repair": "Claim-to-Contract 覆盖审计要求修复章节契约",
    }.get(verdict, "Claim-to-Contract 覆盖审计完成")
    return (
        f"{prefix}：Claims {total} 条，覆盖 {covered} 条，未覆盖 {uncovered} 条，"
        f"P0/P1 未覆盖 {uncovered_p0_p1} 条，过期/无效 {stale_count} 条。"
    )


def _metadata(claim: dict[str, Any]) -> dict[str, Any]:
    metadata = claim.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _positive_ints(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [value]
    result: list[int] = []
    for item in values:
        number = _positive_int(item)
        if number and number not in result:
            result.append(number)
    return sorted(result)


def _local_cognitive_backfill(
    chapter_contracts: dict[str, Any],
    coverage_report: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically inject uncovered P0/P1 claims as cognitive_constraints
    into the claim's concrete chapter scope.

    Returns a new chapter_contracts dict (deep-copied). Returns the same
    reference (no-op) when no actionable issues exist.

    If the coverage item carries an explicit ``chapter_scope``, every existing
    chapter in that scope is hydrated. When old reports only provide a broad
    ``repair_scope.chapters`` endpoint pair, fall back to the first existing
    scope chapter instead of treating endpoints as independent targets.
    """
    issues = coverage_report.get("issues") or []
    if not issues:
        return chapter_contracts
    items_by_claim_id = {
        str(item.get("claim_id") or ""): item
        for item in coverage_report.get("items") or []
        if isinstance(item, dict) and str(item.get("claim_id") or "")
    }

    result: dict[str, Any] = {
        **chapter_contracts,
        "chapter_contracts": [{**c} for c in chapter_contracts.get("chapter_contracts", [])],
    }
    contracts_by_chapter: dict[int, dict[str, Any]] = {}
    for c in result["chapter_contracts"]:
        if isinstance(c, dict) and c.get("chapter_number"):
            contracts_by_chapter[int(c["chapter_number"])] = c

    changed = False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        repair_scopes = issue.get("repair_scope") or []
        if not repair_scopes:
            continue
        scope = repair_scopes[0] if isinstance(repair_scopes[0], dict) else {}
        chapters = scope.get("chapters") or []
        if not chapters:
            continue
        claim_id = issue.get("claim_id")
        if not claim_id:
            continue
        raw_snapshot = issue.get("claim_snapshot")
        item_source = items_by_claim_id.get(str(claim_id), {})
        claim_source = dict(item_source)
        if isinstance(raw_snapshot, dict):
            claim_source.update(raw_snapshot)
        target_chapters = _cognitive_backfill_target_chapters(
            claim_source=claim_source,
            scope_chapters=chapters,
            contracts_by_chapter=contracts_by_chapter,
        )
        for target_chapter in target_chapters:
            contract = contracts_by_chapter.get(target_chapter)
            if not isinstance(contract, dict):
                continue
            new_constraint = _constraint_from_claim_source(
                claim_id=str(claim_id),
                claim_source=claim_source,
                issue=issue,
                target_chapter=target_chapter,
            )
            existing = contract.get("cognitive_constraints") or []
            if not isinstance(existing, list):
                existing = []
            updated_constraints = [*existing]
            replaced = False
            for index, current in enumerate(updated_constraints):
                if not isinstance(current, dict) or str(current.get("claim_id") or "") != claim_id:
                    continue
                merged = {**current, **new_constraint}
                if merged != current:
                    updated_constraints[index] = merged
                    changed = True
                replaced = True
                break
            if not replaced:
                updated_constraints.append(new_constraint)
                changed = True
            contract["cognitive_constraints"] = updated_constraints

    return result if changed else chapter_contracts


def _cognitive_backfill_target_chapters(
    *,
    claim_source: dict[str, Any],
    scope_chapters: Any,
    contracts_by_chapter: dict[int, dict[str, Any]],
) -> list[int]:
    claim_scope = _positive_ints(claim_source.get("chapter_scope")) or _positive_ints(
        claim_source.get("chapter_numbers")
    )
    if not claim_scope:
        claim_scope = _positive_ints(
            [
                claim_source.get("cognitive_chapter"),
                claim_source.get("public_reveal_chapter"),
            ]
        )
    target_chapters = [chapter for chapter in claim_scope if chapter in contracts_by_chapter]
    if target_chapters:
        return target_chapters

    fallback_scope = _positive_ints(scope_chapters)
    for chapter in fallback_scope:
        if chapter in contracts_by_chapter:
            return [chapter]
    return []


def _constraint_from_claim_source(
    *,
    claim_id: str,
    claim_source: dict[str, Any],
    issue: dict[str, Any],
    target_chapter: int,
) -> dict[str, Any]:
    claim_text = _compact_constraint_text(
        claim_source.get("claim_text") or issue.get("description") or claim_id,
        limit=200,
    )
    evidence = _compact_constraint_text(
        claim_source.get("evidence") or f"Backfilled by claim-contract coverage audit: {claim_id}",
        limit=200,
    )
    cognitive_chapter = _positive_int(claim_source.get("cognitive_chapter"))
    if not cognitive_chapter and not claim_source:
        cognitive_chapter = target_chapter
    raw_subjects = _string_list(claim_source.get("cognitive_subjects"))
    metadata = claim_source.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    entity_adjudication_status = str(
        claim_source.get("entity_adjudication_status")
        or metadata.get("entity_adjudication_status")
        or ""
    )
    # ``cognitive_subjects`` has already crossed the entity-reference
    # adjudication boundary before it reaches the Claim ledger.  A ``partial``
    # status may be caused by an unrelated mention in the same Claim (for
    # example, both cognitive subjects resolved while "someone's brother"
    # remained unresolved).  Treating that aggregate status as all-or-nothing
    # erased the resolved subjects and made deterministic backfill impossible.
    # Only the explicit unavailable state means the projected values cannot be
    # trusted; partial projections are intentionally field-safe.
    normalized_subjects = [] if entity_adjudication_status == "unavailable" else raw_subjects
    return {
        "constraint_id": f"backfill_{claim_id}",
        "claim_id": claim_id,
        "claim_text": claim_text,
        "cognitive_subjects": normalized_subjects,
        "cognitive_object": _compact_constraint_text(
            claim_source.get("cognitive_object"),
            limit=200,
        ),
        "cognitive_level": str(claim_source.get("cognitive_level") or "unaware"),
        "action_level": str(claim_source.get("action_level") or "none"),
        "reader_awareness": str(claim_source.get("reader_awareness") or "unknown"),
        "character_knowledge_coverage": _string_dict(
            claim_source.get("character_knowledge_coverage")
        ),
        "cognitive_chapter": cognitive_chapter or None,
        "public_reveal_chapter": (_positive_int(claim_source.get("public_reveal_chapter")) or None),
        "foreshadow_chapters": _positive_ints(claim_source.get("foreshadow_chapters")),
        "source_artifact": str(claim_source.get("artifact") or "claim_contract_coverage"),
        "source_path": str(claim_source.get("source_path") or "/coverage_backfill"),
        "evidence": evidence,
    }


def _compact_constraint_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()
