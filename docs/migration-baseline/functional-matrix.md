# Functional Matrix (PySide6 v2.3 baseline)

> Per spec §4 M0 deliverable #2. Generated from exploration of `novel_forge/desktop/pages/`.
> Locked at v2.3-baseline; M1+ cannot add rows without amending the matrix.

| 页面 | 功能 | 数据源 | 恢复/取消/重试/决定
|---|---|---|---|
| Dashboard 案头 | 项目列表 + metrics + quick-nav + job feed | DesktopWorkspaceSnapshot + JobFeed | Y (cancel/retry/clear-task-flow); N (decision-required) |
| Dashboard ProjectCard | 9 个 signal (compose/view/open/blueprint/graph/profile/.../delete) | ProjectDetail + ProjectFileService | Y (delete confirm + clear task flow) |
| Projects 卷帙 | Combo + 4 tabs (Token/Final/Outline/Character) | 各 page 调 ProjectLayout 读 json | Y (QFileSystemWatcher + autosave) |
| Workflow 机杼 | 短篇 + 长篇表单 + jobs panel | WORKFLOW_JOB_KINDS API submission | Y (cancel/retry/checkpoint/init-repair-retry) |
| Settings 火候 | 13 个参数 tab + 模型管理 + Ollama | Settings + ProfilesConfig + 后端任务路由 | Y (save 后 reload; import/export) |
| Chapter Studio 章台 | 22 个 outbound signal + checkpoint/decision dialog | ChapterWorkspaceSnapshot + JobService stream | Y (decision-required + checkpoint resolve) |
| Reader (document_renderer) | Markdown + 14 个 report 视图 | QTextBrowser + 增量 HTML | Y (per report render cache) |
| CharacterProfile | single character CRUD | character_bible.json + dialogs | N (read-only during long init) |
| CharacterBibleEditor | 全角色编辑器 + RelationshipEditDialog | character_bible.json + atomic write | Y (save with conflict resolution) |
| RelationshipNetwork | d3-style QGraphicsView graph | relationships.json + snapshot | N |
| OutlineEditor / SyncPresenter | interactive outline + sync | outline.json + SyncChapterContractsRequest + ExtendOutlineRequest | Y (resolve conflicts) |
| SubplotManager | subplot CRUD + arc conversion | plans/subplot_execution_matrix.json | Y (subplot worker) |
| FinalRevision | chapter-level revision worker | SelectionRevisionWorker + DirectionSuggestionWorker | Y (invalidation dialog) |
| HumanizeLibraryDashboard | QAbstractTableModel of humanize entries | humanize_library.json | Y (merge / dedup / hit examples) |
| MemoryPanel | UnifiedMemoryPanel 10 components | memory/*.json + episodic_index | Y (memory rebuild) |
| TokenAnalytics | TokenAnalyticsTab | model_profiles.json + run logs | N |
| InitManualRepairDialog | init failure repair | init_request_meta.json | Y (init_repair_retry) |
| AddToHumanizeDialog | add to humanize library | humanize_library.json | N |
| InitArtifactCatalog | init artifact enumeration | source_artifacts/*.json | N |
| CharacterArtifactWriter | artifact write helper | character_bible.json | Y (atomic) |
| RendererHTML | HTML document renderer | various json | N |

**总计**: 21 页面 / 功能组合 — 与 §2 现状快照 54 页面模块 + 14 个 renderer + 19 个 standalone 对应。