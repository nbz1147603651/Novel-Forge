"""Regression tests for the two intentionally distinct signal coalescers."""

from __future__ import annotations

from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from novel_forge.desktop.components.signal_coalescing import (
    LatestValueCoalescer,
    TrailingDebounce,
)


def test_trailing_debounce_waits_for_the_last_trigger(qtbot: QtBot) -> None:
    owner = QObject()
    calls: list[str] = []
    debounce = TrailingDebounce(owner, 30, lambda: calls.append("flushed"))

    debounce.trigger()
    qtbot.wait(15)
    debounce.trigger()
    qtbot.wait(20)
    assert calls == []
    qtbot.waitUntil(lambda: calls == ["flushed"], timeout=120)


def test_latest_value_coalescer_keeps_the_newest_value(qtbot: QtBot) -> None:
    owner = QObject()
    values: list[int] = []
    coalescer = LatestValueCoalescer(owner, 30, values.append)

    coalescer.submit(1)
    coalescer.submit(2)
    coalescer.submit(3)

    qtbot.waitUntil(lambda: values == [3], timeout=120)
    assert not coalescer.is_active()


def test_latest_value_coalescer_clear_discards_pending_value(qtbot: QtBot) -> None:
    owner = QObject()
    values: list[str] = []
    coalescer = LatestValueCoalescer(owner, 20, values.append)

    coalescer.submit("stale")
    coalescer.clear()
    qtbot.wait(40)

    assert values == []
