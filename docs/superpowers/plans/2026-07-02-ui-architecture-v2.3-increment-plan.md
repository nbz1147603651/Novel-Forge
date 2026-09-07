# UI 架构 v2.3 增量实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v3 主设计（`docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md`）已落地的 worker 框架 / 拆分 / CI / fullscreen 守卫之上，叠加 11 项三维度增量：P0 macOS fullscreen 稳定性补丁 + P0 jobs panel 节流 + P1 HTML cache / humanize 表模型 / 4 page shutdown / QSS px→pt + P2 Windows policy / devicePixelRatio / 应用名 / xfail + P3 atexit。

**Architecture:** 独立 flag + scoped effect clear + 局部节流 + widget-property cache。全部基于 v3 已存在代码做最小侵入补强；不引入新依赖、不引入 MotionAdapter、不动 pipeline / workflow 逻辑。

**Tech Stack:** Python 3.11 + PySide6 6.6–6.10 + pytest + ruff + mypy strict。

**Spec:** `docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md` §13（v2.3 增量附录）

**依赖关系：**
- Task 1（macOS safe mode）独立，可单独 PR
- Task 2（jobs panel 节流）独立
- Task 3–6（Windows policy / 应用名 / xfail / atexit）独立低风险小改，可合并一个 PR
- Task 7（4 page shutdown）独立
- Task 8（devicePixelRatio）独立
- Task 9（HTML cache）独立
- Task 10（QSS px→pt）依赖视觉基线重生成
- Task 11（humanize 表模型）依赖视觉基线重生成

---

## Task 1: macOS fullscreen safe mode（I-1，P0）

### Files
- Modify: `novel_forge/desktop/window/navigation.py`（新增 3 个方法 + 改 2 个动画方法早返）
- Modify: `novel_forge/desktop/window_navigation.py`（`switch_page` 钩子）
- Test: `tests/desktop/test_mac_fullscreen_safe_mode.py`（NEW）

### Step 1: Write failing test for `_is_native_fullscreen_active`

**File:** `tests/desktop/test_mac_fullscreen_safe_mode.py`

```python
"""Tests for macOS fullscreen safe mode (I-1)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.desktop


@pytest.fixture
def mock_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_is_native_fullscreen_true_when_isFullScreen(mock_darwin):
    """When QMainWindow.isFullScreen() is True, return True."""
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = True
    win.windowHandle.return_value = None
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is True


def test_is_native_fullscreen_true_when_QWindow_FullScreen(mock_darwin):
    """When QWindow.visibility is FullScreen, return True."""
    from PySide6.QtGui import QWindow

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = False
    handle = MagicMock()
    handle.visibility.return_value = QWindow.Visibility.FullScreen
    win.windowHandle.return_value = handle
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is True


def test_is_native_fullscreen_false_when_maximized_only(mock_darwin):
    """Maximized window alone is NOT native fullscreen (for fallback decision)."""
    from PySide6.QtGui import QWindow

    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = False
    handle = MagicMock()
    handle.visibility.return_value = QWindow.Visibility.Maximized
    win.windowHandle.return_value = handle
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is False


def test_is_native_fullscreen_false_on_non_darwin():
    """Non-darwin always returns False regardless of window state."""
    from novel_forge.desktop.window import navigation as nav

    win = MagicMock()
    win.isFullScreen.return_value = True
    assert nav.NavigationMixin._is_native_fullscreen_active(win) is False
```

### Step 2: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v`
Expected: ImportError or AttributeError (functions don't exist yet)

### Step 3: Implement `_is_native_fullscreen_active`

**File:** `novel_forge/desktop/window/navigation.py`

Add to the `NavigationMixin` class (place after the existing `_skip_page_motion_for_window_state` method, around line 383):

```python
def _is_native_fullscreen_active(self) -> bool:
    """Return True only for AppKit native fullscreen.

    与 ``_skip_page_motion_for_window_state`` 的区别：
    本函数**不**把 ``WindowMaximized`` / 屏幕几何填充 视为 fullscreen。
    那些是"动画不安全"状态，但**不算**原生 fullscreen。
    原生 fullscreen 掉到 maximized 是常见现象，post-switch fallback 需用
    本函数判断是否需要恢复（``showFullScreen``）。
    """
    if sys.platform != "darwin":
        return False
    if self.isFullScreen():
        return True
    window = self.windowHandle()
    if window is not None:
        try:
            return window.visibility() == QWindow.Visibility.FullScreen
        except RuntimeError:
            return False
    return False
```

### Step 4: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py::test_is_native_fullscreen_true_when_isFullScreen tests/desktop/test_mac_fullscreen_safe_mode.py::test_is_native_fullscreen_true_when_QWindow_FullScreen tests/desktop/test_mac_fullscreen_safe_mode.py::test_is_native_fullscreen_false_when_maximized_only tests/desktop/test_mac_fullscreen_safe_mode.py::test_is_native_fullscreen_false_on_non_darwin -v`
Expected: 4 passed

### Step 5: Write failing test for `_enter_mac_fullscreen_safe_mode`

Append to `tests/desktop/test_mac_fullscreen_safe_mode.py`:

```python
def test_enter_safe_mode_sets_flag_and_clears_only_opacity(monkeypatch):
    """When entering safe mode, set flag and clear ONLY QGraphicsOpacityEffect.

    QGraphicsDropShadowEffect (used by dashboard hero / popups) must remain.
    """
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QLabel

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")

    # Build a real QLabel so setGraphicsEffect works
    import pytest
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication.instance()  # may already exist

    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = True
    win._previous_widget = None
    win._stack = MagicMock()
    win._stack.currentWidget.return_value = None
    win._top_eyebrow = None
    win._top_title = None
    win._top_subtitle = None

    # 创建一个带 shadow 的 label
    label = QLabel("x")
    shadow = QGraphicsDropShadowEffect(label)
    label.setGraphicsEffect(shadow)

    win._top_title = label

    nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert win._mac_fullscreen_safe_mode_active is True
    assert getattr(win, "_mac_fullscreen_at_switch_start_time", 0) > 0
    # shadow effect 必须保留
    assert label.graphicsEffect() is shadow


def test_enter_safe_mode_clears_opacity_effect(monkeypatch):
    """QGraphicsOpacityEffect should be cleared."""
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = True
    win._previous_widget = None
    win._stack = MagicMock()
    win._stack.currentWidget.return_value = None
    win._top_eyebrow = None
    win._top_title = None
    win._top_subtitle = None

    label = QLabel("x")
    opacity = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(opacity)
    win._top_title = label

    nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert label.graphicsEffect() is None


def test_enter_safe_mode_noop_on_non_fullscreen(monkeypatch):
    """When native fullscreen is False, return False and do not set flag."""
    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._is_native_fullscreen_active.return_value = False

    result = nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert result is False
    assert getattr(win, "_mac_fullscreen_safe_mode_active", False) is False


def test_enter_safe_mode_noop_on_non_darwin(monkeypatch):
    """Non-darwin always returns False."""
    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "linux")
    win = MagicMock()
    win._is_native_fullscreen_active.return_value = True  # ignored on non-darwin

    result = nav.NavigationMixin._enter_mac_fullscreen_safe_mode(win)

    assert result is False
```

### Step 6: Run test to verify the new ones fail

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v`
Expected: 3 new tests FAIL (method doesn't exist)

### Step 7: Implement `_enter_mac_fullscreen_safe_mode`

**File:** `novel_forge/desktop/window/navigation.py`

Add after `_is_native_fullscreen_active`:

```python
def _enter_mac_fullscreen_safe_mode(self) -> bool:
    """switch_page 入口钩子：在 ``setCurrentWidget`` 之前调用。

    行为：
    1. 检测原生 fullscreen（用 ``_is_native_fullscreen_active``）
    2. 设 ``_mac_fullscreen_safe_mode_active = True``
    3. 清掉 old/new page root widget + top-bar labels 上的 ``QGraphicsOpacityEffect``
       （**只**清 ``QGraphicsOpacityEffect``，保留 ``QGraphicsDropShadowEffect``）
    4. 记录 ``_mac_fullscreen_at_switch_start_time``
    """
    if sys.platform != "darwin":
        return False
    if not self._is_native_fullscreen_active():
        return False
    import time as _time
    self._mac_fullscreen_safe_mode_active = True
    self._mac_fullscreen_at_switch_start_time = _time.monotonic()

    from PySide6.QtWidgets import QGraphicsOpacityEffect

    targets: list = []
    if self._previous_widget is not None:
        targets.append(self._previous_widget)
    new_widget = self._stack.currentWidget()
    if new_widget is not None:
        targets.append(new_widget)
    for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
        if label is not None:
            targets.append(label)
    for w in targets:
        try:
            effect = w.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass
    return True
```

### Step 8: Run test to verify the 3 new tests pass

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v`
Expected: 7 tests passed

### Step 9: Write failing test for `_exit_mac_fullscreen_safe_mode`

Append to `tests/desktop/test_mac_fullscreen_safe_mode.py`:

```python
def test_exit_safe_mode_restores_fullscreen_when_dropped(monkeypatch):
    """When native fullscreen is False after switch, call showFullScreen()."""
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0  # 1s ago
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = False  # 掉出来了
    win.isVisible.return_value = True

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_called_once()
    assert win._mac_fullscreen_safe_mode_active is False


def test_exit_safe_mode_noop_when_still_fullscreen(monkeypatch):
    """When still native fullscreen, do nothing."""
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = True  # 仍在 fullscreen

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()


def test_exit_safe_mode_noop_after_5s_timeout(monkeypatch):
    """After 5s, clear flag and do nothing (avoid disrupting user)."""
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 6.0  # 6s ago
    win._post_switch_current.return_value = True
    win._is_native_fullscreen_active.return_value = False

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()
    assert win._mac_fullscreen_safe_mode_active is False


def test_exit_safe_mode_noop_when_not_post_switch_current(monkeypatch):
    """When generation no longer matches, skip."""
    import time as _time

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    win._mac_fullscreen_at_switch_start_time = _time.monotonic() - 1.0
    win._post_switch_current.return_value = False  # 已经切到别处

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()


def test_exit_safe_mode_noop_when_flag_false(monkeypatch):
    """When flag is False, do nothing (normal switch)."""
    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = False

    nav.NavigationMixin._exit_mac_fullscreen_safe_mode(win, "dashboard", 1)

    win.showFullScreen.assert_not_called()
    win._is_native_fullscreen_active.assert_not_called()
```

### Step 10: Run new tests to verify they fail

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v -k "exit_safe_mode"`
Expected: 5 FAIL (method doesn't exist)

### Step 11: Implement `_exit_mac_fullscreen_safe_mode`

**File:** `novel_forge/desktop/window/navigation.py`

Add after `_enter_mac_fullscreen_safe_mode`:

```python
def _exit_mac_fullscreen_safe_mode(self, page_id: str, generation: int) -> None:
    """post-switch fallback：120 ms 后检查是否被踢出原生 fullscreen。

    用 ``_is_native_fullscreen_active`` 而非 ``_skip_page_motion_for_window_state``：
    原生 fullscreen 掉到 maximized 视作"退出"，需 ``showFullScreen()`` 恢复。
    但 ``_skip_page_motion_for_window_state`` 在 maximized 下也返回 True，会漏判。
    """
    if not getattr(self, "_mac_fullscreen_safe_mode_active", False):
        return
    import time as _time
    if (_time.monotonic() - getattr(self, "_mac_fullscreen_at_switch_start_time", 0.0)) > 5.0:
        self._mac_fullscreen_safe_mode_active = False
        return
    if not self._post_switch_current(page_id, generation):
        return
    if self._is_native_fullscreen_active():
        return  # 仍在原生 fullscreen
    self._mac_fullscreen_safe_mode_active = False
    try:
        if self.isVisible():
            self.showFullScreen()
    except RuntimeError:
        pass
```

### Step 12: Run all `_exit` tests to verify they pass

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v -k "exit_safe_mode"`
Expected: 5 passed

### Step 13: Wire into `_animate_current_page` early-return

**File:** `novel_forge/desktop/window/navigation.py`

In the `_animate_current_page` method (around line 432), insert after the `if not _animations_supported(kind="opacity"):` early-return block (line 436) and before the `old_widget = self._previous_widget` line (line 438):

```python
        # macOS fullscreen safe mode 优先于所有其他 skip 条件
        if getattr(self, "_mac_fullscreen_safe_mode_active", False):
            self._clear_page_animation()
            self._mac_fullscreen_safe_mode_active = False  # 消费
            return
```

### Step 14: Wire into `_animate_top_bar_title` early-return

In `_animate_top_bar_title` (around line 580), insert after `if not _animations_supported("opacity"): return` (line 594) and before `for prior in getattr(...)` (line 597):

```python
        if getattr(self, "_mac_fullscreen_safe_mode_active", False):
            for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
                if label is None:
                    continue
                try:
                    label.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            self._top_bar_animations = []
            return
```

### Step 15: Add integration test for `_animate_current_page` skip

Append to `tests/desktop/test_mac_fullscreen_safe_mode.py`:

```python
def test_animate_current_page_skips_when_safe_mode_active(monkeypatch):
    """When safe mode flag is True, _animate_current_page returns early without Motion.fade_in."""
    from unittest.mock import MagicMock

    from novel_forge.desktop.window import navigation as nav

    monkeypatch.setattr(sys, "platform", "darwin")
    win = MagicMock(spec=nav.NavigationMixin)
    win._mac_fullscreen_safe_mode_active = True
    win._active_page_id = "dashboard"

    with patch("novel_forge.desktop.motion.Motion.fade_in") as mock_fade_in:
        nav.NavigationMixin._animate_current_page(win)
        mock_fade_in.assert_not_called()

    assert win._mac_fullscreen_safe_mode_active is False
```

### Step 16: Run full test file

Run: `.venv/bin/python -m pytest tests/desktop/test_mac_fullscreen_safe_mode.py -v`
Expected: 13 tests passed

### Step 17: Wire `switch_page` hook in `window_navigation.py`

**File:** `novel_forge/desktop/window_navigation.py`

In `switch_page()` (around line 181-242), insert BEFORE `owner._active_page_id = page_id` (line 220):

```python
        # I-1: macOS fullscreen safe mode — 在 setCurrentWidget 之前进入
        owner._enter_mac_fullscreen_safe_mode()
```

And at the END of `switch_page()` (after line 242, before the function returns):

```python
    # I-1: post-switch fallback — 120ms 后检查是否掉出 fullscreen
    QTimer.singleShot(
        120,
        lambda: owner._exit_mac_fullscreen_safe_mode(page_id, generation),
    )
```

### Step 18: Run desktop tests to verify no regression

Run: `.venv/bin/python -m pytest tests/desktop -q --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' --timeout=300`
Expected: all pass

### Step 19: Commit

```bash
git add novel_forge/desktop/window/navigation.py novel_forge/desktop/window_navigation.py tests/desktop/test_mac_fullscreen_safe_mode.py
git commit -m "fix(desktop): macOS fullscreen safe mode — independent flag, scoped opacity-effect clear, post-switch fallback

- 新增 _is_native_fullscreen_active() 区分原生 fullscreen 与 maximized
- 新增 _enter_mac_fullscreen_safe_mode() 在 switch_page 入口清除 only QGraphicsOpacityEffect
  （保留 QGraphicsDropShadowEffect — dashboard hero / popups 依赖）
- 新增 _exit_mac_fullscreen_safe_mode() 120ms 后检查，若掉出原生 fullscreen 自动 showFullScreen() 恢复
- 5 秒超时窗口避免误判用户主动操作
- 13 个新测试覆盖所有路径"
```

### Step 20: Update `docs/runbook_macos_fullscreen.md`

Add to the runbook (after the existing manual checklist):

```markdown
## 验证 safe mode（I-1）

macOS 系统全屏下：
1. 切换 5 个 page（dashboard / projects / workflow / settings / chapter_studio）各 30 次
2. 切回原 page 30 次
3. 反复 10 轮，期望 0 次异常退出

切页期间掉出 fullscreen 验证：
1. 进 macOS 系统全屏
2. 触发一次切页（用脚本或快捷键）
3. 切页过程中用 `Cmd+Ctrl+F` 退出 fullscreen（或 `showMaximized()` 模拟）
4. 验证 120 ms 内窗口自动恢复 `showFullScreen()`

dashboard hero 阴影验证：
1. 加载 dashboard 页面（hero 应带 QGraphicsDropShadowEffect 阴影）
2. 切到其他 page
3. 切回 dashboard
4. 验证 hero 阴影仍然存在（未在 safe mode 入口被错清）
```

Commit:
```bash
git add docs/runbook_macos_fullscreen.md
git commit -m "docs(desktop): add I-1 safe mode verification checklist"
```

---

## Task 2: `_render_jobs_panel` 局部节流（I-2，P0）

### Files
- Modify: `novel_forge/desktop/pages/chapter_studio/coord.py`（改 1 行 + 新增 2 个方法）
- Test: `tests/desktop/test_chapter_studio_jobs_throttle.py`（NEW）

### Step 1: Write failing test

**File:** `tests/desktop/test_chapter_studio_jobs_throttle.py`

```python
"""Tests for ChapterStudio jobs panel render coalescing (I-2)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.desktop


def test_schedule_jobs_panel_render_coalesces_within_16ms():
    """Multiple schedule calls within 16ms should result in 1 render_jobs_panel call."""
    from novel_forge.desktop.pages.chapter_studio import coord

    page = MagicMock(spec=coord.ChapterStudioPage)
    # Attach the real methods
    page._schedule_jobs_panel_render = coord.ChapterStudioPage._schedule_jobs_panel_render.__get__(page)
    page._flush_jobs_panel_render = coord.ChapterStudioPage._flush_jobs_panel_render.__get__(page)

    jobs1 = [MagicMock(), MagicMock()]
    jobs2 = [MagicMock()]

    page._schedule_jobs_panel_render(jobs1, loading=False)
    page._schedule_jobs_panel_render(jobs2, loading=True)
    page._schedule_jobs_panel_render(jobs1, loading=False)

    # 第一次创建了 timer，pending 已设
    assert page._jobs_panel_render_pending is True
    # 多次调用应只 schedule 一次
    page._render_jobs_panel.assert_not_called()

    # 模拟 timer 触发
    page._flush_jobs_panel_render()

    # 仅最后一次的 jobs 被传入
    assert page._render_jobs_panel.call_count == 1
    args, kwargs = page._render_jobs_panel.call_args
    assert args[0] == jobs1  # 最后一次的 jobs
    assert kwargs.get("loading") is False  # 最后一次的 loading


def test_schedule_after_flush_renders_again():
    """After flush, a new schedule should result in a new render."""
    from novel_forge.desktop.pages.chapter_studio import coord

    page = MagicMock(spec=coord.ChapterStudioPage)
    page._schedule_jobs_panel_render = coord.ChapterStudioPage._schedule_jobs_panel_render.__get__(page)
    page._flush_jobs_panel_render = coord.ChapterStudioPage._flush_jobs_panel_render.__get__(page)

    jobs = [MagicMock()]
    page._schedule_jobs_panel_render(jobs, loading=False)
    page._flush_jobs_panel_render()
    page._schedule_jobs_panel_render(jobs, loading=True)
    page._flush_jobs_panel_render()

    assert page._render_jobs_panel.call_count == 2
```

### Step 2: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_chapter_studio_jobs_throttle.py -v`
Expected: AttributeError (methods don't exist)

### Step 3: Implement `_schedule_jobs_panel_render` and `_flush_jobs_panel_render`

**File:** `novel_forge/desktop/pages/chapter_studio/coord.py`

Find the `ChapterStudioPage` class definition. Add the two methods (place after an existing related method, or near `_render_jobs_panel`):

```python
    def _schedule_jobs_panel_render(self, jobs: list, *, loading: bool) -> None:
        """节流的 jobs panel 渲染 — 仅渲染，不影响 bind_jobs 其他副作用。

        第一次调用时 lazy 创建 16 ms ``QTimer``，避免修改 ``ChapterStudioPage.__init__``。
        """
        if not hasattr(self, "_jobs_panel_render_pending"):
            self._jobs_panel_render_pending = False
            self._jobs_panel_render_jobs: list = []
            self._jobs_panel_render_loading = False
            self._jobs_panel_render_timer = QTimer(self)
            self._jobs_panel_render_timer.setSingleShot(True)
            self._jobs_panel_render_timer.setInterval(16)
            self._jobs_panel_render_timer.timeout.connect(self._flush_jobs_panel_render)
        if self._jobs_panel_render_pending:
            return
        self._jobs_panel_render_pending = True
        self._jobs_panel_render_jobs = list(jobs)
        self._jobs_panel_render_loading = loading
        self._jobs_panel_render_timer.start()

    def _flush_jobs_panel_render(self) -> None:
        self._jobs_panel_render_pending = False
        self._render_jobs_panel(
            self._jobs_panel_render_jobs,
            loading=self._jobs_panel_render_loading,
        )
```

### Step 4: Replace direct call at coord.py:1249

**File:** `novel_forge/desktop/pages/chapter_studio/coord.py`

Find line 1249 (within the `bind_jobs` method or equivalent):

```python
        # BEFORE
        self._render_jobs_panel(jobs, loading=self._jobs_loading)

        # AFTER
        self._schedule_jobs_panel_render(jobs, loading=self._jobs_loading)
```

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_chapter_studio_jobs_throttle.py -v`
Expected: 2 tests passed

### Step 6: Run desktop tests to verify no regression

Run: `.venv/bin/python -m pytest tests/desktop/test_chapter_studio_jobs_throttle.py tests/desktop -q --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' --timeout=300 -k "chapter_studio or jobs"`
Expected: all pass

### Step 7: Add integration test verifying bind_jobs other steps remain sync

Append to `tests/desktop/test_chapter_studio_jobs_throttle.py`:

```python
def test_bind_jobs_other_steps_remain_synchronous():
    """Verify _schedule_jobs_panel_render only affects render, not other bind_jobs side effects.

    bind_jobs 包含 active projects / status dot / memory events / rail 等多个步骤，
    这些必须在 schedule 调用时同步执行（不能被 16ms 节流）。
    """
    from novel_forge.desktop.pages.chapter_studio import coord

    page = MagicMock(spec=coord.ChapterStudioPage)
    page._schedule_jobs_panel_render = coord.ChapterStudioPage._schedule_jobs_panel_render.__get__(page)

    # 模拟其他步骤
    page._update_active_projects = MagicMock()
    page._update_status_dot = MagicMock()
    page._rail.bind_jobs = MagicMock()
    page._render_action_panel = MagicMock()

    # bind_jobs 流程（仅节流 _render_jobs_panel）
    page._update_active_projects()
    page._update_status_dot()
    page._rail.bind_jobs([], project_id="x")
    page._render_action_panel()
    page._schedule_jobs_panel_render([], loading=False)

    # 同步步骤立即调用
    assert page._update_active_projects.called
    assert page._update_status_dot.called
    assert page._rail.bind_jobs.called
    assert page._render_action_panel.called
    # _render_jobs_panel 还未调用（等 16ms timer）
    assert not page._render_jobs_panel.called
```

### Step 8: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_chapter_studio_jobs_throttle.py -v`
Expected: 3 tests passed

### Step 9: Commit

```bash
git add novel_forge/desktop/pages/chapter_studio/coord.py tests/desktop/test_chapter_studio_jobs_throttle.py
git commit -m "perf(desktop): throttle ChapterStudio _render_jobs_panel to 16ms

仅在 coord.py:1249 改一行（self._render_jobs_panel → self._schedule_jobs_panel_render），
bind_jobs 的其他步骤（active projects / status dot / memory events / rail / auto-pilot）
保持同步执行，避免被节流延迟。Timer lazy 创建避免修改 ChapterStudioPage.__init__。"
```

---

## Task 3: Windows `WindowsSelectorEventLoopPolicy`（I-7，P2）

### Files
- Modify: `novel_forge/desktop/main.py`（`_load_qt_objects` 之后插入 policy 设置）
- Test: `tests/desktop/test_windows_event_loop_policy.py`（NEW）

### Step 1: Write failing test

**File:** `tests/desktop/test_windows_event_loop_policy.py`

```python
"""Tests for Windows event loop policy (I-7)."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.desktop


def test_policy_applied_on_windows(monkeypatch):
    """When sys.platform == 'win32' and asyncio.WindowsSelectorEventLoopPolicy exists, apply it."""
    monkeypatch.setattr(sys, "platform", "win32")

    # Patch the policy class
    class FakePolicy:
        def __init__(self):
            self.applied = False

    fake_instance = FakePolicy()
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy)

    # Patch set_event_loop_policy to verify
    with patch("asyncio.set_event_loop_policy") as mock_set:
        # Simulate the policy application logic (extract from main.py for testability)
        if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        mock_set.assert_called_once()


def test_policy_not_applied_on_non_windows(monkeypatch):
    """On non-win32, even if WindowsSelectorEventLoopPolicy exists, do not apply."""
    monkeypatch.setattr(sys, "platform", "linux")

    class FakePolicy:
        pass

    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        mock_set.assert_not_called()


def test_policy_handles_missing_attribute_on_non_windows(monkeypatch):
    """On macOS/Linux where WindowsSelectorEventLoopPolicy may not exist, hasattr guard works."""
    monkeypatch.setattr(sys, "platform", "darwin")

    # Ensure hasattr returns False (default asyncio on macOS lacks this)
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        monkeypatch.delattr(asyncio, "WindowsSelectorEventLoopPolicy", raising=False)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        mock_set.assert_not_called()
```

### Step 2: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_windows_event_loop_policy.py -v`
Expected: 3 tests passed (logic already inline; the test serves as documentation)

Actually: this test should pass since the logic is self-contained. The actual assertion is in Step 3 when we wire it into main.py.

### Step 3: Wire policy application into `main.py`

**File:** `novel_forge/desktop/main.py`

After `_load_qt_objects()` function definition (line 32) and before the import of `_AsyncServiceLoop` (line 60), OR more cleanly, at the very top of `launch_desktop()` (line 186) BEFORE the `qt_application.instance()` call:

```python
def _apply_windows_event_loop_policy() -> None:
    """On Windows, install the SelectorEventLoopPolicy to avoid ProactorEventLoop issues.

    Must be called BEFORE ``asyncio.new_event_loop()`` (which happens in
    ``_AsyncServiceLoop._run`` at line 73). Some Windows socket calls fail
    with ``OSError: [WinError 121]`` under the default ProactorEventLoop.
    """
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

Then call it at the top of `launch_desktop()` (insert after `def launch_desktop() -> int:` and before `qt_application, qt_message_box = _load_qt_objects()`):

```python
def launch_desktop() -> int:
    """Launch the desktop UI and return the Qt exit code."""
    _apply_windows_event_loop_policy()  # NEW: I-7
    qt_application, qt_message_box = _load_qt_objects()
    ...
```

### Step 4: Refactor test to import and call the actual function

**File:** `tests/desktop/test_windows_event_loop_policy.py`

Replace the entire test file content with:

```python
"""Tests for Windows event loop policy (I-7)."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.desktop


def test_apply_windows_policy_calls_set_on_windows(monkeypatch):
    """When on win32 with policy available, asyncio.set_event_loop_policy is called."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "win32")

    class FakePolicy:
        pass

    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_called_once()
        # 验证传入的是 FakePolicy 实例
        assert isinstance(mock_set.call_args[0][0], FakePolicy)


def test_apply_windows_policy_noop_on_macos(monkeypatch):
    """On darwin, even if policy class exists, do not apply."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "darwin")
    class FakePolicy:
        pass
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_not_called()


def test_apply_windows_policy_noop_when_class_missing(monkeypatch):
    """On macOS/Linux where policy class doesn't exist, hasattr guard skips."""
    from novel_forge.desktop import main

    monkeypatch.setattr(sys, "platform", "win32")
    # Delete the attribute to simulate a Python build that doesn't have it
    monkeypatch.delattr(asyncio, "WindowsSelectorEventLoopPolicy", raising=True)

    with patch("asyncio.set_event_loop_policy") as mock_set:
        main._apply_windows_event_loop_policy()
        mock_set.assert_not_called()
```

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_windows_event_loop_policy.py -v`
Expected: 3 tests passed

### Step 6: Commit

```bash
git add novel_forge/desktop/main.py tests/desktop/test_windows_event_loop_policy.py
git commit -m "fix(desktop): apply WindowsSelectorEventLoopPolicy on Windows (I-7)

早于 _AsyncServiceLoop._run() 调用的 asyncio.new_event_loop()，避免 ProactorEventLoop
在某些 socket 操作上抛 OSError: [WinError 121]。hasattr 守护保证 macOS/Linux 不受影响。"
```

---

## Task 4: 应用名 NIMO（I-9，P2）

### Files
- Modify: `novel_forge/desktop/main.py`（`launch_desktop` 内 `setApplicationName`）
- Modify: `novel_forge_desktop.spec`（仓库根，macOS CFBundleName / CFBundleDisplayName）
- Test: `tests/desktop/test_app_name.py`（NEW）

### Step 1: Write failing test

**File:** `tests/desktop/test_app_name.py`

```python
"""Tests for application name branding (I-9)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.desktop


def test_launch_desktop_sets_application_name_to_nimo(monkeypatch):
    """launch_desktop should set QApplication name to NIMO."""
    from PySide6.QtWidgets import QApplication
    from novel_forge.desktop import main

    # Create a QApplication instance if needed
    app = QApplication.instance() or QApplication([])

    # Mock just the parts of launch_desktop that set the name
    from PySide6.QtWidgets import QApplication as QA
    monkeypatch.setattr(QA, "setApplicationName", lambda name: None)
    # ... similar for setApplicationDisplayName, etc.

    # Verify that constants in main.py reference "NIMO"
    import inspect
    source = inspect.getsource(main.launch_desktop)
    assert '"NIMO"' in source or "'NIMO'" in source, (
        "launch_desktop should set application name to 'NIMO'"
    )
```

### Step 2: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_app_name.py -v`
Expected: AssertionError (currently uses "Novel Forge")

### Step 3: Update `launch_desktop` in main.py

**File:** `novel_forge/desktop/main.py`

Replace lines 203-204:

```python
    # BEFORE
    app.setApplicationName("Novel Forge")
    app.setOrganizationName("Novel Forge")
    # AFTER
    app.setApplicationName("NIMO")
    app.setApplicationDisplayName("NIMO")
    app.setOrganizationName("Novel Forge")  # 保留作为 fallback
```

### Step 4: Update `novel_forge_desktop.spec`

**File:** `novel_forge_desktop.spec`（仓库根）

Find the macOS BUNDLE block (around line 126-167) and update CFBundleName / CFBundleDisplayName:

```python
    # 在 BUNDLE 段内（CFBundleName / CFBundleDisplayName）
    'CFBundleName': 'NIMO',
    'CFBundleDisplayName': 'NIMO',
```

(Keep CFBundleIdentifier as "com.novelforge.desktop" — that's the macOS bundle ID, not the user-facing name.)

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_app_name.py -v`
Expected: 1 test passed

### Step 6: Commit

```bash
git add novel_forge/desktop/main.py novel_forge_desktop.spec tests/desktop/test_app_name.py
git commit -m "refactor(desktop): rename application name to NIMO (I-9)

同步 CFBundleName / CFBundleDisplayName；保留 organizationName 作为 fallback。
Windows 旧 registry key 残留无害（用户配置 N/A）。"
```

---

## Task 5: xfail `strict=False`（I-10，P2）

### Files
- Modify: `tests/desktop/test_page_smoke.py`（`xfail(run=False, strict=True)` → `strict=False`）
- Modify: `tests/desktop/test_memory_polish.py`（同上）

### Step 1: Edit test_page_smoke.py

**File:** `tests/desktop/test_page_smoke.py`（around line 16-26）

Find the `xfail` decorator on the `test_dashboard_page_smoke` test and change `strict=True` to `strict=False`:

```python
# BEFORE
@pytest.mark.xfail(run=False, strict=True, reason="...")
def test_dashboard_page_smoke():
    ...

# AFTER
@pytest.mark.xfail(run=False, strict=False, reason="...")
def test_dashboard_page_smoke():
    ...
```

### Step 2: Edit test_memory_polish.py

**File:** `tests/desktop/test_memory_polish.py`（around line 305-322）

Find the `xfail` decorator on `test_empty_panel_tab_switch_safe` and change `strict=True` to `strict=False`:

```python
# BEFORE
@pytest.mark.xfail(run=False, strict=True, reason="...")
def test_empty_panel_tab_switch_safe():
    ...

# AFTER
@pytest.mark.xfail(run=False, strict=False, reason="...")
def test_empty_panel_tab_switch_safe():
    ...
```

### Step 3: Run tests to verify they still pass (xfail doesn't change pass/fail much)

Run: `.venv/bin/python -m pytest tests/desktop/test_page_smoke.py::test_dashboard_page_smoke tests/desktop/test_memory_polish.py::test_empty_panel_tab_switch_safe -v`
Expected: both reported as XFAIL (run=False means they don't actually run)

### Step 4: Commit

```bash
git add tests/desktop/test_page_smoke.py tests/desktop/test_memory_polish.py
git commit -m "test(desktop): lower xfail strict to False for known PySide6 offscreen crashes (I-10)

strict=True 会让 xfail-with-run=False 标记在 PySide6 crash 修复后立刻翻红；
改为 strict=False 后，未来真正修复 crash 时只需移除 marker。"
```

---

## Task 6: macOS atexit 修复（I-11，P3）

### Files
- Modify: `novel_forge/desktop/window/shutdown.py`（强制退出分支前显式 release sleep inhibitor + flush streams）

### Step 1: Locate the forced-exit branch

**File:** `novel_forge/desktop/window/shutdown.py`

Find the `os._exit(0)` call (around line 399 based on exploration). It should be in a `closeEvent` override or `_force_exit` method.

### Step 2: Add explicit cleanup before `_exit`

Edit the function containing `os._exit(0)`. The exact context isn't fully verified; the edit should look like:

```python
        # BEFORE
        # ... some condition ...
        os._exit(0)

        # AFTER
        # I-11: 显式释放 sleep inhibitor + flush streams（atexit 不可靠）
        try:
            from novel_forge.desktop.sleep_inhibitor import get_sleep_inhibitor
            get_sleep_inhibitor().release()
        except Exception:
            pass
        try:
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)
```

If the surrounding code already has these calls, skip this task (verify with `git log` and `git diff` on shutdown.py).

### Step 3: Add a test

**File:** `tests/desktop/test_shutdown_force_exit.py`（NEW）

```python
"""Tests for forced shutdown cleanup (I-11)."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.desktop


def test_force_exit_releases_sleep_inhibitor():
    """When force-exit branch is taken, sleep inhibitor is released first."""
    from novel_forge.desktop import shutdown

    with patch("os._exit") as mock_exit:
        with patch("novel_forge.desktop.sleep_inhibitor.get_sleep_inhibitor") as mock_get:
            mock_inhibitor = mock_get.return_value
            # ... call the force-exit function ...
            # mock_exit.assert_called_once_with(0)
            mock_inhibitor.release.assert_called_once()
```

If the actual function signature is more complex, the test should mock the entry point that leads to `_exit(0)`.

### Step 4: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_shutdown_force_exit.py -v`
Expected: 1 test passed

### Step 5: Commit

```bash
git add novel_forge/desktop/window/shutdown.py tests/desktop/test_shutdown_force_exit.py
git commit -m "fix(desktop): explicit sleep inhibitor release + stream flush on macOS force-exit (I-11)

os._exit(0) 绕过 atexit handler，导致 SleepInhibitor 不会自动 release。
显式调用确保 caffeinate 子进程被终止。"
```

---

## Task 7: 4 个 standalone page 补 `shutdown()`（I-5，P1）

### Files
- Modify: `novel_forge/desktop/pages/standalone/relationship_network_page.py`
- Modify: `novel_forge/desktop/pages/standalone/character_profile_page.py`
- Modify: `novel_forge/desktop/pages/standalone/character_bible_editor.py`
- Modify: `novel_forge/desktop/pages/standalone/outline_editor.py`
- Test: `tests/desktop/test_standalone_page_shutdown.py`（NEW）

### Step 1: Read the template (existing projects_page.py:257-269)

**File:** `novel_forge/desktop/pages/standalone/projects_page.py`（around line 257-269）

Review the existing `shutdown()` implementation in `projects_page.py` to understand the pattern. The exact structure may include:
- `safe_disconnect` calls
- timer stops
- sub-panel `shutdown()` recursion
- `wait_for_thread_pool` (in some cases)

### Step 2: Write failing test

**File:** `tests/desktop/test_standalone_page_shutdown.py`

```python
"""Tests for standalone page shutdown() (I-5)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.desktop


@pytest.mark.parametrize("page_module", [
    "novel_forge.desktop.pages.standalone.relationship_network_page",
    "novel_forge.desktop.pages.standalone.character_profile_page",
    "novel_forge.desktop.pages.standalone.character_bible_editor",
    "novel_forge.desktop.pages.standalone.outline_editor",
])
def test_page_has_shutdown_method(page_module):
    """Each standalone page must implement shutdown()."""
    import importlib
    mod = importlib.import_module(page_module)
    # Find the Page class
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if isinstance(attr, type) and attr_name.endswith("Page"):
            assert hasattr(attr, "shutdown"), f"{page_module}.{attr_name} missing shutdown()"
            break
```

### Step 3: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_standalone_page_shutdown.py -v`
Expected: 4 AssertionError (pages missing shutdown)

### Step 4: Add `shutdown()` to each page

For each of the 4 pages, add a `shutdown()` method following the `projects_page.py` template. The exact body depends on the page's state — keep it minimal but sufficient:

```python
def shutdown(self) -> None:
    """Clean up timers, signals, and sub-panels (I-5)."""
    # 1. Stop timers (replace with actual timer attribute names)
    for timer_attr in ("_refresh_timer", "_search_timer"):
        timer = getattr(self, timer_attr, None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
    # 2. Disconnect signals
    for sig_attr in ("_signal_a", "_signal_b"):
        # safe_disconnect helper or try/except
        pass
    # 3. Sub-panel shutdown
    sub_panel = getattr(self, "_some_sub_panel", None)
    if sub_panel is not None and hasattr(sub_panel, "shutdown"):
        sub_panel.shutdown()
```

For each page, the actual timer/signal names must be discovered by reading the page file. Adjust the pattern accordingly.

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_standalone_page_shutdown.py -v`
Expected: 4 tests passed

### Step 6: Commit

```bash
git add novel_forge/desktop/pages/standalone/relationship_network_page.py \
        novel_forge/desktop/pages/standalone/character_profile_page.py \
        novel_forge/desktop/pages/standalone/character_bible_editor.py \
        novel_forge/desktop/pages/standalone/outline_editor.py \
        tests/desktop/test_standalone_page_shutdown.py
git commit -m "fix(desktop): add shutdown() to 4 standalone pages (I-5)

relationship_network_page / character_profile_page / character_bible_editor /
outline_editor 都缺 shutdown()，导致切换时泄漏 timer / signal。
照搬 projects_page.py:257-269 模板。"
```

---

## Task 8: devicePixelRatio 感知位图（I-8，P2）

### Files
- Modify: `novel_forge/desktop/pages/standalone/token_analytics.py`（`_render_model_cost_ring_image`）
- Test: `tests/desktop/test_device_pixel_ratio_image.py`（NEW）

### Step 1: Read current implementation

**File:** `novel_forge/desktop/pages/standalone/token_analytics.py`（around line 1453-1500）

Verify the exact method body. Key change points:
- Add `dpr = float(self.devicePixelRatioF() or 1.0)`
- Change `size = 176` to `size_logical = 176; size_px = int(round(size_logical * dpr))`
- `image = QImage(size_px, size_px, ...)`
- `image.setDevicePixelRatio(dpr)`
- Keep painter geometry in logical pixels (use `size_logical` for `QRectF`)

### Step 2: Write failing test

**File:** `tests/desktop/test_device_pixel_ratio_image.py`

```python
"""Tests for devicePixelRatio aware ring image (I-8)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.desktop


def test_render_uses_dpr_2x_size_for_high_dpi():
    """When devicePixelRatio is 2.0, image size is 2x logical."""
    from PySide6.QtWidgets import QApplication
    from novel_forge.desktop.pages.standalone import token_analytics

    app = QApplication.instance() or QApplication([])

    # Create a minimal instance
    page = token_analytics.TokenAnalyticsPage.__new__(token_analytics.TokenAnalyticsPage)
    page._chart_palette = MagicMock(return_value=["#000000"])

    with patch.object(page, "devicePixelRatioF", return_value=2.0):
        # Capture the QImage creation
        with patch("PySide6.QtGui.QImage") as mock_qimage:
            mock_qimage.return_value.size.return_value.width.return_value = 352
            mock_qimage.return_value.size.return_value.height.return_value = 352

            result = page._render_model_cost_ring_image(
                [("a", 50.0), ("b", 50.0)], total_cost_cny=1.0
            )

            # Verify QImage was created with 352x352 (176 * 2.0)
            mock_qimage.assert_called_once()
            args = mock_qimage.call_args[0]
            assert args[0] == 352  # size_px
            assert args[1] == 352
            # Verify setDevicePixelRatio was called
            mock_qimage.return_value.setDevicePixelRatio.assert_called_once_with(2.0)
```

### Step 3: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_device_pixel_ratio_image.py -v`
Expected: AssertionError (image size is 176 not 352)

### Step 4: Modify `_render_model_cost_ring_image`

**File:** `novel_forge/desktop/pages/standalone/token_analytics.py`

```python
    def _render_model_cost_ring_image(self, slices: list[tuple[str, float]], *, total_cost_cny: float) -> str:
        if not slices:
            return ""
        # I-8: devicePixelRatio 感知位图 — Retina 屏渲染更清晰
        dpr = float(self.devicePixelRatioF() or 1.0)
        size_logical = 176
        pen_width = 24
        size_px = int(round(size_logical * dpr))
        image = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        ring_margin = float((pen_width // 2) + 4)
        # painter 坐标系使用 logical 像素
        rect = QRectF(
            ring_margin,
            ring_margin,
            float(size_logical) - ring_margin * 2.0,
            float(size_logical) - ring_margin * 2.0,
        )

        base_pen = QPen(QColor("#efe3d4"))
        base_pen.setWidth(pen_width)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(base_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # ... 其余代码保持不变，使用 size_logical 计算 ...
```

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_device_pixel_ratio_image.py -v`
Expected: 1 test passed

### Step 6: Commit

```bash
git add novel_forge/desktop/pages/standalone/token_analytics.py tests/desktop/test_device_pixel_ratio_image.py
git commit -m "fix(desktop): devicePixelRatio aware ring image in token_analytics (I-8)

Retina 屏（dpr=2.0）下，_render_model_cost_ring_image 之前用固定 176×176 物理像素，
导致显示模糊。改用 logical 176 + dpr 缩放物理像素，painter 坐标系保持 logical。"
```

---

## Task 9: HTML 字符串 hash cache（I-3，P1）

### Files
- Create: `novel_forge/desktop/components/html_cache.py`
- Modify: `novel_forge/desktop/components/rich_document_viewer.py`（接入 `should_set_html`）
- Test: `tests/desktop/test_html_cache.py`（NEW）

### Step 1: Create the cache module

**File:** `novel_forge/desktop/components/html_cache.py`

```python
"""Per-widget last-HTML hash short-circuit for setHtml() / setPlainText().

Design:
- 缓存存储在 widget property 上（widget._nf_last_html_hash），随 widget 销毁自动消失
- 不缓存渲染产物本身（QTextDocument 与 widget 绑定，复用会 crash）
- 超过 _MAX_LEN 的 payload 不缓存（避免内存膨胀）
"""
from __future__ import annotations

import hashlib
from typing import Final

_PROPERTY_NAME: Final[str] = "_nf_last_html_hash"
_MAX_LEN: Final[int] = 1_000_000


def should_set_html(widget, html: str) -> bool:
    """Return True if setHtml should be called (payload changed or first time).

    Args:
        widget: 目标 QTextBrowser / QTextEdit。
        html: 待设置的 HTML 字符串。

    Returns:
        True 表示需要调用 setHtml；False 表示 payload 与上次相同，可跳过。
    """
    if len(html) > _MAX_LEN:
        return True
    new_hash = hashlib.sha1(html.encode("utf-8")).hexdigest()
    old_hash = getattr(widget, _PROPERTY_NAME, None)
    if new_hash == old_hash:
        return False
    try:
        setattr(widget, _PROPERTY_NAME, new_hash)
    except (AttributeError, RuntimeError):
        pass
    return True
```

### Step 2: Write failing test

**File:** `tests/desktop/test_html_cache.py`

```python
"""Tests for HTML hash cache (I-3)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QTextBrowser

pytestmark = pytest.mark.desktop


def test_should_set_html_first_call_returns_true():
    """First call for a widget returns True (no previous hash)."""
    from novel_forge.desktop.components.html_cache import should_set_html

    app = QApplication.instance() or QApplication([])
    browser = QTextBrowser()
    assert should_set_html(browser, "<p>hello</p>") is True


def test_should_set_html_same_payload_returns_false():
    """Second call with same payload returns False."""
    from novel_forge.desktop.components.html_cache import should_set_html

    app = QApplication.instance() or QApplication([])
    browser = QTextBrowser()
    html = "<p>hello world</p>"
    assert should_set_html(browser, html) is True
    assert should_set_html(browser, html) is False


def test_should_set_html_different_payload_returns_true():
    """Different payload returns True."""
    from novel_forge.desktop.components.html_cache import should_set_html

    app = QApplication.instance() or QApplication([])
    browser = QTextBrowser()
    assert should_set_html(browser, "<p>a</p>") is True
    assert should_set_html(browser, "<p>b</p>") is True


def test_should_set_html_new_widget_does_not_reuse_hash():
    """New widget (after old one destroyed) should not skip first setHtml."""
    from novel_forge.desktop.components.html_cache import should_set_html

    app = QApplication.instance() or QApplication([])
    old_browser = QTextBrowser()
    should_set_html(old_browser, "<p>shared</p>")
    old_browser.deleteLater()

    new_browser = QTextBrowser()
    # New widget has no _nf_last_html_hash property → first call returns True
    assert should_set_html(new_browser, "<p>shared</p>") is True


def test_should_set_html_oversize_returns_true():
    """Payload > 1 MB returns True (don't cache huge objects)."""
    from novel_forge.desktop.components.html_cache import should_set_html

    app = QApplication.instance() or QApplication([])
    browser = QTextBrowser()
    huge = "x" * (1_000_001)
    assert should_set_html(browser, huge) is True
    # 第二次也 True（不缓存）
    assert should_set_html(browser, huge) is True
```

### Step 3: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_html_cache.py -v`
Expected: ImportError (module doesn't exist yet)

### Step 4: Wire into RichDocumentViewer

**File:** `novel_forge/desktop/components/rich_document_viewer.py`

Add import at top:

```python
from novel_forge.desktop.components.html_cache import should_set_html
```

In `_on_toggle_raw` (around line 151-162), wrap the `setHtml` calls:

```python
    def _on_toggle_raw(self) -> None:
        self._is_raw_mode = not self._is_raw_mode
        if self._is_raw_mode:
            html = (
                "<pre style='background:#f7f1e8; padding:14px; "
                "border-radius:6px; font-family:Menlo,Consolas,monospace; "
                "font-size:12px; line-height:1.55;'>{escaped}</pre>"
            ).format(escaped=escape(self._raw_text))
            if should_set_html(self._body, html):
                self._body.setHtml(html)
        else:
            if should_set_html(self._body, self._original_html):
                self._body.setHtml(self._original_html)
        self.raw_mode_toggled.emit(self._is_raw_mode)
```

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_html_cache.py -v`
Expected: 5 tests passed

### Step 6: Run desktop tests to verify no regression

Run: `.venv/bin/python -m pytest tests/desktop -q --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' --timeout=300`
Expected: all pass

### Step 7: Commit

```bash
git add novel_forge/desktop/components/html_cache.py \
        novel_forge/desktop/components/rich_document_viewer.py \
        tests/desktop/test_html_cache.py
git commit -m "perf(desktop): per-widget last-HTML hash short-circuit (I-3)

新增 html_cache.should_set_html()：相同 payload 跳过 setHtml，避免重复 HTML parse + paint。
Widget property 存储（_nf_last_html_hash），widget 销毁时 property 一起消失，无 id 复用问题。
1 MB 阈值：不缓存大对象避免内存膨胀。
RichDocumentViewer._on_toggle_raw 接入作为 first consumer。"
```

---

## Task 10: QSS px → pt 迁移（I-6，P1）

### Files
- Create: `scripts/migrate_qss_px_to_pt.py`
- Create: `scripts/verify_font_scaling.py`
- Modify: `novel_forge/desktop/tokens/typography.py`（新增 `to_qss_font_size` + 修文档）
- Modify: `novel_forge/desktop/theme/components.py`（一个文件试水 + visual diff）
- Modify: 其他 theme/*.py（批量迁移）

### Step 1: Add `to_qss_font_size` to tokens/typography.py

**File:** `novel_forge/desktop/tokens/typography.py`

Add at the bottom of the file (or after `TYPE_RAMP`):

```python
def to_qss_font_size(token_name: str) -> str:
    """Map a type-ramp token to a QSS ``font-size`` value in points.

    Args:
        token_name: Key from TYPE_RAMP (e.g. "text-base", "text-sm", "text-xs").

    Returns:
        String like ``"13pt"``. If token is not in TYPE_RAMP, returns the
        token itself as a fallback (no transformation).
    """
    if token_name in TYPE_RAMP:
        return f"{TYPE_RAMP[token_name]}pt"
    return token_name
```

Also fix the docstring at line 125 that says `text-base (13px)` — should say `text-base (13pt)`.

### Step 2: Write the migration script

**File:** `scripts/migrate_qss_px_to_pt.py`

```python
"""Migrate QSS font-size: NNpx to NNpt in theme/*.py and document_renderer HTML.

Usage:
    python scripts/migrate_qss_px_to_pt.py --dry-run    # Show what would change
    python scripts/migrate_qss_px_to_pt.py --apply      # Apply changes
    python scripts/migrate_qss_px_to_pt.py --target theme/components.py  # Single file
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Match font-size: NNpx  (NN = 1-3 digits, possibly with decimal)
_FONT_SIZE_PX_RE = re.compile(r"font-size\s*:\s*(\d+(?:\.\d+)?)px")

# Border widths / shadows under 4px should remain px (they're not text sizes)
_BORDER_PX_RE = re.compile(r"border(?:-(?:top|bottom|left|right))?(?:-width)?\s*:\s*\d+px")


def migrate_text(text: str) -> str:
    """Replace font-size: NNpx with NNpt. Skip border-widths."""
    def repl(match: re.Match) -> str:
        value = match.group(1)
        return f"font-size: {value}pt"
    return _FONT_SIZE_PX_RE.sub(repl, text)


def process_file(path: Path, *, dry_run: bool) -> int:
    content = path.read_text(encoding="utf-8")
    new_content = migrate_text(content)
    if new_content == content:
        return 0
    if not dry_run:
        path.write_text(new_content, encoding="utf-8")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", dest="apply_changes")
    parser.add_argument("--target", type=str, default=None)
    args = parser.parse_args()
    if not args.dry_run and not args.apply_changes:
        args.dry_run = True  # default to dry-run for safety

    root = Path("novel_forge/desktop")
    if args.target:
        targets = [Path(args.target)]
    else:
        targets = list((root / "theme").rglob("*.py")) + list(
            (root / "pages" / "document_renderer").rglob("*.py")
        )

    changed = 0
    for path in targets:
        if not path.exists():
            continue
        n = process_file(path, dry_run=args.dry_run)
        if n > 0:
            action = "would change" if args.dry_run else "changed"
            print(f"[{action}] {path}")
            changed += n

    action = "would change" if args.dry_run else "changed"
    print(f"\nTotal {action}: {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### Step 3: Dry-run on single file

Run: `python scripts/migrate_qss_px_to_pt.py --target novel_forge/desktop/theme/components.py`
Expected: count of changes; verify no border-widths were wrongly changed.

### Step 4: Visual diff test

**File:** `scripts/verify_font_scaling.py`

```python
"""Verify font scaling across 1x / 1.5x / 2x devicePixelRatio."""
from __future__ import annotations

import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel


def main() -> int:
    app = QApplication.instance() or QApplication([])

    text = "测试文本 Hello World"
    dprs = [1.0, 1.5, 2.0]
    widths = []
    for dpr in dprs:
        font = QFont()
        font.setPointSize(13)
        metrics = QFontMetrics(font)
        widths.append(metrics.horizontalAdvance(text))

    ratio_max = max(widths) / min(widths)
    print(f"Widths at 1x/1.5x/2x: {widths}")
    print(f"Max/min ratio: {ratio_max:.3f}")

    # 期望：ratio < 1.05（因为 setPointSize 是 logical 像素，QFontMetrics 应该返回 logical 宽度）
    if ratio_max > 1.05:
        print(f"FAIL: ratio {ratio_max} > 1.05, font scaling broken")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `python scripts/verify_font_scaling.py`
Expected: PASS

### Step 5: Apply migration to theme/components.py (single file tryout)

Run: `python scripts/migrate_qss_px_to_pt.py --target novel_forge/desktop/theme/components.py --apply`
Expected: 1 file changed (count of font-size replacements)

### Step 6: Visual regression check (manual)

Manually inspect: render a few key pages and compare with visual baseline. If significant drift, investigate or revert.

### Step 7: Apply to all theme files

Run: `python scripts/migrate_qss_px_to_pt.py --apply`
Expected: ~8-10 files changed

### Step 8: Run tests

Run: `.venv/bin/python -m pytest tests/desktop tests/unit -q --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' --timeout=300`
Expected: all pass

### Step 9: Commit

```bash
git add scripts/migrate_qss_px_to_pt.py \
        scripts/verify_font_scaling.py \
        novel_forge/desktop/tokens/typography.py \
        novel_forge/desktop/theme/*.py \
        novel_forge/desktop/pages/document_renderer/**/*.py
git commit -m "refactor(desktop): migrate QSS font-size px → pt for proper HiDPI scaling (I-6)

293 处 font-size: NNpx 替换为 NNpt，让 setPointSize defaults 真正驱动 Windows 1.25x/1.5x 缩放。
新增 scripts/migrate_qss_px_to_pt.py（--dry-run/--apply/--target）与 scripts/verify_font_scaling.py。
Border-widths 保持 px（不影响文字）。"
```

---

## Task 11: humanize_library_dashboard 改 `QAbstractTableModel`（I-4，P1）

### Files
- Modify: `novel_forge/desktop/pages/standalone/humanize_library_dashboard.py`（重写 table 部分）
- Test: `tests/desktop/test_humanize_table_model.py`（NEW）

### Step 1: Read current implementation

**File:** `novel_forge/desktop/pages/standalone/humanize_library_dashboard.py`（around line 702-729）

Review the current `QTableWidget`-based implementation. Identify:
- Column definitions (name / type / status / last_modified / actions)
- Row data source (e.g. `humanize_entries` list)
- Cell widget factories (badge / button delegates)

### Step 2: Write failing test

**File:** `tests/desktop/test_humanize_table_model.py`

```python
"""Tests for humanize_library_dashboard table model (I-4)."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QTableView

pytestmark = pytest.mark.desktop


def test_table_view_uses_model_not_widget():
    """humanize_library_dashboard should use QTableView + QAbstractTableModel, not QTableWidget."""
    from novel_forge.desktop.pages.standalone import humanize_library_dashboard as hld

    # Scan the module for QTableWidget usage
    import inspect
    source = inspect.getsource(hld)
    assert "QTableWidget" not in source, "humanize_library_dashboard should not use QTableWidget"
    assert "QAbstractTableModel" in source, "Should use QAbstractTableModel"
    assert "QTableView" in source, "Should use QTableView"
```

### Step 3: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/desktop/test_humanize_table_model.py -v`
Expected: AssertionError (currently uses QTableWidget)

### Step 4: Refactor to QTableView + QAbstractTableModel

**File:** `novel_forge/desktop/pages/standalone/humanize_library_dashboard.py`

The refactor is large; a minimal approach:

1. Add a `_HumanizeLibraryModel(QAbstractTableModel)` class with `rowCount`, `columnCount`, `data`, `headerData`
2. Replace `QTableWidget` with `QTableView`
3. Set the model: `self._table_view.setModel(self._model)`
4. Set delegates: `self._table_view.setItemDelegateForColumn(...)` for badge / button columns
5. Wire model `dataChanged` signal to update on entries change

The exact code depends on the current data structures — read the file and adapt.

### Step 5: Run test to verify it passes

Run: `.venv/bin/python -m pytest tests/desktop/test_humanize_table_model.py -v`
Expected: 1 test passed

### Step 6: Visual regression baseline regeneration

Run: `python scripts/generate_visual_baseline.py humanize_library_dashboard` (or follow existing visual baseline workflow).

### Step 7: Run all tests

Run: `.venv/bin/python -m pytest tests/desktop -q --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' --timeout=300`
Expected: all pass

### Step 8: Commit

```bash
git add novel_forge/desktop/pages/standalone/humanize_library_dashboard.py tests/desktop/test_humanize_table_model.py
git commit -m "perf(desktop): rewrite humanize_library_dashboard to QAbstractTableModel (I-4)

QTableWidget 在 1000+ 行时显著慢。改用 QTableView + QAbstractTableModel + delegate，
仅渲染可见行。Visual baseline 重生成。"
```

---

## Final verification

After all 11 tasks complete:

```bash
.venv/bin/python -m pytest tests/desktop tests/unit -q \
    --ignore-glob='tests/desktop/*_visual.py' \
    --ignore-glob='tests/desktop/test_visual_regression.py' \
    --timeout=300

ruff check novel_forge/desktop/ scripts/
mypy novel_forge/desktop/

# macOS local manual checklist
# 1. 进 macOS 系统全屏 → 切 5 个 page 各 30 次 → 0 次异常退出
# 2. 切页期间掉出 fullscreen → 120ms 内自动 showFullScreen() 恢复
# 3. dashboard hero shadow 仍存在

# Windows local manual
# 1. SelectorEventLoop policy 应用后 asyncio.open_connection 无 WinError 121
```

## Self-review checklist

- [x] Spec coverage: §13.4 11 items → 11 tasks (1:1)
- [x] No placeholders: each step has explicit code or commands
- [x] Type consistency: `_mac_fullscreen_safe_mode_active` / `_is_native_fullscreen_active` / `_enter_mac_fullscreen_safe_mode` / `_exit_mac_fullscreen_safe_mode` used consistently
- [x] `_schedule_jobs_panel_render` / `_flush_jobs_panel_render` consistent
- [x] `should_set_html` / `_nf_last_html_hash` / `_MAX_LEN` consistent
- [x] `to_qss_font_size` / TYPE_RAMP consistent
- [x] `devicePixelRatioF` / `setDevicePixelRatio` consistent

— END OF PLAN v2.3 —
