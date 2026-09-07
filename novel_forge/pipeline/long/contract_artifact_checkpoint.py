"""Checkpoint and rollback helpers for runtime contract artifact repair."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContractArtifactCheckpoint:
    """Persist a rollback snapshot for contract-related runtime artifacts."""

    storage: Any
    layout: Any
    chapter_number: int

    @property
    def checkpoint_path(self) -> Path:
        base = getattr(self.layout, "states_dir", None)
        if base is None:
            base = self.layout.plans_dir
        return Path(base) / f"chapter_{self.chapter_number:03d}_contract_artifact_checkpoint.json"

    def save(self) -> None:
        items = []
        for path in self._paths():
            exists = bool(self.storage.exists(path))
            items.append(
                {
                    "path": str(path),
                    "exists": exists,
                    "payload": self.storage.load_json(path) if exists else None,
                }
            )
        self.storage.save_json(
            self.checkpoint_path,
            {
                "chapter_number": self.chapter_number,
                "items": items,
            },
        )

    def rollback(self) -> None:
        if not self.storage.exists(self.checkpoint_path):
            return
        checkpoint = self.storage.load_json(self.checkpoint_path)
        for item in list(checkpoint.get("items") or []):
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("path") or ""))
            if not path:
                continue
            if bool(item.get("exists")):
                payload = item.get("payload")
                if isinstance(payload, dict):
                    self.storage.save_json(path, payload)
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def cleanup(self) -> None:
        try:
            self.checkpoint_path.unlink()
        except FileNotFoundError:
            pass

    def _paths(self) -> list[Path]:
        paths: list[Path] = []
        plans_dir = getattr(self.layout, "plans_dir", None)
        if plans_dir is not None:
            paths.append(Path(plans_dir) / "chapter_contracts.json")
        for attr in ("plot_milestone_index_path", "init_readiness_artifact_path"):
            path = getattr(self.layout, attr, None)
            if path is not None:
                paths.append(Path(path))
        source_artifact_path = getattr(self.layout, "source_artifact_path", None)
        if callable(source_artifact_path):
            paths.append(Path(source_artifact_path("chapter_contract_index")))
        chapter_source_slice_path = getattr(self.layout, "chapter_source_slice_path", None)
        if callable(chapter_source_slice_path):
            paths.append(Path(chapter_source_slice_path(self.chapter_number)))
        seen: set[Path] = set()
        result: list[Path] = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                result.append(path)
        return result


__all__ = ["ContractArtifactCheckpoint"]
