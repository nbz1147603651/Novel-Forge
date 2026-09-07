"""Append-only storage for non-canon repair cases and candidate blobs."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover - exercised by Windows packaging tests
    fcntl = None  # type: ignore[assignment]

from novel_forge.core.schemas.audit import ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairCandidate,
    RepairCase,
    RepairCaseEvent,
    RepairCaseEventType,
    RepairPublishReceipt,
    RepairVerificationBundle,
)
from novel_forge.persistence.filesystem import atomic_write_json


class RepairCaseConflictError(ValueError):
    """Raised when a case or candidate CAS precondition is stale."""


class RepairCaseStore:
    """Persist repair evidence below ``.authoring/repair_cases``.

    The JSONL event log is authoritative.  Per-case JSON files are disposable
    projections and are rebuilt from the log whenever they are missing.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.root = self.project_root / ".authoring" / "repair_cases"
        self.events_path = self.root / "events.jsonl"
        self.cases_dir = self.root / "cases"
        self.blobs_dir = self.root / "blobs"
        self.lock_path = self.root / "events.lock"

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def create_case(self, case: RepairCase, *, actor: str = "system") -> RepairCase:
        """Create a case and its first event without mutating project content."""

        if case.version != 1 or case.event_seq != 0:
            raise ValueError("new repair case must start at version=1 and event_seq=0")
        with self._write_lock():
            existing = self._current_case_unlocked(case.case_id)
            if existing is not None:
                comparable = existing.model_copy(update={"event_seq": 0})
                if comparable.model_dump(mode="json") == case.model_dump(mode="json"):
                    return existing
                raise RepairCaseConflictError(f"repair case already exists: {case.case_id}")
            seq = self._next_seq_unlocked()
            event = RepairCaseEvent(
                event_id=uuid4().hex,
                seq=seq,
                case_id=case.case_id,
                case_version=1,
                event_type="case_created",
                actor=actor,
                data={"case": case.model_dump(mode="json")},
            )
            projected = case.model_copy(update={"event_seq": seq})
            self._append_event_unlocked(event)
            self._write_projection_unlocked(projected)
            return projected

    def append_event(
        self,
        case_id: str,
        event_type: RepairCaseEventType,
        data: dict[str, Any],
        *,
        expected_version: int,
        actor: str = "system",
    ) -> RepairCase:
        """Append one CAS-guarded event and refresh its case projection."""

        with self._write_lock():
            current = self._current_case_unlocked(case_id)
            if current is None:
                raise FileNotFoundError(f"repair case not found: {case_id}")
            if current.version != expected_version:
                raise RepairCaseConflictError(
                    f"repair case version changed: expected={expected_version}, "
                    f"current={current.version}"
                )
            seq = self._next_seq_unlocked()
            event = RepairCaseEvent(
                event_id=uuid4().hex,
                seq=seq,
                case_id=case_id,
                case_version=current.version + 1,
                event_type=event_type,
                actor=actor,
                data=dict(data),
            )
            projected = _project_repair_event(current, event)
            self._append_event_unlocked(event)
            self._write_projection_unlocked(projected)
            return projected

    def load_case(self, case_id: str) -> RepairCase | None:
        with self._write_lock():
            return self._current_case_unlocked(case_id)

    def list_cases(self) -> list[RepairCase]:
        with self._write_lock():
            grouped: dict[str, list[RepairCaseEvent]] = {}
            for event in self.events():
                grouped.setdefault(event.case_id, []).append(event)
            cases: list[RepairCase] = []
            for case_id, case_events in grouped.items():
                case = self._current_case_unlocked(case_id, case_events=case_events)
                if case is not None:
                    cases.append(case)
            return sorted(cases, key=lambda item: (item.event_seq, item.case_id), reverse=True)

    def events(self, *, case_id: str | None = None) -> list[RepairCaseEvent]:
        if not self.events_path.exists():
            return []
        events: list[RepairCaseEvent] = []
        previous_seq = 0
        for line_number, raw in enumerate(
            self.events_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            try:
                event = RepairCaseEvent.model_validate_json(raw)
            except Exception as exc:
                raise ValueError(f"invalid repair event at line {line_number}") from exc
            if event.seq <= previous_seq:
                raise ValueError(f"non-monotonic repair event at line {line_number}")
            previous_seq = event.seq
            if case_id is None or event.case_id == case_id:
                events.append(event)
        return events

    def put_blob(self, payload: Any, *, kind: str = "repair_candidate") -> str:
        """Persist one content-addressed local payload and return its digest."""

        envelope = _blob_envelope(payload, kind=kind)
        encoded = canonical_repair_json(envelope)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        path = self.blobs_dir / f"{digest}.json"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if canonical_repair_json(json.loads(existing)) != encoded:
                raise RepairCaseConflictError(f"repair blob hash collision: {digest}")
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, envelope)
        return digest

    def get_blob(self, digest: str) -> Any:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid repair blob digest")
        path = self.blobs_dir / f"{digest}.json"
        if not path.exists():
            raise FileNotFoundError(f"repair blob not found: {digest}")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if hashlib.sha256(canonical_repair_json(envelope).encode("utf-8")).hexdigest() != digest:
            raise ValueError(f"repair blob digest mismatch: {digest}")
        return envelope.get("value")

    def _case_path(self, case_id: str) -> Path:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not case_id or any(char not in allowed for char in case_id):
            raise ValueError("invalid repair case id")
        return self.cases_dir / f"{case_id}.json"

    def _load_case_unlocked(self, case_id: str) -> RepairCase | None:
        path = self._case_path(case_id)
        if not path.exists():
            return None
        return RepairCase.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _current_case_unlocked(
        self,
        case_id: str,
        *,
        case_events: list[RepairCaseEvent] | None = None,
    ) -> RepairCase | None:
        """Return a projection reconciled with the authoritative event tail."""

        projection = self._load_case_unlocked(case_id)
        case_events = case_events if case_events is not None else self.events(case_id=case_id)
        if not case_events:
            return projection
        tail = case_events[-1]
        if (
            projection is None
            or projection.event_seq != tail.seq
            or projection.version != tail.case_version
        ):
            return self._rebuild_case_unlocked(case_id)
        return projection

    def _write_projection_unlocked(self, case: RepairCase) -> None:
        path = self._case_path(case.case_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, case.model_dump(mode="json"))

    def _append_event_unlocked(self, event: RepairCaseEvent) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as event_file:
            event_file.write(event.model_dump_json() + "\n")
            event_file.flush()
            os.fsync(event_file.fileno())

    def _next_seq_unlocked(self) -> int:
        events = self.events()
        return events[-1].seq + 1 if events else 1

    def _rebuild_case_unlocked(self, case_id: str) -> RepairCase | None:
        case: RepairCase | None = None
        for event in self.events(case_id=case_id):
            if event.event_type == "case_created":
                case = RepairCase.model_validate(event.data.get("case"))
                case = case.model_copy(update={"event_seq": event.seq})
                continue
            if case is None:
                raise ValueError(f"repair event precedes case creation: {case_id}")
            case = _project_repair_event(case, event)
        if case is not None:
            self._write_projection_unlocked(case)
        return case

    def _rebuild_all(self) -> list[RepairCase]:
        case_ids = list(dict.fromkeys(event.case_id for event in self.events()))
        cases = [case for case_id in case_ids if (case := self._rebuild_case_unlocked(case_id))]
        return sorted(cases, key=lambda item: (item.event_seq, item.case_id), reverse=True)


def _project_repair_event(case: RepairCase, event: RepairCaseEvent) -> RepairCase:
    if event.case_version != case.version + 1:
        raise RepairCaseConflictError(
            f"non-monotonic repair case version: {event.case_version} after {case.version}"
        )
    updates: dict[str, Any] = {
        "version": event.case_version,
        "event_seq": event.seq,
        "failure_reason": "",
    }
    data = event.data
    if event.event_type == "targets_resolved":
        targets = [ResolvedRepairTarget.model_validate(item) for item in data.get("targets", [])]
        unresolved = not targets or any(item.resolution_status != "resolved" for item in targets)
        updates.update(
            targets=targets,
            status="manual_required" if unresolved else "located",
            failure_reason=(
                str(data.get("reason") or "repair targets are not precisely resolved")
                if unresolved
                else ""
            ),
        )
    elif event.event_type in {"candidate_built", "candidate_edited"}:
        candidate = RepairCandidate.model_validate(data.get("candidate"))
        expected_version = len(case.candidates) + 1
        if candidate.case_id != case.case_id or candidate.version != expected_version:
            raise RepairCaseConflictError("repair candidate version is not the next case version")
        updates.update(
            candidates=[*case.candidates, candidate],
            verification=None,
            receipt=None,
            proposal_id="",
            status=(
                "needs_verification"
                if event.event_type == "candidate_edited"
                else "candidate_ready"
            ),
        )
    elif event.event_type == "verification_requested":
        if case.latest_candidate is None or case.status != "candidate_ready":
            raise RepairCaseConflictError("only a ready candidate may enter verification")
        updates["status"] = "needs_verification"
    elif event.event_type == "verification_completed":
        verification = RepairVerificationBundle.model_validate(data.get("verification"))
        latest = case.latest_candidate
        if latest is None or case.status != "needs_verification":
            raise RepairCaseConflictError(
                "cannot complete verification unless the latest candidate is awaiting it"
            )
        if (
            verification.case_id != case.case_id
            or verification.candidate_version != latest.version
            or verification.candidate_hash != latest.candidate_hash
        ):
            raise RepairCaseConflictError("repair verification does not match latest candidate")
        updates.update(
            verification=verification,
            status=("verified" if verification.passed else "failed"),
            failure_reason=""
            if verification.passed
            else str(data.get("reason") or "verification_failed"),
        )
    elif event.event_type == "approval_requested":
        proposal_id = str(data.get("proposal_id") or "")
        if case.status not in {"verified", "awaiting_approval"} or case.verification is None:
            raise RepairCaseConflictError("only a verified repair case may request approval")
        if case.proposal_id and proposal_id != case.proposal_id:
            raise RepairCaseConflictError("repair case is already bound to another proposal")
        updates.update(
            status="awaiting_approval",
            proposal_id=proposal_id,
        )
    elif event.event_type == "decision_recorded":
        decision = str(data.get("decision") or "")
        if decision not in {"reject", "defer"}:
            raise ValueError("repair decision must be reject or defer")
        updates.update(
            status="rejected" if decision == "reject" else "deferred",
            failure_reason=str(data.get("reason") or decision),
        )
    elif event.event_type == "semantic_decision_recorded":
        decision = str(data.get("decision") or "")
        if decision not in {"accept_compatible", "authoritative_claims"}:
            raise ValueError("unsupported semantic repair decision")
        source_hash = str(data.get("source_hash") or "")
        claim_ids = sorted(str(item) for item in data.get("claim_ids", []) if str(item))
        expected_claim_ids = sorted(
            str(item) for item in case.metadata.get("claim_ids", []) if str(item)
        )
        if source_hash != case.source_hash or claim_ids != expected_claim_ids:
            raise RepairCaseConflictError("semantic decision identity differs from repair source")
        authoritative = sorted(
            str(item) for item in data.get("authoritative_claim_ids", []) if str(item)
        )
        if decision == "authoritative_claims" and (
            not authoritative or not set(authoritative).issubset(claim_ids)
        ):
            raise RepairCaseConflictError("authoritative claims do not belong to this case")
        metadata = dict(case.metadata)
        metadata["semantic_decision"] = {
            "decision": decision,
            "source_hash": source_hash,
            "claim_ids": claim_ids,
            "authoritative_claim_ids": authoritative,
            "reason": str(data.get("reason") or ""),
        }
        updates.update(
            metadata=metadata,
            status="resolved" if decision == "accept_compatible" else "located",
            failure_reason="",
        )
    elif event.event_type == "manual_required":
        updates.update(
            status="manual_required",
            failure_reason=str(data.get("reason") or "manual_review_required"),
        )
    elif event.event_type == "case_stale":
        updates.update(
            status="stale",
            failure_reason=str(data.get("reason") or "repair_source_changed"),
        )
    elif event.event_type == "publication_prepared":
        receipt = RepairPublishReceipt.model_validate(data.get("receipt"))
        latest = case.latest_candidate
        if case.status not in {"verified", "awaiting_approval"} or latest is None:
            raise RepairCaseConflictError("repair case is not ready for publication")
        if receipt.case_id != case.case_id or receipt.candidate_version != latest.version:
            raise RepairCaseConflictError("prepared receipt does not match latest candidate")
        if receipt.transaction_status != "prepared" or receipt.committed:
            raise RepairCaseConflictError("publication intent must be prepared and uncommitted")
        updates["receipt"] = receipt
    elif event.event_type == "publication_committed":
        receipt = RepairPublishReceipt.model_validate(data.get("receipt"))
        latest = case.latest_candidate
        if case.status not in {"verified", "awaiting_approval"} or latest is None:
            raise RepairCaseConflictError("repair case is not publishable")
        prepared = case.receipt
        if prepared is None or prepared.transaction_status != "prepared":
            raise RepairCaseConflictError("repair publication has no prepared intent")
        if receipt.case_id != case.case_id or receipt.candidate_version != latest.version:
            raise RepairCaseConflictError("repair receipt does not match latest candidate")
        if (
            receipt.receipt_id != prepared.receipt_id
            or receipt.target != prepared.target
            or receipt.before_hash != prepared.before_hash
            or receipt.after_hash != prepared.after_hash
        ):
            raise RepairCaseConflictError("committed receipt identity differs from prepared intent")
        if not receipt.committed or receipt.after_hash != latest.candidate_hash:
            raise RepairCaseConflictError(
                "repair publication receipt is not committed candidate evidence"
            )
        updates.update(status="published", receipt=receipt)
    elif event.event_type == "recovery_reconciled":
        receipt = RepairPublishReceipt.model_validate(data.get("receipt"))
        previous = case.receipt
        if previous is None or (
            receipt.receipt_id != previous.receipt_id
            or receipt.case_id != previous.case_id
            or receipt.candidate_version != previous.candidate_version
            or receipt.target != previous.target
            or receipt.before_hash != previous.before_hash
            or receipt.after_hash != previous.after_hash
        ):
            raise RepairCaseConflictError("recovery receipt identity changed")
        if not receipt.committed or not receipt.recovered:
            raise RepairCaseConflictError("repair recovery requires reconciled committed receipt")
        updates.update(status="published", receipt=receipt)
    elif event.event_type == "case_failed":
        updates.update(status="failed", failure_reason=str(data.get("reason") or "repair_failed"))
    else:
        raise ValueError(f"unsupported repair event projection: {event.event_type}")
    return case.model_copy(update=updates)


def _blob_envelope(payload: Any, *, kind: str) -> dict[str, Any]:
    if not kind or not kind.startswith("repair_"):
        raise ValueError("repair blob kind must use the repair_ namespace")
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return {"kind": kind, "value": payload}


def canonical_repair_json(value: Any) -> str:
    """Return the stable JSON representation used by repair evidence hashes."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def repair_content_hash(value: Any) -> str:
    """Hash exact text bytes or a canonical structured value."""

    if isinstance(value, str):
        encoded = value.encode("utf-8")
    elif isinstance(value, bytes):
        encoded = value
    else:
        encoded = canonical_repair_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "RepairCaseConflictError",
    "RepairCaseStore",
    "canonical_repair_json",
    "repair_content_hash",
]
