"""Checkpoint store wrappers for whole-book consistency audit workflows."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_ops.execution_book_audit_helpers import (
    _file_digest,
    _stable_json_digest,
)

AUDIT_CHECKPOINT_FILENAME = "book_consistency_audit_checkpoint.json"
VERIFY_CHECKPOINT_FILENAME = "book_consistency_verify_checkpoint.json"
REPAIR_CHECKPOINT_FILENAME = "book_consistency_repair_checkpoint.json"
_SNAPSHOT_DIR_NAMES = ("chapters", "drafts", "reports", "states")


def book_audit_checkpoint_path(layout: ProjectLayout) -> Path:
    """Path for resumable full-text audit checkpoints."""
    return layout.states_dir / AUDIT_CHECKPOINT_FILENAME


class BookAuditCheckpointStore:
    """Access wrapper for existing book-audit checkpoint artifacts.

    This class intentionally preserves the existing file names and payload
    shapes. It centralizes workspace-level access without replacing the
    pipeline step's own checkpoint signature logic.
    """

    def __init__(self, layout: ProjectLayout) -> None:
        self.layout = layout

    @property
    def audit_path(self) -> Path:
        return self.layout.states_dir / AUDIT_CHECKPOINT_FILENAME

    @property
    def verify_path(self) -> Path:
        return self.layout.states_dir / VERIFY_CHECKPOINT_FILENAME

    @property
    def repair_path(self) -> Path:
        return self.layout.states_dir / REPAIR_CHECKPOINT_FILENAME

    def read_audit_checkpoint(self) -> dict[str, Any]:
        return _read_audit_checkpoint_payload(self.audit_path)

    def write_audit_checkpoint(self, payload: dict[str, Any]) -> None:
        _write_audit_checkpoint_payload(self.audit_path, payload)

    def update_audit_checkpoint(self, updates: dict[str, Any]) -> None:
        _update_audit_checkpoint_payload(self.audit_path, updates)

    def validate_audit_checkpoint(self) -> dict[str, Any]:
        return validate_checkpoint(self.audit_path)

    def invalidate_audit_checkpoint(self) -> None:
        invalidate_checkpoint(self.audit_path)

    def summarize_audit_checkpoint(self) -> str:
        return summarize_book_audit_checkpoint(self.audit_path)

    def create_snapshot(self) -> dict[str, Any]:
        return create_book_audit_snapshot(self.layout)

    def restore_snapshot(self, manifest: dict[str, Any]) -> None:
        restore_book_audit_snapshot(self.layout, manifest)

    def verify_checkpoint_path(self) -> Path:
        return self.verify_path

    def repair_checkpoint_path(self) -> Path:
        return self.repair_path

    @staticmethod
    def write_checkpoint_payload(path: Path | None, payload: dict[str, Any]) -> None:
        """Persist an existing checkpoint payload without changing its schema."""
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)


def _snapshot_root(layout: ProjectLayout) -> Path:
    return layout.logs_dir / "book_consistency_recovery"


def create_book_audit_snapshot(layout: ProjectLayout) -> dict[str, Any]:
    """Copy mutation-prone project directories before a whole-book repair run."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    root = _snapshot_root(layout) / stamp
    root.mkdir(parents=True, exist_ok=True)

    captured: list[str] = []
    missing: list[str] = []
    for name in _SNAPSHOT_DIR_NAMES:
        source = layout.root / name
        target = root / name
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)
            captured.append(name)
        else:
            missing.append(name)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_root": str(root),
        "captured": captured,
        "missing": missing,
    }
    atomic_write_json(root / "manifest.json", manifest)
    return manifest


def restore_book_audit_snapshot(layout: ProjectLayout, manifest: dict[str, Any]) -> None:
    """Restore project directories captured by ``create_book_audit_snapshot``."""
    root = Path(str(manifest.get("snapshot_root", "") or ""))
    if not root.exists():
        raise FileNotFoundError(f"Missing book audit recovery snapshot: {root}")

    captured = [str(item) for item in manifest.get("captured", []) if str(item)]
    missing = {str(item) for item in manifest.get("missing", []) if str(item)}
    for name in _SNAPSHOT_DIR_NAMES:
        destination = layout.root / name
        source = root / name
        if name in captured and source.exists():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
        elif name in missing and destination.exists():
            shutil.rmtree(destination)


def summarize_book_audit_checkpoint(path: Path) -> str:
    """Return a compact status line for an audit checkpoint file."""
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "检测到分批审计检查点，但内容无法读取。建议从头重新审计。"
    if not isinstance(payload, dict):
        return "检测到分批审计检查点，但格式异常。建议从头重新审计。"

    status = str(payload.get("status", "running") or "running")
    total = int(payload.get("chunks_total", 0) or 0)
    completed = payload.get("completed_chunks", [])
    completed_count = len(completed) if isinstance(completed, list) else 0
    if isinstance(payload.get("final_result"), dict) and status == "completed":
        issues = payload.get("final_result", {}).get("issues", [])
        issue_count = len(issues) if isinstance(issues, list) else 0
        return f"检测到已完成的审计结果检查点：可直接复用，含 {issue_count} 条问题。"
    if isinstance(payload.get("two_phase_summary_result"), dict) and status == "summary_completed":
        return "检测到两阶段审计摘要检查点：可复用摘要扫描结果，继续全文批次。"
    failed_chunk = int(payload.get("failed_chunk", 0) or 0)
    failed_chapters = payload.get("failed_chapters", [])
    if isinstance(failed_chapters, list) and failed_chapters:
        failed_text = "，失败批次章节：" + "、".join(str(ch) for ch in failed_chapters[:8])
    else:
        failed_text = ""
    if status == "completed":
        return f"检测到已完成的分批审计检查点：{completed_count}/{total} 批。"
    if failed_chunk > 0:
        return f"检测到未完成的分批审计检查点：已完成 {completed_count}/{total} 批，停在第 {failed_chunk} 批{failed_text}。"
    return f"检测到未完成的分批审计检查点：已完成 {completed_count}/{total} 批。"


def validate_checkpoint(path: Path) -> dict[str, Any]:
    """Validate a book audit checkpoint file."""
    result: dict[str, Any] = {
        "valid": False,
        "error": None,
        "status": "unknown",
        "completed": 0,
        "total": 0,
    }

    if not path.exists():
        result["error"] = "Checkpoint file not found"
        return result

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"Failed to read checkpoint: {exc}"
        return result

    if not isinstance(payload, dict):
        result["error"] = "Checkpoint is not a JSON object"
        return result

    required_fields = ("schema_version", "signature", "status", "chunks_total", "completed_chunks")
    missing = [field for field in required_fields if field not in payload]
    if missing:
        result["error"] = f"Missing required fields: {', '.join(missing)}"
        return result

    status = str(payload.get("status", "") or "")
    total = int(payload.get("chunks_total", 0) or 0)
    completed_chunks = payload.get("completed_chunks", [])
    if not isinstance(completed_chunks, list):
        result["error"] = "completed_chunks is not a list"
        return result

    completed_count = len(completed_chunks)
    result["status"] = status
    result["completed"] = completed_count
    result["total"] = total

    checksum_stored = payload.get("checksum")
    if checksum_stored is None:
        result["valid"] = True
        return result

    chunks_data = json.dumps(completed_chunks, ensure_ascii=False, sort_keys=True, default=str)
    checksum_computed = hashlib.sha256(chunks_data.encode("utf-8")).hexdigest()
    if checksum_computed != str(checksum_stored):
        result["error"] = "Checksum mismatch: completed_chunks data may be corrupted"
        return result

    result["valid"] = True
    return result


def invalidate_checkpoint(path: Path) -> None:
    """Rename a checkpoint file with .invalidated suffix to mark it as unusable."""
    if not path.exists():
        return
    invalidated_path = path.with_suffix(path.suffix + ".invalidated")
    path.rename(invalidated_path)

def _book_audit_resume_signature(
    *,
    layout: ProjectLayout,
    project_id: str,
    completed_chapters: list[int],
    chapter_summaries: list[dict[str, Any]],
    chapter_issue_pool: list[dict[str, Any]],
    analysis_mode: str,
    prompt_hint: str,
    location_strictness: str,
    max_tokens: int,
    temperature: float,
    audit_max_chapters_per_batch: int,
    audit_max_issues_per_chunk: int,
    audit_issue_pool_max_items: int,
    two_phase_enabled: bool,
    two_phase_threshold: float,
    two_phase_max_target_chapters: int,
    parallel_chunks: bool,
    parallel_dimensions: bool,
    memory_enhancement_context: str,
    world_rules: list[str],
    world_setting: str,
    story_theme: str,
    conflict_hint: str,
    world_hint: str,
    character_profiles_compact: list[dict[str, Any]],
    arc_summary: str,
) -> str:
    """Signature for reusing entry-level audit checkpoints safely."""
    chapter_sources: list[dict[str, Any]] = []
    for chapter_number in completed_chapters:
        chapter_path = layout.chapter_path(chapter_number)
        review_draft_path = layout.chapter_review_draft_path(chapter_number)
        source_path = chapter_path if chapter_path.exists() else review_draft_path
        chapter_sources.append(
            {
                "chapter_number": chapter_number,
                "path": source_path.name,
                "sha256": _file_digest(source_path),
            }
        )

    payload = {
        "schema_version": 1,
        "project_id": project_id,
        "completed_chapters": completed_chapters,
        "chapter_summaries": chapter_summaries,
        "chapter_issue_pool": chapter_issue_pool,
        "analysis_mode": analysis_mode,
        "prompt_hint": prompt_hint,
        "location_strictness": location_strictness,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "audit_max_chapters_per_batch": audit_max_chapters_per_batch,
        "audit_max_issues_per_chunk": audit_max_issues_per_chunk,
        "audit_issue_pool_max_items": audit_issue_pool_max_items,
        "two_phase_enabled": two_phase_enabled,
        "two_phase_threshold": two_phase_threshold,
        "two_phase_max_target_chapters": two_phase_max_target_chapters,
        "parallel_chunks": parallel_chunks,
        "parallel_dimensions": parallel_dimensions,
        "memory_enhancement_context": memory_enhancement_context,
        "world_rules": world_rules,
        "world_setting": world_setting,
        "story_theme": story_theme,
        "conflict_hint": conflict_hint,
        "world_hint": world_hint,
        "character_profiles_compact": character_profiles_compact,
        "arc_summary": arc_summary,
        "chapter_sources": chapter_sources,
        "canon_sha256": _file_digest(layout.root / "canon" / "canon_current.json"),
        "outline_sha256": _file_digest(layout.outline_path),
        "characters_sha256": _file_digest(layout.characters_path),
        "story_bible_sha256": _file_digest(layout.root / "story_bible.json"),
        "spec_sha256": _file_digest(layout.root / "spec.json"),
    }
    return _stable_json_digest(payload)


def _read_audit_checkpoint_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_audit_checkpoint_payload(path: Path, payload: dict[str, Any]) -> None:
    BookAuditCheckpointStore.write_checkpoint_payload(path, payload)


def _update_audit_checkpoint_payload(path: Path, updates: dict[str, Any]) -> None:
    payload = _read_audit_checkpoint_payload(path)
    if not payload:
        payload = {
            "schema_version": 1,
            "signature": "",
            "status": "running",
            "chunks_total": 0,
            "completed_chunks": [],
        }
    payload.update(updates)
    _write_audit_checkpoint_payload(path, payload)


def _book_consistency_result_from_payload(payload: dict[str, Any]) -> Any:
    from novel_forge.pipeline.steps.book_consistency_step import BookConsistencyResult
    from novel_forge.workspace.audit_quality_metrics import AuditQualityMetrics

    metrics_raw = payload.get("quality_metrics")
    quality_metrics = None
    if isinstance(metrics_raw, dict):
        quality_metrics = AuditQualityMetrics(
            coverage_ratio=float(metrics_raw.get("coverage_ratio", 0.0) or 0.0),
            estimated_miss_rate=float(metrics_raw.get("estimated_miss_rate", 0.0) or 0.0),
            dimension_scores=dict(metrics_raw.get("dimension_scores") or {}),
            audit_depth=str(metrics_raw.get("audit_depth", "quick") or "quick"),
            total_issues_found=int(metrics_raw.get("total_issues_found", 0) or 0),
            critical_issues_found=int(metrics_raw.get("critical_issues_found", 0) or 0),
        )

    result = BookConsistencyResult(
        issues=list(payload.get("issues") or []),
        summary=str(payload.get("summary", "") or ""),
        consistency_score=float(payload.get("consistency_score", 0.0) or 0.0),
        analysis_mode=str(payload.get("analysis_mode", "summary") or "summary"),
        chapters_audited=[
            int(ch)
            for ch in (payload.get("chapters_audited") or [])
            if isinstance(ch, (int, str)) and str(ch).strip().isdigit()
        ],
        truncated_chapters=[
            int(ch)
            for ch in (payload.get("truncated_chapters") or [])
            if isinstance(ch, (int, str)) and str(ch).strip().isdigit()
        ],
        repair_plan=[item for item in (payload.get("repair_plan") or []) if isinstance(item, dict)],
        auto_repair=(
            payload.get("auto_repair") if isinstance(payload.get("auto_repair"), dict) else None
        ),
        empty_result_reason=(
            str(payload.get("empty_result_reason"))
            if payload.get("empty_result_reason") is not None
            else None
        ),
        quality_metrics=quality_metrics,
        field_sources=dict(payload.get("field_sources") or {}),
        slices=[item for item in (payload.get("slices") or []) if isinstance(item, dict)],
        global_findings=[
            item for item in (payload.get("global_findings") or []) if isinstance(item, dict)
        ],
        repair_queue_summary=(
            dict(payload.get("repair_queue_summary") or {})
            if isinstance(payload.get("repair_queue_summary"), dict)
            else {}
        ),
        coverage_metrics=(
            dict(payload.get("coverage_metrics") or {})
            if isinstance(payload.get("coverage_metrics"), dict)
            else {}
        ),
    )
    return result


def _load_final_audit_result_checkpoint(path: Path, resume_signature: str) -> Any | None:
    payload = _read_audit_checkpoint_payload(path)
    if not payload:
        return None
    if str(payload.get("status", "") or "") != "completed":
        return None
    if str(payload.get("final_result_signature", "") or "") != resume_signature:
        return None
    final_result = payload.get("final_result")
    if not isinstance(final_result, dict):
        return None
    return _book_consistency_result_from_payload(final_result)


def _save_final_audit_result_checkpoint(
    path: Path,
    *,
    resume_signature: str,
    result: Any,
) -> None:
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
    if not isinstance(payload, dict):
        return
    _update_audit_checkpoint_payload(
        path,
        {
            "status": "completed",
            "resume_signature": resume_signature,
            "final_result": payload,
            "final_result_signature": resume_signature,
            "final_result_saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _load_two_phase_summary_checkpoint(path: Path, summary_signature: str) -> Any | None:
    payload = _read_audit_checkpoint_payload(path)
    if str(payload.get("two_phase_summary_signature", "") or "") != summary_signature:
        return None
    summary_payload = payload.get("two_phase_summary_result")
    if not isinstance(summary_payload, dict):
        return None
    return _book_consistency_result_from_payload(summary_payload)


def _save_two_phase_summary_checkpoint(
    path: Path,
    *,
    resume_signature: str,
    summary_signature: str,
    summary_result: Any,
    meta: dict[str, Any],
) -> None:
    payload = (
        summary_result.model_dump(mode="json") if hasattr(summary_result, "model_dump") else {}
    )
    if not isinstance(payload, dict):
        return
    _update_audit_checkpoint_payload(
        path,
        {
            "status": "summary_completed",
            "resume_signature": resume_signature,
            "two_phase_summary_result": payload,
            "two_phase_summary_signature": summary_signature,
            "two_phase_summary_meta": meta,
        },
    )
