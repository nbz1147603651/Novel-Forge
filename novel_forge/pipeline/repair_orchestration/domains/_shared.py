"""Shared helpers for Repair Orchestration v2 domain adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.ledger import (
    append_repair_ledger_record_for_layout,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairControlMode,
    RepairMission,
    RepairOutcome,
)
from novel_forge.pipeline.repair_orchestration.text_utils import (
    text_change_ratio as _text_change_ratio,
)


def text_change_ratio(before: str, after: str) -> float:
    return _text_change_ratio(before, after)


def resolve_mode(raw_mode: RepairControlMode | str | None, settings: Any | None) -> RepairControlMode:
    value = raw_mode or getattr(settings, "repair_control_mode", "ai_assisted")
    if isinstance(value, RepairControlMode):
        return value
    return RepairControlMode(str(value))


def fallback_project_id(bundle: Any | None, runner: Any | None = None) -> str:
    return str(
        getattr(bundle, "project_id", "")
        or getattr(getattr(bundle, "layout", None), "project_id", "")
        or getattr(runner, "project_id", "")
        or ""
    )


def repair_layout_from(*sources: Any) -> Any | None:
    for source in sources:
        if source is None:
            continue
        if getattr(source, "states_dir", None) is not None:
            return source
        layout = getattr(source, "layout", None)
        if layout is not None:
            return layout
        prepared = getattr(source, "prepared", None)
        bundle = getattr(prepared, "bundle", None)
        layout = getattr(bundle, "layout", None)
        if layout is not None:
            return layout
    return None


def record_domain_repair_ledger(
    *,
    mission: RepairMission,
    outcome: RepairOutcome,
    sources: tuple[Any, ...],
) -> None:
    layout = repair_layout_from(*sources)
    if layout is None:
        return
    append_repair_ledger_record_for_layout(layout, mission, outcome)


def snapshot_text_path(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "text": path.read_text(encoding="utf-8") if path.exists() else "",
    }


def restore_text_path(snapshot: dict[str, Any]) -> None:
    raw_path = str(snapshot.get("path") or "")
    if not raw_path:
        return
    path = Path(raw_path)
    if bool(snapshot.get("exists")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(snapshot.get("text") or ""), encoding="utf-8")
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
