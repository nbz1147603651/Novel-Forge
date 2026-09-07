#!/usr/bin/env python3
"""Repair chapter quote marks damaged by over-broad punctuation cleanup.

The 2026-06-30 cleanup for ``data/山风与归人2`` removed sentence-final closing
quotes such as ``。”`` while fixing duplicate punctuation. This script restores
only missing closing quote marks by aligning the damaged chapter against its
draft versions with closing quotes removed.

The script is intentionally conservative:
- default mode is dry-run;
- ``--in-place`` first copies ``chapters/`` to a timestamped backup directory;
- a chapter is not written unless quote-balance validation passes afterward.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

# Ensure project root is importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.pipeline.steps.chapter_quality_prescreen import (  # noqa: E402
    ChapterQualityPrescreen,
)

_OPEN_TO_CLOSE = {
    "“": "”",
    "「": "」",
    "『": "』",
}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_CLOSING_QUOTES = frozenset(_CLOSE_TO_OPEN)
_CLOSABLE_PUNCTUATION = frozenset("。，！？；：、")

# Story-data correction discovered during the repair audit. The broken form came
# from a humanize candidate and leaves an extra opening quote even after quote
# restoration. Keep this explicit so the one non-mechanical edit is auditable.
_KNOWN_TEXT_NORMALIZATIONS = {
    "“原来是“原来是你啊。”": "“原来是你啊。”",
}


@dataclass(frozen=True)
class ChapterRepairResult:
    chapter_number: int
    changed: bool
    inserted_quotes: int
    normalizations: int
    quote_hits_after: int
    path: Path


def _chapter_number(path: Path) -> int:
    try:
        return int(path.stem.split("_")[1])
    except (IndexError, ValueError):
        return 0


def _without_closing_quotes(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    original_positions: list[int] = []
    for index, char in enumerate(text):
        if char in _CLOSING_QUOTES:
            continue
        chars.append(char)
        original_positions.append(index)
    return "".join(chars), original_positions


def _reference_quote_positions(reference_text: str) -> list[tuple[int, str]]:
    """Return closing-quote insertion positions in reference-without-closers."""
    positions: list[tuple[int, str]] = []
    no_closer_index = 0
    for char in reference_text:
        if char in _CLOSING_QUOTES:
            positions.append((no_closer_index, char))
        else:
            no_closer_index += 1
    return positions


def _candidate_insertions(chapter_text: str, references: list[str]) -> dict[int, set[str]]:
    """Map damaged-text insertion index to possible closing quote characters."""
    chapter_basis, chapter_positions = _without_closing_quotes(chapter_text)
    candidates: dict[int, set[str]] = defaultdict(set)

    for reference_text in references:
        reference_basis, _ = _without_closing_quotes(reference_text)
        blocks = SequenceMatcher(
            None,
            reference_basis,
            chapter_basis,
            autojunk=False,
        ).get_matching_blocks()

        for ref_index, close_quote in _reference_quote_positions(reference_text):
            mapped_index: int | None = None
            for block in blocks:
                if block.size and block.a <= ref_index <= block.a + block.size:
                    mapped_index = block.b + (ref_index - block.a)
                    break
            if mapped_index is None:
                continue

            insert_at = (
                len(chapter_text)
                if mapped_index >= len(chapter_positions)
                else chapter_positions[mapped_index]
            )
            if insert_at <= 0:
                continue
            if chapter_text[insert_at - 1] not in _CLOSABLE_PUNCTUATION:
                continue
            if insert_at < len(chapter_text) and chapter_text[insert_at] == close_quote:
                continue
            candidates[insert_at].add(close_quote)

    return candidates


def _apply_balanced_insertions(
    chapter_text: str,
    candidates: dict[int, set[str]],
) -> tuple[str, int]:
    """Insert candidate closing quotes only when they close the current stack."""
    output: list[str] = []
    stack: list[str] = []
    inserted = 0

    for index, char in enumerate(chapter_text):
        if stack and stack[-1] in candidates.get(index, set()):
            output.append(stack.pop())
            inserted += 1

        output.append(char)
        if char in _OPEN_TO_CLOSE:
            stack.append(_OPEN_TO_CLOSE[char])
        elif char in _CLOSING_QUOTES and stack and stack[-1] == char:
            stack.pop()

    if stack and stack[-1] in candidates.get(len(chapter_text), set()):
        output.append(stack.pop())
        inserted += 1

    return "".join(output), inserted


def _apply_known_normalizations(text: str) -> tuple[str, int]:
    count = 0
    for old, new in _KNOWN_TEXT_NORMALIZATIONS.items():
        occurrences = text.count(old)
        if occurrences:
            text = text.replace(old, new)
            count += occurrences
    return text, count


def _quote_hit_count(text: str, chapter_number: int) -> int:
    report = ChapterQualityPrescreen(check_quote_balance=True).prescreen(
        text,
        chapter_number=chapter_number,
    )
    return sum(1 for hit in report.hits if hit.kind == "unbalanced_quote")


def _load_references(project_dir: Path, chapter_number: int) -> list[str]:
    draft_dir = project_dir / "drafts" / f"chapter_{chapter_number:03d}"
    if not draft_dir.is_dir():
        return []
    return [
        path.read_text(encoding="utf-8")
        for path in sorted(draft_dir.glob("*.md"))
        if path.is_file()
    ]


def repair_chapter(chapter_path: Path, project_dir: Path) -> tuple[str, ChapterRepairResult]:
    chapter_number = _chapter_number(chapter_path)
    original = chapter_path.read_text(encoding="utf-8")
    references = _load_references(project_dir, chapter_number)
    if not references:
        raise RuntimeError(f"No draft references found for {chapter_path}")

    repaired, inserted = _apply_balanced_insertions(
        original,
        _candidate_insertions(original, references),
    )
    repaired, normalizations = _apply_known_normalizations(repaired)
    quote_hits = _quote_hit_count(repaired, chapter_number)

    result = ChapterRepairResult(
        chapter_number=chapter_number,
        changed=repaired != original,
        inserted_quotes=inserted,
        normalizations=normalizations,
        quote_hits_after=quote_hits,
        path=chapter_path,
    )
    return repaired, result


def _backup_chapters(project_dir: Path, backup_root: Path | None = None) -> Path:
    source = project_dir / "chapters"
    if backup_root is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = project_dir / "backups" / f"quote_repair_{stamp}"
    target = backup_root / "chapters"
    if target.exists():
        raise FileExistsError(f"Backup target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore missing chapter closing quotes from draft references.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT / "data" / "山风与归人2",
        help="Project directory to repair (default: data/山风与归人2).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Write repaired chapters after creating a backup. Default is dry-run.",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="Optional backup root. The script copies chapters/ under this directory.",
    )
    args = parser.parse_args(argv)

    project_dir = args.project_root
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.is_dir():
        print(f"chapters directory not found: {chapters_dir}", file=sys.stderr)
        return 2

    repairs: list[tuple[str, ChapterRepairResult]] = []
    failures: list[ChapterRepairResult] = []
    for chapter_path in sorted(chapters_dir.glob("chapter_*.md"), key=_chapter_number):
        repaired, result = repair_chapter(chapter_path, project_dir)
        repairs.append((repaired, result))
        if result.quote_hits_after:
            failures.append(result)

    for _repaired, result in repairs:
        status = "FAIL" if result.quote_hits_after else ("FIX" if result.changed else "OK")
        print(
            f"[{status}] Ch{result.chapter_number:03d}: "
            f"inserted={result.inserted_quotes}, "
            f"normalizations={result.normalizations}, "
            f"quote_hits_after={result.quote_hits_after}"
        )

    if failures:
        print("Refusing to write chapters with residual quote-balance hits:", file=sys.stderr)
        for result in failures:
            print(
                f"  Ch{result.chapter_number:03d}: {result.quote_hits_after} hit(s)",
                file=sys.stderr,
            )
        return 1

    changed = [(text, result) for text, result in repairs if result.changed]
    if not args.in_place:
        print(f"Dry-run complete: {len(changed)} chapter(s) would change.")
        return 0

    backup_path = _backup_chapters(project_dir, args.backup_root)
    for repaired, result in changed:
        result.path.write_text(repaired, encoding="utf-8")
    print(f"Wrote {len(changed)} chapter(s). Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
