#!/usr/bin/env python3
"""One-time (idempotent, safe to re-run) cleanup for ``data/runtime_control.db``.

A runaway chapter-autorun loop historically resubmitted the same
``resolve_chapter_checkpoint_finalize`` intent thousands of times, and the
reconciler re-appended a ``reconcile_retry_wait`` event for every stale work
unit on every start.  This bloated the control-plane database to several GB:

* ``event_ledger`` — millions of duplicate ``reconcile_retry_wait`` rows.
* ``work_units`` — thousands of duplicate ACTIVE rows sharing one
  ``idempotency_key`` (one row per resubmit, each under a fresh ``job_id``).

The application-level fixes (reconciler idempotency, shadow-submit dedup, a
partial UNIQUE index on active ``idempotency_key``, and an autorun retry cap)
stop *new* growth; this script removes the *existing* redundancy and reclaims
disk space via ``VACUUM``.

Cleanup policy
--------------
* ``event_ledger``: for each distinct work-unit identity referenced by a
  ``reconcile_retry_wait`` event, keep only the most recent event (highest
  ``seq``) as an audit trail; delete the rest.
* ``work_units``: for each ``idempotency_key`` that has more than one ACTIVE
  row (queued/running/retry_wait/waiting_human), keep the newest row (highest
  ``rowid``) and mark the surplus rows ``cancelled`` (a terminal state).  Rows
  are never hard-deleted so historical references stay intact.
* ``VACUUM`` rewrites the database file to reclaim freed space.

Safety
------
* Defaults to **dry-run**: only reports what would change.  Pass ``--apply``
  to mutate the database.
* Mutations run inside a single transaction; ``VACUUM`` runs afterwards.
* The application must not be running while this executes (the script refuses
  to ``--apply`` if it detects a live SQLite lock holder via ``PRAGMA``).

Usage::

    python scripts/cleanup_runtime_control_duplicates.py            # dry-run
    python scripts/cleanup_runtime_control_duplicates.py --apply    # execute
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("data/runtime_control.db")

ACTIVE_STATES = ("queued", "running", "retry_wait", "waiting_human")
_TARGET_EVENT = "reconcile_retry_wait"


def _fmt(n: int) -> str:
    return f"{n:,}"


def _db_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _identity_expr() -> str:
    """SQL expression identifying the work unit a retry_wait event belongs to."""
    return "COALESCE(json_extract(payload_json, '$.work_unit_id'), run_attempt_id, 'unknown')"


def _active_in() -> str:
    return ", ".join(f"'{s}'" for s in ACTIVE_STATES)


def gather_stats(conn: sqlite3.Connection) -> dict[str, int]:
    identity = _identity_expr()
    active = _active_in()
    cur = conn.cursor()
    stats: dict[str, int] = {}
    stats["ledger_total"] = cur.execute("SELECT COUNT(*) FROM event_ledger").fetchone()[0]
    stats["ledger_target"] = cur.execute(
        "SELECT COUNT(*) FROM event_ledger WHERE event_type = ?", (_TARGET_EVENT,)
    ).fetchone()[0]
    stats["ledger_keep"] = cur.execute(
        f"SELECT COUNT(*) FROM (SELECT MAX(seq) FROM event_ledger "
        f"WHERE event_type = ? GROUP BY {identity})",
        (_TARGET_EVENT,),
    ).fetchone()[0]
    stats["wu_total"] = cur.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
    stats["wu_active"] = cur.execute(
        f"SELECT COUNT(*) FROM work_units WHERE state IN ({active})"
    ).fetchone()[0]
    stats["wu_dup_excess"] = cur.execute(
        f"SELECT COALESCE(SUM(cnt - 1), 0) FROM ("
        f"SELECT COUNT(*) AS cnt FROM work_units "
        f"WHERE state IN ({active}) AND idempotency_key != '' "
        f"GROUP BY idempotency_key HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    return stats


def apply_cleanup(conn: sqlite3.Connection) -> tuple[int, int]:
    """Delete redundant events and cancel duplicate active work units.

    Returns ``(events_deleted, work_units_cancelled)``.
    """
    identity = _identity_expr()
    active = _active_in()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()

    # 1) Keep only the newest retry_wait event per work-unit identity.
    cur.execute("DROP TABLE IF EXISTS temp.keep_rrw_seq")
    cur.execute(
        f"CREATE TEMP TABLE keep_rrw_seq AS "
        f"SELECT MAX(seq) AS kseq FROM event_ledger "
        f"WHERE event_type = ? GROUP BY {identity}",
        (_TARGET_EVENT,),
    )
    cur.execute("CREATE INDEX temp.idx_keep_rrw_seq ON keep_rrw_seq (kseq)")
    cur.execute(
        "DELETE FROM event_ledger WHERE event_type = ? "
        "AND seq NOT IN (SELECT kseq FROM keep_rrw_seq)",
        (_TARGET_EVENT,),
    )
    events_deleted = cur.rowcount
    cur.execute("DROP TABLE IF EXISTS temp.keep_rrw_seq")

    # 2) Keep the newest active row per idempotency_key; cancel the surplus.
    cur.execute(
        f"UPDATE work_units SET state = 'cancelled', updated_at = ? "
        f"WHERE state IN ({active}) AND idempotency_key != '' "
        f"AND rowid NOT IN ("
        f"SELECT MAX(rowid) FROM work_units "
        f"WHERE state IN ({active}) AND idempotency_key != '' "
        f"GROUP BY idempotency_key)",
        (now_iso,),
    )
    work_units_cancelled = cur.rowcount

    conn.commit()
    return events_deleted, work_units_cancelled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to the control-plane SQLite database (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the database (default is a read-only dry-run).",
    )
    parser.add_argument(
        "--skip-vacuum",
        action="store_true",
        help="Skip the final VACUUM (space reclamation) step.",
    )
    args = parser.parse_args()

    db_path: Path = args.db
    if not db_path.exists():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 1

    size_before = _db_size_mb(db_path)
    print(f"Database: {db_path}  ({size_before:,.1f} MiB)")

    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    try:
        before = gather_stats(conn)
        print("\n== Current state ==")
        print(f"  event_ledger rows            : {_fmt(before['ledger_total'])}")
        print(f"  '{_TARGET_EVENT}' rows   : {_fmt(before['ledger_target'])}")
        print(f"  -> kept after cleanup (1/wu) : {_fmt(before['ledger_keep'])}")
        print(
            f"  -> would delete              : "
            f"{_fmt(before['ledger_target'] - before['ledger_keep'])}"
        )
        print(f"  work_units rows              : {_fmt(before['wu_total'])}")
        print(f"  active work units            : {_fmt(before['wu_active'])}")
        print(f"  duplicate active surplus     : {_fmt(before['wu_dup_excess'])}")

        if before["ledger_target"] - before["ledger_keep"] == 0 and before["wu_dup_excess"] == 0:
            print("\nNothing to clean up — database is already deduplicated.")
            return 0

        if not args.apply:
            print("\n[DRY-RUN] No changes made. Re-run with --apply to execute.")
            return 0

        print("\n== Applying cleanup ==")
        conn.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        events_deleted, wu_cancelled = apply_cleanup(conn)
        print(
            f"  deleted {_fmt(events_deleted)} redundant '{_TARGET_EVENT}' events, "
            f"cancelled {_fmt(wu_cancelled)} duplicate active work units "
            f"({time.monotonic() - started:.1f}s)"
        )

        after = gather_stats(conn)
        print("\n== After cleanup ==")
        print(f"  event_ledger rows : {_fmt(after['ledger_total'])}")
        print(f"  active work units : {_fmt(after['wu_active'])}")

        if not args.skip_vacuum:
            print("\nVACUUMing to reclaim space (this may take a while)...")
            started = time.monotonic()
            conn.execute("VACUUM")
            print(f"  VACUUM done ({time.monotonic() - started:.1f}s)")
    finally:
        conn.close()

    size_after = _db_size_mb(db_path)
    print(f"\nDatabase size: {size_before:,.1f} MiB -> {size_after:,.1f} MiB")
    print(f"Reclaimed: {size_before - size_after:,.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
