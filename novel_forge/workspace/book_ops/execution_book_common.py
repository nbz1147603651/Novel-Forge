"""Shared utilities for book consistency execution modules."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from novel_forge.obs.logger import get_logger
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.helpers.execution_helpers import _normalize_repair_lane
from novel_forge.workspace.runtime import RuntimeServices

_log = get_logger("workspace.execution_book_consistency")


class AuditError(Exception):
    """Structured error information for book audit failures."""

    def __init__(
        self,
        message: str,
        recoverable: bool,
        suggested_action: str,
        checkpoint_available: bool,
        error_type: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recoverable = recoverable
        self.suggested_action = suggested_action
        self.checkpoint_available = checkpoint_available
        self.error_type = error_type  # "validation", "model", "storage", "timeout", "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "recoverable": self.recoverable,
            "suggested_action": self.suggested_action,
            "checkpoint_available": self.checkpoint_available,
            "error_type": self.error_type,
        }


def _resolve_book_audit_analysis_mode(
    runtime: RuntimeServices, request: BookConsistencyRequest
) -> str:
    mode = str(getattr(request, "analysis_mode", "auto") or "auto").strip().lower()
    if mode in {"summary", "full_text"}:
        return mode
    configured = str(
        getattr(runtime.settings, "long_book_audit_default_mode", "full_text") or "full_text"
    )
    configured = configured.strip().lower()
    return configured if configured in {"summary", "full_text"} else "full_text"


def _compact_issue_pool_entry(
    *,
    chapter_number: int,
    lane: str,
    index: int,
    issue: Any,
    created_at: Any = None,
) -> dict[str, Any] | None:
    lane_norm = _normalize_repair_lane(lane)
    if chapter_number <= 0 or lane_norm not in {"continuity", "causal"} or index < 0:
        return None
    if hasattr(issue, "model_dump"):
        payload = issue.model_dump(mode="json")
    elif isinstance(issue, dict):
        payload = issue
    else:
        return None
    if not isinstance(payload, dict):
        return None
    fix_hint = ""
    if lane_norm == "continuity":
        actions = payload.get("fix_actions", [])
        if isinstance(actions, list):
            fix_hint = "；".join(str(item).strip() for item in actions if str(item).strip())[:180]
    else:
        fix_hint = str(payload.get("fix_suggestion", "") or "").strip()[:180]
    created_at_raw = payload.get("created_at") or created_at
    created_at_text = str(created_at_raw or "").strip()
    if not created_at_text:
        created_at_text = datetime.now(timezone.utc).isoformat()
    else:
        created_at_text = _normalize_issue_pool_timestamp(created_at_text)

    return {
        "chapter_number": chapter_number,
        "lane": lane_norm,
        "index": index,
        "issue_type": str(payload.get("issue_type", "") or "").strip(),
        "severity": str(payload.get("severity", "") or "").strip().lower(),
        "summary": str(payload.get("summary", "") or "").strip(),
        "location": str(payload.get("location", "") or "").strip(),
        "evidence": str(payload.get("evidence", "") or "").strip(),
        "fix_hint": fix_hint,
        "created_at": created_at_text,
    }


def _normalize_issue_pool_timestamp(value: str) -> str:
    """Return a stable ISO timestamp when possible, preserving invalid legacy values."""
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        created_at = datetime.fromisoformat(normalized)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.isoformat()
    except (ValueError, TypeError):
        return value


def _report_passes_content_hash_gate(
    report: dict[str, Any],
    chapter_num: int,
    *,
    chapter_text_hash_by_chapter: dict[int, str] | None = None,
    canon_state_hash: str | None = None,
    legacy_grace_hours: int = 0,
) -> bool:
    """Check whether a report passes the content-hash AND gate.

    Returns ``True`` if the report should be **included** in the pool.

    **AND-gate logic** (content-hash is an additional check, NOT a replacement for TTL):
      1. If the report has *both* ``source_text_hash`` and ``canon_state_hash``:
         - Both must match the current values → pass
         - Either mismatches → fail (emit ``issue_pool_filtered_content_hash_mismatch``)
      2. If the report is **missing** hash metadata (legacy report):
         - Within ``legacy_grace_hours`` of ``created_at`` → pass (graceful degradation)
         - Outside grace period → fail (emit ``issue_pool_filtered_missing_hash_metadata``)
      3. **Race-condition semantics**: content-hash is a snapshot at persist time. If
         chapter text changes during audit, hash mismatch causes the pool entry to be
         filtered (correct — the chapter changed, issues must be re-evaluated).

    The TTL check is applied separately by the caller — this function only checks
    content-hash validity.
    """
    stored_text_hash: str | None = report.get("source_text_hash")
    stored_canon_hash: str | None = report.get("canon_state_hash")

    # ── Case 1: Both hashes present → normal content-hash check ────────────
    if stored_text_hash is not None and stored_canon_hash is not None:
        current_text_hash: str | None = (
            chapter_text_hash_by_chapter.get(chapter_num) if chapter_text_hash_by_chapter else None
        )
        if current_text_hash is not None and stored_text_hash != current_text_hash:
            _log.info(
                "issue_pool_filtered_content_hash_mismatch | ch=%d | field=chapter_text_hash"
                " | stored=%s | current=%s",
                chapter_num,
                stored_text_hash[:8],
                current_text_hash[:8],
            )
            return False
        if canon_state_hash is not None and stored_canon_hash != canon_state_hash:
            _log.info(
                "issue_pool_filtered_content_hash_mismatch | ch=%d | field=canon_state_hash"
                " | stored=%s | current=%s",
                chapter_num,
                stored_canon_hash[:8],
                canon_state_hash[:8],
            )
            return False
        # Both hashes present and match
        _log.info(
            "issue_pool_hit_content_hash_and_ttl | ch=%d | text_hash=%s | canon_hash=%s",
            chapter_num,
            stored_text_hash[:8],
            stored_canon_hash[:8],
        )
        return True

    # ── Case 2: Missing hash metadata → grace period check ─────────────────
    created_at_str = str(report.get("created_at", "") or "").strip()
    if created_at_str:
        try:
            normalized = (
                created_at_str.replace("Z", "+00:00")
                if created_at_str.endswith("Z")
                else created_at_str
            )
            created_at = datetime.fromisoformat(normalized)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            report_age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
            if report_age_hours <= legacy_grace_hours:
                # Legacy report within grace period — allow reuse
                _log.info(
                    "issue_pool_hit_content_hash_and_ttl | ch=%d | legacy_report_within_grace | age_h=%.1f | grace_h=%d",
                    chapter_num,
                    report_age_hours,
                    legacy_grace_hours,
                )
                return True
            # Outside grace period
            _log.info(
                "issue_pool_filtered_missing_hash_metadata | ch=%d | age_h=%.1f | grace_h=%d",
                chapter_num,
                report_age_hours,
                legacy_grace_hours,
            )
            return False
        except (ValueError, TypeError):
            pass
    # No created_at or unparseable — keep for backward compat (same as TTL logic)
    return True


def _compute_chapter_text_hashes(
    storage: Any,
    layout: Any,
    chapter_numbers: list[int],
) -> dict[int, str]:
    """Read chapter texts from disk and return ``{chapter_number: sha256}``."""
    result: dict[int, str] = {}
    for cn in chapter_numbers:
        cp = layout.chapter_path(cn)
        if storage.exists(cp):
            try:
                raw = storage.read_text(cp)
                result[cn] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            except Exception:
                continue
    return result


def _load_issue_panel_pool_for_chapters(
    *,
    storage: Any,
    layout: Any,
    chapter_numbers: list[int],
    ttl_hours: int = 72,
    # ── Content-hash AND-gate parameters (optional, backward-compatible) ──
    chapter_text_hash_by_chapter: dict[int, str] | None = None,
    canon_state_hash: str | None = None,
    legacy_grace_hours: int = 0,
) -> list[dict[str, Any]]:
    """Load normalized issue-pool entries from per-chapter continuity/causal reports.

    The filter is an **AND gate**: both TTL **and** content-hash must pass.
    Content-hash filtering is only activated when ``chapter_text_hash_by_chapter``
    and/or ``canon_state_hash`` are provided (backward-compatible default).

    Parameters
    ----------
    chapter_text_hash_by_chapter : dict[int, str] | None
        Maps chapter_number → SHA-256 hexdigest of the current chapter text.
        When ``None``, content-hash filtering is skipped for the text dimension.
    canon_state_hash : str | None
        SHA-256 hexdigest of the current canon state (5 key fields).
        When ``None``, content-hash filtering is skipped for the canon dimension.
    legacy_grace_hours : int
        Reports without hash metadata are still reused if their ``created_at``
        is within this many hours (graceful degradation for older reports).
    """
    try:
        ttl_hours = int(ttl_hours)
    except (TypeError, ValueError):
        ttl_hours = 72
    cutoff = None if ttl_hours <= 0 else datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    pool: list[dict[str, Any]] = []

    # Determine whether content-hash checking is active
    content_hash_enabled = chapter_text_hash_by_chapter is not None or canon_state_hash is not None

    for chapter_num in chapter_numbers:
        # ── Continuity reports ─────────────────────────────────────────
        cont_path = layout.continuity_report_path(chapter_num)
        if storage.exists(cont_path):
            try:
                cont_raw = storage.load_json(cont_path) or {}
                # Content-hash AND-gate check (applied at report level)
                if content_hash_enabled and not _report_passes_content_hash_gate(
                    cont_raw,
                    chapter_num,
                    chapter_text_hash_by_chapter=chapter_text_hash_by_chapter,
                    canon_state_hash=canon_state_hash,
                    legacy_grace_hours=legacy_grace_hours,
                ):
                    pass  # Report filtered by content-hash gate
                else:
                    for idx, issue in enumerate(cont_raw.get("issues") or []):
                        entry = _compact_issue_pool_entry(
                            chapter_number=chapter_num,
                            lane="continuity",
                            index=idx,
                            issue=issue,
                            created_at=cont_raw.get("created_at"),
                        )
                        if entry is not None:
                            if cutoff is None or _is_entry_within_ttl(entry, cutoff):
                                pool.append(entry)
                            elif cutoff is not None:
                                _log.info(
                                    "issue_pool_filtered_ttl_expired | ch=%d | lane=continuity",
                                    chapter_num,
                                )
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "Skipping continuity report ch%d (load/parse error): %s", chapter_num, exc
                )
        # ── Causal reports ─────────────────────────────────────────────
        causal_path = layout.chapter_causal_report_path(chapter_num)
        if storage.exists(causal_path):
            try:
                causal_raw = storage.load_json(causal_path) or {}
                # Content-hash AND-gate check (applied at report level)
                if content_hash_enabled and not _report_passes_content_hash_gate(
                    causal_raw,
                    chapter_num,
                    chapter_text_hash_by_chapter=chapter_text_hash_by_chapter,
                    canon_state_hash=canon_state_hash,
                    legacy_grace_hours=legacy_grace_hours,
                ):
                    pass  # Report filtered by content-hash gate
                else:
                    for idx, issue in enumerate(causal_raw.get("issues") or []):
                        entry = _compact_issue_pool_entry(
                            chapter_number=chapter_num,
                            lane="causal",
                            index=idx,
                            issue=issue,
                            created_at=causal_raw.get("created_at"),
                        )
                        if entry is not None:
                            if cutoff is None or _is_entry_within_ttl(entry, cutoff):
                                pool.append(entry)
                            elif cutoff is not None:
                                _log.info(
                                    "issue_pool_filtered_ttl_expired | ch=%d | lane=causal",
                                    chapter_num,
                                )
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping causal report ch%d (load/parse error): %s", chapter_num, exc)
    return pool


def _is_entry_within_ttl(entry: dict[str, Any], cutoff: datetime) -> bool:
    """Check if a pool entry is within the TTL window.

    Backward-compatible: entries without a valid created_at are always kept.
    """
    created_at_str = str(entry.get("created_at", "") or "").strip()
    if not created_at_str:
        return True
    try:
        normalized = (
            created_at_str.replace("Z", "+00:00")
            if created_at_str.endswith("Z")
            else created_at_str
        )
        created_at = datetime.fromisoformat(normalized)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at >= cutoff
    except (ValueError, TypeError):
        return True


def _resolve_issue_pool_ttl_hours(settings: Any) -> int:
    """Read issue-pool TTL from Settings; 0 disables age filtering."""
    try:
        return max(0, int(getattr(settings, "long_book_audit_issue_pool_ttl_hours", 72)))
    except (TypeError, ValueError):
        return 72



