"""Fail-closed admission ledger for optional real repair shadow calls.

The ordinary ``shadow_compare`` command is local and evaluation-only.  This
ledger exists for the more expensive case where a future domain adapter asks
to invoke a real provider in shadow mode.  Admission never starts work and
never changes a RepairCase; JobService remains the only durable dispatcher.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from novel_forge.core.semantic_acceptance import SemanticEvaluatedJudgment

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover - Windows packaging path
    fcntl = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class RepairShadowPolicy:
    enabled: bool = False
    sample_rate: float = 0.05
    max_per_project_30d: int = 10
    budget_usd_30d: float = 0.0
    estimated_call_usd: float = 0.10

    @classmethod
    def from_settings(cls, settings: Any) -> "RepairShadowPolicy":
        return cls(
            enabled=bool(getattr(settings, "repair_real_shadow_enabled", False)),
            sample_rate=min(
                0.05,
                max(0.0, float(getattr(settings, "repair_shadow_sample_rate", 0.05))),
            ),
            max_per_project_30d=min(
                10,
                max(0, int(getattr(settings, "repair_shadow_max_per_project_30d", 10))),
            ),
            budget_usd_30d=max(
                0.0,
                float(getattr(settings, "repair_shadow_budget_usd_30d", 0.0)),
            ),
            estimated_call_usd=max(
                0.000001,
                float(getattr(settings, "repair_shadow_estimated_call_usd", 0.10)),
            ),
        )


@dataclass(frozen=True, slots=True)
class RepairShadowAdmission:
    admitted: bool
    reason: str
    logical_id: str = ""
    sample_value: float = 1.0
    attempts: int = 0
    reserved_usd: float = 0.0


class RepairShadowLedger:
    """Append-only, per-project shadow budget and attempt evidence."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / ".authoring" / "repair_cases"
        self.path = self.root / "shadow_runs.jsonl"
        self.lock_path = self.root / "shadow_runs.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        result: list[dict[str, Any]] = []
        for line_number, raw in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid repair shadow event at line {line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"invalid repair shadow event at line {line_number}")
            result.append(value)
        return result

    def admit(
        self,
        *,
        project_id: str,
        case_id: str,
        candidate_version: int,
        policy: RepairShadowPolicy,
        now: datetime | None = None,
    ) -> RepairShadowAdmission:
        """Reserve at most one real shadow attempt without dispatching it."""

        instant = (now or datetime.now(UTC)).astimezone(UTC)
        logical_id = _logical_shadow_id(project_id, case_id, candidate_version)
        sample_value = _sample_value(logical_id)
        if not policy.enabled:
            return RepairShadowAdmission(False, "disabled", sample_value=sample_value)
        if policy.sample_rate <= 0 or sample_value >= policy.sample_rate:
            return RepairShadowAdmission(False, "not_sampled", sample_value=sample_value)
        if policy.max_per_project_30d <= 0:
            return RepairShadowAdmission(False, "project_cap_zero", sample_value=sample_value)
        if policy.budget_usd_30d <= 0:
            return RepairShadowAdmission(False, "budget_zero", sample_value=sample_value)

        with self._lock():
            events = self.events()
            admitted = [
                event
                for event in events
                if event.get("event") == "admitted"
                and str(event.get("project_id") or "") == project_id
            ]
            if any(str(event.get("logical_id") or "") == logical_id for event in admitted):
                return RepairShadowAdmission(
                    False,
                    "attempt_already_used",
                    logical_id=logical_id,
                    sample_value=sample_value,
                    attempts=1,
                )
            cutoff = instant - timedelta(days=30)
            recent = [event for event in admitted if _event_time(event) >= cutoff]
            if len(recent) >= policy.max_per_project_30d:
                return RepairShadowAdmission(
                    False,
                    "project_30d_cap",
                    logical_id=logical_id,
                    sample_value=sample_value,
                    attempts=len(recent),
                )
            occupied = sum(float(event.get("reserved_usd") or 0.0) for event in recent)
            settlements = {
                str(event.get("logical_id") or ""): float(event.get("actual_usd") or 0.0)
                for event in events
                if event.get("event") == "settled" and _event_time(event) >= cutoff
            }
            for event in recent:
                event_id = str(event.get("logical_id") or "")
                if event_id in settlements:
                    occupied += settlements[event_id] - float(event.get("reserved_usd") or 0.0)
            if occupied + policy.estimated_call_usd > policy.budget_usd_30d:
                return RepairShadowAdmission(
                    False,
                    "budget_30d_exhausted",
                    logical_id=logical_id,
                    sample_value=sample_value,
                    attempts=len(recent),
                )
            admission = RepairShadowAdmission(
                True,
                "admitted",
                logical_id=logical_id,
                sample_value=sample_value,
                attempts=1,
                reserved_usd=policy.estimated_call_usd,
            )
            self._append_unlocked(
                {
                    "event_id": uuid4().hex,
                    "event": "admitted",
                    "occurred_at": instant.isoformat(),
                    "project_id": project_id,
                    "case_id": case_id,
                    "candidate_version": candidate_version,
                    **asdict(admission),
                }
            )
            return admission

    def settle(
        self,
        logical_id: str,
        *,
        actual_usd: float,
        status: str,
        now: datetime | None = None,
    ) -> None:
        """Record cost/outcome only; never mutate a case or publish content."""

        if actual_usd < 0:
            raise ValueError("repair shadow settlement cost cannot be negative")
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock():
            events = self.events()
            admitted = any(
                event.get("event") == "admitted"
                and str(event.get("logical_id") or "") == logical_id
                for event in events
            )
            if not admitted:
                raise ValueError("repair shadow settlement has no admitted attempt")
            if any(
                event.get("event") == "settled"
                and str(event.get("logical_id") or "") == logical_id
                for event in events
            ):
                return
            self._append_unlocked(
                {
                    "event_id": uuid4().hex,
                    "event": "settled",
                    "occurred_at": instant.isoformat(),
                    "logical_id": logical_id,
                    "actual_usd": float(actual_usd),
                    "status": str(status or "unknown"),
                    "shadow_only": True,
                    "published": False,
                    "case_state_changed": False,
                }
            )

    def record_semantic_judgment(
        self,
        logical_id: str,
        *,
        source_hash: str,
        verdict: str,
        structured_success: bool,
        routed_to_human: bool,
        model_call_id: str,
        report_id: str = "",
        legacy_fallback_verdict: str = "not_run",
        now: datetime | None = None,
    ) -> None:
        """Append one model result for an admitted shadow without changing content."""

        self._record_semantic_judgment(
            logical_id,
            source_hash=source_hash,
            verdict=verdict,
            structured_success=structured_success,
            routed_to_human=routed_to_human,
            model_call_id=model_call_id,
            report_id=report_id,
            event_name="semantic_judgment",
            require_admission=True,
            shadow_only=True,
            legacy_fallback_verdict=legacy_fallback_verdict,
            now=now,
        )

    def record_semantic_main_gate_judgment(
        self,
        evaluation_id: str,
        *,
        source_hash: str,
        verdict: str,
        structured_success: bool,
        routed_to_human: bool,
        model_call_id: str,
        report_id: str = "",
        legacy_fallback_verdict: str = "not_run",
        now: datetime | None = None,
    ) -> None:
        """Append post-promotion parallel evidence; this never publishes content."""

        if legacy_fallback_verdict not in {"conflict", "no_conflict", "not_run"}:
            raise ValueError("unsupported legacy fallback verdict")
        self._record_semantic_judgment(
            evaluation_id,
            source_hash=source_hash,
            verdict=verdict,
            structured_success=structured_success,
            routed_to_human=routed_to_human,
            model_call_id=model_call_id,
            report_id=report_id,
            event_name="semantic_main_gate_judgment",
            require_admission=False,
            shadow_only=False,
            legacy_fallback_verdict=legacy_fallback_verdict,
            now=now,
        )

    def _record_semantic_judgment(
        self,
        logical_id: str,
        *,
        source_hash: str,
        verdict: str,
        structured_success: bool,
        routed_to_human: bool,
        model_call_id: str,
        report_id: str,
        event_name: str,
        require_admission: bool,
        shadow_only: bool,
        legacy_fallback_verdict: str = "not_run",
        now: datetime | None = None,
    ) -> None:
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        if legacy_fallback_verdict not in {"conflict", "no_conflict", "not_run"}:
            raise ValueError("unsupported legacy fallback verdict")
        with self._lock():
            events = self.events()
            if require_admission and not any(
                event.get("event") == "admitted"
                and str(event.get("logical_id") or "") == logical_id
                for event in events
            ):
                raise ValueError("semantic shadow judgment has no admitted attempt")
            if any(
                event.get("event") == event_name
                and str(event.get("logical_id") or "") == logical_id
                for event in events
            ):
                return
            allowed = {
                "accept",
                "needs_repair",
                "reject",
                "ambiguous",
                "defer",
                "failed",
            }
            if verdict not in allowed:
                raise ValueError(f"unsupported semantic shadow verdict: {verdict}")
            if structured_success and verdict == "failed":
                raise ValueError("failed semantic shadow cannot be a structured success")
            self._append_unlocked(
                {
                    "event_id": uuid4().hex,
                    "event": event_name,
                    "occurred_at": instant.isoformat(),
                    "logical_id": logical_id,
                    "source_hash": source_hash,
                    "verdict": verdict,
                    "structured_success": bool(structured_success),
                    "routed_to_human": bool(routed_to_human),
                    "model_call_id": model_call_id,
                    "report_id": report_id,
                    "shadow_only": shadow_only,
                    "published": False,
                    "case_state_changed": False,
                    "official_write_count": 0,
                    "stale_approval_publish_count": 0,
                    "legacy_fallback_verdict": legacy_fallback_verdict,
                }
            )

    def review_semantic_judgment(
        self,
        logical_id: str,
        *,
        source_hash: str,
        expected: str,
        critical: bool,
        now: datetime | None = None,
    ) -> None:
        """Bind a human label to the exact admitted source/result once."""

        instant = (now or datetime.now(UTC)).astimezone(UTC)
        if expected not in {"conflict", "compatible", "ambiguous"}:
            raise ValueError(f"unsupported semantic review label: {expected}")
        with self._lock():
            events = self.events()
            judgment = next(
                (
                    event
                    for event in events
                    if event.get("event")
                    in {"semantic_judgment", "semantic_main_gate_judgment"}
                    and str(event.get("logical_id") or "") == logical_id
                ),
                None,
            )
            if judgment is None:
                raise ValueError("semantic shadow review has no model judgment")
            if str(judgment.get("source_hash") or "") != source_hash:
                raise ValueError("semantic shadow source changed before review")
            if any(
                event.get("event") == "semantic_review"
                and str(event.get("logical_id") or "") == logical_id
                for event in events
            ):
                return
            self._append_unlocked(
                {
                    "event_id": uuid4().hex,
                    "event": "semantic_review",
                    "occurred_at": instant.isoformat(),
                    "logical_id": logical_id,
                    "source_hash": source_hash,
                    "expected": expected,
                    "critical": bool(critical),
                    "reviewed": True,
                }
            )

    def semantic_evaluations(self) -> list[SemanticEvaluatedJudgment]:
        """Return reviewed real-shadow judgments from the existing event ledger."""

        events = self.events()
        judgments = {
            str(event.get("logical_id") or ""): event
            for event in events
            if event.get("event") in {"semantic_judgment", "semantic_main_gate_judgment"}
        }
        result: list[SemanticEvaluatedJudgment] = []
        for event in events:
            if event.get("event") != "semantic_review":
                continue
            logical_id = str(event.get("logical_id") or "")
            judgment = judgments.get(logical_id)
            if judgment is None:
                raise ValueError("semantic review references a missing judgment")
            if str(event.get("source_hash") or "") != str(
                judgment.get("source_hash") or ""
            ):
                raise ValueError("semantic review source does not match its judgment")
            result.append(
                SemanticEvaluatedJudgment(
                    evaluation_id=logical_id,
                    origin=(
                        "main_gate"
                        if judgment.get("event") == "semantic_main_gate_judgment"
                        else "real_shadow"
                    ),
                    source_hash=str(judgment.get("source_hash") or ""),
                    expected=str(event.get("expected") or ""),
                    critical=bool(event.get("critical")),
                    verdict=str(judgment.get("verdict") or "failed"),
                    structured_success=bool(judgment.get("structured_success")),
                    routed_to_human=bool(judgment.get("routed_to_human")),
                    model_call_id=str(judgment.get("model_call_id") or ""),
                    official_write_count=int(judgment.get("official_write_count") or 0),
                    stale_approval_publish_count=int(
                        judgment.get("stale_approval_publish_count") or 0
                    ),
                    legacy_fallback_verdict=str(
                        judgment.get("legacy_fallback_verdict") or "not_run"
                    ),
                    reviewed=True,
                )
            )
        return result

    def _append_unlocked(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())


def _logical_shadow_id(project_id: str, case_id: str, candidate_version: int) -> str:
    value = f"{project_id}:{case_id}:{candidate_version}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sample_value(logical_id: str) -> float:
    return int(logical_id[:16], 16) / float(16**16)


def _event_time(event: dict[str, Any]) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(event.get("occurred_at") or ""))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["RepairShadowAdmission", "RepairShadowLedger", "RepairShadowPolicy"]
