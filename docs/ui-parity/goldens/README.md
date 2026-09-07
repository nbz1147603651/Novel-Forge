# PySide6 Golden Capture

This directory intentionally stores capture instructions and manifests, not
large PNG binaries. Capture artifacts are generated into a temporary evidence
directory and referenced by the parity report together with their SHA-256
values. This avoids versioning stale screenshots while retaining a repeatable
source-of-truth command.

Run from the Phase 1 worktree:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-pyside-primary-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink \
  --viewport 1440x900 \
  --viewport 1180x760 \
  --viewport 1728x1117
```

The primary matrix contains six registered pages across five themes and three
viewports: 90 deterministic screenshots. `manifest.json` records the source
fixture, viewport, theme, filename, and SHA-256 for every image.

Use `run_pyside_golden_capture.py`, not the implementation module directly.
The launcher provides the stable Python construction boundary required by the
macOS/offscreen Qt host. The capture also waits for the cold workspace refresh
to settle, then reapplies the immutable fixture and the custom-painted shell
gradients so every theme/image contains the named workspace rather than the
host's previous UI session.

The fixture is test-only (`tests/desktop/ui_parity_fixtures.py`); it does not
call a Provider, read a user project, or mutate the writing engine. Secondary
surface captures will be added with explicit fixture state and dynamic masks
before their individual parity claims are accepted.

The capture runner resets the selected page's vertical scroll position after
the deterministic fixture binds. This keeps a source golden from inheriting a
previous session's scroll offset and makes top-of-page layout comparisons
repeatable.

## 声腔已配置团队状态

声腔的正常入口可以没有项目，因此默认截图只验证空/读取态。下面的独立状态
在不启动 TTS worker、Provider 或写入配音制品的前提下，绑定同一套已配置的旁白、
角色和平台能力投影；它才是 React `voice_state=configured` 的源端对照：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-voice-configured \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages voice_studio \
  --voice-studio-state configured \
  --voice-studio-tab team
```

PySide6 输出名为 `voice_studio--narrative_ember--voice-configured-team--1440x900.png`。
对应的 Tauri 原生捕获必须使用同名状态，不能以默认 Mock 首屏替代：

```bash
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-voice-configured \
  --page voice_studio \
  --theme narrative_ember \
  --viewport 1440x900 \
  --voice-studio-state configured \
  --voice-studio-tab team
```

该状态要求顶部统计、当前项目、团队页签和“保存设置”入口均来自同一只读
`VoiceStudioView`，避免页面内再复制一组角色统计而挤占主编辑区。

### 声腔平台设置默认态

`VoiceStudioPage` 会从本地 UI 状态恢复最后一枚标签和设置滚动位置；因此截图必须
显式钉住 `settings`，不能继承开发机的 QSettings。捕获器会复原源端的新页默认折叠状态：
「本机音频模型中心」「质量目标与模型计划」「当前平台」展开，其他分区收起，滚动条回到顶端。

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-voice-settings \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages voice_studio \
  --voice-studio-state configured \
  --voice-studio-tab settings
```

该状态生成 `voice_studio--narrative_ember--voice-configured-settings--1440x900.png`。
React 固定入口为
`?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&voice_tab=settings`；
它必须保留相同的标签恢复、默认展开集合、设置滚动容器、保存草稿反馈与 reduced-motion
展开行为。浏览器截图只能作为诊断；该帧仍需同状态的 Tauri/macOS 原生捕获和像素报告。

```bash
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-voice-settings \
  --page voice_studio \
  --theme narrative_ember \
  --viewport 1440x900 \
  --voice-studio-state configured \
  --voice-studio-tab settings
```

## Workflow ready-state capture

The workflow surface builds its composer, task-flow and focus panels over
short Qt event-loop turns. Use its explicit ready-state fixture rather than a
fixed sleep, otherwise the source image can freeze the temporary
“正在准备机杼” placeholder:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-workflow-running \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages workflow \
  --workflow-state running
```

This injects only a fixed `DesktopJobRecord`, freezes its elapsed label, and
waits for `WorkflowPage.is_ui_ready()`. It never starts a pipeline or calls a
Provider. The resulting `workflow-running` image is the primary-surface
reference for the React composer and the lower task-flow panels.

## 火候 ready-state capture

火候页也会分两步延迟构建：先装载页骨架，再创建 hero 与运行总览卡片。捕获器
会等待 `SettingsPage.is_ui_ready()`，随后重新绑定固定 workspace snapshot；这样
截图不会误把“加载中”或尚未填入运行参数的空卡片当成视觉基线。该等待只观察 Qt
布局状态，不检测通路、不读取用户项目，也不写入配置。

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-settings-ready \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages settings
```

在这一基线下，待检测模型只显示琥珀状态点；卡片本身保持中性边框。React 侧应
复刻这个层级，而非把整张待检测卡渲染为 warning surface。

## 卷帙 reader states

The source opens 卷帙 with no reader selected when the side rail is used
directly. Capture that empty state first; the populated reader is a separate,
explicit interaction state, not an implicit first-project selection:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-projects-reader \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages projects \
  --projects-state reader
```

The default command produces `projects--loaded`; the additional command
produces `projects--projects-reader` after selecting the deterministic long
project. Both states must be compared before the P0 卷帙 page is accepted.

角色与实体的三枚内层标签需要独立捕获。特别是「关系」是角色编辑器内的表格与
详情卡，不是独立关系网络页；「图谱」则保留左侧角色列表及可编辑节点。二者均使用
同一份临时角色档案夹具：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-projects-characters \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages projects \
  --projects-state character-relationships
```

Use `--projects-state character-graph` for the adjacent graph state.  Do not
accept either React inner tab from a screenshot of the other one.

### 卷帙未保存修改确认窗

项目切换前的终稿砚修确认使用源端共享 `show_message_box`，不是单独的业务窗。
以下测试固定“终稿修订尚未保存”的三枚动作、PySide6 原生 question 图标及 440×147
内容几何；大纲和角色复用同一外壳，但保留各自的文案和取消标签。

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
-m pytest tests/desktop/test_reader_unsaved_session_visual.py --update-baselines -q
```

React 的对应 Chromium interaction snapshot 位于
`clients/nimo-desktop/tests/project-reader.spec.ts-snapshots/`。它只证明本地会话的
保存、放弃和取消语义；仍须以同夹具 Tauri capture 对照 PySide6，尤其是系统 question
图标和字体栅格，才能完成组件级视觉验收。

## 章台动态状态矩阵

章台在仅绑定 workspace 时只能展示空骨架；有真实项目数据时，它还会展示章节
轨道、当前章罗盘、承接记忆与下一步建议。`prepared`、`running`、`checkpoint`
和用户主动打开的 `checkpoint-dialog` 都必须独立捕获，不能由 React mock 反向定义：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-chapter-state \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages chapter_studio \
  --chapter-studio-state prepared \
  --chapter-studio-state running \
  --chapter-studio-state checkpoint \
  --chapter-studio-state checkpoint-dialog
```

该命令生成四个同尺寸状态：`chapter-prepared`、`chapter-running`、
`chapter-checkpoint` 和 `chapter-checkpoint-dialog`。夹具只构造
`ChapterWorkspaceSnapshot` 读模型；运行态额外绑定一个固定的 `DesktopJobRecord`，
决策态额外绑定一个固定的 `DecisionCheckpoint`。最后一项只模拟用户点开“定位 AI
建议浮窗”，并把源端独立 `Qt.Tool` 面板合成到窗口基准图中；它不是浏览器居中模态窗。
所有状态都不读取项目文件、不启动章节任务。它们共同约束 React 的控制区高度、三栏
比例、运行提示、取消语义与浮动建议入口；还需同状态 React/Tauri 截图与像素差异
后才能验收。

## 火候连接状态组件矩阵

空闲、检测中、成功和失败卡片都由同一个单模型夹具冻结。它只调用
`_ModelStatusCard` 的本地展示方法，不启动 Provider worker：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-settings-connection-matrix \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages settings \
  --settings-connection-state idle \
  --settings-connection-state checking \
  --settings-connection-state success \
  --settings-connection-state failure
```

四个 `connection-*` 图像是动态卡片的源证据；它们仍需与同一夹具的
React/Tauri 状态逐帧比较，才能将该组件标记为 `accepted`。

## 火候创作参数展开态

“创作火候 — 浮动与适用范围”在 PySide6 中是页面内的折叠区，不是弹窗。
下面的状态会构建其延迟参数面、折叠其他分区，并将目标滚到稳定的可见位置；
它不保存 QSettings，也不会启动模型检测：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-settings-creative-temperature \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages settings \
  --settings-section-state creative-temperature
```

生成的 `settings--narrative_ember--section-creative-temperature--1440x900.png`
是 React 创作火候展开快照的源证据；它仍需要同夹具的 Tauri/macOS 帧和
逐像素报告，才能获得跨应用视觉验收。

## 火候流程路由展开态

“流程路由 — 为每个步骤选择模型与能力”同样是 PySide6 页面内折叠区。这个 fixture
构建延迟路由标签、折叠其他设置区并把路由矩阵置于视口；它不保存配置、不读取用户密钥、
也不启动 Provider 检测：

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/run_pyside_golden_capture.py \
  --output-dir /tmp/nimo-ui-parity-settings-routing \
  --theme narrative_ember \
  --viewport 1440x900 \
  --pages settings \
  --settings-section-state model-routing
```

React 对应的受限 URL 是
`?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_section=model-routing`。
它只展开当前组和子流程，确保大任务目录不在火候入口阶段造成同步渲染负担。原生 Tauri
采集使用相同状态：

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-settings-routing \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page settings \
  --theme narrative_ember \
  --settings-section model-routing \
  --viewport 1440x900
```

## Chapter-dialog component matrix

The source-sized chapter dialogs use a separate deterministic fixture because
their Qt size is part of the component contract:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_dialog_goldens.py \
  --output-dir /tmp/nimo-ui-parity-chapter-dialog-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

This produces eight dialogs (export, version diff, book audit, clean chapters,
skip strategy, project switch, chapter error log, and the floating checkpoint
panel) across all five themes: 40 component screenshots. The checkpoint fixture uses the source
panel's actual non-modal tool positioning; it is deliberately not converted
into a centered confirmation dialog. The React dialog foundations must still
pass same-state component comparison before they can be marked `accepted`.

导出、全书审计、清理、连跑策略和错误日志的 React/Tauri 同状态入口已经受限到 `prepared` 章台；
切换项目则只允许覆盖固定的 `running` 章台，避免在错误状态误捕获一张不存在于源端的
覆盖层。原生 capture 获得可用的 Screen Recording 权限后，分别使用下列 URL 等价命令
捕获，再裁成与对应 Qt source 文件相同的组件范围；不可把 Chromium snapshot 当作替代品：

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --page chapter_studio --chapter-studio-state prepared --chapter-dialog export \
  --theme narrative_ember --viewport 1440x900 --output-dir /tmp/nimo-tauri-export

./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --page chapter_studio --chapter-studio-state prepared --chapter-dialog book-audit \
  --theme narrative_ember --viewport 1440x900 --output-dir /tmp/nimo-tauri-book-audit

./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --page chapter_studio --chapter-studio-state prepared --chapter-dialog clean \
  --theme narrative_ember --viewport 1440x900 --output-dir /tmp/nimo-tauri-clean

./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --page chapter_studio --chapter-studio-state prepared --chapter-dialog error-log \
  --theme narrative_ember --viewport 1440x900 --output-dir /tmp/nimo-tauri-chapter-error-log

./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --page chapter_studio --chapter-studio-state running --chapter-dialog project-switch \
  --theme narrative_ember --viewport 1440x900 --output-dir /tmp/nimo-tauri-project-switch
```

## Workflow diagnostic component matrix

The workflow error log and stop confirmation share a dedicated fixture. The
error log includes one unresolved and one auto-recovered format entry; the
confirmation uses a running long-init job and a fixed resume checkpoint:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_workflow_goldens.py \
  --output-dir /tmp/nimo-ui-parity-workflow-error-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

This produces ten source images (two dialogs × five themes). They are
component evidence only; the React counterparts remain `foundation` until
same-state screenshots are compared.

## Historical PySide6 workflow preset matrix

The long-form preset editor is captured as seven source-sized dialogs: field
editor, AI hint, AI polish, progress, source error feedback, selectable diff
and populated history.
The fixture is confined to the capture process; it patches only dialog-local
draft/history readers and does not open a project or provider configuration.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_workflow_preset_goldens.py \
  --output-dir /tmp/nimo-ui-parity-workflow-preset-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

This remains source-side visual evidence only. Nimo no longer exposes these
states through `workflow_dialog=preset-*`: its long-init form, AI changes and
creative-note history are all Engine-backed, avoiding a second in-memory
preset state.

## Task-focus floating stream matrix

The detached stream window uses an interleaved reasoning/content fixture. It
exercises the same event order, collapsed-thinking state and source-sized
window geometry that the React floating surface consumes:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_task_focus_goldens.py \
  --output-dir /tmp/nimo-ui-parity-floating-stream-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

React/Tauri uses a restricted `workflow_dialog=floating-stream` startup state
to project the same replay. It includes immutable task/model/token facts in
the `TaskStreamView` snapshot, so the component does not infer them from DOM
text or a timer. Capture its native frame only after building the debug
executable described below:

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-floating-stream \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page workflow \
  --theme narrative_ember \
  --workflow-dialog floating-stream \
  --viewport 1440x900
```

The native frame must be component-cropped to the source 560×540 tool before
comparison; the full workflow page is not a substitute for that P1 gate.

The same restricted workflow startup also freezes the source-sized error-log
and stop-confirmation dialogs without synthesising a click. Replace
`floating-stream` with `error-log` or `cancel` in the command above. The
React fixtures retain the source error ordering and the cancel/resume boundary;
their browser component baselines do not constitute native parity acceptance.

## Narrative timeline component matrix

This fixed 24-chapter fixture freezes four phases, six mainline turns and
three subplot lanes before a React/Tauri comparison is claimed:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_narrative_visualization_goldens.py \
  --output-dir /tmp/nimo-ui-parity-narrative-timeline-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

## Relationship graph component matrix

The graph fixture shares the three named characters and three links used by
the React mock. It captures both the ordinary graph and a focused-protagonist
state, so node fading and edge emphasis have a deterministic source reference:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_relationship_graph_goldens.py \
  --output-dir /tmp/nimo-ui-parity-relationship-graph-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

`hover-node` and `hover-edge` are separate source states because Qt renders
them in a frameless top-level popup rather than inside the graph paint device.
Capture them from the same fixture before changing React tooltip geometry;
the long character detail is currently `612×338` and the relationship detail
is `248×110` in `narrative_ember`:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_relationship_graph_goldens.py \
  --output-dir /tmp/nimo-ui-parity-relationship-tooltip-source \
  --theme narrative_ember \
  --state hover-node \
  --state hover-edge
```

The editable graph's post-drag `RelationshipEditDialog` is another separate
source surface. Its size is layout-driven (`520×417` in the current fixture),
and its relationship-type combo is defined by the canonical writer options;
capture it whenever its React counterpart changes:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_relationship_graph_goldens.py \
  --output-dir /tmp/nimo-ui-parity-relationship-dialog-source \
  --theme narrative_ember \
  --state relationship-edit-dialog
```

## Character profile component matrix

The profile fixture uses the same three character records and captures both a
selected protagonist and the source edit mode. Its temporary project is
created only inside the operating-system temp directory and is removed after
each capture:

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_character_profile_goldens.py \
  --output-dir /tmp/nimo-ui-parity-character-profile-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

## Voice-team rebuild component matrix

The voice-team rebuild capture freezes the source dialog at its 576×560
geometry with three rebuildable roles. The work-level narrator is intentionally
absent because the source provides it as a separate voice action.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_voice_rebuild_goldens.py \
  --output-dir /tmp/nimo-ui-parity-voice-rebuild-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

The resulting five images are source evidence only. The React dialog stays
`foundation` until a same-state React/Tauri comparison is recorded.

## Voice script segment editor component matrix

The script fixture freezes a three-segment chapter-four dubbing script. It
captures both the source editor's 853×1076 normal-script state and its
693×772 performance-guidance state. Both include the script text, emotion,
directing controls and footer actions, but do not open a project, read
credentials or synthesize audio.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_voice_script_editor_goldens.py \
  --output-dir /tmp/nimo-ui-parity-voice-script-editor-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

The resulting ten source captures define the two component geometries and
theme matrix. The React editor intentionally keeps its extra TTS-directing
fields in memory; performance guidance returns the original source script
unchanged until an explicit, versioned EngineClient take-draft command exists.
Both paths remain `foundation` until same-state React/Tauri comparison is
recorded.

## Voice clone supplier-reference component matrix

The native application opens a system file picker for local-reference-capable
providers. That operating-system-owned picker is intentionally not captured.
For providers that require a prior upload, this matrix freezes the native
420×200 supplier File ID prompt used before clone authorization and transcript
collection.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_voice_clone_goldens.py \
  --output-dir /tmp/nimo-ui-parity-voice-clone-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

React keeps only a selected filename or File ID in memory, along with an
explicit consent checkbox when a provider requires it. It does not read audio
bytes or send data anywhere. Capture the same restricted provider-ID state from
the built Tauri WebView with the shared runner:

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-voice-clone-provider-id \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page voice_studio \
  --theme narrative_ember \
  --voice-dialog clone-provider-file-id \
  --viewport 1440x900
```

The component must then be cropped to the source 420×200 content rectangle and
compared without a dynamic-value mask. It remains unaccepted until that native
capture meets the component threshold and a protected upload/clone EngineClient
command exists.

## Voice design brief component matrix

The voice-design fixture opens the same native multiline brief editor used by
the voice studio, with a sanitized Lin Zhu brief and no project, provider or
audio operation. In the current offscreen environment its configuration state
is 624×520.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_voice_design_goldens.py \
  --output-dir /tmp/nimo-ui-parity-voice-design-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

The resulting five source images freeze the configuration state only. The
React running, completed and failed views are deliberate local state-machine
coverage, and remain `foundation` until equivalent React/Tauri captures and a
future protected EngineClient command are available.

## Model-profile editor component matrix

The settings capture freezes the native add and edit dialogs with a sanitized
fixture profile. It does not load `model_profiles.json`, a `.env` file, or a
connection worker.

```bash
QT_QPA_PLATFORM=offscreen \
PYTHONPYCACHEPREFIX=/tmp/nimo-ui-parity-pycache \
./.venv/bin/python \
tools/ui-parity/capture_pyside_model_routing_goldens.py \
  --output-dir /tmp/nimo-ui-parity-model-routing-matrix \
  --theme narrative_ember \
  --theme ink_jade \
  --theme ink_amethyst \
  --theme stillwater \
  --theme twilight_ink
```

This produces ten source captures (add/edit × five themes). They establish
the source form geometry and labels only; React remains `foundation` until
same-state component and Tauri comparisons are complete.

## Tauri/macOS native-content capture

The native runner does not reuse Chromium screenshots. It starts a locally
built Tauri executable with a restricted `__nimo_ui_parity=1` query, which can
only seed the fixed mock page/theme/reader state before React mounts. It gives
the temporary process a title-bar-free client rectangle at the requested
viewport, waits for the WebView's bundled JavaScript to settle, captures only
the native window owned by that temporary process until two consecutive images
are byte-stable, and records the crop and SHA-256 in a manifest. The PySide6 golden is a
`QWidget.render()` content frame, so this client mode—not a decorated macOS
window—is the valid cross-app comparison target. Each source record includes
both the requested native `viewport` and the measured `frame`. On a host that
adds native chrome, those values may differ; that is capture accounting, not
a UI density difference. Compare the Tauri capture against `frame`, not the
requested outer viewport.

Frame settlement still requires two repeated captures. A focused text field may
blink a single caret after the WebView has otherwise settled, so the runner may
accept only a bounded `0.008%` changed-pixel exception during settlement. This
is capture liveness only: the stored frame remains subject to the unchanged
strict screenshot-comparison threshold.

Before writing the frame, the runner converts a display-tagged native capture
(such as Color LCD / Display P3) to sRGB. This is a color-space normalization,
not a visual tolerance: it prevents the host monitor profile from generating a
whole-frame numerical difference before geometry, font, and component styling
are even evaluated.

Build the local debug executable first:

```bash
pnpm ui:build
(cd clients/nimo-desktop/src-tauri && cargo build)
```

The capture runner rejects a binary whose modification time is older than the
newest file in `clients/nimo-desktop/dist/`, and records the checked asset in
its manifest. This prevents a stale embedded Vite bundle from being accepted as
a visual result: it can start normally but leave a white WebView when an old
hashed JavaScript filename no longer exists. Re-run the two commands above
after every frontend build before collecting native evidence.

Then capture the same settings-section fixture as the source example above:

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-settings-creative-temperature \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page settings \
  --theme narrative_ember \
  --settings-section creative-temperature \
  --viewport 1440x900
```

章台的三个主窗状态使用同一个受限查询；`checkpoint` 此处只捕获主窗，独立
AI 建议浮窗仍应使用 chapter-dialog component matrix 采集。这样不会把顶层
Qt Tool 是否落在主窗抓取范围内，误判为章台布局差异：

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-chapter-running \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page chapter_studio \
  --theme narrative_ember \
  --chapter-studio-state running \
  --viewport 1440x900
```

将 `running` 分别替换为 `prepared` 与 `checkpoint`，即可得到同一
`ChapterStudioActivityView` 读模型的三个原生帧。输出文件名会包含
`chapter-<state>`，防止同一目录下互相覆盖。

若要把源端的非阻塞“方案需确认”浮窗与 checkpoint 主窗一起冻结，显式请求
它的 overlay，而不是依赖一次点击或本机 session：

```bash
./.venv/bin/python \
tools/ui-parity/capture_tauri_macos.py \
  --output-dir /tmp/nimo-ui-parity-tauri-chapter-checkpoint-dialog \
  --binary clients/nimo-desktop/src-tauri/target/debug/nimo_desktop \
  --page chapter_studio \
  --theme narrative_ember \
  --chapter-studio-state checkpoint \
  --chapter-dialog checkpoint \
  --viewport 1440x900
```

`--chapter-dialog checkpoint` 强制要求 checkpoint 主窗状态；它只会在受限
`__nimo_ui_parity=1` URL 中初始化一次前端显示状态，常规启动和用户会话均不会
自动弹出该浮窗。

The runner needs macOS Screen Recording permission for the terminal/Codex
host. It closes only the temporary Tauri process that it launches; it never
reads a user project or starts a provider. On machines whose selected Command
Line Tools Swift is newer than its SDK, supply a compatible SDK explicitly,
for example:

```bash
  --swift-sdk /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
```

`--window-mode client` is the default and must use the source manifest's
measured `frame`. `--window-mode decorated` is a diagnostic mode only: macOS may
shrink a decorated 900px-high window to the available work area, so its frame
must not be compared to a 1440×900 PySide6 golden. `--plain-startup` similarly
captures an unseeded startup route only for diagnosing the Tauri shell.

The Tauri build script recursively watches `clients/nimo-desktop/dist/`. Keep
that watch when changing the build: Vite's hashed files are embedded into the
native binary, and failing to refresh the embedded manifest can leave a native
WebView requesting an obsolete asset and appearing as a blank page.

Use the existing comparator only after both source and native files name the
same fixture, theme, viewport and state. Its non-zero exit means that the
acceptance threshold was not met; it is evidence of a gap, not a reason to
relax the threshold.

```bash
./.venv/bin/python \
tools/ui-parity/compare_screenshots.py \
  /tmp/nimo-ui-parity-settings-creative-temperature/settings--narrative_ember--section-creative-temperature--1440x900.png \
  /tmp/nimo-ui-parity-tauri-settings-creative-temperature/tauri--narrative_ember--settings--section-creative-temperature--1440x900.png \
  --diff-output /tmp/nimo-ui-parity-report/settings-creative-temperature-diff.png \
  --report-output /tmp/nimo-ui-parity-report/settings-creative-temperature.json
```

The first native runner intentionally supports static page, theme, collapsed
rail, populated long-reader and expanded 创作火候 states. Dialog, stream and
running-job states remain separate fixture additions rather than being
simulated with timing-dependent clicks.
