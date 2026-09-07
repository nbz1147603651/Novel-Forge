#!/usr/bin/env python3
"""Replay sanitized repair fixtures or persisted RepairCases without providers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.pipeline.repair_orchestration.replay import (  # noqa: E402
    RepairReplayResult,
    replay_project_repair_cases,
    replay_repair_fixtures,
)

_DEFAULT_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "repair_replay"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=_DEFAULT_FIXTURES,
        help="Fixture file/directory, or a project root when --project is used.",
    )
    parser.add_argument("--project", action="store_true", help="Replay persisted project cases.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    return parser


def _render(results: list[RepairReplayResult], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
        return
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.fixture} checks={','.join(result.checks) or '-'}")
        if result.error:
            print(f"  error={result.error}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    results = (
        replay_project_repair_cases(args.path)
        if args.project
        else replay_repair_fixtures(args.path)
    )
    _render(results, as_json=args.json)
    return 0 if results and all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
