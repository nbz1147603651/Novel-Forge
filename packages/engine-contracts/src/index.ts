/**
 * Frontend-facing boundary for the writing engine.
 *
 * React surfaces must only depend on the view models and the EngineClient
 * interface below. A later local Python adapter and a cloud adapter will
 * implement this contract without requiring page-level rewrites.
 */

export type PageId =
  | "dashboard"
  | "projects"
  | "workflow"
  | "settings"
  | "chapter_studio"
  | "voice_studio"
  | "film_studio"
  | "drama_studio"
  | "comic_studio";

export type TaskState = "queued" | "running" | "paused" | "succeeded" | "failed";

/** Credential-free local Engine process state used by the desktop shell. */
export type EngineRuntimeStatus = "ready" | "restartRequired" | "unsupported";

export interface EngineRuntimeView {
  readonly contractVersion: string;
  readonly status: EngineRuntimeStatus;
  readonly bootRevision: string;
  readonly currentRevision: string;
  readonly instanceId: string;
  readonly managedBy: string;
  readonly startedAt: string;
  readonly activeJobCount: number;
  readonly queuedJobCount: number;
  readonly canSubmitTasks: boolean;
}

/**
 * Transport-neutral observations for a task's incremental model output.
 *
 * A future Tauri sidecar, local HTTP bridge, SSE endpoint, or WebSocket
 * adapter can all translate their native event to this boundary.  Pages only
 * ever render this shape; they do not know where the event originated.
 */
export type TaskStreamEventKind = "stream_start" | "delta" | "restart" | "stream_end" | "stream_error" | "validation";
export type TaskStreamSegmentKind = "content" | "reasoning" | "system";
export type TaskStreamValidationStatus = "validating" | "repairing" | "retrying" | "validated" | "failed";

export interface TaskStreamEvent {
  /**
   * Stable Engine-assigned replay cursor. SSE reconnects can replay prior
   * events, so consumers must prefer this over arrival order for deduping.
   */
  readonly cursor?: string;
  readonly streamId: string;
  readonly sequence: number;
  readonly kind: TaskStreamEventKind;
  readonly segment: TaskStreamSegmentKind;
  readonly at?: string;
  readonly modelTaskId?: string;
  readonly attempt?: number;
  readonly text?: string;
  readonly message?: string;
  /**
   * Engine-declared output contract for the event (text | json | report).
   * Lets readers pick the renderer without guessing from partial text.
   */
  readonly outputKind?: string;
  /** Explicit replacement snapshots; delta text must never be deduped by value. */
  readonly textMode?: "delta" | "snapshot";
  readonly textLength?: number;
  readonly textTruncated?: boolean;
  /** Shared across retries of ONE logical call, never just the task type. */
  readonly operationId?: string;
  readonly validationStatus?: TaskStreamValidationStatus;
  readonly repairSource?: string;
  readonly finishReason?: string;
  readonly modelId?: string;
  readonly structuredOutputMode?: string;
  readonly maxAttempts?: number;
}

/**
 * Immutable runtime facts that describe the stream as a whole.
 *
 * Events deliberately stay small and append-only.  Adapters can attach this
 * summary to the replay snapshot without leaking provider clients, prompts,
 * credentials, or transport details into page components.
 */
export interface TaskStreamRuntimeSummary {
  readonly outputKind?: string;
  readonly attempt?: number;
  readonly outputCharacters?: number;
  readonly elapsedMs?: number;
  readonly provider?: string;
  readonly model?: string;
  readonly promptTokens?: number;
  readonly completionTokens?: number;
  readonly totalTokens?: number;
  readonly costUsd?: number;
}

/** A completed Engine-owned delivery artifact, intentionally narrower than a job result. */
export interface TaskDeliveryView {
  readonly filename: string;
  readonly downloadUrl: string;
}

export type TaskModelCallStatus = "running" | "success" | "retrying" | "error";

/**
 * Sanitized lifecycle projection for one logical model call.
 *
 * Request/response bodies and log paths stay behind the Engine boundary. The
 * UI only receives the facts needed to explain which workflow node called
 * which route, how long it took, and what it consumed.
 */
export interface TaskModelCallView {
  readonly callId: string;
  readonly task: string;
  readonly taskLabel: string;
  readonly provider?: string;
  readonly model?: string;
  readonly route?: string;
  readonly status: TaskModelCallStatus;
  readonly event: string;
  readonly attempt?: number;
  readonly maxAttempts?: number;
  readonly maxTokens?: number;
  readonly promptTokens?: number;
  readonly completionTokens?: number;
  readonly totalTokens?: number;
  readonly latencyMs?: number;
  readonly costUsd?: number;
  readonly finishReason?: string;
  readonly willRetry?: boolean;
  readonly startedAt?: string;
  readonly finishedAt?: string;
}

export interface TaskStreamView {
  readonly taskId: string;
  readonly title: string;
  /** Engine-formatted Chinese step label with per-step detail (batch x/y, verdict…). */
  readonly stepLabel: string;
  /** Raw step key; English keyword phase detection (e.g. draft/repair) uses this. */
  readonly stepId?: string;
  readonly status: "streaming" | "paused" | "completed" | "failed";
  readonly progressPercent: number;
  readonly jobState: TaskState;
  readonly error?: { readonly code: string; readonly message: string; readonly retryable: boolean };
  /** Present only for completed Engine-owned delivery jobs. */
  readonly delivery?: TaskDeliveryView;
  readonly summary?: TaskStreamRuntimeSummary;
  /** Logical model calls observed for this workflow, ordered by first start. */
  readonly calls?: readonly TaskModelCallView[];
  readonly events: readonly TaskStreamEvent[];
  /** Cursor of the final event in this page, for incremental recovery. */
  readonly nextCursor?: string;
  /** True when callers should request another page using ``nextCursor``. */
  readonly hasMore?: boolean;
}

export interface TaskStreamSnapshotOptions {
  readonly afterCursor?: string;
  readonly limit?: number;
}

export type TaskStreamUnsubscribe = () => void;
export type TaskStreamListener = (event: TaskStreamEvent) => void;

/**
 * ``subtitle`` marks text-based caption files (.srt/.vtt) that render as plain
 * text; ``binary`` marks non-text assets (audio, archives, office documents)
 * that must not be inlined and need a download surface instead.
 */
export type RenderDocumentFormat =
  | "markdown"
  | "json"
  | "plain_text"
  | "subtitle"
  | "binary";

/** Read-only artifact payload for document dialogs and readers. */
export interface RenderDocumentView {
  readonly title: string;
  readonly sourceLabel: string;
  readonly format: RenderDocumentFormat;
  readonly content: string;
  /** Absolute engine-resolved path; omitted for virtual or cloud-only documents. */
  readonly localPath?: string;
  /** Source byte size when known; the renderer derives UTF-8 size otherwise. */
  readonly byteSize?: number;
  /** Already-localized source modification label, for example `2026-07-26 14:20`. */
  readonly modifiedAtLabel?: string;
}

export interface WorkspaceMetricsView {
  readonly totalProjects: number;
  readonly totalChapters: number;
  readonly totalWords: number;
  readonly configuredProviders: number;
}

export interface ProjectView {
  readonly id: string;
  readonly title: string;
  readonly mode: "short" | "long";
  /**
   * mirrors ProjectDetail.init_resume_available — a long project whose init
   * flow can be resumed (顶栏「继续立项 →」动作组的判据)。
   */
  readonly initResumeAvailable: boolean;
  readonly status: "writing" | "completed" | "planning";
  readonly statusLabel: string;
  readonly progressLabel: string;
  readonly progressPercent: number;
  readonly nextAction: string;
  readonly updatedLabel: string;
  readonly headline: string;
  readonly genre: string;
  readonly tone: string;
  readonly completedChapters: number;
  readonly totalChapters: number | null;
}

/** Health status of a single model profile within a provider group. */
export interface ProviderModelStatusView {
  readonly modelId: string;
  readonly displayName: string;
  /** green = loaded + key set; yellow = key set but not loaded; red = no key */
  readonly health: "green" | "yellow" | "red";
  readonly statusLabel: string;
  readonly supportsThinking: boolean;
  readonly supportsMultiTurn: boolean;
}

/** A provider group with its model status cards (通路点检). */
export interface ProviderGroupView {
  readonly providerId: string;
  readonly providerLabel: string;
  readonly ready: boolean;
  readonly models: readonly ProviderModelStatusView[];
}

export interface WorkspaceView {
  readonly metrics: WorkspaceMetricsView;
  readonly defaultProvider: string;
  readonly projects: readonly ProjectView[];
  /** Provider health groups for the 通路点检 section. */
  readonly providerGroups?: readonly ProviderGroupView[];
}

export interface JobDecisionView {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly decisionId?: string;
  readonly approvalVersion?: string;
  readonly requiresExplicitApproval?: boolean;
}

export interface JobView {
  readonly id: string;
  readonly label: string;
  readonly projectId: string;
  /** System jobs are global Engine work and never belong to a project. */
  readonly scope?: "project" | "system";
  readonly state: TaskState;
  readonly currentStep: string;
  /** Engine-formatted Chinese step label with per-step detail; absent on legacy engines. */
  readonly stepLabel?: string;
  readonly progressPercent: number;
  readonly detail: string;
  /** Decision options when the task is paused awaiting user input. */
  readonly decisions?: readonly JobDecisionView[];
}

export type OllamaRuntimeStatus =
  | "disabled"
  | "starting"
  | "healthy"
  | "degraded"
  | "stopped"
  | "missing_binary"
  | "failed"
  | "external";

export interface OllamaRuntimeStatusView {
  readonly status: OllamaRuntimeStatus;
  readonly version: string;
  readonly detail: string;
}

export interface OllamaSidecarView {
  readonly enabled: boolean;
  readonly autoStart: boolean;
  readonly preferLocal: boolean;
  readonly binaryAvailable: boolean;
}

export interface OllamaStorageView {
  readonly scope: "engine_managed" | "engine_local" | "external";
  readonly displayLabel: string;
  readonly pathRevealCapability: boolean;
}

export interface OllamaCapabilitiesView {
  readonly canEnsure: boolean;
  readonly canRestart: boolean;
  readonly canStop: boolean;
  readonly canPull: boolean;
  readonly canDelete: boolean;
  readonly canConfigurePaths: boolean;
}

export interface OllamaConfiguredRolesView {
  readonly generationModel: string;
  readonly embeddingModel: string;
  readonly managedProfileIds: readonly string[];
}

export interface OllamaModelView {
  readonly name: string;
  readonly size: number;
  readonly modifiedAt: string;
  readonly details: Readonly<Record<string, unknown>>;
  readonly roles: readonly ("generation" | "embedding" | "managed")[];
}

export interface OllamaRoutingImpactView {
  readonly profileIds: readonly string[];
  readonly primaryRouteIds: readonly string[];
  readonly fallbackRouteIds: readonly string[];
  readonly generationSelected: boolean;
  readonly embeddingSelected: boolean;
  /** Short-lived, revision-bound token required for destructive deletion. */
  readonly confirmationToken: string;
}

export interface OllamaOperationView {
  readonly taskId: string;
  readonly kind: string;
  readonly state: TaskState;
  readonly model: string;
  readonly operation: string;
}

export interface OllamaManagerView {
  readonly contractVersion: string;
  readonly revision: string;
  /** The Ollama endpoint belongs to the Engine host, never the browser host. */
  readonly endpointScope: "engine_host" | "external_host";
  readonly ownership: "engine_owned" | "external" | "unavailable";
  readonly runtime: OllamaRuntimeStatusView;
  readonly sidecar: OllamaSidecarView;
  readonly storage: OllamaStorageView;
  readonly capabilities: OllamaCapabilitiesView;
  readonly models: readonly OllamaModelView[];
  readonly configuredRoles: OllamaConfiguredRolesView;
  readonly routingImpactByModel: Readonly<Record<string, OllamaRoutingImpactView>>;
  readonly activeOperations: readonly OllamaOperationView[];
}

export interface OllamaRevisionCommand {
  readonly expectedRevision: string;
  readonly idempotencyKey: string;
}

export interface ConfigureOllamaRuntimeCommand extends OllamaRevisionCommand {
  readonly kind: "configure_ollama_runtime";
  readonly baseUrl?: string;
  readonly enabled?: boolean;
  readonly autoStart?: boolean;
  readonly preferLocal?: boolean;
  /** Accepted only by a loopback Engine request; never returned in reads. */
  readonly binaryPath?: string;
  /** Accepted only by a loopback Engine request; never returned in reads. */
  readonly modelsDir?: string;
}

export interface SetOllamaModelRolesCommand extends OllamaRevisionCommand {
  readonly kind: "set_ollama_model_roles";
  readonly model: string;
  readonly managed: boolean;
  readonly generation: boolean;
  readonly embedding: boolean;
}

export interface OllamaRuntimeCommand extends OllamaRevisionCommand {
  readonly kind: "ensure_ollama_runtime" | "restart_ollama_runtime" | "stop_ollama_runtime";
}

export interface PullOllamaModelCommand extends OllamaRevisionCommand {
  readonly kind: "pull_ollama_model";
  readonly model: string;
}

export interface DeleteOllamaModelCommand extends OllamaRevisionCommand {
  readonly kind: "delete_ollama_model";
  readonly model: string;
  readonly confirmationToken: string;
  readonly cascadeConfiguration: boolean;
}

export interface SettingsView {
  readonly activeThemeId: string;
  /** Shared PySide/React typography preferences. */
  readonly fontPreferences?: FontPreferencesView;
  /**
   * Credential-free id of the default generation profile. A task with an
   * empty ``primaryProfileId`` inherits this profile in the legacy runtime.
   * Optional while read-only legacy Engine adapters are still being adopted.
   */
  readonly defaultProfileId?: string;
  readonly defaultProvider: string;
  readonly storageRootLabel: string;
  readonly mockMode: boolean;
  readonly modelProfiles: readonly ModelProfileView[];
  /** Canonical provider/model catalog shared with the native PySide settings page. */
  readonly modelProviderOptions?: readonly ModelProviderOptionView[] | undefined;
  readonly routingGroups: readonly RoutingGroupView[];
  /** Current creative-temperature jitter settings from the local engine. */
  readonly creativeTemperature?: CreativeTemperatureView;
  /** Runtime creation parameters displayed in the Settings page. */
  readonly creationParameters?: Readonly<Record<string, string>>;
  /** Engine-owned chapter quality/research policy and its stable preset catalog. */
  readonly chapterRuntimePolicy?: ChapterRuntimePolicyView;
}

export type ChapterRuntimePolicyPreset = "compat" | "safe" | "balanced" | "enhanced" | "custom";

export interface ChapterRuntimePolicyValues {
  readonly intentGuardMode: "off" | "warn" | "block";
  readonly factRefreshEnabled: boolean;
  readonly inspirationEnabled: boolean;
  readonly inspirationCooldown: number;
  readonly shortAdaptiveRevisionEnabled: boolean;
  readonly longSingleFinalVerifyEnabled: boolean;
}

export interface ChapterRuntimePolicyPresetView {
  readonly id: Exclude<ChapterRuntimePolicyPreset, "custom">;
  readonly label: string;
  readonly description: string;
  readonly values: ChapterRuntimePolicyValues;
}

export interface ChapterRuntimePolicyView extends ChapterRuntimePolicyValues {
  readonly preset: ChapterRuntimePolicyPreset;
  readonly presetOptions: readonly ChapterRuntimePolicyPresetView[];
}

export interface ChapterRuntimePolicyCommand extends ChapterRuntimePolicyValues {
  readonly preset: ChapterRuntimePolicyPreset;
}

export interface CreativeTemperatureView {
  readonly enabled: boolean;
  readonly scope: "recommended" | "chapter_core" | "init_and_chapter" | "custom";
  readonly downDelta: number;
  readonly upDelta: number;
  readonly customTaskKeys: readonly string[];
}

export type UiFontFamilyId = "source_sans" | "system_sans" | "literary_serif";
export type ReadingFontFamilyId = "source_serif" | "ui_sans" | "calligraphy";

export interface FontPreferencesView {
  readonly uiFamily: UiFontFamilyId;
  readonly readingFamily: ReadingFontFamilyId;
  readonly scale: number;
}

/** Sanitized model configuration. Raw credentials are intentionally absent. */
export interface ModelProfileView {
  readonly id: string;
  readonly label: string;
  readonly provider: string;
  readonly model: string;
  readonly tierLabel: string;
  readonly statusLabel: string;
  readonly supportsThinking: boolean;
  readonly supportsMultiTurn: boolean;
  readonly maskedKey?: string | undefined;
  readonly keyConfigured?: boolean | undefined;
  readonly baseUrl?: string | undefined;
  readonly isEmbedding?: boolean | undefined;
}

export interface ModelProviderOptionView {
  readonly id: string;
  readonly label: string;
  readonly models: readonly string[];
  readonly apiKeyRequired: boolean;
  /** Optional provider-owned OpenAI-compatible endpoint to prefill in the profile editor. */
  readonly defaultBaseUrl?: string | undefined;
  /** Credential or endpoint constraint shown while the provider is selected. */
  readonly connectionHint?: string | undefined;
}

export interface TaskRouteView {
  readonly id: string;
  /** Canonical task key. It is stable across UI labels and future transport. */
  readonly taskKey: string;
  readonly label: string;
  /** Concise explanation shown beneath the task label in the routing matrix. */
  readonly hint: string;
  readonly primaryProfileId: string;
  /** Ordered candidates; only the first three are eligible for automatic failover. */
  readonly fallbackRoutes: readonly TaskRouteFallbackView[];
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
  /** `null` means this task intentionally inherits no route-level temperature control. */
  readonly temperature: number | null;
  /** Whether the temperature is a fixed guardrail or a jitterable creative base value. */
  readonly temperatureKind: "base" | "fixed" | null;
  /** The legacy runtime exposes multi-turn only for a bounded task subset. */
  readonly supportsMultiTurn: boolean;
  /** Used to keep low-frequency but expensive routes discoverable without making them prominent. */
  readonly cadenceLabel?: string;
  /** Protected tasks never receive creative-temperature jitter. */
  readonly temperatureJitterProtected?: boolean;
}

export interface TaskRouteFallbackView {
  readonly profileId: string;
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
}

export interface RoutingSubgroupView {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly routeIds: readonly string[];
}

export interface RoutingGroupView {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly icon?: string;
  readonly subgroups: readonly RoutingSubgroupView[];
  readonly routes: readonly TaskRouteView[];
}

export type WorkflowStageState =
  | "completed"
  | "active"
  | "pending"
  | "failed"
  | "skipped"
  | "blocked"
  | "rolled_back";

export interface WorkflowStageView {
  readonly id: string;
  readonly label: string;
  readonly state: WorkflowStageState;
}

export type WorkflowActualState = "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled" | "timeout";
export type WorkflowQualityStatus = "actual" | "degraded" | "fallback" | "blocked" | "legacy_unknown";
export type WorkflowDerivationStatus = "fresh" | "stale" | "conflict" | "blocked" | "legacy_unknown";

export interface WorkflowRecoveryActionView {
  readonly id: string;
  readonly kind: string;
  readonly label: string;
  /** False means diagnostic guidance only; the client must not invent an action. */
  readonly enabled: boolean;
}

export interface WorkflowCheckpointView {
  /** True only when the Engine found a durable checkpoint on disk. */
  readonly exists: boolean;
  readonly completedStage: string;
  readonly sourceTextHash: string;
  readonly inputSignature: string;
}

/** Tone for detail lines and badges, mirroring PySide6 objectName semantics. */
export type WorkflowDetailTone = "default" | "muted" | "hint" | "success" | "warning" | "danger" | "score" | "scoreFail";

/** One rich detail line in a JobCard footer, mirroring PySide6 detail_lines. */
export interface WorkflowRunDetailLine {
  readonly text: string;
  readonly tone: WorkflowDetailTone;
}

/** A status or replan-reason badge, mirroring PySide6 Badge widget. */
export interface WorkflowRunBadge {
  readonly label: string;
  readonly tone: "default" | "muted" | "success" | "warning" | "danger";
  readonly tooltip?: string;
}

export type RunInsightStatus =
  | "inactive"
  | "pending"
  | "running"
  | "success"
  | "warning"
  | "blocked"
  | "skipped"
  | "rolled_back";

/** Engine-owned explanation of one chapter governance boundary. */
export interface RunInsightView {
  readonly id: "intent" | "research" | "revision" | "final_verify" | string;
  readonly status: RunInsightStatus;
  readonly label: string;
  readonly summary: string;
  readonly detail: string;
  readonly count: number;
  readonly artifactStepKey: string;
}

/** Bounded counters only; prompts, evidence bodies, and private paths stay in Engine. */
export interface RunEfficiencyView {
  readonly llmCalls: number;
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly totalTokens: number;
  readonly costUsd: number;
  readonly researchQueries: number;
  readonly researchCacheHits: number;
  readonly semanticMutations: number;
  readonly reportRefreshes: number;
  readonly repairRounds: number;
  readonly shortRevisionRounds: number;
  readonly rollbacks: number;
  readonly finalHashVerifications: number;
}

export interface WorkflowRunView {
  readonly id: string;
  readonly title: string;
  /** Stable command identity; projectLabel may be localized for display. */
  readonly projectId?: string;
  readonly projectLabel: string;
  readonly elapsedLabel: string;
  readonly progressPercent: number;
  readonly stateLabel: string;
  readonly currentStageLabel: string;
  /** Engine-formatted Chinese step label with per-step detail; absent on legacy engines. */
  readonly stepLabel?: string;
  readonly validationLabel: string;
  readonly activityLabel: string;
  readonly stages: readonly WorkflowStageView[];
  /**
   * Job kind mirroring `DesktopJobRecord.kind` (e.g. "init_long",
   * "run_chapter", "prepare_chapter", "short"). Drives the action-bar
   * branch selection in `JobCard._build_action_bar`.
   */
  readonly kind: string;
  /**
   * Engine-computed availability of init repair actions, mirroring
   * `_init_repair_actions_available`: kind == "init_long" AND failed AND
   * error markers match AND manual repair artifacts exist on disk.
   * When true the card renders "AI修复" + "人工修复" buttons.
   */
  readonly initRepairAvailable: boolean;
  /**
   * Whether a chapter review checkpoint (review_progress.json) exists on
   * disk, mirroring the `has_checkpoint` probe in `_build_action_bar`.
   * When true a failed chapter-kind run renders "🔄 断点续写".
   */
  readonly hasCheckpoint: boolean;
  /**
   * Chapter context for chapter-scoped workflow artifacts.  The task card
   * forwards this to `getStepArtifacts` so paths such as
   * `chapters/chapter_{ch}.md` resolve to the actual chapter rather than an
   * unresolved template path.
   */
  readonly chapterNumber?: number;

  // ── Engine-owned workflow lineage and recovery (v2) ─────────────

  /** Version of the read-model projector, independent from pipeline versions. */
  readonly workflowProjectionVersion?: string;
  /** Domain workflow version recorded in the signed artifact manifest. */
  readonly workflowVersion?: string;
  /** Current semantic stage id from the Engine-owned stage sequence. */
  readonly stageId?: string;
  /** Raw durable task state; preferred over localized stateLabel. */
  readonly actualState?: WorkflowActualState;
  readonly inputSignature?: string;
  readonly outputVersion?: number;
  readonly parentArtifactVersions?: Readonly<Record<string, number>>;
  readonly templateVersion?: string;
  readonly configFingerprint?: string;
  readonly modelFingerprint?: string;
  readonly qualityStatus?: WorkflowQualityStatus;
  readonly degradationReason?: string;
  readonly derivationStatus?: WorkflowDerivationStatus;
  readonly retryable?: boolean;
  readonly recoveryActions?: readonly WorkflowRecoveryActionView[];
  readonly checkpoint?: WorkflowCheckpointView;
  readonly staleDependencies?: readonly string[];
  readonly cumulativeTokens?: number;
  readonly cumulativeCostUsd?: number;
  readonly runInsights?: readonly RunInsightView[];
  readonly efficiency?: RunEfficiencyView;

  // ── Rich detail extensions (Phase B) ─────────────────────────────

  /**
   * Rich detail lines for the card footer, mirroring PySide6 detail_lines.
   * Ordered by priority: error > step summary > checkpoint > auxiliary > etc.
   */
  readonly detailLines?: readonly WorkflowRunDetailLine[];
  /**
   * Status and replan-reason badges shown in the card header,
   * mirroring PySide6 _job_badge_spec + _replan_reason_badge_specs.
   */
  readonly badges?: readonly WorkflowRunBadge[];
  /**
   * Seconds since job creation, for live elapsed timer.
   * When present, the card renders a ticking MM:SS / HH:MM:SS label.
   */
  readonly elapsedSeconds?: number;
  /**
   * Whether this card is in historical compact mode (132px, 2 detail lines),
   * mirroring PySide6 JobCard._historical.
   */
  readonly historical?: boolean;
  /**
   * Whether the run was cancelled by user (as opposed to a true failure),
   * mirroring PySide6 _is_cancelled_job.
   */
  readonly isCancelled?: boolean;
  /**
   * Creation timestamp ISO string for elapsed timer computation.
   */
  readonly createdAt?: string;
}

export interface WorkflowFocusView {
  readonly kindLabel: string;
  readonly statusLabel: string;
  readonly title: string;
  readonly summary: string;
  readonly fragment: string;
}

/** A selected-field preview returned by the Engine-backed creative assistant. */
export interface WorkflowPresetDiffView {
  readonly id: string;
  readonly key: string;
  readonly label: string;
  readonly oldValue: string;
  readonly newValue: string;
}

/** A sanitized, read-only workflow diagnostic entry. */
export interface WorkflowErrorLogEntryView {
  readonly id: string;
  readonly timeLabel: string;
  readonly jobLabel: string;
  readonly taskId: string;
  readonly taskLabel: string;
  readonly attemptLabel: string;
  readonly errorMessage: string;
  readonly excerpt: string;
  readonly logPath: string;
  readonly kindLabel: string;
  readonly autoResolved: boolean;
  /** Engine-persisted time at which an author confirmed review, if any. */
  readonly acknowledgedAt?: string;
  readonly causeCode?: string;
  readonly autoRepairState?:
    | "resolved"
    | "retryable"
    | "blocked"
    | "exhausted"
    | "rolled_back"
    | "not_applicable"
    | "unknown";
  readonly autoRepairExplanation?: string;
  readonly recommendedAction?: string;
  readonly recoveryActionKinds?: readonly string[];
}

export interface WorkflowView {
  readonly errorCount: number;
  readonly errorLog: readonly WorkflowErrorLogEntryView[];
  readonly runs: readonly WorkflowRunView[];
  readonly focus: WorkflowFocusView;
}

/** Engine-owned editor view for a blocked long-initialization artifact. */
export interface InitManualRepairLocationView {
  readonly pointer: string;
  readonly label: string;
  readonly confidence: "exact" | "weak" | string;
  readonly excerpt: string;
}

export interface InitManualRepairIssueView {
  readonly title: string;
  readonly summary: string;
  readonly locations: readonly InitManualRepairLocationView[];
}

export interface InitManualRepairView {
  readonly projectId: string;
  readonly available: boolean;
  readonly artifact: "blueprint" | "outline" | "chapter_contracts" | "";
  readonly artifactLabel: string;
  readonly artifactPath: string;
  readonly payload: Readonly<Record<string, unknown>> | null;
  readonly revision: string;
  readonly summary: string;
  readonly issues: readonly InitManualRepairIssueView[];
}

export interface ProjectReaderFactView {
  readonly label: string;
  readonly value: string;
}

export interface ProjectReaderArtifactView {
  readonly id: string;
  readonly label: string;
  readonly caption: string;
  readonly sourceLabel: string;
  /** Engine-resolved source file; absent only when the source is virtual. */
  readonly sourceLocalPath?: string;
  /** Real source metadata, deliberately separate from the compact reader excerpt. */
  readonly sourceByteSize?: number;
  readonly sourceModifiedAtLabel?: string;
  /** Content hash supplied by the Engine for optimistic concurrency checks. */
  readonly sourceRevision?: string;
  readonly freshness?: "current" | "stale" | "unverified";
  readonly freshnessMessage?: string;
  readonly paragraphs: readonly string[];
  readonly facts: readonly ProjectReaderFactView[];
  /**
   * Full raw source text for lossless rendering via ContentRenderer.
   * Absent when the source is oversized or the adapter only supplies excerpts;
   * consumers then fall back to the compact `paragraphs`/`facts` summary.
   */
  readonly content?: string;
  /** Document format of `content`; only meaningful when `content` is present. */
  readonly format?: RenderDocumentFormat;
}

export interface ProjectReaderChapterView {
  readonly number: number;
  readonly title: string;
  /** Final prose, process draft only, or an outline entry not yet written. */
  readonly state: "final" | "draft" | "pending";
  /** Counted from the currently readable prose file, never an outline estimate. */
  readonly wordCount?: number;
}

export interface ProjectReaderTabView {
  readonly id: string;
  readonly label: string;
  /** Present on the long-form chapter tab; mirrors the PySide outline rail. */
  readonly chapters?: readonly ProjectReaderChapterView[];
  readonly artifacts: readonly ProjectReaderArtifactView[];
}

export interface ProjectReaderView {
  readonly projectId: string;
  readonly projectTitle: string;
  readonly modeLabel: string;
  readonly updatedLabel: string;
  readonly tabs: readonly ProjectReaderTabView[];
}

export type ChapterStudioChapterState = "completed" | "current" | "pending" | "needs_decision";

export interface ChapterStudioChapterView {
  readonly number: number;
  readonly title: string;
  readonly state: ChapterStudioChapterState;
  readonly detail: string;
}

export interface ChapterStudioMemoryView {
  readonly label: string;
  readonly value: string;
  readonly detail: string;
}

/** Stable, presentation-ready tabs for the Chapter Studio memory inspector. */
export type ChapterMemoryTabId = "overview" | "motifs" | "relationships" | "issues" | "reading_power" | "guardrails" | "control";

export interface ChapterMemoryCardView {
  readonly id: string;
  readonly label: string;
  readonly content: string;
  readonly tone?: "default" | "highlight" | "warning";
}

export interface ChapterMemoryTabView {
  readonly id: ChapterMemoryTabId;
  readonly label: string;
  readonly cards: readonly ChapterMemoryCardView[];
}

/** Current chapter activity is a transport-neutral read model, not a UI timer. */
export type ChapterStudioActivityState = "idle" | "running" | "checkpoint";

export interface ChapterStudioCheckpointOptionView {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly recommended: boolean;
}

export interface ChapterStudioCheckpointView {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly prompt: string;
  readonly options: readonly ChapterStudioCheckpointOptionView[];
}

export interface ChapterStudioActivityRunView {
  readonly kind: string;
  readonly taskId: string;
  readonly status: "queued" | "running" | "paused" | "succeeded" | "failed";
  readonly taskLabel: string;
  readonly currentStepLabel: string;
  readonly progressPercent: number | null;
  readonly updatedAt: string;
  readonly stages: readonly WorkflowStageView[];
}

export interface ChapterStudioActivityView {
  /** Workflow identity for stage rendering and matching the global status. */
  readonly kind?: string;
  readonly taskId?: string;
  readonly state: ChapterStudioActivityState;
  readonly taskLabel: string;
  readonly currentStepLabel: string;
  /** Latest Engine model/validation action; never inferred from a UI timer. */
  readonly operationDetail?: string;
  readonly progressPercent: number | null;
  readonly checkpoint: ChapterStudioCheckpointView | null;
  readonly stages: readonly WorkflowStageView[];
  readonly runInsights?: readonly RunInsightView[];
  readonly efficiency?: RunEfficiencyView;
  /** Canonical six-phase chapter journey; raw attempts remain in history. */
  readonly chapterFlow?: ChapterStudioActivityRunView | null;
  /** Prior same-chapter task projections, matching PySide's historical cards. */
  readonly history?: readonly ChapterStudioActivityRunView[];
}

export type BookAutorunStatus =
  | "idle"
  | "waiting_init"
  | "running"
  | "retry_wait"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

/** Durable Engine orchestration state shared by every Chapter Studio client. */
export interface BookAutorunView {
  readonly status: BookAutorunStatus;
  readonly phase: string;
  readonly mode: "chapter" | "book";
  readonly startChapter: number;
  readonly currentChapter: number;
  readonly endChapter: number;
  readonly totalChapters: number;
  readonly completedChapters: readonly number[];
  readonly activeTaskId: string;
  readonly checkpoint: ChapterStudioCheckpointView | null;
  readonly checkpointAttempts: number;
  readonly checkpointBudget: number;
  readonly totalFailures: number;
  readonly failureBudget: number;
  readonly nextRetryAt: string;
  /** Environment wait, not a failed generation attempt; resumed after Engine restart. */
  readonly waitReason?: "" | "engine_restart_required";
  readonly lastError: string;
  /** Engine-owned failure classification; clients must not parse error prose. */
  readonly lastFailureKind?: string;
  /** Recovery ownership for the last failure. Manual forbids generic Plan retry. */
  readonly recoveryTarget?: "" | "plan" | "draft" | "wave" | "manual" | "semantic";
  readonly updatedAt: string;
}

export interface ChapterStudioView {
  readonly projectId: string;
  readonly projectTitle: string;
  readonly projectSynopsis: string;
  readonly nextChapter: number;
  readonly totalChapters: number;
  readonly chapters: readonly ChapterStudioChapterView[];
  readonly planTitle: string;
  readonly planSummary: string;
  readonly previousSummary: string;
  readonly previousExitSummary: string;
  readonly currentGoal: string;
  readonly currentOutlineSummary: string;
  readonly nextGoal: string;
  readonly suggestion: string;
  readonly memories: readonly ChapterStudioMemoryView[];
  readonly memoryTabs: readonly ChapterMemoryTabView[];
  readonly activity: ChapterStudioActivityView;
  readonly autorun: BookAutorunView;
  readonly taskErrorLog: readonly WorkflowErrorLogEntryView[];
}

export interface CharacterProfileView {
  readonly id: string;
  readonly name: string;
  readonly role: string;
  readonly statusLabel: string;
  readonly summary: string;
  readonly arc: string;
}

export interface RelationshipLinkView {
  readonly id: string;
  readonly fromCharacterId: string;
  readonly toCharacterId: string;
  readonly typeLabel: string;
  readonly evidence: string;
}

/** Extended read-only detail used by the character-profile workbench. */
export interface CharacterDetailView {
  readonly characterId: string;
  readonly timelineLabel: string;
  readonly ageLabel: string;
  readonly genderLabel: string;
  readonly occupation: string;
  readonly personality: string;
  readonly backstory: string;
  readonly abilities: string;
  readonly appearance: string;
  readonly arc: string;
  readonly voice: string;
  readonly notes: string;
}

export type NarrativePhaseTone = "opening" | "rising" | "climax" | "resolution";

export interface NarrativePhaseView {
  readonly id: string;
  readonly label: string;
  readonly chapterStart: number;
  readonly chapterEnd: number;
  readonly tensionLabel: string;
  readonly summary: string;
  readonly tone: NarrativePhaseTone;
}

export interface NarrativeMilestoneView {
  readonly id: string;
  readonly chapter: number;
  /** Full turning-point copy for hover and the expanded detail view. */
  readonly description?: string;
  readonly label: string;
}

export interface NarrativeSubplotEventView {
  readonly id: string;
  readonly chapter: number;
  /** Full event copy for hover and the expanded detail view. */
  readonly description?: string;
  readonly label: string;
  readonly emphasis?: "normal" | "turning" | "reveal";
}

export interface NarrativeSubplotLaneView {
  readonly id: string;
  readonly label: string;
  readonly tone: "jade" | "blue" | "red" | "violet";
  readonly events: readonly NarrativeSubplotEventView[];
}

export type NarrativeWeaveLinkType = "feed_main" | "reveal_key" | "theme_echo" | "trigger_start" | "trigger_turn";

/** A typed cross-track relationship; it is not inferred from display labels. */
export interface NarrativeWeaveLinkView {
  readonly id: string;
  readonly sourceLaneId: string;
  readonly target: "mainline" | "subplot";
  readonly targetLaneId?: string;
  readonly chapter: number;
  readonly type: NarrativeWeaveLinkType;
  readonly label: string;
  readonly description: string;
}

export interface NarrativeArcMilestoneView {
  readonly chapterStart: number;
  readonly chapterEnd: number;
  readonly description: string;
}

/** Persisted, editor-ready data for one subplot plan. */
export interface NarrativeSubplotEventInput {
  readonly chapterNumber: number;
  readonly event: string;
  readonly weaveNotes: string;
  readonly dependsOn: readonly string[];
}

/** A typed weaving relationship owned by a subplot plan. */
export interface NarrativeSubplotWeaveLinkInput {
  readonly sourceType: string;
  readonly sourceRef: string;
  readonly targetSubplot: string;
  readonly triggerChapter: number;
  readonly linkType: string;
  readonly description: string;
}

/**
 * Full subplot plan.  This is deliberately separate from the compact timeline
 * lane: the editor must preserve node events and weaving links when a user
 * changes a title, range, or resolution target.
 */
export interface NarrativeSubplotInput {
  readonly name: string;
  readonly description: string;
  readonly involvedChapters: readonly number[];
  readonly chapterEvents: readonly NarrativeSubplotEventInput[];
  readonly weaveLinks: readonly NarrativeSubplotWeaveLinkInput[];
  readonly priority: "primary" | "normal" | "background";
  readonly resolutionChapter: number;
  readonly resolutionTarget: string;
  readonly resolutionType: "" | "resolve" | "reveal" | "ascend" | "merge";
}

export interface NarrativeCharacterArcView {
  readonly id: string;
  readonly character: string;
  readonly arcSummary: string;
  readonly milestones: readonly NarrativeArcMilestoneView[];
}

/** Structured visual material for the timeline and relationship renderers. */
export interface NarrativeVisualizationView {
  readonly totalChapters: number;
  readonly phases: readonly NarrativePhaseView[];
  readonly milestones: readonly NarrativeMilestoneView[];
  readonly subplotLanes: readonly NarrativeSubplotLaneView[];
  readonly weaveLinks: readonly NarrativeWeaveLinkView[];
  /** Character-arc lanes (PySide6 角色弧光); optional for adapters without blueprint arcs. */
  readonly characterArcs?: readonly NarrativeCharacterArcView[];
}

export interface OutlineNodeView {
  readonly id: string;
  /**
   * mirrors outline chapter `chapter_number` — 供大纲工作台选择计数标签
   * 「已选 X/Y 章 · 第 … 章」折叠展示章节号（对标 _format_selected_chapters）。
   */
  readonly chapterNumber: number;
  readonly chapterLabel: string;
  readonly title: string;
  readonly summary: string;
  readonly stateLabel: string;
  /** Optional for older engines; current projections preserve source lists. */
  readonly goal?: string;
  readonly beatsSummary?: readonly string[];
  readonly mainPlotPoints?: readonly string[];
  readonly subplotPoints?: readonly string[];
  readonly sceneDesignGoals?: readonly string[];
  readonly notes?: string;
  readonly facts?: readonly { readonly label: string; readonly value: string }[];
}

export interface SubplotView {
  readonly id: string;
  readonly title: string;
  readonly priorityLabel: string;
  readonly chaptersLabel: string;
  readonly description: string;
  readonly resolution: string;
  /** Full source-shaped plan for the editable subplot workbench. */
  readonly plan: NarrativeSubplotInput;
}

export interface HumanizePatternView {
  readonly id: string;
  readonly name: string;
  readonly sourceLabel: string;
  readonly category: string;
  readonly severity: string;
  readonly hitCount: number;
  readonly lastChapterLabel: string;
  readonly enabled: boolean;
  /** Full editor fields; older Engine versions may omit them. */
  readonly keywords?: readonly string[];
  readonly notes?: string;
  readonly examplePhrase?: string;
}

export interface RevisionCandidateView {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly impactLabel: string;
}

/** One relationship snapshot at a chapter (PySide6 RelationshipSnapshot). */
export interface RelationshipSnapshotView {
  readonly chapter: number;
  readonly status: string;
  readonly trust: number;
  readonly tension: number;
  readonly shift: string;
}

/** Evolution of one character pair across chapters (PySide6 RelationshipTimeline). */
export interface RelationshipTimelineView {
  readonly pairId: string;
  readonly characterA: string;
  readonly characterB: string;
  readonly currentStatus: string;
  readonly currentTrust: number;
  readonly currentTension: number;
  readonly snapshots: readonly RelationshipSnapshotView[];
}

/** Relationship evolution overview (PySide6 RelationshipOverview). */
export interface RelationshipOverviewView {
  readonly totalRelationships: number;
  readonly highTensionPairs: readonly string[];
  readonly timelines: readonly RelationshipTimelineView[];
}

/** Secondary narrative workbench data, deliberately separate from prose. */
export interface NarrativeToolsView {
  readonly characters: readonly CharacterProfileView[];
  readonly characterDetails: readonly CharacterDetailView[];
  readonly relationships: readonly RelationshipLinkView[];
  readonly visualization: NarrativeVisualizationView;
  /** Relationship evolution across chapters (PySide6 关系追踪 source); optional. */
  readonly relationshipOverview?: RelationshipOverviewView;
  readonly outline: readonly OutlineNodeView[];
  /** Book goal and planning coverage are independent of the number of visible outlines. */
  readonly planning?: {
    readonly totalChapters: number;
    readonly hardThroughChapter: number;
    readonly plannedThroughChapter: number;
    readonly archivedChapters?: number;
    readonly task?: { readonly jobId?: string; readonly status: string; readonly error?: string; readonly chapterNumber?: number; readonly targetChapter?: number } | null;
  };
  readonly subplots: readonly SubplotView[];
  readonly humanizePatterns: readonly HumanizePatternView[];
  readonly revisionCandidates: readonly RevisionCandidateView[];
  /** Content hash used to prevent an editor from overwriting a newer blueprint. */
  readonly blueprintRevision?: string;
  /** Content hash used to protect character and relationship source mutations. */
  readonly characterRevision?: string;
  /** Version of the global HumanizeLibrary used by its optimistic write commands. */
  readonly humanizeLibraryRevision?: string;
}

export interface VoiceCastMemberView {
  readonly id: string;
  readonly name: string;
  readonly role: string;
  readonly statusLabel: string;
  readonly voiceLabel: string;
  readonly description: string;
  /** Stable provider voice identifier, when the role has one. */
  readonly voiceId?: string;
  /** Human-readable provenance, such as system catalog or reference clone. */
  readonly voiceSourceLabel?: string;
  /** Persisted, provider-neutral character performance offsets. */
  readonly speedOffset?: number;
  readonly pitchOffset?: number;
  readonly volumeOffset?: number;
  /** Explains whether the shown offsets are automatic or author overrides. */
  readonly performancePolicyLabel?: string;
  /** Compact, auditable voice-match status for the detail panel. */
  readonly matchSummary?: string;
  /** Bounded facts from the persisted voiceprint contract. */
  readonly detailFacts?: readonly VoiceDetailFactView[];
  /** Human-readable reasons that support the current casting decision. */
  readonly matchReasons?: readonly string[];
  /** Items the author must listen for before approving the voice. */
  readonly auditionWarnings?: readonly string[];
  /** The identity preview line already stored for this voice, when available. */
  readonly auditionText?: string;
  /** Auditable design or clone brief, safe to show and edit from the studio. */
  readonly designBrief?: string;
}

export interface VoiceDetailFactView {
  readonly label: string;
  readonly value: string;
}

export interface VoiceCatalogOptionView {
  readonly id: string;
  readonly label: string;
  readonly description: string;
}

export interface VoiceCatalogView {
  readonly projectId: string;
  readonly providerLabel: string;
  readonly options: readonly VoiceCatalogOptionView[];
}

export interface VoiceScriptSegmentView {
  readonly id: string;
  readonly segmentIndex: number;
  readonly speakerId: string;
  readonly speakerLabel: string;
  readonly kindLabel: string;
  readonly emotionLabel: string;
  readonly content: string;
  readonly statusLabel: string;
  readonly needsSpeakerReview: boolean;
  readonly contextBefore?: string;
  readonly contextAfter?: string;
  readonly speakerCandidates?: readonly VoiceSpeakerCandidateView[];
}

export interface VoiceSpeakerCandidateView {
  readonly characterId: string;
  readonly characterName: string;
  readonly confidence: number;
  readonly reason: string;
}

export interface VoiceSoundAssetView {
  readonly id: string;
  readonly kind: "bgm" | "soundscape" | "sfx";
  readonly name: string;
  readonly status: "approved" | "pending" | "rejected";
  readonly scope: "project" | "application";
  readonly source: "imported" | "generated" | "manual";
  readonly tags: readonly string[];
  readonly provider: string;
  readonly model: string;
  readonly license: string;
  readonly commercialUseStatus: "cleared" | "review_required" | "restricted";
  readonly prompt: string;
  readonly audioUrl: string;
}

export interface VoiceMixTrackView {
  readonly id: string;
  readonly label: string;
  readonly durationMs: number;
  readonly eventCount: number;
  readonly failedEventCount: number;
  readonly status: "ready" | "blocked" | "not_started";
  readonly stemAudioUrl: string;
}

export interface VoiceProviderSettingOptionView {
  readonly value: string;
  readonly label: string;
}

export interface VoiceProviderSettingView {
  readonly parameterId: string;
  readonly label: string;
  readonly description: string;
  readonly kind: "boolean" | "number" | "path" | "secret" | "select" | "text";
  readonly defaultValue: string;
  readonly options: readonly VoiceProviderSettingOptionView[];
  readonly modelPurpose: "formal" | "preview" | "clone" | "design" | null;
  readonly minimum: number | null;
  readonly maximum: number | null;
  readonly step: number | null;
  readonly advanced: boolean;
  readonly experimental: boolean;
  readonly visibleModelIds: readonly string[];
  readonly visibilityParameterId: string;
  readonly visibilityValues: readonly string[];
}

export interface VoiceProviderModelView {
  readonly id: string;
  readonly label: string;
  readonly purposes: readonly ("formal" | "preview" | "clone" | "design")[];
  readonly features: readonly string[];
  readonly voiceClone: boolean;
  readonly voiceDesign: boolean;
  readonly systemVoiceCatalog: boolean;
  readonly localReferenceAudio: boolean;
  readonly adapterSupported: boolean;
  readonly recommended: boolean;
  readonly maxInputChars: number;
  readonly notes: readonly string[];
}

export interface VoiceProviderView {
  readonly id: string;
  readonly label: string;
  readonly aliases: readonly string[];
  readonly officialDocsUrl: string;
  readonly defaultModel: string;
  readonly activeModelParameterId: string;
  readonly isLocal: boolean;
  readonly adapterStatus: "production" | "preview" | "mock";
  readonly highlights: readonly string[];
  readonly models: readonly VoiceProviderModelView[];
  readonly settings: readonly VoiceProviderSettingView[];
}

/** Durable Voice Room review state projected from the Engine take manifest. */
export interface VoiceRoomTakeView {
  readonly segmentIndex: number;
  readonly state: "guidance_saved" | "candidate" | "accepted";
  readonly takeId?: string;
  readonly audioUrl?: string;
  /** Durable, Engine-normalized performance guidance for the segment. */
  readonly guidance?: Readonly<Record<string, unknown>>;
}

/** Resumable Voice Studio job categories, normalized from Engine job kinds. */
export type VoiceActiveTaskKind = "team_build" | "script_generation" | "synthesis";

export interface VoiceActiveTaskView {
  readonly id: string;
  readonly kind: VoiceActiveTaskKind;
}

export interface VoiceStudioView {
  readonly projectId: string;
  readonly projectTitle: string;
  readonly providerLabel: string;
  readonly configuredModelLabel: string;
  /** Optional only for legacy hosts; current Engine responses always include it. */
  readonly providerCatalog?: readonly VoiceProviderView[];
  readonly chapterNumber: number;
  readonly availableChapters: readonly number[];
  readonly teamConfirmed: boolean;
  readonly scriptFresh: boolean;
  /** Current novel-final authority and downstream media lineage. Optional for older hosts. */
  readonly novelSourceState?: "ready" | "blocked" | "missing";
  readonly scriptFreshness?: "missing" | "current" | "stale" | "legacy" | "source_missing";
  readonly audioFreshness?: "missing" | "current" | "stale" | "legacy" | "source_missing";
  readonly freshnessBlockingReasons?: readonly string[];
  readonly unresolvedSpeakerCount: number;
  readonly audioReady: boolean;
  readonly subtitleReady: boolean;
  readonly deliveryState: "not_started" | "in_progress" | "blocked" | "ready";
  readonly activeTaskId: string | null;
  /** All active durable voice tasks; replaces the former synthesis-only recovery hint. */
  readonly activeTasks?: readonly VoiceActiveTaskView[];
  readonly chapterAudioUrl?: string;
  readonly subtitleText: string;
  readonly mixTracks: readonly VoiceMixTrackView[];
  readonly soundAssets: readonly VoiceSoundAssetView[];
  readonly cast: readonly VoiceCastMemberView[];
  readonly script: readonly VoiceScriptSegmentView[];
  /** Authoritative persisted Voice Room states, restored after refresh or restart. */
  readonly roomTakes: readonly VoiceRoomTakeView[];
}

/** One application-managed audio model, projected from the shared model center. */
export interface VoiceAudioModelView {
  readonly id: string;
  readonly name: string;
  readonly roles: readonly string[];
  readonly state: "external" | "incomplete" | "installed" | "not_installed";
  readonly stateLabel: string;
  readonly family: string;
  readonly recommendedFor: string;
  readonly detail: string;
  readonly compatible: boolean;
  readonly compatibilityReason: string;
  readonly installedSizeBytes: number;
  readonly estimatedDownloadBytes: number;
  readonly runtimeStatus: "healthy" | "unavailable" | "unchecked";
  readonly runtimeVersion: string;
  readonly installedRevision: string;
  readonly localPath: string;
  readonly projectReferences: readonly string[];
  readonly requiresLicenseAcceptance: boolean;
  readonly licenseName: string;
  readonly licenseUrl: string;
  readonly licenseAccepted: boolean;
  readonly selfTestPassed: boolean | null;
}

/** Health snapshot for one application-managed audio sidecar. */
export interface VoiceAudioRuntimeView {
  readonly id: string;
  readonly name: string;
  readonly detail: string;
  readonly state: "failed" | "incompatible" | "installed" | "not_installed" | "running" | "stopped";
  readonly status: string;
  readonly version: string;
  readonly targetVersion: string;
  readonly managedProcess: boolean;
  readonly rollbackAvailable: boolean;
  readonly lastError: string;
}

/** Repository capacity plus the catalog that PySide and React share. */
export interface VoiceAudioModelCenterView {
  readonly installedModelCount: number;
  readonly modelCount: number;
  readonly repository: {
    readonly root: string;
    readonly sizeBytes: number;
    readonly freeBytes: number;
    readonly rollbackAvailable: boolean;
  };
  readonly models: readonly VoiceAudioModelView[];
  readonly runtimes: readonly VoiceAudioRuntimeView[];
}

export type VoiceAudioOperationKind =
  | "accept_license"
  | "delete"
  | "install"
  | "install_runtime"
  | "reconcile_runtime"
  | "rollback_runtime"
  | "self_test"
  | "start_runtime"
  | "stop_runtime"
  | "upgrade_runtime";

export interface VoiceAudioModelOperationRequest {
  readonly targetKind: "model" | "runtime";
  readonly targetId: string;
  readonly operation: VoiceAudioOperationKind;
  /** Sent for this download only; never returned by the Engine. */
  readonly token?: string;
  readonly acceptLicense?: boolean;
  readonly force?: boolean;
}

/** Cancellable, in-process model-center lifecycle operation. */
export interface VoiceAudioModelOperationView {
  readonly id: string;
  readonly targetKind: "model" | "runtime";
  readonly targetId: string;
  readonly operation: VoiceAudioOperationKind;
  readonly status: "cancelled" | "completed" | "failed" | "queued" | "running";
  readonly message: string;
  /** 0–100; -1 means the provider cannot determine a percentage. */
  readonly percent: number;
}

export interface StepArtifactFile {
  readonly label: string;
  readonly path: string;
  readonly content: string;
  readonly format: RenderDocumentFormat;
  /** Present only for chapter-prose Markdown artifacts. */
  readonly wordCount?: number;
}

export interface StepArtifactsView {
  readonly projectId: string;
  readonly kind: string;
  readonly stepKey: string;
  readonly artifacts: readonly StepArtifactFile[];
  readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
  readonly emptyHint?: string;
}

/** A reviewed test directory or empty generated run trace eligible for cleanup. */
export interface TestProjectCleanupCandidate {
  readonly projectId: string;
  readonly reason: "generated_test_name" | "empty_generated_run";
  readonly fileCount: number;
  readonly sizeBytes: number;
}

export interface TestProjectCleanupPreview {
  readonly candidates: readonly TestProjectCleanupCandidate[];
  readonly message: string;
}

export interface CleanupTestProjectsCommand {
  readonly projectIds: readonly string[];
}

export interface CleanupTestProjectsResult extends TestProjectCleanupPreview {
  readonly removedProjectIds: readonly string[];
  readonly skippedProjectIds: readonly string[];
}

export type FilmStageId =
  | "planning"
  | "screenplay"
  | "visual_development"
  | "storyboard"
  | "shot_production"
  | "sound_picture"
  | "edit"
  | "compliance"
  | "delivery";

export type FilmStageStatus = "pending" | "ready" | "active" | "review" | "completed" | "blocked";
export type FilmProductionMode = "collaborative" | "autonomous";
export type FilmGraphRunStatus =
  | "queued"
  | "running"
  | "waiting_confirmation"
  | "waiting_human"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "skipped"
  | "blocked";
export type FilmGraphRunScope = "selected" | "to_node" | "downstream" | "all";

export interface FilmGraphPositionView {
  readonly x: number;
  readonly y: number;
}

export interface FilmGraphViewportView extends FilmGraphPositionView {
  readonly zoom: number;
}

export interface FilmNodePortView {
  readonly portId: string;
  readonly label: string;
  readonly artifactType: string;
  readonly required: boolean;
  readonly multiple: boolean;
}

export type FilmPromptSource = "novel" | "voice" | "h3_guide" | "workflow" | "user";

export interface FilmPromptSectionView {
  readonly sectionId: string;
  readonly label: string;
  readonly guidance: string;
  readonly content: string;
  readonly required: boolean;
  readonly editable: boolean;
  readonly sources: readonly FilmPromptSource[];
}

export interface FilmNodePromptView {
  readonly schemaVersion: "1.0";
  readonly templateId: string;
  readonly templateVersion: string;
  readonly purpose: string;
  readonly sections: readonly FilmPromptSectionView[];
  readonly userNotes: string;
  readonly updatedAt: string;
}

export interface FilmPromptOptimizationView {
  readonly prompt: FilmNodePromptView;
  readonly renderedPrompt: string;
  readonly changes: readonly string[];
  readonly warnings: readonly string[];
  readonly characterCount: number;
}

export interface FilmGraphNodeView {
  readonly nodeId: string;
  readonly typeId: string;
  readonly typeVersion: string;
  readonly label: string;
  readonly stage: FilmStageId;
  readonly position: FilmGraphPositionView;
  readonly groupId: string;
  readonly config: Readonly<Record<string, unknown>>;
  readonly prompt: FilmNodePromptView;
  readonly inputPorts: readonly FilmNodePortView[];
  readonly outputPorts: readonly FilmNodePortView[];
  readonly providerId: string;
  readonly modelId: string;
  readonly bypassed: boolean;
  readonly disabled: boolean;
  readonly humanCheckpoint: boolean;
  readonly estimatedCostUsd: number;
}

export interface FilmGraphEdgeView {
  readonly edgeId: string;
  readonly sourceNodeId: string;
  readonly sourcePortId: string;
  readonly targetNodeId: string;
  readonly targetPortId: string;
}

export interface FilmGraphGroupView {
  readonly groupId: string;
  readonly label: string;
  readonly color: string;
  readonly collapsed: boolean;
}

export interface FilmGraphDefinitionView {
  readonly schemaVersion: "2.0";
  readonly graphId: string;
  readonly projectId: string;
  readonly revision: number;
  readonly nodes: readonly FilmGraphNodeView[];
  readonly edges: readonly FilmGraphEdgeView[];
  readonly groups: readonly FilmGraphGroupView[];
  readonly viewport: FilmGraphViewportView;
  readonly updatedAt: string;
}

export interface FilmGraphValidationIssue {
  readonly issueId: string;
  readonly code: string;
  readonly message: string;
  readonly severity: "error" | "warning";
  readonly nodeId: string;
  readonly edgeId: string;
}

export interface FilmNodeDefinitionView {
  readonly typeId: string;
  readonly version: string;
  readonly label: string;
  readonly description: string;
  readonly category: string;
  readonly stage: FilmStageId;
  readonly inputPorts: readonly FilmNodePortView[];
  readonly outputPorts: readonly FilmNodePortView[];
  readonly configSchema: Readonly<Record<string, unknown>>;
  readonly defaultConfig: Readonly<Record<string, unknown>>;
  readonly promptTemplate: FilmNodePromptView;
  readonly permissions: readonly string[];
  readonly providerCapabilities: readonly string[];
  readonly cacheable: boolean;
  readonly allowsBypass: boolean;
  readonly paid: boolean;
  readonly humanCheckpoint: boolean;
  readonly estimatedCostUsd: number;
  readonly estimatedDurationS: number;
}

export interface FilmNodeAttemptView {
  readonly attemptId: string;
  readonly runId: string;
  readonly nodeId: string;
  readonly attempt: number;
  readonly status: FilmGraphRunStatus;
  readonly inputSignature: string;
  readonly providerTaskId: string;
  readonly artifactUris: readonly string[];
  readonly costUsd: number;
  readonly errorCode: string;
  readonly errorMessage: string;
  readonly startedAt: string;
  readonly finishedAt: string;
}

export interface FilmRunEstimateView {
  readonly graphRevision: number;
  readonly scope: FilmGraphRunScope;
  readonly targetNodeIds: readonly string[];
  readonly executionNodeIds: readonly string[];
  readonly cachedNodeIds: readonly string[];
  readonly estimatedCostUsd: number;
  readonly estimatedDurationS: number;
  readonly missingInputs: readonly string[];
  readonly validationIssues: readonly FilmGraphValidationIssue[];
  readonly requiresConfirmation: boolean;
}

export interface FilmGraphRunView {
  readonly runId: string;
  readonly projectId: string;
  readonly graphId: string;
  readonly graphRevision: number;
  readonly scope: FilmGraphRunScope;
  readonly targetNodeIds: readonly string[];
  readonly highPriority: boolean;
  readonly status: FilmGraphRunStatus;
  readonly confirmedCost: boolean;
  readonly estimate: FilmRunEstimateView;
  readonly attempts: readonly FilmNodeAttemptView[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface FilmGraphView {
  readonly definition: FilmGraphDefinitionView;
  readonly validationIssues: readonly FilmGraphValidationIssue[];
  readonly latestRun?: FilmGraphRunView | null;
}

export interface FilmArtifactSourceView {
  readonly artifactType: string;
  readonly relativePath: string;
  readonly revision: string;
  readonly exists: boolean;
  readonly fieldsUsed: readonly string[];
}

export interface FilmCharacterView {
  readonly characterId: string;
  readonly name: string;
  readonly role: string;
  readonly dramaticFunction: string;
  readonly age: string;
  readonly gender: string;
  readonly personality: string;
  readonly arc: string;
  readonly screenIdentity: {
    readonly facialAnchors: readonly string[];
    readonly silhouette: string;
    readonly bodyLanguage: string;
    readonly costumePalette: readonly string[];
    readonly signatureProps: readonly string[];
    readonly continuityRules: readonly string[];
    readonly forbiddenDrift: readonly string[];
    readonly referenceAssetUrls: readonly string[];
  };
  readonly voicePerformance: {
    readonly provider: string;
    readonly voiceId: string;
    readonly modelId: string;
    readonly timbre: string;
    readonly vocalRegister: string;
    readonly cadence: string;
    readonly accent: string;
    readonly emotionRange: readonly string[];
    readonly pronunciationNotes: readonly string[];
    readonly deliveryRules: readonly string[];
    readonly referenceAudioPath: string;
  };
}

export interface FilmLocationView {
  readonly locationId: string;
  readonly name: string;
  readonly dramaticFunction: string;
  readonly geography: string;
  readonly era: string;
  readonly spatialLayout: string;
  readonly materials: readonly string[];
  readonly practicalLights: readonly string[];
  readonly weatherStates: readonly string[];
  readonly recurringProps: readonly string[];
  readonly ambientSound: readonly string[];
  readonly continuityRules: readonly string[];
  readonly referenceAssetUrls: readonly string[];
}

export interface FilmProductionBibleView {
  readonly schemaVersion: string;
  readonly projectId: string;
  readonly title: string;
  readonly logline: string;
  readonly format: string;
  readonly language: string;
  readonly targetAudience: string;
  readonly productionIntent: string;
  readonly sources: readonly FilmArtifactSourceView[];
  readonly characters: readonly FilmCharacterView[];
  readonly locations: readonly FilmLocationView[];
  readonly style: {
    readonly visualThesis: string;
    readonly genre: string;
    readonly tone: string;
    readonly aspectRatio: string;
    readonly frameRate: number;
    readonly colorScript: readonly string[];
    readonly lightingRules: readonly string[];
    readonly lensLanguage: readonly string[];
    readonly cameraRules: readonly string[];
    readonly textureMedium: string;
    readonly negativeStyleRules: readonly string[];
    readonly soundThesis: string;
    readonly musicThesis: string;
  };
  readonly worldRules: readonly string[];
  readonly themes: readonly string[];
  readonly continuityRules: readonly string[];
  readonly updatedAt: string;
}

export interface FilmScreenplaySceneView {
  readonly sceneId: string;
  readonly sequenceNumber: number;
  readonly heading: string;
  readonly locationId: string;
  readonly timeOfDay: string;
  readonly characters: readonly string[];
  readonly objective: string;
  readonly conflict: string;
  readonly turn: string;
  readonly visualHook: string;
  readonly soundHook: string;
  readonly durationS: number;
  readonly sourceChapter?: number | null;
  readonly sourceSceneRef: string;
  readonly lines: readonly {
    readonly kind: string;
    readonly speaker: string;
    readonly text: string;
    readonly performanceNote: string;
    readonly sourceRef: string;
  }[];
}

export interface FilmVisualAssetView {
  readonly assetId: string;
  readonly assetType: string;
  readonly name: string;
  readonly subjectId: string;
  readonly view: string;
  readonly prompt: string;
  readonly negativePrompt: string;
  readonly referenceUrls: readonly string[];
  readonly candidates: readonly string[];
  readonly selectedUrl: string;
  readonly providerId: string;
  readonly modelId: string;
  readonly providerTask?: FilmProviderTaskView | null;
  readonly qcStatus: string;
  readonly locked: boolean;
}

export interface FilmProviderTaskView {
  readonly providerId: string;
  readonly modelId: string;
  readonly mode: string;
  readonly state: "pending" | "running" | "succeeded" | "failed";
  readonly taskId: string;
  readonly fileId: string;
  readonly assetUrls: readonly string[];
  readonly errorMessage: string;
}

export interface FilmShotView {
  readonly shotId: string;
  readonly sceneId: string;
  readonly shotNumber: number;
  readonly title: string;
  readonly durationS: number;
  readonly language: {
    readonly shotSize: string;
    readonly cameraAngle: string;
    readonly cameraMotion: string;
    readonly lighting: string;
    readonly emotion: string;
    readonly time: string;
    readonly lensMm: number;
    readonly composition: string;
    readonly focusStrategy: string;
  };
  readonly action: string;
  readonly dialogue: string;
  readonly soundDesign: string;
  readonly transition: string;
  readonly prompt: string;
  readonly negativePrompt: string;
  readonly characterIds: readonly string[];
  readonly locationId: string;
  readonly identityReferenceUrls: readonly string[];
  readonly styleReferenceUrls: readonly string[];
  readonly referenceVideoUrls: readonly string[];
  readonly referenceAudioUrls: readonly string[];
  readonly firstFrameUrl: string;
  readonly lastFrameUrl: string;
  readonly generationParams: Readonly<Record<string, unknown>>;
  readonly selectedAssetUrl: string;
  readonly candidates: readonly string[];
  readonly providerId: string;
  readonly modelId: string;
  readonly generationMode: string;
  readonly providerTask?: FilmProviderTaskView | null;
  readonly qcStatus: string;
  readonly qcNotes: readonly string[];
  readonly locked: boolean;
}

export interface FilmMediaArtifactView {
  readonly artifactId: string;
  readonly kind: "image" | "video" | "audio" | "subtitle";
  readonly subjectRef: string;
  readonly sourceUrl: string;
  readonly localPath: string;
  readonly checksum: string;
  readonly sizeBytes: number;
  readonly width: number;
  readonly height: number;
  readonly durationS: number;
  readonly fps: number;
  readonly codec: string;
  readonly hasAudio: boolean;
  readonly providerId: string;
  readonly taskId: string;
  readonly createdAt: string;
}

export interface FilmQcCheckView {
  readonly name: string;
  readonly status: "pass" | "warn" | "fail";
  readonly detail: string;
  readonly metric?: number | null;
}

export interface FilmQcReportView {
  readonly targetId: string;
  readonly targetType: string;
  readonly passed: boolean;
  readonly checks: readonly FilmQcCheckView[];
  readonly createdAt: string;
}

export interface FilmVisionDimensionView {
  readonly dimension: string;
  readonly score: number;
  readonly note: string;
}

export interface FilmVisionQcReportView {
  readonly targetId: string;
  readonly targetType: string;
  readonly passed: boolean;
  readonly overallScore: number;
  readonly dimensions: readonly FilmVisionDimensionView[];
  readonly retriesUsed: number;
  readonly summary: string;
  readonly createdAt: string;
}

export type FilmJobKind =
  | "generate_asset"
  | "generate_shot"
  | "query_media"
  | "materialize"
  | "render_master";

export type FilmJobState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface FilmJobView {
  readonly jobId: string;
  readonly idempotencyKey: string;
  readonly kind: FilmJobKind;
  readonly targetId: string;
  readonly providerId: string;
  readonly modelId: string;
  readonly state: FilmJobState;
  readonly attempts: number;
  readonly maxAttempts: number;
  readonly errorMessage: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface FilmDeliveryManifestView {
  readonly projectId: string;
  readonly title: string;
  readonly generatedAt: string;
  readonly masterVideoPath: string;
  readonly masterAudioPath: string;
  readonly subtitlePath: string;
  readonly otioPath: string;
  readonly durationS: number;
  readonly width: number;
  readonly height: number;
  readonly frameRate: number;
  readonly shotCount: number;
  readonly clipPaths: readonly string[];
  readonly qcPassed: boolean;
  readonly compliancePassed: boolean;
  readonly complianceReportPath: string;
  readonly notes: readonly string[];
}

export type FilmComplianceSeverity = "red_line" | "high_risk" | "positive_value";
export type FilmComplianceAction = "block" | "revise" | "review" | "none";

export interface FilmComplianceFindingView {
  readonly checkId: string;
  readonly severity: FilmComplianceSeverity;
  readonly title: string;
  readonly detail: string;
  readonly location: string;
  readonly action: FilmComplianceAction;
}

export interface FilmComplianceReportView {
  readonly targetId: string;
  readonly passed: boolean;
  readonly blocked: boolean;
  readonly findings: readonly FilmComplianceFindingView[];
  readonly summary: string;
  readonly createdAt: string;
}

export interface FilmStudioView {
  readonly schemaVersion: string;
  readonly projectId: string;
  readonly projectTitle: string;
  readonly mode: FilmProductionMode;
  readonly currentStage: FilmStageId;
  readonly activeSceneId: string;
  readonly activeShotId: string;
  readonly productionBible: FilmProductionBibleView;
  readonly screenplay: {
    readonly title: string;
    readonly version: number;
    readonly synopsis: string;
    readonly acts: readonly string[];
    readonly scenes: readonly FilmScreenplaySceneView[];
    readonly estimatedDurationS: number;
  };
  readonly visualAssets: readonly FilmVisualAssetView[];
  readonly shots: readonly FilmShotView[];
  readonly runPlan: readonly {
    readonly nodeId: string;
    readonly label: string;
    readonly stage: FilmStageId;
    readonly dependsOn: readonly string[];
    readonly artifactInputs: readonly string[];
    readonly artifactOutputs: readonly string[];
    readonly providerId: string;
    readonly modelId: string;
    readonly status: FilmStageStatus;
    readonly humanCheckpoint: boolean;
    readonly retryLimit: number;
    readonly estimatedCostUsd: number;
    readonly notes: string;
  }[];
  readonly timeline: {
    readonly name: string;
    readonly frameRate: number;
    readonly tracks: readonly {
      readonly trackId: string;
      readonly name: string;
      readonly kind: string;
      readonly clips: readonly {
        readonly clipId: string;
        readonly name: string;
        readonly mediaKind: string;
        readonly sourceUrl: string;
        readonly startS: number;
        readonly durationS: number;
        readonly sourceStartS: number;
        readonly enabled: boolean;
        readonly metadata: Readonly<Record<string, unknown>>;
      }[];
    }[];
    readonly markers: readonly {
      readonly name: string;
      readonly timeS: number;
      readonly color: string;
      readonly metadata: Readonly<Record<string, unknown>>;
    }[];
  };
  readonly stages: readonly {
    readonly stage: FilmStageId;
    readonly status: FilmStageStatus;
    readonly progress: number;
    readonly artifactCount: number;
    readonly summary: string;
    readonly warnings: readonly string[];
    readonly updatedAt: string;
  }[];
  readonly decisions: readonly {
    readonly decisionId: string;
    readonly stage: FilmStageId;
    readonly title: string;
    readonly description: string;
    readonly choices: readonly string[];
    readonly selected: string;
    readonly status: string;
  }[];
  readonly mediaArtifacts: readonly FilmMediaArtifactView[];
  readonly jobs: readonly FilmJobView[];
  readonly qcReports: readonly FilmQcReportView[];
  readonly visionQcReports: readonly FilmVisionQcReportView[];
  readonly complianceReport?: FilmComplianceReportView | null;
  readonly delivery?: FilmDeliveryManifestView | null;
  readonly notices: readonly string[];
  readonly updatedAt: string;
}

export interface FilmProviderCatalogView {
  readonly [providerId: string]: {
    readonly label: string;
    readonly shortLabel?: string;
    readonly strengths?: readonly string[];
    readonly bestFor?: string;
    readonly docsUrl?: string;
    readonly recommended?: boolean;
    readonly imageModels: readonly Readonly<Record<string, unknown>>[];
    readonly videoModels: readonly Readonly<Record<string, unknown>>[];
    readonly audioModels?: Readonly<Record<string, readonly string[]>>;
    readonly assetSchemes?: readonly string[];
  };
}

// ── Short drama (vertical drama series) workbench ────────────────────────────

export interface DramaPaywallBeatView {
  readonly episodeNumber: number;
  readonly position: "start" | "middle" | "end";
  readonly strategy: string;
  readonly description: string;
}

export interface DramaThrillView {
  readonly episodeNumber: number;
  readonly kind: string;
  readonly intensity: number;
  readonly description: string;
}

export interface DramaWaveformStageView {
  readonly stage: string;
  readonly episodeStart: number;
  readonly episodeEnd: number;
  readonly intensityTarget: number;
  readonly note: string;
}

export interface DramaAntagonistView {
  readonly name: string;
  readonly tier: "surface" | "mid" | "boss";
  readonly function: string;
  readonly characterRef: string;
}

export interface DramaSeriesPlanView {
  readonly title: string;
  readonly genre: string;
  readonly logline: string;
  readonly totalEpisodes: number;
  readonly episodeDurationS: number;
  readonly threeActs: readonly string[];
  readonly paywallBeats: readonly DramaPaywallBeatView[];
  readonly thrillMatrix: readonly DramaThrillView[];
  readonly waveformStages: readonly DramaWaveformStageView[];
  readonly antagonistSystem: readonly DramaAntagonistView[];
}

export interface DramaSceneOutlineView {
  readonly sceneNumber: number;
  readonly summary: string;
  readonly location: string;
  readonly emotionalBeat: string;
}

export interface DramaEpisodeOutlineView {
  readonly episodeNumber: number;
  readonly title: string;
  readonly summary: string;
  readonly waveformStage: string;
  readonly paywallMarker: string;
  readonly scenes: readonly DramaSceneOutlineView[];
}

export interface DramaLineView {
  readonly speaker: string;
  readonly line: string;
  readonly action: string;
}

export interface DramaSceneView {
  readonly sceneNumber: number;
  readonly heading: string;
  readonly action: string;
  readonly shotCount: number;
  readonly lines: readonly DramaLineView[];
}

export interface DramaEpisodeScreenplayView {
  readonly episodeNumber: number;
  readonly title: string;
  readonly scenes: readonly DramaSceneView[];
}

export interface DramaTimelineMarkerView {
  readonly name: string;
  readonly timeS: number;
  readonly color: string;
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface DramaRunPlanNodeView {
  readonly nodeId: string;
  readonly label: string;
  readonly stage: string;
  readonly humanCheckpoint: boolean;
  readonly notes: string;
}

export interface DramaStudioView {
  readonly schemaVersion: string;
  readonly projectId: string;
  readonly title: string;
  readonly language: string;
  readonly seriesPlan: DramaSeriesPlanView | null;
  readonly outlines: readonly DramaEpisodeOutlineView[];
  readonly screenplays: readonly DramaEpisodeScreenplayView[];
  readonly runPlan: readonly DramaRunPlanNodeView[];
  readonly timelineMarkers: readonly DramaTimelineMarkerView[];
  readonly updatedAt: string;
}

export interface DramaExportResult {
  readonly projectId: string;
  readonly title: string;
  readonly totalEpisodes: number;
  readonly outlinedEpisodes: readonly number[];
  readonly screenplayEpisodes: readonly number[];
  readonly paywallMarkers: readonly string[];
  readonly auditIssues: readonly string[];
  readonly gatePassed: boolean;
  readonly artifacts: readonly string[];
}

// ── Comic workbench (P3) ────────────────────────────────────────────────────

export interface ComicSpeechBubbleView {
  readonly speaker: string;
  readonly text: string;
  readonly kind: string;
  readonly sourceLineRef: string;
}

export interface ComicPanelView {
  readonly panelId: string;
  readonly pageNumber: number;
  readonly panelNumber: number;
  readonly sceneId: string;
  readonly beat: string;
  readonly action: string;
  readonly characterIds: readonly string[];
  readonly bubbles: readonly ComicSpeechBubbleView[];
  readonly prompt: string;
  readonly aspect: string;
  readonly qcStatus: string;
  readonly locked: boolean;
}

export interface ComicPageView {
  readonly pageNumber: number;
  readonly rhythmNote: string;
  readonly panels: readonly ComicPanelView[];
}

export interface ComicStudioView {
  readonly schemaVersion: string;
  readonly projectId: string;
  readonly title: string;
  readonly format: "page" | "webtoon";
  readonly pages: readonly ComicPageView[];
  readonly sourceRevision: string;
  readonly updatedAt: string;
}

export interface ComicAuditResult {
  readonly gatePassed: boolean;
  readonly issues: readonly string[];
}

export interface ComicExportResult {
  readonly exportDir: string;
  readonly pagesPath: string;
  readonly previewPath: string;
  readonly manifestPath: string;
  readonly panelCount: number;
  readonly auditIssues: readonly string[];
}

export type RepairCaseStatus =
  | "open"
  | "located"
  | "candidate_ready"
  | "needs_verification"
  | "verified"
  | "awaiting_approval"
  | "published"
  | "resolved"
  | "manual_required"
  | "stale"
  | "rejected"
  | "deferred"
  | "failed";

export type RepairAuthority =
  | "automatic_derived"
  | "automatic_working_candidate"
  | "proposal_required"
  | "manual_only";

export interface RepairLocator {
  readonly targetFormat: string;
  readonly role: "repair" | "reference";
  readonly surface: string;
  readonly chapterNumber?: number | null;
  readonly stableNodeId?: string;
  readonly segmentUid?: string;
  readonly segmentIndex?: number | null;
  readonly fieldPath?: string;
  readonly containerHash?: string;
  readonly comparatorId?: string;
  readonly expectedRaw?: unknown;
  readonly actualRaw?: unknown;
  readonly expectedNormalized?: unknown;
  readonly actualNormalized?: unknown;
  readonly sourceVersion?: string;
  readonly targetVersion?: string;
  readonly quote?: string;
  readonly charStart?: number;
  readonly charEnd?: number;
  readonly textHash?: string;
  readonly jsonPointer?: string;
  readonly chapterSet?: readonly number[];
  readonly evidencePairs?: readonly Readonly<Record<string, unknown>>[];
  readonly manualReviewReason?: string;
}

export interface RepairIssue {
  readonly issueId: string;
  readonly dimension: string;
  readonly issueType: string;
  readonly severity: "critical" | "high" | "medium" | "low";
  readonly blocking: boolean;
  readonly status: string;
  readonly summary: string;
  readonly description: string;
  readonly evidence: readonly {
    readonly quote: string;
    readonly source: string;
    readonly locator?: RepairLocator | null;
    readonly confidence: number;
  }[];
  readonly repairTargets: readonly RepairLocator[];
  readonly referenceTargets: readonly RepairLocator[];
  readonly repairIntent: {
    readonly operation: string;
    readonly targetPolicy: string;
    readonly rationale: string;
    readonly preserve: readonly string[];
  };
}

export interface ResolvedRepairTarget {
  readonly targetId: string;
  readonly targetFormat: string;
  readonly surface: string;
  readonly locator: RepairLocator;
  readonly path: string;
  readonly window: Readonly<Record<string, unknown>>;
  readonly currentValue: unknown;
  readonly currentHash: string;
  readonly issueIds: readonly string[];
  readonly allowedOperation: string;
  readonly confidence: number;
  readonly resolutionStatus: "resolved" | "manual_required" | "ambiguous" | "unresolved";
  readonly reason: string;
}

export interface RepairCandidate {
  readonly caseId: string;
  readonly version: number;
  readonly baseHash: string;
  readonly candidateHash: string;
  readonly blobHash: string;
  readonly origin: "deterministic" | "model" | "human_edit" | "legacy_shadow";
  readonly patchCount: number;
  readonly changeRatio: number;
  readonly protectedItems: readonly string[];
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface RepairVerificationBundle {
  readonly caseId: string;
  readonly candidateVersion: number;
  readonly candidateHash: string;
  readonly passed: boolean;
  readonly resolvedIssueIds: readonly string[];
  readonly residualIssueIds: readonly string[];
  readonly regressionIssueIds: readonly string[];
  readonly validators: readonly {
    readonly validatorId: string;
    readonly passed: boolean;
    readonly required: boolean;
    readonly details: readonly string[];
  }[];
  readonly details: readonly string[];
}

export interface RepairPublishReceipt {
  readonly receiptId: string;
  readonly caseId: string;
  readonly candidateVersion: number;
  readonly authority: RepairAuthority;
  readonly target: string;
  readonly beforeHash: string;
  readonly afterHash: string;
  readonly transactionStatus: "not_started" | "prepared" | "committed" | "reconciled" | "failed";
  readonly committed: boolean;
  readonly recovered: boolean;
  readonly message: string;
}

export interface RepairCaseEvent {
  readonly eventId: string;
  readonly seq: number;
  readonly caseId: string;
  readonly caseVersion: number;
  readonly eventType: string;
  readonly actor: string;
  readonly data: Readonly<Record<string, unknown>>;
  readonly createdAt?: string;
}

export interface RepairCase {
  readonly caseId: string;
  readonly projectId: string;
  readonly contentType: string;
  readonly source: string;
  readonly artifactId: string;
  readonly sourceVersion: string;
  readonly sourceHash: string;
  readonly authority: RepairAuthority;
  readonly inputVersion: string;
  readonly policyVersion: number | null;
  readonly status: RepairCaseStatus;
  readonly version: number;
  readonly eventSeq: number;
  readonly title: string;
  readonly chapterNumbers: readonly number[];
  readonly issues: readonly RepairIssue[];
  readonly targets: readonly ResolvedRepairTarget[];
  readonly candidates: readonly RepairCandidate[];
  readonly verification: RepairVerificationBundle | null;
  readonly receipt: RepairPublishReceipt | null;
  readonly proposalId: string;
  readonly failureReason: string;
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface RepairWorkbenchCapabilities {
  readonly annotate: boolean;
  readonly prepare: boolean;
  readonly edit: boolean;
  readonly verify: boolean;
  readonly requestApproval: boolean;
  readonly rejectOrDefer: boolean;
  readonly publish: boolean;
  readonly recover: boolean;
  readonly reason: string;
}

export interface RepairSourceView {
  readonly projectId: string;
  readonly contentType: string;
  readonly artifactId: string;
  readonly chapterNumber: number;
  readonly sourceVersion: string;
  readonly sourceHash: string;
  readonly content: string;
  readonly state: "working_candidate" | "official";
}

export interface RepairCaseDetailView {
  readonly case: RepairCase;
  readonly events: readonly RepairCaseEvent[];
  readonly sourcePayload: unknown;
  readonly candidatePayload: unknown;
  readonly capabilities: RepairWorkbenchCapabilities;
}

export interface RepairCaseFilters {
  readonly contentType?: string;
  readonly status?: RepairCaseStatus;
  readonly source?: string;
  readonly chapter?: number;
  readonly severity?: "critical" | "high" | "medium" | "low";
}

export interface RepairManualAnnotationRequest {
  readonly chapterNumber: number;
  readonly artifactId: string;
  readonly sourceHash: string;
  readonly charStart: number;
  readonly charEnd: number;
  readonly selectedText: string;
  readonly summary: string;
  readonly description?: string;
  readonly severity: "critical" | "high" | "medium" | "low";
}

export interface RepairCandidateEditRequest {
  readonly caseVersion: number;
  readonly candidateVersion: number;
  readonly replacementText: string;
}

export interface RepairVerificationRequest {
  readonly caseVersion: number;
  readonly candidateVersion: number;
}

export interface RepairCaseJobRequest {
  readonly projectId: string;
  readonly caseId: string;
  readonly operation: "prepare" | "verify" | "shadow_compare";
  readonly caseVersion: number;
  readonly candidateVersion: number;
}

export interface RepairCaseDecisionRequest {
  readonly caseVersion: number;
  readonly decision: "reject" | "defer" | "accept_compatible" | "authoritative_claims";
  readonly reason?: string;
  readonly sourceHash?: string;
  readonly claimIds?: readonly string[];
  readonly authoritativeClaimIds?: readonly string[];
}

export interface RepairPublishRequest extends RepairVerificationRequest {
  readonly authorityVersion: number;
}

export type RepairApprovalRequest = RepairVerificationRequest;

export interface RepairRecoveryRequest {
  readonly caseVersion: number;
  readonly receiptId: string;
}

export interface EngineClient {
  /** Capability-gated; absence must not fall back to more automatic behavior. */
  getAuthoringSession?(projectId: string, chapter?: number): Promise<AuthoringSessionView>;
  getAuthoringProposals?(projectId: string): Promise<readonly AuthoringProposalView[]>;
  getAuthoringMessages?(projectId: string): Promise<readonly AuthoringMessageView[]>;
  getRepairSource?(projectId: string, chapter: number): Promise<RepairSourceView>;
  listRepairCases?(projectId: string, filters?: RepairCaseFilters): Promise<readonly RepairCase[]>;
  getRepairCase?(projectId: string, caseId: string): Promise<RepairCaseDetailView>;
  getWorkspace(): Promise<WorkspaceView>;
  listJobs(): Promise<readonly JobView[]>;
  getSettings(): Promise<SettingsView>;
  getOllama(): Promise<OllamaManagerView>;
  getWorkflow(): Promise<WorkflowView>;
  getErrorArchiveSummary(): Promise<ErrorArchiveSummaryView>;
  getInitManualRepair(projectId: string): Promise<InitManualRepairView>;
  getProjectReader(projectId: string): Promise<ProjectReaderView>;
  getChapterStudio(projectId: string): Promise<ChapterStudioView>;
  getVoiceStudio(projectId: string, chapterNumber?: number): Promise<VoiceStudioView>;
  getFilmStudio(projectId: string): Promise<FilmStudioView>;
  getFilmProviderCatalog(): Promise<FilmProviderCatalogView>;
  getFilmGraph(projectId: string): Promise<FilmGraphView>;
  getFilmNodeCatalog(): Promise<readonly FilmNodeDefinitionView[]>;
  listFilmGraphRuns(projectId: string, limit?: number): Promise<readonly FilmGraphRunView[]>;
  getFilmGraphRun(projectId: string, runId: string): Promise<FilmGraphRunView>;
  getDramaStudio(projectId: string): Promise<DramaStudioView>;
  getComicStudio(projectId: string): Promise<ComicStudioView>;
  getComicAudit(projectId: string): Promise<ComicAuditResult>;
  getVoiceCatalog(projectId: string): Promise<VoiceCatalogView>;
  getVoicePlatformRuntimes(): Promise<readonly VoiceAudioRuntimeView[]>;
  getVoiceAudioModelCenter(): Promise<VoiceAudioModelCenterView>;
  startVoiceAudioModelOperation(
    request: VoiceAudioModelOperationRequest,
  ): Promise<VoiceAudioModelOperationView>;
  getVoiceAudioModelOperation(operationId: string): Promise<VoiceAudioModelOperationView>;
  cancelVoiceAudioModelOperation(operationId: string): Promise<VoiceAudioModelOperationView>;
  getNarrativeTools(projectId: string): Promise<NarrativeToolsView>;
  getRelationshipOverview(projectId: string): Promise<RelationshipOverviewView>;
  getTaskStream(taskId: string, options?: TaskStreamSnapshotOptions): Promise<TaskStreamView>;
  subscribeTaskStream(taskId: string, listener: TaskStreamListener): TaskStreamUnsubscribe;
  getStepArtifacts(projectId: string, kind: string, stepKey: string, chapterNumber?: number): Promise<StepArtifactsView>;
}

export interface AuthoringPolicy {
  version: number;
  mode: "manual" | "coauthor" | "authorized_auto";
  startChapter: number;
  endChapter: number;
  budgetUsd: number | null;
  stopped: boolean;
  stopReason: string;
}

export interface AuthoringPermission {
  action: string;
  allowed: boolean;
  requiresApproval: boolean;
  reason: string;
}

export type SemanticConsistencyStatus =
  | "uncompiled"
  | "queued"
  | "running"
  | "clean"
  | "conflict"
  | "review_required"
  | "stale"
  | "failed";

export interface SemanticConsistencyView {
  readonly status: SemanticConsistencyStatus;
  readonly sourceVersion: string;
  readonly sourceFingerprint: string;
  readonly ledgerHash: string;
  readonly reportId: string;
  readonly issueIds: readonly string[];
  readonly issueCount: number;
  readonly jobId?: string;
  readonly affectedChapters: readonly number[];
  readonly reason: string;
}

export interface AuthoringSessionView {
  projectId: string;
  configured: boolean;
  disabled?: boolean;
  policy: AuthoringPolicy;
  inputVersion: string;
  allowedActions: readonly AuthoringPermission[];
  currentTaskId: string;
  waitingReason: string;
  stopState: "idle" | "running" | "stopping" | "stopped";
  proposalIds: readonly string[];
  revisionHistory?: readonly { timestamp: string; previousHash: string; currentHash: string; reason: string; transactionStatus: string }[];
  currentTextHash?: string;
  budget?: { spentUsd: number; reservedUsd: number; unknownCalls: number; callCount: number };
  checkpoint?: { checkpointId?: string; summary?: string; options?: readonly { optionId: string; label: string; description?: string }[] };
  planningTask?: { status?: string; error?: string; jobId?: string; targetChapter?: number; result?: { revisionId?: string; targetChapter?: number } };
  semanticConsistency: SemanticConsistencyView;
}

export interface AuthoringProposalView {
  editable?: boolean;
  id: string;
  projectId: string;
  action: string;
  chapterNumber: number;
  policyVersion: number;
  inputVersion: string;
  candidateVersion: string;
  title: string;
  original: string;
  candidate: string;
  evidence: readonly string[];
  affectedChapters: readonly number[];
  risks: readonly string[];
  lockConflicts: readonly string[];
  costHint: string;
  status: "pending" | "approved" | "rejected" | "deferred" | "stale" | "applied";
  applicationResult: Readonly<Record<string, unknown>>;
}

export interface AuthoringProposalRequest {
  command: "checkpoint" | "revise_chapter" | "restore_chapter" | "publish_planning" | "revise_foundation";
  foundationArtifact?: "spec" | "world" | "characters" | "blueprint";
  chapterNumber: number;
  title?: string;
  candidate?: string;
  optionId?: string;
  notes?: string;
  revisionId?: string;
  revisionSide?: "before" | "after";
  evidence?: readonly string[];
  expectedInputVersion?: string;
}

export type AuthoringContext = "spec" | "world" | "characters" | "blueprint" | "outline" | "chapter" | "reports";

export interface AuthoringMessageRequest {
  message: string;
  chapterNumber: number;
  context: AuthoringContext;
  allowActions: boolean;
}

export interface AuthoringMessageView extends Omit<AuthoringMessageRequest, "allowActions"> {
  id: string;
  projectId: string;
  inputVersion: string;
  policyVersion: number;
  taskId: string;
  createdAt: string;
  status: "queued" | "running" | "completed" | "failed" | "paused";
  response: { reply: string; proposals: readonly AuthoringProposalRequest[]; actions: readonly { action: string; proposalId?: string; reason: string }[] } | null;
  proposalIds: readonly string[];
  actionResults: readonly Readonly<Record<string, unknown>>[];
  error: string;
}

export interface AuthoringProposalDecision {
  decision: "accept" | "reject" | "defer" | "edit";
  candidateVersion: string;
  inputVersion: string;
  policyVersion: number;
  editedCandidate?: string;
}

/* ============================================================
 * EngineCommandClient — write-side boundary for Phase 1.
 *
 * React surfaces call commands to request state changes.  The
 * command payload is credential-free and transport-neutral.
 * A future LegacyLocalEngineClient (Python sidecar) and a
 * cloud adapter both implement this interface without requiring
 * page-level rewrites.
 * ============================================================ */

/**
 * Credential-free editable route draft, matching the desktop
 * session shape so the Settings page can submit it directly.
 */
export interface RouteCommandFallback {
  readonly profileId: string;
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
}

export interface RouteCommand {
  readonly primaryProfileId: string;
  readonly fallbackRoutes: readonly RouteCommandFallback[];
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
  readonly temperature: number | null;
}

export interface ModelProfileCommand {
  readonly id: string;
  /** Original id when provider/model edits rename a profile. */
  readonly previousId?: string | undefined;
  readonly label: string;
  readonly provider: string;
  readonly model: string;
  readonly tierLabel: string;
  readonly supportsThinking: boolean;
  readonly supportsMultiTurn: boolean;
  /** Secret mutation intent; existing keys are preserved unless explicitly changed. */
  readonly apiKeyAction?: "preserve" | "replace" | "clear" | undefined;
  readonly apiKey?: string | undefined;
  readonly baseUrl?: string | undefined;
}

export interface TestModelProfileCommand {
  readonly kind: "test_model_profile";
  readonly id: string;
  readonly previousId?: string | undefined;
  readonly provider: string;
  readonly model: string;
  readonly apiKeyAction?: "preserve" | "replace" | "clear" | undefined;
  readonly apiKey?: string | undefined;
  readonly baseUrl?: string | undefined;
}

export interface TestModelProfileResult {
  readonly ok: boolean;
  readonly detail: string;
  readonly latencyMs: number | null;
  readonly supportsThinking: boolean;
  readonly supportsMultiTurn: boolean;
}

/**
 * The full settings save payload.  All fields are optional so
 * partial saves (e.g. only routing, or only theme) are supported
 * without the page needing to reconstruct the entire state.
 */
export interface SaveSettingsCommand {
  readonly kind: "save_settings";
  readonly defaultProfileId?: string;
  readonly profiles?: readonly ModelProfileCommand[];
  readonly routes?: Readonly<Record<string, RouteCommand>>;
  readonly creativeTemperature?: {
    readonly enabled: boolean;
    readonly scope: "recommended" | "chapter_core" | "init_and_chapter" | "custom";
    readonly downDelta: number;
    readonly upDelta: number;
    readonly customTaskKeys: readonly string[];
  };
  readonly themeId?: string;
  readonly fontPreferences?: FontPreferencesView;
  /**
   * Creation parameter key-value pairs (field id → string value).
   * The backend maps these to NOVEL_FORGE_* environment variables.
   */
  readonly creationParameters?: Readonly<Record<string, string>>;
  readonly chapterRuntimePolicy?: ChapterRuntimePolicyCommand;
}

export type SaveSettingsResultStatus = "saved" | "partial" | "validation_error" | "engine_error";

export interface SaveSettingsResult {
  readonly status: SaveSettingsResultStatus;
  /** Whether this adapter durably wrote the accepted draft. */
  readonly persistence: "persisted" | "accepted_only";
  readonly message: string;
  /** Routes that the engine accepted; empty on validation_error. */
  readonly acceptedRouteIds: readonly string[];
  /** Routes the engine rejected with per-route reasons. */
  readonly rejectedRoutes: readonly { readonly routeId: string; readonly reason: string }[];
  /** Timestamp label for UI display (e.g. "14:32:05"). */
  readonly savedAtLabel: string;
  /** Whether the active runtime has adopted the persisted configuration. */
  readonly runtimeReloadStatus: "reloaded" | "current" | "unavailable" | "failed";
}

export interface DeleteProjectsCommand {
  readonly kind: "delete_projects";
  readonly projectIds: readonly string[];
}

export type DeleteProjectFailureReason =
  | "active_job"
  | "delete_failed"
  | "invalid_project_id"
  | "project_not_found";

export interface DeleteProjectFailure {
  readonly projectId: string;
  readonly reason: DeleteProjectFailureReason;
  readonly message: string;
}

export interface DeleteProjectsResult {
  readonly status: "deleted" | "partial" | "rejected";
  readonly message: string;
  readonly deletedProjectIds: readonly string[];
  readonly failures: readonly DeleteProjectFailure[];
}

export type RevisionInvalidationScope = "none" | "next" | "volume" | "downstream";

export interface SaveChapterRevisionCommand {
  readonly kind: "save_chapter_revision";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly text: string;
  readonly expectedRevision?: string;
  readonly scope: RevisionInvalidationScope;
  readonly backgroundReevaluate: boolean;
}

export interface SaveChapterRevisionResult {
  readonly status: "applied" | "noop" | "conflict" | "rejected";
  readonly message: string;
  readonly revision?: string;
  readonly latestText?: string;
  readonly backgroundReevaluateScheduled?: boolean;
}

/** Engine-routed, unsaved rewrite candidate for a selected final-draft passage. */
export interface GenerateChapterRevisionCandidateCommand {
  readonly kind: "generate_chapter_revision_candidate";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly chapterTitle: string;
  readonly selectedText: string;
  readonly beforeContext: string;
  readonly afterContext: string;
  readonly instruction: string;
}

export interface GenerateChapterRevisionCandidateResult {
  readonly status: "generated" | "rejected";
  readonly message: string;
  readonly replacement?: string;
}

export interface TokenDashboardPreferencesView {
  readonly currency: "USD" | "CNY" | "EUR";
  readonly exchangeRates: Readonly<Record<string, number>>;
  readonly stepWaterfallFilter: "all" | "init" | "chapter" | "repair";
  /** Mirrors PySide's project-level default price setting. */
  readonly pricePerMillion: number;
  readonly priceUnit: "million" | "thousand";
  /** Per-model overrides use the stable provider/model key from analytics. */
  readonly modelPricePerMillion: Readonly<Record<string, number>>;
  readonly modelPriceUnit: Readonly<Record<string, "million" | "thousand">>;
  readonly revision: string;
}

export interface SaveTokenDashboardPreferencesCommand {
  readonly kind: "save_token_dashboard_preferences";
  readonly projectId: string;
  readonly currency: "USD" | "CNY" | "EUR";
  readonly exchangeRates: Readonly<Record<string, number>>;
  readonly stepWaterfallFilter: "all" | "init" | "chapter" | "repair";
  readonly pricePerMillion?: number;
  readonly priceUnit?: "million" | "thousand";
  readonly modelPricePerMillion?: Readonly<Record<string, number>>;
  readonly modelPriceUnit?: Readonly<Record<string, "million" | "thousand">>;
  readonly expectedRevision?: string;
}

export interface SaveTokenDashboardPreferencesResult {
  readonly status: "saved" | "conflict" | "rejected";
  readonly message: string;
  readonly revision?: string;
  readonly preferences?: TokenDashboardPreferencesView;
}

/**
 * Chapter-level commands.  Phase 1 implementations return
 * acknowledgement only; real pipeline execution is Phase 2.
 */
export interface PrepareChapterCommand {
  readonly kind: "prepare_chapter";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly notes?: string;
  readonly force?: boolean;
  readonly writingMode?: "whole_chapter" | "scene_level";
  readonly rewriteStrategy?: "auto" | "sequential" | "compatible" | "reconstruct" | "surgical";
}

export interface CancelChapterCommand {
  readonly kind: "cancel_chapter";
  readonly projectId: string;
  readonly chapterNumber: number;
}

export interface CancelJobCommand {
  readonly kind: "cancel_job";
  readonly taskId: string;
  readonly reason?: string;
}

export interface ResumeJobCommand {
  readonly kind: "resume_job";
  readonly taskId: string;
}

export interface RetryInitRepairCommand {
  readonly kind: "retry_init_repair";
  readonly projectId: string;
  readonly resetRepairHistory?: boolean;
}

export interface SaveInitManualRepairCommand {
  readonly kind: "save_init_manual_repair";
  readonly projectId: string;
  readonly artifact: "blueprint" | "outline" | "chapter_contracts";
  readonly payload: Readonly<Record<string, unknown>>;
  readonly expectedRevision: string;
}

export interface SaveInitManualRepairResult {
  readonly status: "saved" | "conflict" | "rejected";
  readonly message: string;
  readonly repair?: InitManualRepairView;
}

/**
 * Rebuild the durable vector-backed memory indexes for an existing project.
 * The Engine owns the long-running work so both desktop clients observe the
 * same cancellable job instead of maintaining a client-local queue.
 */
export interface RebuildMemoryVectorsCommand {
  readonly kind: "rebuild_memory_vectors";
  readonly projectId: string;
  readonly includeExpression?: boolean;
  readonly fromChapter?: number;
  readonly toChapter?: number;
}

export interface ClearJobHistoryCommand {
  readonly kind: "clear_job_history";
  readonly taskIds?: readonly string[];
}

export interface ClearJobHistoryResult {
  readonly status: "cleared" | "rejected";
  readonly message: string;
  readonly clearedTaskIds: readonly string[];
}

/** Persist author review; it does not retry or repair the failed work. */
export interface AcknowledgeTaskErrorsCommand {
  readonly kind: "acknowledge_task_errors";
  readonly errorEntryIds: readonly string[];
}

/** Return previously acknowledged diagnostics to the pending review queue. */
export interface ReopenTaskErrorsCommand {
  readonly kind: "reopen_task_errors";
  readonly errorEntryIds: readonly string[];
}

export interface TaskErrorResolutionResult {
  readonly status: "acknowledged" | "reopened";
  readonly message: string;
  readonly updatedErrorEntryIds: readonly string[];
}

/** Remove only Engine-verified closed compact diagnostics. */
export interface ClearClosedTaskErrorsCommand {
  readonly kind: "clear_closed_task_errors";
  readonly errorEntryIds: readonly string[];
}

export interface ClearClosedTaskErrorsResult {
  readonly status: "cleared";
  readonly message: string;
  readonly clearedTaskIds: readonly string[];
  readonly clearedErrorEntryIds: readonly string[];
  /** Requested entries that were still pending and therefore retained. */
  readonly retainedPendingErrorEntryIds: readonly string[];
}

/** Aggregate-only view of durable task diagnostics. Individual records and
 * local paths remain inside the Engine until an explicit diagnostic viewer is
 * added to the shared contract. */
export interface ErrorArchiveSummaryView {
  readonly entryCount: number;
  readonly projectCount: number;
  readonly latestTime: string;
}

export interface ClearErrorArchiveCommand {
  readonly kind: "clear_error_archive";
  /** Omit to clear every project's compact diagnostic index. */
  readonly projectId?: string;
}

export interface ClearErrorArchiveResult {
  readonly status: "cleared";
  readonly message: string;
  readonly removedProjectCount: number;
}

/**
 * Deliberately start a long project from a fresh initialization boundary.
 *
 * The Engine preserves completed chapter Markdown, but removes prior init
 * artifacts and inactive task records for this project. Running or queued
 * tasks are protected and cause a rejected response instead.
 */
export interface RestartLongInitCommand {
  readonly kind: "restart_long_init";
  readonly projectId: string;
}

export interface RestartLongInitResult {
  readonly status: "reset" | "rejected";
  readonly message: string;
  readonly clearedTaskIds: readonly string[];
  readonly removedArtifactCount: number;
}

/**
 * Continue a long initialization using the Engine's durable original intent.
 * ``fallbackPayload`` is read only for projects created before durable intent
 * persistence existed; it never overrides a stored story configuration.
 */
export interface ContinueLongInitCommand {
  readonly kind: "continue_long_init";
  readonly projectId: string;
  readonly runMode: WorkflowLaunchMode;
  readonly fallbackPayload?: InitLongWorkflowInput;
}

export interface ChapterCommandResult {
  readonly status: "accepted" | "rejected" | "already_running";
  readonly message: string;
  /** Job ID for tracking via task stream; present when status is "accepted". */
  readonly taskId?: string;
  /** Optional command-specific facts; cleanup uses this to request a refresh after partial writes. */
  readonly data?: Readonly<Record<string, unknown>>;
}

/** Atomically replace the subplot plans in one narrative blueprint revision. */
export interface SaveNarrativeSubplotsCommand {
  readonly kind: "save_narrative_subplots";
  readonly projectId: string;
  readonly subplots: readonly NarrativeSubplotInput[];
  readonly expectedRevision?: string;
}

export interface NarrativeSubplotMutationResult {
  readonly status: "saved" | "candidate" | "conflict" | "rejected";
  readonly proposalId?: string;
  readonly message: string;
  readonly blueprintRevision?: string;
}

/** Source-compatible character fields editable from the narrative workbench. */
export interface NarrativeCharacterProfileInput {
  readonly name?: string;
  readonly role?: string;
  readonly age?: string;
  readonly gender?: string;
  readonly status?: string;
  readonly timeLayer?: string;
  readonly socialStatus?: string;
  readonly abilities?: string;
  readonly appearance?: string;
  readonly personality?: string;
  readonly backstory?: string;
  readonly arc?: string;
  readonly voice?: string;
  readonly notes?: string;
}

export interface NarrativeCharacterMutationResult {
  proposalId?: string;
  readonly status: "saved" | "candidate" | "conflict" | "rejected";
  readonly message: string;
  readonly characterRevision?: string;
  readonly invalidatedChapters: readonly number[];
  readonly warnings: readonly string[];
}

/** Create a new character when ``characterId`` is omitted, otherwise patch it. */
export interface SaveNarrativeCharacterCommand {
  readonly kind: "save_narrative_character";
  readonly projectId: string;
  readonly characterId?: string;
  readonly profile: NarrativeCharacterProfileInput;
  readonly expectedRevision: string;
}

export interface RetireNarrativeCharacterCommand {
  readonly kind: "retire_narrative_character";
  readonly projectId: string;
  readonly characterId: string;
  readonly expectedRevision: string;
}

export interface SaveNarrativeRelationshipCommand {
  readonly kind: "save_narrative_relationship";
  readonly projectId: string;
  readonly sourceCharacterId: string;
  readonly targetCharacterId: string;
  /** Canonical relation type or a UI label such as ``同盟``. */
  readonly relationType: string;
  readonly description: string;
  readonly expectedRevision: string;
}

export interface RemoveNarrativeRelationshipCommand {
  readonly kind: "remove_narrative_relationship";
  readonly projectId: string;
  readonly sourceCharacterId: string;
  readonly targetCharacterId: string;
  readonly expectedRevision: string;
}

/** Source-compatible fields for a user or imported HumanizeLibrary entry. */
export interface HumanizePatternInput {
  readonly patternId?: string;
  readonly name: string;
  readonly category?: string;
  readonly severity?: string;
  readonly keywords?: readonly string[];
  readonly notes?: string;
  readonly examplePhrase?: string;
  /** Imported patterns retain their provenance after Engine validation. */
  readonly source?: "user" | "imported";
}

export interface HumanizeLibraryMutationResult {
  readonly status: "saved" | "conflict" | "rejected";
  readonly message: string;
  readonly humanizeLibraryRevision?: string;
  readonly pattern?: HumanizePatternView;
}

export interface SaveHumanizePatternCommand {
  readonly kind: "save_humanize_pattern";
  readonly projectId: string;
  /** Omit for a new user pattern. */
  readonly patternId?: string;
  readonly pattern: HumanizePatternInput;
  readonly expectedRevision: string;
}

export interface SetHumanizePatternEnabledCommand {
  readonly kind: "set_humanize_pattern_enabled";
  readonly projectId: string;
  readonly patternId: string;
  readonly enabled: boolean;
  readonly expectedRevision: string;
}

export interface RemoveHumanizePatternCommand {
  readonly kind: "remove_humanize_pattern";
  readonly projectId: string;
  readonly patternId: string;
  readonly expectedRevision: string;
}

export interface MergeHumanizePatternsCommand {
  readonly kind: "merge_humanize_patterns";
  readonly projectId: string;
  readonly sourcePatternId: string;
  readonly targetPatternId: string;
  readonly expectedRevision: string;
}

/** Ask the configured writing model for reviewable subplot proposals. */
export interface GenerateNarrativeSubplotsCommand {
  readonly kind: "generate_narrative_subplots";
  readonly projectId: string;
  readonly userHint?: string;
  readonly count?: number;
}

export interface GenerateNarrativeSubplotsResult {
  readonly status: "generated" | "rejected";
  readonly message: string;
  readonly candidates: readonly NarrativeSubplotInput[];
}

/** Convert selected event-driven character arcs without deleting their source arcs. */
export interface ConvertNarrativeArcsToSubplotsCommand {
  readonly kind: "convert_narrative_arcs_to_subplots";
  readonly projectId: string;
  readonly arcIds: readonly string[];
  readonly expectedRevision?: string;
}

export interface ResolveChapterCheckpointCommand {
  readonly kind: "resolve_chapter_checkpoint";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly checkpointId: string;
  readonly optionId: string;
  readonly notes?: string;
  readonly force?: boolean;
  readonly authoringApprovalId?: string;
}

export interface PolishChapterCommand {
  readonly kind: "polish_chapter";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly notes?: string;
}

/** Per-request override of the Engine's configured repair policy. */
export type RepairControlMode = "manual" | "ai_assisted" | "ai_auto";

/** Targeted repair commands are durable jobs; empty issue lists mean the Engine selects open issues. */
export interface RepairContinuityCommand {
  readonly kind: "repair_continuity";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly issueIndices?: readonly number[];
  readonly issueSignatures?: readonly string[];
  readonly syntheticIssues?: readonly Readonly<Record<string, unknown>>[];
  readonly repairControlMode?: RepairControlMode;
}

export interface RepairCausalCommand {
  readonly kind: "repair_causal";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly issueIndices?: readonly number[];
  readonly issueSignatures?: readonly string[];
  readonly syntheticIssues?: readonly Readonly<Record<string, unknown>>[];
  readonly allowExhaustedRetry?: boolean;
  readonly repairControlMode?: RepairControlMode;
}

export interface RepairIssuesCommand {
  readonly kind: "repair_issues";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly continuityIssueIndices?: readonly number[];
  readonly causalIssueIndices?: readonly number[];
  readonly continuityIssueSignatures?: readonly string[];
  readonly causalIssueSignatures?: readonly string[];
  readonly continuitySyntheticIssues?: readonly Readonly<Record<string, unknown>>[];
  readonly causalSyntheticIssues?: readonly Readonly<Record<string, unknown>>[];
  readonly allowExhaustedRetry?: boolean;
  readonly repairControlMode?: RepairControlMode;
}

export interface ReevaluateChapterCommand {
  readonly kind: "reevaluate_chapter";
  readonly projectId: string;
  readonly chapterNumber: number;
}

export interface ReextractRelationshipsCommand {
  readonly kind: "reextract_relationships";
  readonly projectId: string;
  /** 0 instructs the Engine to cover every completed chapter. */
  readonly chapterNumber?: number;
}

export interface RepairMotifHistoryCommand {
  readonly kind: "repair_motif_history";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly forceReExtract?: boolean;
  readonly startChapter?: number;
  readonly endChapter?: number;
}

/** Durable outline-write request shared by 卷帙 and 机杼. */
export interface PolishOutlineCommand {
  readonly kind: "polish_outline";
  readonly projectId: string;
  readonly userHint?: string;
  readonly selectedSuggestions?: readonly string[];
  readonly focusFields?: readonly string[];
  readonly chapterRange?: string;
  readonly analysisOnly?: boolean;
  readonly syncContracts?: boolean;
}

/** Refresh chapter contracts from the current outline without touching prose. */
export interface SyncChapterContractsCommand {
  readonly kind: "sync_chapter_contracts";
  readonly projectId: string;
  readonly affectedChapterNumbers?: readonly number[];
  readonly cascadeDownstream?: boolean;
  readonly rebuildMilestones?: boolean;
  readonly markStale?: boolean;
  readonly proseUntouched?: true;
  readonly maxCascadeDepth?: number;
}

/** Append chapters to an existing outline and optionally sync its contracts. */
export interface ExtendOutlineCommand {
  readonly kind: "extend_outline";
  readonly projectId: string;
  readonly additionalChapters?: number;
  readonly targetTotal?: number;
  readonly decommissionOldEnding?: boolean;
  readonly syncContracts?: boolean;
  readonly reason?: string;
}

export interface AuditBookCommand {
  readonly kind: "audit_book";
  readonly projectId: string;
  readonly chapterRange: readonly number[];
  readonly analysisMode?: "auto" | "summary" | "full_text";
  readonly twoPhaseEnabled?: boolean;
  readonly twoPhaseMaxTargetChapters?: number;
  readonly locationStrictness?: "strict" | "balanced" | "loose";
  readonly maxTokens?: number;
  readonly temperature?: number;
  readonly auditMaxChaptersPerBatch?: number;
  readonly auditMaxIssuesPerChunk?: number;
  readonly auditIssuePoolMaxItems?: number;
  readonly twoPhaseThreshold?: number;
  readonly chapterMaxChars?: number;
  readonly parallelChunks?: boolean;
  readonly parallelDimensions?: boolean;
  readonly promptHint?: string;
}

/** Execute only the ready repair tickets emitted by a completed whole-book audit. */
export interface ExecuteGlobalRepairQueueCommand {
  readonly kind: "execute_global_repair_queue";
  readonly projectId: string;
  readonly runId?: string;
  readonly statuses?: readonly "ready"[];
  readonly maxItems?: number;
  readonly verifyBeforeApply?: boolean;
  readonly rollbackOnFailure?: boolean;
  readonly concurrency?: number;
}

/** Run the publication-level whole-book editorial audit without mutating prose. */
export interface AuditBookEditorialCommand {
  readonly kind: "audit_book_editorial";
  readonly projectId: string;
  readonly chapterRange: readonly number[];
  readonly promptHint?: string;
  readonly maxTokens?: number;
  readonly temperature?: number;
  readonly batchSize?: number;
  readonly batchTimeoutS?: number;
}

export interface ExportBookCommand {
  readonly kind: "export_book";
  readonly projectId: string;
  readonly format: "markdown" | "txt" | "epub";
  readonly chapterRange: readonly number[];
  readonly bookTitle?: string;
}

export interface CleanChaptersCommand {
  readonly kind: "clean_chapters";
  readonly projectId: string;
  readonly fromChapter: number;
}

/**
 * Workflow-level commands.
 */
export type WorkflowLaunchMode = "create" | "copilot" | "autorun";

export interface WorkflowBlueprintElementPreferenceItem {
  readonly elementId: string;
  readonly enabled: boolean | null;
  readonly locked: boolean;
  readonly weight: number;
}

export interface WorkflowBlueprintElementPreferences {
  readonly presetId: string;
  readonly manualOverride: boolean;
  readonly items: readonly WorkflowBlueprintElementPreferenceItem[];
}

/** Transport shape kept in lock-step with Python ``RunShortRequest``. */
export interface RunShortWorkflowInput {
  readonly projectId: string;
  readonly theme: string;
  readonly genre: string;
  readonly tone: string;
  readonly lengthTarget: number;
  readonly segmentTriggerWords: number;
  readonly writingMode: "auto" | "whole_chapter" | "scene_level";
  readonly maxEditRounds: number;
  readonly title: string;
  readonly language: string;
  readonly charactersHint: string;
  readonly worldHint: string;
  readonly conflictHint: string;
  readonly povHint: string;
  readonly openingStyle: string;
  readonly endingStyle: string;
  readonly extraInstructions: string;
  readonly researchEnabled: boolean;
  readonly researchProvider: string;
  readonly researchQueryHint: string;
  readonly blueprintElementPreferences: WorkflowBlueprintElementPreferences;
}

/** Transport shape kept in lock-step with Python ``InitLongRequest``. */
export interface InitLongWorkflowInput {
  readonly projectId: string;
  readonly premise: string;
  readonly genre: string;
  readonly tone: string;
  readonly totalChapters: number;
  readonly wordsPerChapter: number;
  readonly volumeMode: "auto" | "on" | "off";
  readonly chaptersPerVolume: number;
  readonly title: string;
  readonly language: string;
  readonly charactersHint: string;
  readonly worldHint: string;
  readonly conflictHint: string;
  readonly povHint: string;
  readonly openingStyle: string;
  readonly endingStyle: string;
  readonly extraInstructions: string;
  readonly polishHint: string;
  readonly researchEnabled: boolean;
  readonly researchProvider: string;
  readonly researchQueryHint: string;
  readonly regenerateOutline: boolean;
  readonly blueprintElementPreferences: WorkflowBlueprintElementPreferences;
  readonly copilotGates: readonly string[];
  readonly creativeExploration: "adaptive" | "single";
  readonly planningCommitment: "progressive" | "full";
}

export interface RunChapterWorkflowInput {
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly force: boolean;
  readonly notes?: string;
  readonly writingMode: "whole_chapter" | "scene_level";
  readonly autorunScope?: "chapter" | "book";
  readonly skipDone?: boolean;
}

interface StartWorkflowCommandBase {
  readonly kind: "start_workflow";
  readonly projectId: string;
  readonly idempotencyKey: string;
}

export interface StartShortWorkflowCommand extends StartWorkflowCommandBase {
  readonly workflowType: "short";
  readonly runMode: "create";
  readonly payload: RunShortWorkflowInput;
}

export interface StartLongInitWorkflowCommand extends StartWorkflowCommandBase {
  readonly workflowType: "long_init";
  readonly runMode: WorkflowLaunchMode;
  readonly payload: InitLongWorkflowInput;
}

export interface StartLongChapterWorkflowCommand extends StartWorkflowCommandBase {
  readonly workflowType: "long_chapter";
  readonly runMode: "create" | "autorun";
  readonly payload: RunChapterWorkflowInput;
}

export type StartWorkflowCommand =
  | StartShortWorkflowCommand
  | StartLongInitWorkflowCommand
  | StartLongChapterWorkflowCommand;

export interface WorkflowCommandResult {
  readonly status: "accepted" | "rejected" | "already_running";
  readonly taskId?: string;
  readonly message: string;
}

export type WorkflowFormMode = "short" | "long";

export interface WorkflowDraftView {
  readonly mode: WorkflowFormMode;
  readonly payload: Readonly<Record<string, unknown>> | null;
  readonly revision: string;
  readonly savedAtLabel: string;
}

export interface WorkflowPresetRecordView {
  readonly name: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly revision: string;
  readonly updatedAtLabel: string;
}

/**
 * One durable entry in the per-preset AI creative-note timeline.
 *
 * The field names deliberately mirror the PySide preset-manager JSON so a
 * preset's history is shared by both desktop surfaces.
 */
export interface WorkflowAiHistoryEntry {
  readonly id: string;
  readonly timestamp: string;
  readonly operation: string;
  readonly data: Readonly<Record<string, unknown>>;
  readonly hint?: string;
  readonly selectedSuggestions?: readonly string[];
  readonly focusFields?: readonly string[];
  readonly metadata?: Readonly<Record<string, unknown>>;
}

export interface SaveWorkflowAiHistoryCommand {
  readonly kind: "save_workflow_ai_history";
  readonly mode: WorkflowFormMode;
  readonly presetName: string;
  readonly operation: string;
  readonly data: Readonly<Record<string, unknown>>;
  readonly hint?: string;
  readonly selectedSuggestions?: readonly string[];
  readonly focusFields?: readonly string[];
  readonly metadata?: Readonly<Record<string, unknown>>;
}

export interface SaveWorkflowDraftCommand {
  readonly kind: "save_workflow_draft";
  readonly mode: WorkflowFormMode;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly expectedRevision?: string;
}

export interface SaveWorkflowPresetCommand {
  readonly kind: "save_workflow_preset";
  readonly mode: WorkflowFormMode;
  readonly name: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly expectedRevision?: string;
}

export interface DeleteWorkflowPresetCommand {
  readonly kind: "delete_workflow_preset";
  readonly mode: WorkflowFormMode;
  readonly name: string;
  readonly expectedRevision?: string;
}

export interface WorkflowPersistenceResult {
  readonly status: "saved" | "deleted" | "conflict" | "not_found" | "rejected";
  readonly message: string;
  readonly revision?: string;
  readonly savedAtLabel?: string;
}

export interface GenerateWorkflowFieldsCommand {
  readonly kind: "generate_workflow_fields";
  readonly mode: WorkflowFormMode;
  readonly operation: "generate" | "polish";
  readonly currentPayload: Readonly<Record<string, unknown>>;
  readonly userHint: string;
  readonly generationMode?: "replace" | "fill_blanks" | "variant";
  readonly creativeProfile?: Readonly<Record<string, unknown>>;
  readonly hardConstraints?: Readonly<Record<string, unknown>>;
  readonly selectedSuggestions?: readonly string[];
  readonly focusFields?: readonly string[];
}

export interface GenerateWorkflowFieldsResult {
  readonly status: "generated" | "rejected";
  readonly message: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly suggestions: readonly string[];
  readonly creativeNote?: Readonly<Record<string, unknown>>;
}

/**
 * Voice studio commands.
 */
export interface SynthesizeVoiceCommand {
  readonly kind: "synthesize_voice";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly segmentIds?: readonly string[];
  readonly provider?: string;
}

export interface BuildVoiceTeamCommand {
  readonly kind: "build_voice_team";
  readonly projectId: string;
  readonly provider?: string;
  readonly rebuildCharacterIds?: readonly string[];
}

export interface RebuildNarratorVoiceCommand {
  readonly kind: "rebuild_narrator_voice";
  readonly projectId: string;
  readonly provider?: string;
}

export interface ConfirmVoiceTeamCommand {
  readonly kind: "confirm_voice_team";
  readonly projectId: string;
}

export interface CloneCharacterVoiceCommand {
  readonly kind: "clone_character_voice";
  readonly projectId: string;
  readonly characterId: string;
  readonly referenceAudio: string;
  readonly referenceTranscript?: string;
  readonly authorized: boolean;
  readonly provider?: string;
}

export interface DesignCharacterVoiceCommand {
  readonly kind: "design_character_voice";
  readonly projectId: string;
  readonly characterId: string;
  readonly description: string;
  readonly provider?: string;
}

export interface ApproveCharacterVoiceCommand {
  readonly kind: "approve_character_voice";
  readonly projectId: string;
  readonly characterId: string;
}

export interface PreviewCharacterVoiceCommand {
  readonly kind: "preview_character_voice";
  readonly projectId: string;
  readonly characterId: string;
  readonly sampleText?: string;
  readonly provider?: string;
}

/**
 * One audition candidate inside a multi-candidate A/B preview plan.
 *
 * Field names mirror the Python ``VoicePreviewCandidate`` service model
 * (snake_case on the wire, camelCase here); ``audioUrl`` is filled by the
 * engine after the generate command synthesizes the clip.
 */
export interface VoicePreviewCandidateView {
  readonly voiceId: string;
  readonly voiceName: string;
  readonly description: string;
  readonly samplePath?: string;
  readonly audioUrl?: string;
  readonly gender?: string;
  readonly ageHint?: string;
  readonly personality?: string;
  readonly matchReasons?: readonly string[];
  readonly error?: string;
}

/**
 * Multi-candidate A/B preview plan for one character.
 *
 * Mirrors the Python ``VoicePreviewPlan`` service model — every candidate
 * shares the same ``sampleText``/``speed``/``volume`` so the author can
 * compare voices on identical input.
 */
export interface VoicePreviewPlanView {
  readonly characterId: string;
  readonly characterName: string;
  readonly label?: string;
  readonly description?: string;
  readonly voiceId?: string;
  readonly sampleText: string;
  readonly speed: number;
  readonly volume: number;
  readonly candidates: readonly VoicePreviewCandidateView[];
}

export interface BuildVoicePreviewPlanCommand {
  readonly kind: "build_voice_preview_plan";
  readonly projectId: string;
  readonly characters: readonly Readonly<Record<string, unknown>>[];
  readonly provider?: string;
  readonly sampleText?: string;
  readonly candidateCount?: number;
  readonly language?: string;
}

export interface GenerateVoicePreviewsCommand {
  readonly kind: "generate_voice_previews";
  readonly projectId: string;
  readonly plan: VoicePreviewPlanView;
  readonly provider?: string;
}

export interface ConfirmVoicePreviewCommand {
  readonly kind: "confirm_voice_preview";
  readonly projectId: string;
  readonly characterId: string;
  readonly voiceId: string;
  readonly speed: number;
  readonly volume: number;
  readonly provider?: string;
  readonly modelId?: string;
  readonly sampleText?: string;
  readonly samplePath?: string;
}

export interface UpdateVoicePerformanceCommand {
  readonly kind: "update_voice_performance";
  readonly projectId: string;
  readonly characterId: string;
  readonly speedOffset: number;
  readonly pitchOffset: number;
  readonly volumeOffset: number;
}

export interface AssignCatalogVoiceCommand {
  readonly kind: "assign_catalog_voice";
  readonly projectId: string;
  readonly characterId: string;
  readonly voiceId: string;
  readonly provider?: string;
}

export interface AnalyzeVoiceScriptStyleCommand {
  readonly kind: "analyze_voice_script_style";
  readonly projectId: string;
  readonly sourceName: string;
  readonly referenceScriptText: string;
}

export interface GenerateVoiceScriptCommand {
  readonly kind: "generate_voice_script";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly provider?: string;
  readonly referenceStyleStrength?: number;
}

export interface VoiceScriptSegmentEdit {
  readonly segmentIndex: number;
  readonly content: string;
  readonly speakerId: string;
  readonly speakerLabel: string;
  readonly segmentType?: "narration" | "dialogue" | "inner_thought";
  readonly emotionLabel: string;
  readonly emotionIntensity?: number;
  readonly toneHint?: string;
  readonly speedOverride?: number;
  readonly volumeOverride?: number;
  readonly pitchOverride?: number;
  readonly stressWords?: readonly string[];
  readonly narratorDistance?: string;
  readonly pronunciationOverrides?: readonly string[];
  readonly languageCode?: string;
}

export interface SaveVoiceScriptCommand {
  readonly kind: "save_voice_script";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly edits: readonly VoiceScriptSegmentEdit[];
}

/**
 * Persists performance-only Voice Room guidance. The Engine preserves source
 * text and speaker identity and retires any pending audition for each edit.
 */
export interface SaveVoiceGuidanceCommand {
  readonly kind: "save_voice_guidance";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly edits: readonly {
    readonly segmentIndex: number;
    readonly segmentOverride: Readonly<Record<string, unknown>>;
  }[];
}

export interface PreviewVoiceSegmentCommand {
  readonly kind: "preview_voice_segment";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly segmentIndex: number;
  readonly provider?: string;
  readonly segmentOverride?: Readonly<Record<string, unknown>>;
}

export interface AcceptVoiceTakeCommand {
  readonly kind: "accept_voice_take";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly takeId: string;
}

export interface ReassembleVoiceCommand {
  readonly kind: "reassemble_voice";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly fast?: boolean;
}

export interface FullVoicePipelineCommand {
  readonly kind: "full_voice_pipeline";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly automationMode: "manual" | "assisted" | "autonomous";
  readonly provider?: string;
}

export interface RejectVoiceTakeCommand {
  readonly kind: "reject_voice_take";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly takeId: string;
}

export interface ClearVoiceArtifactsCommand {
  readonly kind: "clear_voice_artifacts";
  readonly projectId: string;
  readonly chapterNumber?: number;
  readonly chapterNumbers?: readonly number[];
  readonly categories?: readonly (
    | "stale_previews"
    | "orphan_candidates"
    | "completed_checkpoints"
    | "orphan_sound_assets"
    | "orphan_chapter_reports"
  )[];
  readonly scope:
    | "script_chapter"
    | "chapter"
    | "chapters"
    | "redundant_takes"
    | "stale_files"
    | "project_reset";
}

export interface GenerateSoundPaletteCommand {
  readonly kind: "generate_sound_palette";
  readonly projectId: string;
}

export interface UpdateSoundAssetCommand {
  readonly kind: "update_sound_asset";
  readonly projectId: string;
  readonly assetId: string;
  readonly action: "set_status" | "set_tags" | "set_commercial_rights" | "publish";
  readonly status?: "approved" | "pending" | "rejected";
  readonly tags?: readonly string[];
  readonly commercialUseStatus?: "cleared" | "review_required" | "restricted";
  readonly licenseNote?: string;
}

export interface ImportSoundAssetsCommand {
  readonly kind: "import_sound_assets";
  readonly projectId: string;
  readonly assetKind: "bgm" | "soundscape" | "sfx";
  readonly tags: readonly string[];
  readonly files: readonly {
    readonly name: string;
    readonly base64: string;
  }[];
}

export interface ResolveSpeakersCommand {
  readonly kind: "resolve_speakers";
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly resolutions: readonly {
    readonly segmentIndex: number;
    readonly characterId: string;
    readonly segmentType?: string;
  }[];
}

/** One delivered chapter as audio or its timed subtitle file. */
export interface ExportChapterAudioCommand {
  readonly kind: "export_audio";
  readonly projectId: string;
  readonly scope: "chapter";
  readonly chapterNumber: number;
  readonly format: "wav" | "mp3" | "flac";
  /** Optional loudness normalization, matching the PySide MP3 delivery action. */
  readonly targetLufs?: number;
}

export interface ExportChapterSubtitleCommand {
  readonly kind: "export_audio";
  readonly projectId: string;
  readonly scope: "chapter";
  readonly chapterNumber: number;
  readonly format: "srt";
}

/** All delivered chapters in one portable archive. */
export interface ExportBookAudioCommand {
  readonly kind: "export_audio";
  readonly projectId: string;
  readonly scope: "book";
  readonly format: "zip";
  readonly includeSubtitles: boolean;
}

/**
 * Audio delivery requests are deliberately a discriminated union.  A caller
 * cannot accidentally submit an all-book ZIP as a chapter export, nor submit
 * an SRT request to the audio-only worker path.
 */
export type ExportAudioCommand =
  | ExportChapterAudioCommand
  | ExportChapterSubtitleCommand
  | ExportBookAudioCommand;

/**
 * Finished audiobook delivery package export: per-chapter mp3 set plus the
 * TOC/cover metadata and the segment-level assembly report.  The backend
 * applies a sequential chapter gate — unconfirmed chapters are reported in
 * the rejection message so the UI can prompt "第 N 章确认后继续".
 */
export interface ExportAudiobookCommand {
  readonly kind: "export_audiobook";
  readonly projectId: string;
  /** Empty/omitted = collect every assembled chapter. */
  readonly chapterNumbers?: readonly number[];
  readonly requireDeliveryReady?: boolean;
}

export interface VoiceCommandResult {
  readonly status: "accepted" | "rejected" | "no_segments";
  readonly taskId?: string;
  readonly takeId?: string;
  readonly audioUrl?: string;
  readonly downloadUrl?: string;
  readonly message: string;
  /** Structured payload for command families that return view models
   * (e.g. ``build_voice_preview_plan`` → ``{ plans: [...] }``). */
  readonly data?: unknown;
}

export interface BootstrapFilmCommand {
  readonly kind: "bootstrap_film";
  readonly projectId: string;
  readonly mode: FilmProductionMode;
  readonly refreshSources: boolean;
}

export interface AdvanceFilmCommand {
  readonly kind: "advance_film";
  readonly projectId: string;
  readonly mode: FilmProductionMode;
  readonly useAi: boolean;
  readonly runUntil?: FilmStageId;
}

export interface PlanDramaCommand {
  readonly kind: "plan_drama";
  readonly projectId: string;
  readonly title: string;
  readonly totalEpisodes: number;
  readonly genre: string;
  readonly logline: string;
  readonly episodeDurationS: number;
  readonly characterRoster: readonly string[];
  readonly language: string;
}

export interface ExpandDramaOutlinesCommand {
  readonly kind: "expand_drama_outlines";
  readonly projectId: string;
  readonly start: number;
  readonly end: number;
}

export interface WriteDramaScreenplayCommand {
  readonly kind: "write_drama_screenplay";
  readonly projectId: string;
  readonly episodeNumber: number;
}

export interface ExportDramaPackageCommand {
  readonly kind: "export_drama_package";
  readonly projectId: string;
}

export interface PlanComicPagesCommand {
  readonly kind: "plan_comic_pages";
  readonly projectId: string;
  readonly format: "page" | "webtoon";
}

export interface ExportComicPackageCommand {
  readonly kind: "export_comic_package";
  readonly projectId: string;
}

export interface GenerateFilmAssetCommand {
  readonly kind: "generate_film_asset";
  readonly projectId: string;
  readonly assetId: string;
  readonly providerId: string;
  readonly modelId: string;
  readonly imageCount: number;
}

export interface SelectFilmAssetCommand {
  readonly kind: "select_film_asset";
  readonly projectId: string;
  readonly assetId: string;
  readonly url: string;
  readonly lock: boolean;
}

export interface UpdateFilmShotCommand {
  readonly kind: "update_film_shot";
  readonly projectId: string;
  readonly shotId: string;
  readonly patch: Readonly<Record<string, unknown>>;
}

export interface GenerateFilmShotCommand {
  readonly kind: "generate_film_shot";
  readonly projectId: string;
  readonly shotId: string;
  readonly providerId: string;
  readonly modelId: string;
}

export interface QueryFilmMediaCommand {
  readonly kind: "query_film_media";
  readonly projectId: string;
  readonly targetId: string;
}

export interface PollFilmShotCommand {
  readonly kind: "poll_film_shot";
  readonly projectId: string;
  readonly shotId: string;
}

export interface RerunFilmWorkflowNodeCommand {
  readonly kind: "rerun_film_workflow_node";
  readonly projectId: string;
  readonly nodeId: string;
}

export interface SaveFilmGraphCommand {
  readonly kind: "save_film_graph";
  readonly projectId: string;
  readonly expectedRevision: number;
  readonly graph: FilmGraphDefinitionView;
}

export interface ValidateFilmGraphCommand {
  readonly kind: "validate_film_graph";
  readonly projectId: string;
  readonly graph: FilmGraphDefinitionView;
}

export interface OptimizeFilmNodePromptCommand {
  readonly kind: "optimize_film_node_prompt";
  readonly projectId: string;
  readonly node: FilmGraphNodeView;
}

export interface EstimateFilmGraphRunCommand {
  readonly kind: "estimate_film_graph_run";
  readonly projectId: string;
  readonly scope: FilmGraphRunScope;
  readonly targetNodeIds: readonly string[];
}

export interface CreateFilmGraphRunCommand {
  readonly kind: "create_film_graph_run";
  readonly projectId: string;
  readonly scope: FilmGraphRunScope;
  readonly targetNodeIds: readonly string[];
  readonly confirmedCost: boolean;
  readonly highPriority: boolean;
}

export interface CancelFilmGraphRunCommand {
  readonly kind: "cancel_film_graph_run";
  readonly projectId: string;
  readonly runId: string;
}

export interface ExportFilmTimelineCommand {
  readonly kind: "export_film_timeline";
  readonly projectId: string;
}

export interface MaterializeFilmMediaCommand {
  readonly kind: "materialize_film_media";
  readonly projectId: string;
  readonly targetId: string;
}

export interface QcFilmShotCommand {
  readonly kind: "qc_film_shot";
  readonly projectId: string;
  readonly shotId: string;
}

export interface BatchGenerateFilmShotsCommand {
  readonly kind: "batch_generate_film_shots";
  readonly projectId: string;
  readonly shotIds: readonly string[];
}

export interface RenderFilmMasterCommand {
  readonly kind: "render_film_master";
  readonly projectId: string;
  readonly burnSubtitles: boolean;
  readonly width?: number;
  readonly height?: number;
  readonly frameRate?: number;
}

export interface CancelFilmJobCommand {
  readonly kind: "cancel_film_job";
  readonly projectId: string;
  readonly jobId: string;
}

export interface FilmExportResult {
  readonly path: string;
  readonly format: "OpenTimelineIO";
}

/**
 * Discriminated command union for the write-side engine boundary.
 *
 * Every visible mutation on the two studio surfaces crosses this boundary;
 * page components never emulate durable work with local timers.
 */
export type EngineCommand =
  | SaveSettingsCommand
  | DeleteProjectsCommand
  | SaveChapterRevisionCommand
  | GenerateChapterRevisionCandidateCommand
  | SaveTokenDashboardPreferencesCommand
  | PrepareChapterCommand
  | CancelChapterCommand
  | ResolveChapterCheckpointCommand
  | PolishChapterCommand
  | RepairContinuityCommand
  | RepairCausalCommand
  | RepairIssuesCommand
  | ReevaluateChapterCommand
  | ReextractRelationshipsCommand
  | RepairMotifHistoryCommand
  | PolishOutlineCommand
  | AuditBookCommand
  | AuditBookEditorialCommand
  | ExecuteGlobalRepairQueueCommand
  | ExportBookCommand
  | CleanChaptersCommand
  | CancelJobCommand
  | ResumeJobCommand
  | ClearJobHistoryCommand
  | AcknowledgeTaskErrorsCommand
  | ReopenTaskErrorsCommand
  | ClearClosedTaskErrorsCommand
  | ClearErrorArchiveCommand
  | ContinueLongInitCommand
  | RestartLongInitCommand
  | StartWorkflowCommand
  | SaveWorkflowDraftCommand
  | SaveWorkflowPresetCommand
  | DeleteWorkflowPresetCommand
  | SaveWorkflowAiHistoryCommand
  | GenerateWorkflowFieldsCommand
  | SynthesizeVoiceCommand
  | BuildVoiceTeamCommand
  | RebuildNarratorVoiceCommand
  | ConfirmVoiceTeamCommand
  | CloneCharacterVoiceCommand
  | DesignCharacterVoiceCommand
  | ApproveCharacterVoiceCommand
  | PreviewCharacterVoiceCommand
  | BuildVoicePreviewPlanCommand
  | GenerateVoicePreviewsCommand
  | ConfirmVoicePreviewCommand
  | UpdateVoicePerformanceCommand
  | AssignCatalogVoiceCommand
  | AnalyzeVoiceScriptStyleCommand
  | GenerateVoiceScriptCommand
  | SaveVoiceScriptCommand
  | SaveVoiceGuidanceCommand
  | PreviewVoiceSegmentCommand
  | AcceptVoiceTakeCommand
  | ReassembleVoiceCommand
  | FullVoicePipelineCommand
  | RejectVoiceTakeCommand
  | ClearVoiceArtifactsCommand
  | GenerateSoundPaletteCommand
  | UpdateSoundAssetCommand
  | ImportSoundAssetsCommand
  | ResolveSpeakersCommand
  | ExportAudioCommand
  | ExportAudiobookCommand
  | BootstrapFilmCommand
  | AdvanceFilmCommand
  | GenerateFilmAssetCommand
  | SelectFilmAssetCommand
  | UpdateFilmShotCommand
  | GenerateFilmShotCommand
  | QueryFilmMediaCommand
  | PollFilmShotCommand
  | RerunFilmWorkflowNodeCommand
  | SaveFilmGraphCommand
  | ValidateFilmGraphCommand
  | OptimizeFilmNodePromptCommand
  | EstimateFilmGraphRunCommand
  | CreateFilmGraphRunCommand
  | CancelFilmGraphRunCommand
  | ExportFilmTimelineCommand
  | MaterializeFilmMediaCommand
  | QcFilmShotCommand
  | BatchGenerateFilmShotsCommand
  | RenderFilmMasterCommand
  | CancelFilmJobCommand
  | PlanDramaCommand
  | ExpandDramaOutlinesCommand
  | WriteDramaScreenplayCommand
  | ExportDramaPackageCommand
  | PlanComicPagesCommand
  | ExportComicPackageCommand;

export type EngineCommandResult =
  | SaveSettingsResult
  | DeleteProjectsResult
  | SaveChapterRevisionResult
  | GenerateChapterRevisionCandidateResult
  | SaveTokenDashboardPreferencesResult
  | ChapterCommandResult
  | ClearJobHistoryResult
  | RestartLongInitResult
  | WorkflowCommandResult
  | WorkflowPersistenceResult
  | GenerateWorkflowFieldsResult
  | VoiceCommandResult
  | FilmStudioView
  | FilmExportResult
  | FilmGraphView
  | FilmGraphRunView
  | FilmRunEstimateView
  | FilmPromptOptimizationView
  | readonly FilmGraphValidationIssue[]
  | DramaStudioView
  | DramaExportResult
  | ComicStudioView
  | ComicExportResult;

/**
 * Write-side contract for the writing engine.
 *
 * Pages call commands to request mutations.  The result is always
 * a typed acknowledgement — never a raw HTTP response, Python
 * exception, or provider client.
 */
export interface EngineCommandClient {
  submitJobDecision?(taskId: string, decisionId: string, choice: string, customText: string, approvalVersion: string): Promise<void>;
  sendAuthoringMessage?(projectId: string, request: AuthoringMessageRequest): Promise<AuthoringMessageView>;
  setAuthoringPolicy?(projectId: string, policy: AuthoringPolicy, expectedVersion: number): Promise<AuthoringSessionView>;
  startAuthoringSession?(projectId: string, expectedVersion: number, inputVersion: string): Promise<AuthoringSessionView>;
  pauseAuthoringSession?(projectId: string): Promise<AuthoringSessionView>;
  setAuthoringAvailability?(projectId: string, enabled: boolean, expectedVersion: number, inputVersion: string): Promise<AuthoringSessionView>;
  refreshSemanticConsistency?(projectId: string, expectedStoryVersion: string, expectedPolicyVersion: number): Promise<ChapterCommandResult>;
  createAuthoringProposal?(projectId: string, request: AuthoringProposalRequest): Promise<AuthoringProposalView>;
  decideAuthoringProposal?(projectId: string, proposalId: string, request: AuthoringProposalDecision): Promise<AuthoringProposalView>;
  applyAuthoringProposal?(projectId: string, proposalId: string): Promise<AuthoringProposalView>;
  createRepairAnnotation?(projectId: string, request: RepairManualAnnotationRequest): Promise<RepairCaseDetailView>;
  saveRepairCandidate?(projectId: string, caseId: string, request: RepairCandidateEditRequest): Promise<RepairCaseDetailView>;
  verifyRepairCandidate?(projectId: string, caseId: string, request: RepairVerificationRequest): Promise<RepairCaseDetailView>;
  decideRepairCase?(projectId: string, caseId: string, request: RepairCaseDecisionRequest): Promise<RepairCaseDetailView>;
  requestRepairApproval?(projectId: string, caseId: string, request: RepairApprovalRequest): Promise<RepairCaseDetailView>;
  publishRepairCase?(projectId: string, caseId: string, request: RepairPublishRequest): Promise<RepairCaseDetailView>;
  recoverRepairReceipt?(projectId: string, caseId: string, request: RepairRecoveryRequest): Promise<RepairCaseDetailView>;
  saveSettings(command: SaveSettingsCommand): Promise<SaveSettingsResult>;
  deleteProjects(command: DeleteProjectsCommand): Promise<DeleteProjectsResult>;
  configureOllamaRuntime(command: ConfigureOllamaRuntimeCommand): Promise<OllamaManagerView>;
  setOllamaModelRoles(command: SetOllamaModelRolesCommand): Promise<OllamaManagerView>;
  controlOllamaRuntime(command: OllamaRuntimeCommand): Promise<ChapterCommandResult>;
  pullOllamaModel(command: PullOllamaModelCommand): Promise<ChapterCommandResult>;
  deleteOllamaModel(command: DeleteOllamaModelCommand): Promise<ChapterCommandResult>;
  saveChapterRevision(command: SaveChapterRevisionCommand): Promise<SaveChapterRevisionResult>;
  generateChapterRevisionCandidate(command: GenerateChapterRevisionCandidateCommand): Promise<GenerateChapterRevisionCandidateResult>;
  saveNarrativeSubplots(command: SaveNarrativeSubplotsCommand): Promise<NarrativeSubplotMutationResult>;
  saveNarrativeCharacter(command: SaveNarrativeCharacterCommand): Promise<NarrativeCharacterMutationResult>;
  retireNarrativeCharacter(command: RetireNarrativeCharacterCommand): Promise<NarrativeCharacterMutationResult>;
  saveNarrativeRelationship(command: SaveNarrativeRelationshipCommand): Promise<NarrativeCharacterMutationResult>;
  removeNarrativeRelationship(command: RemoveNarrativeRelationshipCommand): Promise<NarrativeCharacterMutationResult>;
  saveHumanizePattern(command: SaveHumanizePatternCommand): Promise<HumanizeLibraryMutationResult>;
  setHumanizePatternEnabled(command: SetHumanizePatternEnabledCommand): Promise<HumanizeLibraryMutationResult>;
  removeHumanizePattern(command: RemoveHumanizePatternCommand): Promise<HumanizeLibraryMutationResult>;
  mergeHumanizePatterns(command: MergeHumanizePatternsCommand): Promise<HumanizeLibraryMutationResult>;
  generateNarrativeSubplots(command: GenerateNarrativeSubplotsCommand): Promise<GenerateNarrativeSubplotsResult>;
  convertNarrativeArcsToSubplots(command: ConvertNarrativeArcsToSubplotsCommand): Promise<NarrativeSubplotMutationResult>;
  loadTokenDashboardPreferences(projectId: string): Promise<TokenDashboardPreferencesView>;
  saveTokenDashboardPreferences(command: SaveTokenDashboardPreferencesCommand): Promise<SaveTokenDashboardPreferencesResult>;
  testModelProfile(command: TestModelProfileCommand): Promise<TestModelProfileResult>;
  prepareChapter(command: PrepareChapterCommand): Promise<ChapterCommandResult>;
  cancelChapter(command: CancelChapterCommand): Promise<ChapterCommandResult>;
  resolveChapterCheckpoint(command: ResolveChapterCheckpointCommand): Promise<ChapterCommandResult>;
  polishChapter(command: PolishChapterCommand): Promise<ChapterCommandResult>;
  repairContinuity(command: RepairContinuityCommand): Promise<ChapterCommandResult>;
  repairCausal(command: RepairCausalCommand): Promise<ChapterCommandResult>;
  repairIssues(command: RepairIssuesCommand): Promise<ChapterCommandResult>;
  reevaluateChapter(command: ReevaluateChapterCommand): Promise<ChapterCommandResult>;
  reextractRelationships(command: ReextractRelationshipsCommand): Promise<ChapterCommandResult>;
  repairMotifHistory(command: RepairMotifHistoryCommand): Promise<ChapterCommandResult>;
  polishOutline(command: PolishOutlineCommand): Promise<ChapterCommandResult>;
  syncChapterContracts(command: SyncChapterContractsCommand): Promise<ChapterCommandResult>;
  extendOutline(command: ExtendOutlineCommand): Promise<ChapterCommandResult>;
  retryPlanningHorizon?(projectId: string, currentChapter: number): Promise<ChapterCommandResult>;
  auditBook(command: AuditBookCommand): Promise<ChapterCommandResult>;
  auditBookEditorial(command: AuditBookEditorialCommand): Promise<ChapterCommandResult>;
  executeGlobalRepairQueue(command: ExecuteGlobalRepairQueueCommand): Promise<ChapterCommandResult>;
  exportBook(command: ExportBookCommand): Promise<ChapterCommandResult>;
  cleanChapters(command: CleanChaptersCommand): Promise<ChapterCommandResult>;
  cancelJob(command: CancelJobCommand): Promise<ChapterCommandResult>;
  resumeJob(command: ResumeJobCommand): Promise<ChapterCommandResult>;
  retryInitRepair(command: RetryInitRepairCommand): Promise<ChapterCommandResult>;
  saveInitManualRepair(command: SaveInitManualRepairCommand): Promise<SaveInitManualRepairResult>;
  rebuildMemoryVectors(command: RebuildMemoryVectorsCommand): Promise<ChapterCommandResult>;
  clearJobHistory(command: ClearJobHistoryCommand): Promise<ClearJobHistoryResult>;
  acknowledgeTaskErrors(command: AcknowledgeTaskErrorsCommand): Promise<TaskErrorResolutionResult>;
  reopenTaskErrors(command: ReopenTaskErrorsCommand): Promise<TaskErrorResolutionResult>;
  clearClosedTaskErrors(command: ClearClosedTaskErrorsCommand): Promise<ClearClosedTaskErrorsResult>;
  clearErrorArchive(command: ClearErrorArchiveCommand): Promise<ClearErrorArchiveResult>;
  previewTestProjectCleanup(): Promise<TestProjectCleanupPreview>;
  cleanupTestProjects(command: CleanupTestProjectsCommand): Promise<CleanupTestProjectsResult>;
  continueLongInit(command: ContinueLongInitCommand): Promise<WorkflowCommandResult>;
  restartLongInit(command: RestartLongInitCommand): Promise<RestartLongInitResult>;
  startWorkflow(command: StartWorkflowCommand): Promise<WorkflowCommandResult>;
  loadWorkflowDraft(mode: WorkflowFormMode): Promise<WorkflowDraftView>;
  saveWorkflowDraft(command: SaveWorkflowDraftCommand): Promise<WorkflowPersistenceResult>;
  listWorkflowPresets(mode: WorkflowFormMode): Promise<readonly WorkflowPresetRecordView[]>;
  saveWorkflowPreset(command: SaveWorkflowPresetCommand): Promise<WorkflowPersistenceResult>;
  deleteWorkflowPreset(command: DeleteWorkflowPresetCommand): Promise<WorkflowPersistenceResult>;
  listWorkflowAiHistory(mode: WorkflowFormMode, presetName: string): Promise<readonly WorkflowAiHistoryEntry[]>;
  saveWorkflowAiHistory(command: SaveWorkflowAiHistoryCommand): Promise<WorkflowPersistenceResult>;
  clearWorkflowAiHistory(mode: WorkflowFormMode, presetName: string): Promise<WorkflowPersistenceResult>;
  generateWorkflowFields(command: GenerateWorkflowFieldsCommand): Promise<GenerateWorkflowFieldsResult>;
  synthesizeVoice(command: SynthesizeVoiceCommand): Promise<VoiceCommandResult>;
  buildVoiceTeam(command: BuildVoiceTeamCommand): Promise<VoiceCommandResult>;
  rebuildNarratorVoice(command: RebuildNarratorVoiceCommand): Promise<VoiceCommandResult>;
  confirmVoiceTeam(command: ConfirmVoiceTeamCommand): Promise<VoiceCommandResult>;
  cloneCharacterVoice(command: CloneCharacterVoiceCommand): Promise<VoiceCommandResult>;
  designCharacterVoice(command: DesignCharacterVoiceCommand): Promise<VoiceCommandResult>;
  approveCharacterVoice(command: ApproveCharacterVoiceCommand): Promise<VoiceCommandResult>;
  previewCharacterVoice(command: PreviewCharacterVoiceCommand): Promise<VoiceCommandResult>;
  buildVoicePreviewPlan(command: BuildVoicePreviewPlanCommand): Promise<VoiceCommandResult>;
  generateVoicePreviews(command: GenerateVoicePreviewsCommand): Promise<VoiceCommandResult>;
  confirmVoicePreview(command: ConfirmVoicePreviewCommand): Promise<VoiceCommandResult>;
  updateVoicePerformance(command: UpdateVoicePerformanceCommand): Promise<VoiceCommandResult>;
  assignCatalogVoice(command: AssignCatalogVoiceCommand): Promise<VoiceCommandResult>;
  analyzeVoiceScriptStyle(command: AnalyzeVoiceScriptStyleCommand): Promise<VoiceCommandResult>;
  generateVoiceScript(command: GenerateVoiceScriptCommand): Promise<VoiceCommandResult>;
  saveVoiceScript(command: SaveVoiceScriptCommand): Promise<VoiceCommandResult>;
  saveVoiceGuidance(command: SaveVoiceGuidanceCommand): Promise<VoiceCommandResult>;
  previewVoiceSegment(command: PreviewVoiceSegmentCommand): Promise<VoiceCommandResult>;
  acceptVoiceTake(command: AcceptVoiceTakeCommand): Promise<VoiceCommandResult>;
  reassembleVoice(command: ReassembleVoiceCommand): Promise<VoiceCommandResult>;
  runFullVoicePipeline(command: FullVoicePipelineCommand): Promise<VoiceCommandResult>;
  rejectVoiceTake(command: RejectVoiceTakeCommand): Promise<VoiceCommandResult>;
  clearVoiceArtifacts(command: ClearVoiceArtifactsCommand): Promise<VoiceCommandResult>;
  generateSoundPalette(command: GenerateSoundPaletteCommand): Promise<VoiceCommandResult>;
  updateSoundAsset(command: UpdateSoundAssetCommand): Promise<VoiceCommandResult>;
  importSoundAssets(command: ImportSoundAssetsCommand): Promise<VoiceCommandResult>;
  resolveSpeakers(command: ResolveSpeakersCommand): Promise<VoiceCommandResult>;
  exportAudio(command: ExportAudioCommand): Promise<VoiceCommandResult>;
  exportAudiobook(command: ExportAudiobookCommand): Promise<VoiceCommandResult>;
  bootstrapFilm(command: BootstrapFilmCommand): Promise<FilmStudioView>;
  advanceFilm(command: AdvanceFilmCommand): Promise<FilmStudioView>;
  generateFilmAsset(command: GenerateFilmAssetCommand): Promise<FilmStudioView>;
  selectFilmAsset(command: SelectFilmAssetCommand): Promise<FilmStudioView>;
  updateFilmShot(command: UpdateFilmShotCommand): Promise<FilmStudioView>;
  generateFilmShot(command: GenerateFilmShotCommand): Promise<FilmStudioView>;
  queryFilmMedia(command: QueryFilmMediaCommand): Promise<FilmStudioView>;
  pollFilmShot(command: PollFilmShotCommand): Promise<FilmStudioView>;
  rerunFilmWorkflowNode(command: RerunFilmWorkflowNodeCommand): Promise<FilmStudioView>;
  saveFilmGraph(command: SaveFilmGraphCommand): Promise<FilmGraphView>;
  validateFilmGraph(command: ValidateFilmGraphCommand): Promise<readonly FilmGraphValidationIssue[]>;
  optimizeFilmNodePrompt(command: OptimizeFilmNodePromptCommand): Promise<FilmPromptOptimizationView>;
  estimateFilmGraphRun(command: EstimateFilmGraphRunCommand): Promise<FilmRunEstimateView>;
  createFilmGraphRun(command: CreateFilmGraphRunCommand): Promise<FilmGraphRunView>;
  cancelFilmGraphRun(command: CancelFilmGraphRunCommand): Promise<FilmGraphRunView>;
  exportFilmTimeline(command: ExportFilmTimelineCommand): Promise<FilmExportResult>;
  materializeFilmMedia(command: MaterializeFilmMediaCommand): Promise<FilmStudioView>;
  qcFilmShot(command: QcFilmShotCommand): Promise<FilmStudioView>;
  batchGenerateFilmShots(command: BatchGenerateFilmShotsCommand): Promise<FilmStudioView>;
  renderFilmMaster(command: RenderFilmMasterCommand): Promise<FilmStudioView>;
  cancelFilmJob(command: CancelFilmJobCommand): Promise<FilmStudioView>;
  planDrama(command: PlanDramaCommand): Promise<DramaStudioView>;
  expandDramaOutlines(command: ExpandDramaOutlinesCommand): Promise<DramaStudioView>;
  writeDramaScreenplay(command: WriteDramaScreenplayCommand): Promise<DramaStudioView>;
  exportDramaPackage(command: ExportDramaPackageCommand): Promise<DramaExportResult>;
  planComicPages(command: PlanComicPagesCommand): Promise<ComicStudioView>;
  exportComicPackage(command: ExportComicPackageCommand): Promise<ComicExportResult>;
}
