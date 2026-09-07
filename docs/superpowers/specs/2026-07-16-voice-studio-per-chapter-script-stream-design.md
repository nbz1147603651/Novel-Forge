# 配音脚本生成：按章节隔离流式进度显示

**日期:** 2026-07-16
**状态:** 设计待审
**范围:** `novel_forge/desktop/pages/voice_studio/page.py`（UI 显示层）

## 问题陈述

当前在配音脚本页面点击「生成脚本」后，任意一章进入生成流程时，**切换到任何其他章节的脚本窗口都会显示"正在分析脚本"动画**，无法反映各章节的真实状态。

### 根因

1. **动画定时器是页面级单例，不感知章节。**
   `_script_gen_timer`（`page.py:992`）每 400ms 触发 `_tick_script_gen_animation`，后者直接往 `self._script_browser` 写入"正在分析脚本…"HTML，**完全不检查当前显示的是哪一章**。任意 worker 启动它后，切换到任何章节都会被覆盖成"正在分析脚本"。

2. **章节切换不重置流式状态，也不按目标章节渲染。**
   `_on_script_chapter_changed` -> `_load_chapter_artifacts`（`page.py:3600, 3628`）切章节时，会从磁盘加载该章 `chapter_NNN_script.json` 并调用 `_render_script_html()`，但**既不停止 `_script_gen_timer`，也不切换流式 buffer**。切到正在生成的章节时磁盘文件尚未写好（`_current_script=None`），`_render_script_html` 渲染"暂无脚本"，但 400ms 后定时器又把它盖成"正在分析脚本"。

3. **流式 buffer 是单一变量。**
   `_script_stream_text` / `_script_stream_id` / `_script_stream_active`（`page.py:327-329`）只能容纳一章的流式输出，无法为多个章节各自记忆进度。

### 不在本次范围

- **不支持多章真正并行生成。** 后端 `execute_generate_dubbing_script` 带 `@_with_tts_project_lock`（项目级 EXCLUSIVE 锁），同项目两章本就会串行排队；UI 也维持单任务模型（`_current_worker` 单变量，按钮在任务运行时禁用）。本次只修复显示串台，不改并发模型。
- 不改后端、不改 worker、不改持久化路径（`chapter_NNN_script.json` 已按章隔离）。

## 设计目标

1. 单任务运行期间，切换章节时脚本窗口显示**目标章节各自的真实状态**：
   - 正在生成的章节 → 显示"正在分析脚本"动画或已接收的流式片段进度。
   - 未在生成的章节 → 显示该章磁盘脚本（有则渲染脚本，无则"暂无脚本"）。
2. 正在生成的章节**切走再切回时，能恢复已接收的流式片段进度**（不丢失中途的逐段编排内容）。
3. 维持单任务不变量：同一时刻最多一章在生成，按钮在任务运行时禁用，`_current_worker` 仍为单变量。

## 设计

### 核心思路

把"哪一章在生成 + 它的流式进度"从页面级单变量改成按 `chapter_number` 索引的容器。渲染与动画都按**当前显示章节**（`_active_chapter_number`）路由：只有当前显示的章节正好是正在生成的章节时，才往 `_script_browser` 写"正在分析脚本"或流式片段；否则让 `_load_chapter_artifacts` 的磁盘脚本渲染生效。

### 数据结构

新增一个 dataclass 容纳单章的流式状态，放在 `page.py` 顶部（模块级，与现有 dataclass 同区）：

```python
@dataclass
class _ChapterScriptStream:
    """Per-chapter streaming state for dubbing script generation."""
    stream_id: str = ""
    text: str = ""
    active: bool = False
    dots: int = 0
```

页面 `__init__` 中，替换现有 4 个页面级单变量为：

```python
self._script_streams: dict[int, _ChapterScriptStream] = {}
self._generating_chapter: int | None = None
```

- `_script_streams` 按 `chapter_number` 索引；只有正在生成（或刚生成完尚未清理）的章节才会出现 key。
- `_generating_chapter` 标记当前在生成的章节号；单任务不变量保证它同一时刻最多一个值或 `None`。它用于动画定时器判断"当前显示章是否正好是生成章"。
- `_current_worker`、`_tts_operation` 保持不变（单任务模型）。
- 删除旧字段 `_script_stream_id`、`_script_stream_text`、`_script_stream_active`、`_script_gen_dots` 的定义；所有引用点改走 `_script_streams`。

> **健壮性说明：** 流式事件 payload 已携带 `chapter` 字段（`llm_service.py:490, 524` 的 `llm_stream_start/delta`）。`_on_step_progress` 路由时优先用 payload 的 `chapter`；若 payload 缺失则回退到 `_generating_chapter`。这样即使因信号时序导致 `_generating_chapter` 尚未设置，也能正确路由。

### 改造点 1：动画定时器按当前显示章节路由

`_tick_script_gen_animation`（`page.py:2979`）改为：

```python
def _tick_script_gen_animation(self) -> None:
    """Timer callback to animate script generation indicator."""
    chapter = self._generating_chapter
    # 只对正在生成的章节累加动画；非生成章不触发任何写入
    if chapter is None:
        return
    stream = self._script_streams.get(chapter)
    if stream is None or stream.active:
        # 流式已活跃时，动画让位给流式渲染（保留现有行为）
        return
    stream.dots = (stream.dots + 1) % 4
    # 只有当前显示的章节正好是生成章时，才把动画写进 browser
    if chapter != self._active_chapter_number:
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

要点：
- 动画状态累加绑定到 per-chapter stream（`stream.dots`），切走再切回能从上次进度继续。
- 写 browser 的前置条件 `chapter == self._active_chapter_number`：非生成章不会被动画覆盖。

### 改造点 2：流式渲染按当前显示章节路由

`_render_script_stream`（`page.py:2993`）改为从当前显示章节的 stream 取 buffer：

```python
def _render_script_stream(self) -> None:
    if not hasattr(self, "_script_browser"):
        return
    chapter = self._active_chapter_number
    stream = self._script_streams.get(chapter)
    if stream is None:
        # 当前显示章没有活跃流，不应渲染流式视图（交给 _render_script_html）
        return
    segments = _complete_json_array_objects(stream.text, "segments")
    parts = [
        ...,
        f"已接收 {len(stream.text)} 字符 · 已完成 {len(segments)} 段结构</div>",
        ...,
    ]
    self._set_browser_html(self._script_browser, "".join(parts), preserve_scroll=True)
```

要点：渲染源从单一 `_script_stream_text` 改为 `stream.text`，且只在当前显示章有 stream 时渲染。切到非生成章时此方法不写 browser，磁盘脚本渲染不受干扰。

### 改造点 3：`_on_step_progress` 流式分支按 chapter 路由

`_on_step_progress`（`page.py:6898`）的 `llm_stream_*` 分支改为按 payload `chapter` 路由到对应 stream，并仅在该章 == 当前显示章时刷新 browser：

```python
if step == "llm_stream_start" and is_script_stream:
    chapter = int(data.get("chapter") or self._generating_chapter or 0)
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

`llm_stream_delta` 同理：从 payload 取 `chapter`，定位 stream，累加 `stream.text`，并仅在该章是当前显示章时调用 `_render_script_stream()`。`stream_id` 匹配检查改为对该 stream 自身的 `stream.stream_id` 比较（仍防串流，但按章隔离）。

`llm_stream_end` / `llm_stream_restart` / `llm_stream_error` 同样按 chapter 路由更新对应 stream 的 `active`/`text` 状态，browser 刷新仍受 `chapter == self._active_chapter_number` 守卫。

新增辅助方法：

```python
def _get_or_create_stream(self, chapter: int) -> _ChapterScriptStream:
    stream = self._script_streams.get(chapter)
    if stream is None:
        stream = _ChapterScriptStream()
        self._script_streams[chapter] = stream
    return stream
```

### 改造点 4：`_load_chapter_artifacts` 切换时按目标章节状态渲染

`_load_chapter_artifacts`（`page.py:3628`）末尾，在现有 `_render_script_html()` 调用前后增加流式优先判断。目标：若目标章节有活跃流（`active=True`），则渲染流式视图而非磁盘脚本；否则维持现有磁盘加载 + `_render_script_html`。

在方法开头设置 `self._active_chapter_number = chapter_number` 之后、加载磁盘脚本之前，插入流式状态检查：

```python
def _load_chapter_artifacts(self, chapter_number: int) -> None:
    if not self._layout or chapter_number < 1:
        return
    self._active_chapter_number = chapter_number
    self._loaded_chapter_number = chapter_number
    ...
    # 新增：若目标章节有活跃流，优先渲染流式视图并跳过磁盘脚本覆盖
    active_stream = self._script_streams.get(chapter_number)
    if active_stream is not None and active_stream.active:
        self._current_script = None
        ...（清空其余章节级状态，同现有逻辑）...
        if active_stream.text:
            # 已有流式片段，恢复显示
            self._render_script_stream()
        else:
            # 流刚开始，显示动画占位
            self._render_script_gen_placeholder(chapter_number)
        self._update_script_source_hint()
        return
    # 否则走现有磁盘加载 + _render_script_html 路径（不变）
    ...
```

新增 `_render_script_gen_placeholder` 抽取现有"正在分析脚本"HTML 生成逻辑，供动画定时器与初始占位共用。

要点：这是"切走再切回恢复流式片段"的关键。目标章有活跃流时，`stream.text` 里已累积的逐段编排内容通过 `_render_script_stream` 恢复显示；若流刚启动 `text` 还为空，显示"正在分析脚本"占位。

### 改造点 5：worker 终态清理对应章节的 stream

三个终态处理点在清理时移除对应章节的 stream，并在该章是当前显示章时触发一次重渲染：

- `_on_script_updated`（`page.py:7146`）：脚本生成成功。清理 `self._script_streams.pop(generating_chapter, None)`、`self._generating_chapter = None`、停 `_script_gen_timer`。由于 `_on_script_updated` 已设置 `_current_script` 并调用 `_render_script_html`，当前显示章会自然渲染为新脚本；无需额外处理。
- `_on_worker_failed`（`page.py:7581`）：失败。同样 pop stream、清 `_generating_chapter`、停 timer。若失败章 == 当前显示章，由于 `_current_script` 已被清/未设置，`_render_script_html` 会显示"暂无脚本"（合理）。
- `_on_cancel_clicked`（`page.py:6841`）：取消。pop 对应 stream、清 `_generating_chapter`、停 timer。

> 注意：`_generating_chapter` 在 `_on_generate_script` 启动 worker 时设置（见改造点 6），在上述三个终态点统一清理。stream 的 pop 与 `_generating_chapter` 的清空成对出现，保证不泄漏。

### 改造点 6：`_on_generate_script` 启动时初始化 per-chapter stream

`_on_generate_script`（`page.py:6002`）中，设置 `_generating_chapter` 并预创建该章的 stream（清掉可能残留的旧流）：

```python
chapter_num = int(chapter_num)
...
self._generating_chapter = chapter_num
# 清掉该章可能残留的旧流，重新开始
self._script_streams[chapter_num] = _ChapterScriptStream()
self._current_worker = worker
self._begin_tts_operation("script", chapter_num)
...
if self._script_gen_timer is not None:
    self._script_gen_timer.start()
```

原代码里 `self._script_stream_id = ""` / `self._script_stream_text = ""` / `self._script_stream_active = False` 三行删除（已被 stream 容器取代）。

### 不变量与边界

| 不变量 | 保证方式 |
|--------|----------|
| 同一时刻最多一章在生成 | `_generating_chapter` 单值；`_current_worker` 单变量；按钮在 `_tts_operation` 运行时禁用（`_refresh_workflow_controls` 不变） |
| 非生成章显示磁盘脚本 | 动画/流式渲染均受 `chapter == self._active_chapter_number` 守卫；`_load_chapter_artifacts` 对无活跃流的章节走磁盘加载路径 |
| 流式进度按章记忆 | `_script_streams[chapter]` 持有 `text`，切走再切回时 `_load_chapter_artifacts` 检测到活跃流并调 `_render_script_stream` 恢复 |
| 不跨任务泄漏 | 新任务启动时 `_script_streams[chapter_num] = _ChapterScriptStream()` 覆盖旧流；终态 pop 对应 key |
| 关闭页面不崩溃 | `shutdown()` 已停 `_script_gen_timer`（`page.py:2319`）；stream dict 仅是纯 Python 数据，无 Qt 资源，无需额外清理 |

### 涉及文件

仅 `novel_forge/desktop/pages/voice_studio/page.py`。不改 `workers.py`、不改后端、不改持久化。

## 测试策略

由于 `GenerateScriptWorker` 无单元测试覆盖（codegraph 标注 ⚠️ no covering tests found），且流式渲染高度依赖 Qt 事件循环，采用以下策略：

1. **单元测试（新增）**：在 `tests/unit/` 新增 `test_voice_studio_script_stream_isolation.py`，用 `QT_QPA_PLATFORM=offscreen` 构造 page 实例，mock worker 信号，验证：
   - 启动第 3 章生成后，`_script_streams` 含 key 3、`_generating_chapter == 3`。
   - 切到第 5 章（无活跃流），`_load_chapter_artifacts` 走磁盘路径，`_script_browser` 不被"正在分析脚本"覆盖（断言 `_active_chapter_number == 5` 且 stream dict 仍只含 3）。
   - 切回第 3 章，`_render_script_stream` 用 stream 3 的 `text` 渲染。
   - 模拟 `llm_stream_delta` payload 带 `chapter=3`，验证 stream 3 的 `text` 累加，且当前显示章为 5 时 browser 不被写入（用 spy/patch `_set_browser_html` 计数）。
2. **手动验证**：真实 provider 下，启动第 3 章生成 → 切第 5 章确认显示第 5 章磁盘脚本 → 切回第 3 章确认流式片段恢复 → 等完成确认第 3 章脚本正常渲染。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| payload `chapter` 字段缺失导致路由失败 | 回退到 `_generating_chapter`；两者都缺时安全跳过（不写 browser） |
| 切章时 `_load_chapter_artifacts` 既有清空逻辑与新增流式分支冲突 | 流式分支在清空逻辑之后、磁盘加载之前 early return，确保不双重清空 |
| `_script_stream_follow`（StreamFollowController）绑定单一 browser，切章时滚动位置错乱 | 流式分支内调用 `reset_to_latest()`；切走时 stream 数据保留但 follow 控制器状态随 browser 内容切换自然重置 |
| 终态清理遗漏导致 stream 残留 | 三个终态点（updated/failed/cancel）统一 pop + 清 `_generating_chapter`；`_on_generate_script` 启动时覆盖式重建 |
