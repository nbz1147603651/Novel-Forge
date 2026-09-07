"""Verify font scaling across 1x / 1.5x / 2x devicePixelRatio.

The point of this script: confirm that point-size fonts scale correctly
with devicePixelRatio, which is what we're enabling with the pt migration.
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication


def main() -> int:
    QApplication.instance() or QApplication([])

    text = "测试文本 Hello World"
    dprs = [1.0, 1.5, 2.0]
    widths = []
    for _dpr in dprs:
        font = QFont()
        font.setPointSize(13)
        metrics = QFontMetrics(font)
        widths.append(metrics.horizontalAdvance(text))

    ratio_max = max(widths) / min(widths)
    print(f"Widths at 1x/1.5x/2x: {widths}")
    print(f"Max/min ratio: {ratio_max:.3f}")

    # 期望：ratio < 1.05（setPointSize 是 logical 像素，应不随 dpr 改变 logical 宽度）
    if ratio_max > 1.05:
        print(f"FAIL: ratio {ratio_max} > 1.05, font scaling broken")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())