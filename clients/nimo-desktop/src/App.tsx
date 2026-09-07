import {
  Suspense,
  type ChangeEvent,
  type MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";

import type {
  ChapterStudioView,
  EngineClient,
  EngineCommandClient,
  FilmProviderCatalogView,
  FilmStudioView,
  PageId,
  ProjectView,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import { BrandLogo } from "./components/BrandLogo";
import { taskProgressStatus } from "./lib/task-progress-status";
import { EngineRuntimeStatus } from "./components/EngineRuntimeStatus";
import { Icon } from "./components/Icon";
import { TopBarControls } from "./components/TopBarControls";
import { PetCompanion } from "./components/pet/PetCompanion";
import { TaskObservationDialog } from "./components/TaskObservationDialog";
import type { FloatingStreamAnchor } from "./components/FloatingStreamDialog";
import {
  LoadingSurface,
  ConnectionErrorSurface,
  IncompatibleVersionSurface,
  SettingsLoadingSurface,
  ParityInProgress,
} from "./components/LoadingSurface";
import { DashboardPage } from "./components/DashboardPage";
import {
  LazyProjectsReader,
  preloadProjectsReader,
} from "./components/projects-reader-loader";
import {
  LazyWorkflowPage,
  preloadWorkflowPage,
} from "./components/workflow-page-loader";
import {
  LazyChapterStudioPage,
  preloadChapterStudioPage,
} from "./components/chapter-studio-loader";
import {
  LazyVoiceStudioPage,
  preloadVoiceStudioPage,
} from "./components/voice-studio-loader";
import {
  LazyFilmStudioPage,
  preloadFilmStudioPage,
} from "./components/film-studio-loader";
import { LazySettingsPage, preloadSettingsPage } from "./components/settings-page-loader";
import type { WorkflowMode } from "./components/WorkflowComposer";
import type { PageMeta } from "./components/types";
import { formatSideRailLabel } from "./components/types";

import type { EngineRuntimeConfig } from "./lib/engine-client-factory";
import { syncNativeWindowIcon } from "./lib/native-icon";
import { onShortcut } from "./lib/native-bridge";
import {
  applyUrlParityFixture,
  frozenPausedValidatedTaskStreamFixture,
  frozenParityTaskStreamFixture,
} from "./lib/parity-fixture";
import {
  projectChapterStudioParityState,
  type ChapterStudioParityState,
} from "./lib/chapter-studio-session";
import { ConnectivityController } from "./lib/connectivity-controller";
import { LatestRequestGate } from "./lib/latest-request";
import { ChapterStudioPoller } from "./lib/chapter-studio-poller";
import { createTaskStreamState } from "./lib/task-stream";
import { useTaskStream } from "./lib/use-task-stream";
import {
  loadUiSession,
  persistUiSession,
  rememberUiSessionOperation,
  type FilmStudioFormatId,
} from "./lib/ui-session";
import {
  ttsProviderKeyFromLabel,
  ttsProviderLabelForId,
} from "./lib/voice-provider-options";
import { EngineRuntimeContext, type EngineRuntimeState } from "./lib/engine-runtime-context";
import { useEngineRuntimeStatus } from "./lib/engine-runtime-status";
import { useEngineSleepInhibitor } from "./lib/use-engine-sleep-inhibitor";
import { useDesktopResumeRecovery } from "./lib/use-desktop-resume-recovery";
import { isCommandAvailable, isFeatureEnabled } from "./lib/engine-negotiation";
import { useJobNotifications } from "./lib/use-job-notifications";
import { useEngineData, useSettings } from "./hooks/useEngineData";
import { useLayoutDensity } from "./hooks/useLayoutDensity";
import { useProjectReader, useNarrativeTools } from "./hooks/useProjectReader";
import { buildChapterStudioLoadingState } from "./lib/chapter-studio-loading-state";
import { ThemeProvider, useTheme } from "./theme/ThemeProvider";
import {
  applyFontPreferences,
  loadStoredFontPreferences,
} from "./theme/font-preferences";
import { LocaleProvider, loadStoredLocale, saveLocale, translate, useLocale, useT, type Locale } from "./lib/i18n";
import { resolvePageMeta } from "./components/types";

export const sideRailPageMeta: readonly PageMeta[] = [
  { id: "dashboard", labelKey: "nav.dashboard.label", eyebrowKey: "nav.dashboard.label", titleKey: "nav.dashboard.title", subtitleKey: "nav.dashboard.subtitle", icon: "dashboard" },
  { id: "projects", labelKey: "nav.projects.label", eyebrowKey: "nav.projects.label", titleKey: "nav.projects.title", subtitleKey: "nav.projects.subtitle", icon: "archive" },
  { id: "workflow", labelKey: "nav.workflow.label", eyebrowKey: "nav.workflow.label", titleKey: "nav.workflow.title", subtitleKey: "nav.workflow.subtitle", icon: "workflow" },
  { id: "chapter_studio", labelKey: "nav.chapterStudio.label", eyebrowKey: "nav.chapterStudio.label", titleKey: "nav.chapterStudio.title", subtitleKey: "nav.chapterStudio.subtitle", icon: "studio" },
  { id: "voice_studio", labelKey: "nav.voiceStudio.label", eyebrowKey: "nav.voiceStudio.label", titleKey: "nav.voiceStudio.title", subtitleKey: "nav.voiceStudio.subtitle", icon: "voice" },
  { id: "film_studio", labelKey: "nav.filmStudio.label", eyebrowKey: "nav.filmStudio.label", titleKey: "nav.filmStudio.title", subtitleKey: "nav.filmStudio.subtitle", icon: "film" },
  { id: "settings", labelKey: "nav.settings.label", eyebrowKey: "nav.settings.label", titleKey: "nav.settings.title", subtitleKey: "nav.settings.subtitle", icon: "settings" },
];

function currentMeta(page: PageId): PageMeta {
  const meta = sideRailPageMeta.find((entry) => entry.id === page);
  if (meta === undefined) {
    // 旧版短剧/漫画页面已融合进映界：遗留路由回退到映界元信息。
    const film = sideRailPageMeta.find((entry) => entry.id === "film_studio");
    if (film !== undefined) return film;
    throw new Error(`Unknown page: ${page}`);
  }
  return meta;
}

/** 短剧与漫画已融入映界：这些遗留 PageId 与映界页面共享数据加载。 */
function isFilmStudioPage(page: PageId): boolean {
  return page === "film_studio" || page === "drama_studio" || page === "comic_studio";
}

function preloadPage(page: PageId): Promise<unknown> | undefined {
  switch (page) {
    case "projects":
      return preloadProjectsReader();
    case "workflow":
      return preloadWorkflowPage();
    case "settings":
      return preloadSettingsPage();
    case "chapter_studio":
      return preloadChapterStudioPage();
    case "voice_studio":
      return preloadVoiceStudioPage();
    case "film_studio":
    case "drama_studio":
    case "comic_studio":
      return preloadFilmStudioPage();
    case "dashboard":
      return undefined;
  }
}

function operationLabelFromTarget(target: EventTarget | null): string {
  if (!(target instanceof Element)) return "";
  const control = target.closest("button, a[href], [role='tab'], [role='switch'], summary, input[type='checkbox'], input[type='radio']");
  if (control === null) return "";
  return (
    control.getAttribute("aria-label")
    ?? control.getAttribute("title")
    ?? control.textContent?.replaceAll(/\s+/g, " ").trim()
    ?? ""
  ).slice(0, 60);
}

function editableFieldLabelFromTarget(target: EventTarget | null): string {
  if (!(target instanceof Element)) return "";
  const field = target.closest("input, select, textarea");
  if (field === null) return "";
  if (field instanceof HTMLInputElement && field.type === "password") return "";
  const labelled = field.getAttribute("aria-label")
    ?? field.closest("label")?.textContent?.replaceAll(/\s+/g, " ").trim()
    ?? field.getAttribute("name")
    ?? "";
  return labelled.slice(0, 180);
}

/**
 * The desktop shell depends on the transport-neutral EngineClient contract.
 *
 * The engine-client-factory selects the concrete implementation based on
 * VITE_ENGINE_MODE:
 * - "mock" (default): deterministic fixtures for development and testing
 * - "legacy": LegacyLocalEngineClient calling the local Python FastAPI backend
 *
 * Keeping the client at this composition boundary means the same tree can
 * later be mounted with a cloud adapter without page components acquiring
 * a second data path.
 */
export interface AppProps {
  readonly engineClient: EngineClient;
  readonly engineCommandClient: EngineCommandClient;
  readonly runtimeConfig: EngineRuntimeConfig;
}

const noProjects: readonly ProjectView[] = [];

export function readerReusesChapterNarrativeTools(
  readerProjectId: string | null,
  chapterProjectId: string | null,
): boolean {
  return readerProjectId !== null && readerProjectId === chapterProjectId;
}

export function App(props: AppProps) {
  return <ThemeProvider><AppContent {...props} /></ThemeProvider>;
}

function AppContent({ engineClient, engineCommandClient, runtimeConfig }: AppProps) {
  // The initializer runs before `loadUiSession` and only changes storage for
  // an explicit `__nimo_ui_parity=1` native-capture URL.
  const [parityFixture] = useState(applyUrlParityFixture);
  const [uiSession, setUiSession] = useState(loadUiSession);
  const pageCanvasRef = useRef<HTMLElement | null>(null);

  const {
    workspace,
    jobs,
    workflow,
    connectionState,
    negotiation,
    refreshTaskData,
    retry: retryConnection,
  } = useEngineData(engineClient);
  const { diagnostic: engineDiagnostic, refresh: refreshEngineDiagnostic } = useEngineRuntimeStatus(
    runtimeConfig,
  );
  useEngineSleepInhibitor(engineDiagnostic.runtime);
  useDesktopResumeRecovery(() => {
    retryConnection();
    void refreshEngineDiagnostic();
  });

  // Desktop notifications: fire when jobs transition to succeeded/failed.
  useJobNotifications(jobs);

  // Engine runtime context: provides mode + capabilities to all pages.
  const engineRuntime: EngineRuntimeState = useMemo(() => ({
    mode: runtimeConfig.mode,
    negotiation,
    diagnostic: engineDiagnostic,
    canSubmitTasks: engineDiagnostic.canSubmitTasks,
    isCommandAvailable: (command: string) =>
      engineDiagnostic.canSubmitTasks && isCommandAvailable(negotiation, command),
    isFeatureEnabled: (feature: string) => isFeatureEnabled(negotiation, feature),
  }), [engineDiagnostic, negotiation, runtimeConfig.mode]);

  // Density parity: sets data-density attribute on <html> (1360×820 threshold, 500ms debounce)
  useLayoutDensity();
  const {
    settings,
    settingsDirty,
    settingsSaveRequest,
    setSettingsDirty,
    ensureSettingsLoaded,
    reloadSettings,
    requestSave,
  } = useSettings(engineClient);
  const { projectReader, projectReaderId, openProjectReader } = useProjectReader(
    engineClient,
    workspace,
    // 对标 projects_page restore_ui_state()["project_id"]：无 parity fixture 时恢复上次阅览的项目。
    parityFixture.projectReaderId ?? (uiSession.readerProjectId || null),
    uiSession.activePage === "projects",
  );
  const longProjects = workspace?.projects.filter((project) => project.mode === "long") ?? noProjects;
  const longProjectId = longProjects[0]?.id ?? null;
  // 侧栏底部状态栏（rail-footer）数据源：
  // - 项目数 / 字数取自 workspace.metrics，与 PySide `RAIL_FOOTER_ACTIVE` / `RAIL_FOOTER_WORDS` 对应。
  // - provider 列表取自 workspace.providerGroups，只展示就绪的 provider（PySide `RAIL_FOOTER_PROVIDERS`）。
  // - 数字按 `Intl.NumberFormat("zh-CN")` 渲染，匹配 PySide `{:,}` 千位分隔（"15,000" 而非 "15000"）。
  const railFooter = useMemo(() => {
    const metrics = workspace?.metrics;
    const projectCount = metrics?.totalProjects ?? 0;
    const wordCount = metrics?.totalWords ?? 0;
    const numberFormatter = new Intl.NumberFormat("zh-CN");
    const loadedProviders = (workspace?.providerGroups ?? [])
      .filter((group) => group.ready)
      .map((group) => group.providerId);
    const providerLabel = loadedProviders[0] ?? workspace?.defaultProvider ?? "";
    return {
      projectCount: numberFormatter.format(projectCount),
      wordCount: numberFormatter.format(wordCount),
      wordCountRaw: wordCount,
      providerLabel,
      hasLoadedProviders: loadedProviders.length > 0,
    };
  }, [workspace?.metrics, workspace?.providerGroups, workspace?.defaultProvider]);
  const chapterProjectId = useMemo(() => {
    if (
      uiSession.chapterProjectId.length > 0 &&
      longProjects.some((project) => project.id === uiSession.chapterProjectId)
    ) {
      return uiSession.chapterProjectId;
    }
    return longProjectId;
  }, [longProjectId, longProjects, uiSession.chapterProjectId]);
  const voiceProjects = workspace?.projects ?? noProjects;
  const voiceProjectId = useMemo(() => {
    if (
      uiSession.voiceProjectId.length > 0 &&
      voiceProjects.some((project) => project.id === uiSession.voiceProjectId)
    ) {
      return uiSession.voiceProjectId;
    }
    return longProjectId ?? voiceProjects[0]?.id ?? null;
  }, [longProjectId, uiSession.voiceProjectId, voiceProjects]);
  const filmProjects = workspace?.projects ?? noProjects;
  const [selectedFilmProjectId, setSelectedFilmProjectId] = useState<string | null>(null);
  const filmProjectId = useMemo(() => {
    if (
      selectedFilmProjectId !== null &&
      filmProjects.some((project) => project.id === selectedFilmProjectId)
    ) {
      return selectedFilmProjectId;
    }
    return longProjectId ?? filmProjects[0]?.id ?? null;
  }, [filmProjects, longProjectId, selectedFilmProjectId]);
  const voiceRequestGateRef = useRef(new LatestRequestGate());
  const filmRequestGateRef = useRef(new LatestRequestGate());
  const activeVoiceProjectIdRef = useRef<string | null>(voiceProjectId);
  const activeFilmProjectIdRef = useRef<string | null>(filmProjectId);
  activeVoiceProjectIdRef.current = voiceProjectId;
  activeFilmProjectIdRef.current = filmProjectId;

  const [chapterStudio, setChapterStudio] = useState<ChapterStudioView | null>(null);
  const [chapterStudioError, setChapterStudioError] = useState<string | null>(null);
  const [chapterStudioReloadToken, setChapterStudioReloadToken] = useState(0);
  // Initial loads and background polls are separate async paths.  Keep one
  // monotonically increasing generation so a pre-refresh poll can never put
  // an obsolete projection back after an explicit reload.
  const chapterStudioGenerationRef = useRef(0);
  const [narrativeToolsReloadToken, setNarrativeToolsReloadToken] = useState(0);
  const [voiceStudio, setVoiceStudio] = useState<VoiceStudioView | null>(null);
  // Tracks whether voice/synthesis workers are running; disables the
  // top-bar provider dropdown while a task is in flight, mirroring
  // PySide `_switch_provider`'s "请完成或取消后再切换平台" guard.
  const [voiceWorkerBusy, setVoiceWorkerBusy] = useState(false);
  /** Bumped after a top-bar TTS provider switch to refresh the local model center. */
  const [modelCenterRefreshToken, setModelCenterRefreshToken] = useState(0);
  // Mirror the latest voiceStudio into a ref so async callbacks (e.g. the
  // top-bar provider switch handler) can read a non-stale snapshot for
  // optimistic-update bookkeeping without re-deriving deps every render.
  const voiceStudioRef = useRef<VoiceStudioView | null>(voiceStudio);
  voiceStudioRef.current = voiceStudio;
  const [filmStudio, setFilmStudio] = useState<FilmStudioView | null>(null);
  const [filmCatalog, setFilmCatalog] = useState<FilmProviderCatalogView | null>(null);
  const {
    activeTheme,
    activeThemeId: themeId,
    preference: themePreference,
    setContrast: setThemeContrast,
    setMode: setThemeMode,
    setTheme,
  } = useTheme();
  const [fontPreferences, setFontPreferences] = useState(loadStoredFontPreferences);
  const [locale, setLocale] = useState<Locale>(loadStoredLocale);
  const [workflowMode, setWorkflowModeState] = useState<WorkflowMode>(
    uiSession.workflowMode,
  );

  // Narrative tools are now bound to the chapter-studio-selected project id
  // (previously: the first long-form project in the workspace). When the user
  // switches the chapter studio to a different long-form project, narrative
  // tools refresh alongside it.
  const {
    narrativeTools: chapterNarrativeTools,
    narrativeToolsError: chapterNarrativeToolsError,
  } = useNarrativeTools(
    engineClient,
    chapterProjectId,
    narrativeToolsReloadToken,
    uiSession.activePage === "chapter_studio" || (uiSession.activePage === "projects" && projectReaderId === chapterProjectId),
  );
  // 阅读器和章台允许分别浏览不同项目。叙事工具必须跟随实际打开的
  // 阅读器项目，不能复用章台项目的数据，否则会让 A 项目的标题显示 B
  // 项目的空蓝图或关系图谱。
  const readerNarrativeToolsProjectId = projectReader?.projectId ?? projectReaderId;
  const readerUsesChapterNarrativeTools = readerReusesChapterNarrativeTools(
    readerNarrativeToolsProjectId,
    chapterProjectId,
  );
  const { narrativeTools: readerSpecificNarrativeTools, narrativeToolsError: readerSpecificNarrativeToolsError } = useNarrativeTools(
    engineClient,
    readerUsesChapterNarrativeTools ? null : readerNarrativeToolsProjectId,
    narrativeToolsReloadToken,
    uiSession.activePage === "projects",
  );
  const readerNarrativeTools = readerUsesChapterNarrativeTools
    ? chapterNarrativeTools
    : readerSpecificNarrativeTools;
  const setWorkflowMode = (mode: WorkflowMode) => {
    setWorkflowModeState(mode);
    setUiSession((session) =>
      rememberUiSessionOperation(
        { ...session, workflowMode: mode },
        "workflow",
        mode === "long" ? "切换为长篇工作流" : "切换为短篇工作流",
      ),
    );
  };
  const [shortTemplateExportRequest, setShortTemplateExportRequest] = useState(0);
  const [statusMessage, setStatusMessage] = useState("就绪");
  const submitTaskDecision = useCallback((jobId: string, decisionId: string, choice: string, customText: string, approvalVersion = "") => {
    if (!engineCommandClient.submitJobDecision) { setStatusMessage("当前客户端不支持版本化确认，任务保持等待"); return; }
    void engineCommandClient.submitJobDecision(jobId, decisionId, choice, customText, approvalVersion)
      .then(() => setStatusMessage("批准已提交；以引擎任务状态为准"))
      .catch((error) => setStatusMessage(`确认未生效：${String(error)}`));
  }, [engineCommandClient]);
  const [petVisible, setPetVisible] = useState(uiSession.petVisible);
  const [focusObservationOpen, setFocusObservationOpen] = useState(false);
  const [petStreamTaskId, setPetStreamTaskId] = useState<string | null>(null);
  // Ref to the pet's DOM node so we can compute a viewport rect and anchor
  // the pet-owned dialog (TaskObservationDialog with `source="companion"`)
  // next to the pet instead of the workspace center.
  const petContainerRef = useRef<HTMLDivElement | null>(null);
  const [petAnchor, setPetAnchor] = useState<FloatingStreamAnchor | null>(null);
  // Focus owns the globally active task. The pet deliberately has a separate
  // selected task and subscription so the two surfaces never exchange output.
  const activeJobForStream = useMemo(
    () => jobs.find((j) => j.state === "running" || j.state === "paused") ?? null,
    [jobs],
  );
  const globalStream = useTaskStream(engineClient, activeJobForStream?.id ?? null);
  const petObservationJob = useMemo(
    () => jobs.find((job) => job.id === petStreamTaskId) ?? null,
    [jobs, petStreamTaskId],
  );
  const petStream = useTaskStream(engineClient, petStreamTaskId);
  // 章台当前选中章节号（由 ChapterStudioPage 上报，对标 coord 的 chapter spin 值）。
  const [studioChapterNumber, setStudioChapterNumber] = useState<number | null>(null);

  const displayedChapterStudio = useMemo(
    () =>
      chapterStudio === null
        ? null
        : projectChapterStudioParityState(
            chapterStudio,
            parityFixture.chapterStudioState,
          ),
    [chapterStudio, parityFixture.chapterStudioState],
  );
  // 章台顶栏 primary 动态标签：对标 coord.primary_action_label() 四态
  // （checkpoint 在途 / 当前章未完 / 当前章已完且后续有章 / 全书完成）。
  const chapterStudioPrimaryLabel = useMemo(() => {
    const studio = displayedChapterStudio;
    if (studio === null) return translate(locale, "topbar.continueNode");
    if (studio.activity.checkpoint !== null) return translate(locale, "topbar.continueNode");
    const chapterNumber = studioChapterNumber ?? studio.nextChapter;
    const chapter = studio.chapters.find((item) => item.number === chapterNumber);
    if (chapter?.state === "completed") {
      return chapterNumber < studio.totalChapters
        ? translate(locale, "topbar.goToChapter", { n: chapterNumber + 1 })
        : translate(locale, "topbar.bookComplete");
    }
    return translate(locale, "topbar.continueNode");
  }, [displayedChapterStudio, studioChapterNumber, locale]);
  const frozenWorkflowStream = useMemo(
    () =>
      parityFixture.workflowDialog === "floating-stream"
        ? createTaskStreamState(
          parityFixture.workflowStream === "paused-validated"
            ? frozenPausedValidatedTaskStreamFixture
            : frozenParityTaskStreamFixture,
        )
        : null,
    [parityFixture.workflowDialog, parityFixture.workflowStream],
  );
  const connectivityController = useMemo(
    () => (settings === null ? null : new ConnectivityController(settings, engineCommandClient)),
    [engineCommandClient, settings],
  );

  const [, startPageTransition] = useTransition();
  const activePage = uiSession.activePage;
  const sidebarCollapsed = uiSession.sidebarCollapsed;
  const setActivePage = (page: PageId) =>
    startPageTransition(() =>
      setUiSession((session) => ({ ...session, activePage: page })),
    );
  const rememberPageOperation = (label: string) => {
    setUiSession((session) =>
      rememberUiSessionOperation(session, activePage, label),
    );
  };
  const recordPageClick = (event: MouseEvent<HTMLElement>) => {
    const label = operationLabelFromTarget(event.target);
    if (label.length > 0) rememberPageOperation(label);
  };
  const recordPageFieldChange = (event: ChangeEvent<HTMLElement>) => {
    const label = editableFieldLabelFromTarget(event.target);
    if (label.length > 0) rememberPageOperation(`编辑：${label}`);
  };

  useEffect(() => {
    persistUiSession(uiSession);
  }, [uiSession]);

  useEffect(() => {
    const lastOperation = uiSession.lastOperationByPage[activePage];
    if (lastOperation !== undefined) {
      setStatusMessage(`上次操作：${lastOperation.label}`);
    }
  }, [activePage, uiSession.lastOperationByPage]);

  useEffect(() => {
    if (activePage === "settings" || activePage === "voice_studio" || activePage === "film_studio") {
      void ensureSettingsLoaded();
    }
  }, [activePage, ensureSettingsLoaded]);

  useEffect(() => () => connectivityController?.dispose(), [connectivityController]);

  // Ctrl+Alt+N toggles the pet companion (mirrors PySide6 shortcuts.py).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void onShortcut((action) => {
      if (action === "pet.toggle") setPetVisible((visible) => !visible);
    }).then((un) => { unlisten = un; });
    return () => { unlisten?.(); };
  }, []);

  useEffect(() => {
    void syncNativeWindowIcon(activeTheme.tokens["brand.logo.accent"]);
  }, [activeTheme]);

  useEffect(() => {
    applyFontPreferences(fontPreferences);
  }, [fontPreferences]);

  useEffect(() => {
    saveLocale(locale);
  }, [locale]);

  useEffect(() => {
    if (settings?.fontPreferences !== undefined) {
      setFontPreferences(settings.fontPreferences);
    }
  }, [settings?.fontPreferences]);

  useEffect(() => {
    pageCanvasRef.current?.scrollTo({ top: 0 });
  }, [activePage]);

  useEffect(() => {
    if (chapterProjectId === null) {
      chapterStudioGenerationRef.current += 1;
      setChapterStudio(null);
      setChapterStudioError(null);
      return;
    }
    const generation = chapterStudioGenerationRef.current + 1;
    chapterStudioGenerationRef.current = generation;
    let cancelled = false;
    setChapterStudio((current) =>
      current?.projectId === chapterProjectId ? current : null,
    );
    setChapterStudioError(null);
    void engineClient
      .getChapterStudio(chapterProjectId)
      .then((nextStudio) => {
        if (cancelled || chapterStudioGenerationRef.current !== generation) return;
        setChapterStudio(nextStudio);
      })
      .catch((error: unknown) => {
        if (cancelled || chapterStudioGenerationRef.current !== generation) return;
        setChapterStudio(null);
        const message =
          error instanceof Error && error.message.length > 0
            ? error.message
            : `无法加载项目 ${chapterProjectId} 的章台数据。`;
        setChapterStudioError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [chapterProjectId, engineClient, chapterStudioReloadToken]);

  // Poll the chapter-studio projection while the page is active so Engine
  // autorun state (studio.autorun) and job activity surface without a manual
  // reload.  The initial-load effect owns the null→data transition; polling
  // only replaces an already-loaded view and never clears it (no
  // LoadingSurface flicker).  Transient poll failures keep the last view.
  useEffect(() => {
    if (activePage !== "chapter_studio" || chapterProjectId === null) return;
    const generation = chapterStudioGenerationRef.current;
    const poller = new ChapterStudioPoller({
      fetchStudio: (projectId) => engineClient.getChapterStudio(projectId),
      onStudio: (nextStudio) => {
        if (chapterStudioGenerationRef.current === generation) {
          setChapterStudio(nextStudio);
        }
      },
    });
    poller.start(chapterProjectId);
    return () => {
      poller.stop();
    };
  }, [activePage, chapterProjectId, chapterStudioReloadToken, engineClient]);

  const retryChapterStudioLoad = useCallback(() => {
    setChapterStudioReloadToken((token) => token + 1);
    setNarrativeToolsReloadToken((token) => token + 1);
  }, []);

  const refreshNarrativeTools = useCallback(() => {
    setNarrativeToolsReloadToken((token) => token + 1);
  }, []);

  const refreshVoiceStudio = useCallback(async (): Promise<void> => {
    if (voiceProjectId === null) return;
    const requestedProjectId = voiceProjectId;
    const requestedChapterNumber = voiceStudio?.projectId === requestedProjectId
      ? voiceStudio.chapterNumber
      : undefined;
    const request = voiceRequestGateRef.current.begin(
      `voice:${requestedProjectId}:${requestedChapterNumber ?? "default"}`,
    );
    const nextStudio = await engineClient.getVoiceStudio(
      requestedProjectId,
      requestedChapterNumber,
    );
    if (
      !voiceRequestGateRef.current.isCurrent(request)
      || activeVoiceProjectIdRef.current !== requestedProjectId
    ) return;
    setVoiceStudio(nextStudio);
  }, [engineClient, voiceProjectId, voiceStudio]);

  const handleVoiceChapterChange = useCallback(async (chapterNumber: number): Promise<void> => {
    if (voiceProjectId === null) return;
    const requestedProjectId = voiceProjectId;
    const request = voiceRequestGateRef.current.begin(`voice:${requestedProjectId}:${chapterNumber}`);
    const nextStudio = await engineClient.getVoiceStudio(
      requestedProjectId,
      chapterNumber,
    );
    if (
      !voiceRequestGateRef.current.isCurrent(request)
      || activeVoiceProjectIdRef.current !== requestedProjectId
    ) return;
    setVoiceStudio(nextStudio);
    setUiSession((session) => session.voiceProjectId === requestedProjectId
      ? { ...session, voiceChapterNumber: chapterNumber }
      : session);
  }, [engineClient, voiceProjectId]);

  useEffect(() => {
    if (!isFilmStudioPage(activePage) || filmProjectId === null) {
      if (filmProjectId === null) {
        filmRequestGateRef.current.invalidate();
        setFilmStudio(null);
      }
      return;
    }
    const request = filmRequestGateRef.current.begin(`film:${filmProjectId}`);
    setFilmStudio(null);
    void Promise.all([
      engineClient.getFilmStudio(filmProjectId),
      engineClient.getFilmProviderCatalog(),
    ]).then(([nextStudio, nextCatalog]) => {
      if (
        !filmRequestGateRef.current.isCurrent(request)
        || activeFilmProjectIdRef.current !== filmProjectId
      ) return;
      setFilmStudio(nextStudio);
      setFilmCatalog(nextCatalog);
    });
    return () => {
      if (filmRequestGateRef.current.isCurrent(request)) filmRequestGateRef.current.invalidate();
    };
  }, [activePage, engineClient, filmProjectId]);

  useEffect(() => {
    if (voiceProjectId === null) {
      voiceRequestGateRef.current.invalidate();
      setVoiceStudio(null);
      return;
    }
    const persistedChapter = uiSession.voiceChapterNumber >= 1 ? uiSession.voiceChapterNumber : undefined;
    const request = voiceRequestGateRef.current.begin(
      `voice:${voiceProjectId}:${persistedChapter ?? "default"}`,
    );
    setVoiceStudio(null);
    void engineClient.getVoiceStudio(voiceProjectId, persistedChapter).then((nextStudio) => {
      if (
        !voiceRequestGateRef.current.isCurrent(request)
        || activeVoiceProjectIdRef.current !== voiceProjectId
      ) return;
      setVoiceStudio(nextStudio);
    });
    return () => {
      if (voiceRequestGateRef.current.isCurrent(request)) voiceRequestGateRef.current.invalidate();
    };
  }, [engineClient, voiceProjectId]);

  const handleOpenProjectReader = (projectId: string) => {
    openProjectReader(projectId);
    // 打开阅读器前手动刷新一次章台投影，保持与 PySide「阅卷」刷新语义一致。
    setChapterStudioReloadToken((token) => token + 1);
    const project = workspace?.projects.find((item) => item.id === projectId);
    setUiSession((session) =>
      rememberUiSessionOperation(
        { ...session, readerProjectId: projectId },
        "projects",
        `打开项目：${project?.title ?? projectId}`,
      ),
    );
    setActivePage("projects");
  };

  const handleVoiceProjectChange = (projectId: string) => {
    if (projectId === voiceProjectId) return;
    activeVoiceProjectIdRef.current = projectId;
    voiceRequestGateRef.current.invalidate();
    setVoiceStudio(null);
    const project = voiceProjects.find((item) => item.id === projectId);
    setUiSession((session) =>
      rememberUiSessionOperation(
        { ...session, voiceProjectId: projectId },
        "voice_studio",
        `切换配音项目：${project?.title ?? projectId}`,
      ),
    );
    setStatusMessage(`正在载入配音项目：${project?.title ?? projectId}`);
  };

  /**
   * Switch the active TTS provider from the top-bar dropdown.
   *
   * Mirrors the PySide `_switch_provider` flow:
   *   1. 乐观更新 voiceStudio.providerLabel so the header reflects the change.
   *   2. Persist `tts-provider` via commandClient.saveSettings.
   *   3. Refresh settings and the studio projection so dependent surfaces
   *      (Platform Settings / model catalog) read the new provider immediately.
   *   4. On failure, roll back the optimistic update and surface a status hint.
   */
  const handleVoiceProviderChange = useCallback(
    async (provider: string) => {
      const current = voiceStudioRef.current;
      const requestedLabel = ttsProviderLabelForId(provider, current?.providerCatalog);
      // Read the latest studio through a ref so back-to-back switches
      // never observe a stale closure (voiceStudioRef is updated on every
      // render above). The functional setVoiceStudio makes the optimistic
      // update idempotent.
      if (current === null || provider === "") return;
      if (ttsProviderKeyFromLabel(current.providerLabel, current.providerCatalog, "") === provider) return;
      const previousLabel = current.providerLabel;
      setVoiceStudio({ ...current, providerLabel: provider });
      try {
        const result = await engineCommandClient.saveSettings({
          kind: "save_settings",
          creationParameters: {
            "tts-provider": provider,
            "tts-enabled": "true",
          },
        });
        // Engine acknowledges by persistence tier rather than a separate
        // "rejected" status; surface any non-success acknowledgement as a
        // failure so the optimistic state can roll back.
        if (result.status !== "saved") {
          throw new Error(result.message);
        }
        setUiSession((session) =>
          rememberUiSessionOperation(
            session,
            "voice_studio",
            `切换配音平台：${requestedLabel}`,
          ),
        );
        setStatusMessage(`已切换至 ${requestedLabel}，声腔页面已立即生效`);
        // Force a fresh studio + settings read so dependent panels
        // (Platform Settings, model catalog) consume the new provider.
        await Promise.all([refreshVoiceStudio(), reloadSettings()]);
        // `refreshVoiceStudio` may surface a stale engine snapshot (e.g.
        // the mock engine ignores `tts-provider` and keeps returning the
        // initial providerLabel). Re-apply the requested label so the
        // header dropdown, the team panel summary and the platform slot
        // stay consistent with the persisted tts-provider.
        setVoiceStudio((current) =>
          current === null
            ? current
            : { ...current, providerLabel: requestedLabel }
        );
        setModelCenterRefreshToken((token) => token + 1);
      } catch (error: unknown) {
        const detail = error instanceof Error && error.message.length > 0
          ? error.message
          : `${requestedLabel} 平台切换失败`;
        setVoiceStudio((current) =>
          current === null || previousLabel === null
            ? current
            : { ...current, providerLabel: previousLabel }
        );
        setStatusMessage(`平台切换未生效：${detail}`);
      }
    },
    [voiceStudioRef, engineCommandClient, refreshVoiceStudio, reloadSettings],
  );

  const handleFilmProjectChange = (projectId: string) => {
    if (projectId === filmProjectId) return;
    activeFilmProjectIdRef.current = projectId;
    filmRequestGateRef.current.invalidate();
    setFilmStudio(null);
    setSelectedFilmProjectId(projectId);
    const project = filmProjects.find((item) => item.id === projectId);
    setUiSession((session) => rememberUiSessionOperation(
      session,
      "film_studio",
      `切换影视项目：${project?.title ?? projectId}`,
    ));
    setStatusMessage(`正在载入影视项目：${project?.title ?? projectId}`);
  };

  const receiveFilmStudio = useCallback((nextStudio: FilmStudioView) => {
    if (activeFilmProjectIdRef.current !== nextStudio.projectId) return;
    setFilmStudio(nextStudio);
  }, []);

  const handleChapterProjectChange = (projectId: string) => {
    if (projectId === chapterProjectId) return;
    setChapterStudio(null);
    setStudioChapterNumber(null);
    const project = longProjects.find((item) => item.id === projectId);
    setUiSession((session) =>
      rememberUiSessionOperation(
        { ...session, chapterProjectId: projectId },
        "chapter_studio",
        `切换章台项目：${project?.title ?? projectId}`,
      ),
    );
    setStatusMessage(`正在载入章台项目：${project?.title ?? projectId}`);
  };

  // 顶栏动作组分支依据：对标 page_binding 的 dashboard selected_project() /
  // projects current_project_id() + snapshot.details（mode + init_resume_available）。
  const focusedProject = useMemo(() => {
    if (workspace === null) return null;
    if (activePage === "dashboard") {
      return (
        workspace.projects.find(
          (project) => project.id === uiSession.dashboardSelectedProjectId,
        ) ??
        workspace.projects[0] ??
        null
      );
    }
    if (activePage === "projects") {
      if (projectReaderId === null) return null;
      return workspace.projects.find((project) => project.id === projectReaderId) ?? null;
    }
    return null;
  }, [workspace, activePage, uiSession.dashboardSelectedProjectId, projectReaderId]);

  const meta = resolvePageMeta(currentMeta(activePage), locale);
  const taskStatus = taskProgressStatus(activePage, jobs, workflow?.runs ?? [], displayedChapterStudio);

  return (
    <LocaleProvider locale={locale}>
    <EngineRuntimeContext.Provider value={engineRuntime}>
    <div
      className={
        sidebarCollapsed ? "nimo-app is-rail-collapsed" : "nimo-app"
      }
      data-page={activePage}
    >
      <aside className="side-rail" aria-label="Main navigation">
        <div className="rail-header">
          <div className="brand-seal" aria-label="NIMO">
            <BrandLogo />
          </div>
          <button
            className="rail-collapse"
            onClick={() =>
              setUiSession((session) => ({
                ...session,
                sidebarCollapsed: !session.sidebarCollapsed,
              }))
            }
            title={
              sidebarCollapsed ? translate(locale, "action.expand") : translate(locale, "action.collapse")
            }
            type="button"
          >
            <Icon name="collapse" size={16} />
          </button>
        </div>
        <div className="rail-brand">
          <strong>NIMO</strong>
          <span>{locale === "zh" ? "—— 叙事工坊 ——" : "— Narrative Forge —"}</span>
          <p>AI-native narrative workspace</p>
        </div>
        <nav className="rail-navigation">
          {sideRailPageMeta.map((item) => {
            const resolved = resolvePageMeta(item, locale);
            return (
              <button
                aria-label={`· ${resolved.label} ·`}
                aria-current={item.id === activePage ? "page" : undefined}
                className={
                  [
                    "rail-nav-item",
                    item.id === activePage ? "is-active" : "",
                    item.id === "settings" ? "is-utility" : "",
                  ].filter(Boolean).join(" ")
                }
                key={item.id}
                onFocus={() => void preloadPage(item.id)}
                onMouseEnter={() => void preloadPage(item.id)}
                onClick={() => setActivePage(item.id)}
                title={resolved.label}
                type="button"
              >
                <span aria-hidden="true" className="rail-nav-label">
                  {formatSideRailLabel(resolved.label)}
                </span>
                <span aria-hidden="true" className="rail-nav-icon">
                  <Icon name={item.icon} size={19} />
                </span>
              </button>
            );
          })}
        </nav>
        <div
          className="rail-footer"
          aria-label={translate(locale, "rail.footer.closing")}
        >
          <span>
            {translate(locale, "rail.footer.saved", { projects: railFooter.projectCount })}
          </span>
          {railFooter.wordCountRaw > 0 ? (
            <small>
              {translate(locale, "rail.footer.flowed", { words: railFooter.wordCount })}
            </small>
          ) : (
            <small>{translate(locale, "rail.footer.noWords")}</small>
          )}
          {railFooter.hasLoadedProviders && railFooter.providerLabel.length > 0 ? (
            <small>
              {translate(locale, "rail.footer.pathway", {
                provider: railFooter.providerLabel,
              })}
            </small>
          ) : (
            <small>{translate(locale, "rail.footer.mock")}</small>
          )}
          <small>{translate(locale, "rail.footer.closing")}</small>
        </div>
      </aside>
      <main className="workspace">
        <div className="content-shell">
          <header
            className="top-bar"
            onChangeCapture={recordPageFieldChange}
            onClickCapture={recordPageClick}
          >
            <div>
              <span className="page-eyebrow">{meta.eyebrow}</span>
              <h1>{meta.title}</h1>
              <p>{meta.subtitle}</p>
            </div>
            <TopBarControls
              activePage={activePage}
              chapterStudioPrimaryLabel={chapterStudioPrimaryLabel}
              focusedProject={focusedProject}
              onNavigate={setActivePage}
              onReadProject={handleOpenProjectReader}
              onImportSettings={() => (document.getElementById("nimo-settings-import-file") as HTMLInputElement | null)?.click()}
              onExportSettings={() => (document.getElementById("nimo-settings-export-trigger") as HTMLButtonElement | null)?.click()}
              onRequestShortTemplateExport={() =>
                setShortTemplateExportRequest((request) => request + 1)
              }
              onSaveSettings={requestSave}
              onStatus={setStatusMessage}
              projectCount={workspace?.metrics.totalProjects ?? 0}
              settingsDirty={settingsDirty}
              settingsReady={settings !== null}
              totalChapters={workspace?.metrics.totalChapters ?? 0}
              totalWords={workspace?.metrics.totalWords ?? 0}
              voiceProjects={voiceProjects}
              voiceProjectId={voiceProjectId}
              voiceStudio={voiceStudio}
              onVoiceProjectChange={handleVoiceProjectChange}
              onVoiceProviderChange={(provider) => void handleVoiceProviderChange(provider)}
              voiceProviderLocked={voiceWorkerBusy}
              workflowMode={workflowMode}
              onWorkflowModeChange={setWorkflowMode}
            />
          </header>
          <section
            className={
              activePage === "projects"
                ? "page-canvas is-reader-canvas"
                : activePage === "voice_studio"
                  ? "page-canvas is-voice-canvas"
                  : activePage === "film_studio"
                    ? "page-canvas is-film-canvas"
                  : "page-canvas"
            }
            onChangeCapture={recordPageFieldChange}
            onClickCapture={recordPageClick}
            ref={pageCanvasRef}
          >
            {connectionState === "disconnected" ? (
              <ConnectionErrorSurface onRetry={retryConnection} />
            ) : connectionState === "incompatible" && negotiation.status === "incompatible" ? (
              <IncompatibleVersionSurface contractVersion={negotiation.contractVersion} expectedMajor={negotiation.expectedMajor} onRetry={retryConnection} />
            ) : workspace === null ||
            (activePage === "workflow" && workflow === null) ||
            (activePage === "chapter_studio" &&
              (chapterStudioError !== null ||
                chapterNarrativeToolsError !== null ||
                displayedChapterStudio === null ||
                chapterNarrativeTools === null)) ||
            (activePage === "voice_studio" && voiceStudio === null) ||
            (isFilmStudioPage(activePage) && (filmStudio === null || filmCatalog === null)) ? (
              <LoadingSurface
                {...(activePage === "chapter_studio"
                  ? buildChapterStudioLoadingState({
                      chapterStudioError,
                      narrativeToolsError: chapterNarrativeToolsError,
                      onRetry: retryChapterStudioLoad,
                    })
                  : {})}
              />
            ) : activePage === "dashboard" ? (
              <DashboardPage
                commandClient={engineCommandClient}
                focusJob={activeJobForStream}
                focusStream={globalStream}
                jobs={jobs}
                onExpandFocus={() => setFocusObservationOpen(true)}
                onNavigate={setActivePage}
                onReadProject={handleOpenProjectReader}
                onWorkspaceRefresh={refreshTaskData}
                {...(settings !== null ? { storageRootLabel: settings.storageRootLabel } : {})}
                workspace={workspace}
                initialFilter={uiSession.dashboardFilter}
                initialProjectOrder={uiSession.dashboardProjectOrder}
                initialSearch={uiSession.dashboardSearch}
                initialSelectedProjectId={uiSession.dashboardSelectedProjectId}
                onFilterChange={(filter) =>
                  setUiSession((session) => ({ ...session, dashboardFilter: filter }))
                }
                onSearchChange={(search) =>
                  setUiSession((session) => ({ ...session, dashboardSearch: search }))
                }
                onProjectOrderChange={(projectOrder) =>
                  setUiSession((session) => ({ ...session, dashboardProjectOrder: projectOrder }))
                }
                onSelectedProjectChange={(projectId) =>
                  setUiSession((session) => ({ ...session, dashboardSelectedProjectId: projectId }))
                }
              />
            ) : activePage === "projects" ? (
              <Suspense fallback={<LoadingSurface />}>
                <LazyProjectsReader
                  engineClient={engineClient}
                  commandClient={engineCommandClient}
                  isLoading={projectReaderId !== null && projectReader === null}
                  narrativeToolsError={readerUsesChapterNarrativeTools ? chapterNarrativeToolsError : readerSpecificNarrativeToolsError}
                  onRefreshNarrativeTools={refreshNarrativeTools}
                  onSelectProject={handleOpenProjectReader}
                  projects={workspace.projects}
                  reader={projectReader}
                  tools={readerNarrativeTools}
                />
              </Suspense>
            ) : activePage === "workflow" ? (
              <Suspense fallback={<LoadingSurface />}>
                <LazyWorkflowPage
                commandClient={engineCommandClient}
                frozenStream={frozenWorkflowStream}
                initialWorkflowDialog={parityFixture.workflowDialog}
                onNavigate={setActivePage}
                onTaskDataChanged={refreshTaskData}
                shortTemplateExportRequest={shortTemplateExportRequest}
                  streamClient={engineClient}
                  workflow={workflow!}
                  workflowMode={workflowMode}
                  onWorkflowModeChange={setWorkflowMode}
                  workspace={workspace}
                />
              </Suspense>
            ) : activePage === "chapter_studio" ? (
              <Suspense fallback={<LoadingSurface />}>
                <LazyChapterStudioPage
                  commandClient={engineCommandClient}
                  engineClient={engineClient}
                  focusJob={activeJobForStream}
                  focusStream={globalStream}
                  initialDialog={parityFixture.chapterStudioDialog}
                  onNavigate={setActivePage}
                  onOpenObservation={() => setFocusObservationOpen(true)}
                  onProjectChange={handleChapterProjectChange}
                  onReadProject={handleOpenProjectReader}
                  onRefresh={retryChapterStudioLoad}
                  onSelectedChapterChange={setStudioChapterNumber}
                  parityState={parityFixture.chapterStudioState}
                  projects={longProjects}
                  studio={displayedChapterStudio!}
                  tools={chapterNarrativeTools!}
                />
              </Suspense>
            ) : activePage === "voice_studio" ? (
              <Suspense fallback={<LoadingSurface />}>
                <LazyVoiceStudioPage
                  catalogClient={engineClient}
                  commandClient={engineCommandClient}
                  creationParameters={settings?.creationParameters}
                  initialDialogFixture={parityFixture.voiceDialog}
                  initialTab={parityFixture.voiceStudioTab ?? uiSession.voiceTab}
                  initialSegmentIndex={uiSession.voiceSegmentIndex}
                  key={`${voiceStudio!.projectId}:${voiceStudio!.chapterNumber}`}
                  modelCenterClient={engineClient}
                  onChapterChange={handleVoiceChapterChange}
                  parityState={parityFixture.voiceStudioState}
                  routingSettings={settings ?? undefined}
                  streamClient={engineClient}
                  studio={voiceStudio!}
                  onRefresh={refreshVoiceStudio}
                  onTabChange={(tab) =>
                    setUiSession((session) => ({ ...session, voiceTab: tab }))
                  }
                  onSegmentIndexChange={(index) =>
                    setUiSession((session) => ({ ...session, voiceSegmentIndex: index }))
                  }
                  onWorkerBusyChange={setVoiceWorkerBusy}
                  modelCenterRefreshToken={modelCenterRefreshToken}
                />
              </Suspense>
            ) : isFilmStudioPage(activePage) && filmStudio !== null && filmCatalog !== null ? (
              <Suspense fallback={<LoadingSurface />}>
                <LazyFilmStudioPage
                  catalog={filmCatalog}
                  commandClient={engineCommandClient}
                  engineClient={engineClient}
                  format={
                    activePage === "drama_studio" ? "drama"
                      : activePage === "comic_studio" ? "comic"
                        : uiSession.filmFormat
                  }
                  onFormatChange={(format: FilmStudioFormatId) =>
                    setUiSession((session) => ({ ...session, filmFormat: format }))
                  }
                  onProjectChange={handleFilmProjectChange}
                  onStudioChange={receiveFilmStudio}
                  projects={filmProjects}
                  studio={filmStudio}
                />
              </Suspense>
            ) : activePage === "settings" ? (
              settings === null || connectivityController === null ? (
                <SettingsLoadingSurface />
              ) : (
                <Suspense fallback={<SettingsLoadingSurface />}>
                  <LazySettingsPage
                    archiveClient={engineClient}
                    commandClient={engineCommandClient}
                    connectivityController={connectivityController}
                    engineClient={engineClient}
                    initialProfileDialogFixture={
                      parityFixture.settingsDialog
                    }
                    initialSection={parityFixture.settingsSection}
                    locale={locale}
                    onDirtyChange={setSettingsDirty}
                    onSettingsPersisted={reloadSettings}
                    onFontPreferencesChange={setFontPreferences}
                    onLocaleChange={setLocale}
                    onPetVisibleChange={(visible) => {
                      setPetVisible(visible);
                      setUiSession((session) => ({ ...session, petVisible: visible }));
                    }}
                    onThemeChange={setTheme}
                    onThemeContrastChange={setThemeContrast}
                    onThemeModeChange={setThemeMode}
                    petVisible={petVisible}
                    saveRequest={settingsSaveRequest}
                    settings={settings}
                    themeContrast={themePreference.contrast}
                    themeId={themeId}
                    themeMode={themePreference.mode}
                  />
                </Suspense>
              )
            ) : (
              <ParityInProgress page={meta} />
            )}
          </section>
        </div>
      </main>
      <footer className="status-bar">
        <EngineRuntimeStatus
          diagnostic={engineDiagnostic}
          onRefresh={() => void refreshEngineDiagnostic()}
        />
        <span>
          {statusMessage === translate(locale, "status.ready")
            ? taskStatus?.label ?? ""
            : statusMessage}
        </span>
        <span>
          {taskStatus === null
            ? ""
            : `${translate(locale, "status.progress")} ${taskStatus.progressPercent}%`}
        </span>
      </footer>
      <PetCompanion
        atlasUrl="/pets/nimo/spritesheet.webp"
        compact
        containerRef={petContainerRef}
        {...(uiSession.petPositionX !== 0 || uiSession.petPositionY !== 0
          ? { initialPosition: { x: uiSession.petPositionX, y: uiSession.petPositionY } }
          : {})}
        jobs={jobs}
        onHideRequested={() => {
          setPetVisible(false);
          setUiSession((session) => ({ ...session, petVisible: false }));
        }}
        onPositionChange={(pos) =>
          setUiSession((session) => ({ ...session, petPositionX: pos.x, petPositionY: pos.y }))
        }
        spriteUrl="/pets/nimo/sprite.png"
        onTaskActivated={(taskId) => {
          setPetStreamTaskId(taskId);
          // Capture the pet's viewport rect so the dialog opens adjacent
          // to the pet rather than at the workspace center.
          const node = petContainerRef.current;
          if (node !== null) {
            const rect = node.getBoundingClientRect();
            setPetAnchor({ left: rect.left, top: rect.top, width: rect.width, height: rect.height });
          } else {
            setPetAnchor(null);
          }
        }}
        visible={petVisible && activePage !== "voice_studio" && !isFilmStudioPage(activePage)}
      />
      {focusObservationOpen && (
        <TaskObservationDialog
          job={activeJobForStream}
          onDecision={submitTaskDecision}
          onClose={() => setFocusObservationOpen(false)}
          source="focus"
          stream={globalStream}
        />
      )}
      {petStreamTaskId !== null && (
        <TaskObservationDialog
          {...(petAnchor === null ? {} : { anchor: petAnchor })}
          onDecision={submitTaskDecision}
          job={petObservationJob}
          onClose={() => {
            setPetStreamTaskId(null);
            setPetAnchor(null);
          }}
          source="companion"
          stream={petStream}
        />
      )}
    </div>
    </EngineRuntimeContext.Provider>
    </LocaleProvider>
  );
}
