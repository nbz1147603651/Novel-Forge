"""Read-only semantic consistency projection over the existing claim ledger.

This module does not persist a second status file.  Every field is derived
from current planning sources, the existing claim ledger and its adjudication
reports; transient queued/running/failed state is overlaid by ``JobService``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.core.authoring import SemanticConsistencyView
from novel_forge.core.semantic_consistency import semantic_payload_hash
from novel_forge.persistence.authoring_store import story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout

CLAIM_LEDGER_JSON = "init_coherence_claim_ledger.json"
SEMANTIC_STAGE_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "blueprint_coherence",
        "blueprint",
        ("spec", "story_bible", "character_bible", "blueprint"),
    ),
    (
        "outline_inheritance",
        "outline",
        ("spec", "story_bible", "character_bible", "blueprint", "outline"),
    ),
    (
        "contract_coherence",
        "chapter_contracts",
        (
            "spec",
            "story_bible",
            "character_bible",
            "blueprint",
            "outline",
            "narrative_contract",
            "chapter_contracts",
        ),
    ),
)


def load_semantic_source_inputs(
    storage: FileSystemStorage,
    layout: ProjectLayout,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load server-selected semantic sources; callers never supply paths."""

    sources: dict[str, dict[str, Any]] = {}
    for key, path in (
        ("spec", layout.spec_path),
        ("story_bible", layout.bible_path),
        ("character_bible", layout.characters_path),
        ("blueprint", layout.blueprint_path),
        ("outline", layout.outline_path),
        ("chapter_contracts", layout.plans_dir / "chapter_contracts.json"),
    ):
        if storage.exists(path):
            payload = storage.load_json(path)
            if isinstance(payload, dict):
                sources[key] = payload
    if storage.exists(layout.narrative_contract_path):
        payload = storage.load_json(layout.narrative_contract_path)
        if isinstance(payload, dict):
            llm_contract = payload.get("llm_contract")
            sources["narrative_contract"] = (
                llm_contract if isinstance(llm_contract, dict) else payload
            )
    profile_path = layout.reports_dir / "init_coherence_profile.json"
    profile: dict[str, Any] = {}
    if storage.exists(profile_path):
        payload = storage.load_json(profile_path)
        if isinstance(payload, dict):
            profile = payload
    return sources, profile


def semantic_stage_inputs(
    sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        stage: {key: sources[key] for key in keys if key in sources}
        for stage, _repair_artifact, keys in SEMANTIC_STAGE_SPECS
    }


def semantic_affected_chapters(sources: dict[str, dict[str, Any]]) -> list[int]:
    chapters: set[int] = set()
    outline = sources.get("outline", {})
    for item in outline.get("chapters", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            chapters.add(number)
    contracts = sources.get("chapter_contracts", {})
    for item in contracts.get("chapter_contracts", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            chapters.add(number)
    return sorted(chapters)


def semantic_source_fingerprint(
    sources: dict[str, dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    return semantic_payload_hash({"sources": sources, "profile": profile})


def record_semantic_compile_failure(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    source_fingerprint: str,
    runtime_fingerprint: str,
    reason: str,
) -> None:
    """Record the latest failed attempt in the existing claim ledger."""

    ledger_path = layout.memory_dir / CLAIM_LEDGER_JSON
    ledger: dict[str, Any] = {}
    if storage.exists(ledger_path):
        payload = storage.load_json(ledger_path)
        if isinstance(payload, dict):
            ledger = payload
    ledger.setdefault("schema_version", 1)
    ledger.setdefault("claims_by_id", {})
    ledger.setdefault("active_claim_ids", [])
    ledger.setdefault("stages", {})
    ledger.setdefault("history", [])
    ledger["last_compile_failure"] = {
        "source_fingerprint": source_fingerprint,
        "runtime_fingerprint": runtime_fingerprint,
        "reason": reason[:1000],
    }
    storage.save_json(ledger_path, ledger)


def clear_semantic_compile_failure(
    storage: FileSystemStorage,
    layout: ProjectLayout,
) -> None:
    ledger_path = layout.memory_dir / CLAIM_LEDGER_JSON
    if not storage.exists(ledger_path):
        return
    payload = storage.load_json(ledger_path)
    if not isinstance(payload, dict) or "last_compile_failure" not in payload:
        return
    payload.pop("last_compile_failure", None)
    storage.save_json(ledger_path, payload)


def semantic_consistency_view(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    expected_runtime_fingerprint: str = "",
) -> SemanticConsistencyView:
    """Project current compiler readiness without invoking a model."""

    source_version = story_input_version(layout.root)
    try:
        sources, profile = load_semantic_source_inputs(storage, layout)
    except Exception as exc:
        return SemanticConsistencyView(
            status="failed",
            source_version=source_version,
            reason=f"语义来源读取失败：{exc}",
        )
    affected = semantic_affected_chapters(sources)
    source_fingerprint = semantic_source_fingerprint(sources, profile)
    required_sources = {
        "spec",
        "story_bible",
        "character_bible",
        "blueprint",
        "outline",
        "narrative_contract",
        "chapter_contracts",
    }
    missing_sources = sorted(required_sources - set(sources))
    if not profile or missing_sources:
        missing = [*([] if profile else ["coherence_profile"]), *missing_sources]
        return SemanticConsistencyView(
            status="uncompiled",
            source_version=source_version,
            source_fingerprint=source_fingerprint,
            affected_chapters=affected,
            reason="缺少语义编译来源：" + "、".join(missing),
        )

    ledger_path = layout.memory_dir / CLAIM_LEDGER_JSON
    if not storage.exists(ledger_path):
        return SemanticConsistencyView(
            status="uncompiled",
            source_version=source_version,
            source_fingerprint=source_fingerprint,
            affected_chapters=affected,
            reason="尚未生成 claim ledger",
        )
    try:
        ledger = storage.load_json(ledger_path)
        if not isinstance(ledger, dict):
            raise ValueError("claim ledger 不是对象")
    except Exception as exc:
        return SemanticConsistencyView(
            status="failed",
            source_version=source_version,
            source_fingerprint=source_fingerprint,
            affected_chapters=affected,
            reason=f"claim ledger 无法读取：{exc}",
        )

    ledger_hash = semantic_payload_hash(ledger)
    last_failure = ledger.get("last_compile_failure")
    if (
        isinstance(last_failure, dict)
        and str(last_failure.get("source_fingerprint") or "") == source_fingerprint
        and (
            not expected_runtime_fingerprint
            or str(last_failure.get("runtime_fingerprint") or "")
            == expected_runtime_fingerprint
        )
    ):
        return SemanticConsistencyView(
            status="failed",
            source_version=source_version,
            source_fingerprint=source_fingerprint,
            ledger_hash=ledger_hash,
            affected_chapters=affected,
            reason=str(last_failure.get("reason") or "语义编译失败"),
        )
    stage_inputs = semantic_stage_inputs(sources)
    stages = ledger.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    statuses: list[str] = []
    issue_ids: list[str] = []
    report_hashes: list[str] = []
    stale_reasons: list[str] = []
    missing_stages: list[str] = []
    for stage, _repair_artifact, keys in SEMANTIC_STAGE_SPECS:
        record = stages.get(stage)
        if not isinstance(record, dict):
            missing_stages.append(stage)
            continue
        expected_hashes = {
            key: semantic_payload_hash(stage_inputs[stage][key]) for key in keys
        }
        recorded_hashes = record.get("artifact_hashes")
        if not isinstance(recorded_hashes, dict) or any(
            str(recorded_hashes.get(key) or "") != value
            for key, value in expected_hashes.items()
        ):
            stale_reasons.append(f"{stage}:source")
        if expected_runtime_fingerprint and str(
            record.get("compiler_runtime_fingerprint") or ""
        ) != expected_runtime_fingerprint:
            stale_reasons.append(f"{stage}:compiler")
        claim_coverage = record.get("claim_coverage")
        pair_coverage = record.get("pair_coverage")
        if not isinstance(claim_coverage, dict) or not bool(claim_coverage.get("complete")):
            statuses.append("review_required")
        elif not isinstance(pair_coverage, dict) or not bool(pair_coverage.get("complete")):
            statuses.append("review_required")
        else:
            statuses.append(str(record.get("semantic_status") or "review_required"))

        report_path = layout.reports_dir / f"{stage}.json"
        if not storage.exists(report_path):
            missing_stages.append(f"{stage}:report")
            continue
        try:
            report = storage.load_json(report_path)
        except Exception:
            missing_stages.append(f"{stage}:report")
            continue
        if not isinstance(report, dict):
            missing_stages.append(f"{stage}:report")
            continue
        report_hashes.append(semantic_payload_hash(report))
        for issue in report.get("issues", []) or []:
            if not isinstance(issue, dict):
                continue
            issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
            if issue_id and issue_id not in issue_ids:
                issue_ids.append(issue_id)

    report_id = semantic_payload_hash(report_hashes) if report_hashes else ""
    if stale_reasons:
        status = "stale"
        reason = "来源或编译契约已变化：" + "、".join(stale_reasons)
    elif missing_stages:
        status = "uncompiled"
        reason = "语义编译阶段不完整：" + "、".join(missing_stages)
    elif "conflict" in statuses:
        status = "conflict"
        reason = "模型裁决发现明确的上游来源冲突"
    elif "review_required" in statuses:
        status = "review_required"
        reason = "证据不足或覆盖不完整，需要作者确认"
    elif statuses and all(item == "clean" for item in statuses):
        status = "clean"
        reason = "来源证据已完整编译并通过语义裁决"
    else:
        status = "failed"
        reason = "语义编译状态无法确认"
    return SemanticConsistencyView(
        status=status,
        source_version=source_version,
        source_fingerprint=source_fingerprint,
        ledger_hash=ledger_hash,
        report_id=report_id,
        issue_ids=issue_ids,
        issue_count=len(issue_ids),
        affected_chapters=affected,
        reason=reason,
    )


def semantic_consistency_view_for_root(
    root: Path,
    *,
    expected_runtime_fingerprint: str = "",
) -> SemanticConsistencyView:
    return semantic_consistency_view(
        FileSystemStorage(root.parent),
        ProjectLayout(root),
        expected_runtime_fingerprint=expected_runtime_fingerprint,
    )


__all__ = [
    "CLAIM_LEDGER_JSON",
    "SEMANTIC_STAGE_SPECS",
    "load_semantic_source_inputs",
    "clear_semantic_compile_failure",
    "record_semantic_compile_failure",
    "semantic_affected_chapters",
    "semantic_consistency_view",
    "semantic_consistency_view_for_root",
    "semantic_source_fingerprint",
    "semantic_stage_inputs",
]
