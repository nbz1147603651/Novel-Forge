import type { EngineClient, EngineCommandClient, ProjectView, WorkflowRunView } from "@nimo/engine-contracts";

import { LongInitFormPanel } from "./LongInitFormPanel";
import { ShortWorkflowComposer } from "./ShortWorkflowComposer";

export type WorkflowMode = "short" | "long";

interface WorkflowComposerProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly initRunning: boolean;
  readonly mode: WorkflowMode;
  readonly onModeChange: (mode: WorkflowMode) => void;
  /** Switch to the long-init form and bring its resume action into view. */
  readonly onFocusLongInit: () => void;
  readonly onNavigate: (page: "projects" | "chapter_studio") => void;
  readonly projects: readonly ProjectView[];
  readonly resumable: boolean;
  readonly shortTemplateExportRequest?: number;
  readonly activeInitRun?: WorkflowRunView | null;
  readonly activeShortRun?: WorkflowRunView | null;
  readonly hasFailedInit?: boolean;
  readonly onCancelInit?: (runId: string) => void;
  readonly onRestartInit?: (() => Promise<void> | void) | undefined;
  readonly onWorkflowStarted?: (() => Promise<void> | void) | undefined;
}

/**
 * Return the project whose interrupted long-form initialization can continue.
 *
 * ``initResumeAvailable`` is the engine/PySide6 contract. The presentational
 * ``status`` is intentionally not used: a project can still need initialization
 * work even when its summary status has not settled on ``planning`` yet.
 */
export function resumableLongProject(projects: readonly ProjectView[]): ProjectView | null {
  return projects.find((project) => project.mode === "long" && project.initResumeAvailable) ?? null;
}

/**
 * Source-shaped workflow entry surface. It owns only UI session choices;
 * model calls and persistence remain behind EngineClient in the next phase.
 */
export function WorkflowComposer({ commandClient, engineClient, initRunning, mode, onModeChange, onFocusLongInit, onNavigate, projects, resumable, shortTemplateExportRequest = 0, activeInitRun = null, activeShortRun = null, hasFailedInit = false, onCancelInit, onRestartInit, onWorkflowStarted }: WorkflowComposerProps) {
  const resumeProject = resumableLongProject(projects);
  const chapterProject = projects.find(
    (project) => project.mode === "long" && !project.initResumeAvailable,
  ) ?? null;
  // 优先把“待续立项”放在在制项目首位；否则才展示正在连载的项目。
  const activeProject = resumeProject ?? projects.find((project) => project.status === "writing") ?? projects[0] ?? null;
  const chapterEntryAvailable = mode === "long" && chapterProject !== null;

  return (
    <>
      <section className="workflow-composer-hero">
        <div>
          <span className="section-kicker">{"机杼 · 创作工场"}</span>
          <h2>{"先选路线，再开写。"}</h2>
          <p>{"短篇：一句引子到成稿、润色与评估；长篇：先立项，再去章台逐章续写、查报告、交由 AI 决策。"}</p>
        </div>
        <div className="workflow-composer-hero-actions">
          <button className="button button-secondary" disabled={mode !== "long" || (resumeProject === null && chapterProject === null)} onClick={() => onNavigate("projects")} type="button">{"阅卷"}</button>
          {resumeProject !== null ? (
            <button className="button button-primary" onClick={onFocusLongInit} type="button">{"继续立项 →"}</button>
          ) : (
            <button className="button button-secondary" disabled={!chapterEntryAvailable} onClick={() => onNavigate("chapter_studio")} type="button">{"去章台 →"}</button>
          )}
        </div>
      </section>

      <section className="workflow-active-projects">
        <header><h2>{"在制项目"}</h2><p>{"未完成的项目列于此，点击可快速进入对应表单。完成项目展示于卷帙。"}</p></header>
        {activeProject === null ? <p className="workflow-project-empty">{"暂无进行中项目——可于下方选择模式新建。"}</p> : <article className="workflow-active-project">
          <div><strong>{activeProject.title}</strong><span>{activeProject.mode === "long" ? "长篇" : "短篇"}</span><b>{activeProject.statusLabel}</b><small>{activeProject.progressLabel}</small></div>
          <button
            className={activeProject.initResumeAvailable ? "button button-primary" : "button button-secondary"}
            onClick={() => {
              if (activeProject.initResumeAvailable) {
                onFocusLongInit();
                return;
              }
              onNavigate(activeProject.mode === "long" ? "chapter_studio" : "projects");
            }}
            type="button"
          >
            {activeProject.initResumeAvailable ? "继续立项 →" : `${activeProject.nextAction} →`}
          </button>
        </article>}
      </section>

      <div aria-label="创作模式" className="workflow-mode-switcher" role="tablist">
        <button aria-selected={mode === "short"} className={mode === "short" ? "is-active" : ""} onClick={() => onModeChange("short")} role="tab" type="button"><strong>{"短篇创作"}</strong><span>{"一次提交 · 自动生成完整短篇"}</span></button>
        <button aria-selected={mode === "long"} className={mode === "long" ? "is-active" : ""} onClick={() => onModeChange("long")} role="tab" type="button"><strong>{"长篇初始化"}</strong><span>{"分章推进 · 立项后去章台逐章续写"}</span></button>
      </div>

      {mode === "short" ? (
        <ShortWorkflowComposer activeRun={activeShortRun} commandClient={commandClient} engineClient={engineClient} externalTemplateExportRequest={shortTemplateExportRequest} onWorkflowStarted={onWorkflowStarted} />
      ) : (
        <LongInitFormPanel activeRun={activeInitRun} commandClient={commandClient} engineClient={engineClient} hasFailedInit={hasFailedInit} initRunning={initRunning} onCancelInit={onCancelInit} onRestartInit={onRestartInit} onWorkflowStarted={onWorkflowStarted} resumable={resumable} resumableProject={resumeProject} />
      )}
    </>
  );
}
