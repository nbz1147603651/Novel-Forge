"""Global book-audit planning, evidence indexing, and repair queue control plane."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from novel_forge.common.global_audit_dimensions import GLOBAL_AUDIT_DIMENSIONS
from novel_forge.core.review.review_contracts import (
    compile_repair_ticket_from_finding,
    normalize_issue_to_finding,
    source_text_hash,
)
from novel_forge.core.review.review_precision import prepare_findings_for_repair
from novel_forge.core.utils.patch_utils import resolve_paragraph_locally
from novel_forge.obs.audit_store import GlobalAuditSlice, GlobalAuditStore  # noqa: F401
from novel_forge.persistence.models import ProjectLayout

GLOBAL_AUDIT_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _stable_id(prefix: str, payload: Any, *, length: int = 16) -> str:
    raw = _json_dump(payload)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        dumped = dataclasses.asdict(value)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_chapter_list(value: Any) -> list[int]:
    if isinstance(value, (int, str)) and str(value).strip().isdigit():
        return [int(value)]
    if not isinstance(value, list):
        return []
    chapters: list[int] = []
    for item in value:
        if isinstance(item, dict):
            for key in ("chapter_number", "chapter", "source_chapter", "target_chapter"):
                num = _coerce_int(item.get(key), 0)
                if num > 0:
                    chapters.append(num)
                    break
            continue
        num = _coerce_int(item, 0)
        if num > 0:
            chapters.append(num)
    return sorted(set(chapters))


def _chapters_from_range_payload(payload: dict[str, Any]) -> list[int]:
    for key in ("chapters", "chapter_numbers", "covered_chapters"):
        chapters = _coerce_chapter_list(payload.get(key))
        if chapters:
            return chapters

    range_value = (
        payload.get("chapter_range") or payload.get("range") or payload.get("chapters_range")
    )
    if isinstance(range_value, list) and range_value:
        nums = [_coerce_int(item, 0) for item in range_value]
        nums = [num for num in nums if num > 0]
        if len(nums) >= 2:
            return list(range(min(nums), max(nums) + 1))
        if len(nums) == 1:
            return nums
    if isinstance(range_value, dict):
        start = _coerce_int(
            range_value.get("start") or range_value.get("from") or range_value.get("start_chapter"),
            0,
        )
        end = _coerce_int(
            range_value.get("end") or range_value.get("to") or range_value.get("end_chapter"),
            0,
        )
        if start > 0 and end > 0:
            return list(range(min(start, end), max(start, end) + 1))

    start = _coerce_int(
        payload.get("start_chapter") or payload.get("first_chapter") or payload.get("start"),
        0,
    )
    end = _coerce_int(
        payload.get("end_chapter") or payload.get("last_chapter") or payload.get("end"),
        0,
    )
    if start > 0 and end > 0:
        return list(range(min(start, end), max(start, end) + 1))
    chapter = _coerce_int(payload.get("chapter") or payload.get("chapter_number"), 0)
    return [chapter] if chapter > 0 else []


def _intersect_chapters(chapters: list[int], completed: list[int]) -> list[int]:
    completed_set = set(completed)
    return sorted(ch for ch in set(chapters) if ch in completed_set)


def _boundary_chapters(chapters: list[int], completed: list[int]) -> list[int]:
    if not chapters:
        return []
    completed_set = set(completed)
    boundaries = {chapters[0], chapters[-1], min(chapters) - 1, max(chapters) + 1}
    return sorted(ch for ch in boundaries if ch in completed_set)


@dataclass(frozen=True)
class EvidenceWindow:
    """A verified or candidate source-text window for an audit finding."""

    chapter_number: int
    paragraph_span: list[int]
    text: str
    source_hash: str
    locator_method: str
    confidence: float

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class GlobalFinding:
    """Normalized whole-book finding with evidence and locator candidates."""

    finding_id: str
    slice_id: str
    dimension: str
    severity: str
    chapters_involved: list[int]
    primary_chapter: int
    evidence_pairs: list[dict[str, Any]] = field(default_factory=list)
    locator_candidates: list[dict[str, Any]] = field(default_factory=list)
    repair_readiness: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class GlobalRepairQueue:
    """Repair tickets compiled from global findings without applying text edits."""

    run_id: str
    items: list[dict[str, Any]]
    summary: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class GlobalAuditPlanner:
    """Create stable global-audit slices from outline, blueprint, and kernel ledgers."""

    def plan(
        self,
        *,
        completed_chapters: list[int],
        outline: dict[str, Any] | None = None,
        blueprint: dict[str, Any] | None = None,
        kernel_context: dict[str, Any] | None = None,
    ) -> list[GlobalAuditSlice]:
        completed = sorted({int(ch) for ch in completed_chapters if int(ch) > 0})
        if not completed:
            return []
        outline = outline or {}
        blueprint = blueprint or {}
        kernel_context = kernel_context or {}

        slices: list[GlobalAuditSlice] = []
        seen: set[str] = set()

        def add_slice(
            *,
            slice_id: str,
            slice_kind: str,
            chapters: list[int],
            focus_dimensions: list[str],
            source_refs: list[dict[str, Any]],
            dimension_role: str = "shared",
        ) -> None:
            selected = _intersect_chapters(chapters, completed)
            if not selected or slice_id in seen:
                return
            seen.add(slice_id)
            slices.append(
                GlobalAuditSlice(
                    slice_id=slice_id,
                    slice_kind=slice_kind,
                    chapters=selected,
                    boundary_chapters=_boundary_chapters(selected, completed),
                    focus_dimensions=focus_dimensions,
                    dimension="",
                    dimension_role=dimension_role,
                    source_refs=source_refs,
                )
            )

        add_slice(
            slice_id="whole_book",
            slice_kind="whole_book",
            chapters=completed,
            focus_dimensions=list(GLOBAL_AUDIT_DIMENSIONS),
            source_refs=[{"source": "completed_chapters"}],
            dimension_role="shared_whole_book",
        )

        volumes = outline.get("volumes")
        if isinstance(volumes, list):
            for idx, volume in enumerate(volumes, start=1):
                if not isinstance(volume, dict):
                    continue
                chapters = _chapters_from_range_payload(volume)
                title = str(volume.get("title") or volume.get("name") or "").strip()
                add_slice(
                    slice_id=f"volume_{idx}",
                    slice_kind="volume",
                    chapters=chapters,
                    focus_dimensions=[
                        "timeline_arc",
                        "promise_payoff",
                        "character_arc",
                        "plot_thread_liveness",
                        "tension_curve",
                    ],
                    source_refs=[{"source": "outline.volumes", "index": idx, "title": title}],
                    dimension_role="continuous_arc",
                )

        phase_source = blueprint or outline.get("narrative_blueprint") or {}
        phases: list[Any] = []
        if isinstance(phase_source, dict):
            for key in ("phases", "acts", "stages", "arc_phases"):
                raw = phase_source.get(key)
                if isinstance(raw, list) and raw:
                    phases = raw
                    break
        if phases:
            fallback_groups = self._even_groups(completed, len(phases))
            for idx, phase in enumerate(phases, start=1):
                payload = phase if isinstance(phase, dict) else {"name": str(phase)}
                chapters = _chapters_from_range_payload(payload) or fallback_groups[idx - 1]
                add_slice(
                    slice_id=f"blueprint_phase_{idx}",
                    slice_kind="blueprint_phase",
                    chapters=chapters,
                    focus_dimensions=["timeline_arc", "tension_curve", "promise_payoff"],
                    source_refs=[
                        {
                            "source": "narrative_blueprint.phases",
                            "index": idx,
                            "name": str(payload.get("name") or payload.get("title") or ""),
                        }
                    ],
                    dimension_role="blueprint_phase",
                )

        turning_points = []
        if isinstance(phase_source, dict):
            raw_turning = phase_source.get("turning_points") or phase_source.get("turns")
            turning_points = raw_turning if isinstance(raw_turning, list) else []
        for idx, turning_point in enumerate(turning_points, start=1):
            if not isinstance(turning_point, dict):
                continue
            chapters = _chapters_from_range_payload(turning_point)
            if chapters:
                expanded = sorted(set(chapters + [chapters[0] - 1, chapters[-1] + 1]))
                add_slice(
                    slice_id=f"turning_point_{idx}",
                    slice_kind="turning_point",
                    chapters=expanded,
                    focus_dimensions=["timeline_arc", "tension_curve", "promise_payoff"],
                    source_refs=[{"source": "narrative_blueprint.turning_points", "index": idx}],
                    dimension_role="boundary_turning_point",
                )

        for idx, promise in enumerate(
            self._list_field(kernel_context.get("promise_ledger")), start=1
        ):
            chapters = self._chapters_from_ledger_item(
                promise,
                keys=("planted_chapter", "fulfilled_chapter", "payoff_chapter", "source_chapter"),
            )
            add_slice(
                slice_id=_stable_id("promise", {"idx": idx, "chapters": chapters, "item": promise}),
                slice_kind="promise_thread",
                chapters=chapters,
                focus_dimensions=["promise_payoff", "plot_thread_liveness"],
                source_refs=[{"source": "kernel.promise_ledger", "index": idx}],
                dimension_role="non_contiguous_promise",
            )

        for idx, thread in enumerate(self._list_field(kernel_context.get("plot_threads")), start=1):
            chapters = self._chapters_from_ledger_item(
                thread,
                keys=("start_chapter", "last_chapter", "current_chapter", "source_chapter"),
            )
            add_slice(
                slice_id=_stable_id(
                    "plot_thread", {"idx": idx, "chapters": chapters, "item": thread}
                ),
                slice_kind="plot_thread",
                chapters=chapters,
                focus_dimensions=["plot_thread_liveness", "promise_payoff", "tension_curve"],
                source_refs=[{"source": "kernel.plot_threads", "index": idx}],
                dimension_role="non_contiguous_plot_thread",
            )

        for idx, motif in enumerate(
            self._list_field(kernel_context.get("motif_protocols")), start=1
        ):
            chapters = self._chapters_from_ledger_item(
                motif,
                keys=("introduced_chapter", "last_seen_chapter", "source_chapter"),
            )
            add_slice(
                slice_id=_stable_id("motif", {"idx": idx, "chapters": chapters, "item": motif}),
                slice_kind="motif",
                chapters=chapters or completed,
                focus_dimensions=["motif_distribution", "tension_curve"],
                source_refs=[{"source": "kernel.motif_protocols", "index": idx}],
                dimension_role="non_contiguous_motif",
            )

        return slices

    @staticmethod
    def _even_groups(chapters: list[int], count: int) -> list[list[int]]:
        if count <= 0:
            return []
        groups: list[list[int]] = [[] for _ in range(count)]
        for idx, chapter in enumerate(chapters):
            bucket = min(count - 1, idx * count // max(1, len(chapters)))
            groups[bucket].append(chapter)
        return groups

    @staticmethod
    def _list_field(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            for key in ("items", "threads", "promises", "motifs", "entries"):
                raw = value.get(key)
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _chapters_from_ledger_item(item: dict[str, Any], *, keys: tuple[str, ...]) -> list[int]:
        chapters = _chapters_from_range_payload(item)
        for key in keys:
            num = _coerce_int(item.get(key), 0)
            if num > 0:
                chapters.append(num)
        for key in ("chapters", "chapter_numbers", "occurrences", "evidence"):
            chapters.extend(_coerce_chapter_list(item.get(key)))
        return sorted({ch for ch in chapters if ch > 0})


class SemanticEvidenceLocator:
    """Locate finding evidence using local anchors first, semantic recall second."""

    def __init__(
        self,
        *,
        semantic_search: Callable[[str, int], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._semantic_search = semantic_search

    def locate_issue(
        self,
        issue: Any,
        *,
        paragraph_lookup: Callable[[int], list[str]],
        source_hash_lookup: Callable[[int], str],
        max_semantic_candidates: int = 5,
    ) -> list[dict[str, Any]]:
        issue_dict = _to_dict(issue)
        primary = _coerce_int(
            issue_dict.get("primary_chapter") or issue_dict.get("chapter_number"),
            0,
        )
        evidence = str(issue_dict.get("evidence") or issue_dict.get("evidence_quote") or "").strip()
        location = str(issue_dict.get("location") or "").strip()
        llm_hint = _coerce_int(issue_dict.get("paragraph_index"), 0)
        candidates: list[dict[str, Any]] = []

        if primary > 0:
            paragraphs = paragraph_lookup(primary)
            source_hash = source_hash_lookup(primary)
            if evidence:
                for idx, paragraph in enumerate(paragraphs, start=1):
                    if evidence and evidence in paragraph:
                        candidates.append(
                            self._candidate(
                                method="exact",
                                chapter_number=primary,
                                paragraph_span=[idx, idx],
                                text=paragraph,
                                source_hash=source_hash,
                                confidence=0.98,
                            )
                        )
                        break
            if paragraphs and (evidence or location or llm_hint > 0):
                targets, anchor_type, confidence = resolve_paragraph_locally(
                    paragraphs,
                    evidence=evidence,
                    location=location,
                    llm_hint=llm_hint,
                )
                if targets:
                    span = [targets[0] + 1, targets[-1] + 1]
                    text = "\n\n".join(paragraphs[idx] for idx in targets if idx < len(paragraphs))
                    method = "fuzzy" if anchor_type != "reported_paragraph" else "llm_locator"
                    candidates.append(
                        self._candidate(
                            method=method,
                            chapter_number=primary,
                            paragraph_span=span,
                            text=text,
                            source_hash=source_hash,
                            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
                        )
                    )

        if self._semantic_search is not None:
            query = " ".join(
                part
                for part in (
                    str(issue_dict.get("description") or issue_dict.get("summary") or ""),
                    evidence,
                    str(issue_dict.get("issue_type") or issue_dict.get("category") or ""),
                )
                if part
            ).strip()
            if query:
                for item in self._semantic_search(query, max_semantic_candidates):
                    if not isinstance(item, dict):
                        continue
                    chapter = _coerce_int(item.get("chapter_number"), 0)
                    confidence = float(item.get("confidence", 0.0) or 0.0)
                    candidates.append(
                        self._candidate(
                            method="semantic",
                            chapter_number=chapter,
                            paragraph_span=list(item.get("paragraph_span") or []),
                            text=str(item.get("text") or ""),
                            source_hash=str(item.get("source_hash") or source_hash_lookup(chapter)),
                            confidence=max(0.0, min(1.0, confidence)),
                        )
                    )

        return self._dedupe_candidates(candidates)

    @staticmethod
    def _candidate(
        *,
        method: str,
        chapter_number: int,
        paragraph_span: list[int],
        text: str,
        source_hash: str,
        confidence: float,
    ) -> dict[str, Any]:
        window = EvidenceWindow(
            chapter_number=chapter_number,
            paragraph_span=paragraph_span,
            text=text[:2000],
            source_hash=source_hash,
            locator_method=method,
            confidence=confidence,
        )
        payload = window.model_dump()
        payload["candidate_id"] = _stable_id("locator", payload)
        return payload

    @staticmethod
    def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[int, tuple[int, ...], str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in sorted(candidates, key=lambda c: float(c.get("confidence", 0.0)), reverse=True):
            key = (
                _coerce_int(item.get("chapter_number"), 0),
                tuple(_coerce_int(v, 0) for v in item.get("paragraph_span", []) or []),
                str(item.get("locator_method") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped


def build_chapter_paragraph_index(
    layout: ProjectLayout,
    chapter_numbers: list[int],
) -> tuple[list[dict[str, Any]], dict[int, list[str]], dict[int, str]]:
    """Index chapter paragraphs while keeping markdown files as source of truth."""
    rows: list[dict[str, Any]] = []
    paragraphs_by_chapter: dict[int, list[str]] = {}
    chapter_hashes: dict[int, str] = {}
    for chapter_number in sorted({int(ch) for ch in chapter_numbers if int(ch) > 0}):
        source_path = layout.chapter_path(chapter_number)
        if not source_path.exists():
            source_path = layout.chapter_review_draft_path(chapter_number)
        text = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
        chapter_hash = source_text_hash(text)
        chapter_hashes[chapter_number] = chapter_hash
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
        paragraphs_by_chapter[chapter_number] = paragraphs
        for idx, paragraph in enumerate(paragraphs, start=1):
            para_hash = source_text_hash(paragraph)
            rows.append(
                {
                    "chapter_number": chapter_number,
                    "paragraph_index": idx,
                    "text_summary": paragraph[:240],
                    "chapter_hash": chapter_hash,
                    "paragraph_hash": para_hash,
                    "source_path": str(source_path),
                }
            )
    return rows, paragraphs_by_chapter, chapter_hashes


class GlobalRepairQueueExecutor:
    """Compile and gate repair tickets produced by a global audit run."""

    def __init__(
        self,
        *,
        paragraph_lookup: Callable[[int], list[str]],
        source_hash_lookup: Callable[[int], str],
        completed_chapters: list[int] | set[int],
    ) -> None:
        self._paragraph_lookup = paragraph_lookup
        self._source_hash_lookup = source_hash_lookup
        self._completed_chapters = sorted({int(ch) for ch in completed_chapters if int(ch) > 0})

    def build_queue(
        self,
        *,
        run_id: str,
        issues: list[Any],
        slices: list[GlobalAuditSlice] | list[dict[str, Any]],
    ) -> GlobalRepairQueue:
        slice_by_chapter = self._slice_lookup(slices)
        findings = []
        for issue in issues:
            issue_dict = _to_dict(issue)
            if not issue_dict:
                continue
            primary = _coerce_int(
                issue_dict.get("primary_chapter") or issue_dict.get("chapter_number"),
                0,
            )
            if primary <= 0:
                involved = _coerce_chapter_list(issue_dict.get("chapters_involved"))
                primary = involved[0] if involved else 0
            source_hash = self._source_hash_lookup(primary) if primary > 0 else ""
            metadata = {
                "global_audit_run_id": run_id,
                "slice_id": issue_dict.get("slice_id")
                or slice_by_chapter.get(primary, "whole_book"),
                "chapters_involved": issue_dict.get("chapters_involved") or [],
                "evidence_pairs": issue_dict.get("evidence_pairs") or [],
                "locator_candidates": issue_dict.get("locator_candidates") or [],
                "global_repair_queue": True,
            }
            findings.append(
                normalize_issue_to_finding(
                    issue_dict,
                    source_module="book_consistency",
                    chapter_number=primary,
                    dimension=str(issue_dict.get("dimension") or issue_dict.get("category") or ""),
                    current_text_hash=source_hash,
                    review_mode="full_review",
                    metadata=metadata,
                )
            )

        prepared_findings, readiness_summary = prepare_findings_for_repair(
            findings,
            completed_chapters=self._completed_chapters,
            paragraph_lookup=self._paragraph_lookup,
        )

        items: list[dict[str, Any]] = []
        status_counts = {"ready": 0, "verify_first": 0, "manual_review": 0, "blocked": 0}
        for finding in prepared_findings:
            readiness = dict(finding.metadata.get("repair_readiness") or {})
            status = str(readiness.get("status") or "manual_review")
            if status not in status_counts:
                status = "manual_review"
            status_counts[status] += 1
            ticket_payload: dict[str, Any] | None = None
            if status == "ready" and bool(finding.metadata.get("auto_repair_eligible", True)):
                ticket_payload = compile_repair_ticket_from_finding(finding).model_dump(mode="json")
            item = {
                "queue_item_id": f"global_queue_{finding.finding_id}",
                "run_id": run_id,
                "finding_id": finding.finding_id,
                "slice_id": str(finding.metadata.get("slice_id") or "whole_book"),
                "status": status,
                "target_chapter": finding.chapter_number,
                "paragraph_span": [
                    finding.paragraph_start,
                    max(finding.paragraph_start, finding.paragraph_end),
                ]
                if finding.paragraph_start > 0
                else [],
                "source_hash": finding.source_text_hash,
                "readiness": readiness,
                "ticket": ticket_payload,
                "attempt_count": 0,
                "last_verification_result": None,
            }
            items.append(item)

        summary = dict(readiness_summary)
        summary.update(
            {
                "run_id": run_id,
                "queue_item_count": len(items),
                "ready": status_counts["ready"],
                "verify_first": status_counts["verify_first"],
                "manual_review": status_counts["manual_review"],
                "blocked": status_counts["blocked"],
                "architecture": "global_repair_queue_v1",
            }
        )
        return GlobalRepairQueue(run_id=run_id, items=items, summary=summary)

    @staticmethod
    def _slice_lookup(slices: list[GlobalAuditSlice] | list[dict[str, Any]]) -> dict[int, str]:
        lookup: dict[int, str] = {}
        for item in slices:
            payload = item.model_dump() if isinstance(item, GlobalAuditSlice) else _to_dict(item)
            slice_id = str(payload.get("slice_id") or "")
            for chapter in payload.get("chapters") or []:
                num = _coerce_int(chapter, 0)
                if num > 0 and slice_id and num not in lookup:
                    lookup[num] = slice_id
        return lookup

