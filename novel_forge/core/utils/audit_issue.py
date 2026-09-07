"""Shared helpers for audit issue identity and lifecycle metadata."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from novel_forge.core.review.audit_profiles import get_audit_profile
from novel_forge.core.schemas.audit import AuditIssue, AuditPostcondition

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
CLOSED_ISSUE_STATUSES = {
    "resolved",
    "suppressed",
    "artifact_fixed",
    "deferred",
    "closed",
    "fixed",
    "ignored",
}
VALID_SEVERITIES = set(SEVERITY_ORDER)
VALID_STATUSES = {
    "open",
    "repairing",
    *CLOSED_ISSUE_STATUSES,
}
VALID_REPAIR_SURFACES = {
    "chapter_text",
    "bridge_artifact",
    "chapter_plan",
    "state_packet",
    "local_rule",
    "replan",
    "manual",
}


def issue_field(issue: Any, key: str, default: Any = "") -> Any:
    """Read an issue field from either a dict or an attribute-based object."""
    if isinstance(issue, dict):
        return issue.get(key, default)
    return getattr(issue, key, default)


def normalize_issue_text(value: Any) -> str:
    """Normalize free-text fields for stable hashing and loose matching."""
    return "".join(str(value or "").split()).lower()


def normalize_location_key(location: Any) -> str:
    """Normalize location text for loose matching (e.g. 开头第1段 / 第1段)."""
    if not location:
        return ""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(location)).lower()


def severity_rank(severity: Any) -> int:
    """Return numeric rank for a severity label."""
    return SEVERITY_ORDER.get(str(severity or "").lower().strip(), 0)


def issue_severity(issue: Any, default: str = "medium") -> str:
    """Return normalized issue severity."""
    raw = str(issue_field(issue, "severity", default) or default).strip().lower()
    aliases = {
        "major": "high",
        "warning": "medium",
        "minor": "low",
        "blocker": "critical",
    }
    value = aliases.get(raw, raw)
    return value if value in VALID_SEVERITIES else default


def issue_status(issue: Any, default: str = "open") -> str:
    """Return normalized issue lifecycle status."""
    value = str(issue_field(issue, "status", default) or default).strip().lower()
    return value if value in VALID_STATUSES else default


def is_open_issue(issue: Any) -> bool:
    """Return True when an issue should still affect gates and repair loops."""
    return issue_status(issue) not in CLOSED_ISSUE_STATUSES


def repair_surface(issue: Any, default: str = "chapter_text") -> str:
    """Return normalized repair surface."""
    value = str(issue_field(issue, "repair_surface", default) or default).strip().lower()
    return value if value in VALID_REPAIR_SURFACES else default


def _clean(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；： \n") + "…"


def _float_0_1(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _int_ge0(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in list(value)[:limit]:
        text = _clean(item, limit=240)
        if text:
            result.append(text)
    return result


def _issue_dict(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return dict(issue)
    if hasattr(issue, "model_dump"):
        dumped = issue.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if hasattr(issue, "__dict__"):
        return {
            key: value
            for key, value in vars(issue).items()
            if not key.startswith("_")
        }
    return {}


def normalize_postcondition(raw: Any) -> AuditPostcondition | None:
    """Normalize a postcondition-like object into the canonical schema."""
    if isinstance(raw, AuditPostcondition):
        return raw
    if isinstance(raw, str):
        desc = _clean(raw, limit=300)
        if not desc:
            return None
        return AuditPostcondition(
            validator_id="semantic_postcondition",
            description=desc,
            required=True,
        )
    if isinstance(raw, dict):
        desc = _clean(raw.get("description") or raw.get("condition"), limit=300)
        if not desc:
            return None
        return AuditPostcondition(
            validator_id=_clean(raw.get("validator_id") or "semantic_postcondition", limit=120),
            description=desc,
            evidence_hint=_clean(raw.get("evidence_hint"), limit=180),
            required=bool(raw.get("required", True)),
        )
    if hasattr(raw, "model_dump"):
        dumped = raw.model_dump(mode="json")
        if isinstance(dumped, dict):
            return normalize_postcondition(dumped)
    return None


def normalize_audit_issue(
    issue: Any,
    *,
    dimension: str = "unknown",
    namespace: str = "",
    chapter_number: int = 0,
    repair_surface_default: str = "",
    source_module: str = "",
) -> AuditIssue:
    """Normalize any domain-specific issue into the canonical audit schema."""
    data = _issue_dict(issue)
    resolved_dimension = _clean(
        data.get("dimension")
        or data.get("category")
        or dimension
        or "unknown",
        limit=80,
    )
    profile = get_audit_profile(resolved_dimension)
    issue_type = _clean(
        data.get("issue_type")
        or data.get("type")
        or data.get("category")
        or resolved_dimension
        or "audit_issue",
        limit=100,
    )
    issue_profile = profile.issue_profile(issue_type)
    summary = _clean(
        data.get("summary")
        or data.get("description")
        or data.get("target_summary")
        or issue_type,
        limit=360,
    )
    evidence = _clean(
        data.get("evidence")
        or data.get("evidence_quote")
        or data.get("quote")
        or "",
        limit=500,
    )
    location = _clean(data.get("location") or data.get("position") or "", limit=160)
    surface_default = (
        issue_profile.repair_surface
        or repair_surface_default
        or profile.default_repair_surface
        or "chapter_text"
    )
    surface = repair_surface(data, default=surface_default)
    resolved_namespace = namespace or profile.namespace or resolved_dimension or source_module or "audit"
    issue_id = str(data.get("issue_id") or data.get("id") or "").strip()
    if not issue_id:
        issue_id = stable_issue_id(
            resolved_namespace,
            chapter_number=chapter_number,
            issue_type=issue_type,
            summary=summary,
            evidence=evidence,
            location=location,
            repair_surface=surface,
        )
    severity = issue_severity(data)
    status = issue_status(data)
    explicit_blocking = data.get("blocking")
    blocking = bool(explicit_blocking) or (status == "open" and severity_rank(severity) >= 3)
    postconditions = [
        normalized
        for normalized in (
            normalize_postcondition(item)
            for item in list(data.get("postconditions") or [])
        )
        if normalized is not None
    ]
    if not postconditions:
        postconditions = [
            normalized
            for normalized in (
                normalize_postcondition(item.to_dict())
                for item in issue_profile.postconditions
            )
            if normalized is not None
        ]
    paragraph_start = _int_ge0(
        data.get("paragraph_start")
        or data.get("paragraph_index")
        or data.get("para_start")
    )
    paragraph_end = _int_ge0(data.get("paragraph_end") or data.get("para_end") or paragraph_start)
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault(
        "audit_profile",
        {
            "dimension": profile.dimension,
            "repair_lane": profile.repair_lane,
            "reviewer_focus": profile.reviewer_focus,
            "repair_focus": issue_profile.repair_focus,
        },
    )
    return AuditIssue(
        issue_id=issue_id,
        dimension=resolved_dimension,
        source_module=_clean(data.get("source_module") or source_module, limit=120),
        source=_clean(data.get("source"), limit=80),
        issue_type=issue_type,
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        repair_surface=surface,  # type: ignore[arg-type]
        blocking=blocking,
        confidence=_float_0_1(data.get("confidence")),
        summary=summary,
        evidence=evidence,
        evidence_quote=_clean(data.get("evidence_quote") or evidence, limit=500),
        location=location,
        location_confidence=_float_0_1(data.get("location_confidence")),
        anchor_type=_clean(data.get("anchor_type"), limit=80),
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
        fix_suggestion=_clean(
            data.get("fix_suggestion") or data.get("suggestion") or "",
            limit=360,
        ),
        fix_mode=_clean(data.get("fix_mode") or data.get("fix_action") or "", limit=80),
        fix_actions=_string_list(data.get("fix_actions")),
        postconditions=postconditions,
        metadata=metadata,
    )


def audit_issue_prompt_payload(
    issue: Any,
    *,
    dimension: str,
    chapter_number: int = 0,
    source_module: str = "",
) -> dict[str, Any]:
    """Return compact prompt-safe prior issue payload for targeted rechecks."""
    audit_issue = normalize_audit_issue(
        issue,
        dimension=dimension,
        chapter_number=chapter_number,
        source_module=source_module,
    )
    profile_meta = dict(audit_issue.metadata.get("audit_profile") or {})
    return {
        "issue_id": audit_issue.issue_id,
        "dimension": audit_issue.dimension,
        "issue_type": audit_issue.issue_type,
        "severity": audit_issue.severity,
        "summary": audit_issue.summary,
        "evidence": audit_issue.evidence or audit_issue.evidence_quote,
        "location": audit_issue.location,
        "repair_surface": audit_issue.repair_surface,
        "status": audit_issue.status,
        "postconditions": [
            item.model_dump(
                mode="json",
                exclude={"schema_version", "created_at"},
            )
            for item in audit_issue.postconditions
        ],
        "repair_focus": str(profile_meta.get("repair_focus", "") or ""),
        "reviewer_focus": str(profile_meta.get("reviewer_focus", "") or ""),
    }


def audit_issues_prompt_payload(
    issues: list[Any],
    *,
    dimension: str,
    chapter_number: int = 0,
    source_module: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Normalize and compact issue lists before injecting them into recheck prompts."""
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in list(issues or [])[:limit]:
        payload = audit_issue_prompt_payload(
            issue,
            dimension=dimension,
            chapter_number=chapter_number,
            source_module=source_module,
        )
        key = str(payload.get("issue_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        payloads.append(payload)
    return payloads


def _namespace_slug(namespace: Any) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", str(namespace or "").strip().lower())
    return slug.strip("_-") or "issue"


def legacy_issue_fingerprint(
    issue_type: Any,
    summary: Any,
    evidence: Any = "",
    location: Any = "",
    *,
    digest_size: int = 16,
) -> str:
    """Content fingerprint compatible with the historical issue ledger.

    Prefer stable ``issue_id`` when available.  This helper is for older issue
    shapes that do not yet carry one.
    """
    key_parts = [str(issue_type or "").strip().lower()]
    norm_summary = normalize_issue_text(summary)
    if norm_summary:
        key_parts.append(norm_summary)
    else:
        loc_key = normalize_location_key(location)
        if loc_key:
            key_parts.append(loc_key)
    if len(key_parts) == 1 and evidence:
        key_parts.append(normalize_issue_text(str(evidence or "")[:80]))
    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:digest_size]


def mint_issue_id(
    namespace: str,
    *,
    chapter_number: int = 0,
    issue_type: str = "",
) -> str:
    """Generate a unique, opaque issue ID at detection time.

    Unlike :func:`stable_issue_id`, which hashes content fields and therefore
    changes when the issue text is rephrased, this function uses a random suffix
    so the ID remains stable across repair rounds even if the LLM rephrases the
    summary or evidence.

    Format: ``ch003-causal-a1b2c3d4e5f6`` (same format as *stable_issue_id*).
    """
    ns = _namespace_slug(namespace)
    try:
        chapter = int(chapter_number or 0)
    except (TypeError, ValueError):
        chapter = 0
    digest = uuid.uuid4().hex[:12]
    if chapter > 0:
        return f"ch{chapter:03d}-{ns}-{digest}"
    return f"{ns}-{digest}"


def stable_issue_id(
    namespace: str,
    *,
    chapter_number: int = 0,
    issue_type: Any = "",
    summary: Any = "",
    evidence: Any = "",
    location: Any = "",
    repair_surface: Any = "",
    extra: dict[str, Any] | None = None,
    digest_size: int = 12,
) -> str:
    """Build a deterministic issue id from semantic issue fields."""
    ns = _namespace_slug(namespace)
    try:
        chapter = int(chapter_number or 0)
    except (TypeError, ValueError):
        chapter = 0
    payload: dict[str, Any] = {
        "namespace": ns,
        "chapter": chapter,
        "issue_type": str(issue_type or "").strip().lower(),
        "summary": normalize_issue_text(summary),
        "evidence": normalize_issue_text(str(evidence or "")[:160]),
        "location": normalize_location_key(location),
        "repair_surface": str(repair_surface or "").strip().lower(),
    }
    if extra:
        payload["extra"] = extra
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:digest_size]
    if chapter > 0:
        return f"ch{chapter:03d}-{ns}-{digest}"
    return f"{ns}-{digest}"


def issue_identity_key(
    issue: Any,
    *,
    namespace: str = "",
    chapter_number: int = 0,
    repair_surface: str = "",
) -> str:
    """Return the most stable available identity key for an issue."""
    issue_id = str(issue_field(issue, "issue_id", "") or issue_field(issue, "id", "") or "").strip()
    if issue_id:
        return f"id:{issue_id}"
    if namespace:
        return "id:" + stable_issue_id(
            namespace,
            chapter_number=chapter_number,
            issue_type=issue_field(issue, "issue_type", ""),
            summary=issue_field(issue, "summary", "") or issue_field(issue, "description", ""),
            evidence=issue_field(issue, "evidence", "") or issue_field(issue, "evidence_quote", ""),
            location=issue_field(issue, "location", ""),
            repair_surface=repair_surface or str(issue_field(issue, "repair_surface", "") or ""),
        )
    return legacy_issue_fingerprint(
        issue_field(issue, "issue_type", ""),
        issue_field(issue, "summary", "") or issue_field(issue, "description", ""),
        issue_field(issue, "evidence", "") or issue_field(issue, "evidence_quote", ""),
        issue_field(issue, "location", ""),
    )


def ensure_issue_id(
    issue: Any,
    namespace: str,
    *,
    chapter_number: int = 0,
    repair_surface: str = "",
) -> str:
    """Return existing issue_id or generate one from shared audit fields."""
    issue_id = str(issue_field(issue, "issue_id", "") or issue_field(issue, "id", "") or "").strip()
    if issue_id:
        return issue_id
    return stable_issue_id(
        namespace,
        chapter_number=chapter_number,
        issue_type=issue_field(issue, "issue_type", ""),
        summary=issue_field(issue, "summary", "") or issue_field(issue, "description", ""),
        evidence=issue_field(issue, "evidence", "") or issue_field(issue, "evidence_quote", ""),
        location=issue_field(issue, "location", ""),
        repair_surface=repair_surface or str(issue_field(issue, "repair_surface", "") or ""),
    )
