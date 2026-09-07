"""Engine-owned state and persistence for failed long-init manual repair.

This module deliberately has no Qt dependency.  Both PySide and HTTP clients
can use the same artifact selection, optimistic concurrency, schema validation,
and staleness propagation rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from novel_forge.core.schemas.outline import NarrativeBlueprint, StoryOutline
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)

ManualInitArtifact = Literal["blueprint", "outline", "chapter_contracts"]

_ARTIFACT_LABELS: dict[ManualInitArtifact, str] = {
    "blueprint": "叙事蓝图",
    "outline": "章节大纲",
    "chapter_contracts": "章节契约",
}
_STAGE_ARTIFACT_HINTS: dict[str, ManualInitArtifact] = {
    "blueprint_coherence": "blueprint",
    "outline_inheritance": "outline",
    "contract_coherence": "chapter_contracts",
    "claim_contract_coverage": "chapter_contracts",
    "source_artifacts": "chapter_contracts",
}


class ManualInitRepairError(ValueError):
    """Base error for a manual-init repair command."""


class ManualInitRepairConflictError(ManualInitRepairError):
    """The client attempted to overwrite a newer artifact revision."""


@dataclass(frozen=True)
class ManualInitRepairLocation:
    pointer: str
    label: str
    confidence: str = "weak"
    excerpt: str = ""


@dataclass(frozen=True)
class ManualInitRepairIssue:
    title: str
    summary: str
    locations: tuple[ManualInitRepairLocation, ...] = ()


@dataclass(frozen=True)
class ManualInitRepairSnapshot:
    project_id: str
    available: bool
    artifact: str = ""
    artifact_label: str = ""
    artifact_path: str = ""
    payload: dict[str, Any] | None = None
    revision: str = ""
    summary: str = ""
    issues: tuple[ManualInitRepairIssue, ...] = ()


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_revision(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _normalize_artifact(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "narrative_blueprint": "blueprint",
        "plans/narrative_blueprint.json": "blueprint",
        "outline.json": "outline",
        "story_outline": "outline",
        "contracts": "chapter_contracts",
        "plans/chapter_contracts.json": "chapter_contracts",
    }
    return aliases.get(raw, raw)


def _is_supported_artifact(value: str) -> bool:
    return value in _ARTIFACT_LABELS


def _artifact_path(layout: ProjectLayout, artifact: str) -> Path:
    if artifact == "blueprint":
        return layout.blueprint_path
    if artifact == "chapter_contracts":
        return layout.plans_dir / "chapter_contracts.json"
    return layout.outline_path


def _artifact_kind(artifact: str) -> UpstreamArtifactKind:
    if artifact == "blueprint":
        return UpstreamArtifactKind.NARRATIVE_BLUEPRINT
    if artifact == "chapter_contracts":
        return UpstreamArtifactKind.CHAPTER_CONTRACTS
    return UpstreamArtifactKind.OUTLINE


def _issue_artifacts(issue: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for scope in _as_dicts(issue.get("repair_scope")):
        artifact = _normalize_artifact(scope.get("artifact"))
        if _is_supported_artifact(artifact) and artifact not in artifacts:
            artifacts.append(artifact)
    if not artifacts:
        artifact = _STAGE_ARTIFACT_HINTS.get(str(issue.get("stage") or "").strip())
        if artifact is not None:
            artifacts.append(artifact)
    return artifacts


def _candidate_artifacts(readiness: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for action in _as_dicts(readiness.get("recovery_actions")):
        if str(action.get("kind") or "") != "manual_repair":
            continue
        for value in action.get("artifacts", []):
            artifact = _normalize_artifact(value)
            if _is_supported_artifact(artifact) and artifact not in artifacts:
                artifacts.append(artifact)
    for issue in _as_dicts(readiness.get("remaining_issues")):
        for artifact in _issue_artifacts(issue):
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def _selected_artifact(layout: ProjectLayout, readiness: dict[str, Any], requested: str | None) -> str:
    requested_artifact = _normalize_artifact(requested)
    if requested_artifact:
        if not _is_supported_artifact(requested_artifact):
            raise ManualInitRepairError("不支持的初始化修复产物。")
        if not _artifact_path(layout, requested_artifact).is_file():
            raise ManualInitRepairError("待修复产物不存在。")
        return requested_artifact
    for artifact in _candidate_artifacts(readiness):
        if _artifact_path(layout, artifact).is_file():
            return artifact
    return "outline" if layout.outline_path.is_file() else ""


def _compact(value: object, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _issue_summary(issue: dict[str, Any], index: int) -> str:
    lines = [f"[{index}] {str(issue.get('id') or issue.get('issue_id') or f'issue_{index}')}"]
    for key, label in (
        ("description", "矛盾点"),
        ("summary", "摘要"),
        ("evidence", "证据"),
        ("suggestion", "修复建议"),
        ("repair_hint", "修复提示"),
    ):
        if issue.get(key):
            lines.append(f"{label}：{_compact(issue[key])}")
    return "\n".join(lines)


def _scope_locations(issue: dict[str, Any], artifact: str) -> tuple[ManualInitRepairLocation, ...]:
    locations: list[ManualInitRepairLocation] = []
    for scope in _as_dicts(issue.get("repair_scope")):
        scoped_artifact = _normalize_artifact(scope.get("artifact"))
        if scoped_artifact and scoped_artifact != artifact:
            continue
        raw_paths = (
            scope.get("json_paths")
            or scope.get("candidate_json_paths")
            or scope.get("paths")
            or [scope.get("json_pointer") or scope.get("json_path")]
        )
        values = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        chapters = [str(item) for item in scope.get("chapters", []) if str(item)]
        fields = [str(item) for item in scope.get("fields", []) if str(item)]
        label_parts = []
        if chapters:
            label_parts.append(f"第 {', '.join(chapters)} 章")
        if fields:
            label_parts.append("字段 " + ", ".join(fields))
        label = " · ".join(label_parts) or "建议修改范围"
        location_count = len(locations)
        for raw in values:
            pointer = str(raw or "").strip()
            if pointer and not pointer.startswith("/"):
                pointer = "/" + "/".join(part for part in pointer.split(".") if part)
            candidate = ManualInitRepairLocation(
                pointer=pointer,
                label=label,
                confidence="exact" if pointer else "weak",
            )
            if candidate not in locations:
                locations.append(candidate)
        if len(locations) == location_count:
            locations.append(ManualInitRepairLocation(pointer="", label=label))
    return tuple(locations)


def _issue_cards(
    readiness: dict[str, Any], report: dict[str, Any], artifact: str
) -> tuple[ManualInitRepairIssue, ...]:
    source = [
        issue
        for issue in _as_dicts(readiness.get("remaining_issues"))
        if not _issue_artifacts(issue) or artifact in _issue_artifacts(issue)
    ]
    if not source:
        source = _as_dicts(report.get("issues"))
    return tuple(
        ManualInitRepairIssue(
            title=(
                f"[{index}] "
                f"{str(issue.get('id') or issue.get('issue_id') or f'issue_{index}') }"
            ),
            summary=_issue_summary(issue, index),
            locations=_scope_locations(issue, artifact),
        )
        for index, issue in enumerate(source, 1)
    )


def _summary(readiness: dict[str, Any], report: dict[str, Any], artifact: str, path: Path) -> str:
    sections: list[str] = []
    conclusion = str(readiness.get("summary") or report.get("summary") or "").strip()
    if conclusion:
        sections.append(f"准入结论：{conclusion}")
    sections.append(f"当前修复产物：{_ARTIFACT_LABELS[artifact]}（{path.as_posix()}）")
    verdict = str(report.get("verdict") or "").strip()
    if verdict:
        sections.append(f"继承裁判：{verdict}")
    return "\n\n".join(sections)


def load_manual_init_repair(
    storage: FileSystemStorage,
    project_id: str,
    *,
    artifact: str | None = None,
) -> ManualInitRepairSnapshot:
    """Return a stable editor view for a blocked initialization artifact."""

    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise ManualInitRepairError("project_id is required")
    try:
        layout = ProjectLayout(storage.project_path(normalized_project_id))
    except ValueError as exc:
        raise ManualInitRepairError("无效的项目标识。") from exc
    readiness = _load_json_object(layout.root / "reports" / "init_readiness.json")
    report = _load_json_object(layout.root / "reports" / "outline_inheritance.json")
    if not readiness:
        return ManualInitRepairSnapshot(project_id=normalized_project_id, available=False)
    selected = _selected_artifact(layout, readiness, artifact)
    if not selected:
        return ManualInitRepairSnapshot(project_id=normalized_project_id, available=False)
    path = _artifact_path(layout, selected)
    payload = _load_json_object(path)
    relative_path = path.relative_to(layout.root).as_posix()
    return ManualInitRepairSnapshot(
        project_id=normalized_project_id,
        available=True,
        artifact=selected,
        artifact_label=_ARTIFACT_LABELS[selected],
        artifact_path=relative_path,
        payload=payload,
        revision=_file_revision(path),
        summary=_summary(readiness, report, selected, Path(relative_path)),
        issues=_issue_cards(readiness, report, selected),
    )


def save_manual_init_repair(
    storage: FileSystemStorage,
    project_id: str,
    *,
    artifact: str,
    payload: dict[str, Any],
    expected_revision: str,
) -> ManualInitRepairSnapshot:
    """Validate and atomically persist a user repair with conflict protection."""

    from novel_forge.persistence.foundation_guard import require_versioned_foundation_write

    require_versioned_foundation_write(storage.existing_project_dir(project_id))
    current = load_manual_init_repair(storage, project_id, artifact=artifact)
    if not current.available or current.payload is None:
        raise ManualInitRepairError("当前项目没有可人工修复的初始化产物。")
    if not expected_revision:
        raise ManualInitRepairError("缺少产物版本；请刷新后再保存。")
    if expected_revision != current.revision:
        raise ManualInitRepairConflictError("产物已被其他窗口更新；请刷新后合并修改。")
    if current.artifact == "blueprint":
        NarrativeBlueprint.model_validate(payload)
    elif current.artifact == "outline":
        StoryOutline.model_validate(payload)

    layout = ProjectLayout(storage.project_path(current.project_id))
    path = _artifact_path(layout, current.artifact)
    atomic_write_json(path, payload)
    try:
        record_upstream_artifact_revision(
            storage,
            layout,
            artifact_kind=_artifact_kind(current.artifact),
            previous_hash=current.revision,
            scope=RevisionScope.FORWARD_ONLY,
            reason="engine_init_manual_repair",
        )
    except Exception:
        # The artifact write is atomic and valid.  A later repair retry will
        # re-evaluate it even if advisory lineage recording was unavailable.
        pass
    return load_manual_init_repair(storage, current.project_id, artifact=current.artifact)


__all__ = [
    "ManualInitRepairConflictError",
    "ManualInitRepairError",
    "ManualInitRepairIssue",
    "ManualInitRepairLocation",
    "ManualInitRepairSnapshot",
    "load_manual_init_repair",
    "save_manual_init_repair",
]
