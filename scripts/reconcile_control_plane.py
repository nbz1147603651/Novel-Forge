#!/usr/bin/env python3
"""Reconcile control-plane records with task_flow_history.json.

Compares the terminal job records in per-project ``task_flow_history.json``
files with the WorkUnit/RunAttempt records in ``runtime_control.db``.

Modes:
  --check    Report inconsistencies only (exit 1 if found). For CI.
  --apply    Patch missing control-plane records from history files.
  --stages   Also compare stage artifacts (Phase 2, forward-compatible).

Usage:
  python scripts/reconcile_control_plane.py --check
  python scripts/reconcile_control_plane.py --apply
  python scripts/reconcile_control_plane.py --check --stages
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.app_service.contracts import JobRecord, JobState  # noqa: E402
from novel_forge.control_plane.enums import (  # noqa: E402
    Priority,
    RunAttemptState,
    WorkUnitState,
)
from novel_forge.control_plane.factory import (  # noqa: E402
    get_control_plane_store,
    resolve_db_path,
)
from novel_forge.control_plane.schemas import (  # noqa: E402
    RunAttemptDTO,
    WorkUnitDTO,
    utc_now_iso,
)
from novel_forge.core.config import get_settings  # noqa: E402
from novel_forge.persistence.models import ProjectLayout  # noqa: E402

_JOB_HISTORY_FILENAME = "task_flow_history.json"

# JobState -> WorkUnitState
_STATE_MAP: dict[str, WorkUnitState] = {
    JobState.SUCCEEDED.value: WorkUnitState.COMMITTED,
    JobState.FAILED.value: WorkUnitState.FAILED,
    JobState.PAUSED.value: WorkUnitState.WAITING_HUMAN,
}


def _read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def _list_projects(storage_root: Path) -> list[Path]:
    if not storage_root.exists():
        return []
    return [
        item
        for item in storage_root.iterdir()
        if item.is_dir()
        and not item.name.startswith(".")
        and not item.name.startswith("_")
        and item.name.casefold() != "dlq"
    ]


def _kind_str(record: JobRecord) -> str:
    return record.kind.value if hasattr(record.kind, "value") else str(record.kind)


def _priority_for_kind(kind: str) -> Priority:
    from novel_forge.control_plane.shadow import _priority_for_kind as _pf

    return _pf(kind)


def reconcile(
    storage_root: Path,
    store: Any,
    *,
    apply: bool = False,
) -> list[str]:
    """Reconcile history JSON with control plane. Returns list of issues."""
    issues: list[str] = []
    patched = 0

    for project_dir in _list_projects(storage_root):
        history_path = ProjectLayout(project_dir).states_dir / _JOB_HISTORY_FILENAME
        history_records = _read_history(history_path)

        for item in history_records:
            record = JobRecord.from_history_payload(item)
            if record is None:
                continue
            if record.status not in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}:
                continue
            if not record.project_id:
                continue

            kind = _kind_str(record)
            from novel_forge.control_plane.shadow import _is_write_kind

            if not _is_write_kind(kind):
                continue

            # Check if control plane has this work unit
            wu = store.sync.get_work_unit(record.job_id)
            expected_state = _STATE_MAP.get(record.status.value)

            if wu is None:
                if apply:
                    # Patch: create the work unit from history
                    now = utc_now_iso()
                    new_wu = WorkUnitDTO(
                        id=record.job_id,
                        kind=kind,
                        project_id=record.project_id,
                        priority=_priority_for_kind(kind),
                        state=expected_state or WorkUnitState.FAILED,
                        label=record.label,
                        created_at=record.created_at,
                        updated_at=record.updated_at or now,
                    )
                    store.sync.create_work_unit(new_wu)
                    new_attempt = RunAttemptDTO(
                        id=f"{record.job_id}_a1",
                        work_unit_id=record.job_id,
                        attempt_number=1,
                        state=_attempt_state_for(expected_state),
                        error_kind="",
                        error_summary=record.error_summary,
                        started_at=record.created_at,
                        ended_at=record.updated_at or now,
                    )
                    store.sync.create_run_attempt(new_attempt)
                    patched += 1
                else:
                    issues.append(
                        f"  MISSING: job {record.job_id} ({kind}) "
                        f"project={record.project_id} status={record.status.value} "
                        f"exists in history but not in control plane"
                    )
            elif wu.state != expected_state:
                issues.append(
                    f"  MISMATCH: job {record.job_id} ({kind}) "
                    f"history={record.status.value} -> expected {expected_state.value}, "
                    f"control_plane={wu.state.value}"
                )

    if apply and patched > 0:
        print(f"Patched {patched} missing control-plane records from history.")

    return issues


def _attempt_state_for(wu_state: WorkUnitState | None) -> RunAttemptState:
    """Map WorkUnit terminal state to RunAttempt terminal state."""
    if wu_state == WorkUnitState.COMMITTED:
        return RunAttemptState.COMMITTED
    if wu_state == WorkUnitState.FAILED:
        return RunAttemptState.FAILED
    if wu_state == WorkUnitState.WAITING_HUMAN:
        return RunAttemptState.WAITING_HUMAN
    return RunAttemptState.FAILED


def reconcile_stages(storage_root: Path, store: Any) -> list[str]:
    """Compare stage artifacts (Phase 2 forward-compatible). Stub for now."""
    # Phase 2 will implement: compare states/chapter_NNN_artifacts/*.json
    # with StageExecution records in the control plane.
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile control-plane records with task_flow_history.json"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report inconsistencies only (exit 1 if found). For CI.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Patch missing control-plane records from history files.",
    )
    parser.add_argument(
        "--stages",
        action="store_true",
        help="Also compare stage artifacts (Phase 2).",
    )
    args = parser.parse_args(argv)

    if not args.check and not args.apply:
        args.check = True  # Default mode

    settings = get_settings()
    if not settings.runtime_control_enabled:
        print("Control plane is disabled (runtime_control_enabled=False). Nothing to reconcile.")
        return 0

    store = get_control_plane_store(settings)
    if store is None:
        print("Control plane store could not be initialized. Nothing to reconcile.")
        return 0

    storage_root = Path(settings.storage_root).expanduser()
    db_path = resolve_db_path(settings)

    print(f"Storage root: {storage_root}")
    print(f"Control plane DB: {db_path}")
    print(f"Mode: {'apply' if args.apply else 'check'}")
    print()

    issues = reconcile(storage_root, store, apply=args.apply)

    if args.stages:
        issues.extend(reconcile_stages(storage_root, store))

    if issues:
        print(f"Found {len(issues)} issue(s):")
        for issue in issues:
            print(issue)
        return 1

    print("RESULT: PASS - control plane records are consistent with history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
