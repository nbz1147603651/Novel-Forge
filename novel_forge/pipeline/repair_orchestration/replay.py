"""Offline replay checks for repair fixtures and persisted case evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.pipeline.repair_orchestration.domains.initialization import (
    normalize_init_comparison_value,
)


@dataclass(frozen=True, slots=True)
class RepairReplayResult:
    fixture: str
    passed: bool
    checks: tuple[str, ...]
    error: str = ""


def replay_repair_fixture(path: Path) -> RepairReplayResult:
    """Replay one sanitized fixture without a provider or project write."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fixture must be a JSON object")
        kind = str(payload.get("kind") or "")
        if kind == "comparison":
            checks = _replay_comparison(payload)
        elif kind == "candidate_evidence":
            checks = _replay_candidate_evidence(payload)
        elif kind == "shadow_result":
            checks = _replay_shadow_result(payload)
        else:
            raise ValueError(f"unsupported repair replay fixture kind: {kind}")
        return RepairReplayResult(str(path), True, tuple(checks))
    except Exception as exc:
        return RepairReplayResult(
            str(path),
            False,
            (),
            f"{type(exc).__name__}: {exc}",
        )


def replay_repair_fixtures(path: Path) -> list[RepairReplayResult]:
    paths = [path] if path.is_file() else sorted(path.glob("*.json"))
    return [replay_repair_fixture(item) for item in paths]


def replay_project_repair_cases(project_root: Path) -> list[RepairReplayResult]:
    """Validate append-only projections and blobs without rebuilding or writing them."""

    store = RepairCaseStore(project_root)
    results: list[RepairReplayResult] = []
    for case in store.list_cases():
        checks: list[str] = []
        error = ""
        try:
            source_blob = str(case.metadata.get("source_blob_hash") or "")
            if source_blob:
                source = store.get_blob(source_blob)
                if repair_content_hash(source) != case.source_hash:
                    raise ValueError("source blob hash differs from case source hash")
                checks.append("source_hash")
            for candidate in case.candidates:
                candidate_payload = store.get_blob(candidate.blob_hash)
                if repair_content_hash(candidate_payload) != candidate.candidate_hash:
                    raise ValueError(f"candidate blob hash mismatch: v{candidate.version}")
            if case.candidates:
                checks.append("candidate_hashes")
            if case.verification is not None:
                latest = case.latest_candidate
                if latest is None or (
                    case.verification.candidate_version != latest.version
                    or case.verification.candidate_hash != latest.candidate_hash
                ):
                    raise ValueError("verification is not bound to the latest candidate")
                checks.append("verification_binding")
            if case.receipt is not None:
                latest = case.latest_candidate
                if latest is None or case.receipt.after_hash != latest.candidate_hash:
                    raise ValueError("receipt is not bound to the latest candidate")
                if case.status == "published" and not case.receipt.committed:
                    raise ValueError("published case has no committed receipt")
                checks.append("receipt_binding")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        results.append(
            RepairReplayResult(
                fixture=f"case:{case.case_id}",
                passed=not error,
                checks=tuple(checks),
                error=error,
            )
        )
    return results


def _replay_comparison(payload: dict[str, Any]) -> list[str]:
    comparator = str(payload.get("comparator_id") or "")
    if comparator != "init_duration_range_v1":
        raise ValueError(f"unsupported replay comparator: {comparator}")
    expected_raw = payload.get("expected_raw")
    actual_raw = payload.get("actual_raw")
    expected_normalized = normalize_init_comparison_value(expected_raw)
    actual_normalized = normalize_init_comparison_value(actual_raw)
    equivalent = expected_normalized == actual_normalized
    if equivalent != bool(payload.get("expect_equivalent")):
        raise ValueError("comparison replay did not match the expected equivalence")
    if expected_raw == actual_raw:
        raise ValueError("fixture must retain distinct raw values")
    return ["raw_values_retained", "normalized_comparator_replayed"]


def _replay_candidate_evidence(payload: dict[str, Any]) -> list[str]:
    source_hash = str(payload.get("source_hash") or "")
    candidate_base_hash = str(payload.get("candidate_base_hash") or "")
    candidate_hash = str(payload.get("candidate_hash") or "")
    verification_hash = str(payload.get("verification_candidate_hash") or "")
    verification_passed = bool(payload.get("verification_passed"))
    expected_adopted = bool(payload.get("expect_adopted"))
    adopted = (
        bool(source_hash)
        and candidate_base_hash == source_hash
        and bool(candidate_hash)
        and verification_hash == candidate_hash
        and verification_passed
    )
    if adopted != expected_adopted:
        raise ValueError("candidate adoption replay disagrees with expected result")
    return ["immutable_baseline", "verification_candidate_binding", "adoption_gate"]


def _replay_shadow_result(payload: dict[str, Any]) -> list[str]:
    if not bool(payload.get("shadow_only")):
        raise ValueError("shadow replay is not marked evaluation-only")
    if bool(payload.get("published")) or bool(payload.get("case_state_changed")):
        raise ValueError("shadow replay changed publication or case state")
    if int(payload.get("attempts") or 0) > 1:
        raise ValueError("shadow replay exceeded one attempt")
    return ["shadow_only", "no_publication", "case_state_unchanged", "single_attempt"]


__all__ = [
    "RepairReplayResult",
    "replay_project_repair_cases",
    "replay_repair_fixture",
    "replay_repair_fixtures",
]
