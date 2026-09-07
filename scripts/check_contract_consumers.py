"""Trigger consumer-layer validation when registered boundary paths change.

The repository declares two explicit core/contract boundaries (see
"Core & Contract Boundaries" in AGENTS.md):

- contract chain: ``packages/engine-contracts`` <-> ``clients/nimo-desktop``
  (+ ``tools/ui-parity`` parity harness);
- service chain: ``novel_forge/desktop`` <-> ``novel_forge/tts``
  <-> ``novel_forge/workspace``.

This checker inspects the commit diff (``--base``..HEAD, default ``HEAD~1``)
and, when any boundary path changed, runs the consumer validation set:
Python API-route contract tests plus the nimo desktop type-check and vitest
suite.  With no boundary change it exits fast (0) without running anything.
``--changed-files`` injects an explicit change list (one path per line) and is
intended for local verification; ``--workspace`` instead reads the current
working-tree changes (including untracked files) via ``git status --porcelain``
so uncommitted boundary edits are validated before commit; ``--plan`` prints
the commands without executing them.

Run ``python scripts/check_contract_consumers.py [--run|--plan] [--base <ref>] [--workspace]``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Registered boundary prefixes; a change under any of them triggers consumers.
BOUNDARY_PREFIXES: tuple[str, ...] = (
    "packages/engine-contracts/",
    "clients/nimo-desktop/",
    "tools/ui-parity/",
    "novel_forge/desktop/",
    "novel_forge/tts/",
    "novel_forge/workspace/",
)

# Consumer validation set: (label, argv, cwd-relative-to-root).
CONSUMER_COMMANDS: tuple[tuple[str, list[str], str | None], ...] = (
    (
        "python-api-contracts",
        [sys.executable, "-m", "pytest", "tests/unit/test_api_engine_routes.py",
         "tests/unit/test_app_service_jobs.py", "-q"],
        None,
    ),
    ("nimo-typecheck", ["pnpm", "--dir", "clients/nimo-desktop", "check"], None),
    ("nimo-vitest", ["pnpm", "--dir", "clients/nimo-desktop", "test"], None),
)


def _git(args: list[str]) -> str:
    """Run a read-only git command inside the repository root."""

    result = subprocess.run(
        ["git", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def changed_paths(base: str | None) -> list[str]:
    """Return the paths changed between base (default HEAD~1) and HEAD."""

    if base is None:
        base = "HEAD~1"
    return [line for line in _git(["diff", "--name-only", f"{base}..HEAD"]).splitlines() if line]


def changed_paths_from_file(path: str) -> list[str]:
    """Read an explicit change list from a file (one path per line)."""

    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def workspace_changed_paths() -> list[str]:
    """Return working-tree change paths (staged, unstaged, and untracked).

    Uses ``git status --porcelain -z`` (NUL-separated) so paths with spaces or
    special characters need no quoting.  Rename entries carry the new path in
    the following field.
    """

    output = _git(["status", "--porcelain", "-z"])
    parts = output.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        if not entry:
            i += 1
            continue
        # Entry format: "XY path" where X/Y are porcelain status codes.
        status = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if "R" in status:
            # Rename: the next NUL field holds the new path.
            if i + 1 < len(parts) and parts[i + 1]:
                paths.append(parts[i + 1])
                i += 2
                continue
        if path:
            paths.append(path)
        i += 1
    return paths


def boundary_hits(changes: list[str]) -> list[str]:
    """Return the changed paths that fall inside a registered boundary."""

    return [change for change in changes if any(change.startswith(prefix) for prefix in BOUNDARY_PREFIXES)]


def run_consumer(label: str, argv: list[str], cwd: str | None) -> tuple[int, str]:
    """Run one consumer command and return (returncode, tail of output)."""

    result = subprocess.run(
        argv,
        cwd=_ROOT if cwd is None else _ROOT / cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (result.stdout + result.stderr).strip().splitlines()[-8:]
    return result.returncode, "\n".join(tail)


def main() -> int:
    """Parse arguments, detect boundary changes, and run or plan consumers."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="git base ref for the diff (default: HEAD~1)")
    parser.add_argument("--changed-files", default=None, help="explicit change list file (overrides git diff)")
    parser.add_argument("--workspace", action="store_true", help="use working-tree changes (git status) instead of the commit diff")
    parser.add_argument("--plan", action="store_true", help="print the consumer commands without running them")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    if args.changed_files:
        changes = changed_paths_from_file(args.changed_files)
    elif args.workspace:
        changes = workspace_changed_paths()
    else:
        changes = changed_paths(args.base)
    hits = boundary_hits(changes)
    result = {
        "kind": "contract-consumers-check",
        "status": "pass",
        "boundaryChanged": len(hits) > 0,
        "boundaryHits": hits,
        "commands": [label for label, _argv, _cwd in CONSUMER_COMMANDS] if hits else [],
        "results": [],
    }

    if not hits:
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("OK: no registered boundary path changed; consumer validation skipped")
        return 0

    failures = 0
    for label, argv, cwd in CONSUMER_COMMANDS:
        if args.plan:
            if not args.json:
                print(f"PLAN: {label}: {' '.join(argv)}")
            continue
        returncode, tail = run_consumer(label, argv, cwd)
        result["results"].append({"label": label, "returncode": returncode, "tail": tail})
        failures += 1 if returncode != 0 else 0
        if not args.json:
            print(f"RUN: {label}")
            print(tail)
    result["status"] = "fail" if failures else "pass"
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
