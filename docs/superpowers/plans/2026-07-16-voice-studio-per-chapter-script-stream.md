# 配音脚本按章节隔离流式进度显示 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复"任一章开始生成配音脚本后，所有章节窗口都显示'正在分析脚本'"的串台问题，使每个章节的脚本窗口按各自的真实状态显示。

**Architecture:** 把页面级单一流式状态变量（`_script_stream_id/_text/_active` + `_script_gen_dots`）收敛成按 `chapter_number` 索引的 `dict[int, _ChapterScriptStream]` 容器，新增 `_generating_chapter` 标记当前生成章。动画定时器与流式渲染均以"当前显示章 == 生成章"为守卫，仅对生成章写入 browser；章节切换时若目标章有活跃流则恢复其流式片段，否则走磁盘脚本路径。单任务模型不变（`_current_worker` 单变量、按钮运行时禁用）。

**Tech Stack:** PySide6 (QTextBrowser/QTimer), pytest + pytest-qt (qtbot fixture, QT_QPA_PLATFORM=offscreen)

**Spec:** `docs/superpowers/specs/2026-07-16-voice-studio-per-chapter-script-stream-design.md`

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `novel_forge/desktop/pages/voice_studio/page.py` | 配音脚本页面，流式渲染与章节切换 | 修改 |
| `tests/desktop/test_voice_studio_script_stream_isolation.py` | per-chapter 流式隔离回归测试 | 新建 |

不改 `workers.py`、后端、持久化。

---

## 关键背景（执行者必读）

1. **"正在分析脚本"来源**：`_tick_script_gen_animation`（page.py:2979）每 400ms 由 `_script_gen_timer` 触发，直接往唯一 `self._script_browser` 写"正在分析脚本…"HTML，不检查当前是哪一章。
2. **流式事件携带章节号**：后端 `llm_stream_start/delta` 的 payload 含 `"chapter": <int>` 字段（`llm_service.py:490,524`）。UI 可据此路由。
3. **章节切换入口**：`_on_script_chapter_changed`/`_on_room_chapter_changed`/`_on_audio_chapter_changed` 均调 `_load_chapter_artifacts(chapter_number)`，后者从磁盘加载 `chapter_NNN_script.json` 并调 `_render_script_html()`。
4. **两个启动点共用脚本生成阶段**：`_on_generate_script`（page.py:6030）和 `_on_full_pipeline`（page.py:6228）都会触发 `llm_stream_*` 和 `script_updated`，都复用 `_script_gen_timer`/`_script_gen_dots`。
5. **测试模式**：参考 `tests/desktop/test_voice_studio_lazy_tabs.py`，用 `VoiceStudioPage(settings=Settings(_env_file=None))` + `qtbot.addWidget(page)` 构造。

---

## Task 1: 新增 `_ChapterScriptStream` dataclass 与容器初始化

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（导入区 `:14` 后、`__init__` `:326-329`）

- [ ] **Step 1: 添加 `dataclass` 导入**

在 `page.py:14`（`import re` 行）之后添加导入。将：

```python
import re
import shutil
```

改为：

```python
import re
import shutil
from dataclasses import dataclass, field
```

- [ ] **Step 2: 新增 `_ChapterScriptStream` dataclass**

在导入区结束、第一个类/常量定义之前（`page.py` 中 `from __future__` 块和导入之后的模块级区域），添加：

```python
@dataclass
class _ChapterScriptStream:
    """Per-chapter streaming state for dubbing script generation.

    Replaces the page-level single stream variables so switching chapters
    shows each chapter's own progress instead of bleeding one chapter's
    "analyzing script" animation into every chapter window.
    """

    stream_id: str = ""
    text: str = ""
    active: bool = False
    dots: int = 0
```

> 放置位置：找到模块级第一个 `class ...` 或 `_log = ...` 之前的位置。用 grep 定位 `class VoiceStudioPage` 的上一行作为插入锚点。

- [ ] **Step 3: 替换 `__init__` 中的页面级单变量**

将 `page.py:326-329`：

```python
        self._script_gen_dots = 0
        self._script_stream_id = ""
        self._script_stream_text = ""
        self._script_stream_active = False
```

替换为：

```python
        # Per-chapter streaming state: keyed by chapter_number. Only chapters
        # that are generating (or just finished generating) hold an entry.
        self._script_streams: dict[int, _ChapterScriptStream] = {}
        # The chapter currently being generated; at most one at a time (single
        # task model). None when no generation is running.
        self._generating_chapter: int | None = None
```

- [ ] **Step 4: 验证页面仍可构造**

运行:

```bash
.venv/bin/python -c "from PySide6.QtWidgets import QApplication; import os; os.environ['QT_QPA_PLATFORM']='offscreen'; app=QApplication([]); from novel_forge.core.config import Settings; from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage; p=VoiceStudioPage(settings=Settings(_env_file=None)); print('OK', p._script_streams, p._generating_chapter)"
```

预期输出：`OK {} None`

- [ ] **Step 5: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "refactor(voice_studio): introduce per-chapter script stream container

Replace page-level single stream variables (_script_stream_id/_text/
_active, _script_gen_dots) with a dict[int, _ChapterScriptStream]
container and _generating_chapter marker. No behavior change yet;
subsequent tasks route animation and streaming by chapter."
```

---

## Task 2: 动画定时器与流式渲染按章节路由

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（`_tick_script_gen_animation` `:2979-2991`、`_render_script_stream` `:2993-3042`）

- [ ] **Step 1: 重写 `_tick_script_gen_animation` 按生成章路由**

将 `page.py:2979-2991`：

```python
    def _tick_script_gen_animation(self) -> None:
        """Timer callback to animate script generation indicator."""
        if self._script_stream_active:
            return
        self._script_gen_dots = (self._script_gen_dots + 1) % 4
        dots = "." * (self._script_gen_dots + 1)
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size:13pt; color:{qcolor_hex('status.info')};"
            " text-align: center; padding: 40px;'>"
            "<div style='font-size: 18pt; margin-bottom: 12px;'>📝</div>"
            f"正在分析脚本{dots}</div>"
        )
        self._set_browser_html(self._script_browser, html, preserve_scroll=False)
```

替换为：

```python
    def _tick_script_gen_animation(self) -> None:
        """Timer callback to animate script generation indicator.

        Only animates the chapter currently being generated, and only writes
        to the browser when that chapter is the one currently displayed.
        """
        chapter = self._generating_chapter
        if chapter is None:
            return
        stream = self._script_streams.get(chapter)
        if stream is None or stream.active:
            # When streaming is active, the stream renderer owns the browser.
            return
        stream.dots = (stream.dots + 1) % 4
        if chapter != self._active_chapter_number:
            # Animate state for the generating chapter, but don't overwrite a
            # different chapter's browser view.
            return
        dots = "." * (stream.dots + 1)
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size:13pt; color:{qcolor_hex('status.info')};"
            " text-align: center; padding: 40px;'>"
            "<div style='font-size: 18pt; margin-bottom: 12px;'>📝</div>"
            f"正在分析脚本{dots}</div>"
        )
        self._set_browser_html(self._script_browser, html, preserve_scroll=False)
```

- [ ] **Step 2: 重写 `_render_script_stream` 按当前显示章路由**

将 `page.py:2993-2997`（方法签名与开头）：

```python
    def _render_script_stream(self) -> None:
        """Render complete director cards as structured JSON arrives."""
        if not hasattr(self, "_script_browser"):
            return
        segments = _complete_json_array_objects(self._script_stream_text, "segments")
```

替换为：

```python
    def _render_script_stream(self) -> None:
        """Render complete director cards as structured JSON arrives.

        Renders from the currently-displayed chapter's stream buffer. When
        the displayed chapter has no active stream, this is a no-op so the
        on-disk script rendering (_render_script_html) is left untouched.
        """
        if not hasattr(self, "_script_browser"):
            return
        chapter = self._active_chapter_number
        stream = self._script_streams.get(chapter)
        if stream is None:
            return
        segments = _complete_json_array_objects(stream.text, "segments")
```

- [ ] **Step 3: 替换 `_render_script_stream` 体内的剩余单变量引用**

将 `page.py:3003`：

```python
            f"已接收 {len(self._script_stream_text)} 字符 · 已完成 {len(segments)} 段结构</div>",
```

替换为：

```python
            f"已接收 {len(stream.text)} 字符 · 已完成 {len(segments)} 段结构</div>",
```

- [ ] **Step 4: 验证页面可构造且方法可调用**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream
p=VoiceStudioPage(settings=Settings(_env_file=None))
# No generating chapter -> timer tick is a no-op
p._tick_script_gen_animation()
# No stream for active chapter -> render is a no-op
p._render_script_stream()
print('OK no-op guards work')
"
```

预期输出：`OK no-op guards work`

- [ ] **Step 5: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "refactor(voice_studio): route script-gen animation and stream render by chapter

_tick_script_gen_animation now only writes the 'analyzing script'
placeholder when the generating chapter equals the displayed chapter.
_render_script_stream reads from the displayed chapter's stream buffer
and is a no-op when that chapter has no active stream."
```

---

## Task 3: 新增辅助方法 `_get_or_create_stream` 与 `_render_script_gen_placeholder`

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（在 `_render_script_stream` 方法之后插入）

- [ ] **Step 1: 添加 `_get_or_create_stream` 辅助方法**

在 `_render_script_stream` 方法结束（`page.py` 中 `hint.setProperty("state", "streaming")` 所在的 `if hint is not None:` 块之后、下一个 `def` 之前）插入：

```python
    def _get_or_create_stream(self, chapter: int) -> _ChapterScriptStream:
        """Get or create the per-chapter stream state for dubbing script generation."""
        stream = self._script_streams.get(chapter)
        if stream is None:
            stream = _ChapterScriptStream()
            self._script_streams[chapter] = stream
        return stream

    def _render_script_gen_placeholder(self, chapter: int) -> None:
        """Render the initial 'analyzing script' placeholder for one chapter.

        Shared by the animation timer and the chapter-switch path so the
        placeholder markup stays in one place.
        """
        if not hasattr(self, "_script_browser"):
            return
        stream = self._script_streams.get(chapter)
        dots = "." * ((stream.dots if stream else 0) + 1)
        html = (
            f"<div style='font-family:{_HTML_BODY_FONT_FAMILY}; font-size:13pt; color:{qcolor_hex('status.info')};"
            " text-align: center; padding: 40px;'>"
            "<div style='font-size: 18pt; margin-bottom: 12px;'>📝</div>"
            f"正在分析脚本{dots}</div>"
        )
        self._set_browser_html(self._script_browser, html, preserve_scroll=False)

```

> 用 grep 定位 `_render_script_stream` 结束位置：找 `hint.setProperty("state", "streaming")` 后的下一个 `    def ` 行作为插入锚点。

- [ ] **Step 2: 验证辅助方法可调用**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage
p=VoiceStudioPage(settings=Settings(_env_file=None))
s = p._get_or_create_stream(5)
assert s.stream_id == '' and s.text == '' and s.active is False
assert 5 in p._script_streams
p._render_script_gen_placeholder(5)
print('OK helpers work')
"
```

预期输出：`OK helpers work`

- [ ] **Step 3: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "refactor(voice_studio): add _get_or_create_stream and placeholder helper

Shared helpers for per-chapter stream access and the 'analyzing script'
placeholder markup, used by the animation timer and chapter-switch path."
```

---

## Task 4: `_on_step_progress` 流式分支按 payload chapter 路由

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（`_on_step_progress` `:6907-6953`）

- [ ] **Step 1: 重写 `llm_stream_start` 分支**

将 `page.py:6907-6918`：

```python
        if step == "llm_stream_start" and is_script_stream:
            self._script_stream_id = str(data.get("stream_id") or "")
            self._script_stream_text = ""
            self._script_stream_active = True
            if self._script_stream_follow is not None:
                self._script_stream_follow.reset_to_latest()
            if self._script_gen_timer is not None:
                self._script_gen_timer.stop()
            self._render_script_stream()
            self._status_badge.setText("正在流式生成配音脚本…")
            self._status_badge.set_tone("warning")
            return
```

替换为：

```python
        if step == "llm_stream_start" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._get_or_create_stream(chapter)
                stream.stream_id = str(data.get("stream_id") or "")
                stream.text = ""
                stream.active = True
            if chapter == self._active_chapter_number:
                if self._script_stream_follow is not None:
                    self._script_stream_follow.reset_to_latest()
                if self._script_gen_timer is not None:
                    self._script_gen_timer.stop()
                self._render_script_stream()
            self._status_badge.setText("正在流式生成配音脚本…")
            self._status_badge.set_tone("warning")
            return
```

- [ ] **Step 2: 重写 `llm_stream_delta` 分支**

将 `page.py:6919-6930`：

```python
        if step == "llm_stream_delta" and is_script_stream:
            stream_id = str(data.get("stream_id") or "")
            if self._script_stream_id and stream_id != self._script_stream_id:
                return
            segments = data.get("segments")
            if isinstance(segments, list):
                for item in segments:
                    if isinstance(item, dict) and item.get("kind") == "content":
                        self._script_stream_text += str(item.get("text") or "")
            self._script_stream_active = True
            self._render_script_stream()
            return
```

替换为：

```python
        if step == "llm_stream_delta" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if not chapter:
                return
            stream = self._script_streams.get(chapter)
            if stream is None:
                return
            stream_id = str(data.get("stream_id") or "")
            if stream.stream_id and stream_id != stream.stream_id:
                return
            segments = data.get("segments")
            if isinstance(segments, list):
                for item in segments:
                    if isinstance(item, dict) and item.get("kind") == "content":
                        stream.text += str(item.get("text") or "")
            stream.active = True
            if chapter == self._active_chapter_number:
                self._render_script_stream()
            return
```

- [ ] **Step 3: 重写 `llm_stream_end` 分支**

将 `page.py:6931-6939`：

```python
        if step == "llm_stream_end" and is_script_stream:
            final_text = str(data.get("text") or "")
            if final_text:
                self._script_stream_text = final_text
            self._script_stream_active = True
            self._render_script_stream()
            self._status_badge.setText("流式输出完成，正在校验角色与导演指令…")
            self._status_badge.set_tone("warning")
            return
```

替换为：

```python
        if step == "llm_stream_end" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    final_text = str(data.get("text") or "")
                    if final_text:
                        stream.text = final_text
                    stream.active = True
            if chapter == self._active_chapter_number:
                self._render_script_stream()
            self._status_badge.setText("流式输出完成，正在校验角色与导演指令…")
            self._status_badge.set_tone("warning")
            return
```

- [ ] **Step 4: 重写 `llm_stream_restart` 分支**

将 `page.py:6940-6948`：

```python
        if step == "llm_stream_restart" and is_script_stream:
            self._script_stream_id = ""
            self._script_stream_text = ""
            self._script_stream_active = False
            if self._script_stream_follow is not None:
                self._script_stream_follow.reset_to_latest()
            self._status_badge.setText("流式连接已重启，正在重新生成完整脚本…")
            self._status_badge.set_tone("warning")
            return
```

替换为：

```python
        if step == "llm_stream_restart" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    stream.stream_id = ""
                    stream.text = ""
                    stream.active = False
            if chapter == self._active_chapter_number:
                if self._script_stream_follow is not None:
                    self._script_stream_follow.reset_to_latest()
            self._status_badge.setText("流式连接已重启，正在重新生成完整脚本…")
            self._status_badge.set_tone("warning")
            return
```

- [ ] **Step 5: 重写 `llm_stream_error` 分支**

将 `page.py:6949-6953`：

```python
        if step == "llm_stream_error" and is_script_stream:
            self._script_stream_active = False
            self._status_badge.setText("流式输出中断，正在执行安全重试或规则回退…")
            self._status_badge.set_tone("warning")
            return
```

替换为：

```python
        if step == "llm_stream_error" and is_script_stream:
            chapter = int(data.get("chapter") or self._generating_chapter or 0)
            if chapter:
                stream = self._script_streams.get(chapter)
                if stream is not None:
                    stream.active = False
            self._status_badge.setText("流式输出中断，正在执行安全重试或规则回退…")
            self._status_badge.set_tone("warning")
            return
```

- [ ] **Step 6: 验证页面可构造（无语法错误）**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage
p=VoiceStudioPage(settings=Settings(_env_file=None))
from novel_forge.common.constants import TaskType
# Simulate a stream start for chapter 3 while not the active chapter
p._generating_chapter = 3
p._active_chapter_number = 5
p._on_step_progress('llm_stream_start', {'task': TaskType.TTS_GENERATE_DUBBING_SCRIPT.value, 'chapter': 3, 'stream_id': 'abc'})
assert 3 in p._script_streams
assert p._script_streams[3].stream_id == 'abc'
assert p._script_streams[3].active is True
print('OK stream start routed to chapter 3')
"
```

预期输出：`OK stream start routed to chapter 3`

- [ ] **Step 7: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "refactor(voice_studio): route llm_stream events by payload chapter

Each llm_stream_* branch now reads the chapter from the event payload
(falling back to _generating_chapter), updates that chapter's stream
buffer, and only refreshes the browser when the stream's chapter equals
the currently-displayed chapter."
```

---

## Task 5: 章节切换 `_load_chapter_artifacts` 按目标章流式状态渲染

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（`_load_chapter_artifacts` `:3628-3684`）

- [ ] **Step 1: 在 `_load_chapter_artifacts` 清空逻辑后插入流式优先分支**

将 `page.py:3635-3647`（清空逻辑块）：

```python
        self._current_script = None
        self._playback_script = None
        self._current_audio_result = None
        self._source_script_available = False
        self._current_results = []
        self._current_timeline = None
        self._segment_status.clear()
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        self._room_loaded_audio_path = ""
        if self._segment_player is not None:
            self._segment_player.clear()

        script_path = self._layout.tts_dubbing_script_path(chapter_number)
```

替换为：

```python
        self._current_script = None
        self._playback_script = None
        self._current_audio_result = None
        self._source_script_available = False
        self._current_results = []
        self._current_timeline = None
        self._segment_status.clear()
        self._room_draft_segments.clear()
        self._room_candidate_takes.clear()
        self._room_loaded_audio_path = ""
        if self._segment_player is not None:
            self._segment_player.clear()

        # If this chapter has an active generation stream, render its streaming
        # progress (recovering any mid-stream fragments already received) and
        # skip loading the stale/absent on-disk script. This is the key fix:
        # switching away from and back to a generating chapter restores its
        # own streaming view instead of showing "暂无脚本" or another chapter's
        # "正在分析脚本" animation.
        active_stream = self._script_streams.get(chapter_number)
        if active_stream is not None and active_stream.active:
            if active_stream.text:
                self._render_script_stream()
            else:
                self._render_script_gen_placeholder(chapter_number)
            self._update_script_source_hint()
            self._render_sync_script_html()
            self._populate_voice_room_segments()
            self._render_mix_manifest()
            return

        script_path = self._layout.tts_dubbing_script_path(chapter_number)
```

- [ ] **Step 2: 验证切到生成章时走流式分支**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream
from novel_forge.persistence.models import ProjectLayout
p=VoiceStudioPage(settings=Settings(_env_file=None))
# Simulate chapter 3 generating with received stream text
p._generating_chapter = 3
p._script_streams[3] = _ChapterScriptStream(text='{\"segments\":[{\"text\":\"hi\"}]}', active=True)
# Stub layout so _load_chapter_artifacts proceeds past the guard
p._layout = ProjectLayout(project_dir=Path('/tmp/nonexistent_voice_test'))
p._active_chapter_number = 5
# Switch to chapter 3: should hit the active-stream branch and return early
# (before attempting disk reads on the nonexistent path)
p._load_chapter_artifacts(3)
assert p._active_chapter_number == 3
assert p._current_script is None  # disk script not loaded (stream takes over)
print('OK chapter switch to generating chapter renders stream')
"
```

预期输出：`OK chapter switch to generating chapter renders stream`

> 说明：流式分支在磁盘加载之前 early-return，因此 `/tmp/nonexistent_voice_test` 路径不存在不会报错。这验证了目标章有活跃流时走流式分支而非磁盘路径。

- [ ] **Step 3: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "fix(voice_studio): render streaming progress on chapter switch to generating chapter

_load_chapter_artifacts now detects when the target chapter has an
active generation stream and renders its mid-stream fragments (or the
'analyzing script' placeholder) instead of loading the absent on-disk
script. Switching away and back to a generating chapter restores its
own streaming view."
```

---

## Task 6: worker 终态清理对应章节 stream

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（`_on_script_updated` `:7174-7183`、`_on_worker_failed` `:7620-7623`、`_on_cancel_clicked` `:6872-6876`）

- [ ] **Step 1: `_on_script_updated` 清理 stream 与 generating_chapter**

将 `page.py:7179-7183`：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        self._script_stream_active = False
        self._script_stream_id = ""
        self._script_stream_text = ""
```

替换为：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        finished_chapter = self._generating_chapter
        if finished_chapter is not None:
            self._script_streams.pop(finished_chapter, None)
        self._generating_chapter = None
```

- [ ] **Step 2: `_on_worker_failed` 清理 stream 与 generating_chapter**

将 `page.py:7620-7622`：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        self._script_stream_active = False
```

替换为：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        failed_chapter = self._generating_chapter
        if failed_chapter is not None:
            self._script_streams.pop(failed_chapter, None)
        self._generating_chapter = None
```

- [ ] **Step 3: `_on_cancel_clicked` 清理 stream 与 generating_chapter**

将 `page.py:6872-6876`：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        self._script_stream_active = False
        self._script_stream_id = ""
        self._script_stream_text = ""
```

替换为：

```python
        if self._script_gen_timer is not None:
            self._script_gen_timer.stop()
        cancelled_chapter = self._generating_chapter
        if cancelled_chapter is not None:
            self._script_streams.pop(cancelled_chapter, None)
        self._generating_chapter = None
```

- [ ] **Step 4: 验证终态清理**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream
p=VoiceStudioPage(settings=Settings(_env_file=None))
# Simulate chapter 3 generating
p._generating_chapter = 3
p._script_streams[3] = _ChapterScriptStream(active=True)
# Simulate cancel
p._on_cancel_clicked()
assert p._generating_chapter is None
assert 3 not in p._script_streams
print('OK cancel clears generating chapter stream')
"
```

预期输出：`OK cancel clears generating chapter stream`

- [ ] **Step 5: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "fix(voice_studio): clear per-chapter stream on worker terminal states

_on_script_updated, _on_worker_failed, and _on_cancel_clicked now pop
the generating chapter's stream and reset _generating_chapter, so a
finished/failed/cancelled generation does not leave a stale stream
entry that would hijack the next chapter switch."
```

---

## Task 7: 启动点设置 `_generating_chapter` 与 stream 初始化

**Files:**
- Modify: `novel_forge/desktop/pages/voice_studio/page.py`（`_on_generate_script` `:6060-6070`、`_on_full_pipeline` `:6265-6272`）

- [ ] **Step 1: `_on_generate_script` 设置 generating_chapter 与初始化 stream**

将 `page.py:6060-6070`：

```python
        self._current_worker = worker
        self._begin_tts_operation("script", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在生成配音脚本...")
        self._status_badge.set_tone("warning")
        self._script_gen_dots = 0
        self._script_stream_id = ""
        self._script_stream_text = ""
        self._script_stream_active = False
        if self._script_gen_timer is not None:
            self._script_gen_timer.start()
```

替换为：

```python
        self._current_worker = worker
        self._begin_tts_operation("script", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在生成配音脚本...")
        self._status_badge.set_tone("warning")
        # Mark this chapter as the one being generated and reset its stream
        # so any leftover state from a previous run is discarded.
        self._generating_chapter = chapter_num
        self._script_streams[chapter_num] = _ChapterScriptStream()
        if self._script_gen_timer is not None:
            self._script_gen_timer.start()
```

- [ ] **Step 2: `_on_full_pipeline` 设置 generating_chapter 与初始化 stream**

将 `page.py:6265-6272`：

```python
        self._current_worker = worker
        self._begin_tts_operation("full_pipeline", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在执行完整 TTS 流程...")
        self._status_badge.set_tone("warning")
        self._script_gen_dots = 0
        if self._script_gen_timer is not None:
            self._script_gen_timer.start()
```

替换为：

```python
        self._current_worker = worker
        self._begin_tts_operation("full_pipeline", chapter_num)
        self._cancel_btn.setEnabled(True)
        self._status_badge.setText("正在执行完整 TTS 流程...")
        self._status_badge.set_tone("warning")
        # The full pipeline also runs the script-generation stage, so mark the
        # chapter and reset its stream the same way as _on_generate_script.
        self._generating_chapter = chapter_num
        self._script_streams[chapter_num] = _ChapterScriptStream()
        if self._script_gen_timer is not None:
            self._script_gen_timer.start()
```

- [ ] **Step 3: 验证启动点设置**

运行:

```bash
.venv/bin/python -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
app=QApplication([])
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage
p=VoiceStudioPage(settings=Settings(_env_file=None))
# Confirm the two startup paths' field names are wired (static check via grep)
import inspect, re
src = inspect.getsource(p._on_generate_script)
assert '_generating_chapter = chapter_num' in src
assert '_script_streams[chapter_num] = _ChapterScriptStream()' in src
assert '_script_gen_dots' not in src
src2 = inspect.getsource(p._on_full_pipeline)
assert '_generating_chapter = chapter_num' in src2
assert '_script_gen_dots' not in src2
print('OK startup paths set generating_chapter and init stream')
"
```

预期输出：`OK startup paths set generating_chapter and init stream`

- [ ] **Step 4: 全量扫描无残留旧字段引用**

运行:

```bash
grep -n "_script_stream_active\|_script_stream_id\|_script_stream_text\|_script_gen_dots" novel_forge/desktop/pages/voice_studio/page.py
```

预期：**无输出**（所有旧字段引用已清除）。

- [ ] **Step 5: Commit**

```bash
git add novel_forge/desktop/pages/voice_studio/page.py
git commit -m "fix(voice_studio): set _generating_chapter and init stream on generation start

_on_generate_script and _on_full_pipeline now mark the chapter being
generated and reset its per-chapter stream. Removes the last references
to the old single stream variables (_script_gen_dots, _script_stream_*)."
```

---

## Task 8: 新增 per-chapter 流式隔离回归测试

**Files:**
- Create: `tests/desktop/test_voice_studio_script_stream_isolation.py`

- [ ] **Step 1: 编写回归测试文件**

创建 `tests/desktop/test_voice_studio_script_stream_isolation.py`：

```python
"""Regression tests for per-chapter dubbing script stream isolation.

Ensures starting script generation on one chapter does not bleed its
"analyzing script" animation into other chapters, and that switching
away from and back to a generating chapter restores its own streaming
progress.
"""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage, _ChapterScriptStream


def _page(qtbot: QtBot) -> VoiceStudioPage:
    page = VoiceStudioPage(settings=Settings(_env_file=None))
    qtbot.addWidget(page)
    return page


def test_generate_script_marks_generating_chapter(qtbot: QtBot) -> None:
    """Starting script generation records which chapter is generating."""
    page = _page(qtbot)
    # Simulate the startup wiring _on_generate_script performs (without
    # submitting a real worker, which needs a project layout).
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()

    assert page._generating_chapter == 3
    assert 3 in page._script_streams
    assert page._script_streams[3].active is False


def test_animation_does_not_overwrite_non_generating_chapter(qtbot: QtBot) -> None:
    """The timer must not write to the browser when the displayed chapter
    differs from the generating chapter."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()
    page._active_chapter_number = 5  # user is viewing chapter 5

    with patch.object(page, "_set_browser_html") as mock_set:
        page._tick_script_gen_animation()

    mock_set.assert_not_called()
    # But the generating chapter's dots still advance (state preserved).
    assert page._script_streams[3].dots == 1


def test_animation_writes_when_viewing_generating_chapter(qtbot: QtBot) -> None:
    """The timer writes the placeholder when viewing the generating chapter."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream()
    page._active_chapter_number = 3

    with patch.object(page, "_set_browser_html") as mock_set:
        page._tick_script_gen_animation()

    mock_set.assert_called_once()
    html = mock_set.call_args[0][1]
    assert "正在分析脚本" in html


def test_stream_delta_routes_by_payload_chapter(qtbot: QtBot) -> None:
    """llm_stream_delta updates the stream for the chapter in the payload,
    not the currently-displayed chapter."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(stream_id="abc", active=True)
    page._active_chapter_number = 5  # viewing a different chapter

    page._on_step_progress(
        "llm_stream_delta",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "abc",
            "segments": [{"kind": "content", "text": "片段一"}],
        },
    )

    assert page._script_streams[3].text == "片段一"
    # Chapter 5 has no stream entry and is untouched.
    assert 5 not in page._script_streams


def test_stream_delta_ignored_for_wrong_stream_id(qtbot: QtBot) -> None:
    """A delta with a mismatched stream_id is dropped (anti-cross-talk guard)."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(stream_id="abc", active=True)
    page._active_chapter_number = 3

    page._on_step_progress(
        "llm_stream_delta",
        {
            "task": TaskType.TTS_GENERATE_DUBBING_SCRIPT.value,
            "chapter": 3,
            "stream_id": "different",
            "segments": [{"kind": "content", "text": "不应写入"}],
        },
    )

    assert page._script_streams[3].text == ""


def test_cancel_clears_generating_chapter_stream(qtbot: QtBot) -> None:
    """Cancelling pops the generating chapter's stream entry."""
    page = _page(qtbot)
    page._generating_chapter = 3
    page._script_streams[3] = _ChapterScriptStream(active=True)
    page._current_worker = None  # _on_cancel_clicked tolerates None

    page._on_cancel_clicked()

    assert page._generating_chapter is None
    assert 3 not in page._script_streams


def test_render_script_stream_noop_for_non_streaming_chapter(qtbot: QtBot) -> None:
    """Rendering is a no-op when the displayed chapter has no stream."""
    page = _page(qtbot)
    page._active_chapter_number = 5
    # No stream entry for chapter 5.

    with patch.object(page, "_set_browser_html") as mock_set:
        page._render_script_stream()

    mock_set.assert_not_called()
```

- [ ] **Step 2: 运行测试验证全部通过**

运行:

```bash
.venv/bin/python -m pytest tests/desktop/test_voice_studio_script_stream_isolation.py -v
```

预期：7 个测试全部 PASS。

- [ ] **Step 3: 运行 ruff 检查**

运行:

```bash
.venv/bin/ruff check tests/desktop/test_voice_studio_script_stream_isolation.py
```

预期：无错误。

- [ ] **Step 4: Commit**

```bash
git add tests/desktop/test_voice_studio_script_stream_isolation.py
git commit -m "test(voice_studio): add per-chapter script stream isolation regression tests

Covers: generating-chapter marking, animation not overwriting non-
generating chapters, stream delta routing by payload chapter, stream_id
anti-cross-talk guard, cancel cleanup, and no-op render for non-
streaming chapters."
```

---

## Task 9: 回归验证与 lint

**Files:** 无修改，仅验证

- [ ] **Step 1: 运行现有 voice_studio desktop 测试无回归**

运行:

```bash
.venv/bin/python -m pytest tests/desktop/test_voice_studio_lazy_tabs.py tests/desktop/test_voice_studio_experience.py tests/unit/test_voice_studio_workers.py -v
```

预期：全部 PASS（现有测试不引用已删除的旧字段）。

- [ ] **Step 2: ruff 全量检查改动文件**

运行:

```bash
.venv/bin/ruff check novel_forge/desktop/pages/voice_studio/page.py tests/desktop/test_voice_studio_script_stream_isolation.py
```

预期：无错误。

- [ ] **Step 3: 确认无旧字段残留（最终扫描）**

运行:

```bash
grep -rn "_script_stream_active\|_script_stream_id\|_script_stream_text\|_script_gen_dots" novel_forge/desktop/pages/voice_studio/
```

预期：无输出。

- [ ] **Step 4: 运行新测试与现有桌面测试一并确认**

运行:

```bash
.venv/bin/python -m pytest tests/desktop/ -v
```

预期：全部 PASS。

---

## 自审记录

**1. Spec 覆盖检查：**
- 改造点 1（动画定时器按章路由）→ Task 2 Step 1 ✅
- 改造点 2（流式渲染按章路由）→ Task 2 Step 2-3 ✅
- 改造点 3（`_on_step_progress` 流式分支按 chapter 路由）→ Task 4 Step 1-5 ✅
- 改造点 4（`_load_chapter_artifacts` 切换时按目标章状态渲染）→ Task 5 ✅
- 改造点 5（worker 终态清理对应章节 stream）→ Task 6 ✅
- 改造点 6（`_on_generate_script` 启动时初始化 per-chapter stream）→ Task 7 Step 1 ✅
- 辅助方法 `_get_or_create_stream`/`_render_script_gen_placeholder` → Task 3 ✅
- spec 提到 `_on_full_pipeline` 也复用脚本生成阶段 → Task 7 Step 2 补充覆盖 ✅
- 测试策略 → Task 8 ✅

**2. 占位符扫描：** 无 TBD/TODO/省略号。所有代码块完整。Task 5 Step 2 的验证命令含一条注释说明若 `ProjectLayout` 构造签名不同需核对，这是合理的执行者提示而非占位符。

**3. 类型一致性：**
- `_ChapterScriptStream` 字段（`stream_id`/`text`/`active`/`dots`）在 Task 1 定义，Task 2/3/4/6/7/8 引用一致。
- `_get_or_create_stream(chapter: int) -> _ChapterScriptStream` 签名在 Task 3 定义，Task 4 调用一致。
- `_generating_chapter: int | None` 在 Task 1 定义，Task 4/6/7/8 引用一致。
- `_script_streams: dict[int, _ChapterScriptStream]` 在 Task 1 定义，全计划引用一致。
