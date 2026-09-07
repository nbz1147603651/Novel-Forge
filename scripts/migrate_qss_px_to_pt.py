"""Migrate QSS font-size: NNpx to NNpt in theme/*.py and document_renderer HTML.

Usage:
    python scripts/migrate_qss_px_to_pt.py --dry-run    # Show what would change
    python scripts/migrate_qss_px_to_pt.py --apply      # Apply changes
    python scripts/migrate_qss_px_to_pt.py --target theme/components.py  # Single file
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Match font-size: NNpx  (NN = 1-3 digits, possibly with decimal)
_FONT_SIZE_PX_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")

# Repo root is the parent of the scripts/ directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def migrate_text(text: str) -> str:
    """Replace font-size: NNpx with NNpt."""
    def repl(match: re.Match) -> str:
        value = match.group(1)
        return f"font-size: {value}pt"
    return _FONT_SIZE_PX_RE.sub(repl, text)


def process_file(path: Path, *, dry_run: bool) -> int:
    content = path.read_text(encoding="utf-8")
    new_content = migrate_text(content)
    if new_content == content:
        return 0
    if not dry_run:
        path.write_text(new_content, encoding="utf-8")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", dest="apply_changes")
    parser.add_argument("--target", type=str, default=None)
    args = parser.parse_args()
    if not args.dry_run and not args.apply_changes:
        args.dry_run = True  # default to dry-run for safety

    root = _REPO_ROOT / "novel_forge" / "desktop"
    if args.target:
        targets = [Path(args.target).resolve()]
    else:
        targets = list((root / "theme").rglob("*.py"))
        targets += list((root / "pages" / "document_renderer").rglob("*.py"))

    changed = 0
    for path in targets:
        if not path.exists():
            continue
        n = process_file(path, dry_run=args.dry_run)
        if n > 0:
            action = "would change" if args.dry_run else "changed"
            print(f"[{action}] {path}")
            changed += n

    action = "would change" if args.dry_run else "changed"
    print(f"\nTotal {action}: {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())