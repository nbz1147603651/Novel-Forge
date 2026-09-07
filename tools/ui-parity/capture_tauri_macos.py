#!/usr/bin/env python3
"""Capture a deterministic Tauri/macOS content frame for UI-parity evidence.

The runner launches a locally built Tauri binary with a restricted fixture
query, waits for its native window, waits until repeated window captures are
byte-stable, then crops the operating-system title bar and normalises Retina
pixels back to the named content viewport.  It never starts a Provider or
opens a user project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import Qt
from PySide6.QtGui import QColorSpace, QImage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = PROJECT_ROOT / "clients/nimo-desktop/src-tauri/target/debug/nimo_ui_parity"
FRONTEND_DIST = PROJECT_ROOT / "clients/nimo-desktop/dist"
WINDOW_LOOKUP = Path(__file__).with_name("tauri_window_id.swift")
WINDOW_LOOKUP_BINARY = Path("/tmp/nimo-ui-parity-tauri-window-id")
THEMES = {"narrative_ember", "ink_jade", "ink_amethyst", "stillwater", "twilight_ink"}
PAGES = {"dashboard", "projects", "workflow", "settings", "chapter_studio", "voice_studio"}
MAX_DYNAMIC_PIXEL_RATIO = 0.00008


def frontend_bundle_freshness(binary: Path, *, frontend_dist: Path = FRONTEND_DIST) -> dict[str, object]:
    """Reject a native capture when its embedded Vite bundle is older than ``dist``.

    Tauri embeds the generated frontend during Cargo's build step.  A native
    executable can therefore start successfully while still rendering a stale
    (or now missing) hashed JavaScript asset.  That failure used to look like a
    white reader pane and could be mistaken for a React layout regression.

    The check intentionally compares generated assets, rather than TypeScript
    sources: a capture proves the currently built ``dist`` fixture, and callers
    must run Vite before asking Cargo to embed it.  ``build.rs`` recursively
    watches the same directory, so a newer asset requires a newer executable.
    """

    if not frontend_dist.is_dir():
        raise RuntimeError(
            f"Vite dist directory is missing: {frontend_dist}. "
            "Run `pnpm ui:build` before building the Tauri capture binary."
        )
    assets = [path for path in frontend_dist.rglob("*") if path.is_file()]
    if not assets:
        raise RuntimeError(
            f"Vite dist directory has no files: {frontend_dist}. "
            "Run `pnpm ui:build` before building the Tauri capture binary."
        )
    newest_asset = max(assets, key=lambda path: path.stat().st_mtime_ns)
    binary_mtime_ns = binary.stat().st_mtime_ns
    newest_asset_mtime_ns = newest_asset.stat().st_mtime_ns
    result: dict[str, object] = {
        "binary_mtime_ns": binary_mtime_ns,
        "dist_asset_count": len(assets),
        "newest_dist_asset": str(newest_asset.relative_to(frontend_dist)),
        "newest_dist_asset_mtime_ns": newest_asset_mtime_ns,
    }
    if binary_mtime_ns < newest_asset_mtime_ns:
        raise RuntimeError(
            "Tauri capture binary is older than the generated Vite bundle "
            f"({binary} < {newest_asset}). Build a fresh fixture with "
            "`pnpm ui:build && (cd clients/nimo-desktop/src-tauri && cargo build)` "
            "before capturing; stale embedded assets can render a white WebView."
        )
    return result


def changed_pixel_count(previous: QImage, current: QImage) -> int | None:
    """Return the number of changed RGBA pixels, or None when frames differ in size.

    Byte identity is normally the strongest capture-settlement signal. A focused
    native text field, however, blinks a caret after the WebView has otherwise
    settled. Treating that tiny, local change as a loading frame makes a valid
    source-autofocus fixture impossible to capture. This helper deliberately
    measures the exception instead of disabling stability checks altogether.
    """

    before = previous.convertToFormat(QImage.Format.Format_RGBA8888)
    after = current.convertToFormat(QImage.Format.Format_RGBA8888)
    if before.size() != after.size():
        return None
    before_bytes = before.constBits().tobytes()
    after_bytes = after.constBits().tobytes()
    changed = 0
    for offset in range(0, len(before_bytes), 4):
        if (
            before_bytes[offset] != after_bytes[offset]
            or before_bytes[offset + 1] != after_bytes[offset + 1]
            or before_bytes[offset + 2] != after_bytes[offset + 2]
            or before_bytes[offset + 3] != after_bytes[offset + 3]
        ):
            changed += 1
    return changed


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Viewport uses WIDTHxHEIGHT, for example 1440x900") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Viewport dimensions must be positive")
    return width, height


def fixture_query(args: argparse.Namespace) -> str:
    values = {
        "__nimo_ui_parity": "1",
        "page": args.page,
        "theme": args.theme,
    }
    if args.reader is not None:
        values["reader"] = args.reader
    if args.settings_section is not None:
        values["settings_section"] = args.settings_section
    if args.chapter_studio_state is not None:
        values["chapter_state"] = args.chapter_studio_state
    if args.chapter_dialog is not None:
        values["chapter_dialog"] = args.chapter_dialog
    if args.workflow_dialog is not None:
        values["workflow_dialog"] = args.workflow_dialog
    if args.voice_dialog is not None:
        values["voice_dialog"] = args.voice_dialog
    if args.voice_studio_state is not None:
        values["voice_state"] = args.voice_studio_state
    if args.voice_studio_tab is not None:
        values["voice_tab"] = args.voice_studio_tab
    if args.rail == "collapsed":
        values["rail"] = "collapsed"
    return "?" + urlencode(values)


def ensure_window_lookup_binary(*, swift_sdk: Path | None) -> Path:
    """Compile the CoreGraphics lookup once instead of once per poll."""

    if WINDOW_LOOKUP_BINARY.exists() and WINDOW_LOOKUP_BINARY.stat().st_mtime >= WINDOW_LOOKUP.stat().st_mtime:
        return WINDOW_LOOKUP_BINARY
    command = ["swiftc"]
    if swift_sdk is not None:
        command.extend(["-sdk", str(swift_sdk), "-module-cache-path", "/tmp/nimo-ui-parity-swift-module-cache"])
    command.extend([str(WINDOW_LOOKUP), "-o", str(WINDOW_LOOKUP_BINARY)])
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Swift compilation failed"
        raise RuntimeError(f"Unable to compile native-window lookup helper: {detail}")
    return WINDOW_LOOKUP_BINARY


def active_window(
    binary: Path,
    *,
    lookup_binary: Path,
    process_id: int,
    title: str | None,
) -> dict[str, object] | None:
    """Find only the window belonging to this runner's temporary process."""

    command = [str(lookup_binary), binary.name, title or "", str(process_id)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def take_window_capture(window: dict[str, object], destination: Path) -> str:
    """Capture by native window id, then fall back to its on-screen bounds."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    commands = (
        ("window-id", ["screencapture", "-x", "-o", f"-l{int(window['windowId'])}", str(destination)]),
        (
            "window-bounds",
            [
                "screencapture",
                "-x",
                "-o",
                "-R",
                f"{int(window['x'])},{int(window['y'])},{int(window['width'])},{int(window['height'])}",
                str(destination),
            ],
        ),
    )
    errors: list[str] = []
    for mode, command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
            return mode
        errors.append(f"{mode}: {result.stderr.strip() or 'no image was created'}")
        destination.unlink(missing_ok=True)
    raise RuntimeError(
        "macOS screencapture could not create an image for "
        f"window {window['windowId']} ({window['owner']!r}, title={window['title']!r}); "
        f"grant Screen Recording permission to the terminal/Codex host. {'; '.join(errors)}"
    )


def wait_for_stable_capture(
    binary: Path,
    *,
    lookup_binary: Path,
    process_id: int,
    title: str | None,
    temporary_capture: Path,
    timeout_seconds: float,
) -> tuple[dict[str, object], Path, str, str]:
    deadline = time.monotonic() + timeout_seconds
    last_digest: str | None = None
    last_image: QImage | None = None
    last_capture_error: RuntimeError | None = None
    consecutive_matches = 0
    while time.monotonic() < deadline:
        window = active_window(
            binary,
            lookup_binary=lookup_binary,
            process_id=process_id,
            title=title,
        )
        if window is None:
            time.sleep(0.12)
            continue
        try:
            capture_mode = take_window_capture(window, temporary_capture)
        except RuntimeError as error:
            # Decoration/fullscreen changes can briefly expose a CoreGraphics
            # window record before it is captureable. Re-query instead of
            # mistaking that transient state for a revoked Screen Recording
            # permission.
            last_capture_error = error
            time.sleep(0.18)
            continue
        digest = hashlib.sha256(temporary_capture.read_bytes()).hexdigest()
        current_image = QImage(str(temporary_capture))
        if current_image.isNull():
            last_capture_error = RuntimeError("macOS screencapture wrote an unreadable image")
            time.sleep(0.18)
            continue
        if digest == last_digest:
            consecutive_matches += 1
            stability = "byte-identical"
        elif last_image is not None:
            changed = changed_pixel_count(last_image, current_image)
            pixel_count = current_image.width() * current_image.height()
            if changed is not None and changed <= max(8, round(pixel_count * MAX_DYNAMIC_PIXEL_RATIO)):
                consecutive_matches += 1
                stability = "focus-caret-tolerant"
            else:
                consecutive_matches = 0
                stability = "unstable"
        else:
            consecutive_matches = 0
            stability = "unstable"
        last_digest = digest
        last_image = current_image
        if consecutive_matches >= 2:
            return window, temporary_capture, capture_mode, stability
        time.sleep(0.18)
    detail = f" Last capture error: {last_capture_error}" if last_capture_error is not None else ""
    raise TimeoutError(f"Tauri window did not become capture-stable before the timeout.{detail}")


def crop_content_frame(raw_path: Path, destination: Path, *, viewport: tuple[int, int]) -> dict[str, int]:
    raw = QImage(str(raw_path))
    if raw.isNull():
        raise RuntimeError(f"Unable to read macOS capture: {raw_path}")
    width, height = viewport
    scale = max(1, round(raw.width() / width))
    target_width, target_height = width * scale, height * scale
    if raw.width() < target_width or raw.height() < target_height:
        raise RuntimeError(
            f"Native capture is smaller than requested content viewport: "
            f"raw={raw.width()}x{raw.height()}, viewport={width}x{height}, scale={scale}"
        )
    # Native macOS capture includes the OS title bar, whereas the PySide6
    # fixture uses QWidget.render() and contains client pixels only.  Keep the
    # bottom-aligned content area, dropping title/chrome above it.
    origin_x = (raw.width() - target_width) // 2
    origin_y = raw.height() - target_height
    content = raw.copy(origin_x, origin_y, target_width, target_height)
    if scale != 1:
        content = content.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # `screencapture` can preserve the host display profile (for example
    # Color LCD / Display P3), while PySide6 QWidget.render() writes untagged
    # sRGB-like pixels. Normalise the native frame before comparing byte-level
    # channels; otherwise a visually identical surface differs everywhere.
    content.convertToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not content.save(str(destination), "PNG"):
        raise RuntimeError(f"Unable to save normalised Tauri frame: {destination}")
    return {
        "raw_width": raw.width(),
        "raw_height": raw.height(),
        "scale": scale,
        "crop_x": origin_x,
        "crop_y": origin_y,
        "crop_width": target_width,
        "crop_height": target_height,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument(
        "--swift-sdk",
        type=Path,
        help="Optional macOS SDK path for the Swift window locator when the selected CLT SDK mismatches Swift.",
    )
    parser.add_argument("--page", choices=sorted(PAGES), required=True)
    parser.add_argument(
        "--plain-startup",
        action="store_true",
        help="Diagnostic only: omit the parity query and capture the normal startup route.",
    )
    parser.add_argument("--theme", choices=sorted(THEMES), default="narrative_ember")
    parser.add_argument("--reader", choices=("long",))
    parser.add_argument("--settings-section", choices=("creative-temperature", "model-routing"))
    parser.add_argument("--chapter-studio-state", choices=("prepared", "running", "checkpoint"))
    parser.add_argument(
        "--chapter-dialog",
        choices=("checkpoint", "export", "book-audit", "clean", "project-switch", "error-log"),
        help="Open a source-shaped Chapter Studio overlay over its required named page state.",
    )
    parser.add_argument(
        "--workflow-dialog",
        choices=("floating-stream", "error-log", "cancel"),
        help="Open a source-sized workflow overlay over its required named page state.",
    )
    parser.add_argument(
        "--voice-dialog",
        choices=("clone-provider-file-id",),
        help="Open a source-sized Voice Studio overlay over its required named page state.",
    )
    parser.add_argument(
        "--voice-studio-state",
        choices=("configured",),
        help="Capture the configured Voice Studio cast read model.",
    )
    parser.add_argument(
        "--voice-studio-tab",
        choices=("team", "script", "room", "post", "settings"),
        help="Pin a Voice Studio tab for a same-state native comparison.",
    )
    parser.add_argument("--rail", choices=("expanded", "collapsed"), default="expanded")
    parser.add_argument(
        "--window-mode",
        choices=("client", "decorated"),
        default="client",
        help="Use an exact title-bar-free client fixture or the native decorated window (default: client).",
    )
    parser.add_argument("--title", help="Optional native title used to identify the capture window.")
    parser.add_argument("--viewport", type=parse_viewport, default=(1440, 900))
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=5.0,
        help="Minimum frontend settle time before capture-stability polling (default: 5.0).",
    )
    parser.add_argument("--timeout-seconds", type=float, default=18.0)
    parser.add_argument("--keep-raw", action="store_true")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        parser.error("capture_tauri_macos.py only runs on macOS")
    binary = args.binary.resolve()
    if not binary.exists():
        parser.error(
            f"Tauri binary is missing: {binary}. Build it with "
            "`pnpm ui:build && (cd clients/nimo-desktop/src-tauri && cargo build)` first."
        )
    try:
        bundle_freshness = frontend_bundle_freshness(binary)
    except RuntimeError as error:
        parser.error(str(error))
    if args.settings_section is not None and args.page != "settings":
        parser.error("--settings-section requires --page settings")
    if args.reader is not None and args.page != "projects":
        parser.error("--reader requires --page projects")
    if args.chapter_studio_state is not None and args.page != "chapter_studio":
        parser.error("--chapter-studio-state requires --page chapter_studio")
    if args.chapter_dialog is not None and args.page != "chapter_studio":
        parser.error("--chapter-dialog requires --page chapter_studio")
    if args.chapter_dialog == "checkpoint" and args.chapter_studio_state != "checkpoint":
        parser.error("--chapter-dialog checkpoint requires --chapter-studio-state checkpoint")
    if args.chapter_dialog in {"export", "book-audit", "clean", "error-log"} and args.chapter_studio_state != "prepared":
        parser.error("--chapter-dialog export/book-audit/clean/error-log requires --chapter-studio-state prepared")
    if args.chapter_dialog == "project-switch" and args.chapter_studio_state != "running":
        parser.error("--chapter-dialog project-switch requires --chapter-studio-state running")
    if args.workflow_dialog is not None and args.page != "workflow":
        parser.error("--workflow-dialog requires --page workflow")
    if args.voice_dialog is not None and args.page != "voice_studio":
        parser.error("--voice-dialog requires --page voice_studio")
    if args.voice_studio_state is not None and args.page != "voice_studio":
        parser.error("--voice-studio-state requires --page voice_studio")
    if args.voice_studio_tab is not None and args.page != "voice_studio":
        parser.error("--voice-studio-tab requires --page voice_studio")
    if args.settle_seconds < 0:
        parser.error("--settle-seconds cannot be negative")
    swift_sdk = args.swift_sdk.resolve() if args.swift_sdk is not None else None
    if swift_sdk is not None and not swift_sdk.exists():
        parser.error(f"Swift SDK does not exist: {swift_sdk}")
    lookup_binary = ensure_window_lookup_binary(swift_sdk=swift_sdk)

    query = None if args.plain_startup else fixture_query(args)
    width, height = args.viewport
    environment = os.environ.copy()
    if query is not None:
        environment["NIMO_UI_PARITY_QUERY"] = query
    if args.window_mode == "client":
        # The native title bar consumes part of Tauri's configured outer-window
        # height. Ask the test-only Rust hook to compensate before first paint
        # so captured WebView content is exactly the same 1440x900-style
        # fixture as PySide6's QWidget.render() source image.
        environment["NIMO_UI_PARITY_CAPTURE_VIEWPORT"] = f"{width}x{height}"
    process = subprocess.Popen([str(binary)], cwd=PROJECT_ROOT, env=environment)
    state_parts = [args.page]
    if args.reader is not None:
        state_parts.append(f"reader-{args.reader}")
    if args.settings_section is not None:
        state_parts.append(f"section-{args.settings_section}")
    if args.chapter_studio_state is not None:
        state_parts.append(f"chapter-{args.chapter_studio_state}")
    if args.chapter_dialog is not None:
        state_parts.append(f"dialog-{args.chapter_dialog}")
    if args.workflow_dialog is not None:
        state_parts.append(f"dialog-{args.workflow_dialog}")
    if args.voice_dialog is not None:
        state_parts.append(f"voice-dialog-{args.voice_dialog}")
    if args.voice_studio_state is not None:
        state_parts.append(f"voice-{args.voice_studio_state}")
    if args.voice_studio_tab is not None:
        state_parts.append(f"voice-tab-{args.voice_studio_tab}")
    state = "--".join(state_parts)
    output = args.output_dir / f"tauri--{args.theme}--{state}--{width}x{height}.png"
    raw = output.with_suffix(".raw.png")
    try:
        # A native WebView can report three byte-identical white frames before
        # its bundled JavaScript has mounted. Let the fixture route, mock data,
        # and first paint settle before the existing capture-stability check.
        time.sleep(args.settle_seconds)
        window, stable_raw, capture_mode, stability = wait_for_stable_capture(
            binary,
            lookup_binary=lookup_binary,
            process_id=process.pid,
            title=args.title or None,
            temporary_capture=raw,
            timeout_seconds=args.timeout_seconds,
        )
        crop = crop_content_frame(stable_raw, output, viewport=args.viewport)
        manifest = {
            "source": "Tauri native WebView capture",
            "binary": str(binary),
            "fixture_query": query,
            "window_mode": args.window_mode,
            "window": window,
            "capture_mode": capture_mode,
            "stability": stability,
            "viewport": {"width": width, "height": height},
            "normalisation": crop,
            "bundle_freshness": bundle_freshness,
            "file": output.name,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
        manifest_path = args.output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if raw.exists() and not args.keep_raw:
            raw.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
