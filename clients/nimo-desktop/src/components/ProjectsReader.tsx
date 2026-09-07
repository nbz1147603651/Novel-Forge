import { useCallback, useEffect, useMemo, useState } from "react";

import type { NarrativeToolsAction, NarrativeToolTab } from "./NarrativeToolsWorkbench";
import type { AuthoringContext, EngineClient, EngineCommandClient, NarrativeToolsView, ProjectReaderArtifactView, ProjectReaderChapterView, ProjectReaderTabView, ProjectReaderView, ProjectView, RelationshipOverviewView } from "@nimo/engine-contracts";
import { AuthoringWorkspace, useAuthoring } from "./AuthoringWorkspace";

import { DocTag } from "./document/DocumentPrimitives";
import { DropdownSelect } from "./DropdownSelect";
import { NarrativeToolsWorkbench } from "./NarrativeToolsWorkbench";
import { SourceMessageDialog } from "./SourceMessageDialog";
import { ChapterProseRevision, ChapterReportBrowser, GovernanceBrowser, OutlineSessionWorkbench, ReaderArtifactDocument, TokenAnalyticsWorkbench } from "./ProjectReaderWorkbenches";
import { readerUnsavedSessionOrder, type ReaderUnsavedSession, type ReaderUnsavedSessionChange, type ReaderUnsavedSessionId } from "../lib/reader-unsaved-session";
import { LONG_READER_SECTIONS, type ReaderSectionKind } from "../lib/reader-sections";
import { useVirtualList } from "../lib/use-virtual-list";

/* Mirror PySide6 ChapterRail._VIRTUAL_THRESHOLD: only virtualize long rails. */
const CHAPTER_LIST_VIRTUAL_THRESHOLD = 50;
/* .reader-chapter-list button min-height 31px + list gap 3px. */
const CHAPTER_LIST_ROW_HEIGHT = 34;

interface ReaderSubtab {
  readonly id: string;
  readonly label: string;
  readonly emptyLabel: string;
  readonly artifact?: ProjectReaderArtifactView;
}

interface ReaderSection {
  readonly id: string;
  readonly label: string;
  readonly kind: ReaderSectionKind;
  readonly chapters?: readonly ProjectReaderChapterView[];
  readonly subTabs: readonly ReaderSubtab[];
}

interface ReaderSectionNavigation {
  readonly activeSubtabId: string;
  readonly documentOpened: boolean;
}

type PersistentReaderPanelId = "chapters" | "outline" | "foundation-characters";

const emptyReaderSections: readonly ReaderSection[] = [];

/* Matches ProjectsPage._LONG_GLOBAL_GROUPS + _LONG_STANDALONE_TABS, with the
 * project-scoped support workspace added beside its parent narrative blueprint.
 * Document payloads remain entirely owned by ProjectReaderView from EngineClient. */
const longReaderSections: readonly Pick<ReaderSection, "id" | "kind" | "label">[] = LONG_READER_SECTIONS;

const foundationFallbackTabs: readonly Omit<ReaderSubtab, "artifact">[] = [
  { id: "spec", label: "故事规格", emptyLabel: "故事规格" },
  { id: "world", label: "世界观", emptyLabel: "世界观" },
  { id: "characters", label: "角色与实体", emptyLabel: "角色与关系" },
  { id: "elements", label: "要素与风格", emptyLabel: "要素与风格" },
];

function findTab(tabs: readonly ProjectReaderTabView[], id: string): ProjectReaderTabView | undefined {
  return tabs.find((tab) => tab.id === id);
}

function readerSubtab(
  id: string,
  label: string,
  artifact: ProjectReaderArtifactView | undefined,
  emptyLabel = label,
): ReaderSubtab {
  return artifact === undefined ? { id, label, emptyLabel } : { id, label, emptyLabel, artifact };
}

function readerSubtabs(tab: ProjectReaderTabView | undefined): readonly ReaderSubtab[] {
  return (tab?.artifacts ?? []).map((artifact) => readerSubtab(artifact.id, artifact.label, artifact));
}

function sourceShapedSections(reader: ProjectReaderView): readonly ReaderSection[] {
  if (reader.modeLabel !== "长篇项目") {
    return reader.tabs.map((tab) => ({ id: tab.id, label: tab.label, kind: "nested", subTabs: readerSubtabs(tab) }));
  }

  return longReaderSections.map((sourceSection) => {
    const tab = findTab(reader.tabs, sourceSection.id);
    if (sourceSection.kind === "subplots") {
      // This is a project-scoped workbench, not a backend document catalog leaf.
      // Give it a virtual leaf so the reader opens it just like source tabs.
      return { ...sourceSection, subTabs: [readerSubtab("subplot-workbench", "支线管理", undefined)] };
    }
    if (sourceSection.id === "foundation") {
      const artifacts = tab?.artifacts ?? [];
      return {
        ...sourceSection,
        subTabs: foundationFallbackTabs.map((fallback) => readerSubtab(
          fallback.id,
          fallback.label,
          artifacts.find((artifact) => artifact.id === fallback.id),
          fallback.emptyLabel,
        )),
      };
    }
    if (sourceSection.kind === "direct") {
      const firstArtifact = tab?.artifacts[0];
      return { ...sourceSection, subTabs: firstArtifact === undefined ? [] : [readerSubtab(firstArtifact.id, firstArtifact.label, firstArtifact)] };
    }
    if (sourceSection.kind === "chapters") {
      return {
        ...sourceSection,
        chapters: tab?.chapters ?? [],
        subTabs: readerSubtabs(tab),
      };
    }
    if (sourceSection.kind === "tracking") {
      const artifacts = tab?.artifacts ?? [];
      return {
        ...sourceSection,
        subTabs: [
          readerSubtab("relationship", "关系追踪", artifacts.find((artifact) => artifact.id === "relationship"), "关系追踪"),
          readerSubtab("token-analytics", "Token 追踪", artifacts.find((artifact) => artifact.id === "token-analytics"), "Token 追踪"),
        ],
      };
    }
    return { ...sourceSection, subTabs: readerSubtabs(tab) };
  });
}

function sourceShapedNavigation(sections: readonly ReaderSection[]): ReadonlyMap<string, ReaderSectionNavigation> {
  return new Map(sections.map((section) => [section.id, {
    activeSubtabId: section.subTabs[0]?.id ?? "",
    // QTabWidget renders its first child immediately.  Opening nested groups
    // with an empty panel dropped the source reader's default document view.
    documentOpened: section.subTabs.length > 0,
  }]));
}

function sectionEmptyLabel(section: ReaderSection | undefined, subtab: ReaderSubtab | undefined): string {
  return subtab?.emptyLabel ?? section?.label ?? "文档";
}

function persistentReaderPanelId(
  activeSection: ReaderSection | undefined,
  activeSubtab: ReaderSubtab | undefined,
  documentOpened: boolean,
): PersistentReaderPanelId | undefined {
  if (!documentOpened || activeSection === undefined) return undefined;
  if (activeSection.kind === "chapters") return "chapters";
  if (activeSection.id === "outline") return "outline";
  if (activeSection.id === "foundation" && activeSubtab?.id === "characters") return "foundation-characters";
  return undefined;
}

export interface ProjectsReaderProps {
  readonly engineClient?: EngineClient;
  readonly narrativeToolsError?: string | null;
  readonly commandClient: EngineCommandClient;
  readonly isLoading: boolean;
  readonly onRefreshNarrativeTools?: (() => void) | undefined;
  readonly onSelectProject: (projectId: string) => void;
  readonly projects: readonly ProjectView[];
  readonly reader: ProjectReaderView | null;
  readonly tools: NarrativeToolsView | null;
}

/**
 * Source-faithful project reader: its hierarchy is derived from the PySide
 * document catalog, but all document values are read-only EngineClient views.
 * It deliberately owns only selection/search/local preview state, never paths
 * or persistence.
 */
export function ProjectsReader(props: ProjectsReaderProps) {
  return <AuthoringWorkspace commandClient={props.commandClient} {...(props.engineClient ? { engineClient: props.engineClient } : {})} projectId={props.reader?.projectId ?? ""} enabled={props.reader?.modeLabel === "长篇项目"}><ProjectsReaderContent {...props} /></AuthoringWorkspace>;
}

function ProjectsReaderContent({ commandClient, isLoading, narrativeToolsError, onRefreshNarrativeTools, onSelectProject, projects, reader, tools }: ProjectsReaderProps) {
  const setAuthoringLocation = useAuthoring()?.setLocation;
  const sections = reader === null ? emptyReaderSections : sourceShapedSections(reader);
  const [activeSectionId, setActiveSectionId] = useState("foundation");
  const [sectionNavigation, setSectionNavigation] = useState<ReadonlyMap<string, ReaderSectionNavigation>>(() => new Map());
  const [mountedPersistentPanels, setMountedPersistentPanels] = useState<ReadonlySet<PersistentReaderPanelId>>(() => new Set());
  const [persistentPanelsProjectId, setPersistentPanelsProjectId] = useState("");
  const [operationNotice, setOperationNotice] = useState("");
  const [pendingProjectId, setPendingProjectId] = useState<string | null>(null);
  const [unsavedSessions, setUnsavedSessions] = useState<ReadonlyMap<ReaderUnsavedSessionId, ReaderUnsavedSession>>(() => new Map());
  const [displayedTools, setDisplayedTools] = useState(tools);
  // A live projection must not rebase an open editor onto a newer revision.
  // Apply the latest snapshot only after the user saves or discards the draft.
  if (unsavedSessions.size === 0 && displayedTools !== tools) setDisplayedTools(tools);
  const activeSection = sections.find((section) => section.id === activeSectionId) ?? sections[0];
  const activeNavigation = activeSection === undefined ? undefined : sectionNavigation.get(activeSection.id);
  const activeSubtabId = activeNavigation?.activeSubtabId ?? activeSection?.subTabs[0]?.id ?? "";
  const activeSubtab = activeSection?.subTabs.find((subtab) => subtab.id === activeSubtabId) ?? activeSection?.subTabs[0];
  const documentOpened = activeNavigation?.documentOpened === true || activeSubtab?.artifact !== undefined;
  const activePersistentPanelId = persistentReaderPanelId(activeSection, activeSubtab, documentOpened);
  useEffect(() => {
    if (activeSection?.kind === "chapters") return;
    const context: AuthoringContext = activeSection?.id === "foundation"
      ? (["spec", "world", "characters"].includes(activeSubtabId) ? activeSubtabId as AuthoringContext : "spec")
      : activeSection?.id === "outline" ? "outline" : activeSection?.kind === "governance" ? "reports" : "blueprint";
    setAuthoringLocation?.(context);
  }, [activeSection?.id, activeSection?.kind, activeSubtabId, setAuthoringLocation]);
  const activeUnsavedSession = useMemo(
    () => readerUnsavedSessionOrder.map((id) => unsavedSessions.get(id)).find((session): session is ReaderUnsavedSession => session !== undefined),
    [unsavedSessions],
  );
  // Governance owns its source three-level document hierarchy internally
  // (全书审修 → audit/repair, etc.).  Rendering a generic middle row here
  // would duplicate its native group tabs and split their selection state.
  const hasInnerTabs = activeSection !== undefined && (activeSection.kind === "nested" || activeSection.kind === "tracking") && activeSection.subTabs.length > 0;

  useEffect(() => {
    setActiveSectionId(sections[0]?.id ?? "foundation");
    setSectionNavigation(sourceShapedNavigation(sections));
    setMountedPersistentPanels(new Set());
    setPersistentPanelsProjectId(reader?.projectId ?? "");
    setOperationNotice("");
    setPendingProjectId(null);
    setUnsavedSessions(new Map());
  }, [reader?.projectId]);

  useEffect(() => {
    if (activePersistentPanelId === undefined) return;
    setMountedPersistentPanels((current) => current.has(activePersistentPanelId)
      ? current
      : new Set([...current, activePersistentPanelId]));
  }, [activePersistentPanelId]);

  const onUnsavedSessionChange = useCallback<ReaderUnsavedSessionChange>((id, session) => {
    setUnsavedSessions((current) => {
      const next = new Map(current);
      if (session === null) {
        if (next.size === 0) return current;
        next.delete(id);
      } else {
        next.set(id, session);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (pendingProjectId === null || activeUnsavedSession !== undefined) return;
    setPendingProjectId(null);
    onSelectProject(pendingProjectId);
  }, [activeUnsavedSession, onSelectProject, pendingProjectId]);

  const requestProjectSwitch = useCallback((projectId: string) => {
    if (projectId.length === 0 || projectId === reader?.projectId) return;
    if (activeUnsavedSession === undefined) {
      onSelectProject(projectId);
      return;
    }
    setPendingProjectId(projectId);
  }, [activeUnsavedSession, onSelectProject, reader?.projectId]);

  const resolveUnsavedSession = useCallback((actionId: string) => {
    if (activeUnsavedSession === undefined) return;
    if (actionId === "save") {
      activeUnsavedSession.onSave();
      return;
    }
    if (actionId === "discard") {
      activeUnsavedSession.onDiscard();
      return;
    }
    setPendingProjectId(null);
  }, [activeUnsavedSession]);

  const selectSection = (section: ReaderSection) => {
    setActiveSectionId(section.id);
    setOperationNotice("");
  };

  const selectSubtab = (subtab: ReaderSubtab) => {
    if (activeSection === undefined) return;
    setSectionNavigation((current) => {
      const next = new Map(current);
      next.set(activeSection.id, { activeSubtabId: subtab.id, documentOpened: true });
      return next;
    });
    setOperationNotice("");
  };

  return <div className="reader-page">
    {narrativeToolsError && <div className="reader-sync-error" role="alert">角色与叙事数据同步失败，已保留最近一次内容。{narrativeToolsError}<button className="button button-secondary" onClick={onRefreshNarrativeTools} type="button">重新同步</button></div>}
    <header className="reader-header">
      <DropdownSelect
        ariaLabel="选择阅卷项目"
        className="reader-project-select"
        disabled={projects.length === 0}
        onChange={requestProjectSwitch}
        options={[
          { value: "", label: "请选择项目…", disabled: true },
          ...projects.map((project) => ({ value: project.id, label: project.title })),
        ]}
        value={reader?.projectId ?? ""}
      />
      {reader !== null && <strong>📖　{reader.projectTitle}</strong>}
    </header>

    <div className={reader === null ? "reader-content" : `reader-content is-reader-loaded${hasInnerTabs ? " has-reader-inner-tabs" : ""}`}>
      {reader === null
        ? <section className="reader-empty" aria-live="polite">{isLoading ? "正在展开卷帙…" : "请在案头选择一个项目，然后点击「阅卷」按钮来到此处。"}</section>
        : <>
          <div aria-label="卷帙分类" className="reader-tabs reader-outer-tabs" role="tablist">
            {sections.map((section) => <button aria-selected={section.id === activeSection?.id} className={section.id === activeSection?.id ? "is-active" : ""} key={section.id} onClick={() => selectSection(section)} role="tab" title={section.label} type="button">{section.label}</button>)}
          </div>
          {hasInnerTabs && activeSection !== undefined && <div aria-label={`${activeSection.label}分类`} className="reader-tabs reader-inner-tabs" role="tablist">
            {activeSection.subTabs.map((subtab) => <button aria-selected={subtab.id === activeSubtab?.id} className={subtab.id === activeSubtab?.id ? "is-active" : ""} key={subtab.id} onClick={() => selectSubtab(subtab)} role="tab" title={subtab.label} type="button">{subtab.label}</button>)}
          </div>}
          <ReaderPersistentPanelHost activePanelId={activePersistentPanelId} commandClient={commandClient} mountedPanelIds={persistentPanelsProjectId === reader.projectId ? mountedPersistentPanels : new Set()} onOperation={setOperationNotice} onRefreshNarrativeTools={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={reader.projectId} projectTitle={reader.projectTitle} sections={sections} tools={displayedTools} />
          <ReaderSectionContent activeSection={activeSection} activeSubtab={activeSubtab} commandClient={commandClient} documentOpened={documentOpened} onOperation={setOperationNotice} onRefreshNarrativeTools={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={reader.projectId} projectTitle={reader.projectTitle} tools={displayedTools} />
          {operationNotice.length > 0 && <p aria-live="polite" className="reader-operation-notice">{operationNotice}</p>}
        </>}
    </div>
    {pendingProjectId !== null && activeUnsavedSession !== undefined && <SourceMessageDialog actions={[
      { id: "save", label: activeUnsavedSession.saveLabel, tone: "primary" },
      { id: "discard", label: activeUnsavedSession.discardLabel, tone: "danger" },
      { id: "cancel", label: activeUnsavedSession.cancelLabel, tone: "secondary" },
    ]} informativeText={activeUnsavedSession.informativeText} message={activeUnsavedSession.message} onAction={resolveUnsavedSession} onClose={() => setPendingProjectId(null)} title={activeUnsavedSession.title} />}
  </div>;
}

/**
 * PySide keeps each QTabWidget page alive after it has been opened.  Only the
 * reader's three session-bearing panels need the same treatment on the web:
 * mounting every artifact would cost memory and make hidden workbenches react
 * to unrelated data changes.  `hidden` preserves React state and the dirty
 * session registrations while contributing no layout or paint work.
 */
function ReaderPersistentPanelHost({ activePanelId, commandClient, mountedPanelIds, onOperation, onRefreshNarrativeTools, onUnsavedSessionChange, projectId, projectTitle, sections, tools }: {
  readonly activePanelId: PersistentReaderPanelId | undefined;
  readonly commandClient: EngineCommandClient;
  readonly mountedPanelIds: ReadonlySet<PersistentReaderPanelId>;
  readonly onOperation: (message: string) => void;
  readonly onRefreshNarrativeTools?: (() => void) | undefined;
  readonly onUnsavedSessionChange: ReaderUnsavedSessionChange;
  readonly projectId: string;
  readonly projectTitle: string;
  readonly sections: readonly ReaderSection[];
  readonly tools: NarrativeToolsView | null;
}) {
  const shouldMount = (id: PersistentReaderPanelId) => activePanelId === id || mountedPanelIds.has(id);
  const chapterSection = sections.find((section) => section.kind === "chapters");
  const chapterArtifacts = chapterSection?.subTabs ?? [];
  const chapters = chapterSection?.chapters ?? [];

  return <>
    {shouldMount("chapters") && <div className="reader-persistent-panel" hidden={activePanelId !== "chapters"} key={`${projectId}-chapters`}>
      <ChapterReader active={activePanelId === "chapters"} artifacts={chapterArtifacts} chapters={chapters} commandClient={commandClient} humanizeLibraryRevision={tools?.humanizeLibraryRevision} onOperation={onOperation} onRefreshHumanizeLibrary={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={projectId} projectTitle={projectTitle} />
    </div>}
    {shouldMount("outline") && <div className="reader-persistent-panel" hidden={activePanelId !== "outline"} key={`${projectId}-outline`}>
      {tools === null
        ? <section className="reader-empty reader-document-empty">正在准备章节大纲工作台…</section>
        : <OutlineSessionWorkbench commandClient={commandClient} onOperation={onOperation} outline={tools.outline} planning={tools.planning} totalChapters={tools.visualization.totalChapters} projectId={projectId} />}
    </div>}
    {shouldMount("foundation-characters") && <div className="reader-persistent-panel" hidden={activePanelId !== "foundation-characters"} key={`${projectId}-foundation-characters`}>
      <ReaderNarrativeTools ariaLabel="角色与实体工作台" commandClient={commandClient} initialTab="characters" labels={{ characters: "档案", relationships: "关系", graph: "图谱" }} onOperation={onOperation} onRefreshNarrativeTools={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={projectId} sourceCharacterEditor tools={tools} visibleTabs={["characters", "relationships", "graph"]} />
    </div>}
  </>;
}

function ReaderSectionContent({ activeSection, activeSubtab, commandClient, documentOpened, onOperation, onRefreshNarrativeTools, onUnsavedSessionChange, projectId, projectTitle, tools }: { readonly activeSection: ReaderSection | undefined; readonly activeSubtab: ReaderSubtab | undefined; readonly commandClient: EngineCommandClient; readonly documentOpened: boolean; readonly onOperation: (message: string) => void; readonly onRefreshNarrativeTools?: (() => void) | undefined; readonly onUnsavedSessionChange: ReaderUnsavedSessionChange; readonly projectId: string; readonly projectTitle: string; readonly tools: NarrativeToolsView | null }) {
  if (!documentOpened || activeSection === undefined) {
    return <section className="reader-empty reader-document-empty">暂无{sectionEmptyLabel(activeSection, activeSubtab)}数据。<br />完成对应流程后将自动生成。</section>;
  }
  if (persistentReaderPanelId(activeSection, activeSubtab, documentOpened) !== undefined) return null;
  if (activeSection.kind === "chapters") return <ChapterReader artifacts={activeSection.subTabs} chapters={activeSection.chapters ?? []} commandClient={commandClient} humanizeLibraryRevision={tools?.humanizeLibraryRevision} onOperation={onOperation} onRefreshHumanizeLibrary={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={projectId} projectTitle={projectTitle} />;
  if (activeSection.kind === "tracking") return <TrackingReader activeSubtab={activeSubtab} commandClient={commandClient} onOperation={onOperation} projectId={projectId} projectTitle={projectTitle} tools={tools} />;
  if (activeSection.kind === "governance") return <GovernanceBrowser artifacts={activeSection.subTabs.flatMap((subtab) => subtab.artifact === undefined ? [] : [subtab.artifact])} onOperation={onOperation} projectTitle={projectTitle} />;
  if (activeSection.kind === "subplots") {
    return <ReaderNarrativeTools key="subplots" ariaLabel="支线管理工作台" commandClient={commandClient} hideTabs initialTab="subplots" onOperation={onOperation} onRefreshNarrativeTools={onRefreshNarrativeTools} projectId={projectId} tools={tools} visibleTabs={["subplots"]} />;
  }
  if (activeSection.id === "foundation" && activeSubtab?.id === "characters") {
    return <ReaderNarrativeTools key="foundation-characters" ariaLabel="角色与实体工作台" commandClient={commandClient} initialTab="characters" labels={{ characters: "档案", relationships: "关系", graph: "图谱" }} onOperation={onOperation} onRefreshNarrativeTools={onRefreshNarrativeTools} onUnsavedSessionChange={onUnsavedSessionChange} projectId={projectId} sourceCharacterEditor tools={tools} visibleTabs={["characters", "relationships", "graph"]} />;
  }
  if (activeSection.id === "blueprint") {
    return <ReaderNarrativeTools key="blueprint" ariaLabel="叙事蓝图工作台" commandClient={commandClient} hideTabs initialTab="timeline" onOperation={onOperation} onRefreshNarrativeTools={onRefreshNarrativeTools} projectId={projectId} tools={tools} visibleTabs={["timeline"]} />;
  }
  if (activeSection.id === "outline") {
    if (tools === null) return <section className="reader-empty reader-document-empty">正在准备章节大纲工作台…</section>;
    return <OutlineSessionWorkbench commandClient={commandClient} onOperation={onOperation} outline={tools.outline} planning={tools.planning} totalChapters={tools.visualization.totalChapters} projectId={projectId} />;
  }
  if (activeSubtab?.artifact !== undefined) return <ReaderArtifactDocument artifact={activeSubtab.artifact} projectTitle={projectTitle} />;
  return <section className="reader-empty reader-document-empty">暂无{sectionEmptyLabel(activeSection, activeSubtab)}数据。<br />完成对应流程后将自动生成。</section>;
}

function ReaderNarrativeTools({ ariaLabel, commandClient, hideTabs = false, initialTab, labels, onOperation, onRefreshNarrativeTools, onUnsavedSessionChange, projectId, relationshipFocusedCharacterId, sourceCharacterEditor = false, tools, visibleTabs }: { readonly ariaLabel: string; readonly commandClient?: EngineCommandClient | undefined; readonly hideTabs?: boolean; readonly initialTab: NarrativeToolTab; readonly labels?: Partial<Record<NarrativeToolTab, string>>; readonly onOperation: (message: string) => void; readonly onRefreshNarrativeTools?: (() => void) | undefined; readonly onUnsavedSessionChange?: ReaderUnsavedSessionChange; readonly projectId?: string | undefined; readonly relationshipFocusedCharacterId?: string | null; readonly sourceCharacterEditor?: boolean; readonly tools: NarrativeToolsView | null; readonly visibleTabs: readonly NarrativeToolTab[] }) {
  const handleAction = useCallback((action: NarrativeToolsAction) => onOperation(action.message), [onOperation]);
  if (tools === null) return <section className="reader-empty reader-document-empty">正在准备{ariaLabel}…</section>;
  const optionalLabels = labels === undefined ? {} : { labels };
  const optionalRelationshipFilter = relationshipFocusedCharacterId === undefined ? {} : { relationshipFocusedCharacterId };
  const optionalUnsavedSession = onUnsavedSessionChange === undefined ? {} : { onUnsavedSessionChange };
  return <section aria-label={ariaLabel} className="reader-narrative-tools"><NarrativeToolsWorkbench commandClient={commandClient} hideTabs={hideTabs} initialTab={initialTab} onAction={handleAction} onNarrativeToolsRefresh={onRefreshNarrativeTools} projectId={projectId} showNotice={false} sourceCharacterEditor={sourceCharacterEditor} tools={tools} visibleTabs={visibleTabs} {...optionalLabels} {...optionalRelationshipFilter} {...optionalUnsavedSession} /></section>;
}

function chapterNumber(artifact: ProjectReaderArtifactView): number | null {
  const match = /第\s*(\d+)\s*章/.exec(artifact.label) ?? /chapter[-_](\d+)/i.exec(artifact.id);
  return match === null ? null : Number(match[1]);
}

function isReportArtifact(artifact: ProjectReaderArtifactView): boolean {
  return artifact.id.includes("report") || artifact.sourceLabel.startsWith("reports/");
}

/** Draft version artifacts carry a `-draft-` segment in their id (see backend
 * `_chapter_draft_artifacts`); the final text does not. */
function isDraftArtifact(artifact: ProjectReaderArtifactView): boolean {
  return artifact.id.includes("-draft-");
}

/** Read-only prose view for draft snapshots (PySide6 `render_chapter_prose`). */
function ChapterProseReadOnly({ artifact }: { readonly artifact: ProjectReaderArtifactView }) {
  const text = artifact.content ?? artifact.paragraphs.join("\n\n");
  return <article className="reader-prose-read">
    <header><span className="section-kicker">{artifact.caption}</span><h2>{artifact.label}</h2><p>草稿快照 · 只读</p></header>
    <div>{text.split("\n\n").map((paragraph, index) => <p key={`${artifact.id}-draft-${index}`}>{paragraph}</p>)}</div>
    <footer><span>该版本为过程草稿；终稿可在「终稿」页签查阅与砚修。</span></footer>
  </article>;
}

export function ChapterReader({ active = true, artifacts, chapters, commandClient, humanizeLibraryRevision, onOperation, onRefreshHumanizeLibrary, onUnsavedSessionChange, projectId, projectTitle }: { readonly active?: boolean; readonly artifacts: readonly ReaderSubtab[]; readonly chapters: readonly ProjectReaderChapterView[]; readonly commandClient: EngineCommandClient; readonly humanizeLibraryRevision?: string | undefined; readonly onOperation: (message: string) => void; readonly onRefreshHumanizeLibrary?: (() => void) | undefined; readonly onUnsavedSessionChange: ReaderUnsavedSessionChange; readonly projectId: string; readonly projectTitle: string }) {
  const setAuthoringLocation = useAuthoring()?.setLocation;
  const [mode, setMode] = useState<"prose" | "reports">("prose");
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const allArtifacts = artifacts.flatMap((subtab) => subtab.artifact === undefined ? [] : [subtab.artifact]);
  const chapterEntries = useMemo(() => {
    if (chapters.length > 0) return chapters;
    return [...new Set(allArtifacts.map(chapterNumber).filter((value): value is number => value !== null))]
      .sort((a, b) => a - b)
      .map((number): ProjectReaderChapterView => ({
        number,
        state: allArtifacts.some((artifact) => chapterNumber(artifact) === number && !isReportArtifact(artifact) && !isDraftArtifact(artifact)) ? "final" : "draft",
        title: `第${number}章`,
      }));
  }, [allArtifacts, chapters]);
  const chapterNumbers = useMemo(() => chapterEntries.map((chapter) => chapter.number), [chapterEntries]);
  const [selectedChapter, setSelectedChapter] = useState(() => chapterNumbers[0] ?? null);
  useEffect(() => { if (active) setAuthoringLocation?.(mode === "reports" ? "reports" : "chapter", selectedChapter ?? 1); }, [active, mode, selectedChapter, setAuthoringLocation]);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleChapters = chapterEntries.filter((chapter) => (
    normalizedQuery.length === 0
    || `第 ${chapter.number} 章 ${chapter.title}`.toLocaleLowerCase().includes(normalizedQuery)
  ));
  // Prose versions for the selected chapter: the final text plus any draft
  // snapshots (PySide6 `_fill_chapter_text` shows them as version tabs).
  const proseVersions = useMemo(
    () => allArtifacts.filter((artifact) => chapterNumber(artifact) === selectedChapter && !isReportArtifact(artifact)),
    [allArtifacts, selectedChapter],
  );
  const selectedVersion = proseVersions.find((version) => version.id === selectedVersionId)
    ?? proseVersions.find((version) => !isDraftArtifact(version))
    ?? proseVersions[0];

  useEffect(() => {
    if (selectedChapter === null || !chapterNumbers.includes(selectedChapter)) setSelectedChapter(chapterNumbers[0] ?? null);
  }, [chapterNumbers, selectedChapter]);

  // Switching chapter resets the version pick back to the final text.
  useEffect(() => { setSelectedVersionId(null); }, [selectedChapter]);

  const reportArtifacts = allArtifacts.filter(isReportArtifact);
  const virtual = useVirtualList({ itemCount: visibleChapters.length, itemHeight: CHAPTER_LIST_ROW_HEIGHT });
  const shouldVirtualize = visibleChapters.length > CHAPTER_LIST_VIRTUAL_THRESHOLD;
  const renderedChapters = shouldVirtualize
    ? visibleChapters.slice(virtual.visibleRange.start, virtual.visibleRange.end)
    : visibleChapters;
  const chapterStateLabel: Readonly<Record<ProjectReaderChapterView["state"], string>> = { final: "终稿", draft: "草稿", pending: "待续写" };
  const chapterRow = (chapter: ProjectReaderChapterView) => {
    const meta = [
      chapterStateLabel[chapter.state],
      chapter.wordCount && chapter.wordCount > 0 ? `${chapter.wordCount.toLocaleString()} 字` : "",
    ].filter(Boolean).join(" · ");
    return <button aria-current={chapter.number === selectedChapter ? "true" : undefined} aria-label={`第 ${chapter.number} 章 · ${chapter.title} · ${meta}`} className={chapter.number === selectedChapter ? "is-active" : ""} key={chapter.number} onClick={() => setSelectedChapter(chapter.number)} role="listitem" type="button"><span className="reader-chapter-label">第 {chapter.number} 章 · {chapter.title}</span><span aria-hidden="true" className="reader-chapter-compact-number">{chapter.number}</span><small>{meta}</small></button>;
  };
  return <section aria-label="章节阅读器" className={`reader-chapter-workbench${railCollapsed ? " is-rail-collapsed" : ""}`}>
    <div className="reader-chapter-mode" role="tablist" aria-label="章节内容类型"><button aria-selected={mode === "prose"} className={mode === "prose" ? "is-active" : ""} onClick={() => setMode("prose")} role="tab" type="button">正文</button><button aria-selected={mode === "reports"} className={mode === "reports" ? "is-active" : ""} onClick={() => setMode("reports")} role="tab" type="button">报告</button><button aria-controls="reader-chapter-rail" aria-expanded={!railCollapsed} className="reader-chapter-rail-toggle" onClick={() => setRailCollapsed((value) => !value)} type="button">{railCollapsed ? "展开章节轨道" : "收起章节轨道"}</button></div>
    <aside className={`reader-chapter-list${railCollapsed ? " is-collapsed" : ""}`} id="reader-chapter-rail"><label><span className="sr-only">搜索章节</span><input aria-label="搜索章节" onChange={(event) => setQuery(event.target.value)} placeholder="搜索章节" type="search" value={query} /></label>{shouldVirtualize
      ? <div className="reader-chapter-scroll" role="list" {...virtual.containerProps}><div style={{ height: virtual.totalHeight, position: "relative" }}><div className="reader-chapter-virtual-window" style={{ transform: `translateY(${virtual.offsetY}px)` }}>{renderedChapters.map(chapterRow)}</div></div></div>
      : <div className="reader-chapter-scroll" role="list">{renderedChapters.map(chapterRow)}{visibleChapters.length === 0 && <p>没有匹配的章节。</p>}</div>}</aside>
    <div className="reader-chapter-document">{mode === "prose"
      ? proseVersions.length === 0
        ? <section className="reader-empty">{selectedChapter === null ? "暂无章节数据。\n完成立项后此处将列出所有章节。" : `第 ${selectedChapter} 章尚未落笔。\n续写该章后可在此阅读。`}</section>
        : <>
            {proseVersions.length > 1 && <div aria-label="章节版本" className="reader-chapter-versions" role="tablist">{proseVersions.map((version) => <button aria-selected={version.id === selectedVersion?.id} className={version.id === selectedVersion?.id ? "is-active" : ""} key={version.id} onClick={() => setSelectedVersionId(version.id)} role="tab" type="button">{isDraftArtifact(version) ? version.label : "终稿"}</button>)}</div>}
            {selectedVersion !== undefined && (isDraftArtifact(selectedVersion)
              ? <ChapterProseReadOnly artifact={selectedVersion} />
              : <ChapterProseRevision artifact={selectedVersion} commandClient={commandClient} humanizeLibraryRevision={humanizeLibraryRevision} onOperation={onOperation} onRefreshHumanizeLibrary={onRefreshHumanizeLibrary} onUnsavedSessionChange={onUnsavedSessionChange} projectId={projectId} projectTitle={projectTitle} />)}
          </>
      : <ChapterReportBrowser artifacts={reportArtifacts.filter((artifact) => chapterNumber(artifact) === selectedChapter)} onOperation={onOperation} projectTitle={projectTitle} />}</div>
  </section>;
}

function TrackingReader({ activeSubtab, commandClient, onOperation, projectId, projectTitle, tools }: { readonly activeSubtab: ReaderSubtab | undefined; readonly commandClient: EngineCommandClient; readonly onOperation: (message: string) => void; readonly projectId: string; readonly projectTitle: string; readonly tools: NarrativeToolsView | null }) {
  if (activeSubtab?.id === "relationship") return <RelationshipTrackingReader onOperation={onOperation} tools={tools} />;
  if (activeSubtab?.id === "token-analytics" && activeSubtab.artifact !== undefined) return <TokenAnalyticsWorkbench artifact={activeSubtab.artifact} commandClient={commandClient} onOperation={onOperation} projectId={projectId} />;
  if (activeSubtab?.artifact !== undefined) return <ReaderArtifactDocument artifact={activeSubtab.artifact} projectTitle={projectTitle} />;
  return <section className="reader-empty reader-document-empty">暂无{activeSubtab?.emptyLabel ?? "追踪"}数据。</section>;
}

function relationshipPercent(value: number): number {
  return Math.round(Math.min(1, Math.max(0, value)) * 100);
}

function RelationshipMetricBar({ label, value, tone }: { readonly label: string; readonly value: number; readonly tone: "trust" | "tension" }) {
  const pct = relationshipPercent(value);
  return <div className={`relationship-metric is-${tone}`}>
    <div className="relationship-metric-copy">
      <span className="relationship-metric-label">{label}</span>
      <strong className="relationship-metric-value">{pct}%</strong>
    </div>
    <div
      aria-label={`${label} ${pct}%`}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={pct}
      className="relationship-metric-track"
      role="progressbar"
    >
      <span className={`relationship-metric-fill is-${tone}`} style={{ width: `${pct}%` }} />
    </div>
  </div>;
}

/** Relationship evolution dashboard (PySide6 `_build_relationship_tab` source):
 * per-pair trust/tension metrics plus the chapter-by-chapter shift trail. */
export function RelationshipEvolutionView({ overview }: { readonly overview: RelationshipOverviewView }) {
  const [filterName, setFilterName] = useState<string | null>(null);
  const allNames = useMemo(() => {
    const names = new Set<string>();
    for (const timeline of overview.timelines) {
      names.add(timeline.characterA);
      names.add(timeline.characterB);
    }
    return [...names].sort();
  }, [overview]);
  const characterOptions = [
    { value: "", label: "全部角色" },
    ...allNames.map((name) => ({ value: name, label: name })),
  ];
  const timelines = overview.timelines.filter((timeline) => filterName === null || timeline.characterA === filterName || timeline.characterB === filterName);

  if (overview.timelines.length === 0) {
    return <section className="reader-empty reader-document-empty">暂未追踪到角色关系。\n完成章节创作后，角色之间的关系变化将在此展示。</section>;
  }

  return <div className="doc-rich relationship-evolution">
    <header className="relationship-evolution-filter">
      <div className="relationship-filter-control">
        <span>角色筛选</span>
        <DropdownSelect
          ariaLabel="角色筛选"
          className="relationship-character-select"
          onChange={(value) => setFilterName(value || null)}
          options={characterOptions}
          value={filterName ?? ""}
        />
      </div>
      <span className="relationship-evolution-count">{filterName === null ? `共 ${overview.totalRelationships} 组关系` : `显示 ${timelines.length} 组关系`}</span>
    </header>
    <div className="relationship-pair-list">
      {timelines.map((timeline) => (
        <article className="doc-section relationship-pair" key={timeline.pairId}>
          <header className="relationship-pair-head">
            <div>
              <h3 className="relationship-pair-title">{timeline.characterA} × {timeline.characterB}</h3>
              <span className="relationship-pair-meta">已记录 {timeline.snapshots.length} 次关系变动</span>
            </div>
            {timeline.currentStatus.length > 0 && <DocTag>{timeline.currentStatus}</DocTag>}
          </header>
          <div className="relationship-pair-metrics">
            <RelationshipMetricBar label="信任" tone="trust" value={timeline.currentTrust} />
            <RelationshipMetricBar label="张力" tone="tension" value={timeline.currentTension} />
          </div>
          {timeline.snapshots.length > 0 && <div aria-label={`${timeline.characterA}与${timeline.characterB}的关系时间线`} className="relationship-pair-snapshots" role="table">
            <div aria-hidden="true" className="relationship-snapshot is-heading" role="row">
              <span role="columnheader">章节</span>
              <span role="columnheader">关系状态</span>
              <span role="columnheader">变动记录</span>
            </div>
            {timeline.snapshots.map((snapshot, index) => (
              <div className="relationship-snapshot" key={`${timeline.pairId}-${snapshot.chapter}-${index}`} role="row">
                <span className="relationship-snapshot-chapter" role="cell">第 {snapshot.chapter} 章</span>
                <span className="relationship-snapshot-status" role="cell">
                  <strong>{snapshot.status}</strong>
                  <small>信任 {relationshipPercent(snapshot.trust)}% · 张力 {relationshipPercent(snapshot.tension)}%</small>
                </span>
                <span className="relationship-snapshot-shift" role="cell">{snapshot.shift}</span>
              </div>
            ))}
          </div>}
        </article>
      ))}
    </div>
  </div>;
}

function RelationshipTrackingReader({ onOperation, tools }: { readonly onOperation: (message: string) => void; readonly tools: NarrativeToolsView | null }) {
  const [view, setView] = useState<"evolution" | "graph">("evolution");
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  if (tools === null) return <section className="reader-empty reader-document-empty">正在准备关系追踪工作台…</section>;
  const overview = tools.relationshipOverview;
  const graphCharacterOptions = [
    { value: "", label: "全部角色" },
    ...tools.characters.map((character) => ({ value: character.id, label: character.name })),
  ];
  return <section aria-label="关系追踪工作台" className="reader-tracking-workbench">
    <header className="reader-tracking-toolbar">
      <div className="relationship-toolbar-main">
        <div aria-label="关系视图" className="relationship-view-toggle" role="group"><button className={view === "evolution" ? "is-active" : ""} onClick={() => setView("evolution")} type="button">演变</button><button className={view === "graph" ? "is-active" : ""} onClick={() => setView("graph")} type="button">图谱</button></div>
        {view === "graph" && <div className="relationship-toolbar-filter"><span>角色筛选</span><DropdownSelect ariaLabel="图谱角色筛选" className="relationship-character-select" onChange={(value) => setSelectedCharacterId(value || null)} options={graphCharacterOptions} value={selectedCharacterId ?? ""} /></div>}
      </div>
      <button className="button button-secondary" onClick={() => { setRefreshVersion((version) => version + 1); onOperation("已按当前筛选刷新关系追踪。"); }} type="button">刷新</button>
    </header>
    {view === "evolution" && overview !== undefined
      ? <RelationshipEvolutionView key={refreshVersion} overview={overview} />
      : <ReaderNarrativeTools ariaLabel="关系图谱" hideTabs initialTab="relationships" key={refreshVersion} onOperation={onOperation} relationshipFocusedCharacterId={selectedCharacterId} tools={tools} visibleTabs={["relationships"]} />}
  </section>;
}
