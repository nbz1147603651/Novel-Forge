"""Keep the legacy mypy debt visible while preventing type-check regressions.

The project has a substantial pre-existing strict-mypy backlog.  This checker
runs the normal ``mypy novel_forge`` command and compares each diagnostic's
path and error code with the checked-in baseline, preserving repeated
diagnostics as a multiset. Source line numbers and full error messages are
intentionally excluded: mechanical refactors and Mypy's nondeterministic
rendering order for ``Literal`` members must not look like hundreds of type
regressions. It deliberately does not disable error codes or use
``ignore_errors``: any new diagnostic, or any resolved diagnostic left stale
in the baseline, fails CI.

Run ``python scripts/check_mypy_baseline.py --update`` only when intentionally
accepting a changed baseline (for example after a dedicated typing-cleanup
commit).  A future clean baseline can be deleted together with this script and
CI can return to invoking mypy directly.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_ERROR_PATTERN = re.compile(r"^(?P<path>[^:\n]+):\d+(?::\d+)?: error: .+ \[(?P<code>[^\]]+)]$")
_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_PATH = _ROOT / "typing" / "mypy_baseline.json"


def _diagnostic_fingerprints(output: str) -> tuple[list[str], list[str]]:
    """Return line- and message-independent mypy identities and unparsable lines."""

    fingerprints: list[str] = []
    unparsable: list[str] = []
    for line in output.splitlines():
        if ": error:" not in line:
            continue
        matched = _ERROR_PATTERN.match(line)
        if matched is None:
            unparsable.append(line)
            continue
        fingerprints.append(f"{matched.group('path')}:{matched.group('code')}")
    return sorted(fingerprints), unparsable


def _load_baseline() -> list[str]:
    if not _BASELINE_PATH.is_file():
        raise FileNotFoundError(f"Missing mypy baseline: {_BASELINE_PATH}")
    payload = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 3:
        raise ValueError(f"Invalid mypy baseline format: {_BASELINE_PATH}")
    errors = payload.get("errors")
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        raise ValueError(f"Invalid mypy baseline errors: {_BASELINE_PATH}")
    return sorted(errors)


def _write_baseline(errors: list[str]) -> None:
    _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 3, "errors": errors}
    _BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _format_delta(title: str, values: Counter[str]) -> str:
    examples = [
        f"  {fingerprint}" + (f" (x{count})" if count > 1 else "")
        for fingerprint, count in values.most_common(20)
    ]
    remainder = sum(values.values()) - len(examples)
    if remainder > 0:
        examples.append(f"  ... and {remainder} more")
    return "\n".join([f"{title} ({sum(values.values())}):", *examples])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the checked-in baseline from the current mypy diagnostics",
    )
    args = parser.parse_args()

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--show-error-codes", "--no-error-summary", "novel_forge"],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    errors, unparsable = _diagnostic_fingerprints(output)
    if unparsable:
        print("Unable to fingerprint one or more mypy diagnostics:", file=sys.stderr)
        print("\n".join(unparsable[:20]), file=sys.stderr)
        return 2
    if result.returncode not in {0, 1}:
        print(output, end="", file=sys.stderr)
        return result.returncode

    if args.update:
        _write_baseline(errors)
        print(f"Updated {_BASELINE_PATH.relative_to(_ROOT)} with {len(errors)} diagnostics.")
        return 0

    try:
        expected = _load_baseline()
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    actual_counter = Counter(errors)
    expected_counter = Counter(expected)
    unexpected = actual_counter - expected_counter
    stale = expected_counter - actual_counter
    if unexpected or stale:
        print("mypy baseline changed; do not silently accept type-check drift.", file=sys.stderr)
        if unexpected:
            print(_format_delta("New diagnostics", unexpected), file=sys.stderr)
        if stale:
            print(_format_delta("Resolved diagnostics", stale), file=sys.stderr)
        print(
            "Run this checker with --update only in an intentional typing-debt commit.",
            file=sys.stderr,
        )
        return 1

    print(f"mypy baseline verified: {len(errors)} tracked diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
