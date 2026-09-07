"""Report merge helpers for initialization coherence v2."""

from __future__ import annotations

import json
from typing import Any

CLAIM_LEDGER_JSON = "init_coherence_claim_ledger.json"


def _merge_focus_chunk_reports(
    *,
    stage: str,
    repair_artifact: str,
    focus_chunks: list[list[int]],
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    focus_numbers = sorted({number for chunk in focus_chunks for number in chunk if number >= 1})
    completed_chunks = len(reports)
    issues = _dedupe_report_items(
        item for report in reports for item in _as_dict_list(report.get("issues"))
    )
    repair_scope = _dedupe_report_items(
        item for report in reports for item in _as_dict_list(report.get("repair_scope"))
    )
    preserve = _dedupe_jsonable(
        item for report in reports for item in _as_list(report.get("preserve"))
    )
    source_refs = _dedupe_jsonable(
        item for report in reports for item in _as_list(report.get("source_refs"))
    )
    chunk_reports = [
        {
            "chunk_index": index,
            "focus_chapters": chunk,
            "verdict": report.get("verdict", ""),
            "blocked": bool(report.get("blocked")),
            "claims_count": _report_int_metric(report, "claims_count"),
            "candidate_count": _report_int_metric(report, "candidate_count"),
            "issue_count": len(report.get("issues") or []),
            "stopped_reason": report.get("stopped_reason", ""),
        }
        for index, (chunk, report) in enumerate(zip(focus_chunks, reports, strict=False), start=1)
    ]
    claims_count = _sum_report_metric(reports, "claims_count")
    candidate_count = _sum_report_metric(reports, "candidate_count")
    stopped_reasons = sorted(
        {
            str(report.get("stopped_reason") or "")
            for report in reports
            if report.get("stopped_reason")
        }
    )
    verdict = _worst_chunk_verdict(reports)
    blocked = any(bool(report.get("blocked")) for report in reports)
    degraded_memory = any(bool(report.get("degraded_memory")) for report in reports)
    summary = (
        f"{stage} 分块复检完成：{completed_chunks}/{len(focus_chunks)} 个窗口，"
        f"claims={claims_count}，candidates={candidate_count}，"
        f"issues={len(issues)}，verdict={verdict}。"
    )
    if stopped_reasons:
        summary += f" 已因 {', '.join(stopped_reasons)} 停止后续窗口。"

    active_claims = _sum_report_metric(reports, "active_claims_count")
    if active_claims <= 0:
        active_claims = claims_count
    report_payload: dict[str, Any] = {
        "stage": stage,
        "artifact": repair_artifact,
        "verdict": verdict,
        "issues": issues,
        "repair_scope": repair_scope,
        "preserve": preserve,
        "source_refs": source_refs,
        "blocked": blocked,
        "summary": summary,
        "claims_count": claims_count,
        "candidate_count": candidate_count,
        "adjudicated_candidate_count": _sum_report_metric(
            reports,
            "adjudicated_candidate_count",
        ),
        "degraded_memory": degraded_memory,
        "extracted_claims_count": _sum_report_metric(reports, "extracted_claims_count"),
        "active_claims_count": active_claims,
        "ledger_active_claims_count": max(
            (_report_int_metric(report, "ledger_active_claims_count") for report in reports),
            default=0,
        ),
        "retrieval_scope": "focus_chunked",
        "exact_candidate_count": _sum_report_metric(reports, "exact_candidate_count"),
        "semantic_candidate_count": _sum_report_metric(reports, "semantic_candidate_count"),
        "post_filter_drop_count": _sum_report_metric(reports, "post_filter_drop_count"),
        "fully_pushed_down_ratio": _weighted_ratio(
            reports,
            numerator_key="fully_pushed_down_ratio",
            weight_key="claims_count",
        ),
        "zvec_backend": _join_unique_report_values(reports, "zvec_backend"),
        "semantic_skipped_reason": _join_unique_report_values(
            reports,
            "semantic_skipped_reason",
        ),
        "claim_ledger_path": f"memory/{CLAIM_LEDGER_JSON}",
        "artifact_hashes": _first_dict_report_value(reports, "artifact_hashes"),
        "focus_chapters": focus_numbers,
        "focus_chunks": focus_chunks,
        "chunk_reports": chunk_reports,
        "completed_chunk_count": completed_chunks,
        "planned_chunk_count": len(focus_chunks),
    }
    if stopped_reasons:
        report_payload["stopped_reason"] = ",".join(stopped_reasons)
    return report_payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _dedupe_report_items(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(
            item.get("id")
            or item.get("issue_id")
            or item.get("target_id")
            or item.get("description")
            or _json_dedupe_key(item)
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_jsonable(items: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = _json_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _json_dedupe_key(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _report_int_metric(report: dict[str, Any], key: str) -> int:
    try:
        return int(report.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _sum_report_metric(reports: list[dict[str, Any]], key: str) -> int:
    return sum(_report_int_metric(report, key) for report in reports)


def _report_float_metric(report: dict[str, Any], key: str) -> float:
    try:
        return float(report.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _weighted_ratio(
    reports: list[dict[str, Any]],
    *,
    numerator_key: str,
    weight_key: str,
) -> float:
    total_weight = _sum_report_metric(reports, weight_key)
    if total_weight <= 0:
        return 0.0
    weighted = sum(
        _report_float_metric(report, numerator_key) * _report_int_metric(report, weight_key)
        for report in reports
    )
    return max(0.0, min(1.0, weighted / total_weight))


def _join_unique_report_values(reports: list[dict[str, Any]], key: str) -> str:
    values = [
        str(report.get(key) or "").strip()
        for report in reports
        if str(report.get(key) or "").strip()
    ]
    return ",".join(sorted(set(values)))


def _first_dict_report_value(reports: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for report in reports:
        value = report.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _worst_chunk_verdict(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return "accept"
    rank = {
        "accept": 0,
        "passed": 0,
        "ok": 0,
        "ambiguous": 1,
        "defer": 1,
        "needs_targeted_repair": 2,
        "needs_repair": 3,
        "reject": 4,
        "blocked": 4,
    }
    verdict = "accept"
    worst = 0
    for report in reports:
        candidate = str(report.get("verdict") or "").lower()
        value = rank.get(candidate, 1)
        if value > worst:
            worst = value
            verdict = candidate or "ambiguous"
    if any(bool(report.get("blocked")) for report in reports):
        return "needs_repair" if verdict == "accept" else verdict
    return verdict
