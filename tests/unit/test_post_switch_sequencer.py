"""Regression tests for post-switch failure reporting."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject

from novel_forge.desktop.components import toast as toast_module
from novel_forge.desktop.window._post_switch import _PostSwitchSequencer


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._post_switch_sequencer: _PostSwitchSequencer | None = None

    def _post_switch_current(self, _page_id: str, _generation: int) -> bool:
        return True


def test_post_switch_failure_emits_warning_toast(monkeypatch: Any, qapp: Any) -> None:
    owner = _Owner()
    sequencer = _PostSwitchSequencer(owner, "projects", 1, None)  # type: ignore[arg-type]
    owner._post_switch_sequencer = sequencer
    toast_calls: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        toast_module,
        "show_toast",
        lambda message, *, variant, duration: toast_calls.append((message, variant, duration)),
    )

    def _fail() -> None:
        raise RuntimeError("workspace bind failed")

    sequencer._do_workspace_bind = _fail  # type: ignore[method-assign]
    sequencer._on_timeout()

    assert toast_calls == [
        ("页面切换后处理失败（workspace bind），部分数据可能未加载", "warning", 5000)
    ]
    assert owner._post_switch_sequencer is None
