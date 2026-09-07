"""Freshness decisions for expensive chapter quality reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel

from novel_forge.core.utils.text_hash import source_text_hash

ReportRefreshAction = Literal["reuse", "refresh"]
_FRESHNESS_METADATA_KEYS = {
    "source_text_hash",
    "report_context_hash",
    "report_freshness",
    "context_hash",
    "source_context_hash",
}


def _hashable_report_context(value: Any) -> Any:
    if value is None:
        return None
    value_type = type(value)
    if value_type.__module__ == "unittest.mock":
        # Test doubles/proxies claim every attribute (including model_dump),
        # which otherwise creates an infinite chain of fresh mock objects.
        return {"opaque_type": f"{value_type.__module__}.{value_type.__qualname__}"}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping | list | tuple | set | frozenset | str | int | float | bool):
            return _hashable_report_context(dumped)
    if is_dataclass(value) and not isinstance(value, type):
        return _hashable_report_context(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _hashable_report_context(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_hashable_report_context(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_hashable_report_context(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, Enum):
        return _hashable_report_context(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | float | bool):
        return value
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        public = {key: item for key, item in attributes.items() if not str(key).startswith("_")}
        if public:
            return _hashable_report_context(public)
    # Avoid repr strings that embed a process-specific memory address. Domain
    # evidence should be Pydantic/dataclass/mapping-shaped; an opaque object can
    # safely contribute its stable type without forcing a false refresh loop.
    return {"opaque_type": f"{value_type.__module__}.{value_type.__qualname__}"}


def quality_report_evidence_binding(
    *,
    packet: Any,
    bridge: Any,
    plan: Any,
    bundle: Any,
) -> dict[str, Any]:
    """Fingerprint the upstream evidence family shared by hard review modules."""

    components = {
        "packet": _hashable_report_context(packet),
        "bridge": _hashable_report_context(bridge),
        "plan": _hashable_report_context(plan),
        "chapter_outline": _hashable_report_context(getattr(bundle, "chapter_outline", None)),
        "chapter_source_slice": _hashable_report_context(
            getattr(bundle, "chapter_source_slice", None)
        ),
        "canon_state": _hashable_report_context(getattr(bundle, "canon_state", None)),
        "story_bible": _hashable_report_context(getattr(bundle, "story_bible", None)),
        "character_bible": _hashable_report_context(
            getattr(bundle, "character_bible", None)
        ),
        "style_profile": _hashable_report_context(getattr(bundle, "style_profile", None)),
        "editorial_contract": _hashable_report_context(
            getattr(bundle, "editorial_contract", None)
        ),
        "narrative_contract": _hashable_report_context(
            getattr(bundle, "narrative_contract", None)
        ),
        "structure_profile": _hashable_report_context(
            getattr(bundle, "structure_profile", None)
        ),
        "upstream_revision_fingerprint": _hashable_report_context(
            getattr(bundle, "upstream_revision_fingerprint", None)
        ),
    }
    evidence_hashes: dict[str, str] = {}
    for name, value in components.items():
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        evidence_hashes[name] = source_text_hash(encoded)
    context_encoded = json.dumps(evidence_hashes, ensure_ascii=False, sort_keys=True)
    return {
        "schema_version": 2,
        "context_hash": source_text_hash(context_encoded),
        "evidence_hashes": evidence_hashes,
    }


def quality_report_context_hash(
    *,
    packet: Any,
    bridge: Any,
    plan: Any,
    bundle: Any,
) -> str:
    """Return the aggregate upstream evidence hash used for freshness decisions."""

    return str(
        quality_report_evidence_binding(
            packet=packet,
            bridge=bridge,
            plan=plan,
            bundle=bundle,
        )["context_hash"]
    )


def stamp_report_freshness(
    payload: dict[str, Any],
    *,
    current_hash: str,
    context_hash: str,
    evidence_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bind a report payload to both prose and its exact upstream evidence."""

    payload["source_text_hash"] = current_hash
    if context_hash:
        payload["report_context_hash"] = context_hash
        payload["report_freshness"] = {
            "schema_version": 2,
            "source_text_hash": current_hash,
            "context_hash": context_hash,
            "evidence_hashes": dict(evidence_hashes or {}),
        }
    return payload


@dataclass(frozen=True)
class ReportFreshnessDecision:
    """Decision for one report dimension."""

    dimension: str
    action: ReportRefreshAction
    reason: str
    report: Any | None = None
    source: str = ""
    source_text_hash: str = ""
    context_hash: str = ""

    @property
    def should_reuse(self) -> bool:
        return self.action == "reuse" and self.report is not None


class ReportRefreshPlanner:
    """Decide whether a quality report can be reused for current chapter text."""

    def __init__(
        self,
        *,
        storage: Any,
        current_text_hash: str,
        context_hash: str = "",
        artifact_loader: Any | None = None,
    ) -> None:
        self._storage = storage
        self._current_text_hash = str(current_text_hash or "")
        self._context_hash = str(context_hash or "")
        self._artifact_loader = artifact_loader

    def decide(
        self,
        *,
        dimension: str,
        provided: Any | None,
        path: Path | None,
        model: type[BaseModel],
    ) -> ReportFreshnessDecision:
        provided_hash = self._report_hash(provided)
        provided_context_hash = self._report_context_hash(provided)
        provided_context_reason = ""
        if provided is not None and provided_hash == self._current_text_hash:
            if self._context_mismatch(provided_context_hash):
                provided_context_reason = (
                    "missing_report_context_hash"
                    if not provided_context_hash
                    else "stale_context_hash"
                )
            else:
                return ReportFreshnessDecision(
                    dimension=dimension,
                    action="reuse",
                    reason="provided_current",
                    report=provided,
                    source="provided",
                    source_text_hash=provided_hash,
                    context_hash=provided_context_hash or self._context_hash,
                )

        loaded, loaded_payload = self._load_report(path, model)
        loaded_hash = self._report_hash(loaded) or self._report_hash(loaded_payload)
        loaded_context_hash = self._report_context_hash(loaded) or self._report_context_hash(
            loaded_payload
        )
        if loaded is not None and loaded_hash == self._current_text_hash:
            if self._context_mismatch(loaded_context_hash):
                return self._refresh_decision(
                    dimension=dimension,
                    reason=(
                        "missing_report_context_hash"
                        if not loaded_context_hash
                        else "stale_context_hash"
                    ),
                )
            return ReportFreshnessDecision(
                dimension=dimension,
                action="reuse",
                reason="persisted_current",
                report=loaded,
                source="persisted",
                source_text_hash=loaded_hash,
                context_hash=loaded_context_hash or self._context_hash,
            )

        reason = provided_context_reason or self._refresh_reason(
            provided_hash=provided_hash,
            loaded_hash=loaded_hash,
            path=path,
        )
        return self._refresh_decision(dimension=dimension, reason=reason)

    def _refresh_decision(self, *, dimension: str, reason: str) -> ReportFreshnessDecision:
        return ReportFreshnessDecision(
            dimension=dimension,
            action="refresh",
            reason=reason,
            report=None,
            source="planner",
            source_text_hash=self._current_text_hash,
            context_hash=self._context_hash,
        )

    @staticmethod
    def _report_hash(report: Any | None) -> str:
        if report is None:
            return ""
        if isinstance(report, dict):
            return str(report.get("source_text_hash", "") or "").strip()
        return str(getattr(report, "source_text_hash", "") or "").strip()

    @staticmethod
    def _report_context_hash(report: Any | None) -> str:
        if report is None:
            return ""
        payload: dict[str, Any] = {}
        if isinstance(report, dict):
            payload = report
        elif hasattr(report, "model_dump"):
            dumped = report.model_dump(mode="json")
            payload = dumped if isinstance(dumped, dict) else {}
        direct = (
            payload.get("report_context_hash")
            or payload.get("context_hash")
            or payload.get("source_context_hash")
            or ""
        )
        if direct:
            return str(direct).strip()
        freshness = payload.get("report_freshness")
        if isinstance(freshness, dict):
            return str(freshness.get("context_hash", "") or "").strip()
        return str(getattr(report, "report_context_hash", "") or "").strip()

    def _context_mismatch(self, stored_context_hash: str) -> bool:
        # A matching prose hash is insufficient: a report produced from a
        # different (or unknown) plan/bridge/source slice is stale evidence.
        return bool(self._context_hash and stored_context_hash != self._context_hash)

    def _load_report(
        self,
        path: Path | None,
        model: type[BaseModel],
    ) -> tuple[BaseModel | None, dict[str, Any]]:
        if path is None or not path.exists():
            return None, {}
        try:
            payload = (
                self._artifact_loader.load_json(path)
                if self._artifact_loader is not None
                else self._storage.load_json(path)
            )
        except Exception:
            return None, {}
        if not isinstance(payload, dict):
            return None, {}
        try:
            return model.model_validate(self._strip_freshness_metadata(payload, model)), payload
        except Exception:
            return None, payload

    @staticmethod
    def _strip_freshness_metadata(
        payload: dict[str, Any],
        model: type[BaseModel],
    ) -> dict[str, Any]:
        allowed_fields = set(getattr(model, "model_fields", {}) or {})
        return {
            key: value
            for key, value in payload.items()
            if key in allowed_fields or key not in _FRESHNESS_METADATA_KEYS
        }

    def _refresh_reason(
        self,
        *,
        provided_hash: str,
        loaded_hash: str,
        path: Path | None,
    ) -> str:
        if path is None or not path.exists():
            return "missing_report"
        stale_hash = provided_hash or loaded_hash
        if not stale_hash:
            return "missing_source_text_hash"
        if stale_hash != self._current_text_hash:
            return "stale_source_text_hash"
        return "unparseable_report"
