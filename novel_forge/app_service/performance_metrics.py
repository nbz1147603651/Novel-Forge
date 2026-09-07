"""Lightweight per-job performance metrics for app-service executions."""

from __future__ import annotations

import time
from collections import Counter
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

_REVIEW_STEPS = (
    "check",
    "quality",
    "alignment",
    "continuity",
    "causal",
    "reading_power",
    "guard",
    "evaluate",
)
_GENERATE_STEPS = ("draft", "wave", "generate")
_PLANNING_STEPS = ("plan", "bridge", "context", "preflight", "state_packet")
_POLISH_STEPS = ("polish",)
_HUMANIZE_STEPS = ("humanize", "ai_flavor")
_FINALIZE_STEPS = ("persist", "archive", "canon", "memory", "finalize", "export")


def _phase_for_step(step: str) -> str:
    normalized = str(step or "").lower()
    if any(marker in normalized for marker in _FINALIZE_STEPS):
        return "finalize"
    if any(marker in normalized for marker in _HUMANIZE_STEPS):
        return "humanize"
    if any(marker in normalized for marker in _POLISH_STEPS):
        return "polish"
    if "repair" in normalized:
        return "repair"
    if any(marker in normalized for marker in _REVIEW_STEPS):
        return "review"
    if any(marker in normalized for marker in _GENERATE_STEPS):
        return "generate"
    if any(marker in normalized for marker in _PLANNING_STEPS):
        return "planning"
    return "other"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class RunPerformanceMetrics:
    """Accumulates cheap, internal performance counters for one job run."""

    started_at: float = field(default_factory=time.monotonic)
    _last_step_at: float = field(default_factory=time.monotonic)
    phase_timings_ms: dict[str, float] = field(default_factory=dict)
    step_counts: Counter[str] = field(default_factory=Counter)
    llm_calls: Counter[str] = field(default_factory=Counter)
    tokens: Counter[str] = field(default_factory=Counter)
    storage_reads: Counter[str] = field(default_factory=Counter)
    storage_writes: Counter[str] = field(default_factory=Counter)
    report_refreshes: Counter[str] = field(default_factory=Counter)
    semantic_mutations: Counter[str] = field(default_factory=Counter)
    intent_conflicts: Counter[str] = field(default_factory=Counter)
    research: Counter[str] = field(default_factory=Counter)
    repair_rounds: int = 0
    short_revision_rounds: int = 0
    final_hash_verifications: int = 0
    cost_usd: float = 0.0

    def observe_step(self, step: str, payload: Any = None) -> None:
        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_step_at) * 1000.0)
        self._last_step_at = now

        phase = _phase_for_step(step)
        self.phase_timings_ms[phase] = self.phase_timings_ms.get(phase, 0.0) + elapsed_ms
        self.step_counts[str(step or "")] += 1

        normalized = str(step or "").lower()
        if "quality_reports_refresh" in normalized:
            self.report_refreshes["requested"] += 1
        elif normalized == "quality_report_reused":
            self.report_refreshes["reused"] += 1
        elif normalized == "quality_report_refresh_required":
            self.report_refreshes["refresh_required"] += 1

        if "repair_round" in normalized or normalized.endswith("_repair"):
            self.repair_rounds += 1

        if normalized == "text_changed_before_archive" and isinstance(payload, dict):
            before_hash = str(payload.get("before_hash") or "")
            after_hash = str(payload.get("after_hash") or "")
            if before_hash and after_hash and before_hash != after_hash:
                reason = str(payload.get("reason") or "unspecified")
                self.semantic_mutations[reason] += 1
        elif normalized == "final_text_hash_verified":
            self.final_hash_verifications += 1
        elif normalized == "short_adaptive_revision_round" and isinstance(payload, dict):
            self.short_revision_rounds = max(
                self.short_revision_rounds,
                int(_number(payload.get("round"))),
            )
        elif normalized == "chapter_research_start" and isinstance(payload, dict):
            self.research["provider_calls"] += 1
            self.research["queries"] += int(_number(payload.get("queries")))
        elif normalized == "chapter_research_cache_hit" and isinstance(payload, dict):
            self.research["cache_hits"] += 1
            self.research["cached_queries"] += int(_number(payload.get("queries")))
        elif normalized == "chapter_research_ready" and isinstance(payload, dict):
            self.research["evidence_cards"] += int(_number(payload.get("cards")))
            self.research["inspiration_cards"] += int(
                _number(payload.get("inspiration_cards"))
            )
            self.research["inspiration_duplicates_omitted"] += int(
                _number(payload.get("inspiration_duplicates_omitted"))
            )

        if isinstance(payload, dict):
            round_value = payload.get("round") or payload.get("round_index")
            if round_value is not None and "repair" in normalized:
                self.repair_rounds = max(self.repair_rounds, int(_number(round_value)))
            conflicts = payload.get("intent_conflicts")
            if isinstance(conflicts, list):
                for conflict in conflicts:
                    field_name = (
                        str(conflict.get("field") or "unspecified")
                        if isinstance(conflict, dict)
                        else "unspecified"
                    )
                    self.intent_conflicts[field_name] += 1
            if normalized == "short_adaptive_diagnosis":
                score = payload.get("intent_score")
                if score is None or _number(score) < 9.0:
                    self.intent_conflicts["short_intent_compliance"] += 1

    def observe_router_event(self, event: str, payload: dict[str, Any]) -> None:
        event_name = str(event or "")
        if event_name.endswith("_start"):
            self.llm_calls["started"] += 1
            return
        if event_name.endswith(("_error", "_timeout", "_incomplete")):
            self.llm_calls["failed"] += 1
        if event_name.endswith("_done"):
            self.llm_calls["succeeded"] += 1

        self.tokens["prompt"] += int(_number(payload.get("prompt_tokens")))
        self.tokens["completion"] += int(_number(payload.get("completion_tokens")))
        self.tokens["total"] += int(_number(payload.get("total_tokens")))
        self.cost_usd += _number(payload.get("cost_usd"))

    def observe_storage_read(self, operation: str) -> None:
        self.storage_reads[str(operation or "read")] += 1

    def observe_storage_write(self, operation: str) -> None:
        self.storage_writes[str(operation or "write")] += 1

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "schema_version": 1,
            "duration_ms": round((now - self.started_at) * 1000.0, 2),
            "phase_timings": {
                key: round(value, 2) for key, value in sorted(self.phase_timings_ms.items())
            },
            "llm_calls": dict(self.llm_calls),
            "tokens": dict(self.tokens),
            "cost_usd": round(self.cost_usd, 6),
            "storage_reads": dict(self.storage_reads),
            "storage_writes": dict(self.storage_writes),
            "report_refreshes": dict(self.report_refreshes),
            "semantic_mutations": dict(self.semantic_mutations),
            "intent_conflicts": dict(self.intent_conflicts),
            "research": dict(self.research),
            "repair_rounds": self.repair_rounds,
            "short_revision_rounds": self.short_revision_rounds,
            "final_hash_verifications": self.final_hash_verifications,
            "step_counts": dict(self.step_counts),
        }


class CountingStorageProxy:
    """Storage proxy that forwards all calls while counting hot operations."""

    def __init__(self, storage: Any, metrics: RunPerformanceMetrics) -> None:
        self._storage = storage
        self._metrics = metrics

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)

    @property
    def root(self) -> Path:
        return cast(Path, self._storage.root)

    def project_path(self, project_id: str) -> Path:
        return cast(Path, self._storage.project_path(project_id))

    def ensure_project_dir(self, project_id: str) -> Path:
        return cast(Path, self._storage.ensure_project_dir(project_id))

    def existing_project_dir(self, project_id: str) -> Path:
        self._metrics.observe_storage_read("existing_project_dir")
        return cast(Path, self._storage.existing_project_dir(project_id))

    def project_dir(self, project_id: str) -> Path:
        return cast(Path, self._storage.project_dir(project_id))

    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        self._metrics.observe_storage_write("save_json")
        self._storage.save_json(path, data)

    def load_json(self, path: Path) -> dict[str, Any]:
        self._metrics.observe_storage_read("load_json")
        return cast(dict[str, Any], self._storage.load_json(path))

    def save_text(self, path: Path, text: str) -> None:
        self._metrics.observe_storage_write("save_text")
        self._storage.save_text(path, text)

    def load_text(self, path: Path) -> str:
        self._metrics.observe_storage_read("load_text")
        return cast(str, self._storage.load_text(path))

    def exists(self, path: Path) -> bool:
        self._metrics.observe_storage_read("exists")
        return bool(self._storage.exists(path))

    def list_dir(self, path: Path) -> list[Path]:
        self._metrics.observe_storage_read("list_dir")
        return cast(list[Path], self._storage.list_dir(path))

    def project_lock(self, project_id: str) -> AbstractContextManager[Any]:
        return cast(AbstractContextManager[Any], self._storage.project_lock(project_id))

    def project_shared_lock(self, project_id: str) -> AbstractContextManager[Any]:
        return cast(AbstractContextManager[Any], self._storage.project_shared_lock(project_id))
