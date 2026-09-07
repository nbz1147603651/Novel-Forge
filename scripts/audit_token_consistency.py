#!/usr/bin/env python3
"""Audit token consistency: validate that design tokens match theme hex values.

Parses all ``theme/*.py`` files, extracts hex values via regex, and reports
which ones are covered by the token system and which are not.

Usage::

    python scripts/audit_token_consistency.py            # human-readable report
    python scripts/audit_token_consistency.py --check    # exit 1 if inconsistencies
    python scripts/audit_token_consistency.py --json     # JSON output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path for imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from novel_forge.desktop.theme.core import (  # noqa: E402
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    BG_SURFACE,
    BG_WORKSPACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from novel_forge.desktop.tokens.colors import COLORS, all_hex_values  # noqa: E402


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _extract_theme_hexes(theme_dir: Path) -> dict[str, set[str]]:
    """Extract all unique hex values from each theme/*.py file."""
    result: dict[str, set[str]] = {}
    for f in sorted(theme_dir.glob("*.py")):
        content = f.read_text()
        hexes = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}", content)}
        result[f.name] = hexes
    return result


def _check_core_constants() -> list[dict[str, str]]:
    """Verify the 7 core constants match token values pixel-perfectly."""
    checks = [
        ("ACCENT_PRIMARY", ACCENT_PRIMARY, "accent.primary"),
        ("ACCENT_PRIMARY_HOVER", ACCENT_PRIMARY_HOVER, "accent.primary.hover"),
        ("BG_WORKSPACE", BG_WORKSPACE, "bg.workspace"),
        ("BG_SURFACE", BG_SURFACE, "bg.surface"),
        ("TEXT_PRIMARY", TEXT_PRIMARY, "text.primary"),
        ("TEXT_SECONDARY", TEXT_SECONDARY, "text.secondary"),
    ]
    issues: list[dict[str, str]] = []
    for name, rgb_tuple, token_key in checks:
        expected_hex = _rgb_to_hex(*rgb_tuple[:3])
        token_hex = COLORS.get(token_key)
        if token_hex is None:
            issues.append({
                "check": "core_constant",
                "constant": name,
                "token": token_key,
                "expected": expected_hex,
                "actual": "MISSING TOKEN",
                "status": "FAIL",
            })
        elif token_hex[0].lower() != expected_hex.lower():
            issues.append({
                "check": "core_constant",
                "constant": name,
                "token": token_key,
                "expected": expected_hex,
                "actual": token_hex[0],
                "status": "FAIL",
            })
        else:
            issues.append({
                "check": "core_constant",
                "constant": name,
                "token": token_key,
                "expected": expected_hex,
                "actual": token_hex[0],
                "status": "PASS",
            })
    return issues


def _check_coverage(theme_dir: Path) -> dict[str, object]:
    """Check what percentage of theme hex values are covered by tokens."""
    token_hexes = all_hex_values()
    per_file = _extract_theme_hexes(theme_dir)

    all_found: set[str] = set()
    all_matched: set[str] = set()
    all_unmatched: set[str] = set()
    file_reports: dict[str, dict[str, object]] = {}

    for fname, hexes in per_file.items():
        all_found.update(hexes)
        matched = hexes & token_hexes
        unmatched = hexes - token_hexes
        all_matched.update(matched)
        all_unmatched.update(unmatched)
        file_reports[fname] = {
            "total": len(hexes),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "coverage_pct": round(len(matched) / max(len(hexes), 1) * 100, 1),
            "unmatched_values": sorted(unmatched),
        }

    total = len(all_found)
    coverage = len(all_matched) / max(total, 1) * 100

    return {
        "total_unique_hex": total,
        "matched": len(all_matched),
        "unmatched": len(all_unmatched),
        "coverage_pct": round(coverage, 1),
        "unmatched_values": sorted(all_unmatched),
        "per_file": file_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit token consistency")
    parser.add_argument("--check", action="store_true", help="Exit 1 if inconsistencies")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    theme_dir = _PROJECT_ROOT / "novel_forge" / "desktop" / "theme"

    # 1. Core constant checks
    core_results = _check_core_constants()

    # 2. Coverage analysis
    coverage = _check_coverage(theme_dir)

    # 3. Token count
    token_count = len(COLORS)

    report: dict[str, object] = {
        "token_count": token_count,
        "core_constants": core_results,
        "coverage": coverage,
    }

    if args.json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=== Token Consistency Audit ===")
        print(f"Total tokens: {token_count}")
        print()

        print("--- Core Constants (pixel-perfect) ---")
        for c in core_results:
            status_icon = "✓" if c["status"] == "PASS" else "✗"
            print(f"  {status_icon} {c['constant']} → {c['token']}: {c['expected']} == {c['actual']}")
        print()

        print("--- Coverage ---")
        cov = coverage["coverage_pct"]
        print(f"  {cov}% ({coverage['matched']}/{coverage['total_unique_hex']})")
        unmatched_vals = coverage["unmatched_values"]
        if unmatched_vals:
            print(f"  Unmatched: {unmatched_vals[:20]}")
        print()

        print("--- Per-file ---")
        for fname, info in coverage["per_file"].items():
            print(f"  {fname}: {info['coverage_pct']}% ({info['matched']}/{info['total']})")

    # Exit code
    if args.check:
        core_fail = any(c["status"] != "PASS" for c in core_results)
        cov_pct = coverage["coverage_pct"]
        assert isinstance(cov_pct, float)
        cov_below_80 = cov_pct < 80
        if core_fail:
            print("\nFAIL: Core constant mismatch detected", file=sys.stderr)
            return 1
        if cov_below_80:
            print(f"\nFAIL: Coverage {cov_pct}% < 80%", file=sys.stderr)
            return 1
        print("\nPASS: All checks passed")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
