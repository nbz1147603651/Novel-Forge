"""Visual regression testing utilities for Novel Forge desktop app.

Provides screenshot capture, baseline management, and pixel-diff comparison
with configurable tolerance threshold.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

# Default tolerance: 2% normalized color delta allowed
DEFAULT_THRESHOLD = 0.02

# Baseline storage directory (relative to tests/desktop/)
BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
VISUAL_STORAGE_ROOT = Path("/tmp/novel_forge_visual_workspace")


def _artifact_dir() -> Path:
    return Path(os.environ.get("NOVEL_FORGE_VISUAL_ARTIFACT_DIR", "/tmp/novel_forge_visual_failures"))


def capture_widget_screenshot(widget: QWidget) -> QImage:
    """Capture a screenshot of a Qt widget and return as QImage.

    Renders the widget at its current size. The widget must be shown
    (or at least have a valid pixmap) for meaningful results.

    Args:
        widget: The Qt widget to capture.

    Returns:
        QImage containing the rendered screenshot.
    """
    pixmap = QPixmap(widget.size())
    pixmap.fill(widget.palette().color(widget.backgroundRole()))
    widget.render(pixmap)
    return pixmap.toImage()


def save_baseline(name: str, image: QImage, baseline_dir: Path | None = None) -> Path:
    """Save an image as a baseline screenshot.

    Args:
        name: Baseline name (e.g. 'dashboard', 'settings').
        image: The QImage to save.
        baseline_dir: Override directory (defaults to BASELINE_DIR).

    Returns:
        Path to the saved baseline file.
    """
    target_dir = baseline_dir or BASELINE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.png"
    image.save(str(path), "PNG")
    return path


def load_baseline(name: str, baseline_dir: Path | None = None) -> QImage | None:
    """Load a baseline screenshot by name.

    Args:
        name: Baseline name (e.g. 'dashboard', 'settings').
        baseline_dir: Override directory (defaults to BASELINE_DIR).

    Returns:
        QImage if baseline exists, None otherwise.
    """
    target_dir = baseline_dir or BASELINE_DIR
    path = target_dir / f"{name}.png"
    if not path.exists():
        return None
    image = QImage()
    image.load(str(path))
    return image


def compute_pixel_diff(baseline: QImage, current: QImage) -> float:
    """Compute the normalized color delta between two images.

    Resizes the current image to match the baseline if dimensions differ,
    then returns the mean absolute RGBA channel delta normalized to 0.0-1.0.
    Qt screenshots frequently vary by small antialiasing/font-rendering
    deltas, so exact pixel equality is too noisy for regression detection.

    Args:
        baseline: The baseline reference image.
        current: The current image to compare.

    Returns:
        Float normalized color delta (0.0 to 1.0).
    """
    # Resize current to match baseline if needed
    if current.size() != baseline.size():
        current = current.scaled(baseline.size())

    width = baseline.width()
    height = baseline.height()
    total_pixels = width * height

    if total_pixels == 0:
        return 0.0

    delta_sum = 0
    for y in range(height):
        for x in range(width):
            base_pixel = baseline.pixel(x, y)
            curr_pixel = current.pixel(x, y)
            delta_sum += abs(((base_pixel >> 16) & 0xFF) - ((curr_pixel >> 16) & 0xFF))
            delta_sum += abs(((base_pixel >> 8) & 0xFF) - ((curr_pixel >> 8) & 0xFF))
            delta_sum += abs((base_pixel & 0xFF) - (curr_pixel & 0xFF))
            delta_sum += abs(((base_pixel >> 24) & 0xFF) - ((curr_pixel >> 24) & 0xFF))

    return delta_sum / (total_pixels * 4 * 255)


def save_visual_failure_artifacts(
    baseline_name: str,
    baseline: QImage,
    current: QImage,
    *,
    artifact_dir: Path | None = None,
) -> Path:
    """Save baseline/current/diff images for diagnosing visual regressions."""
    target_dir = artifact_dir or _artifact_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    baseline.save(str(target_dir / f"{baseline_name}.baseline.png"), "PNG")
    current.save(str(target_dir / f"{baseline_name}.current.png"), "PNG")

    if current.size() != baseline.size():
        current = current.scaled(baseline.size())

    diff_image = QImage(baseline.size(), QImage.Format.Format_ARGB32)
    diff_image.fill(QColor(255, 255, 255))
    painter = QPainter(diff_image)
    painter.setPen(QPen(QColor(220, 0, 0)))
    for y in range(baseline.height()):
        for x in range(baseline.width()):
            if baseline.pixel(x, y) != current.pixel(x, y):
                painter.drawPoint(x, y)
    painter.end()
    diff_image.save(str(target_dir / f"{baseline_name}.diff.png"), "PNG")
    return target_dir


def compute_image_hash(image: QImage) -> str:
    """Compute a quick hash of an image for fingerprinting.

    Args:
        image: The QImage to hash.

    Returns:
        Hex digest string.
    """
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return hashlib.md5(buffer.data()).hexdigest()


def assert_visual_match(
    current: QImage,
    baseline_name: str,
    threshold: float = DEFAULT_THRESHOLD,
    baseline_dir: Path | None = None,
    update_baselines: bool = False,
) -> str:
    """Assert that current rendering matches baseline within threshold.

    Args:
        current: The current rendered image.
        baseline_name: Name of the baseline to compare against.
        threshold: Maximum allowed pixel difference ratio (default 0.02 = 2%).
        baseline_dir: Override directory for baselines.
        update_baselines: If True, save current as new baseline instead of comparing.

    Returns:
        Empty string on success, or error message on failure.

    Raises:
        AssertionError: If difference exceeds threshold and update_baselines is False.
    """
    target_dir = baseline_dir or BASELINE_DIR

    if update_baselines:
        save_baseline(baseline_name, current, target_dir)
        return ""

    baseline = load_baseline(baseline_name, target_dir)
    if baseline is None:
        save_baseline(baseline_name, current, target_dir)
        return f"Baseline '{baseline_name}' did not exist — created automatically."

    diff = compute_pixel_diff(baseline, current)
    if diff > threshold:
        artifact_dir = save_visual_failure_artifacts(baseline_name, baseline, current)
        return (
            f"Visual regression detected for '{baseline_name}': "
            f"{diff:.4f} ({diff:.1%}) normalized color delta exceeds "
            f"threshold {threshold:.4f} ({threshold:.1%}). "
            f"Artifacts saved to {artifact_dir}."
        )

    return ""
