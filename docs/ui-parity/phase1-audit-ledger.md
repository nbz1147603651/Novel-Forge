# Phase 1 UI Parity Audit Ledger

Migration note (2026-08-31): this is retained as historical convergence
evidence. NIMO is now primary and PySide is frozen behind `nimo-p`; new product
features no longer require a parallel PySide implementation.

This ledger is the acceptance boundary for the parallel PySide6 and React
interfaces. A page is not “1:1 complete” because its first loaded frame looks
similar: every named state below needs the same fixture, viewport, theme,
interaction path and screenshot comparison.

## Shared compact-density contract

The React shell has one type and control hierarchy, defined in
`clients/nimo-desktop/src/styles/design-tokens.css`:

| Level | Token | Intended use |
| --- | --- | --- |
| micro | `--nf-font-micro` | stable supporting metadata only |
| meta | `--nf-font-meta` | chips, status, secondary labels |
| UI | `--nf-font-ui` | compact controls and table cells |
| body | `--nf-font-body` | ordinary interface copy |
| section | `--nf-font-section` | local section headings and primary reader tabs |
| page/display | `--nf-font-page`, `--nf-font-display` | page title and a single high-emphasis title only |

Rules that must hold on every primary surface:

1. Parent navigation is visually stronger than child navigation. In particular,
   卷帙's outer category tabs are larger and heavier than its nested tabs.
2. A normal button uses the 30px control tier; only dense toolbars may use the
   27px tier. Large controls need a source-measured exception.
3. Side-rail navigation uses a centred, bounded track. The rail keeps its
   stable shell width, while its active label does not become a full-width card.
4. Main canvas padding and top-bar height are shared chrome values, not
   page-local guesses. Page prose and dense data views can reserve the released
   space for their primary content.

## Primary surfaces

All source frames below were regenerated with the test-only PySide6 fixture on
2026-07-22 at `1440×900`, `narrative_ember`:
`/tmp/nimo-ui-parity-primary-audit/manifest.json`.

| Surface | Source state inspected | React state inspected | Current conclusion | Required next acceptance slice |
| --- | --- | --- | --- | --- |
| 案头 | loaded dashboard | populated dashboard fixture | Shared density and search-in-hero are in place; source and React project counts intentionally differ. | Bind an equivalent dashboard selection fixture and compare search, filter, project-card and empty states. |
| 卷帙 | empty reader + populated reader + character relationship table + character graph, node/edge hover and editable graph dialog | empty reader, populated reader, source-shaped relationship table, graph, window-bounded hover popup and editable local session | 角色与实体的三枚内层标签 use the source tab contract: the relationship tab keeps a role roster, filters rows by the selected source character and exposes the source table/detail/action flow. The graph now reuses `CharacterGraphWidget`'s responsive core/orbit geometry, role colours, hit-state hierarchy and active-edge moving point; node, edge and blank-canvas focus transitions are regression-tested. The source tooltip's long `612×338` character and `248×110` relationship states now use a canvas-external portal with source-style flip/clamp semantics. Editable parity now includes node/edge/canvas context menus, node-ring drag-to-target, source-compatible relationship upsert/remove semantics, and the captured `520×417` relationship editor with the canonical ten type options. Outer/inner tab hierarchy is regression-tested. | Capture the matching graph and editable dialog in native Tauri, then compare populated/focused/hovered/edit states strictly; complete the remaining persistent reader panels with equivalent data. |
| 机杼 | loaded and running | populated short-workflow composer | Composer hierarchy is compact, but loaded source and React fixtures are not yet equivalent. | Capture and compare ready/running/error/history/export states with one shared fixture; verify cancel and resume semantics. |
| 火候 | loaded, connection matrix, routing section | configured settings mock | Compact token ramp applies; routing remains a separate dynamic matrix. | Compare idle/checking/success/failure cards and each expanded routing group, including save/error feedback. |
| 章台 | loaded, prepared/running/checkpoint/checkpoint-dialog | populated prepared/running/checkpoint views and right-side checkpoint tool | PySide6 state capture is now stable for the three content states and the independent `Qt.Tool` suggestion panel; React uses the same compact, non-blurring right-side tool rather than a centred modal. | Compare each named state with one shared fixture; validate every toolbar action, checkpoint resolution, drag/close behaviour and floating-panel bounds. |
| 声腔 | loaded, configured team and configured platform-settings fixture | configured team and restored `voice_tab=settings` mock | 标签恢复、团队、平台设置分类折叠、草稿保存边界与本地运行时检查反馈已有可重复的前端验证；平台设置宽度现在与源端工作列对齐。它仍是 mock 会话，尚不能声明真实持久化或全页 1:1。 | 使用同一 `VoiceStudioView` 捕获团队、平台设置、克隆、脚本、配音室、后处理及导出每一状态；核对 provider 切换、凭据保护、异步 worker 的 loading/error/cancel 与 Tauri 像素报告。 |

## Cross-cutting acceptance gates

- Test the same interaction state in both UIs; a mock-only visual frame is not
  evidence of functional parity.
- Include keyboard focus, disabled/loading/error states, unsaved-change guards,
  streamed/task-progress views and reduced-motion behaviour.
- Keep PySide6 fixtures as frozen behavior/visual regression evidence while the fallback exists.
  React/Tauri now uses the negotiated HTTP Engine for supported read and
  command paths, and `nimo` owns a managed loopback backend or connects to an
  explicit HTTPS Engine. Unsupported mutations remain capability-gated and
  must not be simulated as durable writes.
- Native Tauri capture remains a required final visual gate. The macOS host is
  currently denied Screen Recording permission, so browser screenshots are
  diagnostic evidence only and cannot mark a surface accepted.
