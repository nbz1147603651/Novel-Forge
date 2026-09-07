/**
 * LegacyLocalEngineClient — HTTP adapter for the local Python FastAPI backend.
 *
 * Implements the transport-neutral EngineClient and EngineCommandClient
 * contracts by calling the `/api/v1/ui/*` and `/api/v1/engine/*` endpoints
 * exposed by the legacy Python sidecar.
 *
 * Design constraints:
 * - Pages never call HTTP directly; they only see EngineClient/EngineCommandClient.
 * - This adapter maps snake_case JSON → camelCase TypeScript view models.
 * - Timeout: 15s per request, 1 retry on network failure.
 * - Errors are mapped to typed Error instances with meaningful messages.
 */

import type {
  AuthoringMessageRequest,
  AuthoringMessageView,
  AuthoringSessionView,
  AuthoringPolicy,
  AuthoringProposalView,
  AuthoringProposalRequest,
  AuthoringProposalDecision,
  AcceptVoiceTakeCommand,
  AnalyzeVoiceScriptStyleCommand,
  AssignCatalogVoiceCommand,
  AuditBookCommand,
  AuditBookEditorialCommand,
  ApproveCharacterVoiceCommand,
  BuildVoicePreviewPlanCommand,
  ConfirmVoicePreviewCommand,
  GenerateVoicePreviewsCommand,
  BuildVoiceTeamCommand,
  ChapterCommandResult,
  ChapterStudioView,
  CleanChaptersCommand,
  CleanupTestProjectsCommand,
  CleanupTestProjectsResult,
  ClearErrorArchiveCommand,
  ClearErrorArchiveResult,
  ClearClosedTaskErrorsCommand,
  ClearClosedTaskErrorsResult,
  ClearJobHistoryCommand,
  ClearJobHistoryResult,
  AcknowledgeTaskErrorsCommand,
  ReopenTaskErrorsCommand,
  TaskErrorResolutionResult,
  ContinueLongInitCommand,
  RestartLongInitCommand,
  RestartLongInitResult,
  ClearVoiceArtifactsCommand,
  ConfirmVoiceTeamCommand,
  CloneCharacterVoiceCommand,
  DesignCharacterVoiceCommand,
  RebuildNarratorVoiceCommand,
  EngineClient,
  EngineCommandClient,
  ConfigureOllamaRuntimeCommand,
  DeleteProjectsCommand,
  DeleteProjectsResult,
  DeleteOllamaModelCommand,
  ErrorArchiveSummaryView,
  ExecuteGlobalRepairQueueCommand,
  ExtendOutlineCommand,
  ExportBookCommand,
  ExportAudioCommand,
  ExportAudiobookCommand,
  ExportFilmTimelineCommand,
  FilmExportResult,
  FilmGraphRunView,
  FilmGraphView,
  FilmNodeDefinitionView,
  FilmPromptOptimizationView,
  FilmRunEstimateView,
  FilmGraphValidationIssue,
  FilmProviderCatalogView,
  FilmStudioView,
  BootstrapFilmCommand,
  AdvanceFilmCommand,
  GenerateFilmAssetCommand,
  GenerateFilmShotCommand,
  QueryFilmMediaCommand,
  PollFilmShotCommand,
  RerunFilmWorkflowNodeCommand,
  SaveFilmGraphCommand,
  ValidateFilmGraphCommand,
  OptimizeFilmNodePromptCommand,
  EstimateFilmGraphRunCommand,
  CreateFilmGraphRunCommand,
  CancelFilmGraphRunCommand,
  MaterializeFilmMediaCommand,
  QcFilmShotCommand,
  BatchGenerateFilmShotsCommand,
  RenderFilmMasterCommand,
  CancelFilmJobCommand,
  DramaStudioView,
  DramaExportResult,
  PlanDramaCommand,
  ExpandDramaOutlinesCommand,
  WriteDramaScreenplayCommand,
  ExportDramaPackageCommand,
  ComicStudioView,
  ComicAuditResult,
  ComicExportResult,
  PlanComicPagesCommand,
  ExportComicPackageCommand,
  SelectFilmAssetCommand,
  UpdateFilmShotCommand,
  GenerateWorkflowFieldsCommand,
  GenerateWorkflowFieldsResult,
  GenerateNarrativeSubplotsCommand,
  GenerateNarrativeSubplotsResult,
  GenerateVoiceScriptCommand,
  GenerateSoundPaletteCommand,
  JobView,
  OllamaManagerView,
  OllamaRuntimeCommand,
  NarrativeToolsView,
  NarrativeCharacterMutationResult,
  HumanizeLibraryMutationResult,
  MergeHumanizePatternsCommand,
  NarrativeSubplotMutationResult,
  PolishChapterCommand,
  PolishOutlineCommand,
  PrepareChapterCommand,
  PullOllamaModelCommand,
  RepairCausalCommand,
  RepairContinuityCommand,
  RepairApprovalRequest,
  RepairCandidateEditRequest,
  RepairCase,
  RepairCaseDecisionRequest,
  RepairCaseDetailView,
  RepairCaseFilters,
  RepairIssuesCommand,
  RepairManualAnnotationRequest,
  RepairMotifHistoryCommand,
  RepairPublishRequest,
  RepairRecoveryRequest,
  RepairSourceView,
  RepairVerificationRequest,
  ReevaluateChapterCommand,
  ReextractRelationshipsCommand,
  PreviewCharacterVoiceCommand,
  PreviewVoiceSegmentCommand,
  FullVoicePipelineCommand,
  ImportSoundAssetsCommand,
  InitManualRepairView,
  RejectVoiceTakeCommand,
  ReassembleVoiceCommand,
  RebuildMemoryVectorsCommand,
  ResumeJobCommand,
  RetryInitRepairCommand,
  ResolveChapterCheckpointCommand,
  ResolveSpeakersCommand,
  CancelChapterCommand,
  ProjectReaderView,
  RelationshipOverviewView,
  SaveSettingsCommand,
  SaveVoiceGuidanceCommand,
  SaveVoiceScriptCommand,
  SaveSettingsResult,
  SetOllamaModelRolesCommand,
  SaveChapterRevisionCommand,
  SaveChapterRevisionResult,
  GenerateChapterRevisionCandidateCommand,
  GenerateChapterRevisionCandidateResult,
  SaveInitManualRepairCommand,
  SaveInitManualRepairResult,
  SaveNarrativeCharacterCommand,
  SaveNarrativeRelationshipCommand,
  SaveHumanizePatternCommand,
  SaveNarrativeSubplotsCommand,
  RetireNarrativeCharacterCommand,
  RemoveNarrativeRelationshipCommand,
  RemoveHumanizePatternCommand,
  SetHumanizePatternEnabledCommand,
  SaveTokenDashboardPreferencesCommand,
  SaveTokenDashboardPreferencesResult,
  SettingsView,
  StartWorkflowCommand,
  StepArtifactsView,
  DeleteWorkflowPresetCommand,
  SaveWorkflowDraftCommand,
  SaveWorkflowPresetCommand,
  SynthesizeVoiceCommand,
  SyncChapterContractsCommand,
  ConvertNarrativeArcsToSubplotsCommand,
  TestModelProfileCommand,
  TestModelProfileResult,
  TestProjectCleanupPreview,
  TaskStreamEvent,
  TaskStreamListener,
  TaskStreamUnsubscribe,
  TaskStreamView,
  TokenDashboardPreferencesView,
  UpdateVoicePerformanceCommand,
  UpdateSoundAssetCommand,
  VoiceCommandResult,
  VoicePreviewPlanView,
  VoiceAudioModelCenterView,
  VoiceAudioModelOperationRequest,
  VoiceAudioModelOperationView,
  VoiceAudioRuntimeView,
  VoiceCatalogView,
  VoiceStudioView,
  WorkflowCommandResult,
  WorkflowAiHistoryEntry,
  WorkflowDraftView,
  WorkflowFormMode,
  WorkflowPersistenceResult,
  WorkflowPresetRecordView,
  SaveWorkflowAiHistoryCommand,
  WorkflowView,
  WorkspaceView,
} from "@nimo/engine-contracts";

// ── Configuration ─────────────────────────────────────────────────────────────

export interface LegacyClientConfig {
  readonly baseUrl: string;
  readonly timeoutMs?: number;
  readonly maxRetries?: number;
  /** Optional Bearer token for local API authentication (Tauri secure storage). */
  readonly token?: string;
}

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_RETRIES = 1;

// ── Error types ───────────────────────────────────────────────────────────────

export class EngineHttpError extends Error {
  readonly status: number;
  readonly endpoint: string;

  constructor(status: number, endpoint: string, message: string) {
    super(`Engine HTTP ${status} at ${endpoint}: ${message}`);
    this.name = "EngineHttpError";
    this.status = status;
    this.endpoint = endpoint;
  }
}

export class EngineTimeoutError extends Error {
  readonly endpoint: string;

  constructor(endpoint: string, timeoutMs: number) {
    super(`Engine request timed out after ${timeoutMs}ms: ${endpoint}`);
    this.name = "EngineTimeoutError";
    this.endpoint = endpoint;
  }
}

/** A fetch-level failure has no HTTP response, so expose a safe recovery path. */
export class EngineConnectionError extends Error {
  readonly endpoint: string;

  constructor(endpoint: string) {
    super(
      `无法连接本地后端：${endpoint}。请确认后端正在运行；若状态栏提示“后端需安全重启”，请等待当前任务结束后再安全重启。`,
    );
    this.name = "EngineConnectionError";
    this.endpoint = endpoint;
  }
}

// ── snake_case → camelCase mapping ───────────────────────────────────────────

function toCamelCase(str: string): string {
  return str.replace(/_([a-z0-9])/g, (_, char: string) => char.toUpperCase());
}

function toSnakeCase(str: string): string {
  return str.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`);
}

function mapKeys(obj: unknown): unknown {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return obj.map(mapKeys);
  if (typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[toCamelCase(key)] = mapKeys(value);
    }
    return result;
  }
  return obj;
}

/** Convert typed TypeScript command payloads to the Python API's field names. */
function mapKeysToSnakeCase(obj: unknown): unknown {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return obj.map(mapKeysToSnakeCase);
  if (typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      result[toSnakeCase(key)] = mapKeysToSnakeCase(value);
    }
    return result;
  }
  return obj;
}

// ── Client implementation ─────────────────────────────────────────────────────

export class LegacyLocalEngineClient implements EngineClient, EngineCommandClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly token: string | undefined;

  constructor(config: LegacyClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, "");
    this.timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = config.maxRetries ?? DEFAULT_MAX_RETRIES;
    this.token = config.token;
  }

  // ── EngineClient (read path) ──────────────────────────────────────────────

  async getAuthoringSession(projectId: string, chapter = 1): Promise<AuthoringSessionView> {
    return this.get<AuthoringSessionView>(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}?chapter=${chapter}`,
    );
  }

  async getAuthoringProposals(projectId: string): Promise<readonly AuthoringProposalView[]> {
    return this.get(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/proposals`);
  }

  async getAuthoringMessages(projectId: string): Promise<readonly AuthoringMessageView[]> {
    return this.get(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/messages`);
  }

  async getRepairSource(projectId: string, chapter: number): Promise<RepairSourceView> {
    return this.get(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/source?chapter=${chapter}`,
    );
  }

  async listRepairCases(
    projectId: string,
    filters: RepairCaseFilters = {},
  ): Promise<readonly RepairCase[]> {
    const query = new URLSearchParams();
    if (filters.contentType) query.set("content_type", filters.contentType);
    if (filters.status) query.set("status", filters.status);
    if (filters.source) query.set("source", filters.source);
    if (filters.chapter !== undefined) query.set("chapter", String(filters.chapter));
    if (filters.severity) query.set("severity", filters.severity);
    const encoded = query.toString();
    const suffix = encoded ? `?${encoded}` : "";
    return this.get(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs${suffix}`,
    );
  }

  async getRepairCase(projectId: string, caseId: string): Promise<RepairCaseDetailView> {
    return this.get(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}`,
    );
  }

  async sendAuthoringMessage(projectId: string, request: AuthoringMessageRequest): Promise<AuthoringMessageView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/messages`, request);
  }

  async setAuthoringPolicy(projectId: string, policy: AuthoringPolicy, expectedVersion: number): Promise<AuthoringSessionView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/policy`, { policy, expectedVersion });
  }

  async startAuthoringSession(projectId: string, expectedVersion: number, inputVersion: string): Promise<AuthoringSessionView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/start`, { expectedVersion, inputVersion });
  }

  async pauseAuthoringSession(projectId: string): Promise<AuthoringSessionView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/pause`, {});
  }

  async setAuthoringAvailability(projectId: string, enabled: boolean, expectedVersion: number, inputVersion: string): Promise<AuthoringSessionView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/availability`, { enabled, expectedVersion, inputVersion });
  }

  async refreshSemanticConsistency(projectId: string, expectedStoryVersion: string, expectedPolicyVersion: number): Promise<ChapterCommandResult> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/semantic-consistency/refresh`,
      { expectedStoryVersion, expectedPolicyVersion },
    );
  }

  async createAuthoringProposal(projectId: string, request: AuthoringProposalRequest): Promise<AuthoringProposalView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/proposals`, request);
  }

  async decideAuthoringProposal(projectId: string, proposalId: string, request: AuthoringProposalDecision): Promise<AuthoringProposalView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}/decision`, request);
  }

  async applyAuthoringProposal(projectId: string, proposalId: string): Promise<AuthoringProposalView> {
    return this.post(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}/apply`, {});
  }

  async createRepairAnnotation(
    projectId: string,
    request: RepairManualAnnotationRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/annotations`,
      request,
    );
  }

  async saveRepairCandidate(
    projectId: string,
    caseId: string,
    request: RepairCandidateEditRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/candidate`,
      request,
    );
  }

  async verifyRepairCandidate(
    projectId: string,
    caseId: string,
    request: RepairVerificationRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/verify`,
      request,
    );
  }

  async decideRepairCase(
    projectId: string,
    caseId: string,
    request: RepairCaseDecisionRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/decision`,
      request,
    );
  }

  async requestRepairApproval(
    projectId: string,
    caseId: string,
    request: RepairApprovalRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/approval`,
      request,
    );
  }

  async publishRepairCase(
    projectId: string,
    caseId: string,
    request: RepairPublishRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/publish`,
      request,
    );
  }

  async recoverRepairReceipt(
    projectId: string,
    caseId: string,
    request: RepairRecoveryRequest,
  ): Promise<RepairCaseDetailView> {
    return this.post(
      `/api/v1/engine/authoring/${encodeURIComponent(projectId)}/repairs/${encodeURIComponent(caseId)}/recover`,
      request,
    );
  }

  async getWorkspace(): Promise<WorkspaceView> {
    return this.get<WorkspaceView>("/api/v1/ui/workspace");
  }

  async listJobs(): Promise<readonly JobView[]> {
    const response = await this.getRaw("/api/v1/engine/jobs");
    const mapped = mapKeys(response) as { jobs?: JobView[] };
    return mapped.jobs ?? [];
  }

  async getSettings(): Promise<SettingsView> {
    return this.get<SettingsView>("/api/v1/ui/settings");
  }

  async getOllama(): Promise<OllamaManagerView> {
    return this.get<OllamaManagerView>("/api/v1/engine/ollama");
  }

  async getWorkflow(): Promise<WorkflowView> {
    return this.get<WorkflowView>("/api/v1/ui/workflow");
  }

  async getErrorArchiveSummary(): Promise<ErrorArchiveSummaryView> {
    return this.get<ErrorArchiveSummaryView>("/api/v1/engine/error-archive");
  }

  async getInitManualRepair(projectId: string): Promise<InitManualRepairView> {
    return this.get<InitManualRepairView>(
      `/api/v1/engine/projects/${encodeURIComponent(projectId)}/init-manual-repair`,
    );
  }

  async getProjectReader(projectId: string): Promise<ProjectReaderView> {
    return this.get<ProjectReaderView>(`/api/v1/ui/projects/${encodeURIComponent(projectId)}/reader`);
  }

  async getChapterStudio(projectId: string): Promise<ChapterStudioView> {
    return this.get<ChapterStudioView>(
      `/api/v1/engine/novel/projects/${encodeURIComponent(projectId)}/studio`,
    );
  }

  async getVoiceStudio(projectId: string, chapterNumber?: number): Promise<VoiceStudioView> {
    const chapterQuery = chapterNumber === undefined
      ? ""
      : `?chapter_number=${encodeURIComponent(String(chapterNumber))}`;
    const result = await this.get<VoiceStudioView>(
      `/api/v1/engine/voice/projects/${encodeURIComponent(projectId)}/studio${chapterQuery}`,
    );
    const absoluteUrl = (value: string | undefined) =>
      value?.startsWith("/") ? `${this.baseUrl}${value}` : value;
    const { chapterAudioUrl, ...studio } = result;
    return {
      ...studio,
      ...(chapterAudioUrl ? { chapterAudioUrl: absoluteUrl(chapterAudioUrl)! } : {}),
      soundAssets: result.soundAssets.map((asset) => ({
        ...asset,
        audioUrl: absoluteUrl(asset.audioUrl) ?? "",
      })),
    };
  }

  async getFilmStudio(projectId: string): Promise<FilmStudioView> {
    return this.get<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}`,
    );
  }

  async getFilmProviderCatalog(): Promise<FilmProviderCatalogView> {
    return this.get<FilmProviderCatalogView>("/api/v1/film/catalog");
  }

  async getFilmGraph(projectId: string): Promise<FilmGraphView> {
    return this.get<FilmGraphView>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/graph`,
    );
  }

  async getFilmNodeCatalog(): Promise<readonly FilmNodeDefinitionView[]> {
    return this.get<readonly FilmNodeDefinitionView[]>("/api/v1/film/node-catalog");
  }

  async listFilmGraphRuns(projectId: string, limit = 50): Promise<readonly FilmGraphRunView[]> {
    return this.get<readonly FilmGraphRunView[]>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/runs?limit=${encodeURIComponent(String(limit))}`,
    );
  }

  async getFilmGraphRun(projectId: string, runId: string): Promise<FilmGraphRunView> {
    return this.get<FilmGraphRunView>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`,
    );
  }

  async getDramaStudio(projectId: string): Promise<DramaStudioView> {
    return this.get<DramaStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/drama`,
    );
  }

  async getComicStudio(projectId: string): Promise<ComicStudioView> {
    return this.get<ComicStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/comic`,
    );
  }

  async getComicAudit(projectId: string): Promise<ComicAuditResult> {
    return this.get<ComicAuditResult>(
      `/api/v1/film/projects/${encodeURIComponent(projectId)}/comic/audit`,
    );
  }

  async getVoiceCatalog(projectId: string): Promise<VoiceCatalogView> {
    return this.get<VoiceCatalogView>(
      `/api/v1/engine/voice/projects/${encodeURIComponent(projectId)}/catalog`,
    );
  }

  async getVoicePlatformRuntimes(): Promise<readonly VoiceAudioRuntimeView[]> {
    return this.get<readonly VoiceAudioRuntimeView[]>("/api/v1/ui/voice-platform/runtimes");
  }

  async getVoiceAudioModelCenter(): Promise<VoiceAudioModelCenterView> {
    return this.get<VoiceAudioModelCenterView>("/api/v1/ui/voice-platform/model-center");
  }

  async startVoiceAudioModelOperation(
    request: VoiceAudioModelOperationRequest,
  ): Promise<VoiceAudioModelOperationView> {
    return this.post<VoiceAudioModelOperationView>(
      "/api/v1/ui/voice-platform/model-center/operations",
      request,
    );
  }

  async getVoiceAudioModelOperation(
    operationId: string,
  ): Promise<VoiceAudioModelOperationView> {
    return this.get<VoiceAudioModelOperationView>(
      `/api/v1/ui/voice-platform/model-center/operations/${encodeURIComponent(operationId)}`,
    );
  }

  async cancelVoiceAudioModelOperation(
    operationId: string,
  ): Promise<VoiceAudioModelOperationView> {
    return this.post<VoiceAudioModelOperationView>(
      `/api/v1/ui/voice-platform/model-center/operations/${encodeURIComponent(operationId)}/cancel`,
      {},
    );
  }

  async getNarrativeTools(projectId: string): Promise<NarrativeToolsView> {
    return this.get<NarrativeToolsView>(
      `/api/v1/ui/projects/${encodeURIComponent(projectId)}/narrative-tools`,
    );
  }

  async getRelationshipOverview(projectId: string): Promise<RelationshipOverviewView> {
    return this.get<RelationshipOverviewView>(
      `/api/v1/ui/projects/${encodeURIComponent(projectId)}/relationship-overview`,
    );
  }

  async getTaskStream(taskId: string, options?: { readonly afterCursor?: string; readonly limit?: number }): Promise<TaskStreamView> {
    const query = new URLSearchParams();
    if (options?.afterCursor) query.set("after_cursor", options.afterCursor);
    if (options?.limit !== undefined) query.set("limit", String(options.limit));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    const result = await this.get<TaskStreamView>(
      `/api/v1/engine/jobs/${encodeURIComponent(taskId)}/task-stream${suffix}`,
    );
    return result.delivery?.downloadUrl?.startsWith("/")
      ? {
        ...result,
        delivery: {
          ...result.delivery,
          downloadUrl: `${this.baseUrl}${result.delivery.downloadUrl}`,
        },
      }
      : result;
  }

  subscribeTaskStream(taskId: string, listener: TaskStreamListener): TaskStreamUnsubscribe {
    // The durable job SSE channel is the wake-up source. For each wake-up we
    // read the canonical Engine task-stream projection, which preserves the
    // server cursor, content/reasoning segment and attempt metadata. The raw
    // lifecycle SSE endpoint replays on reconnect; cursor dedupe makes that
    // replay safe and avoids fabricating browser-local sequence numbers.
    const url = `${this.baseUrl}/api/v1/jobs/${encodeURIComponent(taskId)}/events`;
    const eventSource = new EventSource(url);
    const seen = new Set<string>();
    let disposed = false;
    let inFlight = false;
    let rerunRequested = false;
    let latestCursor: string | undefined;

    const eventIdentity = (event: TaskStreamEvent) =>
      event.cursor
      ?? `${event.streamId}:${event.sequence}:${event.kind}:${event.segment}:${event.at ?? ""}`;

    const flushSnapshot = async (): Promise<void> => {
      if (disposed) return;
      if (inFlight) {
        rerunRequested = true;
        return;
      }
      inFlight = true;
      try {
        let hasMore = true;
        while (hasMore && !disposed) {
          const snapshot = await this.getTaskStream(
            taskId,
            latestCursor === undefined ? undefined : { afterCursor: latestCursor },
          );
          for (const event of snapshot.events) {
            const identity = eventIdentity(event);
            if (seen.has(identity)) continue;
            seen.add(identity);
            listener(event);
          }
          latestCursor = snapshot.nextCursor
            ?? snapshot.events.at(-1)?.cursor
            ?? latestCursor;
          hasMore = snapshot.hasMore === true && latestCursor !== undefined;
        }
      } catch {
        // The SSE connection remains alive and the next event/reconnect will
        // retry the canonical snapshot without dropping the page session.
      } finally {
        inFlight = false;
        if (rerunRequested && !disposed) {
          rerunRequested = false;
          void flushSnapshot();
        }
      }
    };

    const scheduleSnapshot = () => {
      void flushSnapshot();
    };
    const terminalSnapshot = () => {
      void flushSnapshot().finally(() => eventSource.close());
    };

    for (const eventName of [
      "job_snapshot",
      "job_started",
      "job_step",
      "token_update",
      "section_changed",
      "decision_required",
      "job_paused",
    ]) {
      eventSource.addEventListener(eventName, scheduleSnapshot);
    }
    for (const eventName of ["job_succeeded", "job_failed", "job_cancelled"]) {
      eventSource.addEventListener(eventName, terminalSnapshot);
    }

    eventSource.onerror = () => {
      // Auto-reconnect is handled by EventSource
    };
    void flushSnapshot();

    return () => {
      disposed = true;
      eventSource.close();
    };
  }

  async getStepArtifacts(projectId: string, kind: string, stepKey: string, chapterNumber?: number): Promise<StepArtifactsView> {
    const params = new URLSearchParams({ kind, step_key: stepKey });
    if (chapterNumber !== undefined && chapterNumber > 0) {
      params.set("chapter_number", String(chapterNumber));
    }
    return this.get<StepArtifactsView>(
      `/api/v1/ui/projects/${encodeURIComponent(projectId)}/step-artifacts?${params.toString()}`,
    );
  }

  // ── EngineCommandClient (write path) ──────────────────────────────────────

  async saveSettings(command: SaveSettingsCommand): Promise<SaveSettingsResult> {
    return this.post<SaveSettingsResult>("/api/v1/engine/commands/save-settings", command);
  }

  async deleteProjects(command: DeleteProjectsCommand): Promise<DeleteProjectsResult> {
    return this.post<DeleteProjectsResult>("/api/v1/engine/commands/delete-projects", command);
  }

  async configureOllamaRuntime(command: ConfigureOllamaRuntimeCommand): Promise<OllamaManagerView> {
    return this.post<OllamaManagerView>("/api/v1/engine/commands/configure-ollama-runtime", command);
  }

  async setOllamaModelRoles(command: SetOllamaModelRolesCommand): Promise<OllamaManagerView> {
    return this.post<OllamaManagerView>("/api/v1/engine/commands/set-ollama-model-roles", command);
  }

  async controlOllamaRuntime(command: OllamaRuntimeCommand): Promise<ChapterCommandResult> {
    const paths: Record<OllamaRuntimeCommand["kind"], string> = {
      ensure_ollama_runtime: "/api/v1/engine/commands/ensure-ollama-runtime",
      restart_ollama_runtime: "/api/v1/engine/commands/restart-ollama-runtime",
      stop_ollama_runtime: "/api/v1/engine/commands/stop-ollama-runtime",
    };
    return this.post<ChapterCommandResult>(paths[command.kind], command);
  }

  async pullOllamaModel(command: PullOllamaModelCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/pull-ollama-model", command);
  }

  async deleteOllamaModel(command: DeleteOllamaModelCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/delete-ollama-model", command);
  }

  async saveChapterRevision(command: SaveChapterRevisionCommand): Promise<SaveChapterRevisionResult> {
    return this.post<SaveChapterRevisionResult>(
      "/api/v1/engine/commands/save-chapter-revision",
      command,
    );
  }

  async generateChapterRevisionCandidate(
    command: GenerateChapterRevisionCandidateCommand,
  ): Promise<GenerateChapterRevisionCandidateResult> {
    return this.post<GenerateChapterRevisionCandidateResult>(
      "/api/v1/engine/commands/generate-chapter-revision-candidate",
      command,
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async saveNarrativeSubplots(
    command: SaveNarrativeSubplotsCommand,
  ): Promise<NarrativeSubplotMutationResult> {
    return this.post<NarrativeSubplotMutationResult>(
      "/api/v1/engine/commands/save-narrative-subplots",
      command,
    );
  }

  async saveNarrativeCharacter(
    command: SaveNarrativeCharacterCommand,
  ): Promise<NarrativeCharacterMutationResult> {
    return this.post<NarrativeCharacterMutationResult>(
      "/api/v1/engine/commands/save-narrative-character",
      command,
    );
  }

  async retireNarrativeCharacter(
    command: RetireNarrativeCharacterCommand,
  ): Promise<NarrativeCharacterMutationResult> {
    return this.post<NarrativeCharacterMutationResult>(
      "/api/v1/engine/commands/retire-narrative-character",
      command,
    );
  }

  async saveNarrativeRelationship(
    command: SaveNarrativeRelationshipCommand,
  ): Promise<NarrativeCharacterMutationResult> {
    return this.post<NarrativeCharacterMutationResult>(
      "/api/v1/engine/commands/save-narrative-relationship",
      command,
    );
  }

  async removeNarrativeRelationship(
    command: RemoveNarrativeRelationshipCommand,
  ): Promise<NarrativeCharacterMutationResult> {
    return this.post<NarrativeCharacterMutationResult>(
      "/api/v1/engine/commands/remove-narrative-relationship",
      command,
    );
  }

  async saveHumanizePattern(
    command: SaveHumanizePatternCommand,
  ): Promise<HumanizeLibraryMutationResult> {
    return this.post<HumanizeLibraryMutationResult>(
      "/api/v1/engine/commands/save-humanize-pattern",
      command,
    );
  }

  async setHumanizePatternEnabled(
    command: SetHumanizePatternEnabledCommand,
  ): Promise<HumanizeLibraryMutationResult> {
    return this.post<HumanizeLibraryMutationResult>(
      "/api/v1/engine/commands/set-humanize-pattern-enabled",
      command,
    );
  }

  async removeHumanizePattern(
    command: RemoveHumanizePatternCommand,
  ): Promise<HumanizeLibraryMutationResult> {
    return this.post<HumanizeLibraryMutationResult>(
      "/api/v1/engine/commands/remove-humanize-pattern",
      command,
    );
  }

  async mergeHumanizePatterns(
    command: MergeHumanizePatternsCommand,
  ): Promise<HumanizeLibraryMutationResult> {
    return this.post<HumanizeLibraryMutationResult>(
      "/api/v1/engine/commands/merge-humanize-patterns",
      command,
    );
  }

  async generateNarrativeSubplots(
    command: GenerateNarrativeSubplotsCommand,
  ): Promise<GenerateNarrativeSubplotsResult> {
    return this.post<GenerateNarrativeSubplotsResult>(
      "/api/v1/engine/commands/generate-narrative-subplots",
      command,
      900_000,
    );
  }

  async convertNarrativeArcsToSubplots(
    command: ConvertNarrativeArcsToSubplotsCommand,
  ): Promise<NarrativeSubplotMutationResult> {
    return this.post<NarrativeSubplotMutationResult>(
      "/api/v1/engine/commands/convert-narrative-arcs-to-subplots",
      command,
    );
  }

  async loadTokenDashboardPreferences(projectId: string): Promise<TokenDashboardPreferencesView> {
    return this.get<TokenDashboardPreferencesView>(
      `/api/v1/engine/projects/${encodeURIComponent(projectId)}/token-dashboard-preferences`,
    );
  }

  async saveTokenDashboardPreferences(command: SaveTokenDashboardPreferencesCommand): Promise<SaveTokenDashboardPreferencesResult> {
    return this.post<SaveTokenDashboardPreferencesResult>(
      "/api/v1/engine/commands/save-token-dashboard-preferences",
      command,
    );
  }

  async testModelProfile(command: TestModelProfileCommand): Promise<TestModelProfileResult> {
    return this.post<TestModelProfileResult>("/api/v1/engine/commands/test-model-profile", command);
  }

  async prepareChapter(command: PrepareChapterCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/prepare-chapter", command);
  }

  async cancelChapter(command: CancelChapterCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/cancel-chapter", command);
  }

  async resolveChapterCheckpoint(command: ResolveChapterCheckpointCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/resolve-chapter-checkpoint", command);
  }

  async polishChapter(command: PolishChapterCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/polish-chapter", command);
  }

  async repairContinuity(command: RepairContinuityCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/repair-continuity", command);
  }

  async repairCausal(command: RepairCausalCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/repair-causal", command);
  }

  async repairIssues(command: RepairIssuesCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/repair-issues", command);
  }

  async reevaluateChapter(command: ReevaluateChapterCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/reevaluate-chapter", command);
  }

  async reextractRelationships(command: ReextractRelationshipsCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/reextract-relationships", command);
  }

  async repairMotifHistory(command: RepairMotifHistoryCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/repair-motif-history", command);
  }

  async polishOutline(command: PolishOutlineCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/polish-outline", command);
  }

  async syncChapterContracts(command: SyncChapterContractsCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/sync-chapter-contracts", command);
  }

  async extendOutline(command: ExtendOutlineCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/extend-outline", command);
  }

  async retryPlanningHorizon(projectId: string, currentChapter: number): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(`/api/v1/engine/authoring/${encodeURIComponent(projectId)}/planning/retry`, { currentChapter });
  }

  async auditBook(command: AuditBookCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/audit-book", command);
  }

  async auditBookEditorial(command: AuditBookEditorialCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      "/api/v1/engine/commands/audit-book-editorial",
      command,
    );
  }

  async executeGlobalRepairQueue(command: ExecuteGlobalRepairQueueCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      "/api/v1/engine/commands/execute-global-repair-queue",
      command,
    );
  }

  async exportBook(command: ExportBookCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/export-book", command);
  }

  async cleanChapters(command: CleanChaptersCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>("/api/v1/engine/commands/clean-chapters", command);
  }

  async cancelJob(command: { readonly kind: "cancel_job"; readonly taskId: string; readonly reason?: string }): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      `/api/v1/engine/jobs/${encodeURIComponent(command.taskId)}/cancel`,
      { reason: command.reason ?? "用户已取消" },
    );
  }

  async resumeJob(command: ResumeJobCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      "/api/v1/engine/commands/resume-job",
      command,
    );
  }

  async submitJobDecision(taskId: string, decisionId: string, choice: string, customText: string, approvalVersion: string): Promise<void> {
    await this.post(`/api/v1/jobs/${encodeURIComponent(taskId)}/decision`, { decisionId, choice, customText, approvalVersion });
  }

  async retryInitRepair(command: RetryInitRepairCommand): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      "/api/v1/engine/commands/retry-init-repair",
      command,
    );
  }

  async saveInitManualRepair(
    command: SaveInitManualRepairCommand,
  ): Promise<SaveInitManualRepairResult> {
    return this.post<SaveInitManualRepairResult>(
      "/api/v1/engine/commands/save-init-manual-repair",
      command,
    );
  }

  async rebuildMemoryVectors(
    command: RebuildMemoryVectorsCommand,
  ): Promise<ChapterCommandResult> {
    return this.post<ChapterCommandResult>(
      "/api/v1/engine/commands/rebuild-memory-vectors",
      command,
    );
  }

  async clearJobHistory(command: ClearJobHistoryCommand): Promise<ClearJobHistoryResult> {
    return this.post<ClearJobHistoryResult>(
      "/api/v1/engine/commands/clear-job-history",
      command,
    );
  }

  async acknowledgeTaskErrors(
    command: AcknowledgeTaskErrorsCommand,
  ): Promise<TaskErrorResolutionResult> {
    return this.post<TaskErrorResolutionResult>(
      "/api/v1/engine/commands/acknowledge-task-errors",
      command,
    );
  }

  async reopenTaskErrors(
    command: ReopenTaskErrorsCommand,
  ): Promise<TaskErrorResolutionResult> {
    return this.post<TaskErrorResolutionResult>(
      "/api/v1/engine/commands/reopen-task-errors",
      command,
    );
  }

  async clearClosedTaskErrors(
    command: ClearClosedTaskErrorsCommand,
  ): Promise<ClearClosedTaskErrorsResult> {
    return this.post<ClearClosedTaskErrorsResult>(
      "/api/v1/engine/commands/clear-closed-task-errors",
      command,
    );
  }

  async clearErrorArchive(
    command: ClearErrorArchiveCommand,
  ): Promise<ClearErrorArchiveResult> {
    return this.post<ClearErrorArchiveResult>(
      "/api/v1/engine/commands/clear-error-archive",
      command,
    );
  }

  async previewTestProjectCleanup(): Promise<TestProjectCleanupPreview> {
    return this.get<TestProjectCleanupPreview>("/api/v1/ui/maintenance/test-projects");
  }

  async cleanupTestProjects(
    command: CleanupTestProjectsCommand,
  ): Promise<CleanupTestProjectsResult> {
    return this.post<CleanupTestProjectsResult>(
      "/api/v1/ui/maintenance/test-projects/cleanup",
      command,
    );
  }

  async continueLongInit(command: ContinueLongInitCommand): Promise<WorkflowCommandResult> {
    return this.post<WorkflowCommandResult>(
      "/api/v1/engine/commands/continue-long-init",
      command,
    );
  }

  async restartLongInit(command: RestartLongInitCommand): Promise<RestartLongInitResult> {
    return this.post<RestartLongInitResult>(
      "/api/v1/engine/commands/restart-long-init",
      command,
    );
  }

  async startWorkflow(command: StartWorkflowCommand): Promise<WorkflowCommandResult> {
    return this.post<WorkflowCommandResult>("/api/v1/engine/commands/start-workflow", command);
  }

  async generateWorkflowFields(command: GenerateWorkflowFieldsCommand): Promise<GenerateWorkflowFieldsResult> {
    return this.post<GenerateWorkflowFieldsResult>(
      "/api/v1/engine/commands/generate-workflow-fields",
      {
        ...command,
        focusFields: command.focusFields?.map(toSnakeCase),
      },
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async loadWorkflowDraft(mode: WorkflowFormMode): Promise<WorkflowDraftView> {
    return this.get<WorkflowDraftView>(
      `/api/v1/engine/workflow/drafts/${encodeURIComponent(mode)}`,
    );
  }

  async saveWorkflowDraft(command: SaveWorkflowDraftCommand): Promise<WorkflowPersistenceResult> {
    return this.put<WorkflowPersistenceResult>(
      `/api/v1/engine/workflow/drafts/${encodeURIComponent(command.mode)}`,
      { payload: command.payload, expectedRevision: command.expectedRevision },
    );
  }

  async listWorkflowPresets(mode: WorkflowFormMode): Promise<readonly WorkflowPresetRecordView[]> {
    const response = await this.get<{ readonly presets: readonly WorkflowPresetRecordView[] }>(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(mode)}`,
    );
    return response.presets;
  }

  async saveWorkflowPreset(command: SaveWorkflowPresetCommand): Promise<WorkflowPersistenceResult> {
    return this.put<WorkflowPersistenceResult>(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(command.mode)}/${encodeURIComponent(command.name)}`,
      { payload: command.payload, expectedRevision: command.expectedRevision },
    );
  }

  async deleteWorkflowPreset(command: DeleteWorkflowPresetCommand): Promise<WorkflowPersistenceResult> {
    const query = command.expectedRevision === undefined
      ? ""
      : `?expected_revision=${encodeURIComponent(command.expectedRevision)}`;
    const raw = await this.fetchWithRetry(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(command.mode)}/${encodeURIComponent(command.name)}${query}`,
      { method: "DELETE" },
    );
    return mapKeys(raw) as WorkflowPersistenceResult;
  }

  async listWorkflowAiHistory(mode: WorkflowFormMode, presetName: string): Promise<readonly WorkflowAiHistoryEntry[]> {
    const response = await this.get<{ readonly entries: readonly WorkflowAiHistoryEntry[] }>(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(mode)}/${encodeURIComponent(presetName)}/history`,
    );
    return response.entries;
  }

  async saveWorkflowAiHistory(command: SaveWorkflowAiHistoryCommand): Promise<WorkflowPersistenceResult> {
    return this.put<WorkflowPersistenceResult>(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(command.mode)}/${encodeURIComponent(command.presetName)}/history`,
      {
        operation: command.operation,
        data: command.data,
        hint: command.hint,
        selectedSuggestions: command.selectedSuggestions,
        focusFields: command.focusFields?.map(toSnakeCase),
        metadata: command.metadata,
      },
    );
  }

  async clearWorkflowAiHistory(mode: WorkflowFormMode, presetName: string): Promise<WorkflowPersistenceResult> {
    const raw = await this.fetchWithRetry(
      `/api/v1/engine/workflow/presets/${encodeURIComponent(mode)}/${encodeURIComponent(presetName)}/history`,
      { method: "DELETE" },
    );
    return mapKeys(raw) as WorkflowPersistenceResult;
  }

  async synthesizeVoice(command: SynthesizeVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/synthesize-voice", command);
  }

  async buildVoiceTeam(command: BuildVoiceTeamCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/build-voice-team", command);
  }

  async rebuildNarratorVoice(command: RebuildNarratorVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/rebuild-narrator-voice", command);
  }

  async confirmVoiceTeam(command: ConfirmVoiceTeamCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/confirm-voice-team", command);
  }

  async cloneCharacterVoice(command: CloneCharacterVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/clone-character-voice", command);
  }

  async designCharacterVoice(command: DesignCharacterVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/design-character-voice", command);
  }

  async approveCharacterVoice(command: ApproveCharacterVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/approve-character-voice", command);
  }

  async previewCharacterVoice(command: PreviewCharacterVoiceCommand): Promise<VoiceCommandResult> {
    const result = await this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/preview-character-voice",
      command,
    );
    return result.audioUrl?.startsWith("/")
      ? { ...result, audioUrl: `${this.baseUrl}${result.audioUrl}` }
      : result;
  }

  async buildVoicePreviewPlan(command: BuildVoicePreviewPlanCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/build-voice-preview-plan",
      command,
    );
  }

  async generateVoicePreviews(command: GenerateVoicePreviewsCommand): Promise<VoiceCommandResult> {
    const result = await this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/generate-voice-previews",
      command,
    );
    const payload = result.data as { plan?: VoicePreviewPlanView } | undefined;
    const plan = payload?.plan;
    if (plan === undefined) return result;
    const candidates = plan.candidates.map((candidate) =>
      candidate.audioUrl?.startsWith("/")
        ? { ...candidate, audioUrl: `${this.baseUrl}${candidate.audioUrl}` }
        : candidate,
    );
    return { ...result, data: { ...payload, plan: { ...plan, candidates } } };
  }

  async confirmVoicePreview(command: ConfirmVoicePreviewCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/confirm-voice-preview",
      command,
    );
  }

  async updateVoicePerformance(command: UpdateVoicePerformanceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/update-voice-performance", command);
  }

  async assignCatalogVoice(command: AssignCatalogVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/assign-catalog-voice", command);
  }

  async analyzeVoiceScriptStyle(command: AnalyzeVoiceScriptStyleCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/analyze-voice-script-style",
      command,
    );
  }

  async generateVoiceScript(command: GenerateVoiceScriptCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/generate-voice-script", command);
  }

  async saveVoiceScript(command: SaveVoiceScriptCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/save-voice-script", command);
  }

  async saveVoiceGuidance(command: SaveVoiceGuidanceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/save-voice-guidance", command);
  }

  async previewVoiceSegment(command: PreviewVoiceSegmentCommand): Promise<VoiceCommandResult> {
    const result = await this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/preview-voice-segment",
      command,
    );
    return result.audioUrl?.startsWith("/")
      ? { ...result, audioUrl: `${this.baseUrl}${result.audioUrl}` }
      : result;
  }

  async acceptVoiceTake(command: AcceptVoiceTakeCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/accept-voice-take", command);
  }

  async reassembleVoice(command: ReassembleVoiceCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/reassemble-voice", command);
  }

  async runFullVoicePipeline(command: FullVoicePipelineCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/full-voice-pipeline", command);
  }

  async rejectVoiceTake(command: RejectVoiceTakeCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/reject-voice-take", command);
  }

  async clearVoiceArtifacts(command: ClearVoiceArtifactsCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/clear-voice-artifacts", command);
  }

  async generateSoundPalette(command: GenerateSoundPaletteCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/generate-sound-palette",
      command,
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async updateSoundAsset(command: UpdateSoundAssetCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/update-sound-asset", command);
  }

  async importSoundAssets(command: ImportSoundAssetsCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/import-sound-assets",
      command,
      Math.max(this.timeoutMs, 120_000),
    );
  }

  async resolveSpeakers(command: ResolveSpeakersCommand): Promise<VoiceCommandResult> {
    return this.post<VoiceCommandResult>("/api/v1/engine/commands/resolve-speakers", command);
  }

  async exportAudio(command: ExportAudioCommand): Promise<VoiceCommandResult> {
    const result = await this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/export-audio",
      command,
    );
    return result.downloadUrl?.startsWith("/")
      ? { ...result, downloadUrl: `${this.baseUrl}${result.downloadUrl}` }
      : result;
  }

  async exportAudiobook(command: ExportAudiobookCommand): Promise<VoiceCommandResult> {
    const result = await this.post<VoiceCommandResult>(
      "/api/v1/engine/commands/export-audiobook",
      command,
    );
    return result.downloadUrl?.startsWith("/")
      ? { ...result, downloadUrl: `${this.baseUrl}${result.downloadUrl}` }
      : result;
  }

  async bootstrapFilm(command: BootstrapFilmCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/bootstrap`,
      { mode: command.mode, refreshSources: command.refreshSources },
      Math.max(this.timeoutMs, 120_000),
    );
  }

  async advanceFilm(command: AdvanceFilmCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/advance`,
      { mode: command.mode, useAi: command.useAi, runUntil: command.runUntil },
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async generateFilmAsset(command: GenerateFilmAssetCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/assets/${encodeURIComponent(command.assetId)}/generate`,
      { providerId: command.providerId, modelId: command.modelId, imageCount: command.imageCount },
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async selectFilmAsset(command: SelectFilmAssetCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/assets/${encodeURIComponent(command.assetId)}/select`,
      { url: command.url, lock: command.lock },
    );
  }

  async updateFilmShot(command: UpdateFilmShotCommand): Promise<FilmStudioView> {
    return this.patch<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/shots/${encodeURIComponent(command.shotId)}`,
      { patch: command.patch },
    );
  }

  async generateFilmShot(command: GenerateFilmShotCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/shots/${encodeURIComponent(command.shotId)}/generate`,
      { providerId: command.providerId, modelId: command.modelId },
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async queryFilmMedia(command: QueryFilmMediaCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/media/${encodeURIComponent(command.targetId)}/query`,
      {},
    );
  }

  async pollFilmShot(command: PollFilmShotCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/shots/${encodeURIComponent(command.shotId)}/poll`,
      {},
      Math.max(this.timeoutMs, 1_800_000),
    );
  }

  async rerunFilmWorkflowNode(command: RerunFilmWorkflowNodeCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/workflow/nodes/${encodeURIComponent(command.nodeId)}/rerun`,
      {},
    );
  }

  async saveFilmGraph(command: SaveFilmGraphCommand): Promise<FilmGraphView> {
    return this.put<FilmGraphView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/graph`,
      { expectedRevision: command.expectedRevision, graph: command.graph },
    );
  }

  async validateFilmGraph(command: ValidateFilmGraphCommand): Promise<readonly FilmGraphValidationIssue[]> {
    return this.post<readonly FilmGraphValidationIssue[]>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/graph/validate`,
      { graph: command.graph },
    );
  }

  async optimizeFilmNodePrompt(command: OptimizeFilmNodePromptCommand): Promise<FilmPromptOptimizationView> {
    return this.post<FilmPromptOptimizationView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/graph/prompts/optimize`,
      { node: command.node },
    );
  }

  async estimateFilmGraphRun(command: EstimateFilmGraphRunCommand): Promise<FilmRunEstimateView> {
    return this.post<FilmRunEstimateView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/runs/estimate`,
      { scope: command.scope, targetNodeIds: [...command.targetNodeIds] },
    );
  }

  async createFilmGraphRun(command: CreateFilmGraphRunCommand): Promise<FilmGraphRunView> {
    return this.post<FilmGraphRunView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/runs`,
      {
        scope: command.scope,
        targetNodeIds: [...command.targetNodeIds],
        confirmedCost: command.confirmedCost,
        highPriority: command.highPriority,
      },
    );
  }

  async cancelFilmGraphRun(command: CancelFilmGraphRunCommand): Promise<FilmGraphRunView> {
    return this.post<FilmGraphRunView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/runs/${encodeURIComponent(command.runId)}/cancel`,
      {},
    );
  }

  async exportFilmTimeline(command: ExportFilmTimelineCommand): Promise<FilmExportResult> {
    return this.post<FilmExportResult>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/export-otio`,
      {},
    );
  }

  async materializeFilmMedia(command: MaterializeFilmMediaCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/media/${encodeURIComponent(command.targetId)}/materialize`,
      {},
      Math.max(this.timeoutMs, 900_000),
    );
  }

  async qcFilmShot(command: QcFilmShotCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/shots/${encodeURIComponent(command.shotId)}/qc`,
      {},
      Math.max(this.timeoutMs, 300_000),
    );
  }

  async batchGenerateFilmShots(command: BatchGenerateFilmShotsCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/shots/batch-generate`,
      { shotIds: [...command.shotIds] },
      Math.max(this.timeoutMs, 1_800_000),
    );
  }

  async renderFilmMaster(command: RenderFilmMasterCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/render-master`,
      {
        burnSubtitles: command.burnSubtitles,
        ...(command.width !== undefined ? { width: command.width } : {}),
        ...(command.height !== undefined ? { height: command.height } : {}),
        ...(command.frameRate !== undefined ? { frameRate: command.frameRate } : {}),
      },
      Math.max(this.timeoutMs, 1_800_000),
    );
  }

  async cancelFilmJob(command: CancelFilmJobCommand): Promise<FilmStudioView> {
    return this.post<FilmStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/jobs/${encodeURIComponent(command.jobId)}/cancel`,
      {},
    );
  }

  async planDrama(command: PlanDramaCommand): Promise<DramaStudioView> {
    return this.post<DramaStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/drama/plan`,
      {
        title: command.title,
        totalEpisodes: command.totalEpisodes,
        genre: command.genre,
        logline: command.logline,
        episodeDurationS: command.episodeDurationS,
        characterRoster: [...command.characterRoster],
        language: command.language,
      },
      Math.max(this.timeoutMs, 300_000),
    );
  }

  async expandDramaOutlines(command: ExpandDramaOutlinesCommand): Promise<DramaStudioView> {
    return this.post<DramaStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/drama/outlines`,
      { start: command.start, end: command.end },
      Math.max(this.timeoutMs, 300_000),
    );
  }

  async writeDramaScreenplay(command: WriteDramaScreenplayCommand): Promise<DramaStudioView> {
    return this.post<DramaStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/drama/screenplay`,
      { episodeNumber: command.episodeNumber },
      Math.max(this.timeoutMs, 300_000),
    );
  }

  async exportDramaPackage(command: ExportDramaPackageCommand): Promise<DramaExportResult> {
    return this.post<DramaExportResult>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/drama/export`,
      {},
    );
  }

  async planComicPages(command: PlanComicPagesCommand): Promise<ComicStudioView> {
    return this.post<ComicStudioView>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/comic/plan`,
      { format: command.format },
      Math.max(this.timeoutMs, 300_000),
    );
  }

  async exportComicPackage(command: ExportComicPackageCommand): Promise<ComicExportResult> {
    return this.post<ComicExportResult>(
      `/api/v1/film/projects/${encodeURIComponent(command.projectId)}/comic/export`,
      {},
    );
  }

  // ── Health check ──────────────────────────────────────────────────────────

  async checkHealth(): Promise<boolean> {
    try {
      const result = await this.getRaw("/health");
      return (result as { status?: string }).status === "ok";
    } catch {
      return false;
    }
  }

  // ── Internal fetch helpers ────────────────────────────────────────────────

  private async get<T>(path: string): Promise<T> {
    const raw = await this.getRaw(path);
    return mapKeys(raw) as T;
  }

  private async post<T>(path: string, body: unknown, timeoutMs = this.timeoutMs): Promise<T> {
    const raw = await this.postRaw(path, body, timeoutMs);
    return mapKeys(raw) as T;
  }

  private async put<T>(path: string, body: unknown): Promise<T> {
    const raw = await this.fetchWithRetry(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mapKeysToSnakeCase(body)),
    });
    return mapKeys(raw) as T;
  }

  private async patch<T>(path: string, body: unknown): Promise<T> {
    const raw = await this.fetchWithRetry(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mapKeysToSnakeCase(body)),
    });
    return mapKeys(raw) as T;
  }

  private async getRaw(path: string): Promise<unknown> {
    return this.fetchWithRetry(path, { method: "GET" });
  }

  private async postRaw(path: string, body: unknown, timeoutMs = this.timeoutMs): Promise<unknown> {
    return this.fetchWithRetry(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mapKeysToSnakeCase(body)),
    }, timeoutMs);
  }

  private async fetchWithRetry(
    path: string,
    init: RequestInit,
    timeoutMs = this.timeoutMs,
  ): Promise<unknown> {
    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        return await this.fetchOnce(path, init, timeoutMs);
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
        // Only retry on network errors, not on HTTP errors.
        if (error instanceof EngineHttpError) {
          throw error;
        }
        if (attempt < this.maxRetries) {
          await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
        }
      }
    }

    if (lastError instanceof TypeError) {
      // Tauri/WebKit commonly exposes this as the unhelpful "Load failed".
      // It means no HTTP response was received, not that a chapter stage or
      // its humanization result failed.
      throw new EngineConnectionError(path);
    }
    throw lastError ?? new Error(`Request failed: ${path}`);
  }

  private async fetchOnce(path: string, init: RequestInit, timeoutMs = this.timeoutMs): Promise<unknown> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);

    const headers: Record<string, string> = {};
    if (init.headers) {
      Object.assign(headers, init.headers);
    }
    if (this.token !== undefined) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    try {
      const response = await fetch(url, {
        ...init,
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        let message = response.statusText;
        try {
          const body = await response.json();
          message = (body as { message?: string; detail?: string }).message
            ?? (body as { detail?: string }).detail
            ?? message;
        } catch {
          // Use statusText if body is not JSON.
        }
        throw new EngineHttpError(response.status, path, message);
      }

      return await response.json();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new EngineTimeoutError(path, timeoutMs);
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
}
