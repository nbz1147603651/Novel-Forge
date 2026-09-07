"""Canonical artifact diff harness for behavior-snapshot tests.

Provides deterministic hashing of pipeline artifacts (JSON files, Markdown
chapters, reports) so that refactoring can be verified by comparing
pre/post artifact fingerprints.  Noise fields (timestamps, absolute paths,
run IDs, UUIDs) are stripped before hashing to avoid false positives.

Usage::

    from tests.helpers.artifact_diff import artifact_fingerprint

    fp = artifact_fingerprint(project_dir, [
        "chapters/chapter_001.md",
        "reports/chapter_001_alignment.json",
        "story_kernel/canon_current.json",
    ])
    # fp is a dict[str, str] mapping relative-path → sha256 hex digest

All public functions are pure and side-effect free.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_NOISE_KEYS: frozenset[str] = frozenset({
    "ts",
    "timestamp",
    "created_at",
    "updated_at",
    "started_at",
    "ended_at",
    "run_id",
    "session_id",
    "trace_id",
    "span_id",
    "request_id",
    "uuid",
    "generated_at",
    "logged_at",
    "elapsed_ms",
    "latency_ms",
    "duration_ms",
    "fingerprint",
})

_NOISE_KEY_SUFFIXES: tuple[str, ...] = (
    "_timestamp",
    "_at",
    "_ms",
    "_path",
    "_dir",
)

_PATH_PATTERN = re.compile(
    r"/(?:private/)?(?:tmp|var|Users|home|tmpdir|pytest|var/folders)[^\s\"']+",
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _is_noise_key(key: str) -> bool:
    if key in _NOISE_KEYS:
        return True
    return any(key.endswith(suffix) for suffix in _NOISE_KEY_SUFFIXES)


def _strip_noise(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_noise(v)
            for k, v in value.items()
            if not _is_noise_key(k)
        }
    if isinstance(value, list):
        return [_strip_noise(item) for item in value]
    if isinstance(value, str):
        cleaned = _PATH_PATTERN.sub("<PATH>", value)
        cleaned = _UUID_PATTERN.sub("<UUID>", cleaned)
        return cleaned
    return value


def canonical_json_bytes(obj: Any) -> bytes:
    stripped = _strip_noise(obj)
    return json.dumps(
        stripped,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_json_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def byte_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonicalize_text(text: str) -> str:
    cleaned = _PATH_PATTERN.sub("<PATH>", text)
    cleaned = _UUID_PATTERN.sub("<UUID>", cleaned)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(canonicalize_text(text).encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix == ".json":
        try:
            obj = json.loads(raw)
            return canonical_json_hash(obj)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        text = raw.decode("utf-8")
        return text_hash(text)
    except UnicodeDecodeError:
        return byte_hash(raw)


def artifact_fingerprint(
    base_dir: Path,
    relative_paths: list[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in relative_paths:
        full = base_dir / rel
        if full.exists() and full.is_file():
            result[rel] = hash_file(full)
        else:
            result[rel] = "<MISSING>"
    return result


def fingerprint_dir(
    base_dir: Path,
    *,
    suffixes: tuple[str, ...] = (".json", ".md"),
    exclude_patterns: tuple[str, ...] = ("logs", "__pycache__", ".cache"),
) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        rel = path.relative_to(base_dir).as_posix()
        if any(excl in rel for excl in exclude_patterns):
            continue
        result[rel] = hash_file(path)
    return result


def assert_fingerprints_match(
    baseline: dict[str, str],
    current: dict[str, str],
    *,
    ignore_missing_in_current: bool = False,
) -> None:
    mismatches: list[str] = []
    all_keys = sorted(set(baseline) | set(current))
    for key in all_keys:
        b = baseline.get(key, "<MISSING>")
        c = current.get(key, "<MISSING>")
        if b == "<MISSING>" and key not in baseline:
            if not ignore_missing_in_current:
                mismatches.append(f"  + {key} (new in current, not in baseline)")
            continue
        if c == "<MISSING>" and key not in current:
            mismatches.append(f"  - {key} (in baseline, missing in current)")
            continue
        if b != c:
            mismatches.append(f"  ~ {key}\n      baseline={b[:16]}…\n      current ={c[:16]}…")
    if mismatches:
        details = "\n".join(mismatches)
        raise AssertionError(
            f"Artifact fingerprint mismatch ({len(mismatches)} differences):\n{details}"
        )
