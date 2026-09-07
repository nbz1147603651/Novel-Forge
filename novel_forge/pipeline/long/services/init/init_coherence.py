"""LLM-adjudicated initialization coherence gates and scoped patch safety."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from novel_forge.common.constants import severity_at_least
from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.utils.repair_target_resolver import (
    RepairResolverContext,
    RepairTargetResolver,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout

BLUEPRINT_ARTIFACT = "blueprint"
OUTLINE_ARTIFACT = "outline"
CHAPTER_CONTRACTS_ARTIFACT = "chapter_contracts"

INIT_READINESS_REPORT = "init_readiness.json"

_ARTIFACT_ALIASES: dict[str, str] = {
    "blueprint": BLUEPRINT_ARTIFACT,
    "narrative_blueprint": BLUEPRINT_ARTIFACT,
    "plans/narrative_blueprint.json": BLUEPRINT_ARTIFACT,
    "outline": OUTLINE_ARTIFACT,
    "story_outline": OUTLINE_ARTIFACT,
    "outline.json": OUTLINE_ARTIFACT,
    "chapter_contracts": CHAPTER_CONTRACTS_ARTIFACT,
    "contracts": CHAPTER_CONTRACTS_ARTIFACT,
    "plans/chapter_contracts.json": CHAPTER_CONTRACTS_ARTIFACT,
}

_LIST_KEY_BY_ARTIFACT: dict[str, str] = {
    OUTLINE_ARTIFACT: "chapters",
    CHAPTER_CONTRACTS_ARTIFACT: "chapter_contracts",
}

_STAGE_ARTIFACT_HINTS: dict[str, str] = {
    "blueprint_coherence": BLUEPRINT_ARTIFACT,
    "outline_inheritance": OUTLINE_ARTIFACT,
    "contract_coherence": CHAPTER_CONTRACTS_ARTIFACT,
    "claim_contract_coverage": CHAPTER_CONTRACTS_ARTIFACT,
    "source_artifacts": "source_artifacts",
}

_ARTIFACT_PATH_HINTS: dict[str, str] = {
    BLUEPRINT_ARTIFACT: "plans/narrative_blueprint.json",
    OUTLINE_ARTIFACT: "outline.json",
    CHAPTER_CONTRACTS_ARTIFACT: "plans/chapter_contracts.json",
    "source_artifacts": "source_artifacts/",
}

_DEFAULT_OUTLINE_FIELDS = {
    "goal",
    "beats_summary",
    "main_plot_points",
    "notes",
    "expected_hook",
    "expected_payoffs",
}

_DEFAULT_CONTRACT_FIELDS = {
    "entry_state_requirements",
    "required_events",
    "allowed_changes",
    "forbidden_changes",
    "promise_ops",
    "relationship_ops",
    "item_ops",
    "knowledge_ops",
    "new_character_candidates",
    "new_entity_candidates",
    "new_group_candidates",
    "new_collective_candidates",
    "new_organization_candidates",
    "new_location_candidates",
    "new_item_candidates",
    "new_concept_candidates",
    "exit_state_targets",
    "required_progressions",
    "allowed_progressions",
    "forbidden_progressions",
    "completion_criteria",
    "future_leak_risks",
}

_MISSING = object()
_TOP_LEVEL_LONG_TEXT_MIN_CHARS = 120
_TOP_LEVEL_TEXT_REPLACE_MIN_RATIO = 0.6
_REPAIR_SCOPE_RANGE_EXPANSION_LIMIT = 30
_MAX_SCOPE_FALLBACK_TARGETS = 48
_REPAIR_EVIDENCE_QUOTE_RE = re.compile(r"[「“\"]([^」”\"]{4,160})[」”\"]")
_TEXT_NORMALIZE_RE = re.compile(r"[\s，。？！、；：:,.!?;「」“”‘’'\"（）()《》【】\[\]{}\-—_]+")
_IRREVERSIBLE_MARKERS = (
    "最后一次",
    "不用再出来",
    "不再出来",
    "彻底",
    "完全",
    "完成核心人格整合",
    "人格整合初步完成",
    "融入主人格",
    "感知不到",
    "消失",
)
_BLUEPRINT_CHAPTER_RANGE_START_KEYS = ("chapter_start", "start_chapter", "from_chapter")
_BLUEPRINT_CHAPTER_RANGE_END_KEYS = ("chapter_end", "end_chapter", "to_chapter")
_BLUEPRINT_DIRECT_CHAPTER_KEYS = (
    "chapter",
    "chapter_number",
    "cognitive_chapter",
    "introduce_chapter",
    "resolve_chapter",
)
_BLUEPRINT_CHAPTER_LIST_KEYS = (
    "chapters",
    "chapter_numbers",
    "foreshadow_chapters",
    "payoff_chapters",
    "setup_chapters",
)


class InitCoherenceError(RuntimeError):
    """Raised when initialization coherence gates block progression."""


def init_readiness_path(layout: ProjectLayout) -> Path:
    """Return the canonical initialization readiness report path."""
    return layout.reports_dir / INIT_READINESS_REPORT


def normalize_artifact_key(value: Any) -> str:
    """Normalize user/LLM artifact labels into internal keys."""
    raw = str(value or "").strip().lower()
    return _ARTIFACT_ALIASES.get(raw, raw)


def normalize_coherence_report(
    report: dict[str, Any],
    *,
    artifact: str,
    default_summary: str = "未发现硬冲突。",
) -> dict[str, Any]:
    """Normalize an LLM coherence report to the shared gate shape."""
    issues = report.get("issues")
    normalized_issues = (
        [item for item in issues if isinstance(item, dict)] if isinstance(issues, list) else []
    )
    repair_scope = _as_list(report.get("repair_scope"))
    for issue in normalized_issues:
        repair_scope.extend(_as_list(issue.get("repair_scope")))
    return {
        "artifact": artifact,
        "verdict": str(report.get("verdict") or "ambiguous").strip().lower(),
        "issues": normalized_issues,
        "source_refs": _as_list(report.get("source_refs")),
        "repair_scope": repair_scope,
        "preserve": _as_list(report.get("preserve")),
        "change_intent": str(report.get("change_intent") or ""),
        "blocked": bool(report.get("blocked", False)),
        "summary": str(report.get("summary") or default_summary),
        **{
            key: value
            for key, value in report.items()
            if key
            not in {
                "artifact",
                "verdict",
                "issues",
                "source_refs",
                "repair_scope",
                "preserve",
                "change_intent",
                "blocked",
                "summary",
            }
        },
    }


def blocking_issues(report: dict[str, Any], *, min_severity: str = "high") -> list[dict[str, Any]]:
    """Return issues severe enough to block the init gate."""
    result: list[dict[str, Any]] = []
    for issue in report.get("issues", []) or []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "medium").strip().lower()
        if severity_at_least(severity, min_severity):
            result.append(issue)
    return result


def coherence_blocks(report: dict[str, Any], *, min_severity: str = "high") -> bool:
    """Return True when a coherence report should block progression."""
    verdict = str(report.get("verdict") or "accept").strip().lower()
    if verdict in {"reject", "needs_repair"}:
        return True
    if bool(report.get("blocked", False)):
        return True
    return bool(blocking_issues(report, min_severity=min_severity))


def has_repair_scope(
    report: dict[str, Any],
    *,
    artifact: str,
    min_severity: str = "high",
) -> bool:
    """Return True when blocking issues have at least one usable local repair scope."""
    top_level_scopes = _as_list(report.get("repair_scope"))
    severe_issues = blocking_issues(report, min_severity=min_severity)
    if severe_issues:
        for issue in severe_issues:
            issue_scopes = _as_list(issue.get("repair_scope")) if isinstance(issue, dict) else []
            scoped_report = {"repair_scope": issue_scopes or top_level_scopes, "issues": []}
            if not _has_usable_scope(
                collect_repair_scopes(scoped_report, default_artifact=artifact),
                artifact=artifact,
            ):
                return False
        return True
    return _has_usable_scope(
        collect_repair_scopes(report, default_artifact=artifact), artifact=artifact
    )


def _has_usable_scope(scopes: list["RepairScope"], *, artifact: str) -> bool:
    if not scopes:
        return False
    if artifact == BLUEPRINT_ARTIFACT:
        return any(scope.fields for scope in scopes)
    return any(scope.chapters and scope.fields for scope in scopes)


def collect_repair_scopes(
    report: dict[str, Any],
    *,
    default_artifact: str,
) -> list["RepairScope"]:
    """Collect top-level and issue-level repair scopes from a report."""
    raw_scopes: list[Any] = []
    raw_scopes.extend(_as_list(report.get("repair_scope")))
    for issue in report.get("issues", []) or []:
        if isinstance(issue, dict):
            raw_scopes.extend(_as_list(issue.get("repair_scope")))
            raw_scopes.extend(_scopes_from_audit_repair_targets(issue, default_artifact))

    scopes: list[RepairScope] = []
    for raw in raw_scopes:
        if not isinstance(raw, dict):
            continue
        artifact = normalize_artifact_key(raw.get("artifact") or default_artifact)
        if artifact != default_artifact:
            continue
        fields = _coerce_str_set(raw.get("fields") or raw.get("field_whitelist"))
        if artifact == BLUEPRINT_ARTIFACT:
            fields = {_normalize_blueprint_scope_field(field) for field in fields if field}
        scopes.append(
            RepairScope(
                artifact=artifact,
                chapters=_coerce_scope_chapters(raw),
                fields=fields,
                operation=str(raw.get("operation") or "field_replace").strip().lower(),
                issue_ids=_coerce_str_set(raw.get("issue_ids") or raw.get("issues")),
            )
        )
    return scopes


def _scopes_from_audit_repair_targets(
    issue: dict[str, Any],
    default_artifact: str,
) -> list[dict[str, Any]]:
    """Convert AuditIssueV2 repair locators into legacy scope allowances."""

    scopes: list[dict[str, Any]] = []
    issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
    for raw in _as_list(issue.get("repair_targets")):
        if not isinstance(raw, dict):
            continue
        target_format = str(raw.get("target_format") or "").strip()
        if target_format != "json_artifact":
            continue
        artifact = normalize_artifact_key(raw.get("artifact") or default_artifact)
        if artifact != default_artifact:
            continue
        fields = _coerce_str_set(raw.get("field"))
        json_pointer = str(raw.get("json_pointer") or "").strip()
        if json_pointer:
            root = json_pointer.strip("/").split("/", 1)[0]
            if root:
                fields.add(
                    _normalize_blueprint_scope_field(root)
                    if artifact == BLUEPRINT_ARTIFACT
                    else root
                )
        if not fields:
            continue
        chapter_numbers = set(_coerce_scope_chapters(raw))
        if raw.get("chapter_number"):
            number = _coerce_int(raw.get("chapter_number"))
            if number:
                chapter_numbers.add(number)
        scopes.append(
            {
                "artifact": artifact,
                "chapters": sorted(chapter_numbers),
                "fields": sorted(fields),
                "operation": "field_replace",
                "issue_ids": [issue_id] if issue_id else [],
            }
        )
    return scopes


@dataclass(frozen=True)
class RepairScope:
    """Local repair allowance produced by the LLM adjudicator."""

    artifact: str
    chapters: set[int]
    fields: set[str]
    operation: str
    issue_ids: set[str]


@dataclass(frozen=True)
class RepairTarget:
    """A concrete, code-verified edit target for initialization repair."""

    artifact: str
    target_id: str
    path: str
    chapter_number: int | None
    field: str
    current_value: Any
    current_hash: str
    issue_ids: tuple[str, ...]
    match_reason: str
    title: str = ""
    evidence_hit: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return the compact target shape passed to the repair prompt."""
        payload: dict[str, Any] = {
            "target_id": self.target_id,
            "artifact": self.artifact,
            "path": self.path,
            "field": self.field,
            "current_value": self.current_value,
            "current_hash": self.current_hash,
            "issue_ids": list(self.issue_ids),
            "match_reason": self.match_reason,
        }
        if self.chapter_number is not None:
            payload["chapter_number"] = self.chapter_number
        if self.title:
            payload["title"] = self.title
        if self.evidence_hit:
            payload["evidence_hit"] = self.evidence_hit
        return payload


def backup_init_artifact(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    artifact: str,
    payload: dict[str, Any],
    round_index: int,
) -> None:
    """Persist a pre-repair backup for an initialization artifact."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = layout.states_dir / "init_v2" / "repair_backups"
    storage.save_json(
        backup_dir / f"{artifact}_round_{round_index}_{stamp}.json",
        payload,
    )


def resolve_init_repair_targets(
    artifact: str,
    payload: dict[str, Any],
    report: dict[str, Any],
    scopes: list[RepairScope],
) -> list[RepairTarget]:
    """Resolve adjudication repair scopes into concrete edit targets.

    The LLM is good at rewriting a verified field value, but unreliable at
    producing JSON Pointer paths or byte-exact old-value preconditions.  This
    resolver keeps path selection in code: it first limits candidates by the
    adjudicator's scope, then looks for issue evidence in the current payload.
    """

    normalized_artifact = normalize_artifact_key(artifact)
    if not _has_usable_scope(scopes, artifact=normalized_artifact):
        return []

    issues = [item for item in report.get("issues", []) or [] if isinstance(item, dict)]
    if not issues:
        issues = [
            {
                "id": "report",
                "description": report.get("summary", ""),
                "repair_scope": report.get("repair_scope", []),
            }
        ]

    targets: dict[str, RepairTarget] = {}
    v2_targets = _resolve_v2_init_repair_targets(
        artifact=normalized_artifact,
        payload=payload,
        issues=issues,
    )
    uses_audit_v2_targets = str(report.get("schema_version") or "").strip() == "audit_v2" or any(
        isinstance(issue, dict) and ("repair_targets" in issue or "reference_targets" in issue)
        for issue in issues
    )
    for target in v2_targets:
        targets[target.target_id] = target
    if targets or uses_audit_v2_targets:
        return list(targets.values())

    candidate_map = {
        target.target_id: target
        for target in _iter_repair_target_candidates(
            artifact=normalized_artifact,
            payload=payload,
            scopes=scopes,
        )
    }
    all_candidates = list(candidate_map.values())
    for issue in issues:
        issue_id_values = _issue_identity_values(issue)
        fragments = _issue_evidence_fragments(issue, report)
        ranked_issue_matches: list[tuple[int, int, RepairTarget]] = []
        for candidate in all_candidates:
            current_text = (
                candidate.current_value if isinstance(candidate.current_value, str) else ""
            )
            evidence_hit = _matching_evidence_fragment(fragments, current_text)
            if evidence_hit:
                ranked_issue_matches.append(
                    (
                        _fragment_match_priority(evidence_hit, current_text),
                        len(_normalize_repair_text(current_text)),
                        _with_issue_match(
                            candidate,
                            issue_ids=issue_id_values,
                            match_reason="evidence",
                            evidence_hit=evidence_hit,
                        ),
                    )
                )
                continue
            if _issue_prefers_irreversible_markers(issue) and _text_has_irreversible_marker(
                current_text
            ):
                ranked_issue_matches.append(
                    (
                        3,
                        len(_normalize_repair_text(current_text)),
                        _with_issue_match(
                            candidate,
                            issue_ids=issue_id_values,
                            match_reason="irreversible_marker",
                            evidence_hit="",
                        ),
                    )
                )

        ranked_issue_matches.sort(key=lambda item: (item[0], item[1]))
        issue_matches = [item[2] for item in ranked_issue_matches]

        if not issue_matches and len(all_candidates) == 1:
            for candidate in all_candidates[:_MAX_SCOPE_FALLBACK_TARGETS]:
                issue_matches.append(
                    _with_issue_match(
                        candidate,
                        issue_ids=issue_id_values,
                        match_reason="scope_fallback",
                        evidence_hit="",
                    )
                )

        for target in issue_matches:
            targets[target.target_id] = _merge_repair_target_issue_ids(
                targets.get(target.target_id),
                target,
            )
    return list(targets.values())


def _resolve_v2_init_repair_targets(
    *,
    artifact: str,
    payload: dict[str, Any],
    issues: list[dict[str, Any]],
) -> list[RepairTarget]:
    resolved: list[RepairTarget] = []
    resolver = RepairTargetResolver(
        RepairResolverContext(
            artifacts={artifact: payload},
            min_confidence=0.58,
            ambiguity_margin=0.04,
        )
    )
    for issue in issues:
        if not isinstance(issue, dict) or not issue.get("repair_targets"):
            continue
        try:
            audit_issue = AuditIssueV2.model_validate(issue)
        except Exception:
            continue
        for target in resolver.resolve_issue(audit_issue):
            if target.resolution_status != "resolved" or target.target_format != "json_artifact":
                continue
            if (
                target.locator.artifact
                and normalize_artifact_key(target.locator.artifact) != artifact
            ):
                continue
            resolved.append(_repair_target_from_resolved(target, artifact=artifact))
    return resolved


def _repair_target_from_resolved(target: ResolvedRepairTarget, *, artifact: str) -> RepairTarget:
    locator = target.locator
    field = _normalize_blueprint_scope_field(
        locator.field or target.path.strip("/").split("/", 1)[0]
    )
    if artifact != BLUEPRINT_ARTIFACT and not field:
        parts = _split_json_pointer(target.path)
        field = parts[2] if len(parts) >= 3 else (parts[0] if parts else "")
    chapter_number = locator.chapter_number
    if chapter_number is None and locator.chapter_range:
        chapter_number = locator.chapter_range[0]
    return RepairTarget(
        artifact=artifact,
        target_id=target.target_id,
        path=target.path,
        chapter_number=chapter_number,
        field=field,
        current_value=target.current_value,
        current_hash=target.current_hash,
        issue_ids=tuple(target.issue_ids),
        match_reason=target.reason or "audit_v2_locator",
        evidence_hit=locator.quote,
    )


def apply_targeted_init_patch(
    *,
    artifact: str,
    payload: dict[str, Any],
    patch_payload: dict[str, Any],
    target_map: dict[str, RepairTarget],
    scopes: list[RepairScope],
    max_ops: int,
    strict: bool = True,
    skipped: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply target-id based repair patches against code-resolved paths."""

    patches = patch_payload.get("patches")
    if isinstance(patches, dict):
        patches = [patches]
    if not isinstance(patches, list):
        raise InitCoherenceError("初始化修复输出缺少 patches 数组。")
    if len(patches) > max_ops:
        if strict:
            raise InitCoherenceError(f"初始化修复 patch 操作数 {len(patches)} 超过上限 {max_ops}。")
        overflow = patches[max_ops:]
        patches = patches[:max_ops]
        if skipped is not None:
            for overflow_index, overflow_patch in enumerate(overflow, start=max_ops):
                target_id = (
                    str(overflow_patch.get("target_id") or "").strip()
                    if isinstance(overflow_patch, dict)
                    else ""
                )
                target = target_map.get(target_id)
                skipped.append(
                    {
                        "index": overflow_index,
                        "target_id": target_id,
                        "path": target.path if target is not None else "",
                        "op": "replace",
                        "reason": (
                            f"patch 操作数超过本轮上限 {max_ops}，已延后到后续精准修复轮次。"
                        ),
                    }
                )

    normalized_artifact = normalize_artifact_key(artifact)
    applied: list[dict[str, Any]] = []
    result = copy.deepcopy(payload)
    for index, patch in enumerate(patches):
        try:
            if not isinstance(patch, dict):
                raise InitCoherenceError(f"第 {index + 1} 个 patch 不是对象。")
            target_id = str(patch.get("target_id") or "").strip()
            if not target_id:
                raise InitCoherenceError("target patch 缺少 target_id。")
            target = target_map.get(target_id)
            if target is None:
                raise InitCoherenceError(f"未知 repair target_id：{target_id}")
            if "value" not in patch:
                raise InitCoherenceError("target patch 缺少 value。")
            _assert_path_allowed(
                artifact=normalized_artifact,
                payload=result,
                path=target.path,
                scopes=scopes,
            )
            normalized_path, old_hash = _replace_json_pointer(
                result,
                target.path,
                patch.get("value"),
                expected_old_value=_MISSING,
                expected_old_hash=target.current_hash,
            )
            patch_issue_ids = _as_list(patch.get("issue_ids")) or list(target.issue_ids)
            applied.append(
                {
                    "op": "replace",
                    "target_id": target.target_id,
                    "path": normalized_path,
                    "chapter_number": target.chapter_number,
                    "field": target.field,
                    "issue_ids": patch_issue_ids,
                    "rationale": str(patch.get("rationale") or ""),
                    "precondition": {
                        "expected_old_value_checked": False,
                        "expected_old_hash": target.current_hash,
                        "actual_old_hash": old_hash,
                    },
                }
            )
        except InitCoherenceError as exc:
            if strict:
                raise
            if skipped is not None:
                skipped.append(
                    {
                        "index": index,
                        "target_id": patch.get("target_id") if isinstance(patch, dict) else "",
                        "path": target_map.get(str(patch.get("target_id") or "").strip()).path
                        if isinstance(patch, dict)
                        and str(patch.get("target_id") or "").strip() in target_map
                        else "",
                        "op": "replace",
                        "reason": str(exc),
                    }
                )
    return result, applied


def apply_scoped_init_patch(
    *,
    artifact: str,
    payload: dict[str, Any],
    patch_payload: dict[str, Any],
    scopes: list[RepairScope],
    max_ops: int,
    strict: bool = True,
    skipped: list[dict[str, Any]] | None = None,
    issue_evidence_by_id: dict[str, list[str]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate and apply a restricted JSON Patch payload."""
    patches = patch_payload.get("patches")
    if isinstance(patches, dict):
        patches = [patches]
    if not isinstance(patches, list):
        raise InitCoherenceError("初始化修复输出缺少 patches 数组。")
    if len(patches) > max_ops:
        if strict:
            raise InitCoherenceError(f"初始化修复 patch 操作数 {len(patches)} 超过上限 {max_ops}。")
        overflow = patches[max_ops:]
        patches = patches[:max_ops]
        if skipped is not None:
            for overflow_index, overflow_patch in enumerate(overflow, start=max_ops):
                skipped.append(
                    {
                        "index": overflow_index,
                        "path": overflow_patch.get("path")
                        if isinstance(overflow_patch, dict)
                        else "",
                        "op": overflow_patch.get("op") if isinstance(overflow_patch, dict) else "",
                        "reason": (
                            f"patch 操作数超过本轮上限 {max_ops}，已延后到后续精准修复轮次。"
                        ),
                    }
                )

    normalized_artifact = normalize_artifact_key(artifact)
    applied: list[dict[str, Any]] = []
    result = copy.deepcopy(payload)
    for index, patch in enumerate(patches):
        try:
            if not isinstance(patch, dict):
                raise InitCoherenceError(f"第 {index + 1} 个 patch 不是对象。")
            op = str(patch.get("op") or "").strip().lower()
            if op == "edit" and not strict:
                op = "replace"
            if op != "replace":
                raise InitCoherenceError(
                    f"初始化局部修复 v2 只允许 replace，拒绝 {op or '空 op'}。"
                )
            if "value" not in patch:
                raise InitCoherenceError("replace patch 缺少 value。")
            path = str(patch.get("path") or "").strip()
            if not path.startswith("/"):
                raise InitCoherenceError(f"patch path 必须是 JSON Pointer：{path!r}")
            _assert_path_allowed(
                artifact=normalized_artifact,
                payload=result,
                path=path,
                scopes=scopes,
            )
            _assert_patch_targets_issue_evidence(
                result,
                path=path,
                replacement=patch.get("value"),
                issue_ids=_as_list(patch.get("issue_ids")),
                issue_evidence_by_id=issue_evidence_by_id,
            )
            expected_old_value = (
                patch.get("expected_old_value") if "expected_old_value" in patch else _MISSING
            )
            expected_old_hash = str(patch.get("expected_old_hash") or "").strip()
            # Auto-fill expected_old_hash for long text fields when missing.
            # In the init repair flow (strict=False), the blueprint was freshly
            # generated so the current value is guaranteed to be the one the LLM
            # saw when generating the patch.
            if not strict and expected_old_value is _MISSING and not expected_old_hash:
                parts = _split_json_pointer(path)
                if len(parts) == 1 and isinstance(result, dict) and parts[0] in result:
                    current_val = result[parts[0]]
                    if (
                        isinstance(current_val, str)
                        and len(current_val.strip()) >= _TOP_LEVEL_LONG_TEXT_MIN_CHARS
                    ):
                        expected_old_hash = _stable_value_hash(current_val)
            normalized_path, old_hash = _replace_json_pointer(
                result,
                path,
                patch.get("value"),
                expected_old_value=expected_old_value,
                expected_old_hash=expected_old_hash,
            )
            applied_record: dict[str, Any] = {
                "op": op,
                "path": normalized_path,
                "issue_ids": _as_list(patch.get("issue_ids")),
                "rationale": str(patch.get("rationale") or ""),
            }
            if expected_old_value is not _MISSING or expected_old_hash:
                applied_record["precondition"] = {
                    "expected_old_value_checked": expected_old_value is not _MISSING,
                    "expected_old_hash": expected_old_hash,
                    "actual_old_hash": old_hash,
                }
            applied.append(applied_record)
        except InitCoherenceError as exc:
            if strict:
                raise
            if skipped is not None:
                skipped.append(
                    {
                        "index": index,
                        "path": patch.get("path") if isinstance(patch, dict) else "",
                        "op": patch.get("op") if isinstance(patch, dict) else "",
                        "reason": str(exc),
                    }
                )
    return result, applied


def build_init_readiness_report(
    *,
    reports: dict[str, dict[str, Any] | None],
    repairs: list[dict[str, Any]],
    min_severity: str,
    required: bool,
) -> dict[str, Any]:
    """Build the final initialization readiness report."""
    stage_status: dict[str, Any] = {}
    remaining: list[dict[str, Any]] = []
    allowed = True
    total_claims = 0
    total_candidates = 0
    total_extracted_claims = 0
    max_active_claims = 0
    degraded_memory = False
    claim_ledger_path = ""
    for stage, report in reports.items():
        if report is None:
            stage_status[stage] = {"verdict": "skipped", "blocked": False, "summary": "未运行。"}
            continue
        blocked = coherence_blocks(report, min_severity=min_severity)
        if blocked:
            allowed = False
        claims_count = int(report.get("claims_count", 0) or 0)
        extracted_claims_count = int(report.get("extracted_claims_count", claims_count) or 0)
        active_claims_count = int(report.get("active_claims_count", claims_count) or 0)
        total_claims += claims_count
        total_extracted_claims += extracted_claims_count
        max_active_claims = max(max_active_claims, active_claims_count)
        total_candidates += int(report.get("candidate_count", 0) or 0)
        degraded_memory = degraded_memory or bool(report.get("degraded_memory", False))
        if report.get("claim_ledger_path"):
            claim_ledger_path = str(report.get("claim_ledger_path") or "")
        stage_status[stage] = {
            "verdict": report.get("verdict", "ambiguous"),
            "blocked": blocked,
            "summary": report.get("summary", ""),
            "issue_count": len(report.get("issues", []) or []),
            "blocking_issue_count": len(blocking_issues(report, min_severity=min_severity)),
            "claims_count": claims_count,
            "extracted_claims_count": extracted_claims_count,
            "active_claims_count": active_claims_count,
            "candidate_count": int(report.get("candidate_count", 0) or 0),
            "degraded_memory": bool(report.get("degraded_memory", False)),
            "claim_ledger_path": str(report.get("claim_ledger_path") or ""),
        }
        auto_repair = report.get("auto_repair_applied")
        if isinstance(auto_repair, dict):
            stage_status[stage]["auto_repair"] = {
                "fix_count": int(auto_repair.get("fix_count") or 0),
                "resolved_issue_ids": _as_list(auto_repair.get("resolved_issue_ids")),
                "unresolved_issue_ids": _as_list(auto_repair.get("unresolved_issue_ids")),
                "skipped_reason": str(auto_repair.get("skipped_reason") or ""),
            }
        for issue in blocking_issues(report, min_severity=min_severity):
            remaining.append({"stage": stage, **issue})

    if not required:
        allowed = True
    repair_effectiveness = _summarize_init_repair_effectiveness(
        repairs,
        remaining_issues=remaining,
    )
    recovery_actions = _build_init_recovery_actions(
        remaining,
        repair_effectiveness=repair_effectiveness,
        allowed=allowed,
    )
    return {
        "allowed": allowed,
        "required": required,
        "block_min_severity": min_severity,
        "stages": stage_status,
        "repairs": repairs,
        "repair_effectiveness": repair_effectiveness,
        "recovery_actions": recovery_actions,
        "remaining_issues": remaining,
        "claims_count": total_claims,
        "extracted_claims_count": total_extracted_claims,
        "active_claims_count": max_active_claims,
        "candidate_count": total_candidates,
        "degraded_memory": degraded_memory,
        "claim_ledger_path": claim_ledger_path,
        "summary": (
            "初始化准入通过，可以进入章节生成。"
            if allowed
            else f"初始化准入阻断：仍有 {len(remaining)} 个 {min_severity}+ 问题。"
        ),
    }


def _build_init_recovery_actions(
    remaining_issues: list[dict[str, Any]],
    *,
    repair_effectiveness: dict[str, Any],
    allowed: bool,
) -> list[dict[str, Any]]:
    """Return machine-readable next actions for blocked init readiness."""

    if allowed or not remaining_issues:
        return []
    artifacts = _remaining_issue_artifacts(remaining_issues)
    unresolved_ids = _as_list(repair_effectiveness.get("remaining_issue_ids"))
    touched_ids = _as_list(repair_effectiveness.get("remaining_touched_issue_ids"))
    return [
        {
            "id": "ai_retry_repair",
            "kind": "ai_repair",
            "label": "AI 修复并复审",
            "description": "基于剩余阻断问题重新生成局部 patch；适合用户希望系统继续尝试时使用。",
            "artifacts": artifacts,
            "artifact_paths": [
                _ARTIFACT_PATH_HINTS.get(artifact, artifact) for artifact in artifacts
            ],
            "issue_ids": unresolved_ids,
            "previous_attempt_touched_issue_ids": touched_ids,
            "options": _build_ai_repair_options(remaining_issues),
        },
        {
            "id": "manual_artifact_edit",
            "kind": "manual_repair",
            "label": "人工修复",
            "description": "打开具体矛盾点和关联产物，由用户手动修改后触发复审。",
            "artifacts": artifacts,
            "artifact_paths": [
                _ARTIFACT_PATH_HINTS.get(artifact, artifact) for artifact in artifacts
            ],
            "issue_ids": unresolved_ids,
        },
        {
            "id": "rerun_readiness",
            "kind": "verify",
            "label": "重新校验",
            "description": "不重新生成内容，只重新运行初始化一致性准入检查。",
            "artifacts": artifacts,
            "artifact_paths": [
                _ARTIFACT_PATH_HINTS.get(artifact, artifact) for artifact in artifacts
            ],
            "issue_ids": unresolved_ids,
        },
    ]


def _remaining_issue_artifacts(remaining_issues: list[dict[str, Any]]) -> list[str]:
    artifacts: list[str] = []

    def add(value: Any) -> None:
        artifact = normalize_artifact_key(value)
        if artifact and artifact not in artifacts:
            artifacts.append(artifact)

    for issue in remaining_issues:
        if not isinstance(issue, dict):
            continue
        scopes = [scope for scope in _as_list(issue.get("repair_scope")) if isinstance(scope, dict)]
        for scope in scopes:
            add(scope.get("artifact"))
        if scopes:
            continue
        stage = str(issue.get("stage") or "").strip()
        add(_STAGE_ARTIFACT_HINTS.get(stage, ""))
    return artifacts


def _build_ai_repair_options(remaining_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for issue in remaining_issues:
        if not isinstance(issue, dict):
            continue
        issue_id = init_coherence_issue_id(issue)
        issue_type = str(issue.get("issue_type") or "").strip()
        if issue_type == "chapter_number_contradiction":
            directions = [
                {
                    "id": f"{issue_id}:unify_event_chapter",
                    "label": "统一事件章节",
                    "description": "选择一个正史发生章节，并把其他字段改为铺垫、回声或删除。",
                },
                {
                    "id": f"{issue_id}:split_event_meaning",
                    "label": "拆分相似事件",
                    "description": "保留多个章节，但明确它们是不同阶段、不同动作或不同认知层级。",
                },
            ]
        elif issue_type == "foreshadow_after_reveal_violation":
            directions = [
                {
                    "id": f"{issue_id}:remove_late_foreshadow",
                    "label": "删除晚到伏笔",
                    "description": "移除晚于揭示/兑现章节的 foreshadow_chapters，保留合法铺垫。",
                },
                {
                    "id": f"{issue_id}:move_reveal_later",
                    "label": "后移揭示事件",
                    "description": "若晚章节才是真正揭示，则同步后移 reveal/payoff 章节。",
                },
            ]
        else:
            directions = [
                {
                    "id": f"{issue_id}:minimal_patch",
                    "label": "最小局部修复",
                    "description": "只修改 repair_scope 指向字段，优先保留已经生成的蓝图和大纲内容。",
                },
                {
                    "id": f"{issue_id}:regenerate_scoped_fragment",
                    "label": "重写相关片段",
                    "description": "重写受影响字段片段，用一致性裁判证据约束新版本。",
                },
            ]
        options.append(
            {
                "issue_id": issue_id,
                "issue_type": issue_type or "unknown",
                "summary": str(issue.get("description") or issue.get("summary") or "")[:500],
                "directions": directions,
            }
        )
    return options


def init_coherence_issue_id(issue: dict[str, Any]) -> str:
    """Return a stable issue id for repair bookkeeping.

    This is only a ledger key. Semantic truth remains owned by the LLM
    adjudication report that produced the issue.
    """
    for key in ("issue_id", "id", "claim_id", "candidate_id"):
        value = str(issue.get(key) or "").strip()
        if value:
            return value
    raw = json.dumps(
        {
            "severity": issue.get("severity"),
            "description": str(issue.get("description") or issue.get("summary") or "")[:300],
            "repair_scope": issue.get("repair_scope"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "init_issue_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _issue_ids(issues: list[dict[str, Any]]) -> list[str]:
    return sorted({init_coherence_issue_id(issue) for issue in issues if isinstance(issue, dict)})


def _summarize_init_repair_effectiveness(
    repairs: list[dict[str, Any]],
    *,
    remaining_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    remaining_issue_ids = set(_issue_ids(remaining_issues))
    source_issue_ids: set[str] = set()
    applied_issue_ids: set[str] = set()
    explicit_resolved_issue_ids: set[str] = set()
    skipped_patch_count = 0
    patch_count = 0
    patch_precondition_count = 0
    target_count = 0
    located_count = 0
    applied_target_count = 0
    skipped_target_count = 0
    rounds_by_artifact: dict[str, int] = {}
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        artifact = str(repair.get("artifact") or "").strip() or "unknown"
        rounds_by_artifact[artifact] = rounds_by_artifact.get(artifact, 0) + 1
        source_issue_ids.update(
            str(item) for item in _as_list(repair.get("source_issue_ids")) if item
        )
        explicit_resolved_issue_ids.update(
            str(item) for item in _as_list(repair.get("resolved_issue_ids")) if item
        )
        target_count += int(repair.get("target_count", 0) or 0)
        located_count += int(repair.get("located_count", 0) or 0)
        for patch in repair.get("patches") or []:
            if not isinstance(patch, dict):
                continue
            patch_count += 1
            if patch.get("target_id"):
                applied_target_count += 1
            if isinstance(patch.get("precondition"), dict):
                patch_precondition_count += 1
            applied_issue_ids.update(str(item) for item in _as_list(patch.get("issue_ids")) if item)
        skipped_patch_count += int(repair.get("skipped_patch_count", 0) or 0)
        for patch in repair.get("skipped_patches") or []:
            if isinstance(patch, dict) and patch.get("target_id"):
                skipped_target_count += 1

    resolved_ids = sorted((explicit_resolved_issue_ids | applied_issue_ids) - remaining_issue_ids)
    remaining_touched_ids = sorted((source_issue_ids | applied_issue_ids) & remaining_issue_ids)
    return {
        "schema_version": 1,
        "source": "explicit_resolved_ids_plus_patch_records",
        "repair_round_count": len([item for item in repairs if isinstance(item, dict)]),
        "rounds_by_artifact": rounds_by_artifact,
        "patch_count": patch_count,
        "skipped_patch_count": skipped_patch_count,
        "patch_precondition_count": patch_precondition_count,
        "target_count": target_count,
        "located_count": located_count,
        "applied_target_count": applied_target_count,
        "skipped_target_count": skipped_target_count,
        "source_issue_ids": sorted(source_issue_ids),
        "applied_issue_ids": sorted(applied_issue_ids),
        "explicit_resolved_issue_ids": sorted(explicit_resolved_issue_ids),
        "remaining_issue_ids": sorted(remaining_issue_ids),
        "resolved_issue_ids": resolved_ids,
        "remaining_touched_issue_ids": remaining_touched_ids,
        "resolved_issue_count": len(resolved_ids),
        "remaining_touched_issue_count": len(remaining_touched_ids),
    }


def _iter_repair_target_candidates(
    *,
    artifact: str,
    payload: dict[str, Any],
    scopes: list[RepairScope],
) -> list[RepairTarget]:
    if artifact == BLUEPRINT_ARTIFACT:
        return _iter_blueprint_repair_targets(payload=payload, scopes=scopes)
    list_key = _LIST_KEY_BY_ARTIFACT.get(artifact)
    if list_key is None:
        return []
    items = payload.get(list_key)
    if not isinstance(items, list):
        return []
    targets: list[RepairTarget] = []
    default_fields = (
        _DEFAULT_OUTLINE_FIELDS if artifact == OUTLINE_ARTIFACT else _DEFAULT_CONTRACT_FIELDS
    )
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        chapter_number = _coerce_int(item.get("chapter_number"))
        title = str(item.get("title") or "")
        for scope in scopes:
            if chapter_number not in scope.chapters:
                continue
            fields = scope.fields or set(default_fields)
            for field in sorted(fields):
                if field not in item or field == "chapter_number":
                    continue
                targets.extend(
                    _targets_for_field_value(
                        artifact=artifact,
                        chapter_number=chapter_number,
                        title=title,
                        field=field,
                        value=item[field],
                        base_path=_join_json_pointer([list_key, str(item_index), field]),
                    )
                )
    return targets


def _iter_blueprint_repair_targets(
    *,
    payload: dict[str, Any],
    scopes: list[RepairScope],
) -> list[RepairTarget]:
    targets: list[RepairTarget] = []
    for scope in scopes:
        for field in sorted(scope.fields):
            field = _normalize_blueprint_scope_field(field)
            if field not in payload:
                continue
            field_targets = _targets_for_field_value(
                artifact=BLUEPRINT_ARTIFACT,
                chapter_number=None,
                title="",
                field=field,
                value=payload[field],
                base_path=_join_json_pointer([field]),
            )
            if scope.chapters:
                field_targets = [
                    target
                    for target in field_targets
                    if _blueprint_path_allowed_by_scope(
                        payload=payload,
                        parts=_split_json_pointer(target.path),
                        scope=scope,
                    )
                ]
            targets.extend(field_targets)
    return targets


def _targets_for_field_value(
    *,
    artifact: str,
    chapter_number: int | None,
    title: str,
    field: str,
    value: Any,
    base_path: str,
) -> list[RepairTarget]:
    targets: list[RepairTarget] = []

    def walk(current: Any, path: str, suffix_parts: tuple[str, ...]) -> None:
        if isinstance(current, str):
            direct_list_index = (
                int(suffix_parts[0])
                if len(suffix_parts) == 1 and suffix_parts[0].isdigit()
                else None
            )
            target_suffix = None if direct_list_index is not None else suffix_parts or None
            targets.append(
                _make_repair_target(
                    artifact=artifact,
                    chapter_number=chapter_number,
                    title=title,
                    field=field,
                    value=current,
                    path=path,
                    sub_index=direct_list_index,
                    target_suffix=target_suffix,
                )
            )
            return
        if isinstance(current, list):
            for index, item in enumerate(current):
                if isinstance(item, (str, list, dict)):
                    walk(
                        item,
                        _join_json_pointer([*_split_json_pointer(path), str(index)]),
                        (*suffix_parts, str(index)),
                    )
            return
        if isinstance(current, dict):
            for key, item in current.items():
                if isinstance(key, str) and isinstance(item, (str, list, dict)):
                    walk(
                        item,
                        _join_json_pointer([*_split_json_pointer(path), key]),
                        (*suffix_parts, key),
                    )

    walk(value, base_path, ())
    return targets


def _make_repair_target(
    *,
    artifact: str,
    chapter_number: int | None,
    title: str,
    field: str,
    value: Any,
    path: str,
    sub_index: int | None,
    target_suffix: tuple[str, ...] | None = None,
) -> RepairTarget:
    if target_suffix:
        suffix = ":".join(_target_id_part(part) for part in target_suffix)
        if chapter_number is None:
            target_id = f"{artifact}:{field}:{suffix}"
        else:
            target_id = f"{artifact}:{chapter_number}:{field}:{suffix}"
    elif chapter_number is None:
        target_id = f"{artifact}:{field}"
    elif sub_index is None:
        target_id = f"{artifact}:{chapter_number}:{field}"
    else:
        target_id = f"{artifact}:{chapter_number}:{field}:{sub_index}"
    return RepairTarget(
        artifact=artifact,
        target_id=target_id,
        path=path,
        chapter_number=chapter_number,
        field=field,
        current_value=value,
        current_hash=_stable_value_hash(value),
        issue_ids=(),
        match_reason="candidate",
        title=title,
    )


def _target_id_part(value: str) -> str:
    return str(value).replace("%", "%25").replace(":", "%3A").replace("/", "%2F")


def _with_issue_match(
    target: RepairTarget,
    *,
    issue_ids: tuple[str, ...],
    match_reason: str,
    evidence_hit: str,
) -> RepairTarget:
    return RepairTarget(
        artifact=target.artifact,
        target_id=target.target_id,
        path=target.path,
        chapter_number=target.chapter_number,
        field=target.field,
        current_value=target.current_value,
        current_hash=target.current_hash,
        issue_ids=tuple(sorted(set(target.issue_ids) | set(issue_ids))),
        match_reason=match_reason,
        title=target.title,
        evidence_hit=evidence_hit,
    )


def _merge_repair_target_issue_ids(
    existing: RepairTarget | None,
    target: RepairTarget,
) -> RepairTarget:
    if existing is None:
        return target
    issue_ids = tuple(sorted(set(existing.issue_ids) | set(target.issue_ids)))
    evidence_hit = existing.evidence_hit or target.evidence_hit
    match_reason = existing.match_reason
    if existing.match_reason == "scope_fallback" and target.match_reason != "scope_fallback":
        match_reason = target.match_reason
    return RepairTarget(
        artifact=existing.artifact,
        target_id=existing.target_id,
        path=existing.path,
        chapter_number=existing.chapter_number,
        field=existing.field,
        current_value=existing.current_value,
        current_hash=existing.current_hash,
        issue_ids=issue_ids,
        match_reason=match_reason,
        title=existing.title,
        evidence_hit=evidence_hit,
    )


def _issue_identity_values(issue: dict[str, Any]) -> tuple[str, ...]:
    values = {init_coherence_issue_id(issue)}
    for key in ("id", "issue_id", "claim_id", "candidate_id"):
        value = str(issue.get(key) or "").strip()
        if value:
            values.add(value)
    return tuple(sorted(values))


def _issue_evidence_fragments(issue: dict[str, Any], report: dict[str, Any]) -> list[str]:
    raw_values: list[Any] = []
    raw_values.extend(_as_list(issue.get("evidence")))
    raw_values.extend(_as_list(issue.get("source_refs")))
    raw_values.extend(
        [
            issue.get("description"),
            issue.get("summary"),
            report.get("change_intent"),
        ]
    )
    fragments: list[str] = []
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        fragments.extend(
            match.group(1).strip() for match in _REPAIR_EVIDENCE_QUOTE_RE.finditer(text)
        )
        if "：" in text:
            fragments.append(text.split("：", 1)[1].strip())
        if ":" in text:
            fragments.append(text.split(":", 1)[1].strip())
        if len(text) <= 160:
            fragments.append(text)
    return _dedupe_texts(fragments)


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _matching_evidence_fragment(fragments: list[str], text: str) -> str:
    for fragment in fragments:
        if _fragment_matches_text(fragment, text):
            return fragment[:120]
    return ""


def _fragment_matches_text(fragment: str, text: str) -> bool:
    normalized_fragment = _normalize_repair_text(fragment)
    normalized_text = _normalize_repair_text(text)
    if len(normalized_fragment) < 2 or not normalized_text:
        return False
    if normalized_fragment == normalized_text:
        return True
    # Two- and three-character Chinese entity names are common. Permit direct
    # containment in the candidate, but do not reverse-match a short candidate
    # against a large generic repair instruction (the source of noisy targets).
    if normalized_fragment in normalized_text:
        return True
    if len(normalized_text) >= 4 and normalized_text in normalized_fragment:
        return True
    if len(normalized_fragment) < 8:
        return False
    match = SequenceMatcher(None, normalized_fragment, normalized_text).find_longest_match(
        0,
        len(normalized_fragment),
        0,
        len(normalized_text),
    )
    required = max(4, min(10, len(normalized_fragment) // 2))
    return match.size >= required


def _fragment_match_priority(fragment: str, text: str) -> int:
    """Rank exact entity leaves ahead of prose and fuzzy evidence matches."""

    normalized_fragment = _normalize_repair_text(fragment)
    normalized_text = _normalize_repair_text(text)
    if normalized_fragment == normalized_text:
        return 0
    if normalized_fragment and normalized_fragment in normalized_text:
        return 1
    if normalized_text and normalized_text in normalized_fragment:
        return 2
    return 3


def _normalize_repair_text(text: str) -> str:
    return _TEXT_NORMALIZE_RE.sub("", str(text or "").lower())


def _issue_prefers_irreversible_markers(issue: dict[str, Any]) -> bool:
    issue_type = str(issue.get("type") or issue.get("issue_type") or "").lower()
    if "irreversible" in issue_type or "timeline" in issue_type:
        return True
    text = " ".join(
        str(issue.get(key) or "") for key in ("description", "summary", "change_intent")
    )
    return "不可逆" in text or "最后一次" in text or "时间线" in text


def _text_has_irreversible_marker(text: str) -> bool:
    return any(marker in text for marker in _IRREVERSIBLE_MARKERS)


def readiness_payload_allows(payload: dict[str, Any]) -> bool:
    """Return True when an init_readiness payload allows chapter generation."""
    return bool(payload.get("allowed", False))


def _assert_path_allowed(
    *,
    artifact: str,
    payload: dict[str, Any],
    path: str,
    scopes: list[RepairScope],
) -> None:
    parts = _split_json_pointer(path)
    if not parts:
        raise InitCoherenceError("不允许替换整个 artifact。")
    if "chapter_number" in parts:
        raise InitCoherenceError("不允许修改 chapter_number。")
    if artifact == BLUEPRINT_ARTIFACT:
        field = parts[0]
        if any(scope.operation in {"chapter_insert", "chapter_delete"} for scope in scopes):
            raise InitCoherenceError("初始化修复 v2 不开放章节增删操作。")
        matching_scopes = [
            scope
            for scope in scopes
            if field in {_normalize_blueprint_scope_field(item) for item in scope.fields}
        ]
        if not matching_scopes:
            raise InitCoherenceError(f"patch path 未落在蓝图字段白名单内：{path}")
        if any(not scope.chapters for scope in matching_scopes):
            return
        if any(
            _blueprint_path_allowed_by_scope(payload=payload, parts=parts, scope=scope)
            for scope in matching_scopes
        ):
            return
        raise InitCoherenceError(f"patch path 未落在蓝图章节范围内：{path}")

    list_key = _LIST_KEY_BY_ARTIFACT.get(artifact)
    if list_key is None:
        raise InitCoherenceError(f"未知初始化 artifact：{artifact}")
    if parts[0] != list_key:
        raise InitCoherenceError(f"patch path 必须落在 /{list_key}/<index>/<field> 下：{path}")
    if len(parts) < 3:
        raise InitCoherenceError("不允许替换整章/整条章节契约。")
    try:
        item_index = _parse_list_index(parts[1])
    except ValueError as exc:
        raise InitCoherenceError(f"章节索引必须是数字：{path}") from exc
    items = payload.get(list_key)
    if not isinstance(items, list) or item_index < 0 or item_index >= len(items):
        raise InitCoherenceError(f"章节索引越界：{path}")
    item = items[item_index]
    if not isinstance(item, dict):
        raise InitCoherenceError(f"章节条目不是对象：{path}")
    chapter_number = _coerce_int(item.get("chapter_number"))
    field = parts[2]
    default_fields = (
        _DEFAULT_OUTLINE_FIELDS if artifact == OUTLINE_ARTIFACT else _DEFAULT_CONTRACT_FIELDS
    )
    for scope in scopes:
        allowed_fields = scope.fields or set(default_fields)
        if chapter_number in scope.chapters and field in allowed_fields:
            return
    raise InitCoherenceError(f"patch path 未落在裁判 repair_scope 内：{path}")


def _blueprint_path_allowed_by_scope(
    *,
    payload: dict[str, Any],
    parts: list[str],
    scope: RepairScope,
) -> bool:
    if not parts:
        return False
    field = parts[0]
    if field not in {_normalize_blueprint_scope_field(item) for item in scope.fields}:
        return False
    if not scope.chapters:
        return True
    if len(parts) == 1:
        return True
    if _blueprint_scope_explicit_path_allows(parts, scope):
        return True
    if field not in payload:
        return False
    field_value = payload[field]
    if len(parts) == 1:
        return not isinstance(field_value, (dict, list))
    structured_contexts = _blueprint_structured_contexts(payload, parts)
    return any(
        _blueprint_value_mentions_scope_chapter(value, scope.chapters)
        for value in reversed(structured_contexts)
    )


def _blueprint_scope_explicit_path_allows(parts: list[str], scope: RepairScope) -> bool:
    for raw_field in scope.fields:
        raw_path = str(raw_field or "").strip().strip("/")
        if "/" not in raw_path:
            continue
        scope_parts = [part for part in raw_path.split("/") if part]
        if len(scope_parts) <= 1 or len(parts) < len(scope_parts):
            continue
        if parts[: len(scope_parts)] == scope_parts:
            return True
    return False


def _blueprint_structured_contexts(payload: dict[str, Any], parts: list[str]) -> list[Any]:
    if not parts or parts[0] not in payload:
        return []
    current: Any = payload[parts[0]]
    contexts: list[Any] = []
    for part in parts[1:]:
        try:
            current, _ = _descend_pointer(current, part)
        except InitCoherenceError:
            return contexts
        if isinstance(current, (dict, list)):
            contexts.append(current)
    return contexts


def _blueprint_value_mentions_scope_chapter(value: Any, chapters: set[int]) -> bool:
    if not chapters:
        return False
    if isinstance(value, dict):
        if _blueprint_dict_range_intersects(value, chapters):
            return True
        for key in _BLUEPRINT_DIRECT_CHAPTER_KEYS:
            if _coerce_int(value.get(key)) in chapters:
                return True
        for key in _BLUEPRINT_CHAPTER_LIST_KEYS:
            if any(_coerce_int(item) in chapters for item in _as_list(value.get(key))):
                return True
        return any(
            _blueprint_value_mentions_scope_chapter(item, chapters) for item in value.values()
        )
    if isinstance(value, list):
        return any(_blueprint_value_mentions_scope_chapter(item, chapters) for item in value)
    return _coerce_int(value) in chapters


def _blueprint_dict_range_intersects(value: dict[str, Any], chapters: set[int]) -> bool:
    start = 0
    end = 0
    for key in _BLUEPRINT_CHAPTER_RANGE_START_KEYS:
        start = _coerce_int(value.get(key))
        if start:
            break
    for key in _BLUEPRINT_CHAPTER_RANGE_END_KEYS:
        end = _coerce_int(value.get(key))
        if end:
            break
    if start and not end:
        end = start
    if end and not start:
        start = end
    return bool(start and end and any(start <= chapter <= end for chapter in chapters))


def _assert_patch_targets_issue_evidence(
    payload: dict[str, Any],
    *,
    path: str,
    replacement: Any,
    issue_ids: list[str],
    issue_evidence_by_id: dict[str, list[str]] | None,
) -> None:
    if not issue_evidence_by_id or not issue_ids:
        return
    evidence_tokens = [
        token
        for issue_id in issue_ids
        for token in issue_evidence_by_id.get(str(issue_id), [])
        if str(token or "").strip()
    ]
    if not evidence_tokens:
        return
    old_value = _read_json_pointer(payload, path)
    if not any(_payload_contains_evidence(old_value, token) for token in evidence_tokens):
        raise InitCoherenceError(f"patch path 未包含 issue evidence，拒绝非定位修复：{path}")
    if isinstance(old_value, list) and isinstance(replacement, list):
        _assert_list_patch_only_changes_evidence_items(
            old_value,
            replacement,
            evidence_tokens=evidence_tokens,
            path=path,
        )


def _assert_list_patch_only_changes_evidence_items(
    old_value: list[Any],
    replacement: list[Any],
    *,
    evidence_tokens: list[str],
    path: str,
) -> None:
    if len(old_value) != len(replacement):
        return
    if not all(isinstance(item, str) for item in old_value):
        return
    if not all(isinstance(item, str) for item in replacement):
        return
    for index, (old_item, new_item) in enumerate(zip(old_value, replacement, strict=True)):
        if old_item == new_item:
            continue
        if any(token in old_item for token in evidence_tokens):
            continue
        raise InitCoherenceError(f"列表 patch 修改了未命中 issue evidence 的元素：{path}/{index}")


def _payload_contains_evidence(value: Any, evidence: str) -> bool:
    token = str(evidence or "").strip()
    if not token:
        return False
    if isinstance(value, str):
        return token in value
    if isinstance(value, list):
        return any(_payload_contains_evidence(item, token) for item in value)
    if isinstance(value, dict):
        return any(
            token in str(key) or _payload_contains_evidence(item, token)
            for key, item in value.items()
        )
    return False


def _read_json_pointer(payload: Any, path: str) -> Any:
    parts = _split_json_pointer(path)
    if not parts:
        raise InitCoherenceError("不允许读取整个 artifact。")
    target = payload
    for part in parts:
        target, _ = _descend_pointer(target, part)
    return target


def _replace_json_pointer(
    payload: Any,
    path: str,
    value: Any,
    *,
    expected_old_value: Any = _MISSING,
    expected_old_hash: str = "",
) -> tuple[str, str]:
    parts = _split_json_pointer(path)
    target = payload
    normalized_parts: list[str] = []
    for part in parts[:-1]:
        target, normalized_part = _descend_pointer(target, part)
        normalized_parts.append(normalized_part)
    last = parts[-1]
    if isinstance(target, dict):
        if last not in target:
            raise InitCoherenceError(f"replace path 不存在：{path}")
        old_value = target[last]
        _assert_patch_precondition(
            old_value,
            expected_old_value=expected_old_value,
            expected_old_hash=expected_old_hash,
            path=path,
        )
        if old_value == value:
            raise InitCoherenceError(f"replace value 未改变原值：{path}")
        old_hash = _stable_value_hash(old_value)
        _assert_replacement_compatible(old_value, value, path)
        _assert_replacement_granularity(
            old_value,
            value,
            path=path,
            parts=parts,
            expected_old_value=expected_old_value,
            expected_old_hash=expected_old_hash,
        )
        target[last] = value
        normalized_parts.append(last)
        return _join_json_pointer(normalized_parts), old_hash
    if isinstance(target, list):
        try:
            index = _parse_list_index(last)
        except ValueError as exc:
            raise InitCoherenceError(f"列表索引必须是数字：{path}") from exc
        if index < 0 or index >= len(target):
            raise InitCoherenceError(f"列表索引越界：{path}")
        old_value = target[index]
        _assert_patch_precondition(
            old_value,
            expected_old_value=expected_old_value,
            expected_old_hash=expected_old_hash,
            path=path,
        )
        if old_value == value:
            raise InitCoherenceError(f"replace value 未改变原值：{path}")
        old_hash = _stable_value_hash(old_value)
        _assert_replacement_compatible(old_value, value, path)
        _assert_replacement_granularity(
            old_value,
            value,
            path=path,
            parts=parts,
            expected_old_value=expected_old_value,
            expected_old_hash=expected_old_hash,
        )
        target[index] = value
        normalized_parts.append(str(index))
        return _join_json_pointer(normalized_parts), old_hash
    raise InitCoherenceError(f"replace path 的父节点不可写：{path}")


def _stable_value_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assert_patch_precondition(
    current: Any,
    *,
    expected_old_value: Any,
    expected_old_hash: str,
    path: str,
) -> None:
    if expected_old_value is not _MISSING and current != expected_old_value:
        raise InitCoherenceError(f"patch 前置旧值不匹配：{path}")
    if expected_old_hash:
        actual_hash = _stable_value_hash(current)
        if actual_hash != expected_old_hash:
            raise InitCoherenceError(f"patch 前置旧值哈希不匹配：{path}")


def _descend_pointer(target: Any, part: str) -> tuple[Any, str]:
    if isinstance(target, dict):
        if part not in target:
            raise InitCoherenceError(f"JSON Pointer 路径不存在：{part}")
        return target[part], part
    if isinstance(target, list):
        try:
            index = _parse_list_index(part)
        except ValueError as exc:
            raise InitCoherenceError(f"列表索引必须是数字：{part}") from exc
        if index < 0 or index >= len(target):
            raise InitCoherenceError(f"列表索引越界：{part}")
        return target[index], str(index)
    raise InitCoherenceError(f"JSON Pointer 无法穿过 {type(target).__name__}")


def _split_json_pointer(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]


def _assert_replacement_compatible(current: Any, value: Any, path: str) -> None:
    if isinstance(current, dict) and not isinstance(value, dict):
        raise InitCoherenceError(f"replace value 类型不兼容：{path}")
    if isinstance(current, list) and not isinstance(value, list):
        raise InitCoherenceError(f"replace value 类型不兼容：{path}")


def _assert_replacement_granularity(
    current: Any,
    value: Any,
    *,
    path: str,
    parts: list[str],
    expected_old_value: Any,
    expected_old_hash: str,
) -> None:
    if len(parts) != 1:
        return
    if isinstance(current, (dict, list)):
        raise InitCoherenceError(f"不允许整体替换顶层复合字段：{path}；请定位到具体子路径。")
    if not isinstance(current, str) or not isinstance(value, str):
        return
    current_text = current.strip()
    new_text = value.strip()
    if len(current_text) < _TOP_LEVEL_LONG_TEXT_MIN_CHARS:
        return
    if expected_old_value is _MISSING and not expected_old_hash:
        raise InitCoherenceError(f"替换顶层长文本字段必须携带旧值或旧哈希前置条件：{path}")
    minimum_length = int(len(current_text) * _TOP_LEVEL_TEXT_REPLACE_MIN_RATIO)
    if len(new_text) < minimum_length:
        raise InitCoherenceError(
            f"拒绝用短文本覆盖顶层长文本字段：{path}，"
            f"新值长度 {len(new_text)} 小于原值的 {_TOP_LEVEL_TEXT_REPLACE_MIN_RATIO:.0%}"
        )


_WINDOWED_INDEX_RE = re.compile(r"^(?P<start>\d+)\s*:\s*(?P<end>\d+)\[(?P<index>\d+)\]$")
_BRACKET_INDEX_RE = re.compile(r"^\[(?P<index>\d+)\]$")


def _parse_list_index(part: str) -> int:
    text = str(part).strip()
    if text.isdigit():
        return int(text)
    bracket_match = _BRACKET_INDEX_RE.match(text)
    if bracket_match:
        return int(bracket_match.group("index"))
    window_match = _WINDOWED_INDEX_RE.match(text)
    if window_match:
        return int(window_match.group("start")) + int(window_match.group("index"))
    raise ValueError(text)


def _normalize_blueprint_scope_field(field: str) -> str:
    return str(field or "").strip().strip("/").split("/", 1)[0]


def _join_json_pointer(parts: list[str]) -> str:
    escaped = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_int_set(value: Any) -> set[int]:
    result: set[int] = set()
    for item in _as_list(value):
        number = _coerce_int(item)
        if number > 0:
            result.add(number)
    return result


def _coerce_scope_chapters(raw: dict[str, Any]) -> set[int]:
    chapters = _coerce_int_set(raw.get("chapters") or raw.get("chapter_numbers"))
    if chapters:
        return chapters
    return _coerce_chapter_range_set(raw.get("chapter_range"))


def _coerce_chapter_range_set(value: Any) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, dict):
        start = _coerce_int(value.get("start"))
        end = _coerce_int(value.get("end"))
    else:
        numbers = _coerce_int_set(value)
        if not numbers:
            return set()
        start = min(numbers)
        end = max(numbers)
    if not start and not end:
        return set()
    if not start:
        start = end
    if not end:
        end = start
    start, end = min(start, end), max(start, end)
    if start <= 0 or end <= 0:
        return set()
    if end - start + 1 > _REPAIR_SCOPE_RANGE_EXPANSION_LIMIT:
        return {start, end}
    return set(range(start, end + 1))


def _coerce_str_set(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _as_list(value):
        text = str(item or "").strip()
        if text:
            result.add(text)
    return result
