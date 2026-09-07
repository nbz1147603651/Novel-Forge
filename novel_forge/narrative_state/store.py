"""Persistence helpers for LLM-adjudicated narrative state."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover - Windows compatibility path
    fcntl = None  # type: ignore[assignment]

from novel_forge.narrative_state.schemas import (
    AdjudicationDecision,
    CandidateStateDelta,
    EntityRecord,
    EntityRegistry,
    FinalStateAdjudication,
    StateLedgerEntry,
    StoryStateProjection,
    stable_id,
)
from novel_forge.persistence.filesystem import atomic_write_text

logger = logging.getLogger(__name__)

# Prompt instruction keywords commonly leaked into concept entities.
_INSTRUCTION_PATTERNS = re.compile(
    r"(不得|不要|仅允许|仅称其为|修正方案|不涉及|保证叙事|以及老年|但无梦境|但需控制|依赖关系|"
    r"变化方向|合作方紧急|向投资方|喃喃|却不知来历|制造悬念|冲突越有|其他角色知道|"
    r"事件链是|仅称|仅限|必须|应当|需要|不能|禁止|不可)"
)
_DERIVED_INIT_REGISTRY_SOURCE = "deterministic_init_registry"
_PROJECTION_ACCEPTED_TAIL = 48
_PROJECTION_BY_TYPE_TAIL = 6
_PROJECTION_RECENT_SUMMARY_TAIL = 80
_PROJECTION_PENDING_TAIL = 80
_PENDING_QUEUE_MAX = 120
_COMPACT_TEXT_LIMIT = 240
_COMPACT_LIST_LIMIT = 8
_COMPACT_MAPPING_LIMIT = 10
_EVIDENCE_QUOTE_LIMIT = 2
_ALLOWED_STATE_SCOPES = {
    "chapter_presence",
    "main_plot",
    "subplot",
    "relationship_carry_forward",
    "contract_progression",
}
_STATE_UPDATE_KEYS = (
    "candidate_id",
    "scope",
    "state_path",
    "value",
    "summary",
    "present_characters",
    "entity_ids",
    "plot_thread_id",
    "subplot_id",
    "relationship_pair",
    "contract_field",
    "target",
    "knowledge_type",
    "cognitive_subjects",
    "cognitive_object",
    "cognitive_level",
    "action_level",
    "character_knowledge_coverage",
    "next_impact",
    "source",
)


def cleanup_concept_pollution(registry: EntityRegistry) -> int:
    """Remove concept entities that leaked from prompt instruction fragments.

    Rules (entity_type == "concept" only):
    - name > 50 chars → prompt fragment
    - name ends with 。 and > 20 chars → instruction sentence
    - name > 12 chars and matches _INSTRUCTION_PATTERNS → instruction fragment
    """
    before = len(registry.entities)
    clean: list[EntityRecord] = []
    for entity in registry.entities:
        if entity.entity_type != "concept":
            clean.append(entity)
            continue
        name = entity.name.strip()
        if entity.source == _DERIVED_INIT_REGISTRY_SOURCE:
            logger.debug(
                "cleanup_concept_pollution: removing derived init concept entity_id=%s name=%r",
                entity.entity_id,
                name[:80],
            )
            continue
        if len(name) > 50:
            logger.debug(
                "cleanup_concept_pollution: removing long concept entity_id=%s name=%r",
                entity.entity_id,
                name[:80],
            )
            continue
        if name.endswith("。") and len(name) > 20:
            logger.debug(
                "cleanup_concept_pollution: removing sentence concept entity_id=%s name=%r",
                entity.entity_id,
                name[:80],
            )
            continue
        if len(name) > 12 and _INSTRUCTION_PATTERNS.search(name):
            logger.debug(
                "cleanup_concept_pollution: removing instruction concept entity_id=%s name=%r",
                entity.entity_id,
                name[:80],
            )
            continue
        clean.append(entity)
    registry.entities = clean
    removed = before - len(clean)
    if removed:
        logger.info("cleanup_concept_pollution: removed %d concept entities", removed)
    return removed


class NarrativeStateStore:
    """Project-local store for entity registry, adjudication reports, and ledger."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / "narrative_state"
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def entity_registry_path(self) -> Path:
        return self.root / "entity_registry.json"

    @property
    def state_ledger_path(self) -> Path:
        return self.root / "state_ledger.jsonl"

    @property
    def pending_queue_path(self) -> Path:
        return self.root / "pending_queue.json"

    @property
    def projection_path(self) -> Path:
        return self.root / "story_state_projection.json"

    @property
    def report_index_path(self) -> Path:
        return self.root / "adjudication_report_index.json"

    @property
    def evidence_dir(self) -> Path:
        return self.root / "evidence"

    @property
    def memory_index_path(self) -> Path:
        return self.root.parent / "memory" / "narrative_state_index.json"

    def adjudication_report_path(self, chapter_number: int) -> Path:
        return self.root / f"chapter_{chapter_number:03d}_state_adjudication.json"

    def evidence_snapshot_path(self, chapter_number: int) -> Path:
        return self.evidence_dir / f"chapter_{chapter_number:03d}_evidence.json"

    def load_entity_registry(self) -> EntityRegistry:
        if not self.entity_registry_path.exists():
            return EntityRegistry()
        data = json.loads(self.entity_registry_path.read_text(encoding="utf-8"))
        registry = EntityRegistry.model_validate(data)
        cleanup_concept_pollution(registry)
        return registry

    def save_entity_registry(self, registry: EntityRegistry) -> None:
        cleanup_concept_pollution(registry)
        atomic_write_text(
            self.entity_registry_path,
            json.dumps(registry.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def ensure_character_entities(self, names: list[str]) -> EntityRegistry:
        """Mechanically ensure known character names have entity ids."""
        registry = self.load_entity_registry()
        by_name = {item.name: item for item in registry.entities}
        changed = False
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name or name in by_name:
                continue
            record = EntityRecord(
                entity_id=stable_id("char", name),
                name=name,
                entity_type="character",
                aliases=[],
                source="known_characters",
            )
            registry.entities.append(record)
            by_name[name] = record
            changed = True
        if changed:
            self.save_entity_registry(registry)
        return registry

    def load_ledger_entries(self) -> list[StateLedgerEntry]:
        """Load all ledger entries with corruption tolerance and deduplication.

        Corrupted lines (invalid JSON) are logged and skipped.
        Duplicate ``entry_id`` values are deduplicated with last-writer-wins.
        """
        if not self.state_ledger_path.exists():
            return []
        entries: list[StateLedgerEntry] = []
        seen_ids: dict[str, int] = {}  # entry_id -> index in entries list
        try:
            text = self.state_ledger_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "load_ledger_entries: failed to read %s: %s", self.state_ledger_path, exc
            )
            return []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning(
                    "load_ledger_entries: skipping corrupt line %d in %s",
                    line_no,
                    self.state_ledger_path,
                )
                continue
            try:
                entry = StateLedgerEntry.model_validate(data)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "load_ledger_entries: skipping invalid entry at line %d in %s",
                    line_no,
                    self.state_ledger_path,
                )
                continue
            eid = entry.entry_id
            if eid in seen_ids:
                # last-writer-wins: replace earlier occurrence
                entries[seen_ids[eid]] = entry
            else:
                seen_ids[eid] = len(entries)
                entries.append(entry)
        return entries

    def _load_ledger_entries_sorted(self) -> list[StateLedgerEntry]:
        """Load and sort entries by (chapter_number, entry_id)."""
        entries = self.load_ledger_entries()
        entries.sort(key=lambda item: (item.chapter_number, item.entry_id))
        return entries

    def load_pending_items(self) -> list[dict[str, Any]]:
        if not self.pending_queue_path.exists():
            return []
        try:
            data = json.loads(self.pending_queue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        items = data.get("pending_items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def load_projection(self) -> StoryStateProjection:
        if not self.projection_path.exists():
            return StoryStateProjection()
        data = json.loads(self.projection_path.read_text(encoding="utf-8"))
        return StoryStateProjection.model_validate(data)

    def append_entries(self, entries: list[StateLedgerEntry]) -> None:
        """Append entries to the ledger using append-only writes.

        Uses file-level locking (``fcntl.flock``) for concurrent safety.
        Each stable ``entry_id`` is written at most once, so a crash between
        the ledger append and the finalization-manifest update can be replayed
        without duplicating accepted state.
        After appending, the projection is updated incrementally.
        """
        if not entries:
            return
        appended: list[StateLedgerEntry] = []
        with self._ledger_lock():
            existing_ids = {entry.entry_id for entry in self.load_ledger_entries()}
            appended = [entry for entry in entries if entry.entry_id not in existing_ids]
            if not appended:
                return
            lines = [
                json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
                for item in appended
            ]
            # Append-only write: no full rewrite, no re-sort
            with open(self.state_ledger_path, "a", encoding="utf-8") as fh:
                fh.writelines(lines)
        # Incremental projection update: load existing + merge new
        self._save_projection_incremental(appended)

    @contextlib.contextmanager
    def _ledger_lock(self) -> Iterator[None]:
        """File-level exclusive lock for append-only ledger writes."""
        if sys.platform == "win32":
            # Windows: skip file locking (not using fcntl); rely on atomic_write_text
            yield
            return
        if fcntl is None:
            yield
            return
        lock_path = self.state_ledger_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def append_pending_items(
        self,
        *,
        chapter_number: int,
        candidates: list[CandidateStateDelta],
        decisions: list[AdjudicationDecision],
        final_adjudication: FinalStateAdjudication,
        omitted_candidates: list[CandidateStateDelta] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist LLM-deferred items as a queue without resolving them locally."""
        candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
        decision_map = {decision.candidate_id: decision for decision in decisions}
        pending: list[dict[str, Any]] = []
        for item in final_adjudication.pending_items:
            if isinstance(item, dict):
                payload = dict(item)
                payload.setdefault("chapter_number", chapter_number)
                pending.append(payload)
        for candidate_id in final_adjudication.pending_candidate_ids:
            candidate = candidate_map.get(candidate_id)
            decision = decision_map.get(candidate_id)
            pending.append(
                {
                    "pending_id": stable_id("pending", chapter_number, candidate_id),
                    "chapter_number": chapter_number,
                    "candidate_id": candidate_id,
                    "summary": candidate.summary if candidate else "",
                    "delta_type": candidate.delta_type if candidate else "other",
                    "pending_reason": decision.pending_reason if decision else "",
                    "verdict": decision.verdict if decision else "defer",
                }
            )
        for candidate in omitted_candidates or []:
            pending.append(
                {
                    "pending_id": stable_id(
                        "pending_omitted",
                        chapter_number,
                        candidate.candidate_id,
                    ),
                    "chapter_number": chapter_number,
                    "candidate_id": candidate.candidate_id,
                    "summary": candidate.summary,
                    "delta_type": candidate.delta_type,
                    "pending_reason": "候选数量超过本轮裁判上限，未进入单候选裁判。",
                    "verdict": "defer",
                    "flow_status": "not_adjudicated",
                    "flow_reason": "candidate_cap_omitted",
                }
            )
        if not pending:
            return []

        existing = self.load_pending_items()
        by_id: dict[str, dict[str, Any]] = {}
        for item in existing + pending:
            pending_id = str(item.get("pending_id") or "").strip()
            if not pending_id:
                pending_id = stable_id(
                    "pending",
                    item.get("chapter_number", ""),
                    item.get("candidate_id", ""),
                    item.get("summary", ""),
                )
                item["pending_id"] = pending_id
            by_id[pending_id] = item
        ordered = sorted(
            by_id.values(),
            key=lambda item: (
                int(item.get("chapter_number", 0) or 0),
                str(item.get("pending_id", "")),
            ),
        )
        ordered = _trim_pending_queue(_compact_pending_items(ordered))
        atomic_write_text(
            self.pending_queue_path,
            json.dumps({"pending_items": ordered}, ensure_ascii=False, indent=2),
        )
        self.save_projection()
        return pending

    def save_report_artifacts(
        self,
        *,
        report: Any,
        report_paths: list[Path],
    ) -> None:
        """Persist report metadata and evidence pointers for UI/memory lookup."""
        self._save_evidence_snapshot(report)
        self._upsert_report_index(report=report, report_paths=report_paths)
        projection = (
            self.load_projection() if self.projection_path.exists() else StoryStateProjection()
        )
        self.save_memory_index(projection)

    def save_projection(
        self, entries: list[StateLedgerEntry] | None = None
    ) -> StoryStateProjection:
        """Rebuild projection from entries (or full ledger if entries is None).

        When *entries* is ``None``, performs a full rebuild via
        ``load_ledger_entries()``.  For incremental updates, prefer
        ``_save_projection_incremental()``.
        """
        entries = entries if entries is not None else self.load_ledger_entries()
        entries = [
            entry
            for entry in entries
            if str(getattr(entry, "evidence_status", "active") or "active") == "active"
        ]
        accepted_updates, facts_by_path, by_delta_type, recent_summaries = _project_entries(entries)
        projection = StoryStateProjection(
            entries=[],
            accepted_updates=accepted_updates[-_PROJECTION_ACCEPTED_TAIL:],
            facts_by_path=_normalize_facts_by_path(facts_by_path),
            by_delta_type={
                key: value[-_PROJECTION_BY_TYPE_TAIL:] for key, value in by_delta_type.items()
            },
            recent_summaries=recent_summaries[-_PROJECTION_RECENT_SUMMARY_TAIL:],
            pending_items=_compact_pending_items(self.load_pending_items())[
                -_PROJECTION_PENDING_TAIL:
            ],
            last_chapter=max((entry.chapter_number for entry in entries), default=0),
        )
        atomic_write_text(
            self.projection_path,
            json.dumps(projection.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        self.save_memory_index(projection)
        return projection

    def _save_projection_incremental(
        self, new_entries: list[StateLedgerEntry]
    ) -> StoryStateProjection:
        """Incrementally update projection by merging *new_entries* into existing.

        Avoids full ``load_ledger_entries()`` when only a small batch of new
        entries needs to be merged.  The heavy ``facts_by_path`` dict is
        updated in-place from new entries; tail slices are recomputed.
        """
        projection = self.load_projection()
        active_new = [
            entry
            for entry in new_entries
            if str(getattr(entry, "evidence_status", "active") or "active") == "active"
        ]
        if not active_new:
            return projection

        # Build projection fragments from new entries only
        new_accepted, new_facts, new_by_type, new_summaries = _project_entries(active_new)

        # Merge accepted_updates: append new, keep tail
        merged_accepted = list(projection.accepted_updates or []) + new_accepted
        merged_accepted = merged_accepted[-_PROJECTION_ACCEPTED_TAIL:]

        # Merge facts_by_path: new facts override old
        merged_facts = dict(projection.facts_by_path or {})
        merged_facts.update(new_facts)
        merged_facts = _normalize_facts_by_path(merged_facts)

        # Merge by_delta_type: append new per type, keep tail
        merged_by_type: dict[str, list[dict[str, Any]]] = dict(projection.by_delta_type or {})
        for dtype, items in new_by_type.items():
            existing = list(merged_by_type.get(dtype, []))
            existing.extend(items)
            merged_by_type[dtype] = existing[-_PROJECTION_BY_TYPE_TAIL:]

        # Merge recent_summaries: append new, keep tail
        merged_summaries = list(projection.recent_summaries or []) + new_summaries
        merged_summaries = merged_summaries[-_PROJECTION_RECENT_SUMMARY_TAIL:]

        # Compute new last_chapter
        new_max_ch = max(
            (entry.chapter_number for entry in active_new),
            default=int(projection.last_chapter or 0),
        )
        last_chapter = max(int(projection.last_chapter or 0), new_max_ch)

        updated = StoryStateProjection(
            entries=[],
            accepted_updates=merged_accepted,
            facts_by_path=merged_facts,
            by_delta_type=merged_by_type,
            recent_summaries=merged_summaries,
            pending_items=projection.pending_items,  # unchanged by ledger append
            last_chapter=last_chapter,
        )
        atomic_write_text(
            self.projection_path,
            json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        self.save_memory_index(updated)
        return updated

    def save_memory_index(self, projection: StoryStateProjection | None = None) -> None:
        """Persist a compact memory-facing index of adjudicated state."""
        projection = projection or self.load_projection()
        self.memory_index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "narrative_state_projection",
            "last_chapter": projection.last_chapter,
            "recent_summaries": projection.recent_summaries[-40:],
            "pending_items": projection.pending_items[-20:],
            "by_delta_type": {key: value[-20:] for key, value in projection.by_delta_type.items()},
            "report_index_path": str(self.report_index_path),
            "evidence_dir": str(self.evidence_dir),
        }
        atomic_write_text(
            self.memory_index_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def projection_for_prompt(
        self,
        *,
        max_entries: int = 24,
        max_pending: int = 12,
        max_chapter: int | None = None,
    ) -> dict[str, Any]:
        """Return a compact prompt payload bounded by a causal chapter waterline.

        When *max_chapter* is ``None`` or close to the cached projection's
        ``last_chapter``, the cached projection is used directly (O(1)).
        Only when *max_chapter* is significantly behind the projection does
        a full ledger scan occur (O(N)).
        """
        projection = self.load_projection()
        entries = list(projection.entries or [])
        pending_items = list(projection.pending_items or [])

        # Determine if we can serve from cached projection
        proj_last = int(projection.last_chapter or 0)
        can_use_cache = (
            max_chapter is None or max_chapter >= proj_last  # requesting current or future chapter
        )

        if not can_use_cache:
            # Historical waterline: must scan full ledger
            cutoff = max(0, int(max_chapter or 0))
            entries = self.load_ledger_entries()
            entries = [
                entry
                for entry in entries
                if int(getattr(entry, "chapter_number", 0) or 0) <= cutoff
            ]
            pending_items = [
                item
                for item in _compact_pending_items(self.load_pending_items())
                if _pending_item_chapter_number(item) <= cutoff
            ]
            accepted_updates, _facts_by_path, _by_delta_type, _recent_summaries = _project_entries(
                entries
            )
            last_chapter = max(
                (int(getattr(entry, "chapter_number", 0) or 0) for entry in entries),
                default=0,
            )
        else:
            accepted_updates = list(projection.accepted_updates or [])
            recent_summaries = [
                str(item).strip()
                for item in list(projection.recent_summaries or [])
                if str(item).strip()
            ]
            last_chapter = proj_last

        entry_limit = max(0, max_entries)
        pending_limit = max(0, max_pending)
        recent_entries = entries[-entry_limit:] if entry_limit else []
        summary_tail = (
            [entry.summary for entry in recent_entries if str(entry.summary or "").strip()]
            if max_chapter is not None
            else recent_summaries[-entry_limit:]
            if entry_limit
            else []
        )
        return {
            "last_chapter": last_chapter,
            "recent_summaries": summary_tail,
            "accepted_updates": accepted_updates[-entry_limit:] if entry_limit else [],
            "pending_items": _compact_pending_items(pending_items)[-pending_limit:]
            if pending_limit
            else [],
            "memory_index_path": str(self.memory_index_path),
        }

    @staticmethod
    def build_ledger_entries(
        *,
        chapter_number: int,
        candidates: list[CandidateStateDelta],
        decisions: list[AdjudicationDecision],
        final_adjudication: FinalStateAdjudication,
    ) -> list[StateLedgerEntry]:
        """Build entries exactly from LLM final accepted ids; no semantic judgement."""
        candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        decisions_by_id = {decision.candidate_id: decision for decision in decisions}
        entries: list[StateLedgerEntry] = []
        for candidate_id in final_adjudication.accepted_candidate_ids:
            candidate = candidates_by_id.get(candidate_id)
            decision = decisions_by_id.get(candidate_id)
            if candidate is None or decision is None:
                continue
            state_update = _state_update_for_candidate(candidate_id, final_adjudication)
            if not state_update:
                continue
            entries.append(
                StateLedgerEntry(
                    entry_id=stable_id("state", chapter_number, candidate_id, decision.verdict),
                    chapter_number=chapter_number,
                    candidate_id=candidate_id,
                    delta_type=candidate.delta_type,
                    summary=candidate.summary,
                    state_update=state_update,
                    decision=decision,
                    evidence=_compact_evidence_for_ledger(candidate.evidence),
                )
            )
        return entries

    def _save_evidence_snapshot(self, report: Any) -> None:
        evidence_items: list[dict[str, Any]] = []
        adjudicated_ids = {candidate.candidate_id for candidate in report.candidates}
        for candidate in list(report.candidates) + list(report.omitted_candidates):
            evidence_items.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "delta_type": candidate.delta_type,
                    "summary": candidate.summary,
                    "adjudicated": candidate.candidate_id in adjudicated_ids,
                    "evidence": [
                        span.model_dump(mode="json") if hasattr(span, "model_dump") else span
                        for span in candidate.evidence
                    ],
                }
            )
        atomic_write_text(
            self.evidence_snapshot_path(report.chapter_number),
            json.dumps(
                {
                    "chapter_number": report.chapter_number,
                    "source_text_hash": report.source_text_hash,
                    "contract_coverage": (
                        report.contract_coverage.model_dump(mode="json")
                        if getattr(report, "contract_coverage", None) is not None
                        and hasattr(report.contract_coverage, "model_dump")
                        else getattr(report, "contract_coverage", None)
                    ),
                    "evidence": evidence_items,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _upsert_report_index(self, *, report: Any, report_paths: list[Path]) -> None:
        existing: dict[str, Any] = {}
        if self.report_index_path.exists():
            try:
                loaded = json.loads(self.report_index_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except json.JSONDecodeError:
                existing = {}
        reports = existing.get("reports", [])
        if not isinstance(reports, list):
            reports = []
        final = report.final_adjudication
        evidence_count = sum(len(candidate.evidence) for candidate in report.candidates)
        found_evidence_count = sum(
            1 for candidate in report.candidates for span in candidate.evidence if span.found
        )
        payload = {
            "chapter_number": report.chapter_number,
            "source_text_hash": report.source_text_hash,
            "report_paths": [str(path) for path in report_paths],
            "canonical_report_path": str(self.adjudication_report_path(report.chapter_number)),
            "evidence_snapshot_path": str(self.evidence_snapshot_path(report.chapter_number)),
            "candidate_count": len(report.candidates),
            "omitted_candidate_count": len(report.omitted_candidates),
            "decision_count": len(report.decisions),
            "ledger_entry_count": len(report.ledger_entries),
            "final_verdict": final.verdict,
            "final_severity": final.severity,
            "final_confidence": final.confidence,
            "should_block_archive": final.should_block_archive,
            "accepted_count": len(final.accepted_candidate_ids),
            "pending_count": len(final.pending_candidate_ids) + len(final.pending_items),
            "repair_count": len(final.repair_candidate_ids) + len(final.repair_issues),
            "evidence_count": evidence_count,
            "found_evidence_count": found_evidence_count,
        }
        coverage = getattr(report, "contract_coverage", None)
        if coverage is not None:
            payload.update(
                {
                    "contract_total_required_targets": int(
                        getattr(coverage, "total_required_targets", 0) or 0
                    ),
                    "contract_covered_count": int(getattr(coverage, "covered_count", 0) or 0),
                    "contract_uncovered_count": len(
                        getattr(coverage, "uncovered_targets", []) or []
                    ),
                    "contract_unaccepted_count": len(
                        getattr(coverage, "unaccepted_targets", []) or []
                    ),
                    "contract_all_required_covered": bool(
                        getattr(coverage, "all_required_covered", False)
                    ),
                }
            )
        by_chapter = {
            int(item.get("chapter_number", 0) or 0): item
            for item in reports
            if isinstance(item, dict)
        }
        by_chapter[report.chapter_number] = payload
        ordered = [by_chapter[key] for key in sorted(by_chapter)]
        atomic_write_text(
            self.report_index_path,
            json.dumps({"reports": ordered}, ensure_ascii=False, indent=2),
        )


def _state_update_for_candidate(
    candidate_id: str,
    final_adjudication: FinalStateAdjudication,
) -> dict[str, Any]:
    for update in final_adjudication.state_updates:
        if isinstance(update, dict) and str(update.get("candidate_id") or "") == candidate_id:
            return _compact_state_update(update)
    return {}


def _pending_item_chapter_number(item: Any) -> int:
    if not isinstance(item, dict):
        return 0
    try:
        return max(0, int(item.get("chapter_number", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _compact_text(value: Any, *, limit: int = _COMPACT_TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compact_value(value: Any, *, depth: int = 0, text_limit: int = _COMPACT_TEXT_LIMIT) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text(value, limit=text_limit)
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except Exception:
            return _compact_text(value, limit=text_limit)
    if depth >= 2:
        return _compact_text(value, limit=text_limit)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:_COMPACT_MAPPING_LIMIT]:
            key_text = _compact_text(key, limit=80)
            if not key_text:
                continue
            result[key_text] = _compact_value(item, depth=depth + 1, text_limit=text_limit)
        return result
    if isinstance(value, (list, tuple, set)):
        return [
            _compact_value(item, depth=depth + 1, text_limit=text_limit)
            for item in list(value)[:_COMPACT_LIST_LIMIT]
        ]
    return _compact_text(value, limit=text_limit)


def _compact_state_update(update: dict[str, Any]) -> dict[str, Any]:
    """Project whitelisted state fields without truncating materialized values."""
    if not isinstance(update, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in _STATE_UPDATE_KEYS:
        if key in update:
            payload[key] = _json_safe_value(update[key])
    if "state_path" not in payload and "path" in update:
        payload["state_path"] = _json_safe_value(update["path"])
    if not payload.get("state_path"):
        return {}
    scope = _normalize_state_scope(payload.get("scope"))
    if scope:
        payload["scope"] = scope
    elif payload.get("state_path"):
        payload["scope"] = _infer_state_scope(payload.get("state_path"))
    return payload


def _compact_evidence_quotes(entry: StateLedgerEntry) -> list[str]:
    quotes: list[str] = []
    for span in list(entry.evidence or [])[:_EVIDENCE_QUOTE_LIMIT]:
        quote = _compact_text(getattr(span, "quote", ""), limit=160)
        if quote:
            quotes.append(quote)
    return quotes


def _compact_evidence_for_ledger(items: Any) -> list[Any]:
    compacted: list[Any] = []
    for span in list(items or [])[:_EVIDENCE_QUOTE_LIMIT]:
        if hasattr(span, "model_copy"):
            compacted.append(span.model_copy(update={"context": ""}))
        else:
            compacted.append(span)
    return compacted


def _normalize_state_scope(value: Any) -> str:
    scope = str(value or "").strip()
    return scope if scope in _ALLOWED_STATE_SCOPES else ""


def _infer_state_scope(state_path: Any) -> str:
    path = str(state_path or "").strip()
    if ".present_characters" in path or path.startswith("chapter."):
        return "chapter_presence"
    if path.startswith("plot.main"):
        return "main_plot"
    if path.startswith("subplot."):
        return "subplot"
    if path.startswith("relationship."):
        return "relationship_carry_forward"
    if path.startswith("contract."):
        return "contract_progression"
    return ""


def _compact_pending_item(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    keys = (
        "pending_id",
        "chapter_number",
        "candidate_id",
        "summary",
        "delta_type",
        "pending_reason",
        "verdict",
        "flow_status",
        "flow_reason",
    )
    payload = {key: _compact_value(item[key]) for key in keys if key in item}
    for key, value in item.items():
        if key in payload or len(payload) >= _COMPACT_MAPPING_LIMIT:
            continue
        payload[str(key)] = _compact_value(value)
    return payload


def _compact_pending_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [payload for item in items if (payload := _compact_pending_item(item))]


def _trim_pending_queue(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) <= _PENDING_QUEUE_MAX:
        return items
    return items[-_PENDING_QUEUE_MAX:]


def _json_safe_value(value: Any) -> Any:
    """Normalize persisted materialized state without content truncation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):
        return _json_safe_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _normalize_facts_by_path(mapping: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe_value(value) for key, value in (mapping or {}).items()}


def _project_entries(
    entries: list[StateLedgerEntry],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    accepted_updates: list[dict[str, Any]] = []
    facts_by_path: dict[str, Any] = {}
    by_delta_type: dict[str, list[dict[str, Any]]] = {}
    recent_summaries: list[str] = []
    for entry in entries:
        raw_update = dict(entry.state_update or {})
        update = _compact_state_update(raw_update)
        if not update:
            continue
        payload = {
            "chapter_number": entry.chapter_number,
            "candidate_id": entry.candidate_id,
            "delta_type": entry.delta_type,
            "summary": _compact_text(entry.summary),
            "state_update": update,
            "evidence_quotes": _compact_evidence_quotes(entry),
        }
        accepted_updates.append(payload)
        by_delta_type.setdefault(str(entry.delta_type), []).append(payload)
        if entry.summary:
            recent_summaries.append(
                f"第{entry.chapter_number}章：{_compact_text(entry.summary, limit=180)}"
            )
        state_path = str(raw_update.get("state_path") or raw_update.get("path") or "").strip()
        if state_path:
            facts_by_path.pop(state_path, None)
            facts_by_path[state_path] = _json_safe_value(raw_update.get("value", raw_update))
    return (
        accepted_updates,
        _normalize_facts_by_path(facts_by_path),
        by_delta_type,
        recent_summaries[-_PROJECTION_RECENT_SUMMARY_TAIL:],
    )
