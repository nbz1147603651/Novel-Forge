#!/usr/bin/env python3
"""
构建 PySide6 兼容后备端可执行文件的便捷脚本。

用法:
    python scripts/build_pyside_desktop.py [--clean] [--onefile]

选项:
    --clean     构建前清空 build/ 和 dist/ 目录
    --onefile   强制使用单文件模式（macOS/Linux 默认为 one-dir）
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "novel_forge_desktop.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
DEFAULT_OLLAMA_BUNDLE_DIR = ROOT / "vendor" / "ollama"


def _clean() -> None:
    for d in (DIST, BUILD):
        if d.exists():
            print(f"[build] 清理目录: {d}")
            shutil.rmtree(d)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] 安装 PyInstaller …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def _ensure_pillow() -> None:
    """Pillow 用于 macOS 上自动将 .ico 转换为 .icns。"""
    try:
        import PIL  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print("[build] 安装 Pillow（macOS 图标转换需要）…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])


def _ensure_uv() -> Path:
    """Return the uv executable that will be bundled for managed sidecar Python."""
    executable = "uv.exe" if platform.system() == "Windows" else "uv"
    candidates = [Path(sys.executable).parent / executable]
    located = shutil.which("uv")
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    print("[build] 安装 uv 运行时管理器 …")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "uv>=0.10,<1"])
    installed = Path(sys.executable).parent / executable
    if not installed.is_file():
        raise RuntimeError("uv 安装完成但未找到可执行文件。")
    return installed.resolve()


def _build(extra_args: list[str], *, env: dict[str, str] | None = None) -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        str(SPEC),
        *extra_args,
    ]
    print("[build] 执行:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 PySide6 兼容后备端")
    parser.add_argument("--clean",   action="store_true", help="构建前清空 build/ 和 dist/")
    parser.add_argument("--onefile", action="store_true", help="强制单文件模式（覆盖 spec 默认行为）")
    parser.add_argument(
        "--ollama-bundle-dir",
        help="可选：指定要一起打包的 Ollama sidecar 目录（应包含可执行文件，及可选 models/）",
    )
    args = parser.parse_args()

    _ensure_pyinstaller()

    if platform.system() == "Darwin":
        _ensure_pillow()

    if args.clean:
        _clean()

    extra: list[str] = []
    if args.onefile:
        extra += ["--onefile"]

    env = os.environ.copy()
    uv_executable = _ensure_uv()
    env["NOVEL_FORGE_UV_BUNDLE_PATH"] = str(uv_executable)
    print(f"[build] 打包 uv 运行时管理器: {uv_executable}")
    bundle_dir: Path | None = None
    if args.ollama_bundle_dir:
        bundle_dir = Path(args.ollama_bundle_dir).expanduser().resolve()
        if not bundle_dir.exists():
            parser.error(f"--ollama-bundle-dir 不存在: {bundle_dir}")
    elif DEFAULT_OLLAMA_BUNDLE_DIR.exists():
        bundle_dir = DEFAULT_OLLAMA_BUNDLE_DIR.resolve()

    if bundle_dir is not None:
        env["NOVEL_FORGE_OLLAMA_BUNDLE_DIR"] = str(bundle_dir)
        print(f"[build] 打包 Ollama sidecar 目录: {bundle_dir}")
    else:
        env.pop("NOVEL_FORGE_OLLAMA_BUNDLE_DIR", None)
        print("[build] 未提供 Ollama sidecar 目录，仅打包桌面应用本体")

    code = _build(extra, env=env)
    if code == 0:
        out = DIST / ("NIMO.app" if platform.system() == "Darwin" else "NIMO")
        print(f"\n[build] 构建成功！输出路径: {out}")
    else:
        print(f"\n[build] 构建失败，退出码: {code}", file=sys.stderr)
        sys.exit(code)


if __name__ == "__main__":
    main()
