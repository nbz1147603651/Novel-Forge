#!/usr/bin/env python3
"""Batch PySide6 ↔ Tauri/macOS pixel-level comparison and report generation.

Generates a comparison report in docs/ui-parity/ that classifies each
page × theme combination as:
  - accepted: diff below threshold (可接受差异)
  - needs-fix: diff above threshold (需修复差异)
  - missing: one or both captures unavailable

Usage:
  # 1. Build Tauri debug binary first:
  cd clients/nimo-desktop && CI=true ./node_modules/.bin/tauri build --debug --no-bundle && cd ../..

  # 2. Run the batch comparison:
  .venv/bin/python tools/ui-parity/run_tauri_parity_comparison.py \
    --pyside6-dir /tmp/nimo-ui-parity-pyside-matrix \
    --tauri-dir /tmp/nimo-ui-parity-tauri-matrix \
    --report-dir docs/ui-parity/tauri-parity-report
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPARE_SCRIPT = PROJECT_ROOT / "tools" / "ui-parity" / "compare_screenshots.py"

THEMES = ["narrative_ember", "ink_jade", "ink_amethyst", "stillwater", "twilight_ink"]
PAGES = ["dashboard", "chapter_studio", "workflow", "settings", "voice_studio"]
VIEWPORT = "1440x900"

MAX_CHANGED_PIXEL_RATIO = 0.0075
MAX_MEAN_CHANNEL_DELTA = 0.0075


@dataclass
class ComparisonEntry:
    page: str
    theme: str
    status: str  # "accepted" | "needs-fix" | "missing"
    changed_pixel_ratio: float | None = None
    mean_channel_delta: float | None = None
    pyside6_file: str | None = None
    tauri_file: str | None = None
    diff_file: str | None = None
    classification: str = ""  # "可接受差异" | "需修复差异" | "截图缺失"


def find_pyside6_golden(pyside6_dir: Path, page: str, theme: str) -> Path | None:
    """Locate the PySide6 golden for a page × theme combination."""
    patterns = [
        f"{page}--{theme}--{VIEWPORT}.png",
        f"{page}--{theme}.png",
        f"pyside6--{page}--{theme}--{VIEWPORT}.png",
    ]
    for pattern in patterns:
        candidate = pyside6_dir / pattern
        if candidate.exists():
            return candidate
    # Fallback: search for any file matching page + theme
    for f in pyside6_dir.glob(f"*{page}*{theme}*{VIEWPORT}*.png"):
        return f
    for f in pyside6_dir.glob(f"*{page}*{theme}*.png"):
        return f
    return None


def find_tauri_capture(tauri_dir: Path, page: str, theme: str) -> Path | None:
    """Locate the Tauri capture for a page × theme combination."""
    patterns = [
        f"tauri--{theme}--{page}--{VIEWPORT}.png",
        f"tauri--{theme}--{page}.png",
    ]
    for pattern in patterns:
        candidate = tauri_dir / pattern
        if candidate.exists():
            return candidate
    # Fallback: search
    for f in tauri_dir.glob(f"tauri*{theme}*{page}*{VIEWPORT}*.png"):
        return f
    for f in tauri_dir.glob(f"tauri*{theme}*{page}*.png"):
        return f
    return None


def compare_pair(
    pyside6_file: Path,
    tauri_file: Path,
    diff_output: Path,
    report_output: Path,
) -> dict:
    """Run the pixel-level comparison between two PNG files."""
    command = [
        sys.executable,
        str(COMPARE_SCRIPT),
        str(pyside6_file),
        str(tauri_file),
        "--diff-output",
        str(diff_output),
        "--report-output",
        str(report_output),
        "--max-changed-pixel-ratio",
        str(MAX_CHANGED_PIXEL_RATIO),
        "--max-mean-channel-delta",
        str(MAX_MEAN_CHANNEL_DELTA),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        return json.loads(report_output.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"accepted": result.returncode == 0, "metrics": {}}


def generate_report(entries: list[ComparisonEntry], report_dir: Path) -> None:
    """Generate an HTML comparison report."""
    report_dir.mkdir(parents=True, exist_ok=True)

    # JSON report
    json_path = report_dir / "comparison-report.json"
    json_data = {
        "summary": {
            "total": len(entries),
            "accepted": sum(1 for e in entries if e.status == "accepted"),
            "needs_fix": sum(1 for e in entries if e.status == "needs-fix"),
            "missing": sum(1 for e in entries if e.status == "missing"),
        },
        "entries": [asdict(e) for e in entries],
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # HTML report
    html_path = report_dir / "comparison-report.html"
    accepted_count = json_data["summary"]["accepted"]
    needs_fix_count = json_data["summary"]["needs_fix"]
    missing_count = json_data["summary"]["missing"]

    rows = []
    for e in entries:
        status_class = "accepted" if e.status == "accepted" else "needs-fix" if e.status == "needs-fix" else "missing"
        diff_link = f'<a href="{e.diff_file}">diff</a>' if e.diff_file else "—"
        metric_text = "—"
        if e.changed_pixel_ratio is not None:
            metric_text = f"{e.changed_pixel_ratio:.4%} pixels, Δ={e.mean_channel_delta:.4f}"
        rows.append(
            f'<tr class="{status_class}">'
            f"<td>{e.page}</td><td>{e.theme}</td>"
            f'<td class="status-{status_class}">{e.classification}</td>'
            f"<td>{metric_text}</td><td>{diff_link}</td></tr>"
        )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>UI-Parity Tauri/macOS 像素级对比报告</title>
<style>
body {{ font-family: -apple-system, 'PingFang SC', sans-serif; margin: 24px; }}
h1 {{ font-size: 20px; }}
.summary {{ display: flex; gap: 16px; margin: 16px 0; }}
.summary .card {{ padding: 12px 20px; border-radius: 8px; border: 1px solid #ddd; }}
.card.accepted {{ background: #e8f5e9; border-color: #4caf50; }}
.card.needs-fix {{ background: #fff3e0; border-color: #ff9800; }}
.card.missing {{ background: #f5f5f5; border-color: #9e9e9e; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 13px; }}
th {{ background: #f5f5f5; }}
tr.accepted {{ background: #fafffa; }}
tr.needs-fix {{ background: #fffbf5; }}
tr.missing {{ background: #fafafa; }}
.status-accepted {{ color: #2e7d32; font-weight: 600; }}
.status-needs-fix {{ color: #e65100; font-weight: 600; }}
.status-missing {{ color: #757575; font-weight: 600; }}
</style>
</head>
<body>
<h1>UI-Parity Tauri/macOS 像素级对比报告</h1>
<p>阈值: changed_pixel_ratio ≤ {MAX_CHANGED_PIXEL_RATIO:.2%}, mean_channel_delta ≤ {MAX_MEAN_CHANNEL_DELTA:.2%}</p>
<div class="summary">
  <div class="card accepted">✅ 可接受差异: {accepted_count}</div>
  <div class="card needs-fix">⚠️ 需修复差异: {needs_fix_count}</div>
  <div class="card missing">❓ 截图缺失: {missing_count}</div>
</div>
<table>
<thead><tr><th>页面</th><th>主题</th><th>分类</th><th>差异指标</th><th>Diff</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</body>
</html>"""
    html_path.write_text(html_content, encoding="utf-8")
    print(f"Report generated: {html_path}")
    print(f"JSON report: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyside6-dir", type=Path, required=True, help="Directory with PySide6 golden captures")
    parser.add_argument("--tauri-dir", type=Path, required=True, help="Directory with Tauri captures")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "docs" / "ui-parity" / "tauri-parity-report")
    parser.add_argument("--pages", nargs="+", default=PAGES)
    parser.add_argument("--themes", nargs="+", default=THEMES)
    args = parser.parse_args()

    entries: list[ComparisonEntry] = []

    for page in args.pages:
        for theme in args.themes:
            pyside6_file = find_pyside6_golden(args.pyside6_dir, page, theme)
            tauri_file = find_tauri_capture(args.tauri_dir, page, theme)

            if pyside6_file is None or tauri_file is None:
                entries.append(ComparisonEntry(
                    page=page, theme=theme, status="missing",
                    pyside6_file=str(pyside6_file) if pyside6_file else None,
                    tauri_file=str(tauri_file) if tauri_file else None,
                    classification="截图缺失",
                ))
                continue

            diff_dir = args.report_dir / "diffs"
            diff_dir.mkdir(parents=True, exist_ok=True)
            diff_file = diff_dir / f"{page}--{theme}--diff.png"
            report_file = diff_dir / f"{page}--{theme}--report.json"

            result = compare_pair(pyside6_file, tauri_file, diff_file, report_file)
            accepted = result.get("accepted", False)
            metrics = result.get("metrics", {})
            changed_ratio = metrics.get("changed_pixel_ratio", 0)
            mean_delta = metrics.get("mean_rgba_channel_delta", 0)

            entries.append(ComparisonEntry(
                page=page,
                theme=theme,
                status="accepted" if accepted else "needs-fix",
                changed_pixel_ratio=changed_ratio,
                mean_channel_delta=mean_delta,
                pyside6_file=str(pyside6_file),
                tauri_file=str(tauri_file),
                diff_file=str(diff_file.relative_to(args.report_dir)),
                classification="可接受差异" if accepted else "需修复差异",
            ))

    generate_report(entries, args.report_dir)

    # Print summary
    accepted = sum(1 for e in entries if e.status == "accepted")
    needs_fix = sum(1 for e in entries if e.status == "needs-fix")
    missing = sum(1 for e in entries if e.status == "missing")
    print(f"\nSummary: {accepted} accepted, {needs_fix} needs-fix, {missing} missing (total: {len(entries)})")

    # A comparison gate is only meaningful when every requested pair exists.
    # Returning success for an all-missing matrix would let Phase 1 sign-off
    # pass without any PySide6 ↔ Tauri evidence.
    return 0 if needs_fix == 0 and missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
