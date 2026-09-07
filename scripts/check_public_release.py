#!/usr/bin/env python3
"""Check publishable files; --history also scans every locally reachable Git blob.

Only paths, rule names and object IDs are printed, never matching secret values.
This is a narrow release guard, not a complete secret or image-content scanner.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider-token": re.compile(
        rb"(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{24,}|"
        rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})"
    ),
}
# Exact, reviewed synthetic fixtures only; never exempt a whole test directory.
SYNTHETIC = {
    hashlib.sha256(b"sk-sp-session-only-fixture-secret").digest(),
    hashlib.sha256(b"sk-1234567890abcdefghijklmnop").digest(),
}
REQUIRED = (
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/README.md",
    ".env.example",
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def private_path(name: str) -> bool:
    path = Path(name)
    base = path.name
    if base == ".env" or base.startswith(".env.") and base != ".env.example":
        return True
    if base in {
        "model_profiles.json",
        "credentials.json",
        "secrets.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
    }:
        return True
    if set(path.parts) & {".authoring", ".locks", ".codegraph", ".codex", ".claude", "Reference"}:
        return True
    if name.startswith("data/"):
        return not (
            path.parent.as_posix() == "data/spec_examples" and base.endswith(".example.json")
        )
    return path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def secret_rules(data: bytes) -> list[str]:
    return [
        name
        for name, pattern in PATTERNS.items()
        if any(
            hashlib.sha256(match.group()).digest() not in SYNTHETIC
            for match in pattern.finditer(data)
        )
    ]


def inspect(name: str, data: bytes, object_id: str = "") -> int:
    rules = secret_rules(data)
    if private_path(name):
        rules.append("private-path")
    if rules:
        print(f"FAIL {name!r} {'object=' + object_id if object_id else ''}: {', '.join(rules)}")
        return 1
    return 0


def check_worktree() -> int:
    errors = 0
    for name in REQUIRED:
        if not Path(name).is_file():
            print(f"FAIL required file missing: {name}")
            errors += 1
    names = set(
        git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    )
    count = 0
    for name in sorted(names - {""}):
        path = Path(name)
        if path.is_symlink():
            print(f"FAIL symlink requires manual release review: {name!r}")
            errors += 1
        elif path.is_file():
            errors += inspect(name, path.read_bytes())
            count += 1
    probes = [
        ".env",
        ".env.local",
        "model_profiles.json",
        "data/private-story/chapters/1.md",
        "data/spec_examples/.locks/.authoring.lock",
        "data/spec_examples/private.json",
        ".codex/session.json",
        "models/private/weights.bin",
        ".authoring/model_costs.sqlite",
    ]
    for name in probes:
        if subprocess.run(["git", "check-ignore", "--no-index", "-q", name]).returncode != 0:
            print(f"FAIL missing ignore rule: {name}")
            errors += 1
    for name in [".env.example", "data/spec_examples/spec_with_backstory_window.example.json"]:
        if subprocess.run(["git", "check-ignore", "--no-index", "-q", name]).returncode != 1:
            print(f"FAIL public example is ignored: {name}")
            errors += 1
    print(f"Worktree: {count} files checked; {errors} findings.")
    return errors


def check_history() -> int:
    errors = count = 0
    entries = git("rev-list", "--objects", "--all").splitlines()
    with subprocess.Popen(
        ["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        for entry in entries:
            object_id, _, raw_name = entry.partition(b" ")
            proc.stdin.write(object_id + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)
            if header[1] == b"blob":
                count += 1
                errors += inspect(raw_name.decode(errors="replace"), data, object_id.decode())
        proc.stdin.close()
    print(
        f"History: {count} reachable blobs checked; {errors} findings. Remote-only refs and image contents are not checked."
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history", action="store_true", help="also scan all locally reachable history"
    )
    args = parser.parse_args()
    errors = check_worktree()
    if args.history:
        errors += check_history()
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
