#!/usr/bin/env python3
"""Verify design tokens: validate that all ``{{token.name}}`` placeholders in
theme QSS CONTENT strings are registered in the token system.

Parses every ``theme/*.py`` module, extracts ``{{…}}`` placeholders via regex,
and checks each one against ``tokens/colors.py`` and ``tokens/spacing.py``.
Also reports any residual 6-digit hex values that should have been replaced.

Usage::

    python scripts/verify_design_tokens.py            # human-readable report
    python scripts/verify_design_tokens.py --check    # exit 1 on any issue
    python scripts/verify_design_tokens.py --json     # JSON output
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path for imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from novel_forge.desktop.tokens.colors import COLORS  # noqa: E402
from novel_forge.desktop.tokens.spacing import SPACING  # noqa: E402

# ── Constants ───────────────────────────────────────────────────────────────

THEME_DIR = _PROJECT_ROOT / "novel_forge" / "desktop" / "theme"

# All theme modules that contain CONTENT strings
THEME_MODULES = [
    "_globals.py",
    "components.py",
    "dialogs.py",
    "forms.py",
    "memory.py",
    "navigation.py",
    "toast.py",
]

# Regex patterns
TOKEN_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")
HEX6_RE = re.compile(r"#[0-9a-fA-F]{6}\b")

# Valid token names (union of color + spacing)
VALID_TOKENS: set[str] = set(COLORS.keys()) | set(SPACING.keys())


# ── Analysis ────────────────────────────────────────────────────────────────


def _parse_theme_file(fpath: Path) -> dict[str, object]:
    """Parse a single theme/*.py file and extract token/hex statistics."""
    module_name = f"novel_forge.desktop.theme.{fpath.stem}"
    module = importlib.import_module(module_name)
    content = getattr(module, "CONTENT", None)
    if not isinstance(content, str):
        return {"error": "no CONTENT string found"}

    # Extract token placeholders
    placeholders = TOKEN_PLACEHOLDER_RE.findall(content)
    token_counts: dict[str, int] = {}
    for p in placeholders:
        token_counts[p] = token_counts.get(p, 0) + 1

    # Check for unregistered tokens
    unregistered = [t for t in token_counts if t not in VALID_TOKENS]

    # Check for residual hex values (should be 0 after migration)
    residual_hex = sorted(set(m.lower() for m in HEX6_RE.findall(content)))

    return {
        "placeholders": dict(sorted(token_counts.items())),
        "placeholder_count": len(placeholders),
        "unique_tokens": len(token_counts),
        "unregistered": sorted(unregistered),
        "residual_hex": residual_hex,
        "content_length": len(content),
    }


def _analyze_all() -> dict[str, object]:
    """Analyze all theme modules and produce a full report."""
    per_file: dict[str, dict[str, object]] = {}
    all_tokens: dict[str, int] = {}
    all_unregistered: set[str] = set()
    all_residual_hex: set[str] = set()
    total_placeholders = 0

    for fname in THEME_MODULES:
        fpath = THEME_DIR / fname
        if not fpath.exists():
            per_file[fname] = {"error": "file not found"}
            continue
        result = _parse_theme_file(fpath)
        per_file[fname] = result

        if "error" in result:
            continue

        # Aggregate
        placeholders = result.get("placeholders", {})
        assert isinstance(placeholders, dict)
        for token, count in placeholders.items():
            all_tokens[token] = all_tokens.get(token, 0) + count

        total_placeholders += result.get("placeholder_count", 0)
        unreg = result.get("unregistered", [])
        assert isinstance(unreg, list)
        all_unregistered.update(unreg)
        residual = result.get("residual_hex", [])
        assert isinstance(residual, list)
        all_residual_hex.update(residual)

    # Token usage statistics
    unused_tokens = VALID_TOKENS - set(all_tokens.keys())
    used_color_tokens = set(all_tokens.keys()) & set(COLORS.keys())
    used_spacing_tokens = set(all_tokens.keys()) & set(SPACING.keys())

    return {
        "error_files": sorted(
            fname
            for fname, info in per_file.items()
            if isinstance(info, dict) and "error" in info
        ),
        "total_tokens_registered": len(VALID_TOKENS),
        "color_tokens": len(COLORS),
        "spacing_tokens": len(SPACING),
        "total_placeholders": total_placeholders,
        "unique_tokens_used": len(all_tokens),
        "color_tokens_used": len(used_color_tokens),
        "spacing_tokens_used": len(used_spacing_tokens),
        "unused_tokens": len(unused_tokens),
        "unused_token_names": sorted(unused_tokens),
        "unregistered_tokens": sorted(all_unregistered),
        "residual_hex_values": sorted(all_residual_hex),
        "per_file": per_file,
        "token_usage": dict(sorted(all_tokens.items())),
    }


# ── Output ──────────────────────────────────────────────────────────────────


def _print_report(report: dict[str, object]) -> None:
    """Print a human-readable report."""
    print("=== Design Token Verification ===")
    print()

    # Summary
    print(f"Registered tokens: {report['total_tokens_registered']} "
          f"({report['color_tokens']} color + {report['spacing_tokens']} spacing)")
    print(f"Placeholders in QSS: {report['total_placeholders']} "
          f"({report['unique_tokens_used']} unique)")
    print(f"  Color tokens used: {report['color_tokens_used']}")
    print(f"  Spacing tokens used: {report['spacing_tokens_used']}")
    print(f"  Unused tokens: {report['unused_tokens']}")
    print()

    # Per-file breakdown
    print("--- Per-file ---")
    per_file = report["per_file"]
    assert isinstance(per_file, dict)
    for fname, info in per_file.items():
        if "error" in info:
            print(f"  {fname}: ERROR — {info['error']}")
        else:
            n = info.get("placeholder_count", 0)
            u = info.get("unique_tokens", 0)
            print(f"  {fname}: {n} placeholders ({u} unique)")
    print()

    # Unregistered tokens
    unregistered = report["unregistered_tokens"]
    assert isinstance(unregistered, list)
    if unregistered:
        print(f"--- Unregistered Tokens ({len(unregistered)}) ---")
        for t in unregistered:
            print(f"  ✗ {t}")
        print()

    # Residual hex
    residual = report["residual_hex_values"]
    assert isinstance(residual, list)
    if residual:
        print(f"--- Residual Hex Values ({len(residual)}) ---")
        for h in residual:
            print(f"  ✗ {h}")
        print()

    # Unused tokens (informational)
    unused = report["unused_token_names"]
    assert isinstance(unused, list)
    if unused:
        print(f"--- Unused Tokens ({len(unused)}) ---")
        for t in unused[:20]:
            print(f"  · {t}")
        if len(unused) > 20:
            print(f"  … and {len(unused) - 20} more")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify design tokens in theme QSS")
    parser.add_argument("--check", action="store_true", help="Exit 1 on any issue")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    report = _analyze_all()

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report)

    if args.check:
        error_files = report["error_files"]
        assert isinstance(error_files, list)
        unregistered = report["unregistered_tokens"]
        assert isinstance(unregistered, list)
        residual = report["residual_hex_values"]
        assert isinstance(residual, list)

        if error_files:
            print(f"\nFAIL: {len(error_files)} theme file(s) could not be parsed", file=sys.stderr)
            return 1
        if unregistered:
            print(f"\nFAIL: {len(unregistered)} unregistered token(s)", file=sys.stderr)
            return 1
        if residual:
            print(f"\nFAIL: {len(residual)} residual hex value(s)", file=sys.stderr)
            return 1
        print("\nPASS: All design tokens verified")
        return 0

    if not args.json_output:
        unregistered = report["unregistered_tokens"]
        assert isinstance(unregistered, list)
        residual = report["residual_hex_values"]
        assert isinstance(residual, list)
        if not unregistered and not residual:
            print("All design tokens verified ✓")

    return 0


if __name__ == "__main__":
    sys.exit(main())
