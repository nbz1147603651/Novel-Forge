#!/usr/bin/env python3
"""Compare two PNG screenshots with optional rectangular dynamic masks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QPainter, QPen


@dataclass(frozen=True)
class RectMask:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def _read_image(path: Path) -> QImage:
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"Unable to read image: {path}")
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _parse_mask(value: str) -> RectMask:
    try:
        x, y, width, height = (int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Masks use x,y,width,height") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Mask width and height must be positive")
    return RectMask(x=x, y=y, width=width, height=height)


def _crop(image: QImage, rect: RectMask | None, *, label: str) -> QImage:
    """Return a validated component crop before applying comparison masks."""

    if rect is None:
        return image
    if rect.x < 0 or rect.y < 0 or rect.x + rect.width > image.width() or rect.y + rect.height > image.height():
        raise ValueError(
            f"{label} crop must fit the input image: crop={rect.x},{rect.y},{rect.width},{rect.height}, "
            f"image={image.width()}x{image.height()}"
        )
    return image.copy(rect.x, rect.y, rect.width, rect.height)


def compare(source: QImage, target: QImage, masks: tuple[RectMask, ...]) -> tuple[dict[str, float | int], QImage]:
    if source.size() != target.size():
        raise ValueError(
            f"Dimensions must match: source={source.width()}x{source.height()}, "
            f"target={target.width()}x{target.height()}"
        )

    diff = QImage(source.size(), QImage.Format.Format_ARGB32)
    diff.fill(QColor(255, 255, 255))
    painter = QPainter(diff)
    painter.setPen(QPen(QColor(212, 55, 35)))
    compared_pixels = 0
    changed_pixels = 0
    channel_delta_total = 0
    for y in range(source.height()):
        for x in range(source.width()):
            if any(mask.contains(x, y) for mask in masks):
                continue
            source_pixel = source.pixel(x, y)
            target_pixel = target.pixel(x, y)
            compared_pixels += 1
            deltas = (
                abs(((source_pixel >> 16) & 0xFF) - ((target_pixel >> 16) & 0xFF)),
                abs(((source_pixel >> 8) & 0xFF) - ((target_pixel >> 8) & 0xFF)),
                abs((source_pixel & 0xFF) - (target_pixel & 0xFF)),
                abs(((source_pixel >> 24) & 0xFF) - ((target_pixel >> 24) & 0xFF)),
            )
            delta = sum(deltas)
            channel_delta_total += delta
            if delta > 0:
                changed_pixels += 1
                painter.drawPoint(x, y)
    painter.end()
    return {
        "compared_pixels": compared_pixels,
        "changed_pixels": changed_pixels,
        "changed_pixel_ratio": changed_pixels / compared_pixels if compared_pixels else 0.0,
        "mean_rgba_channel_delta": channel_delta_total / (compared_pixels * 4 * 255)
        if compared_pixels
        else 0.0,
    }, diff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--source-crop", type=_parse_mask, help="Optional x,y,width,height crop in source coordinates.")
    parser.add_argument("--target-crop", type=_parse_mask, help="Optional x,y,width,height crop in target coordinates.")
    parser.add_argument("--mask", action="append", default=[], type=_parse_mask)
    parser.add_argument("--diff-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--max-changed-pixel-ratio", type=float, default=0.0075)
    parser.add_argument("--max-mean-channel-delta", type=float, default=0.0075)
    args = parser.parse_args()

    masks = tuple(args.mask)
    source = _crop(_read_image(args.source), args.source_crop, label="source")
    target = _crop(_read_image(args.target), args.target_crop, label="target")
    metrics, diff = compare(source, target, masks)
    accepted = (
        metrics["changed_pixel_ratio"] <= args.max_changed_pixel_ratio
        and metrics["mean_rgba_channel_delta"] <= args.max_mean_channel_delta
    )
    report = {
        "source": str(args.source),
        "target": str(args.target),
        "source_crop": asdict(args.source_crop) if args.source_crop is not None else None,
        "target_crop": asdict(args.target_crop) if args.target_crop is not None else None,
        "masks": [asdict(mask) for mask in masks],
        "thresholds": {
            "max_changed_pixel_ratio": args.max_changed_pixel_ratio,
            "max_mean_channel_delta": args.max_mean_channel_delta,
        },
        "metrics": metrics,
        "accepted": accepted,
    }
    if args.diff_output is not None:
        args.diff_output.parent.mkdir(parents=True, exist_ok=True)
        if not diff.save(str(args.diff_output), "PNG"):
            raise RuntimeError(f"Unable to save diff image: {args.diff_output}")
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_output is not None:
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
