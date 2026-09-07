"""Factories for constructing Repair Orchestration v2 missions."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.repair_orchestration.models import (
    RepairControlMode,
    RepairDomain,
    RepairMission,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
)

_OPENING_BOUNDARY_CONTINUITY_TYPES = {"opening_gap", "bridge_contract_not_followed"}


def resolve_control_mode(
    settings: Any, override: RepairControlMode | str | None = None
) -> RepairControlMode:
    """Resolve repair control mode from an explicit value or settings."""

    value = override or getattr(settings, "repair_control_mode", "ai_assisted")
    if isinstance(value, RepairControlMode):
        return value
    return RepairControlMode(str(value))


def _chapter_text_target(
    *,
    domain: RepairDomain,
    chapter_number: int,
    issue_ref: str,
    severity: str = "medium",
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> RepairTarget:
    return RepairTarget(
        domain=domain,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=chapter_number,
        issue_ref=issue_ref,
        severity=severity,
        summary=summary,
        payload=payload or {},
    )


def _issue_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Split selected repair inputs into one payload per issue-like item."""

    common: dict[str, Any] = {}
    for key in (
        "allow_exhausted_retry",
        "strategy",
        "repair_strategy",
        "estimated_change_ratio",
        "crosses_artifact_boundary",
        "skip_empty_target",
    ):
        if key in payload:
            common[key] = payload[key]

    result: list[dict[str, Any]] = []
    for index in payload.get("issue_indices") or []:
        result.append({**common, "issue_indices": [index], "point_repair": True})
    for signature in payload.get("issue_signatures") or []:
        result.append({**common, "issue_signatures": [signature], "point_repair": True})
    for issue in payload.get("synthetic_issues") or []:
        result.append({**common, "synthetic_issues": [issue], "point_repair": True})
    if result:
        return result
    if payload.get("skip_empty_target"):
        return [payload]
    return []


def _payload_issue_ref(domain: RepairDomain, chapter_number: int, payload: dict[str, Any]) -> str:
    prefix = f"{domain.value}:{chapter_number}"
    indices = payload.get("issue_indices") or []
    if indices:
        return f"{prefix}:index:{indices[0]}"
    signatures = payload.get("issue_signatures") or []
    if signatures:
        return f"{prefix}:signature:{str(signatures[0])[:80]}"
    synthetic = payload.get("synthetic_issues") or []
    if synthetic and isinstance(synthetic[0], dict):
        issue = synthetic[0]
        for key in ("signature", "issue_ref", "id", "issue_id"):
            value = str(issue.get(key) or "").strip()
            if value:
                return f"{prefix}:synthetic:{value[:80]}"
        issue_type = str(issue.get("issue_type") or issue.get("type") or "issue").strip()
        summary = str(issue.get("summary") or issue.get("description") or "").strip()
        return f"{prefix}:synthetic:{issue_type}:{summary[:80]}"
    return prefix


def _issue_summary(domain: RepairDomain, payload: dict[str, Any]) -> str:
    synthetic = payload.get("synthetic_issues") or []
    if synthetic and isinstance(synthetic[0], dict):
        summary = str(synthetic[0].get("summary") or synthetic[0].get("description") or "").strip()
        if summary:
            return summary
    if payload.get("issue_indices"):
        return f"{domain.value} issue index {payload['issue_indices'][0]}"
    if payload.get("issue_signatures"):
        return f"{domain.value} issue signature {payload['issue_signatures'][0]}"
    return f"{domain.value} issue repair"


def _chapter_issue_targets(
    *,
    domain: RepairDomain,
    chapter_number: int,
    payload: dict[str, Any],
    severity: str = "medium",
) -> list[RepairTarget]:
    return [
        _chapter_text_target(
            domain=domain,
            chapter_number=chapter_number,
            issue_ref=_payload_issue_ref(domain, chapter_number, item),
            severity=severity,
            summary=_issue_summary(domain, item),
            payload=item,
        )
        for item in _issue_payloads(payload)
    ]


def _surface(value: Any, fallback: RepairSurface) -> RepairSurface:
    if isinstance(value, RepairSurface):
        return value
    if value is None or value == "":
        return fallback
    return RepairSurface(str(value))


def continuity_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission from a continuity repair request-like object."""

    chapter_number = int(request.chapter_number)
    payload = {
        "issue_indices": list(getattr(request, "issue_indices", []) or []),
        "issue_signatures": list(getattr(request, "issue_signatures", []) or []),
        "synthetic_issues": list(getattr(request, "synthetic_issues", []) or []),
    }
    targets = _chapter_issue_targets(
        domain=RepairDomain.CONTINUITY,
        chapter_number=chapter_number,
        payload=payload,
    )
    if not targets:
        targets = [
            _chapter_text_target(
                domain=RepairDomain.CONTINUITY,
                chapter_number=chapter_number,
                issue_ref=f"continuity:{chapter_number}",
                summary="continuity repair",
                payload=payload,
            )
        ]
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=targets,
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
    )


def causal_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission from a causal repair request-like object."""

    chapter_number = int(request.chapter_number)
    payload = {
        "issue_indices": list(getattr(request, "issue_indices", []) or []),
        "issue_signatures": list(getattr(request, "issue_signatures", []) or []),
        "synthetic_issues": list(getattr(request, "synthetic_issues", []) or []),
        "allow_exhausted_retry": bool(getattr(request, "allow_exhausted_retry", False)),
    }
    targets = _chapter_issue_targets(
        domain=RepairDomain.CAUSAL,
        chapter_number=chapter_number,
        payload=payload,
        severity="high",
    )
    if not targets:
        targets = [
            _chapter_text_target(
                domain=RepairDomain.CAUSAL,
                chapter_number=chapter_number,
                issue_ref=f"causal:{chapter_number}",
                severity="high",
                summary="causal repair",
                payload=payload,
            )
        ]
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=targets,
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
    )


def guardrail_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission for a guardrail text repair request-like object."""

    chapter_number = int(getattr(request, "chapter_number", 0) or 0)
    payload = dict(getattr(request, "payload", {}) or {})
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=[
            RepairTarget(
                domain=RepairDomain.GUARDRAIL,
                surface=_surface(getattr(request, "surface", None), RepairSurface.CHAPTER_TEXT),
                chapter_number=chapter_number,
                issue_ref=str(getattr(request, "issue_ref", "") or f"guardrail:{chapter_number}"),
                severity=str(getattr(request, "severity", "high") or "high"),
                summary=str(getattr(request, "summary", "") or "guardrail repair"),
                evidence=str(getattr(request, "evidence", "") or ""),
                payload=payload,
            )
        ],
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
        source_context=dict(getattr(request, "source_context", {}) or {}),
    )


def init_artifact_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission for an initialization artifact repair request-like object."""

    surface = _surface(getattr(request, "surface", None), RepairSurface.BLUEPRINT)
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=[
            RepairTarget(
                domain=RepairDomain.INIT_ARTIFACT,
                surface=surface,
                issue_ref=str(
                    getattr(request, "issue_ref", "") or f"init_artifact:{surface.value}"
                ),
                severity=str(getattr(request, "severity", "high") or "high"),
                summary=str(getattr(request, "summary", "") or "init artifact repair"),
                evidence=str(getattr(request, "evidence", "") or ""),
                payload=dict(getattr(request, "payload", {}) or {}),
            )
        ],
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
        source_context=dict(getattr(request, "source_context", {}) or {}),
    )


def book_consistency_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission for a whole-book repair queue request-like object."""

    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=[
            RepairTarget(
                domain=RepairDomain.BOOK_CONSISTENCY,
                surface=_surface(getattr(request, "surface", None), RepairSurface.BOOK_CHAPTER_SET),
                issue_ref=str(getattr(request, "issue_ref", "") or "book_consistency"),
                severity=str(getattr(request, "severity", "high") or "high"),
                summary=str(getattr(request, "summary", "") or "book consistency repair"),
                evidence=str(getattr(request, "evidence", "") or ""),
                payload=dict(getattr(request, "payload", {}) or {}),
            )
        ],
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
        source_context=dict(getattr(request, "source_context", {}) or {}),
    )


def state_adjudication_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
) -> RepairMission:
    """Build a v2 mission for an adjudicated state repair request-like object."""

    chapter_number = int(getattr(request, "chapter_number", 0) or 0)
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=[
            RepairTarget(
                domain=RepairDomain.STATE_ADJUDICATION,
                surface=_surface(getattr(request, "surface", None), RepairSurface.CHAPTER_TEXT),
                chapter_number=chapter_number,
                issue_ref=str(
                    getattr(request, "issue_ref", "") or f"state_adjudication:{chapter_number}"
                ),
                severity=str(getattr(request, "severity", "high") or "high"),
                summary=str(getattr(request, "summary", "") or "state adjudication repair"),
                evidence=str(getattr(request, "evidence", "") or ""),
                payload=dict(getattr(request, "payload", {}) or {}),
            )
        ],
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
        source_context=dict(getattr(request, "source_context", {}) or {}),
    )


def issues_repair_mission(
    request: Any,
    *,
    settings: Any,
    control_mode: RepairControlMode | str | None = None,
    include_empty_continuity_target: bool = False,
    include_empty_causal_target: bool = False,
) -> RepairMission:
    """Build a v2 mission from a combined continuity/causal issues request."""

    chapter_number = int(request.chapter_number)
    continuity_payload = {
        "issue_indices": list(getattr(request, "continuity_issue_indices", []) or []),
        "issue_signatures": list(getattr(request, "continuity_issue_signatures", []) or []),
        "synthetic_issues": list(getattr(request, "continuity_synthetic_issues", []) or []),
    }
    causal_payload = {
        "issue_indices": list(getattr(request, "causal_issue_indices", []) or []),
        "issue_signatures": list(getattr(request, "causal_issue_signatures", []) or []),
        "synthetic_issues": list(getattr(request, "causal_synthetic_issues", []) or []),
        "allow_exhausted_retry": bool(getattr(request, "allow_exhausted_retry", False)),
    }
    targets: list[RepairTarget] = []
    continuity_has_inputs = any(continuity_payload.values())
    if continuity_has_inputs or include_empty_continuity_target:
        if not continuity_has_inputs:
            continuity_payload.update(
                {
                    "skip_empty_target": True,
                    "strategy": RepairStrategy.VERIFY_ONLY.value,
                    "estimated_change_ratio": 0.0,
                }
            )
        continuity_targets = _chapter_issue_targets(
            domain=RepairDomain.CONTINUITY,
            chapter_number=chapter_number,
            payload=continuity_payload,
        )
        targets.extend(continuity_targets)
    causal_has_inputs = any(
        value for key, value in causal_payload.items() if key != "allow_exhausted_retry"
    )
    if causal_has_inputs or include_empty_causal_target:
        if not causal_has_inputs:
            causal_payload.update(
                {
                    "skip_empty_target": True,
                    "strategy": RepairStrategy.VERIFY_ONLY.value,
                    "estimated_change_ratio": 0.0,
                }
            )
        causal_targets = _chapter_issue_targets(
            domain=RepairDomain.CAUSAL,
            chapter_number=chapter_number,
            payload=causal_payload,
            severity="high",
        )
        if _continuity_targets_opening_boundary(continuity_payload):
            targets = [*causal_targets, *targets]
        else:
            targets.extend(causal_targets)
    return RepairMission(
        project_id=str(request.project_id),
        control_mode=resolve_control_mode(settings, control_mode),
        targets=targets,
        policy={
            "change_budget": float(getattr(settings, "long_repair_max_change_ratio", 0.35) or 0.35)
        },
    )


def _continuity_targets_opening_boundary(payload: dict[str, Any]) -> bool:
    for item in payload.get("synthetic_issues") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("issue_type") or "").strip().lower() in _OPENING_BOUNDARY_CONTINUITY_TYPES:
            return True
    return False
