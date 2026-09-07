#!/usr/bin/env python3
"""Compose two equal-width UI captures into one review image.

This is intentionally a review aid, not a parity metric: use
compare_screenshots.py for the latter once the source and native targets share
the same content rectangle.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter


def load(path: Path) -> QImage:
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"Unable to read image: {path}")
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def main() -> int:
    app = QGuiApplication([])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--left-label", required=True)
    parser.add_argument("--right-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--content-height", type=int, help="Optional shared top-crop height for unequal captures.")
    args = parser.parse_args()

    left, right = load(args.left), load(args.right)
    if left.width() != right.width():
        parser.error(f"Widths must match: left={left.width()}, right={right.width()}")
    content_height = args.content_height or min(left.height(), right.height())
    if content_height <= 0 or content_height > min(left.height(), right.height()):
        parser.error("--content-height must fit both images")

    label_height = 28
    gap = 12
    canvas = QImage(left.width() * 2 + gap, content_height + label_height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#f2f4f7"))
    painter = QPainter(canvas)
    painter.setFont(QFont("Sans Serif", 11, QFont.Weight.DemiBold))
    painter.setPen(QColor("#263746"))
    painter.drawText(8, 19, args.left_label)
    painter.drawText(left.width() + gap + 8, 19, args.right_label)
    painter.drawImage(0, label_height, left.copy(0, 0, left.width(), content_height))
    painter.drawImage(left.width() + gap, label_height, right.copy(0, 0, right.width(), content_height))
    painter.end()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not canvas.save(str(args.output), "PNG"):
        raise RuntimeError(f"Unable to write image: {args.output}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
