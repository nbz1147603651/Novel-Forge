#!/usr/bin/env python3
"""Build the primary NIMO React/Tauri desktop client.

The PySide6 compatibility package remains available through
``scripts/build_pyside_desktop.py``. This script intentionally does not install
Node, pnpm, Rust, or platform SDKs on the user's behalf.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ROOT = ROOT / "clients" / "nimo-desktop"


def _build_command(*, debug: bool, target: str | None) -> list[str]:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise FileNotFoundError("未找到 pnpm；请先安装 Node.js 22+ 与 pnpm。")
    command = [pnpm, "--filter", "@nimo/desktop", "tauri", "build"]
    if debug:
        command.append("--debug")
    if target:
        command.extend(["--target", target])
    return command


def _clean() -> None:
    for path in (CLIENT_ROOT / "dist", CLIENT_ROOT / "src-tauri" / "target"):
        if path.exists():
            print(f"[build] 清理目录: {path}")
            shutil.rmtree(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建默认 NIMO React/Tauri 桌面端")
    parser.add_argument("--clean", action="store_true", help="构建前清理前端和 Rust 产物")
    parser.add_argument("--debug", action="store_true", help="生成未优化的调试构建")
    parser.add_argument("--target", help="可选 Rust target triple")
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="只打印将执行的命令，用于环境和 CI 检查",
    )
    args = parser.parse_args(argv)

    try:
        command = _build_command(debug=args.debug, target=args.target)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    if args.clean and not args.print_command:
        _clean()

    print("[build] 执行:", " ".join(command))
    if args.print_command:
        return 0
    return subprocess.run(command, cwd=str(ROOT), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
