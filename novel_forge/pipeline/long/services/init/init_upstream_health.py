"""Deterministic upstream health gates for long initialization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    coerce_world_rule_book,
    validate_world_rule_book,
)
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def research_context_fingerprint(research_context: Any) -> str:
    """Return the stable fingerprint used to guard StoryBible reuse."""
    return hash_payload(research_context or {})


def load_upstream_health(ctx: Any) -> dict[str, Any]:
    """Load the aggregate upstream-health report if present."""
    path = ctx.layout.reports_dir / "init_upstream_health.json"
    if not ctx.storage.exists(path):
        return {
            "schema_version": 1,
            "status": "unknown",
            "checks": {},
            "warnings": [],
            "blocked": False,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    payload = ctx.storage.load_json(path)
    return payload if isinstance(payload, dict) else {}


def story_bible_research_context_matches(ctx: Any, expected_fingerprint: str) -> bool:
    """Return True when cached StoryBible was built with the current research context."""
    report = load_upstream_health(ctx)
    checks = report.get("checks") if isinstance(report, dict) else None
    story = checks.get("story_bible") if isinstance(checks, dict) else None
    if not isinstance(story, dict):
        return not expected_fingerprint
    recorded = str(story.get("research_context_fingerprint") or "")
    return recorded == expected_fingerprint


def record_story_bible_health(
    ctx: Any,
    *,
    story_bible: Any,
    research_context: Any,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    world_rule_integrity: dict[str, Any] = {
        "status": "missing",
        "rule_count": 0,
        "errors": [],
    }
    premise = str(getattr(story_bible, "premise", "") or "").strip()
    if not premise:
        issues.append(_issue("critical", "story_bible_missing_premise", "StoryBible 缺少核心前提。"))
    rule_book = getattr(story_bible, "world_rule_book", None)
    if not rule_book or not list(getattr(rule_book, "rules", []) or []):
        world_rule_integrity["errors"] = ["world_rule_book missing"]
        issues.append(
            _issue(
                "critical",
                "story_bible_missing_world_rule_book",
                "StoryBible 缺少结构化 world_rule_book；项目必须重新初始化。",
            )
        )
    else:
        settings = getattr(ctx, "settings", None)
        block_on_violation = bool(getattr(settings, "world_rule_block_on_violation", False))
        integrity_errors = validate_world_rule_book(
            coerce_world_rule_book(rule_book, settings=settings),
            magic_or_tech=str(getattr(story_bible, "magic_or_tech", "") or ""),
            settings=settings,
        )
        world_rule_integrity = {
            "status": "pass" if not integrity_errors else "fail",
            "rule_count": len(getattr(rule_book, "rules", []) or []),
            "errors": integrity_errors,
        }
        for message in integrity_errors:
            # Governance violations default to "medium" (warning, non-blocking).
            # Only block init when world_rule_block_on_violation is explicitly enabled.
            severity = "critical" if block_on_violation else "medium"
            issues.append(_issue(severity, "story_bible_invalid_world_rule_book", message))
    if not str(getattr(story_bible, "time_convention", "") or "").strip():
        issues.append(_issue("low", "story_bible_missing_time_convention", "StoryBible 未明确时间表达约定。"))
    check = _check_payload(
        artifact="story_bible",
        issues=issues,
        metadata={
            "artifact_fingerprint": hash_payload(_model_payload(story_bible)),
            "research_context_fingerprint": research_context_fingerprint(research_context),
            "world_rule_integrity": world_rule_integrity,
        },
    )
    return _save_check(ctx, "story_bible", check)


def record_character_bible_health(ctx: Any, *, character_bible: Any) -> dict[str, Any]:
    characters = list(getattr(character_bible, "characters", []) or [])
    issues: list[dict[str, Any]] = []
    if not characters:
        issues.append(_issue("critical", "character_bible_empty", "角色设定没有可用角色。"))
    ids: set[str] = set()
    names: set[str] = set()
    duplicate_names: set[str] = set()
    missing_ids = 0
    incomplete_profiles: list[str] = []
    for profile in characters:
        name = str(getattr(profile, "name", "") or "").strip()
        character_id = str(getattr(profile, "character_id", "") or "").strip()
        if not character_id:
            missing_ids += 1
        elif character_id in ids:
            issues.append(_issue("high", "character_bible_duplicate_id", f"角色 ID 重复：{character_id}"))
        ids.add(character_id)
        if name:
            if name in names:
                duplicate_names.add(name)
            names.add(name)
        missing_fields = [
            field
            for field in ("personality", "backstory", "arc", "voice")
            if not str(getattr(profile, field, "") or "").strip()
        ]
        if missing_fields:
            incomplete_profiles.append(
                f"{name or character_id or '<unknown>'}({','.join(missing_fields)})"
            )
    if missing_ids:
        issues.append(_issue("high", "character_bible_missing_ids", f"{missing_ids} 个角色缺少稳定 ID。"))
    for name in sorted(duplicate_names):
        issues.append(_issue("medium", "character_bible_duplicate_name", f"角色姓名重复：{name}"))
    if incomplete_profiles:
        issues.append(
            _issue(
                "high",
                "character_bible_incomplete_profiles",
                "角色源档案缺少人格、背景、弧线或声纹，禁止继续放大到下游："
                + "；".join(incomplete_profiles),
            )
        )
    check = _check_payload(
        artifact="character_bible",
        issues=issues,
        metadata={
            "artifact_fingerprint": hash_payload(_model_payload(character_bible)),
            "characters": len(characters),
        },
    )
    return _save_check(ctx, "character_bible", check)


def record_character_system_health(ctx: Any, *, character_system: Any) -> dict[str, Any]:
    relationships = list(getattr(character_system, "relationship_edges", []) or [])
    identity_links = list(getattr(character_system, "identity_links", []) or [])
    audit = list(getattr(character_system, "audit", []) or [])
    issues: list[dict[str, Any]] = []
    isolated = [
        str(getattr(item, "character_name", "") or "").strip()
        for item in audit
        if str(getattr(item, "code", "") or "").strip() == "isolated_active_character"
    ]
    isolated = [name for name in isolated if name]
    if isolated:
        issues.append(
            _issue(
                "high",
                "character_system_isolated_active_characters",
                "活跃关键人物缺少关系边，禁止进入蓝图与大纲：" + "、".join(isolated),
            )
        )
    non_blocking_audit = [
        item
        for item in audit
        if str(getattr(item, "code", "") or "").strip() != "isolated_active_character"
    ]
    if non_blocking_audit:
        issues.append(_issue("medium", "character_system_audit_not_empty", "角色系统审计存在待关注项。"))
    check = _check_payload(
        artifact="character_system",
        issues=issues,
        metadata={
            "artifact_fingerprint": hash_payload(_model_payload(character_system)),
            "relationships": len(relationships),
            "identity_links": len(identity_links),
            "audit": len(audit),
        },
    )
    return _save_check(ctx, "character_system", check)


def record_entity_graph_health(ctx: Any, *, entity_graph: Any) -> dict[str, Any]:
    entities = list(getattr(entity_graph, "entities", []) or [])
    links = list(getattr(entity_graph, "entity_links", []) or [])
    issues: list[dict[str, Any]] = []
    if not entities:
        issues.append(_issue("critical", "entity_graph_empty", "实体图谱没有实体。"))
    check = _check_payload(
        artifact="entity_graph",
        issues=issues,
        metadata={
            "artifact_fingerprint": hash_payload(_model_payload(entity_graph)),
            "entities": len(entities),
            "links": len(links),
        },
    )
    return _save_check(ctx, "entity_graph", check)


def record_creative_packet_health(ctx: Any, *, creative_packet: Any) -> dict[str, Any]:
    motifs = list(getattr(creative_packet, "signature_motifs", []) or [])
    tensions = list(getattr(creative_packet, "relationship_tensions", []) or [])
    issues: list[dict[str, Any]] = []
    if not motifs:
        issues.append(_issue("low", "creative_packet_no_motifs", "创作导演包缺少签名母题。"))
    if not tensions:
        issues.append(_issue("low", "creative_packet_no_tensions", "创作导演包缺少关系张力摘要。"))
    check = _check_payload(
        artifact="creative_director_packet",
        issues=issues,
        metadata={
            "artifact_fingerprint": hash_payload(_model_payload(creative_packet)),
            "motifs": len(motifs),
            "tensions": len(tensions),
        },
    )
    return _save_check(ctx, "creative_director_packet", check)


def assert_upstream_health_allows_progress(ctx: Any, *, artifact: str) -> None:
    """Raise when a just-recorded upstream artifact has blocking health issues."""
    report = load_upstream_health(ctx)
    checks = report.get("checks") if isinstance(report, dict) else {}
    check = checks.get(artifact) if isinstance(checks, dict) else None
    if isinstance(check, dict) and bool(check.get("blocked")):
        summary = str(check.get("summary") or f"{artifact} upstream health blocked")
        raise ValueError(summary)


def _save_check(ctx: Any, key: str, check: dict[str, Any]) -> dict[str, Any]:
    report = load_upstream_health(ctx)
    checks = report.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    checks[key] = check
    all_issues = [
        issue
        for item in checks.values()
        if isinstance(item, dict)
        for issue in list(item.get("issues", []) or [])
        if isinstance(issue, dict)
    ]
    blocked = any(_severity_rank(str(issue.get("severity") or "")) >= 3 for issue in all_issues)
    status = "blocked" if blocked else ("warning" if all_issues else "pass")
    report = {
        "schema_version": 1,
        "status": status,
        "checks": checks,
        "warnings": [
            str(issue.get("message") or issue.get("code") or "")
            for issue in all_issues
            if _severity_rank(str(issue.get("severity") or "")) < 3
        ],
        "blocked": blocked,
        "created_at": str(report.get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    ctx.storage.save_json(ctx.layout.reports_dir / "init_upstream_health.json", report)
    ctx.on_step(
        "init_upstream_health",
        {
            "artifact": key,
            "status": check["status"],
            "blocked": check["blocked"],
            "issues": len(check["issues"]),
            "path": str(ctx.layout.reports_dir / "init_upstream_health.json"),
        },
    )
    return report


def _check_payload(
    *,
    artifact: str,
    issues: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    blocked = any(_severity_rank(str(issue.get("severity") or "")) >= 3 for issue in issues)
    status = "blocked" if blocked else ("warning" if issues else "pass")
    return {
        "artifact": artifact,
        "status": status,
        "blocked": blocked,
        "issues": issues,
        "summary": f"{artifact} health {status}; issues={len(issues)}",
        "metadata": metadata,
        "checked_at": datetime.now(UTC).isoformat(),
        **metadata,
    }


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity.strip().lower(), 0)


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    return value
