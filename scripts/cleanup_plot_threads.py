#!/usr/bin/env python3
"""One-time cleanup: deduplicate plot threads and normalize statuses in canon_current.json.

Usage:
    python scripts/cleanup_plot_threads.py data/浮京一梦3/canon/canon_current.json

Creates a backup at <path>.bak before writing changes.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

# ── Status normalization map (mirrors PlotThreadNormalizer._STATUS_MAP) ──────

_STATUS_MAP: dict[str, str] = {
    "激活": "active",
    "开启": "active",
    "新启": "active",
    "介入": "active",
    "initiated": "active",
    "activated": "active",
    "推进": "advancing",
    "推进中": "advancing",
    "进展": "advancing",
    "advanced": "advancing",
    "升级": "escalated",
    "恶化": "escalated",
    "critical": "escalated",
    "解决": "resolved",
    "已解": "resolved",
    "暂时退却": "dormant",
    "伏笔": "planted",
    "深化": "advancing",
    "扩展": "advancing",
    "关键揭示": "revealed",
    "转折点": "advancing",
    "钩子强化": "active",
    "evolving": "advancing",
    "complicated": "escalated",
    "suspicious": "active",
    "uncertain": "active",
}

_CANONICAL_STATUSES = frozenset({
    "active", "resolved", "dormant", "escalated",
    "advancing", "planted", "revealed",
})

_THREAD_SUFFIX_RE = re.compile(r"(线|索|案|之谜|谜团|阴谋|真相|伏笔|主线)$")


def normalize_status(raw: str) -> str:
    s = raw.strip()
    if not s:
        return "active"
    low = s.lower()
    if low in _CANONICAL_STATUSES:
        return low
    mapped = _STATUS_MAP.get(s) or _STATUS_MAP.get(low)
    if mapped:
        return mapped
    for key, val in _STATUS_MAP.items():
        if key in s:
            return val
    return "active"


def normalize_thread_id(tid: str) -> str:
    return _THREAD_SUFFIX_RE.sub("", tid).strip()


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <canon_current.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    threads: dict[str, dict] = data.get("plot_threads", {})
    if not threads:
        print("No plot_threads found.")
        return

    print(f"Original thread count: {len(threads)}")

    # ── Phase 1: Status normalization ────────────────────────────────────
    status_changes = 0
    for tid, t in threads.items():
        old_status = t.get("status", "active")
        new_status = normalize_status(old_status)
        if new_status != old_status:
            t["status"] = new_status
            status_changes += 1
            print(f"  status: {tid}: {old_status!r} → {new_status!r}")
    print(f"Status normalized: {status_changes} changes")

    # ── Phase 2: Dedup by suffix-stripped thread ID ──────────────────────
    # Group by normalized ID
    groups: dict[str, list[str]] = {}
    for tid in list(threads.keys()):
        norm = normalize_thread_id(tid)
        groups.setdefault(norm, []).append(tid)

    merged_count = 0
    for _, ids in groups.items():
        if len(ids) <= 1:
            continue
        # Keep the one with highest last_touched_chapter
        ids.sort(key=lambda t: threads[t].get("last_touched_chapter", 0), reverse=True)
        winner = ids[0]
        for loser in ids[1:]:
            lt = threads[loser]
            wt = threads[winner]
            # Merge summary
            if lt.get("summary") and lt["summary"] not in (wt.get("summary") or ""):
                wt["summary"] = (wt.get("summary") or "") + " " + lt["summary"]
            # Merge owners
            existing_owners = set(wt.get("owners", []))
            for o in lt.get("owners", []):
                if o not in existing_owners:
                    wt.setdefault("owners", []).append(o)
            del threads[loser]
            merged_count += 1
            print(f"  dedup: {loser!r} merged into {winner!r}")
    print(f"Deduplication: {merged_count} threads merged")

    # ── Phase 3: Write ───────────────────────────────────────────────────
    data["plot_threads"] = threads
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    print(f"Backup saved: {backup}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Final thread count: {len(threads)}")
    print("Done.")


if __name__ == "__main__":
    main()
