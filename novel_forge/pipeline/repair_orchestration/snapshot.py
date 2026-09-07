"""Snapshot storage for Repair Orchestration v2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.models import RepairSnapshot


@dataclass
class RepairSnapshotStore:
    """Project-aware snapshot store used by the v2 orchestrator."""

    project_id: str | None = None
    root: Path | None = None
    _snapshots: dict[str, RepairSnapshot] = field(default_factory=dict)

    def _snapshot_path(self, snapshot_id: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / f"{snapshot_id}.json"

    def create(
        self,
        *,
        target_id: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> RepairSnapshot:
        snapshot = RepairSnapshot(
            target_id=target_id,
            payload=dict(payload),
            metadata=dict(metadata or {}),
        )
        if self.project_id and "project_id" not in snapshot.metadata:
            snapshot.metadata["project_id"] = self.project_id
        self._snapshots[snapshot.snapshot_id] = snapshot
        path = self._snapshot_path(snapshot.snapshot_id)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        return snapshot

    def get(self, snapshot_id: str) -> RepairSnapshot | None:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is not None:
            return snapshot
        path = self._snapshot_path(snapshot_id)
        if path is None or not path.exists():
            return None
        snapshot = RepairSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot
