"""Durable scoped cost journal supplementing, never raising, gateway limits."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from novel_forge.persistence.authoring_store import AuthoringDeniedError, AuthoringStore


class AuthoringBudget:
    """Keep settled estimates and uncertain in-flight costs across every resume.

    Reservations use the gateway's existing pricing data. Unknown/failed calls
    keep their reservation, not a fictional refund; no model call is required
    to inspect this journal. Policy changes never reset accumulated costs.
    """

    def __init__(self, root: Path, check: Callable[[], None] | None = None) -> None:
        self.store = AuthoringStore(root)
        self.check = check
        self.path = self.store.directory / "model_costs.sqlite"

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, estimate REAL, cost REAL, status TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _totals(db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='settled' THEN cost ELSE 0 END),0), COALESCE(SUM(CASE WHEN status!='settled' THEN estimate ELSE 0 END),0), SUM(CASE WHEN status!='settled' AND estimate IS NULL THEN 1 ELSE 0 END), COUNT(*) FROM calls"
        ).fetchone()
        return {
            "spent_usd": float(row[0]),
            "reserved_usd": float(row[1]),
            "unknown_calls": int(row[2] or 0),
            "call_count": int(row[3]),
        }

    def totals(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"spent_usd": 0.0, "reserved_usd": 0.0, "unknown_calls": 0, "call_count": 0}
        # UI reads do not initialize/mutate the journal.
        try:
            with closing(
                sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            ) as db:
                return self._totals(db)
        except sqlite3.Error as exc:
            raise AuthoringDeniedError(
                "费用记录不可读，不能假定剩余额度；请检查记录后恢复"
            ) from exc

    def reserve(self, call_id: str, estimate: float | None, metadata: dict[str, Any]) -> None:
        if estimate is not None and (estimate < 0 or not math.isfinite(estimate)):
            raise AuthoringDeniedError("调用费用估算无效，未派发")
        with self.store.lock(), self.connection() as db:
            if self.check is not None:
                self.check()
            policy = self.store.policy()
            if policy is None:
                raise AuthoringDeniedError("授权已移除，未派发")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
                raise AuthoringDeniedError("调用编号已使用，不能再次计费派发")
            totals = self._totals(db)
            if policy.budget_usd is not None:
                if estimate is None or totals["unknown_calls"]:
                    raise AuthoringDeniedError("存在未知费用，无法验证所选预算；未派发新调用")
                if (
                    totals["spent_usd"] + totals["reserved_usd"] + estimate > policy.budget_usd
                    or policy.budget_usd == 0
                ):
                    raise AuthoringDeniedError(
                        "本作品授权预算不足（包含未确认调用占用）；候选保留，未派发"
                    )
            db.execute(
                "INSERT INTO calls VALUES (?, ?, NULL, 'reserved', ?)",
                (
                    call_id,
                    estimate,
                    json.dumps(
                        {
                            **metadata,
                            "policy_version": policy.version,
                            "scope": [policy.start_chapter, policy.end_chapter],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

    def settle(self, call_id: str, cost: float | None) -> None:
        if cost is not None and (cost < 0 or not math.isfinite(cost)):
            cost = None
        with self.connection() as db:
            # Idempotent accounting; pause, code rollback or a rejected proposal
            # cannot erase a paid call. Unknown costs retain their reservation.
            db.execute(
                "UPDATE calls SET cost=?, status=? WHERE id=? AND status!='settled'",
                (cost, "settled" if cost is not None else "unknown", call_id),
            )
