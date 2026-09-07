import { useEffect, useMemo, useState } from "react";

import type {
  DeleteProjectsResult,
  EngineCommandClient,
  JobView,
  PageId,
  TestProjectCleanupPreview,
  WorkspaceView,
} from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";
import { TaskFocusPanel } from "./TaskFocusPanel";
import { EmptyProjectSearch } from "./dashboard/EmptyProjectSearch";
import { JobCard } from "./dashboard/JobCard";
import { MetricsPanel } from "./dashboard/MetricsPanel";
import { orderProjects } from "./dashboard/orderProjects";
import { ProjectCard } from "./dashboard/ProjectCard";
import { ProjectDetailCard } from "./dashboard/ProjectDetailCard";
import { ProviderStatusSection } from "./dashboard/ProviderStatusSection";
import { SectionHeading } from "./dashboard/SectionHeading";
import type { TaskStreamState } from "../lib/task-stream";
import type { ProjectFilter } from "./types";
import { getFilterLabels } from "./types";
import { useLocale } from "../lib/i18n";

function formatCleanupBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DashboardPageProps {
  readonly workspace: WorkspaceView;
  readonly jobs: readonly JobView[];
  readonly commandClient?: EngineCommandClient;
  readonly onNavigate: (page: PageId) => void;
  readonly onReadProject: (projectId: string) => void;
  readonly onWorkspaceRefresh?: (() => Promise<unknown> | void) | undefined;
  readonly onClearJobs?: (jobIds: readonly string[]) => void;
  readonly onTaskDecision?: (jobId: string, decisionId: string) => void;
  readonly onQuickAction?: (action: string, projectId: string) => void;
  /** Restored filter — mirrors dashboard restore_ui_state()["filter"]. */
  readonly initialFilter?: ProjectFilter;
  /** Restored search — mirrors dashboard restore_ui_state()["search"]. */
  readonly initialSearch?: string;
  /** Persisted bookshelf arrangement. This only changes the dashboard view. */
  readonly initialProjectOrder?: readonly string[];
  /** Restored selection — mirrors dashboard restore_ui_state()["selected_project_id"]. */
  readonly initialSelectedProjectId?: string;
  readonly onFilterChange?: (filter: ProjectFilter) => void;
  readonly onSearchChange?: (search: string) => void;
  readonly onProjectOrderChange?: (projectOrder: readonly string[]) => void;
  readonly onSelectedProjectChange?: (projectId: string) => void;
  /** 案头关注面板（mirrors dashboard_page.py TaskFocusPanel GLOBAL, compact）。 */
  readonly focusJob?: JobView | null;
  readonly focusStream?: TaskStreamState | null;
  readonly onExpandFocus?: () => void;
  /** 工坊所在：数据目录标签（settings.storageRootLabel）。 */
  readonly storageRootLabel?: string;
}

export function DashboardPage({
  commandClient,
  jobs,
  onClearJobs,
  onNavigate,
  onQuickAction,
  onReadProject,
  onTaskDecision,
  onWorkspaceRefresh,
  workspace,
  initialFilter = "all",
  initialSearch = "",
  initialProjectOrder = [],
  initialSelectedProjectId = "",
  onFilterChange,
  onSearchChange,
  onProjectOrderChange,
  onSelectedProjectChange,
  focusJob = null,
  focusStream = null,
  onExpandFocus,
  storageRootLabel,
}: DashboardPageProps) {
  const locale = useLocale();
  const filterLabels = getFilterLabels(locale);
  const [filter, setFilterState] = useState<ProjectFilter>(initialFilter);
  const [query, setQueryState] = useState(initialSearch);
  const [projectOrder, setProjectOrder] = useState<readonly string[]>(initialProjectOrder);
  const [batchMode, setBatchMode] = useState(false);
  const [batchDialogOpen, setBatchDialogOpen] = useState(false);
  const [batchDeleteBusy, setBatchDeleteBusy] = useState(false);
  const [batchDeleteMessage, setBatchDeleteMessage] = useState("");
  const [testCleanupPreview, setTestCleanupPreview] = useState<TestProjectCleanupPreview | null>(null);
  const [testCleanupBusy, setTestCleanupBusy] = useState(false);
  const [testCleanupMessage, setTestCleanupMessage] = useState("");
  const [selectedProjectIds, setSelectedProjectIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [selectedProjectId, setSelectedProjectIdState] = useState(
    initialSelectedProjectId || workspace.projects[0]?.id || "",
  );
  const setFilter = (next: ProjectFilter) => {
    setFilterState(next);
    onFilterChange?.(next);
  };
  const setQuery = (next: string) => {
    setQueryState(next);
    onSearchChange?.(next);
  };
  const setSelectedProjectId = (next: string) => {
    setSelectedProjectIdState(next);
    onSelectedProjectChange?.(next);
  };
  const orderedProjects = useMemo(
    () => orderProjects(workspace.projects, projectOrder),
    [workspace.projects, projectOrder],
  );
  const availableProjects = orderedProjects;
  const visibleProjects = useMemo(
    () =>
      availableProjects.filter((project) => {
        const queryMatches = `${project.title} ${project.id} ${project.genre} ${project.tone}`
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase());
        const filterMatches =
          filter === "all" || project.status === filter || project.mode === filter;
        return queryMatches && filterMatches;
      }),
    [availableProjects, filter, query],
  );
  const selectedProject =
    visibleProjects.find((project) => project.id === selectedProjectId) ??
    visibleProjects[0];
  // 卷库统计：单次遍历派生全部摘要，避免 JSX 内联多次 filter（事件/字数多时是热点）。
  const libraryStats = useMemo(() => {
    let long = 0;
    let short = 0;
    let writing = 0;
    let planning = 0;
    let completed = 0;
    for (const project of availableProjects) {
      if (project.mode === "long") long += 1;
      else short += 1;
      if (project.status === "writing") writing += 1;
      else if (project.status === "planning") planning += 1;
      else if (project.status === "completed") completed += 1;
    }
    return { completed, long, planning, short, writing };
  }, [availableProjects]);
  // 选中项被过滤/搜索排除时回退到首个可见项目，并同步选择状态至上层，
  // 使顶栏动作组与页内高亮始终一致（对标 PySide6 dashboard selected_project() 总返回当前实际选中）。
  useEffect(() => {
    if (selectedProject !== undefined && selectedProject.id !== selectedProjectId) {
      setSelectedProjectId(selectedProject.id);
    }
  }, [selectedProject, selectedProjectId]);
  const saveProjectOrder = (nextOrder: readonly string[]) => {
    setProjectOrder(nextOrder);
    onProjectOrderChange?.(nextOrder);
  };
  const deleteProjects = async (projectIds: readonly string[]): Promise<DeleteProjectsResult> => {
    if (commandClient === undefined) {
      throw new Error("当前引擎不支持项目删除命令。");
    }
    const result = await commandClient.deleteProjects({
      kind: "delete_projects",
      projectIds,
    });
    if (result.deletedProjectIds.length > 0) {
      const deletedIds = new Set(result.deletedProjectIds);
      saveProjectOrder(projectOrder.filter((projectId) => !deletedIds.has(projectId)));
      setSelectedProjectIds((current) => new Set(
        [...current].filter((projectId) => !deletedIds.has(projectId)),
      ));
      await onWorkspaceRefresh?.();
    }
    return result;
  };
  const moveProject = (projectId: string, direction: -1 | 1) => {
    const index = availableProjects.findIndex((project) => project.id === projectId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= availableProjects.length) return;
    const nextProjects = [...availableProjects];
    [nextProjects[index], nextProjects[nextIndex]] = [nextProjects[nextIndex]!, nextProjects[index]!];
    saveProjectOrder(nextProjects.map((project) => project.id));
  };
  const toggleBatchSelection = (projectId: string, selected: boolean) => {
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      if (selected) next.add(projectId);
      else next.delete(projectId);
      return next;
    });
  };
  const selectAllVisibleProjects = () =>
    setSelectedProjectIds(new Set(visibleProjects.map((project) => project.id)));
  const moveSelectedProjects = (destination: "start" | "end") => {
    const selected = availableProjects.filter((project) => selectedProjectIds.has(project.id));
    if (selected.length === 0) return;
    const remaining = availableProjects.filter((project) => !selectedProjectIds.has(project.id));
    const nextProjects = destination === "start" ? [...selected, ...remaining] : [...remaining, ...selected];
    saveProjectOrder(nextProjects.map((project) => project.id));
  };
  const removeSelectedProjects = async () => {
    if (batchDeleteBusy) return;
    setBatchDeleteBusy(true);
    setBatchDeleteMessage("");
    try {
      const result = await deleteProjects(
        visibleProjects
          .filter((project) => selectedProjectIds.has(project.id))
          .map((project) => project.id),
      );
      setBatchDeleteMessage(
        [result.message, ...result.failures.map((failure) => failure.message)]
          .filter(Boolean)
          .join(" "),
      );
      if (result.failures.length === 0) {
        setSelectedProjectIds(new Set());
        setBatchDialogOpen(false);
      }
    } catch (error) {
      setBatchDeleteMessage(error instanceof Error ? error.message : "项目删除失败。");
    } finally {
      setBatchDeleteBusy(false);
    }
  };
  const previewTestCleanup = async () => {
    if (commandClient === undefined || testCleanupBusy) return;
    setTestCleanupBusy(true);
    try {
      const preview = await commandClient.previewTestProjectCleanup();
      if (preview.candidates.length === 0) {
        setTestCleanupMessage(preview.message);
        return;
      }
      setTestCleanupMessage("");
      setTestCleanupPreview(preview);
    } catch (error) {
      setTestCleanupMessage(error instanceof Error ? error.message : "无法检查测试残留。");
    } finally {
      setTestCleanupBusy(false);
    }
  };
  const confirmTestCleanup = async () => {
    if (commandClient === undefined || testCleanupPreview === null || testCleanupBusy) return;
    setTestCleanupBusy(true);
    try {
      const result = await commandClient.cleanupTestProjects({
        projectIds: testCleanupPreview.candidates.map((candidate) => candidate.projectId),
      });
      setTestCleanupPreview(null);
      setTestCleanupMessage(result.message);
      await onWorkspaceRefresh?.();
    } catch (error) {
      setTestCleanupMessage(error instanceof Error ? error.message : "测试残留清理失败。");
    } finally {
      setTestCleanupBusy(false);
    }
  };
  const selectedBatchCount = visibleProjects.filter((project) => selectedProjectIds.has(project.id)).length;
  // 可清理任务（非运行中）单次派生，避免渲染期重复 filter。
  const clearableJobIds = useMemo(
    () => jobs.filter((job) => job.state !== "running").map((job) => job.id),
    [jobs],
  );

  // 对标 dashboard_page._render_hero：hero 标题/正文/主按钮随 featured project
  // 切换（list_projects 按 updated_at 倒序，featured 即 workspace.projects[0]）。
  const featuredProject = availableProjects[0];
  const hero =
    featuredProject === undefined
      ? {
          body: "可先往机杼启篇，再回案头候其行止。",
          button: "起笔",
          title: "案头未陈一卷，且先起今日第一笔。",
        }
      : {
          body: `${featuredProject.mode === "long" ? "长篇" : "短篇"} · ${featuredProject.progressLabel} · 最近更新 ${featuredProject.updatedLabel}`,
          button: featuredProject.mode === "long" ? "续主卷" : "赴机杼",
          title:
            featuredProject.mode === "long"
              ? `当前主卷：${featuredProject.title}`
              : `案头当看：${featuredProject.title}`,
        };
  // 对标 _emit_featured_compose：长篇主卷 → 章台续写；否则 → 机杼。
  const handleHeroCompose = () =>
    onNavigate(featuredProject?.mode === "long" ? "chapter_studio" : "workflow");

  return (
    <div className="dashboard-page">
      <section className="dashboard-hero">
        <div className="hero-copy">
          <span className="section-kicker">案头一览</span>
          <h2>{hero.title}</h2>
          <p>{hero.body}</p>
          <div className="button-row">
            <button
              className="button button-primary"
              onClick={handleHeroCompose}
              type="button"
            >
              {hero.button}
            </button>
            <button
              className="button button-secondary"
              onClick={() => onNavigate("workflow")}
              type="button"
            >
              去机杼
            </button>
          </div>
          <label className="search-field dashboard-hero-search">
            <span className="sr-only">检索卷帙</span>
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="检卷名、项目 ID、题材或气口"
              value={query}
            />
          </label>
        </div>
        <MetricsPanel {...(storageRootLabel !== undefined ? { storageRootLabel } : {})} workspace={workspace} />
      </section>

      {/* 案头关注：全局活跃任务的面板（mirrors dashboard_page.py TaskFocusPanel GLOBAL, compact）。 */}
      <TaskFocusPanel
        compact
        job={focusJob}
        {...(onExpandFocus !== undefined ? { onExpand: onExpandFocus } : {})}
        scope="global"
        stream={focusStream}
        title="案头关注"
      />

      <SectionHeading
        description="按题材、进度与卷势查检卷帙，只理在库诸卷。"
        title="卷库总览"
      />
      <section className="filter-surface filter-surface-compact">
          <div className="filter-top-row filter-summary-row">
            <div className="library-summary">
            <span>
              在库 {availableProjects.length} 卷 · 长篇{" "}
              {libraryStats.long}{" "}
              · 短篇{" "}
              {
                libraryStats.short
              }
            </span>
            <small>
              今筛得 {visibleProjects.length} 卷 · 连载{" "}
              {
                libraryStats.writing
              }{" "}
              · 筹备{" "}
              {
                libraryStats.planning
              }{" "}
              · 完稿{" "}
              {
                libraryStats.completed
              }
              </small>
            </div>
            <button
              aria-pressed={batchMode}
              className={batchMode ? "button button-secondary is-active" : "button button-secondary"}
              onClick={() => {
                setBatchMode((active) => !active);
                setSelectedProjectIds(new Set());
              }}
              type="button"
            >
              {batchMode ? "完成管理" : "批量管理"}
            </button>
            <button
              className="button button-secondary button-quiet"
              disabled={commandClient === undefined || testCleanupBusy}
              onClick={() => void previewTestCleanup()}
              title="检查并确认删除测试项目与没有项目内容的自动运行残留。"
              type="button"
            >
              {testCleanupBusy ? "检查中…" : "清理测试残留"}
            </button>
          </div>
          {testCleanupMessage.length > 0 && (
            <p aria-live="polite" className="dashboard-cleanup-notice">{testCleanupMessage}</p>
          )}
        <div className="filter-row">
          {(Object.keys(filterLabels) as ProjectFilter[]).map((key) => (
            <button
              aria-pressed={key === filter}
              className={
                key === filter ? "filter-chip is-active" : "filter-chip"
              }
              key={key}
              onClick={() => setFilter(key)}
              type="button"
            >
              {filterLabels[key]}
            </button>
          ))}
        </div>
        {batchMode && (
          <div aria-label="卷册批量管理" className="batch-management-toolbar" role="region">
            <strong>已选 {selectedBatchCount} 卷</strong>
            <button className="button button-secondary button-quiet" disabled={visibleProjects.length === 0} onClick={selectAllVisibleProjects} type="button">全选筛得</button>
            <button className="button button-secondary button-quiet" disabled={selectedBatchCount === 0} onClick={() => setSelectedProjectIds(new Set())} type="button">清空选择</button>
            <button className="button button-secondary button-quiet" disabled={selectedBatchCount === 0} onClick={() => moveSelectedProjects("start")} type="button">置于最前</button>
            <button className="button button-secondary button-quiet" disabled={selectedBatchCount === 0} onClick={() => moveSelectedProjects("end")} type="button">置于最后</button>
            <button className="button button-danger button-quiet" disabled={selectedBatchCount === 0} onClick={() => { setBatchDeleteMessage(""); setBatchDialogOpen(true); }} type="button">永久删除</button>
          </div>
        )}
      </section>

      {selectedProject !== undefined && (
        <ProjectDetailCard
          canMoveEarlier={availableProjects.findIndex((project) => project.id === selectedProject.id) > 0}
          canMoveLater={availableProjects.findIndex((project) => project.id === selectedProject.id) < availableProjects.length - 1}
          commandClient={commandClient}
          onDelete={(projectId) => deleteProjects([projectId])}
          onMoveEarlier={() => moveProject(selectedProject.id, -1)}
          onMoveLater={() => moveProject(selectedProject.id, 1)}
          onNavigate={onNavigate}
          onReadProject={onReadProject}
          onWorkspaceRefresh={onWorkspaceRefresh}
          project={selectedProject}
        />
      )}
      <SectionHeading description="筛选一动，下面诸卷随之更迭。" title="在库卷册" />
      {visibleProjects.length > 0 ? (
        <div className="project-grid">
          {visibleProjects.map((project) => (
            <ProjectCard
              isSelected={project.id === selectedProject?.id}
              batchMode={batchMode}
              isBatchSelected={selectedProjectIds.has(project.id)}
              key={project.id}
              onBatchSelect={(selected) => toggleBatchSelection(project.id, selected)}
              onQuickAction={onQuickAction}
              onSelect={() => setSelectedProjectId(project.id)}
              project={project}
            />
          ))}
        </div>
      ) : (
        <EmptyProjectSearch query={query} />
      )}
      {batchDialogOpen && (
        <AppDialog
          closeOnConfirm={false}
          confirmDisabled={batchDeleteBusy}
          confirmLabel={batchDeleteBusy ? "删除中…" : `永久删除 ${selectedBatchCount} 卷`}
          description={`这会永久删除选中的 ${selectedBatchCount} 个项目目录及其中的正文、大纲、报告、配音与日志，无法撤销。正在运行或排队的项目会被拒绝。`}
          onClose={() => { if (!batchDeleteBusy) setBatchDialogOpen(false); }}
          onConfirm={() => void removeSelectedProjects()}
          title={`永久删除 ${selectedBatchCount} 卷？`}
          tone="danger"
        >
          <p className="narrative-dialog-note">请仅在确定不再需要这些项目时继续。</p>
          {batchDeleteMessage.length > 0 && <p aria-live="polite" className="narrative-dialog-note">{batchDeleteMessage}</p>}
        </AppDialog>
      )}
      {testCleanupPreview !== null && (
        <AppDialog
          closeOnConfirm={false}
          confirmDisabled={testCleanupBusy}
          confirmLabel={testCleanupBusy ? "删除中…" : `删除 ${testCleanupPreview.candidates.length} 项`}
          description="仅删除明确命名的测试目录，以及没有规格、正文、大纲或检查点的自动运行残留。正在运行或排队的项目会被跳过。"
          onClose={() => setTestCleanupPreview(null)}
          onConfirm={() => void confirmTestCleanup()}
          title={`清理 ${testCleanupPreview.candidates.length} 个测试残留？`}
          tone="danger"
        >
          <ul className="dashboard-cleanup-list">
            {testCleanupPreview.candidates.map((candidate) => (
              <li key={candidate.projectId}>
                <code>{candidate.projectId}</code>
                <span>{candidate.reason === "generated_test_name" ? "测试命名" : "空白执行残留"}</span>
                <small>{candidate.fileCount} 个文件 · {formatCleanupBytes(candidate.sizeBytes)}</small>
              </li>
            ))}
          </ul>
        </AppDialog>
      )}

      {/* 案头近况：工坊所在 + 通路点检（mirrors dashboard_page.py workspace_panel + 通路点检）。 */}
      <SectionHeading
        description="工坊所在与各供应商、模型就绪状态。"
        title="案头近况"
      />
      <section className="workspace-status-panel">
        <h3>工坊所在</h3>
        <p>此处记路径、默认通路与卷库规模。</p>
        <div className="workspace-status-row">
          <span>
            工坊路径：
            <strong>{storageRootLabel ?? "尚未照见"}</strong>
          </span>
          <span>
            默认通路：
            <strong>{workspace.defaultProvider}</strong>
          </span>
          <span>
            在库卷册：
            <strong>{workspace.metrics.totalProjects} 卷</strong>
          </span>
          <span>
            累计字数：
            <strong>{workspace.metrics.totalWords.toLocaleString("zh-CN")} 字</strong>
          </span>
        </div>
      </section>

      {/* 通路点检：供应商分组 + 模型状态卡片 */}
      {workspace.providerGroups !== undefined && workspace.providerGroups.length > 0 && (
        <>
          <SectionHeading
            description="绿灯已载入、黄灯密钥就绪、红灯未配置。"
            title="通路点检"
          />
          <ProviderStatusSection groups={workspace.providerGroups} />
        </>
      )}

      <SectionHeading description="全部任务实时进度，滚动查看更多。" title="后台任务" />
      <section className="jobs-surface">
        <div className="jobs-toolbar">
          <button
            className="button button-secondary button-quiet"
            disabled={clearableJobIds.length === 0}
            onClick={() => onClearJobs?.(clearableJobIds)}
            title={"清空案头任务列表里的历史条目（已完成 / 失败 / 待决策）。\n运行中任务不会被中断。"}
            type="button"
          >
            🧹 清理
          </button>
        </div>
        {jobs.map((job) => (
          <JobCard job={job} key={job.id} onTaskDecision={onTaskDecision} />
        ))}
      </section>
    </div>
  );
}
