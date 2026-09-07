# Novel Forge Desktop UI 全栈优化 — 设计文档（v2，校准后）

> **文档状态**：DRAFT v3 — 第三轮 review，待用户最终批准
> **日期**：2026-07-02
> **作者**：ZCode (brainstorming skill flow)
>
> **v1 → v2 变更**：根据用户 review 重写。修正了：现状文件/行数过期、与代码脱节（lifecycle/event_bus/python 版本配置）、lazy markdown 大文档假设不成立、page shutdown 等待全池不安全、embedder 单例线程缺 shutdown。
>
> **v2 → v3 变更**：根据用户第二轮 review 6 个修正点 + 2 个文档小问题：
> 1. `BaseJobWorker` 位置从 `jobs.py` 抽出到 `desktop/workers/base.py`
> 2. `BaseJobWorkerSignals` 不统一业务信号形状（子类通过 `signals_cls` 自定义）
> 3. 全局 3 pool wait 从 `JobManager.shutdown()` 迁到 `thread_pools.shutdown_desktop_thread_pools()`
> 4. `JobSnapshotCache` 加 `_SnapshotSignature(run_dir file_count + latest_mtime_ns + total_size_bytes)`
> 5. chunked HTML 方案改为保守：清理假增量语义 + bench 脚本 + 可插拔 ChunkedHtmlSetter（不启用，默认空实现）
> 6. CI desktop-tests job 显式 `--ignore-glob='*_visual.py' --ignore-glob='test_visual_regression.py'`
> 7. §1.9 冲突表标反（"无冲突"误导）→ 列"歧义 / 物理状态 / 操作"决策表
> 8. §11.2 测试计数与列表一致
> 9. 顺序重排为：M5.1 → M5.2 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7

---

## 0. 关键变更（v1 → v2）

| # | v1 错误 | v2 修正 |
|---|---|---|
| 1 | 写"73 个 py、约 28k 行" | **135 个 py、约 108290 行**（已实测 `wc -l`） |
| 2 | Top 巨文件 = window.py/jobs.py/settings_page.py | **document_renderers.py:4850 > window.py:4728 > document_renderer_story_artifacts.py:4518 > document_renderer_reports.py:4244 > jobs.py:3583 > settings_page_parameters.py:3355**（多出 3 个前所未见的巨型 renderer） |
| 3 | settings_page.py:3055 行未拆分 | **已部分拆：settings_page.py:1302 + 7 个 settings_*.py 子模块**（settings_page_parameters.py 仍 3355 行） |
| 4 | `jobs.py:1358` 无 close() | **`DesktopJobManager.shutdown(wait_ms=500)` 已存在** (jobs.py:3229-3296)，覆盖大部分 spec 要求 |
| 5 | `_app_event_timer` 永不 stop | **shutdown 内已 stop 两次（line 3249/3290）**；仍缺 `deleteLater` |
| 6 | path 冲突：spec 想创建 `components/task_focus/loaders.py` 与现有 `task_focus.py` 冲突 | **Phase 改为：先 git mv 把 file 转 package，再加新模块；任何 Phase 都要先列 file→package 迁移顺序** |
| 7 | window.py/jobs.py 拆 package 与现有 file 冲突 | 同上 |
| 8 | EventBus 用 `self.changed.connect(...)` | **实际是 `_section_changed/_project_changed/_job_completed` 三个 signal（私有，前缀下划线）**；改 `subscribe()` API 即可，对应 dataclass 事件保持现状 |
| 9 | QueuedConnection 改写破坏同步语义 | **保持 AutoConnection（Qt 默认同/跨线程分别 DirectConnection/QueuedConnection），不强制显式 QueuedConnection；改用 section-level `QTimer.singleShot(0, defer)` 而非改 connection type** |
| 10 | page shutdown 等待全 3 pool | **不安全，会被无关任务拖住**；改为 page 只取消并断开自己持有的 worker + 不 wait pool；window._pre_close_cleanup 统一 wait 三 pool |
| 11 | Python 3.11 = 改 requires-python | **同时改三处：requires-python / ruff.target-version=py311 / mypy.python_version=3.11** |
| 12 | LazyMarkdown 大章节 5万+ 字符假设 | **当前实测最大 chapter 28 KB，未触发性能瓶颈**；改用"为未来 1MB+ 章节预铺路"视角 |
| 13 | `_HumanizeEmbedderAdapter` 单例化不需 shutdown | **当前无 reload/shutdown 方法**；RuntimeServices.shutdown() 不清理它；新设计必须加 shutdown + reload 时显式调 |
| 14 | `_flush_workspace_refresh` 是 spec 新方法 | **当前不存在**；设计时给当前真实存在的 `_on_workspace_refreshed(...)`（6 参数签名）加守卫 |

---

## 1. 现状精确盘点（2026-07-02 实测）

### 1.1 文件与行数（Phase 0 校准结果）

- `novel_forge/desktop/` 下 `*.py` 文件：**135 个**
- 总行数：**108290**
- 顶层结构：components(15) / pages(63) / resources/ / state(4) / theme(9) / tokens(5)

### 1.2 Top 10 巨型文件（必须拆分目标）

| # | 文件 | 行数 | 状态 |
|---|---|---|---|
| 1 | `pages/document_renderers.py` | 4850 | 纯渲染函数集合 + 巨型 widget，非 page |
| 2 | `window.py` | 4728 | 主窗口 + 4728 行 4 大职责混合 |
| 3 | `pages/document_renderer_story_artifacts.py` | 4518 | render_story_artifacts 系列 |
| 4 | `pages/document_renderer_reports.py` | 4244 | render_reports 系列 |
| 5 | `jobs.py` | 3583 | DesktopJobManager 全栈 + signal + history + stream |
| 6 | `pages/settings_page_parameters.py` | 3355 | settings 子模块 |
| 7 | `components/task_focus.py` | 2782 | 14 个 widget 类 |
| 8 | `components/memory_components.py` | 2729 | 14+ memory widget |
| 9 | `pages/settings_page_components.py` | 2501 | settings 子组件 |
| 10 | `pages/workflow_jobs.py` | 2434 | workflow 子模块 |

> **v1 spec 没列出的 3 个巨型 renderer**：document_renderers.py / document_renderer_story_artifacts.py / document_renderer_reports.py — 必须纳入 Phase 3 拆分。

### 1.3 `DesktopJobManager` 现状（关键）

已存在方法（与 v1 假设不同）：
- `__init__(line 1358)` — 建 4 个 QTimer + 1 个 subscription + 1 个 sleep inhibitor
- `cancel_job(line 2626)` — 取消单 job + worker.request_cancel + job_service.cancel + job_completed.emit
- `request_cancel_all(line 3200)` — 取消全部 worker + app-job（**不清理 worker 注册表**）
- `shutdown(wait_ms=500)(line 3229)` — **已关闭 4 个 timer + subscription + sleep inhibitor + worker.request_cancel + signal.disconnect × 6 + thread_pool.clear + thread_pool.waitForDone + _history_worker disconnect**
- **`close()` 不存在**
- **`__del__` 不存在**

shutdown 已实现的功能 ✅：
1. `self._closed = True`
2. `_unsubscribe_from_events()` 调
3. `_app_event_timer.stop() + disconnect _drain_app_service_events`
4. `_app_event_subscription.close()`
5. `job_service.shutdown(wait_s=..., reason="应用正在关闭")`
6. 每个 worker.request_cancel
7. 每个 worker 6 个 signal.disconnect(handler)
8. `self._thread_pool.clear()`
9. `self._thread_pool.waitForDone(wait_ms)`
10. `_sleep_inhibitor.release()`
11. `_waiting_poll_timer.stop()`, `_jobs_bind_timer.stop()`, `_token_timer.stop()`
12. `_history_event.set()`
13. `_history_worker.signals.finished.disconnect + 清 None`

shutdown **缺口**（v2 新增补齐）：
1. ❌ 所有 QTimer `deleteLater()`（仅 stop，留着悬挂 QObject）
2. ❌ `ui_io_pool.waitForDone(wait_ms)` — 只 wait `_thread_pool` (job_pool)
3. ❌ `aux_pool.waitForDone(wait_ms)` — 同上
4. ❌ window closeEvent 显式 `_sleep_inhibitor.release()`（仅依赖 shutdown 间接）

window closeEvent 调用链：
- closeEvent (4454) → `_shutdown_with_active_jobs` → `_pre_close_cleanup` (4189)
- `_pre_close_cleanup` 调 `page.shutdown()` 循环 (4221-4226) 然后 `self._job_manager.shutdown(wait_ms=2000)` (4228)
- `_job_manager.shutdown(wait_ms=500)` 默认 vs closeEvent 用 2000 — **已实际走通**

### 1.4 `WindowState` & `WorkspaceEventBus` 现状

`WorkspaceEventBus(QObject)` (state/event_bus.py:85-163)：
- 3 个 Signal（前缀下划线）：`_section_changed=Signal(str)`, `_project_changed=Signal(str, str)`, `_job_completed=Signal(str)`
- 公开访问：`publish(event)` / `subscribe(event_type, cb)` / `unsubscribe(cb)`
- 内部用 `signal.connect(callback)` **无 `type=` 参数** → 默认 `Qt.AutoConnection`
- **`_subscriptions: list[tuple]` 无锁保护**

`_on_workspace_refreshed` 现状（window.py:1402-1549）：
- 6 参数签名：(snapshot, service, payload, section_hashes, changed_sections, snapshot_hash)
- `worker.signals.refresh_ready.connect(self._on_workspace_refreshed)` (line 1388) AutoConnection
- 不引用 `event_bus.publish/subscribe/unsubscribe`
- 是 `Qt.EventLoop → slot` 模式，**不是 QTimer 触发**

### 1.5 thread pools 真相

- `desktop/thread_pools.py:24` 全局 `_THREAD_POOLS` 单例
- `desktop_thread_pools()` lazy 构造
- 三个池：**全局共享，无 page 隔离**
  - `job_pool` (long-running jobs, max≥2)
  - `ui_io_pool` (short UI I/O, max 2-4)
  - `aux_pool` (misc, max 2-6)
- **page.shutdown() 只有部分 page 调了 `wait_for_thread_pool()`**：
  - ✅ `projects_page.py:269`, `subplot_manager.py:860`, `dashboard_page.py:638`, `workflow_presets.py:871`, `dialogs_add_to_humanize.py:1013` (2000ms), `workflow_components.py:501` (500ms)
  - ❌ `chapter_studio_page`, `settings_page`, `token_analytics`, `workflow_page`, `humanize_library_dashboard`, `chapter_studio_dialogs` 不 wait
- `_pre_close_cleanup` 调所有 `page.shutdown()` 但**不在主线程统一 wait 三 pool**；仅 `self._job_manager.shutdown(wait_ms=2000)` 间接 wait

**v2 决策**：页面关闭**不** wait 全 3 pool（避免被无关任务拖住）。改为：
- `safe_shutdown_page()` 只 (a) cancel 自己持有的 worker + (b) disconnect self signals + (c) stop self timers + (d) deleteLater
- window `_pre_close_cleanup` 集中 wait 三 pool

### 1.6 `_HumanizeEmbedderAdapter` 真相

`workspace/runtime.py:212-251`：
- `__init__` 仅持 `settings` + `_service` + `_build_lock` + `_build_failed`
- `embed(texts)` → 调 `_run_async_in_worker(coro)` (line 254-267)
- **`_run_async_in_worker` 每次新建**：`asyncio.new_event_loop + ThreadPoolExecutor(1) + pool.submit + future.result()`
- **无 `close()`/`shutdown()`/`reload()` 方法**
- `reload_runtime_dependencies` 仅 `self._humanize_embedder = None` 丢引用靠 GC
- `RuntimeServices.shutdown()` **不碰 `_humanize_embedder`**（line 599-626）

**1000 次 embed → 1000 个 event_loop + 1000 个 Executor + 1000 个 thread**（实测不可接受）。

### 1.7 `pyproject.toml` 当前配置

| 行 | 字段 | 现状 |
|---|---|---|
| 10 | `requires-python` | `">=3.12"` ❌ |
| 83 | `ruff.target-version` | `"py312"` ❌ |
| 91 | `mypy.python_version` | `"3.12"` ❌ |
| 84 | `ruff.line-length` | `100` |
| 87 | `ruff.lint.select` | `["E","F","I","B"]` |
| 88 | `ruff.lint.ignore` | `["E501"]` |
| 92 | `mypy.strict` | `true` |
| 93 | `mypy.plugins` | `["pydantic.mypy"]` |
| 65 | `pytest.asyncio_mode` | `"auto"` |
| 69-72 | `pytest env` | `QT_QPA_PLATFORM=offscreen` + `NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS=true` |
| 34 | desktop extra | `PySide6>=6.6,<7` |
| 73-80 | markers | unit/integration/regression/perf/visual/timeout |

`.github/workflows/ci.yml` 5 个 job 的 `python-version`：`14/lint`, `39/unit`, `61/integration`, `80/visual`, `93/security` — **全部 `"3.11"`**。

**v2 决策**：三处都改成 3.11（用户的"Python 3.11"决策扩展到全工具链）。

### 1.8 大章节实测

`data/山风与归人2/chapters/` 实际最大 chapter = **27638 bytes (~27 KB)**。当前所有 chapter 在 13-28 KB，无 1MB/5MB 级别。**当前未触发 `QTextBrowser.setHtml` 性能瓶颈**。
但 `IncrementalDocumentRenderer`（pages/document_renderer_incremental.py:413-438）的 "cache" 仅做 HTML 字符串相等检查，"增量" 名不副实。

### 1.9 path 冲突与迁移顺序清单（v3 校正）

Python 在 `import` 时同时允许同名的 file 与目录，但**实际**路径中：

- 若一个目录内既有 `__init__.py`（package 入口）又有同名 .py file，**不冲突**；但**会让人困惑**（开发者难以判断哪个被 import）。
- 若现有一个 `file.py` 且想新增同名 `dir/`：必须先 `git mv file.py dir/__init__.py`，否则后续无法解释为什么 `from .file import X` 不工作。
- 若现有一个 `dir/`（已含 `__init__.py`）且想新增同名 `file.py`：可以共存，但**不推荐**，因为会导致 `import dir` 与 `from dir import file` 冲突的可读性。

**Phase 1-6 各迁移对的关系**：

| 现有 | Phase 计划新增 | 物理状态 | 操作 |
|---|---|---|---|
| `jobs.py` (file) | `desktop/workers/` (新 dir) | **无冲突** | 直接新增 dir；Phase 3 才转 `jobs.py` 为 `jobs/__init__.py` |
| `components/task_focus.py` (file) | `components/task_focus/` (新 dir) | **有歧义**（共存 file + dir 时外部 import 难判断） | **Phase 3 转 package 前**：保持 file 形态；新增模块写到 `components/task_focus_loader.py`（避免与 `task_focus.py` 同名 dir） |
| `window.py` (file) | `desktop/window/` (新 dir) | **无冲突** | 直接新增 dir；Phase 3 才转 package |
| `theme.py` | `theme/` (existing dir) | **已存在 dir** | 不要新增同名 dir；如需要新模块写到 `theme/...` 之内 |
| `state/` | 已是 dir | — | 复用现有 dir，新模块写到 `state/` 内 |
| `components/` | 已是 dir | — | 复用现有 dir |
| `pages/` | 已是 dir | — | 复用现有 dir |
| `tokens/` | 已是 dir | — | 复用现有 dir |
| `workers/` (new) | — | **不存在** | Phase 1 直接新增为 dir |

**规则**：
- ✅ file 与新 dir 不同名：可直接建 dir，无需迁移
- ✅ file 想转 package：用 `git mv .py dir/__init__.py`，**然后**才能在 dir 内加新文件
- ❌ file 与同名 dir 共存：禁止在 Phase 内做这件事（避免 import 歧义）

每个 Phase 1-6 的新增路径都要先在 plan 阶段过这张表，列"先 git mv 还是直接加 dir"。

---

## 2. 用户决策（已对齐，跨 v1/v2 不变）

| 维度 | 选择 |
|---|---|
| 优先级 | 性能优先 + 架构拆分优先 + Worker 安全优先 + CI/兼容性优先 + 解决 macOS fullscreen bug + 性能优化 |
| 外部依赖 | 按需引入 1-2 个轻量库（Phase 7 视需要） |
| 拆分粒度 | 拆分巨型文件 + 重组 pages 子目录 |
| Python 版本 | **3.11**（与 CI 一致） |
| macOS visual CI | 不加 |

---

## 3. 设计目标

| 维度 | v2 重新定义的目标 |
|---|---|
| 性能 | Model Call Panel 走非主线程；为未来 1MB+ 章节预铺 lazy markdown；embedder 复用 loop + thread + 配套 shutdown |
| 维护 | **8** 个 2500+ 行巨型文件全部拆分（window/document_renderers/document_renderer_story_artifacts/document_renderer_reports/jobs/settings_page_parameters/task_focus/memory_components）；pages 子目录化；统一 shutdown 助手 |
| Worker 安全 | `BaseJobWorker` 框架统一 cancel 协议；`request_cancel()` 普及；**复用**现有 `DesktopJobManager.shutdown()`，**补齐**：`deleteLater()`、`ui_io_pool.waitForDone`、`aux_pool.waitForDone` |
| 兼容 | `pyproject.toml` 三处（requires-python / ruff.target-version / mypy.python_version）全部 3.11；新增 desktop-tests job 三 runner |
| macOS fullscreen | `_on_workspace_refreshed` 入口加 fullscreen 守卫；保持 `Qt.AutoConnection`（不强制 QueuedConnection 而改语义）；`WorkspaceEventBus.subscribe` 改为可显式声明 default_queue，但**默认仍是 AutoConnection** |
| 拓展 | metadata-driven page registration（pkgutil.iter_modules + 子目录 `__init__.py` 自动发现） |

**非目标 (YAGNI)**：
- 不引入插件机制 / i18n 抽取
- 不升级到 Qt7 / shiboken7
- 不重写 pipeline / workflow 逻辑
- Phase 1-6 不引入任何新依赖

---

## 4. 现状问题清单（30 条去重，v2 已校准）

### 性能 (P0/P1)

| 编号 | 问题 | 位置 |
|---|---|---|
| **P0-1** | `load_model_call_snapshot` 主线程同步 `rglob+json.loads` 最多 120 个 JSON | `model_call_observation.py:98-129` + `task_focus.py:950` |
| **P0-5** | `QTextBrowser.setHtml` 整段插入；"增量"版仅做 HTML 字符串 hash 比较，未真分块 | `document_renderers.py:365-394`, `document_renderer_incremental.py:413-438` |
| **P0-7** | `_HumanizeEmbedderAdapter.embed` 每次新建 `ThreadPoolExecutor(1)+loop+result()` | `workspace/runtime.py:241-267` |
| **P1-10** | 长会话 RSS 无界增长（`_memory_contexts` + `_humanize_embedder` + `RuntimeServices.shutdown()` 不清理 embedder） | `workspace/runtime.py:280-285, 599-626` |
| **P1-11** | refresh 节流：`QFileSystemWatcher + 6s timer + 500ms force` 在写盘期雪崩 | `window.py:483-498,1339-1390` |
| **P1-12** | `_build_project_detail` 每次 7+ JSON + Pydantic validate | `workspace/projects.py:817-832,1483-1535` |

### 架构与维护 (P0/P1)

| 编号 | 问题 | 位置 |
|---|---|---|
| **P0-3** | **8 个 2500+ 行巨文件** | 见 §1.2 |
| **P0-4** | `shutdown()` 已覆盖大部分；缺 `deleteLater` + `ui_io_pool.waitForDone` + `aux_pool.waitForDone` | `jobs.py:3229-3296` |
| **P1-9** | `ChapterStudioPage` 7 层 mixin + 5 Coordinator；shutdown 只清 facade | `chapter_studio_page.py:46-54` |
| **P1-13** | `pages/` 63 文件扁平，描述失真 | `pages/AGENTS.md:7` |
| **P1-26** | `safe_disconnect` 助手不统一；手写 `try/except (RuntimeError, TypeError)` 多处易漏 | `workflow_page.py:96-210` |

### 兼容与拓展 (P0/P2)

| 编号 | 问题 | 位置 |
|---|---|---|
| **P0-2** | pyproject 三处配置与 CI 矛盾（requires-python/ruff/mypy 都 py312 但 CI 用 py311） | `pyproject.toml:10,83,91` vs `ci.yml:14,39,61,80,93` |
| **P1-14** | desktop 测试仅 ubuntu visual job 跑 CI；其余 ~36 个 desktop 测试从未跑 CI | `ci.yml:71-84` |
| **P1-16** | 新 page 须三处改（`pages/__init__.py` + `page_registrations.py` + 新文件） | `page_registrations.py:3-8`, `pages/__init__.py:1-22` |

### macOS fullscreen (用户新增)

代码已有防御（`window.py:3908-3955 _skip_page_motion_for_window_state`）：
- 检测 darwin + isFullScreen + windowState + visibility + frameGeometry 与 screen 比对（tolerance 3 px）

真实触发链：
1. `_on_workspace_refreshed` (window.py:1402-1549, 6 参数签名) 主线程执行密集 widget 操作
2. 该方法由 `worker.signals.refresh_ready` (window.py:1388) `Qt.AutoConnection` 投递
3. `_WorkspaceRefreshRunnable` 跑在 `ui_io_pool` 后台线程
4. macOS Spaces transition 中状态可能被瞬间识别为"非全屏"
5. `_animate_current_page` (line 3997-4095) 安装 `QGraphicsOpacityEffect`
6. AppKit 判定 window 状态异常 → 退出 Space

**v2 修复方向**：`_on_workspace_refreshed` 入口加 fullscreen 守卫 → `QTimer.singleShot(0, defer)` 不阻塞当前 paint 周期。

---

## 5. 实施路线（7 Phase，7 里程碑，v2 校准后）

### Phase 1 — Worker 框架与安全补齐（M1）

**核心变更**：补齐现有 `DesktopJobManager.shutdown()` 的 2 个缺口（具体见 §1.3） + 新增 `BaseJobWorker` 框架 + 新增 `safe_shutdown_page()` 助手。

#### 1.0 文件布局（v3 校正）

v2 的草案把 `BaseJobWorker` 追加到 `jobs.py`，理由是避免和 `desktop/workers/` 冲突。这是不必要的谨慎：`jobs.py` 与 `desktop/workers/` **没有**路径冲突（一个是 file，一个是新目录）。v3 直接落到正确位置：

```
desktop/workers/             ← NEW package
├── __init__.py              # re-export BaseJobWorker, BaseJobWorkerSignals,
│                            # safe_shutdown_page, get_pool_for
├── base.py                  # BaseJobWorker + BaseJobWorkerSignals（见 §1.2）
├── lifecycle.py             # safe_shutdown_page, disconnect_signals helper（见 §1.4）
└── pool_assign.py           # get_pool_for('job'|'ui_io'|'aux') 解析
```

`jobs.py` **不**添加 worker 基类；现有 `_WorkspaceJobWorker` 直接 `from ..workers.base import BaseJobWorker` 继承。

**这一步与 Phase 3 大拆分独立**：先把 worker 抽象抽出来，使后续 page worker 切换时不依赖 `jobs.py` 内容。

#### 1.1 补齐 `DesktopJobManager.shutdown()` 缺口

**v3 重要校正**：v2 让 `shutdown()` wait 全三 pool。这是越权 — `ui_io_pool` 与 `aux_pool` 由 `thread_pools.py` 创建，不归 JobManager。JobManager 应只关心：
1. 自己持有的 worker 取消
2. 自己持有的 timer `deleteLater`
3. 自己持有的 subscription close
4. `sleep_inhibitor.release()`
5. **`self._thread_pool.waitForDone(wait_ms)`**（job_pool，因为 JobManager 把自己 start 进 `_thread_pool`）

修改文件：`novel_forge/desktop/jobs.py:3229-3296`

```python
def shutdown(self, *, wait_ms: int = 500) -> None:
    """JobManager 只清自己持有的状态与 job_pool。
    ui_io_pool / aux_pool 由 window._pre_close_cleanup()
    通过 thread_pools.shutdown_desktop_thread_pools() 统一管理。
    """
    self._closed = True
    self._unsubscribe_from_events()
    # ... 现有代码保留 ...

    # ===== v3 新增：timer deleteLater（4 个 timer）=====
    for timer_attr in ("_app_event_timer", "_waiting_poll_timer",
                       "_jobs_bind_timer", "_token_timer"):
        timer = getattr(self, timer_attr, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()  # NEW

    self._thread_pool.waitForDone(wait_ms)  # 现有 — 仍属 JobManager 职责
    self._sleep_inhibitor.release()  # 现有
    # ... 其余现有代码 ...
```

**新增** `novel_forge/desktop/thread_pools.py::shutdown_desktop_thread_pools()`：

```python
def shutdown_desktop_thread_pools(*, wait_ms: int = 3000) -> None:
    """集中关闭全局三 pool。

    调用方：window._pre_close_cleanup() 在调完 page.shutdown() 循环 + 
    self._job_manager.shutdown() 之后调用。
    """
    pools = desktop_thread_pools()
    pools.ui_io_pool.waitForDone(wait_ms)
    pools.aux_pool.waitForDone(wait_ms)
    pools.job_pool.waitForDone(wait_ms)  # JobManager 也 wait 过，但二次 wait no-op
```

**修改 `DesktopThreadPools`**（`thread_pools.py`）加 `by_name(name)` 协议方法：
```python
@dataclass
class DesktopThreadPools:
    job_pool: QThreadPool
    ui_io_pool: QThreadPool
    aux_pool: QThreadPool

    def by_name(self, name: str) -> QThreadPool:
        return {
            "job": self.job_pool,
            "ui_io": self.ui_io_pool,
            "aux": self.aux_pool,
        }[name]
```

**window.py:4189-4240 `_pre_close_cleanup` 新增一行**：
```python
# 在 _job_manager.shutdown(wait_ms=2000) 之后
from novel_forge.desktop.thread_pools import shutdown_desktop_thread_pools
shutdown_desktop_thread_pools(wait_ms=3000)  # NEW
```

#### 1.1.1 新增 `close()` 别名

为命名一致性新增 `close()` 别名（不影响现有 closeEvent 调用）：
```python
# 同步追加到 jobs.py
def close(self, *, wait_ms: int = 500) -> None:
    """Alias for shutdown(). UI convention: close() = clean teardown."""
    self.shutdown(wait_ms=wait_ms)
```

#### 1.2 新增 `BaseJobWorker` 框架

**v3 重要校正**：`BaseJobWorker` **不**统一业务信号形状。每个 worker 自己的 signals 形状（`_WorkspaceJobWorker` 现有 `step(job_id, step, payload)` / `finished(job_id, result)` / `failed(job_id, payload)`；page workers 各有差异）。基类只保证：

1. **lifecycle** — 通过 `signals_cls` 字段保留子类自定义 signals
2. **cancel** — `threading.Event` + `request_cancel()` 与现有协议一致
3. **run/asyncio** — `loop.run_until_complete + shutdown_asyncgens + close`（**不**统一改 asyncio.run）
4. **error wrapping** — 错误统一走 `summarize_desktop_error().as_payload()` 后 emit 到子类的 `failed` signal

新文件 `desktop/workers/base.py`：

```python
"""Worker 基类与 lifecycle 助手。

不强制统一业务信号形状 — 子类通过 signals_cls 自定义。
只统一：cancel/run/asyncio/error-wrap/pool 选择。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from novel_forge.desktop.errors import summarize_desktop_error
from novel_forge.desktop.thread_pools import desktop_thread_pools


class BaseJobWorkerSignals(QObject):
    """最简 lifecycle signals。

    注意：这里**没有**强业务信号（step/finished/failed 等）。
    子类应继承并定义自己的 signals 类型，保持现有使用点不变。
    只在需要"基础错误封装"时，可调用父类的 emit_failed()。
    """
    worker_started = Signal(str)         # worker_id
    worker_cancelled = Signal(str)        # worker_id
    worker_failed = Signal(str, dict)     # worker_id, structured_payload


class BaseJobWorker(QRunnable):
    """所有 worker 的统一基类。

    复用现有 _WorkspaceJobWorker 的 cancel 协议（threading.Event）。
    复用现有 asyncio 启动模式（loop.run_until_complete + shutdown_asyncgens + close）。

    Attributes:
        signals_cls: 子类可定义自己的 signals 类（继承 BaseJobWorkerSignals 或独立 QObject）。
                     必须是无参数的 QObject 子类。
        pool: 字符串 'job' | 'ui_io' | 'aux'。默认为 'job'，子类可覆盖。
    """

    signals_cls: type = BaseJobWorkerSignals
    pool: str = "job"

    def __init__(self, *, mock: bool = False):
        super().__init__()
        self.signals = self.signals_cls()
        self._cancel_event = threading.Event()
        self.mock = mock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._worker_id = f"{self.__class__.__name__}-{id(self):x}"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def signals_qobject(self) -> QObject:
        return self.signals

    def request_cancel(self) -> None:
        """取消协议 — 与现有 _WorkspaceJobWorker 完全一致。"""
        self._cancel_event.set()
        self._on_cancel_requested()

    def _on_cancel_requested(self) -> None:
        """子类的 hook：loop.call_soon_threadsafe(task.cancel)。"""
        loop = self._loop
        task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def _check_cancel(self) -> None:
        """子类应在长时间 await 循环里周期性调用以响应取消。"""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

    # -------- Framework run() — implements QRunnable.run ----------

    def run(self) -> None:
        """QRunnable 入口：建立 asyncio loop 并运行 _run_async。

        保持与 _WorkspaceJobWorker 完全相同的协议：
        - new_event_loop + create_task + run_until_complete
        - finally：drain tasks + shutdown_asyncgens + close
        - 异常：emit worker_failed（含 summarize_desktop_error）
        - 取消：emit worker_cancelled
        """
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            self.signals.worker_started.emit(self._worker_id)
            self._task = loop.create_task(self._run_async())
            try:
                loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                self.signals.worker_cancelled.emit(self._worker_id)
            except Exception as exc:
                self._emit_failure(exc)
        finally:
            try:
                # drain remaining tasks
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop = None
            self._task = None
            loop.close()

    async def _run_async(self) -> None:
        """子类覆盖 — 通过 self._check_cancel() 提供可中断点。"""
        raise NotImplementedError(f"{self.__class__.__name__}._run_async")

    def _emit_failure(self, exc: BaseException) -> None:
        try:
            payload = summarize_desktop_error(exc).as_payload()
        except Exception:
            payload = {
                "kind": exc.__class__.__name__,
                "message": str(exc),
                "structured_error": False,
            }
        self.signals.worker_failed.emit(self._worker_id, payload)

    def submit(self) -> None:
        """便捷方法 — 把 worker 投递到正确的 thread pool。"""
        pool_obj = desktop_thread_pools().by_name(self.pool)
        pool_obj.start(self)
```

> 关于 `asyncio.run` 替换：v3 不强制替换 page worker 现有 `asyncio.run(self._async_run())` 调用链 — BaseJobWorker 提供同样的 `run()` 路径；任何 page worker 想升级只需把 `asyncio.run` 那行替换为在 `run()` 中的 `loop.run_until_complete`。**这是渐进式迁移，不是一次性重写**。

> 关于 `signals_cls`：`_WorkspaceJobWorker` 现有 `_WorkerSignals`（5 信号：`started/step/decision_required/finished/failed/cleanup_runtime`）**保留原签名**，仅继承 `BaseJobWorker`（而不是 `QRunnable`），不强制改 signals 形状。

#### 1.3 替换 8 个零散 worker 为 `BaseJobWorker` 子类

| 文件 | 行号 | 现有类 | v2 改动 |
|---|---|---|---|
| `desktop/jobs.py:1148` | 1148 | `_WorkspaceJobWorker` | **继续继承 BaseJobWorker**（注意：去掉下划线变成 `WorkspaceJobWorker`？或保留下划线，向后兼容 — 方案选保留下划线，避免外部 import 路径变更） |
| `pages/final_revision.py:333,430` | 333, 430 | `_DirectionSuggestionWorker`, `_SelectionRevisionWorker` | 改为 `BaseJobWorker`（保留 asyncio.run 路径仍兼容 — 用 `loop.run_until_complete` 替代） |
| `pages/workflow_workers.py:30` | 30 | `SemanticResolveWorker` | 同上 |
| `pages/settings_page_components.py:1950,2061` | 1950, 2061 | `_ConnectionTestWorker`, `_OllamaModelWorker` | 同上 |
| `pages/dialogs_add_to_humanize.py:219,272` | 219, 272 | `_AddEntryWorker`, `_DedupWorker` | 同上 |
| `pages/subplot_manager.py:1145` | 1145 | `_SubplotWorker` | 同上 |
| `pages/workflow_presets.py:879,919` | 879, 919 | `_PresetWorker`, `_PresetSaveWorker` | 同上 |

**每个替换点**：
1. 类声明改继承 `BaseJobWorker` (从 `QRunnable`)
2. `Signals` 类改继承 `BaseJobWorkerSignals`
3. 删除自家 `asyncio.run(self._async_run())` → 用框架 `run()` 路径
4. 保留所有现有 cancel 调用点与 signal connection 端点

#### 1.4 `safe_shutdown_page()` 助手（路径调整）

v1 写在 `desktop/workers/lifecycle.py`。v2 改：

```python
# 追加到 jobs.py 末尾（或 atomic 到 base_subpackage 文件）

def safe_shutdown_page(
    page: QWidget,
    *,
    workers: Iterable[BaseJobWorker],
    timers: Iterable[QTimer],
    signals_to_disconnect: Iterable[tuple[Signal, Callable]] = (),
) -> None:
    """统一协议（NEW 名称：safe_shutdown_page）:
    1. 全部 worker.request_cancel()
    2. 安全 disconnect signals（统一 helper，避免 try/except 复制）
    3. timers stop + deleteLater（不是 waitForDone — page 关闭不等全 pool）
    4. 不 waitForDone — 由 window._pre_close_cleanup 统一 wait
    """
```

**关键安全决策**（v1 → v2 修改）：
- ❌ v1：`safe_shutdown_page` wait 3 个 pool → 可能被无关长任务拖住
- ✅ v2：`safe_shutdown_page` 只 cancel + disconnect + stop timer；wait 由 window.closeEvent 集中

#### 1.5 新增测试

| 测试 | 覆盖 |
|---|---|
| `tests/desktop/test_worker_base.py` | BaseJobWorker 框架：mock 协程、cancel、error 序列化 |
| `tests/desktop/test_page_shutdown.py` | 构造 5 个 mock page，调 `safe_shutdown_page`；断言：worker cancelled、no signal 残留连接、timers stopped、**不 wait pool** |
| `tests/desktop/test_jobs_manager_shutdown.py` | 现有 `shutdown()` + 新增 `close()` 别名 + 全 pool wait + deleteLater |

### Phase 2 — 性能快赢（M2）

**v2 重大修订**：
- `LazyMarkdownViewer` 不必立即需要（实测当前 28 KB 章节未触发瓶颈）
- 但 `IncrementalDocumentRenderer.update_content` 的"假增量"是代码味道 — 仍要修
- `JobSnapshotCache` 边界已明确：append-only + LRU 64 + run_id mtime 触发的 invalidate

#### 2.1 Model Call Panel 异步

**v3 修正**：v2 的 cache 仅做 TTL，没体现 run_dir 文件变化的 mtime 失效。v3 把 signature 写实：

新文件 `novel_forge/desktop/state/snapshot_cache.py`：

```python
"""Job snapshot cache — 进程内 LRU + 物理文件 signature。

Cache key: (run_id, signature) 其中
    signature = (file_count, latest_mtime_ns, total_size_bytes)
    每次 get_or_load 时重新计算 signature，若与缓存中的不同 → invalidate
    这样 append-only 的 model_calls/ 目录新增 JSON 后会立即被察觉

TTL 仅作兜底（防止磁盘上文件无变化但 stale window 超过 60s）。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class _SnapshotSignature:
    """Physical signature of a model_calls/ directory.

    任何 append / change 都会改变 signature。
    """
    file_count: int
    latest_mtime_ns: int
    total_size_bytes: int

    @classmethod
    def compute(cls, model_calls_dir: Path) -> _SnapshotSignature:
        if not model_calls_dir.is_dir():
            return cls(0, 0, 0)
        count = 0
        latest_ns = 0
        total = 0
        for p in model_calls_dir.rglob("*.json"):
            try:
                stat = p.stat()
                count += 1
                total += stat.st_size
                latest_ns = max(latest_ns, stat.st_mtime_ns)
            except OSError:
                continue
        return cls(count, latest_ns, total)


class JobSnapshotCache:
    """LRU(64) 缓存 run_id -> (signature, ModelCallSnapshot)。"""

    def __init__(self, max_entries: int = 64, ttl_s: float = 60.0):
        self._cache: OrderedDict[
            str, tuple[float, _SnapshotSignature, ModelCallSnapshot]
        ] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._ttl = ttl_s

    def get_or_load(
        self,
        run_id: str,
        model_calls_dir: Path,
        loader: Callable[[], ModelCallSnapshot],
    ) -> ModelCallSnapshot:
        """Get cached or load fresh.

        失效条件（任一）：
        1. cache 中无该 run_id
        2. 当前 _SnapshotSignature.compute(model_calls_dir) ≠ cache 中存的 signature
        3. 自 cache 起算时间 > ttl_s

        以上任一命中 → 调 loader 重算。
        """
        current_sig = _SnapshotSignature.compute(model_calls_dir)
        now = time.time()
        with self._lock:
            entry = self._cache.get(run_id)
            if entry is not None:
                ts, sig, snap = entry
                if sig == current_sig and (now - ts) < self._ttl:
                    self._cache.move_to_end(run_id)
                    return snap
                # 失效（signature 变或 TTL 过）→ 丢弃
                self._cache.pop(run_id, None)

        # 锁外 IO
        snap = loader()

        with self._lock:
            self._cache[run_id] = (now, current_sig, snap)
            self._cache.move_to_end(run_id)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return snap

    def invalidate(self, run_id: str) -> None:
        """显式失效（pipeline 端可调）。"""
        with self._lock:
            self._cache.pop(run_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
```

**调用位置改造**（`components/task_focus.py:950` 处）：

```python
# BEFORE
snapshot = load_model_call_snapshot(self._job, max_files=120)

# AFTER
run_id = self._job.run_id
model_calls_dir = project_dir / "logs" / run_id / "model_calls"
snapshot = _snapshot_cache.get_or_load(
    run_id,
    model_calls_dir,
    lambda: load_model_call_snapshot(self._job, max_files=120),
)
```

新文件 `novel_forge/desktop/components/task_focus/loaders.py`：
- **Phase 1 改名 `components/task_focus.py` 为 `task_focus/__init__.py` 后才放这里**（避免 v1 路径冲突）
- 否则先放在 `components/task_focus_loader.py`（无点冲突路径）

```python
class TaskModelCallLoader(BaseJobWorker):
    pool = "ui_io"  # 新值 — 不抢 job_pool

    def __init__(self, job, *, max_files: int = 120):
        super().__init__()
        self._job = job
        self._max_files = max_files

    async def _run_async(self) -> None:
        # 复用现有 load_model_call_snapshot
        snapshot = load_model_call_snapshot(self._job, max_files=self._max_files)
        self.signals.finished.emit(snapshot)


class TaskModelCallPanel:
    def _refresh(self):
        self._loader = TaskModelCallLoader(job=self._job)
        self._loader.signals.finished.connect(self._on_loaded, Qt.QueuedConnection)
        desktop_thread_pools().ui_io_pool.start(self._loader)
```

`task_focus.py:950` 调用点改走 `JobSnapshotCache.get_or_load(run_id, lambda: load_model_call_snapshot(...))`。

#### 2.2 LazyMarkdown / 真增量渲染

**v3 重要校正**：v2 的 chunked_insert 危险 — 按字符切可能切断 HTML tag/entity；`QApplication.processEvents()` 有重入风险。

**当前真实情况**：实测最大章节 28 KB，未触发 `QTextBrowser.setHtml` 性能瓶颈。**Phase 2 不必急于 chunked insert**。

**v3 决策**：Phase 2.2 拆为三层：
1. **删除"假增量"标签与误导**（无需 chunked 也能让代码可读）
2. **加可插拔 chunked insert 钩子**（接口存在，但默认空实现）
3. **加 bench 脚本**（`scripts/benchmark_chapter_render.py`）用合成 HTML（28 KB / 100 KB / 500 KB / 1 MB）跑出 setHtml vs chunked setHtml 的真实开销；**用数据决定**未来是否启用 chunked

```python
# document_renderer_incremental.py — 仅清理假增量语义，不引入真 chunked
def update_content(self, html: str) -> None:
    """首次或变化时全量重新渲染。

    注：历史上此方法的"incremental" 标签是误称 —— 它仅做 HTML 字符串相等检查，
    实际仍然 clear() + 全量 insertHtml()。Phase 2.2 仅清理文档与 type hint，
    真正的 chunked insert（按段落边界 + QTimer.singleShot 分帧）作为可插拔实现保留，
    由增量阈值开关启用，默认关闭。
    """
    if html == self._last_html:
        return
    self._browser.clear()
    self._browser.textCursor().insertHtml(html)
    self._last_html = html
```

**未来真分块设计**（Phase 8+ 用，仅给方向不在 Phase 2 实施）：

```python
# 仅作为可插拔 chunked_chunked_setter.py；不在 Phase 2 启用

def split_html_by_block_boundaries(html: str) -> list[str]:
    """在 </p></h1>...</pre></div> 边界切分，绝不在 tag 中切。
    fallback: 整段单 chunk。
    """
    import re
    parts = re.split(r'(?<=</(?:p|h\d|div|pre|li)>)', html)
    return [p for p in parts if p.strip()] or [html]


class ChunkedHtmlSetter:
    """通过 QTimer 单次 shot 分帧注入 HTML，绝不在 run loop 里 processEvents。"""

    def __init__(self, browser, *, frame_delay_ms: int = 16):
        self._browser = browser
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(frame_delay_ms)
        self._timer.timeout.connect(self._render_one_chunk)
        self._chunks: list[str] = []
        self._cursor = None

    def set_html(self, html: str) -> None:
        self._timer.stop()  # 取消之前的 frame
        self._browser.clear()
        self._cursor = self._browser.textCursor()
        self._chunks = split_html_by_block_boundaries(html)
        self._timer.start()

    def _render_one_chunk(self) -> None:
        if not self._chunks:
            return
        chunk = self._chunks.pop(0)
        self._cursor.insertHtml(chunk)
        if self._chunks:
            self._timer.start()  # 下一帧
        else:
            self._timer.stop()
```

**Phase 2 实际**：
1. `document_renderer_incremental.py:413-438` 改名为 `update_content` 仅做文档清理（commit message 写明"语义清理，不改行为"）
2. `scripts/benchmark_chapter_render.py` 写测量脚本
3. **不**启用 ChunkedHtmlSetter
4. 测试只覆盖"假增量"的现有行为不变 + bench 脚本能跑通

#### 2.3 `_HumanizeEmbedderAdapter` 单例 + 配套 shutdown

修改 `workspace/runtime.py:212-251`：

```python
class _HumanizeEmbedderAdapter:
    """单例化 embedder：
    - 1 个 loop + 1 个 daemon thread 持有
    - embed() 通过 loop.call_soon_threadsafe 投递
    - shutdown() 显式 join thread + close loop（避免线程泄漏）
    - reload() 与 RuntimeServices.reload_runtime_dependencies 配合
    """
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any = None
        self._build_lock = threading.Lock()
        self._build_failed = False
        # NEW: 长生命周期的 loop + thread
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="humanize_embedder")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        service = self._ensure_service()
        if service is None:
            return []
        future = concurrent.futures.Future()
        async def _do() -> list[list[float]]:
            try:
                results = await service.generate_batch(texts)
                future.set_result([list(getattr(r, "embedding", []) or []) for r in (results or [])])
            except Exception as exc:
                future.set_exception(exc)
        try:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_do(), loop=self._loop))
            return future.result(timeout=30)
        except Exception as exc:
            _logger.debug("humanize_embedder_embed_failed | error=%s", exc)
            return []

    def shutdown(self) -> None:
        """NEW: 显式释放线程与 loop。"""
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=3)
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass
        self._thread = None

    def reload(self) -> None:
        """NEW: 配合 RuntimeServices.reload_runtime_dependencies。"""
        self.shutdown()
        self._service = None
        self._build_failed = False
```

修改 `RuntimeServices.shutdown`（runtime.py:599-626）新增：
```python
async def shutdown(self) -> None:
    for context in list(self.memory_contexts.values()):
        ...
    self.memory_contexts.clear()
    # NEW: 清理 humanize_embedder
    embedder = getattr(self, "_humanize_embedder", None)
    if embedder is not None and hasattr(embedder, "shutdown"):
        try:
            embedder.shutdown()
        except Exception:
            pass
        self._humanize_embedder = None
    # ... 现有 router.shutdown() 等 ...
```

修改 `RuntimeServices.reload_runtime_dependencies`（runtime.py:311-353）：**从 `self._humanize_embedder = None` 改为先 `embedder.shutdown()` 再 None**。

新增测试 `tests/unit/test_humanize_embedder_singleton.py`：
- 1000 次 embed → thread_count = 1, loop_count = 1
- shutdown 后 _thread is None, _loop.is_closed() == True
- reload 一次后重新建 thread

#### 2.4 refresh 节流

v1 详细保留；v2 加细节：`window.py:483-498` `QFileSystemWatcher` 监听 + `window.py:1339-1390 refresh_workspace`。

新增 `_refresh_debouncer`：

```python
# 放在 window.py 内部
class _RefreshDebouncer:
    def __init__(self, callback: Callable[[], None], *, debounce_ms: int = 250):
        self._callback = callback
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._fire)
        self._enabled = True

    def trigger(self) -> None:
        if not self._enabled:
            return
        self._timer.start()

    def _fire(self) -> None:
        self._callback()

    def disable(self) -> None:
        self._enabled = False
        self._timer.stop()
```

#### 2.5 验证与基准

新测试：
- `tests/desktop/test_lazy_markdown.py`：mock 30KB / 200KB html，断言分块插入与等长结果
- `tests/desktop/test_snapshot_cache.py`：60 秒 TTL 验证；mtime 触发的 invalidate；并发读
- `tests/unit/test_humanize_embedder_singleton.py`（如上）

`scripts/benchmark_ui_perf.py` 加输出列：
- `model_call_panel_load_ms`（旧 主线程阻塞 0.5s+ → 新 < 200ms）
- `refresh_roundtrip_ms`

### Phase 3 — 架构拆分（M3，8 split commit）

v2 修订：从 v1 的 5 个变为 **8 个**（新增 document_renderers / document_renderer_story_artifacts / document_renderer_reports 三个 renderer 拆包）。

#### 3.1 路径迁移顺序（避免冲突）

每拆一个 file → package 都按下面顺序：

1. **目录预检**：`ls novel_forge/desktop/<X>/` 若已存在 → 用不同名；确认不会冲突
2. **`git mv 旧 .py 文件 → 新目录/__init__.py`**（保留 history）
3. **拆分类**到子文件
4. **`__init__.py` re-export 公共 API**
5. **跑测试**：`pytest tests/desktop tests/unit -q`
6. **跑 lint**：`ruff check novel_forge/desktop/<X>/`
7. **跑 mypy**：`mypy novel_forge/desktop/<X>/`
8. **commit**：`refactor(desktop): split <X> into <package>`

#### 3.2 8 个拆分目标

| # | 文件 | 行数 | 拆分目标 |
|---|---|---|---|
| 1 | `pages/document_renderers.py` | 4850 | → `pages/document_renderer/__init__.py` (公共渲染函数 + 入口) / `chapter.py` (render_chapter_prose 等) / `incidents.py` / `cards.py` 等 |
| 2 | `window.py` | 4728 | → `window/__init__.py` re-export + `core.py` (~1000) + `navigation.py` (~1200) + `jobs_binding.py` (~1100) + `panels/{top_bar,side_rail,skeleton_overlay}.py` + 现有 `window_jobs.py` `window_navigation.py` `window_runnables.py` `window_widgets.py` 整合 |
| 3 | `pages/document_renderer_story_artifacts.py` | 4518 | → `pages/document_renderer/story_artifacts/__init__.py` + 子拆分 |
| 4 | `pages/document_renderer_reports.py` | 4244 | → `pages/document_renderer/reports/__init__.py` + 子拆分 |
| 5 | `jobs.py` | 3583 | → `jobs/__init__.py` (DesktopJobManager re-export) + `manager.py` + `worker_base.py` (Phase 1 加) + `signals.py` + `history.py` + `stream.py` + `errors.py` |
| 6 | `pages/settings_page_parameters.py` | 3355 | → `pages/settings/__init__.py` re-export SettingsPage + `parameters/{form,validator,persistence,widgets}.py` |
| 7 | `components/task_focus.py` | 2782 | → `components/task_focus/{__init__.py,panel.py,task_companion.py,task_call_panel.py,floating_window.py,dialog.py,switcher.py,observation_store.py}` |
| 8 | `components/memory_components.py` | 2729 | → `components/memory_components/{__init__.py,panel.py,motif_cards.py,repetition_warning.py,summary_panel.py,context_overview.py,chip.py,suggestion_card.py,outline_tracker.py}` |

**新增 AGENTS.md**：
- `desktop/window/AGENTS.md`
- `desktop/jobs/AGENTS.md`
- `desktop/components/task_focus/AGENTS.md`
- `desktop/components/memory_components/AGENTS.md`
- `desktop/pages/document_renderer/AGENTS.md`
- `desktop/pages/settings/AGENTS.md`

#### 3.3 验证

- 现有 `tests/desktop/` 全过（45 文件）
- 新增 `tests/desktop/test_window_imports.py`：从 `desktop.window` import 全部公共类
- 新增 `tests/desktop/test_jobs_imports.py`：从 `desktop.jobs` import 全部公共类

### Phase 4 — pages 子目录化（M4）

v2 修订：63 个页面文件分到 **7 个子目录**（v1 是 6 个；新加 `document_renderer` 子目录因为 Phase 3.2 已拆出）。

```
desktop/pages/
├── AGENTS.md              # 重写
├── __init__.py            # pkgutil.iter_modules 自动发现
├── _page_utils.py         # 现有
├── _registry.py           # 现有 page_registrations.py 改名
├── chapter_studio/        # 18 个
├── workflow/              # 12 个
├── document_renderer/     # 4 个 + document_renderers.py 整合（来自 Phase 3）
├── settings/              # 9 个（来自 Phase 3.2-6）
└── standalone/            # 10+ 独立页
    ├── projects.py
    ├── dashboard.py
    ├── character_*.py     # 5 个
    ├── outline_*.py       # 3 个
    ├── subplot_manager.py
    ├── final_revision.py
    ├── token_analytics.py
    └── renderer_html.py
```

`pages/__init__.py`：
```python
import importlib, pkgutil
for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name.startswith("_"):
        continue
    if not _ispkg:
        continue
    importlib.import_module(f"{__name__}.{_name}")
```

子目录 `__init__.py`：
```python
from .page import ChapterStudioPage  # 公共类
# 不要在 import 时调 register — 留给 pageregistry 的 lazy init
__all__ = ["ChapterStudioPage"]
```

新增 `scripts/refactor_pages_layout.py`：

```python
"""一次性迁移 import 路径 from novel_forge.desktop.pages.X import Y
→ from novel_forge.desktop.pages.<subdir>.X import Y
"""
```

- 模式：dry-run / apply (`--apply` flag)
- 输出 old → new 映射表
- 跑 `ruff --fix` + 手工 review 兜底

**重写** `pages/AGENTS.md`：6+ 子目录索引、命名、注册协议。

**新增** `desktop/AGENTS.md`：desktop 子树总约定（shutdown / signals / state / 测试矩阵）。

**新测试** `tests/desktop/test_pages_autodiscovery.py`：扫 `pages/*/` → 断言全部预期 `*Page` 类被注册到 `page_registry`。

### Phase 5 — CI / 兼容性（M5）

#### 5.1 Python 版本对齐（三处）

`pyproject.toml` 三处：

```diff
[project]
- requires-python = ">=3.12"
+ requires-python = ">=3.11"

[tool.ruff]
- target-version = "py312"
+ target-version = "py311"

[tool.mypy]
- python_version = "3.12"
+ python_version = "3.11"
```

CI `.github/workflows/ci.yml` 不动（仍 `"3.11"`）。

**验证步骤**：
1. `python3.11 -m venv .venv311`
2. `pip install -e ".[dev,desktop,all]"`
3. `pytest tests/unit -q` 全过
4. `pytest tests/desktop -q` 全过（用现有 platform=offscreen）
5. `ruff check novel_forge/`
6. `mypy novel_forge/`

#### 5.2 新 CI job：`desktop-tests`

**v3 校正**：v2 写 `pytest tests/desktop -n auto`，会让 desktop-tests job 把 visual tests 也一并收集。当前 visual tests 因 `PYTEST_XDIST_WORKER` 跳过才看似没冲突，但实际 CI 应**显式**排除 visual，把非 visual 的 desktop 测试在 3 runner 跑通。

```yaml
desktop-tests:
  name: Desktop Tests (PySide6, non-visual)
  runs-on: ${{ matrix.os }}
  strategy:
    matrix:
      os: [ubuntu-latest, windows-latest, macos-latest]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.11"
        cache: pip
    - name: Install system deps (Ubuntu)
      if: matrix.os == 'ubuntu-latest'
      run: sudo apt-get install -y libegl1 libgles2 libgl1
    - name: Install Windows system deps
      if: matrix.os == 'windows-latest'
      run: echo "PySide6 wheels provide bundled libraries"
    - name: Install
      run: pip install -e ".[dev,desktop]"
    - name: Run desktop tests (non-visual)
      env:
        QT_QPA_PLATFORM: offscreen
        NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS: "true"
      # v3 显式 exclude visual: 它们仍由 visual job 单独跑 ubuntu-only
      run: |
        pytest tests/desktop \
          --ignore-glob='tests/desktop/*_visual.py' \
          --ignore-glob='tests/desktop/test_visual_regression.py' \
          -n auto --timeout=300 -q

visual-tests:
  # 现有 job 保留，ubuntu-only
  name: Visual Regression (PySide6)
  runs-on: ubuntu-latest
  # ... 现有 visual job 配置 ...
```

**验证步骤**：
1. `--ignore-glob` 等价写法：`--deselect tests/desktop/*_visual.py` 也可；任选一种
2. 跨平台等价：windows runner 上 pytest glob 对 `*_visual.py` 也匹配（用反斜杠要在 CI 上验证，但目前 desktop tests 未在 windows runner 上跑过，等 Phase 5.2 首次跑时确认）

#### 5.3 跨平台硬编码

抽 `desktop/platform/{sleep_inhibit,ollama_paths,fonts}` 三平台模块：

```
desktop/platform/
├── __init__.py
├── sleep_inhibit/
│   ├── __init__.py        # dispatch per-sys.platform
│   ├── darwin.py          # caffeinate
│   ├── win32.py           # SetThreadExecutionState
│   └── linux.py           # systemd-inhibit
├── ollama_paths.py        # 抽 ollama_sidecar.py:74,94
└── fonts.py               # 抽 main.py:173-178
```

`tests/desktop/visual_regression.py:22-26` 硬编码 `/tmp/...`：
```python
# BEFORE
WORKSPACE = "/tmp/novel_forge_visual_workspace"
# AFTER
import tempfile
WORKSPACE = str(Path(tempfile.gettempdir()) / "novel_forge_visual_workspace")
```

### Phase 6 — macOS fullscreen（M6）

**v2 修订**：不强制 `Qt.QueuedConnection`（会破坏同步语义），改成**守卫式 defer**。

#### 6.1 `_on_workspace_refreshed` 守卫

```python
# window.py:1402
def _on_workspace_refreshed(self, snapshot, service, payload,
                            section_hashes, changed_sections, snapshot_hash):
    if sys.platform == "darwin" and self._skip_page_motion_for_window_state():
        # v2: defer 到下一 idle 周期，避免 macOS Spaces transition 期间密集 paint
        QTimer.singleShot(0, lambda: self._flush_workspace_refresh_apply(
            snapshot, service, payload, section_hashes,
            changed_sections, snapshot_hash))
        return
    self._flush_workspace_refresh_apply(...)  # 重命名现有方法体
```

**关键**：仅在 macOS 全屏/maximized 状态走 defer；非 macOS 或非全屏状态行为完全不变。

#### 6.2 `WorkspaceEventBus.subscribe` 显式可选 Queued

不破坏现有 AutoConnection 默认行为。新增可选参数：

```python
# desktop/state/event_bus.py:134
def subscribe(self, event_type, callback, *, default_queue: bool = False) -> None:
    signal = self._EVENT_MAP[event_type]
    if default_queue:
        signal.connect(callback, Qt.QueuedConnection)
    else:
        signal.connect(callback)
```

文档明示：AutoConnection 是默认；`default_queue=True` 只在订阅方明确需要异步时用。

#### 6.3 closeEvent 收紧

`window.py:4454 closeEvent` → `_pre_close_cleanup` → 在 `self._job_manager.shutdown(wait_ms=2000)` 之前加：

```python
# NEW: 等所有 in-flight workspace refresh 完成
active = getattr(self, "_active_workspace_refresh_worker", None)
if active is not None:
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
# drain ui_io_pool for any pending signal
desktop_thread_pools().ui_io_pool.waitForDone(500)
```

#### 6.4 验证脚本

新文件 `scripts/verify_macos_fullscreen.py`：

```python
"""验证 macOS fullscreen 下切 page 不退出全屏。

优先用纯 PySide6（QWindow.visibility + 模拟键盘）；如必须 PyObjC，仅作为本地开发工具。
不在 CI 跑；本地 macOS 手测入口。
"""
import sys

def main():
    if sys.platform != "darwin":
        print("[!] only runs on macOS")
        sys.exit(0)
    # 用 PySide6 QTest 模拟 50 次 page 切换
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QWindow
    # ...
```

（详细实现 Phase 6 实际写 plan 时细化）

#### 6.5 真实回归

本地 macOS 人工 checklist 写到 `docs/runbook_macos_fullscreen.md`：
1. 进 macOS 系统全屏（绿色按钮）
2. 切换 5 个 page（dashboard / projects / workflow / settings / chapter_studio）
3. 切回原 page
4. 退出全屏
5. 反复 10 次，期望 0 次异常退出

### Phase 7 — 文档收尾（M7）

新文件：
- `docs/runbook_macos_fullscreen.md`
- `docs/runbook_ui_architecture.md`
- `desktop/AGENTS.md`（顶层）
- `desktop/workers/AGENTS.md`（若 Phase 1 建 `workers/` 子目录）
- `desktop/platform/AGENTS.md`（若 Phase 5 建）

更新：
- `pages/AGENTS.md`（重写）
- `pages/<group>/AGENTS.md`（每个子目录一份）

更新 visual regression baseline：现有 `tests/desktop/baselines/` 9 张 PNG 重生成（自动 + 人工 review）。

---

## 6. 风险与权衡（v2 校准后）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 巨型文件拆分 → file/package 路径冲突 | Phase 3 实施的杀手 | **每拆一个 file → package 都先 git mv + __init__.py re-export + 跑测试** |
| BaseJobWorker 替换破坏 `asyncio.run` 模式 | 用户主动 cancel 失灵 | 框架用 `loop.run_until_complete(loop.shutdown_asyncgens())` 替代，复用现有 cancel 协议 |
| page shutdown 等待全 pool → 被无关任务拖住 | Phase 1 设计的杀手 | **v2 改为 page 只 cancel 自己 worker；wait 由 window 集中** |
| QueuedConnection 强制改写破坏同步语义 | 现有测试失败 | **v2 不强制，改用守卫式 `QTimer.singleShot(0, defer)`** |
| `_HumanizeEmbedderAdapter` 单例 → 线程泄漏 | shutdown 不彻底 | **v2 加 `shutdown()` + `reload()` + 在 `RuntimeServices.shutdown` 显式调** |
| LazyMarkdown 当前不需要 | 范围蔓延 | **v2 只修 `IncrementalDocumentRenderer` 的 chunked insert；不引入 LazyMarkdownViewer** |
| 模型调用 cache 在写入持续增长下 stale | 用户看到旧数据 | v2 用 60s TTL + run_dir mtime 失效 + loader 时强制 invalidate |
| Python 3.11 降级触发 typing_extensions 上限 | pip 解析失败 | 提前 run `pip install -e ".[dev]"` 验证 |
| visual baseline 重画 | 所有 `*visual.py` baseline 失效 | 自动重新生成 + 人工 review |
| 引入新依赖 | pyproject 复杂化 | Phase 1-6 不引入；如确需，仅 Phase 7 候选 |
| `from shiboken6` 直接调用 | Qt7 升级断链 | 本次不动；非目标 |

---

## 7. 验证标准（v2 每 Phase 合并前）

1. `pytest tests/desktop -q` 全过（45 文件）
2. `pytest tests/unit -q` 全过
3. `pytest tests/perf -q` 全过（含 WorkspaceEventBus 相关）
4. `pytest tests/integration -q` 全过
5. `ruff check novel_forge/desktop/` 无新增警告
6. `mypy novel_forge/desktop/` 无新增错误
7. macOS 本地（Phase 6 起）：fullscreen checklist
8. `scripts/benchmark_ui_perf.py` model_call 加载时间 < 200ms（vs 旧主线程阻塞 0.5s+）

---

## 8. 里程碑汇总（v2 校准后）

| 里程碑 | 提交数 | 通过标准 |
|---|---|---|
| M1 Worker 框架 | 1-2 | 8+ worker 全 `BaseJobWorker`；`safe_shutdown_page` 替换手写；`shutdown` 补齐 `deleteLater + ui_io_pool.waitForDone + aux_pool.waitForDone + close alias`；3 个新测试过 |
| M2 性能快赢 | 4-5 | TaskModelCallLoader 走 ui_io_pool；JobSnapshotCache + invalidate；IncrementalDocumentRenderer chunked insert；`_HumanizeEmbedderAdapter` 单例 + shutdown + reload + 在 RuntimeServices.shutdown 调用；benchmark 显著改善 |
| M3 巨型文件拆分 | 8 | 8 个 2500+ 行文件全拆；window.py < 800 行；全测试过 |
| M4 pages 子目录化 | 1 大 + import 迁移 | 7 子目录；AGENTS.md 重写；自动发现 |
| M5 CI/兼容 | pyproject 三处 + CI job + 平台模块 | desktop job 3 runner 全过；Python 3.11 全工具链对齐 |
| M6 macOS fullscreen | 1-2 | 守卫在；event_bus 可选 QueuedConnection；closeEvent 收紧；本地手测过 |
| M7 文档 | 文档提交 | 设计文档 + runbook 齐全 |

可平行：M1 与 M5.1 Python 决策可同时启动。

---

## 9. 实施顺序（v3 重排：M5.1 → M5.2 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7）

用户建议的新顺序理由：**"在大拆分前先让 desktop 非视觉测试进 CI，后面的 worker 和拆包风险会更可控"**。

1. **M5.1** — pyproject 三处 3.11 兼容 + 验证 — 零风险，立即做
2. **M5.2** — desktop-tests CI job（非 visual，3 runner）— 第一份"基线" — 不依赖 worker 重构
3. **M1** — Worker 框架 + safe_shutdown_page — 在新基线保护下做
4. **M2** — 性能快赢（TaskModelCallLoader 异步 + JobSnapshotCache + embedder 单例 + chunked insert bench 脚本）— 依赖 M1 的 BaseJobWorker
5. **M6** — macOS fullscreen 守卫（独立、window.py 加守卫 + closeEvent 收紧 + WorkspaceEventBus.subscribe 可选 default_queue）— 在 M3 拆 window.py 前修好
6. **M3** — 8 个 巨型文件拆 package（git mv → __init__.py → 子拆分）— worker 与性能稳定后
7. **M4** — pages 子目录化（pkgutil 自动发现 + import 迁移脚本）— 依赖 M3 的子目录模式
8. **M5.3** — 跨平台三模块（sleep_inhibit/ollama_paths/fonts）+ visual baseline 重生成 — 独立最后做，避免早期阻塞
9. **M7** — 文档收尾

---

## 10. 非目标与潜在扩展

**本次明确不做**：
- 插件机制（entry_points / dynamic import）
- i18n 抽取（gettext + .po）
- Qt7 / shiboken7 升级
- pipeline / workflow 逻辑重写
- 重新设计 navigation（保持 QStackedWidget）
- 引入新的 LLM provider
- pipeline worker 拆分（pipeline/long/ 与本设计解耦）
- 立即引入 `LazyMarkdownViewer`（当前 28KB 章节未触发瓶颈；先修 chunked insert）

**潜在扩展**：
- chapter > 200KB 时引入 viewport-based `LazyMarkdownViewer`
- Visual regression 在 macOS / Windows runner
- 真 RT 编辑器（vs QTextBrowser）
- metadata-driven page entry points（装饰器 + 扫描）
- 嵌入式可视化（如生成式 pipeline 流程图）

---

## 11. 附录

### 11.1 文件影响清单（实施时再确认）

详细 import path 改造表见 `scripts/refactor_pages_layout.py` 自动生成的映射。

### 11.2 测试影响清单

新增（共 9 个，列了 9 项；以列表为准）：
- `tests/desktop/test_worker_base.py`
- `tests/desktop/test_page_shutdown.py`
- `tests/desktop/test_jobs_manager_shutdown.py`
- `tests/desktop/test_snapshot_cache.py`
- `tests/desktop/test_lazy_markdown.py`
- `tests/desktop/test_pages_autodiscovery.py`
- `tests/desktop/test_window_imports.py`
- `tests/desktop/test_jobs_imports.py`
- `tests/unit/test_humanize_embedder_singleton.py`

### 11.3 文档新增清单

- `docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md`（本文 v3）
- `docs/runbook_macos_fullscreen.md`
- `docs/runbook_ui_architecture.md`
- `desktop/AGENTS.md`
- `desktop/window/AGENTS.md`
- `desktop/jobs/AGENTS.md`
- `desktop/components/task_focus/AGENTS.md`
- `desktop/components/memory_components/AGENTS.md`
- `desktop/pages/document_renderer/AGENTS.md`
- `desktop/pages/settings/AGENTS.md`
- `desktop/pages/AGENTS.md`（重写）
- `desktop/pages/<group>/AGENTS.md`（7 份）

### 11.4 新脚本清单

- `scripts/refactor_pages_layout.py`
- `scripts/verify_cross_platform_paths.py`
- `scripts/verify_macos_fullscreen.py`（本地 GUI 用，不入 CI）

---

## 12. 文档 metadata

- **v1 提交**：`87da491f2 docs(spec): design for Novel Forge Desktop UI 全栈优化`（已废弃）
- **v2 提交**：本次 amend
- **作者**：ZCode (brainstorming skill flow)
- **审查者**：用户
- **批准日期**：待批准
- **实施开始日期**：待批准后
- **预计完成**：8 Phase × 平均 1-3 提交 = 10-15 提交
- **优先级**：高（用户已确认 4 维度均为优先）

— END OF DESIGN v2 —

---

## 13. v2.3 增量附录（用户聚焦：性能 + 交互动画 + Mac/Win 适配）

### 13.1 范围与定位

本附录是对 v3 主体的**叠加**而非替换。v3 的 9 phase（M5.1 → M5.2 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7）继续作为主干。

用户在 v3 批准后希望把焦点收窄到三维度，并要求基于 v3 现状（**已实施**）补充：
- 性能：v3 M2 已覆盖大头，本附录补若干遗漏
- 交互动画：v3 完全没有覆盖；用户的「原生动效替换 QPropertyAnimation」方向经多轮 review 后被否定
- Mac/Win 适配：v3 M5.3 + M6 已覆盖主要项，本附录补若干遗漏

### 13.2 关键修正（v2.1 → v2.3 推翻的方向）

| 方向 | v2.1 提议 | 修正后立场 | 原因 |
|---|---|---|---|
| MotionAdapter 平台抽象层 | 把 QPropertyAnimation 替换为 Mac/Win 原生 | **不做** | 抽象层会破坏现有 fade；Mac 端 windowOpacity 不能通用替代 QGraphicsOpacityEffect；用户复审后撤回 |
| 新增 page-switch fade | 解决全屏稳定性 | **反向** | 当前 `Motion.fade_in` 本身就是触发 AppKit 退出 Space 的原因；不应再加 |
| 复制 `JobCard` pixmap cache 到 chapter_studio jobs panel | 解决 chapter_studio 卡顿 | **改为只复制节流（16 ms）** | pixmap cache 是 `JobCard` 内部优化，子类已继承；真正缺的是 render coalescing |
| `SegmentWidget` cursor 优化 | 减少每段 cursor 重建 | **已实现** | `stream_detail.py:848-856` `_sync_streaming_cursor` 已仅对最后一段 content 开 cursor |

### 13.3 8 处伪代码与现状冲突（v2.2 → v2.3 修正）

经逐项核验实际代码后修正：

1. `window_navigation.py:228` 在 `setCurrentWidget` 后重新赋值 `_force_instant_page_transition_once = defer_cold_page or (...)`，会覆盖 safe mode 预设为 True → 改用**独立 flag** `_mac_fullscreen_safe_mode_active`
2. `navigation.py:348` `_skip_page_motion_for_window_state` 把 `WindowMaximized` 和屏幕几何填充也判为 unsafe → 区分"原生 fullscreen"与"动画不安全"是必需的，新增 `_is_native_fullscreen_active()` 只检查 `isFullScreen()` / `QWindow.Visibility.FullScreen`
3. `dashboard_page.py:1600-1611` hero 显式管理 `QGraphicsDropShadowEffect` ↔ `QGraphicsOpacityEffect` 切换（D10 互斥）；`popups.py:97` 也用 `setGraphicsEffect(shadow)` → safe mode 入口**只**清 `QGraphicsOpacityEffect`
4. `coord.py:1170-1259` `bind_jobs` 不止渲染（还含 active projects / status dot / memory events / rail / `_try_auto_action`）→ 节流**仅**作用于 `_render_jobs_panel` 调用点
5. `page.py:245-282` 初始化在 `ChapterStudioPage.__init__`，mixin 无独立构造链 → 改为**lazy 创建** timer
6. `token_analytics.py:1453-1458` `_render_model_cost_ring_image` 是 widget 实例方法，无 `widget` 参数 → 用 `self.devicePixelRatioF()`
7. spec 文件路径 → `novel_forge_desktop.spec`（**仓库根**），非 `novel_forge/novel_forge_desktop.spec`
8. `asyncio.WindowsSelectorEventLoopPolicy` 仅 Windows 存在 → `hasattr(asyncio, "WindowsSelectorEventLoopPolicy")` 守护；测试用 `monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy)`

### 13.4 增量项与优先级

| 等级 | 编号 | 项目 | 来源 v3 是否覆盖 |
|---|---|---|---|
| P0 | I-1 | macOS fullscreen safe mode（独立 flag + 单独 fallback 函数 + 只清 opacity effect） | v3 M6 仅覆盖 guard 触发时机；本附录修正 flag 冲突 + 区分 native fullscreen + 范围化清 effect |
| P0 | I-2 | `_render_jobs_panel` 局部节流（不包 `bind_jobs`） | 未覆盖 |
| P1 | I-3 | HTML 字符串 hash cache（widget-property 模式） | 未覆盖 |
| P1 | I-4 | humanize_library_dashboard 改 `QAbstractTableModel` | 未覆盖 |
| P1 | I-5 | 4 个 standalone page 补 `shutdown()` | 未覆盖 |
| P1 | I-6 | QSS px → pt 迁移（dry-run 试水先行） | 未覆盖 |
| P2 | I-7 | Windows `WindowsSelectorEventLoopPolicy`（hasattr 守护） | v3 M5.3 未覆盖 asyncio policy |
| P2 | I-8 | `token_analytics` devicePixelRatio 感知位图（`self.devicePixelRatioF()`） | 未覆盖 |
| P2 | I-9 | 应用名 NIMO（spec 路径修正） | v3 M5.3 未覆盖 |
| P2 | I-10 | `xfail(run=False, strict=False)` 降级 | 未覆盖 |
| P3 | I-11 | macOS 强制退出 atexit 修复 | 未覆盖 |

### 13.5 关键设计：macOS fullscreen safe mode

**问题重述**：`switch_page` 流程（`window_navigation.py:181-242`）当前在 `setCurrentWidget` 之后才设 `_force_instant_page_transition_once`，而现有 `_skip_page_motion_for_window_state`（`navigation.py:335-382`）只在动画方法内部触发——`setCurrentWidget` 已触发 paint，无法阻止 swap 本身被 AppKit 识别为状态变化。

**v2.3 方案**：

1. **新 flag**：`_mac_fullscreen_safe_mode_active: bool`（独立于 `_force_instant_page_transition_once`，避开 `window_navigation.py:228` 覆盖）
2. **新检测函数**：`_is_native_fullscreen_active()` 只检查原生 fullscreen（`isFullScreen()` / `QWindow.Visibility.FullScreen`），与 `_skip_page_motion_for_window_state` 区分
3. **入口**：`_enter_mac_fullscreen_safe_mode()` 在 `_active_page_id = page_id` 之前调（`switch_page` 内）
4. **作用范围**：
   - 设 `_mac_fullscreen_safe_mode_active = True`
   - 记录 `_mac_fullscreen_at_switch_start_time = monotonic()`
   - **只**清 old/new page root widget + top-bar labels 上的 `QGraphicsOpacityEffect`（保留 `QGraphicsDropShadowEffect`）
5. **消费点**：`_animate_current_page` / `_animate_top_bar_title` 在 `if not _animations_supported("opacity")` 后立刻检查 flag；True 则清 effect + return（**不**调 `Motion.fade_in/out`）
6. **post-switch fallback**：`_exit_mac_fullscreen_safe_mode()` 120 ms 后调；用 `_is_native_fullscreen_active` 判断（**不**用 `_skip_page_motion_for_window_state`，因为后者会把 maximized 也判为 fullscreen 漏掉恢复）；5 秒超时后清 flag

**关键代码草图**（完整版见 v2.3 计划正文）：

```python
# window/navigation.py — NavigationMixin

def _is_native_fullscreen_active(self) -> bool:
    """只检查 AppKit 原生 fullscreen（不含 WindowMaximized）。"""
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


def _enter_mac_fullscreen_safe_mode(self) -> bool:
    if sys.platform != "darwin":
        return False
    if not self._is_native_fullscreen_active():
        return False
    import time as _time
    self._mac_fullscreen_safe_mode_active = True
    self._mac_fullscreen_at_switch_start_time = _time.monotonic()
    from PySide6.QtWidgets import QGraphicsOpacityEffect
    targets: list[QWidget] = []
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
            # 只清 QGraphicsOpacityEffect，保留 QGraphicsDropShadowEffect
            if isinstance(effect, QGraphicsOpacityEffect):
                w.setGraphicsEffect(None)
        except RuntimeError:
            pass
    return True


def _exit_mac_fullscreen_safe_mode(self, page_id: str, generation: int) -> None:
    if not getattr(self, "_mac_fullscreen_safe_mode_active", False):
        return
    import time as _time
    if (_time.monotonic() - getattr(self, "_mac_fullscreen_at_switch_start_time", 0.0)) > 5.0:
        self._mac_fullscreen_safe_mode_active = False
        return
    if not self._post_switch_current(page_id, generation):
        return
    if self._is_native_fullscreen_active():
        return
    self._mac_fullscreen_safe_mode_active = False
    try:
        if self.isVisible():
            self.showFullScreen()
    except RuntimeError:
        pass
```

**`switch_page` 钩子位置**（`window_navigation.py:181-242`）：
- 在 `owner._active_page_id = page_id` 之前插入 `owner._enter_mac_fullscreen_safe_mode()`
- 函数末尾追加 `QTimer.singleShot(120, lambda: owner._exit_mac_fullscreen_safe_mode(page_id, generation))`

### 13.6 实施顺序（11 项）

| # | 阶段 | 内容 | 风险 | 估时 |
|---|---|---|---|---|
| 1 | I-1 | macOS fullscreen safe mode | 中 | 1-2d |
| 2 | I-2 | `_render_jobs_panel` 局部节流 | 极低 | 0.5d |
| 3 | I-7 | Windows SelectorEventLoopPolicy | 低 | 0.5d |
| 4 | I-9 | 应用名 NIMO | 低 | 0.5d |
| 5 | I-10 | xfail strict=False | 极低 | 0.25d |
| 6 | I-11 | macOS atexit 修复 | 极低 | 0.25d |
| 7 | I-5 | 4 个 page 补 shutdown | 低 | 1d |
| 8 | I-8 | devicePixelRatio 位图 | 低 | 0.5d |
| 9 | I-3 | HTML hash cache | 低 | 1d |
| 10 | I-6 | QSS px → pt（dry-run 试水 → visual diff → 批量） | 中 | 2-3d |
| 11 | I-4 | humanize 表模型 | 中 | 2-3d |

### 13.7 验证标准

1. `pytest tests/desktop -q` 全过（既有 ~45 + 新增 ~7）
2. `pytest tests/unit -q` 全过
3. `ruff check novel_forge/desktop/` 无新增警告
4. `mypy novel_forge/desktop/` 无新增错误
5. I-1 macOS 本地：fullscreen checklist 3 项（hero shadow 不被错清）
6. I-7 Windows 本地：SelectorEventLoop policy 应用后 `asyncio.open_connection` 无 WinError 121
7. I-3 demo：100 次相同 setHtml → 实际 setHtml 调用 ≤ 1
8. I-4 / I-6 visual regression baseline 重生成

### 13.8 明确不做的（v2.3 撤回）

- MotionAdapter / Mac 原生 page fade / `windowOpacity` 替换 / cursor gate / JobCard 动画（已落地或方向错误）
- Mac `QMenuBar` + Cmd 快捷键（独立特性，后续单独 brainstorm）
- 8 个巨型文件拆分（v3 M3 单独处理）
- dark/light mode toggle
- 新依赖

### 13.9 文档元数据

- **v2.3 提交**：本次 amend
- **作者**：ZCode (brainstorming skill flow, follow-up)
- **审查者**：用户
- **批准日期**：本次
- **预计完成**：11 项 × 平均 0.5-3d = 8-12d 累计
- **优先级**：高（聚焦三维度）

— END OF DESIGN v2.3 —
